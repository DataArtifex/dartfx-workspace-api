(() => {
  const rootMeta = document.querySelector('meta[name="dartfx-root-path"]');
  const BASE = (rootMeta ? rootMeta.getAttribute("content") : "").replace(/\/$/, "");
  const API = (path) => `${BASE}${path}`;

  const state = {
    apiKey: sessionStorage.getItem("dartfx_api_key") || "",
    principal: { id: "anonymous", tier: "anonymous", label: "Public Anonymous" },
    spaces: [],
    workspaces: [],
    currentSpaceId: null,
    currentWsId: null,
    currentDir: ".",
    currentTab: "overview",
    currentResource: null,
    lastFiles: [],
    collapsedArtifactParents: new Set(),
    eventSources: new Map(),
  };

  const $ = (id) => document.getElementById(id);

  function fmtBytes(bytes) {
    if (bytes == null) return "Unlimited";
    if (bytes < 1024) return `${bytes} B`;
    let val = bytes;
    for (const unit of ["KB", "MB", "GB", "TB"]) {
      val /= 1024;
      if (val < 1024) return `${val.toFixed(1)} ${unit}`;
    }
    return `${val.toFixed(1)} PB`;
  }

  function toast(message, isError = false) {
    const el = document.createElement("div");
    el.className = `toast${isError ? " error" : ""}`;
    el.textContent = message;
    $("toast-container").appendChild(el);
    setTimeout(() => el.remove(), 4500);
  }

  async function apiFetch(path, opts = {}) {
    const headers = new Headers(opts.headers || {});
    if (state.apiKey) headers.set("X-API-Key", state.apiKey);
    const res = await fetch(API(path), { ...opts, headers });
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try {
        const body = await res.json();
        if (body.detail) detail = body.detail;
      } catch (_) {}
      throw new Error(detail);
    }
    const ctype = res.headers.get("content-type") || "";
    if (ctype.includes("application/json")) return res.json();
    return res.text();
  }

  function authQuerySuffix() {
    return state.apiKey ? `api_key=${encodeURIComponent(state.apiKey)}` : "";
  }

  function downloadUrl(path) {
    const sep = path.includes("?") ? "&" : "?";
    const qs = authQuerySuffix();
    return API(qs ? `${path}${sep}${qs}` : path);
  }

  async function refreshWhoami() {
    try {
      state.principal = await apiFetch("/auth/whoami");
    } catch (err) {
      toast(`Auth check failed: ${err.message}`, true);
      state.apiKey = "";
      sessionStorage.removeItem("dartfx_api_key");
      state.principal = await apiFetch("/auth/whoami");
    }
    const badge = $("auth-badge");
    badge.className = `tier-badge tier-${state.principal.tier}`;
    if (state.principal.tier === "admin") {
      badge.textContent = `Admin: ${state.principal.label}`;
    } else if (state.principal.tier === "user") {
      badge.textContent = `User: ${state.principal.label}`;
    } else {
      badge.textContent = "Anonymous Sandbox";
    }
    document.querySelectorAll(".admin-only").forEach((el) => {
      el.classList.toggle("hidden", state.principal.tier !== "admin");
    });
  }

  let isPopstateHandling = false;
  let lastHandledHash = window.location.hash;

  function parseHash() {
    const hash = window.location.hash.replace(/^#\/?/, "");
    if (!hash) {
      return { tab: "overview", ws: null, dir: ".", space: null, file: null };
    }
    const [pathPart, queryPart] = hash.split("?");
    const tab = pathPart || "overview";
    const params = new URLSearchParams(queryPart || "");
    return {
      tab: tab || "overview",
      ws: params.get("ws") || null,
      dir: params.get("dir") || ".",
      space: params.get("space") || null,
      file: params.get("file") || null,
    };
  }

  function buildHash(override = {}) {
    const tab = override.tab !== undefined ? override.tab : state.currentTab || "overview";
    const ws = override.ws !== undefined ? override.ws : state.currentWsId;
    const dir = override.dir !== undefined ? override.dir : state.currentDir;
    const space = override.space !== undefined ? override.space : state.currentSpaceId;
    const file =
      override.file !== undefined ? override.file : state.currentResource ? state.currentResource.path : null;

    const params = new URLSearchParams();
    if (ws) params.set("ws", ws);
    if (space) params.set("space", space);
    if (tab === "files" && dir && dir !== "." && dir !== "/") params.set("dir", dir);
    if (file) params.set("file", file);

    const qs = params.toString();
    return `#/${tab}${qs ? "?" + qs : ""}`;
  }

  function syncUrl(push = true) {
    if (isPopstateHandling) return;
    const targetHash = buildHash();
    if (window.location.hash === targetHash) return;
    lastHandledHash = targetHash;
    if (push) {
      history.pushState({ tab: state.currentTab, ws: state.currentWsId, dir: state.currentDir }, "", targetHash);
    } else {
      history.replaceState({ tab: state.currentTab, ws: state.currentWsId, dir: state.currentDir }, "", targetHash);
    }
  }

  function switchTab(tabName, push = true) {
    const validTabs = ["overview", "files", "queries", "jobs", "admin"];
    let targetTab = validTabs.includes(tabName) ? tabName : "overview";
    if (targetTab === "admin" && state.principal.tier !== "admin") targetTab = "overview";

    const btn = document.querySelector(`.tab[data-tab="${targetTab}"]`);
    const panel = $(`tab-${targetTab}`);
    if (!btn || !panel) return;

    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    panel.classList.add("active");
    state.currentTab = targetTab;

    if (targetTab === "admin") loadAdminData();
    if (push) syncUrl(true);
  }

  function navigateToDir(dirPath, push = true) {
    if (state.currentResource) closeResourceDrawer(false);
    state.currentDir = dirPath || ".";
    loadFiles();
    if (push) syncUrl(true);
  }

  function closeResourceDrawer(updateHistory = true) {
    state.currentResource = null;
    $("resource-drawer").classList.add("hidden");
    if (updateHistory && !isPopstateHandling) {
      if (history.state && history.state.drawerOpened) {
        history.back();
      } else {
        syncUrl(true);
      }
    }
  }

  async function openFileByPath(filePath) {
    if (!state.currentWsId || !filePath) return;
    const fileName = filePath.split("/").pop() || filePath;
    let entry = (state.lastFiles || []).find((f) => f.path === filePath);
    if (!entry) {
      try {
        const detail = await apiFetch(`/workspaces/${state.currentWsId}/resources/${encodeURIComponent(filePath)}`);
        entry = {
          name: fileName,
          path: detail.path,
          uuid: detail.uuid,
          is_dir: false,
          registered: true,
          file_format: detail.file_format,
          file_type: detail.type,
          size_bytes: detail.size_bytes,
        };
      } catch (_) {
        entry = {
          name: fileName,
          path: filePath,
          is_dir: false,
          registered: false,
        };
      }
    }
    await openResourceDrawer(entry, false);
  }

  async function applyNavState(nav, isInit = false) {
    isPopstateHandling = true;
    try {
      // 1. Space
      const targetSpace = nav.space || null;
      if (targetSpace !== state.currentSpaceId) {
        state.currentSpaceId = targetSpace;
        const spaceSelect = $("space-select");
        if (spaceSelect) spaceSelect.value = targetSpace || "";
        populateWorkspaceSelect(false);
      }

      // 2. Workspace
      let wsChanged = false;
      const targetWs = nav.ws;
      if (targetWs && targetWs !== state.currentWsId) {
        const match = state.workspaces.find((w) => w.id === targetWs || w.slug === targetWs);
        if (match) {
          state.currentWsId = match.id;
          const wsSelect = $("workspace-select");
          if (wsSelect) wsSelect.value = match.id;
          wsChanged = true;
        }
      }

      // 3. Tab
      const validTabs = ["overview", "files", "queries", "jobs", "admin"];
      let targetTab = validTabs.includes(nav.tab) ? nav.tab : "overview";
      if (targetTab === "admin" && state.principal.tier !== "admin") targetTab = "overview";

      if (targetTab !== state.currentTab || isInit) {
        state.currentTab = targetTab;
        document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
        document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
        const tabBtn = document.querySelector(`.tab[data-tab="${targetTab}"]`);
        const tabPanel = $(`tab-${targetTab}`);
        if (tabBtn && tabPanel) {
          tabBtn.classList.add("active");
          tabPanel.classList.add("active");
          if (targetTab === "admin") loadAdminData();
        }
      }

      // 4. Directory in files tab
      const targetDir = nav.dir || ".";
      let dirChanged = false;
      if (targetDir !== state.currentDir) {
        state.currentDir = targetDir;
        dirChanged = true;
      }

      if (wsChanged) {
        await refreshWorkspaceData();
      } else if (dirChanged && state.currentTab === "files") {
        await loadFiles();
      }

      // 5. File drawer
      if (nav.file) {
        if (!state.currentResource || state.currentResource.path !== nav.file) {
          await openFileByPath(nav.file);
        }
      } else {
        if (state.currentResource) {
          closeResourceDrawer(false);
        }
      }
    } finally {
      isPopstateHandling = false;
    }
  }

  async function handleLocationChange() {
    if (window.location.hash === lastHandledHash) return;
    lastHandledHash = window.location.hash;
    const nav = parseHash();
    await applyNavState(nav, false);
  }

  async function loadSpacesAndWorkspaces() {
    state.spaces = await apiFetch("/spaces");
    state.workspaces = await apiFetch("/workspaces");

    const spaceSelect = $("space-select");
    spaceSelect.innerHTML = `<option value="">All Accessible Spaces</option>`;
    state.spaces.forEach((sp) => {
      const opt = document.createElement("option");
      opt.value = sp.id;
      opt.textContent = `${sp.name} (${sp.visibility})`;
      spaceSelect.appendChild(opt);
    });

    const adminSpaceSelect = $("admin-ws-space");
    adminSpaceSelect.innerHTML = "";
    state.spaces.forEach((sp) => {
      const opt = document.createElement("option");
      opt.value = sp.id;
      opt.textContent = `${sp.name} (${sp.id})`;
      adminSpaceSelect.appendChild(opt);
    });

    if (state.currentSpaceId) spaceSelect.value = state.currentSpaceId;
    populateWorkspaceSelect(true);
  }

  function populateWorkspaceSelect(autoRefresh = true) {
    const wsSelect = $("workspace-select");
    const filtered = state.currentSpaceId
      ? state.workspaces.filter((w) => w.space_id === state.currentSpaceId)
      : state.workspaces;

    wsSelect.innerHTML = "";
    filtered.forEach((ws) => {
      const opt = document.createElement("option");
      opt.value = ws.id;
      opt.textContent = `${ws.name} (${ws.slug})`;
      wsSelect.appendChild(opt);
    });

    if (filtered.length > 0) {
      if (!filtered.some((w) => w.id === state.currentWsId)) {
        state.currentWsId = filtered[0].id;
        state.currentDir = ".";
      }
      wsSelect.value = state.currentWsId;
      if (autoRefresh) refreshWorkspaceData();
    } else {
      state.currentWsId = null;
    }
  }

  async function refreshWorkspaceData() {
    if (!state.currentWsId) return;
    await Promise.all([loadWorkspaceOverview(), loadFiles(), loadJobs()]);
  }

  async function loadWorkspaceOverview() {
    const detail = await apiFetch(`/workspaces/${state.currentWsId}`);
    $("overview-title").textContent = detail.name;
    $("overview-path").textContent = detail.path;
    $("ws-visibility-badge").textContent = detail.visibility;
    $("ws-ephemeral-badge").classList.toggle("hidden", !detail.is_ephemeral);

    const quota = detail.storage_quota_bytes;
    const used = detail.used_bytes || 0;
    $("quota-text").textContent = quota ? `${fmtBytes(used)} / ${fmtBytes(quota)}` : `${fmtBytes(used)} / Unlimited`;
    const pct = quota ? Math.min(100, (used / quota) * 100) : 0;
    $("quota-fill").style.width = `${pct}%`;

    const stats = detail.stats;
    if (!stats) {
      $("stat-total-files").textContent = "0";
      $("stat-total-size").textContent = fmtBytes(used);
      $("stat-registered").textContent = "0";
      $("stat-unregistered").textContent = "0";
      $("type-breakdown").innerHTML = `<p class="muted">Workspace not initialized yet.</p>`;
      return;
    }

    $("stat-total-files").textContent = String(stats.total_files);
    $("stat-total-size").textContent = fmtBytes(stats.total_size_bytes);
    $("stat-registered").textContent = String(stats.registered_count);
    $("stat-unregistered").textContent = String(stats.unregistered_count);

    const bd = $("type-breakdown");
    bd.innerHTML = "";
    Object.entries(stats.types_info || {}).forEach(([ftype, info]) => {
      const div = document.createElement("div");
      div.className = "breakdown-item";
      div.innerHTML = `<div class="muted">${ftype}</div><strong>${info.count} files</strong> <small class="muted">(${fmtBytes(info.size_bytes)})</small>`;
      bd.appendChild(div);
    });
  }

  function renderBreadcrumbs() {
    const container = $("file-breadcrumbs");
    container.innerHTML = "";
    const parts = state.currentDir === "." || state.currentDir === "/" ? [] : state.currentDir.split("/").filter(Boolean);
    $("upload-target-label").textContent = parts.length ? `/${parts.join("/")}` : "/";

    const rootBtn = document.createElement("button");
    rootBtn.className = "crumb";
    rootBtn.textContent = "/";
    rootBtn.onclick = () => {
      navigateToDir(".");
    };
    container.appendChild(rootBtn);

    let acc = "";
    parts.forEach((p) => {
      acc = acc ? `${acc}/${p}` : p;
      const target = acc;
      const btn = document.createElement("button");
      btn.className = "crumb";
      btn.textContent = p;
      btn.onclick = () => {
        navigateToDir(target);
      };
      container.appendChild(btn);
    });
  }

  let filesLoadSeq = 0;
  async function loadFiles() {
    if (!state.currentWsId) return;
    renderBreadcrumbs();

    const seq = ++filesLoadSeq;
    const params = new URLSearchParams({ path: state.currentDir });
    if ($("filter-recursive").checked) params.set("recursive", "true");
    if ($("filter-glob").value.trim()) params.set("glob", $("filter-glob").value.trim());
    if ($("filter-type").value) params.set("type", $("filter-type").value);
    if ($("filter-format").value.trim()) params.set("format", $("filter-format").value.trim());
    if ($("filter-registered").value) params.set("registered", $("filter-registered").value);

    const originFilter = $("filter-origin") ? $("filter-origin").value : "";
    let entries = await apiFetch(`/workspaces/${state.currentWsId}/files?${params.toString()}`);
    if (seq !== filesLoadSeq) return; // Discard superseded response
    if (originFilter === "user") {
      entries = entries.filter((e) => e.is_dir || !e.is_generated);
    } else if (originFilter === "system") {
      entries = entries.filter((e) => !e.is_dir && e.is_generated);
    }
    state.lastFiles = entries;
    const tbody = $("files-tbody");
    tbody.innerHTML = "";

    if (entries.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" class="muted">No files match the current path or filters.</td></tr>`;
      return;
    }

    // Group system-generated artifacts under their parent dataset when viewing "all" / unfiltered
    const childrenByParent = new Map();
    const topLevelEntries = [];

    if (!originFilter || originFilter === "all") {
      const userFiles = entries.filter((e) => !e.is_dir && !e.is_generated);
      const byUuid = new Map();
      userFiles.forEach((u) => {
        if (u.uuid) byUuid.set(u.uuid, u);
        childrenByParent.set(u.path, []);
      });

      const findParent = (artifact) => {
        if (artifact.source_uuid && byUuid.has(artifact.source_uuid)) {
          return byUuid.get(artifact.source_uuid);
        }
        return userFiles.find((u) => {
          const stem = u.name.replace(/\.[^/.]+$/, "");
          return artifact.name.startsWith(`${u.name}.`) || artifact.name.startsWith(`${stem}.`);
        });
      };

      entries.forEach((entry) => {
        if (!entry.is_dir && entry.is_generated) {
          const parent = findParent(entry);
          if (parent) {
            childrenByParent.get(parent.path).push(entry);
            return;
          }
        }
        topLevelEntries.push(entry);
      });
    } else {
      topLevelEntries.push(...entries);
    }

    function buildFileRow(entry, { isChild = false, childCount = 0, isExpanded = false } = {}) {
      const tr = document.createElement("tr");
      if (isChild) tr.classList.add("artifact-row");
      if (isChild && !isExpanded) tr.classList.add("hidden");

      const regBadge = entry.is_dir ? "-" : entry.registered ? `<span class="text-ok">✔</span>` : `<span class="text-warn">✘</span>`;
      const typeLabel = entry.is_dir
        ? "directory"
        : entry.file_format && entry.file_format !== "undetermined"
          ? `${entry.file_type}/${entry.file_format}`
          : entry.file_type;

      let fairifyBadge = "";
      if (entry.fairify_status === "up_to_date") {
        fairifyBadge = ` <span class="badge badge-fairify-ok" title="FAIRify artifacts are up-to-date">FAIRified ✔</span>`;
      } else if (entry.fairify_status === "stale") {
        fairifyBadge = ` <span class="badge badge-fairify-stale" title="Source was modified after FAIRification; artifacts outdated">FAIRify Outdated ⚠️</span>`;
      }

      const originBadge = entry.is_dir
        ? "-"
        : (entry.is_generated
            ? `<span class="badge badge-system" title="System-generated artifact${entry.generated_by ? " by " + entry.generated_by : ""}">System</span>`
            : `<span class="badge badge-user" title="User-provided file">User</span>`) + fairifyBadge;

      const applicable = Array.isArray(entry.applicable_actions) && entry.applicable_actions.length > 0
        ? entry.applicable_actions
        : (entry.is_dir ? ["download", "delete"] : ["preview", "download", "delete"]);

      const actions = [];
      if (!entry.is_dir) {
        actions.push(`<button class="btn btn-xs btn-secondary act-dl" title="Download">⬇</button>`);
        if (applicable.includes("fairify")) {
          actions.push(`<button class="btn btn-xs btn-secondary act-fairify" title="Run Fairify (qsv)">✨</button>`);
        }
        if (applicable.includes("tocsv")) {
          actions.push(`<button class="btn btn-xs btn-secondary act-tocsv" title="Convert to CSV (readstat)">📄</button>`);
        }
      }
      actions.push(`<button class="btn btn-xs btn-danger act-del" title="Delete">🗑</button>`);

      const toggleHtml = childCount > 0
        ? `<button class="artifact-toggle-btn" title="Show/hide ${childCount} generated artifact(s)">${isExpanded ? "▾" : "▸"} ${childCount} artifacts</button>`
        : "";

      const nameCellContent = isChild
        ? `<span class="artifact-indent"><span class="artifact-connector">↳</span><button class="file-link">📄 ${entry.name}</button></span>`
        : `<button class="file-link">${entry.is_dir ? "📁 " : "📄 "}${entry.name}${entry.is_dir ? "/" : ""}</button>${toggleHtml}`;

      tr.innerHTML = `
        <td>${regBadge}</td>
        <td>${nameCellContent}</td>
        <td><span class="pill">${typeLabel}</span></td>
        <td>${originBadge}</td>
        <td>${entry.is_dir ? "-" : fmtBytes(entry.size_bytes)}</td>
        <td class="muted">${new Date(entry.modified_at).toLocaleString()}</td>
        <td><div class="actions-row">${actions.join("")}</div></td>
      `;

      tr.querySelector(".file-link").onclick = () => {
        if (entry.is_dir) {
          navigateToDir(entry.path);
        } else {
          openResourceDrawer(entry, true);
        }
      };

      const dlBtn = tr.querySelector(".act-dl");
      if (dlBtn) {
        dlBtn.onclick = () => {
          window.location.href = downloadUrl(
            `/workspaces/${state.currentWsId}/files/download?path=${encodeURIComponent(entry.path)}`
          );
        };
      }

      const fairifyBtn = tr.querySelector(".act-fairify");
      if (fairifyBtn) fairifyBtn.onclick = () => triggerFairify(entry.path);

      const tocsvBtn = tr.querySelector(".act-tocsv");
      if (tocsvBtn) tocsvBtn.onclick = () => triggerToCsv(entry.path);

      tr.querySelector(".act-del").onclick = async () => {
        const confirmMsg = entry.fairify_status
          ? `Delete '${entry.path}' and any related FAIRification artifacts?`
          : `Delete '${entry.path}'?`;
        if (!confirm(confirmMsg)) return;
        try {
          const res = await apiFetch(`/workspaces/${state.currentWsId}/files?path=${encodeURIComponent(entry.path)}`, {
            method: "DELETE",
          });
          const extra =
            res && res.deleted_artifacts && res.deleted_artifacts.length > 0
              ? ` and ${res.deleted_artifacts.length} related artifact(s)`
              : "";
          toast(`Deleted ${entry.path}${extra}`);
          refreshWorkspaceData();
        } catch (err) {
          toast(err.message, true);
        }
      };

      return tr;
    }

    topLevelEntries.forEach((entry) => {
      const children = childrenByParent.get(entry.path) || [];
      const isExpanded = !state.collapsedArtifactParents.has(entry.path);
      const parentRow = buildFileRow(entry, {
        isChild: false,
        childCount: children.length,
        isExpanded,
      });
      tbody.appendChild(parentRow);

      const childRows = children.map((child) => {
        const childRow = buildFileRow(child, { isChild: true, isExpanded });
        tbody.appendChild(childRow);
        return childRow;
      });

      const toggleBtn = parentRow.querySelector(".artifact-toggle-btn");
      if (toggleBtn) {
        toggleBtn.onclick = (ev) => {
          ev.stopPropagation();
          const nowCollapsed = !state.collapsedArtifactParents.has(entry.path);
          if (nowCollapsed) {
            state.collapsedArtifactParents.add(entry.path);
          } else {
            state.collapsedArtifactParents.delete(entry.path);
          }
          const isExp = !nowCollapsed;
          toggleBtn.textContent = `${isExp ? "▾" : "▸"} ${children.length} artifacts`;
          childRows.forEach((r) => r.classList.toggle("hidden", !isExp));
        };
      }
    });
  }

  async function uploadFiles(fileList) {
    if (!state.currentWsId || !fileList || fileList.length === 0) return;
    const form = new FormData();
    Array.from(fileList).forEach((f) => form.append("files", f));
    form.append("target_dir", state.currentDir === "." ? "/" : state.currentDir);
    form.append("overwrite", $("upload-overwrite").checked ? "true" : "false");
    form.append("auto_scan", "true");

    try {
      const res = await apiFetch(`/workspaces/${state.currentWsId}/files/upload`, {
        method: "POST",
        body: form,
      });
      toast(`Uploaded ${res.length} file(s) and registered in KB.`);
      await refreshWorkspaceData();
    } catch (err) {
      toast(`Upload failed: ${err.message}`, true);
    }
  }

  async function openResourceDrawer(entry, push = true) {
    state.currentResource = entry;
    $("resource-drawer").classList.remove("hidden");
    $("drawer-title").textContent = entry.name;
    $("drawer-subtitle").textContent = entry.path;

    const applicable = Array.isArray(entry.applicable_actions) ? entry.applicable_actions : [];
    const canFairify = applicable.includes("fairify");
    const canToCsv = applicable.includes("tocsv");

    const fairifyBtn = $("drawer-fairify-btn");
    if (fairifyBtn) {
      fairifyBtn.style.display = canFairify ? "inline-flex" : "none";
      fairifyBtn.onclick = () => triggerFairify(entry.path);
    }
    const tocsvBtn = $("drawer-tocsv-btn");
    if (tocsvBtn) {
      tocsvBtn.style.display = canToCsv ? "inline-flex" : "none";
      tocsvBtn.onclick = () => triggerToCsv(entry.path);
    }

    $("drawer-download-btn").onclick = () => {
      window.location.href = downloadUrl(
        `/workspaces/${state.currentWsId}/files/download?path=${encodeURIComponent(entry.path)}`
      );
    };

    const aboutEl = $("dtab-about");
    if (entry.registered) {
      try {
        const detail = await apiFetch(
          `/workspaces/${state.currentWsId}/resources/${encodeURIComponent(entry.uuid || entry.path)}`
        );
        const detailActions = Array.isArray(detail.applicable_actions) ? detail.applicable_actions : applicable;
        const detailCanFairify = detailActions.includes("fairify");
        const detailCanToCsv = detailActions.includes("tocsv");
        if (fairifyBtn) {
          if (detailCanFairify) {
            fairifyBtn.style.display = "inline-flex";
            fairifyBtn.textContent = "✨ Fairify";
            fairifyBtn.onclick = () => triggerFairify(entry.path, false);
          } else if (detail.fairify_status === "up_to_date") {
            fairifyBtn.style.display = "inline-flex";
            fairifyBtn.textContent = "✨ Re-Fairify (Force)";
            fairifyBtn.onclick = () => {
              if (confirm(`'${entry.path}' has already been FAIRified. Force re-run?`)) {
                triggerFairify(entry.path, true);
              }
            };
          } else {
            fairifyBtn.style.display = "none";
          }
        }
        if (tocsvBtn) tocsvBtn.style.display = detailCanToCsv ? "inline-flex" : "none";

        let fairifyStatusRow = "";
        if (detail.fairify_status === "up_to_date") {
          fairifyStatusRow = `<tr><td class="muted">FAIRify Status</td><td><span class="badge badge-fairify-ok">FAIRified (Up to date) ✔</span></td></tr>`;
        } else if (detail.fairify_status === "stale") {
          fairifyStatusRow = `<tr><td class="muted">FAIRify Status</td><td><span class="badge badge-fairify-stale">FAIRify Outdated ⚠️ (modified after generation)</span></td></tr>`;
        }

        const sourceRow = detail.source_uuid
          ? `<tr><td class="muted">Source Dataset</td><td class="mono">${detail.source_uuid}</td></tr>`
          : "";
        const originRow = `<tr><td class="muted">Origin</td><td>${
          detail.is_generated
            ? `<span class="badge badge-system">System Generated${detail.generated_by ? " (" + detail.generated_by + ")" : ""}</span>`
            : '<span class="badge badge-user">User Provided</span>'
        }</td></tr>`;
        const applicableRow = `<tr><td class="muted">Applicable Actions</td><td>${
          detailActions.map((a) => `<span class="pill-action">${a}</span>`).join(" ") || "-"
        }</td></tr>`;

        const attrRows = Object.entries(detail.attributes || {})
          .map(([k, v]) => `<tr><td class="muted">${k}</td><td class="mono">${v}</td></tr>`)
          .join("");
        aboutEl.innerHTML = `
          <table class="kv-table">
            <tr><td class="muted">UUID</td><td class="mono">${detail.uuid}</td></tr>
            <tr><td class="muted">Path</td><td class="mono">${detail.path}</td></tr>
            ${sourceRow}
            ${originRow}
            ${fairifyStatusRow}
            ${applicableRow}
            <tr><td class="muted">Type</td><td>${detail.type}</td></tr>
            <tr><td class="muted">Format</td><td>${detail.file_format}</td></tr>
            <tr><td class="muted">MIME</td><td>${detail.mime_type || "-"}</td></tr>
            <tr><td class="muted">Size</td><td>${fmtBytes(detail.size_bytes)} (${detail.size_bytes} B)</td></tr>
            <tr><td class="muted">BLAKE3</td><td class="mono">${detail.blake3_hash}</td></tr>
            <tr><td class="muted">Created</td><td>${detail.created_at}</td></tr>
            <tr><td class="muted">Modified</td><td>${detail.updated_at}</td></tr>
            ${attrRows}
          </table>`;
      } catch (err) {
        aboutEl.innerHTML = `<p class="muted">Could not load resource metadata: ${err.message}</p>`;
      }
    } else {
      aboutEl.innerHTML = `<p class="muted">File is unregistered. Run Scan Workspace to index it in the Knowledge Base.</p>`;
    }

    loadPreview();
    if (entry.registered && entry.uuid) loadRdf();
    else $("rdf-content").textContent = "Resource is not registered in Knowledge Base.";

    if (push && !isPopstateHandling) {
      history.pushState({ drawerOpened: true }, "", buildHash({ file: entry.path }));
    }
  }

  async function loadPreview() {
    if (!state.currentResource) return;
    try {
      const mode = $("preview-mode").value;
      const lines = $("preview-lines").value || 25;
      const res = await apiFetch(
        `/workspaces/${state.currentWsId}/files/preview?path=${encodeURIComponent(state.currentResource.path)}&mode=${mode}&lines=${lines}`
      );
      $("preview-content").textContent = res.lines.join("\n") || "(empty file)";
    } catch (err) {
      $("preview-content").textContent = `Preview error: ${err.message}`;
    }
  }

  async function loadRdf() {
    if (!state.currentResource || !state.currentResource.uuid) return;
    try {
      const fmt = $("rdf-format").value;
      const txt = await apiFetch(
        `/workspaces/${state.currentWsId}/resources/${state.currentResource.uuid}/rdf?format=${encodeURIComponent(fmt)}`
      );
      $("rdf-content").textContent = typeof txt === "string" ? txt : JSON.stringify(txt, null, 2);
    } catch (err) {
      $("rdf-content").textContent = `RDF error: ${err.message}`;
    }
  }

  function subscribeJobSse(jobId) {
    if (state.eventSources.has(jobId)) return;
    const url = downloadUrl(`/jobs/${jobId}/events`);
    const es = new EventSource(url);
    state.eventSources.set(jobId, es);

    es.addEventListener("task_event", (ev) => {
      const data = JSON.parse(ev.data);
      loadJobs();
      if (["completed", "failed", "cancelled"].includes(data.status)) {
        es.close();
        state.eventSources.delete(jobId);
        toast(`Job ${data.task_type} ${data.status}: ${data.current_step}`, data.status === "failed");
        refreshWorkspaceData();
      }
    });
    es.onerror = () => {
      es.close();
      state.eventSources.delete(jobId);
    };
  }

  async function triggerScan(clean = false) {
    try {
      const job = await apiFetch(`/workspaces/${state.currentWsId}/scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_path: ".", clean }),
      });
      toast(`Scan job queued (${job.id.slice(0, 8)})`);
      subscribeJobSse(job.id);
      loadJobs();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function triggerFairify(sourcePath, force = false) {
    try {
      const job = await apiFetch(`/workspaces/${state.currentWsId}/fairify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_path: sourcePath, describe: false, force }),
      });
      toast(`Fairify job queued for ${sourcePath}`);
      subscribeJobSse(job.id);
      loadJobs();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function triggerToCsv(sourcePath) {
    try {
      const job = await apiFetch(`/workspaces/${state.currentWsId}/readstat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_path: sourcePath, to_csv: true }),
      });
      toast(`ToCSV job queued for ${sourcePath}`);
      subscribeJobSse(job.id);
      loadJobs();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function loadJobs() {
    const jobs = await apiFetch(`/jobs?limit=30`);
    const active = jobs.filter((j) => ["pending", "running"].includes(j.status));
    const badge = $("active-job-count");
    badge.textContent = String(active.length);
    badge.classList.toggle("hidden", active.length === 0);

    active.forEach((j) => subscribeJobSse(j.id));

    const container = $("jobs-list");
    container.innerHTML = "";
    if (jobs.length === 0) {
      container.innerHTML = `<div class="card muted">No jobs recorded yet.</div>`;
      return;
    }

    jobs.forEach((job) => {
      const card = document.createElement("div");
      card.className = "job-card";
      const logsTail = (job.logs || []).slice(-6).join("\n");
      card.innerHTML = `
        <div class="card-head">
          <div>
            <strong>${job.task_type.toUpperCase()}</strong>
            <span class="pill">${job.status}</span>
            <span class="mono muted">${job.id.slice(0, 8)}</span>
          </div>
          <div>
            <span class="muted">${Math.round(job.progress_pct)}%</span>
            ${["pending", "running"].includes(job.status) ? `<button class="btn btn-xs btn-danger btn-cancel-job">Cancel</button>` : ""}
          </div>
        </div>
        <div class="muted">${job.current_step}</div>
        <div class="progress-track"><div class="progress-bar" style="width:${job.progress_pct}%"></div></div>
        ${job.error ? `<div class="text-warn">${job.error}</div>` : ""}
        ${logsTail ? `<pre class="code-box mt-sm">${logsTail}</pre>` : ""}
      `;
      const cancelBtn = card.querySelector(".btn-cancel-job");
      if (cancelBtn) {
        cancelBtn.onclick = async () => {
          await apiFetch(`/jobs/${job.id}/cancel`, { method: "POST" });
          loadJobs();
        };
      }
      container.appendChild(card);
    });
  }

  function renderResultTable(targetEl, cols, rows) {
    if (!cols || cols.length === 0) {
      targetEl.innerHTML = `<p class="muted">Query executed with 0 columns.</p>`;
      return;
    }
    const header = cols.map((c) => `<th>${c}</th>`).join("");
    const body = rows
      .map((r) => `<tr>${cols.map((c) => `<td>${r[c] ?? ""}</td>`).join("")}</tr>`)
      .join("");
    targetEl.innerHTML = `<div class="table-wrap"><table><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  async function loadAdminData() {
    if (state.principal.tier !== "admin") return;
    try {
      const [keys, tools] = await Promise.all([apiFetch("/admin/api-keys"), apiFetch("/system/tools")]);
      const keysEl = $("api-keys-list");
      keysEl.innerHTML = keys.length === 0 ? `<p class="muted">No DB API keys issued yet.</p>` : "";
      keys.forEach((k) => {
        const row = document.createElement("div");
        row.className = "tool-row mt-sm";
        row.innerHTML = `
          <div><strong>${k.label}</strong> <span class="pill">${k.tier}</span> <span class="mono muted">${k.key_prefix}...</span></div>
          <button class="btn btn-xs btn-danger">Revoke</button>
        `;
        row.querySelector("button").onclick = async () => {
          await apiFetch(`/admin/api-keys/${k.id}`, { method: "DELETE" });
          loadAdminData();
        };
        keysEl.appendChild(row);
      });

      const toolsEl = $("system-tools-list");
      toolsEl.innerHTML = "";
      Object.entries(tools).forEach(([name, info]) => {
        const row = document.createElement("div");
        row.className = "tool-row";
        row.innerHTML = `
          <div><strong>${name}</strong> <span class="mono muted">${info.version || info.path || ""}</span></div>
          <span class="${info.available ? "text-ok" : "text-warn"}">${info.available ? "Available" : "Missing"}</span>
        `;
        toolsEl.appendChild(row);
      });
    } catch (err) {
      toast(`Admin load failed: ${err.message}`, true);
    }
  }

  // Event bindings
  window.addEventListener("popstate", handleLocationChange);
  window.addEventListener("hashchange", handleLocationChange);
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.currentResource) {
      closeResourceDrawer(true);
    }
  });

  document.querySelectorAll(".tab").forEach((btn) => {
    btn.onclick = () => {
      switchTab(btn.dataset.tab, true);
    };
  });

  document.querySelectorAll(".dtab").forEach((btn) => {
    btn.onclick = () => {
      document.querySelectorAll(".dtab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".dtab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      $(`dtab-${btn.dataset.dtab}`).classList.add("active");
    };
  });

  $("theme-toggle").onclick = () => {
    const html = document.documentElement;
    html.dataset.theme = html.dataset.theme === "dark" ? "light" : "dark";
  };

  $("open-auth-modal").onclick = () => {
    $("api-key-input").value = state.apiKey;
    $("auth-modal").classList.remove("hidden");
  };
  $("close-auth-modal").onclick = () => $("auth-modal").classList.add("hidden");
  $("save-api-key").onclick = async () => {
    state.apiKey = $("api-key-input").value.trim();
    if (state.apiKey) sessionStorage.setItem("dartfx_api_key", state.apiKey);
    else sessionStorage.removeItem("dartfx_api_key");
    $("auth-modal").classList.add("hidden");
    await bootstrap();
  };
  $("clear-api-key").onclick = async () => {
    state.apiKey = "";
    sessionStorage.removeItem("dartfx_api_key");
    $("auth-modal").classList.add("hidden");
    await bootstrap();
  };

  $("space-select").onchange = (e) => {
    state.currentSpaceId = e.target.value || null;
    populateWorkspaceSelect();
    syncUrl(true);
  };
  $("workspace-select").onchange = (e) => {
    state.currentWsId = e.target.value;
    state.currentDir = ".";
    if (state.currentResource) closeResourceDrawer(false);
    refreshWorkspaceData();
    syncUrl(true);
  };

  $("btn-scan").onclick = () => triggerScan(false);
  $("btn-scan-clean").onclick = () => triggerScan(true);
  $("btn-download-ws-zip").onclick = () => {
    window.location.href = downloadUrl(`/workspaces/${state.currentWsId}/files/archive?path=.`);
  };
  $("btn-download-folder-zip").onclick = () => {
    window.location.href = downloadUrl(
      `/workspaces/${state.currentWsId}/files/archive?path=${encodeURIComponent(state.currentDir)}`
    );
  };
  $("btn-mkdir").onclick = async () => {
    const name = prompt("New folder name (relative to current directory):");
    if (!name) return;
    const base = state.currentDir === "." ? "" : `${state.currentDir}/`;
    try {
      await apiFetch(`/workspaces/${state.currentWsId}/files/mkdir`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: `${base}${name}` }),
      });
      loadFiles();
    } catch (err) {
      toast(err.message, true);
    }
  };

  let filterDebounceTimer = null;
  function debouncedLoadFiles(delay = 250) {
    clearTimeout(filterDebounceTimer);
    filterDebounceTimer = setTimeout(() => {
      loadFiles();
    }, delay);
  }

  // Instant filtering on select dropdowns and recursive checkbox
  ["filter-type", "filter-origin", "filter-registered", "filter-recursive"].forEach((id) => {
    const el = $(id);
    if (el) {
      el.onchange = () => {
        clearTimeout(filterDebounceTimer);
        loadFiles();
      };
    }
  });

  // Debounced live filtering on text / glob search inputs with Enter shortcut
  ["filter-glob", "filter-format"].forEach((id) => {
    const el = $(id);
    if (el) {
      el.oninput = () => debouncedLoadFiles(250);
      el.onsearch = () => {
        clearTimeout(filterDebounceTimer);
        loadFiles();
      };
      el.onkeydown = (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          clearTimeout(filterDebounceTimer);
          loadFiles();
        }
      };
    }
  });

  $("btn-refresh-files").onclick = () => {
    clearTimeout(filterDebounceTimer);
    loadFiles();
  };
  $("btn-browse-upload").onclick = () => $("file-input").click();
  $("file-input").onchange = (e) => uploadFiles(e.target.files);
  const dz = $("dropzone");
  dz.ondragover = (e) => {
    e.preventDefault();
    dz.classList.add("dragover");
  };
  dz.ondragleave = () => dz.classList.remove("dragover");
  dz.ondrop = (e) => {
    e.preventDefault();
    dz.classList.remove("dragover");
    uploadFiles(e.dataTransfer.files);
  };

  $("close-drawer").onclick = () => closeResourceDrawer(true);
  $("btn-reload-preview").onclick = loadPreview;
  $("btn-reload-rdf").onclick = loadRdf;
  $("btn-download-rdf").onclick = () => {
    if (!state.currentResource || !state.currentResource.uuid) return;
    const fmt = $("rdf-format").value;
    window.location.href = downloadUrl(
      `/workspaces/${state.currentWsId}/resources/${state.currentResource.uuid}/rdf?format=${encodeURIComponent(fmt)}`
    );
  };

  document.querySelectorAll(".sparql-preset").forEach((btn) => {
    btn.onclick = () => {
      if (btn.dataset.preset === "data") {
        $("sparql-input").value = `PREFIX dartfx: <https://dataartifex.org/workspace/>\nSELECT ?path ?format ?size WHERE {\n  ?s a dartfx:FileResource ;\n     dartfx:path ?path ;\n     dartfx:fileType "data" ;\n     dartfx:fileFormat ?format ;\n     dartfx:sizeBytes ?size .\n}`;
      } else {
        $("sparql-input").value = `PREFIX dartfx: <https://dataartifex.org/workspace/>\nSELECT ?path ?type ?format ?size WHERE {\n  ?s a dartfx:FileResource ;\n     dartfx:path ?path ;\n     dartfx:fileType ?type ;\n     dartfx:fileFormat ?format ;\n     dartfx:sizeBytes ?size .\n} ORDER BY ?path`;
      }
    };
  });

  $("btn-run-sparql").onclick = async () => {
    try {
      const res = await apiFetch(`/workspaces/${state.currentWsId}/sparql`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: $("sparql-input").value }),
      });
      $("sparql-meta").textContent = `${res.row_count} rows`;
      renderResultTable($("sparql-results"), res.columns, res.rows);
    } catch (err) {
      toast(err.message, true);
    }
  };

  $("btn-run-sql").onclick = async () => {
    try {
      const res = await apiFetch(`/workspaces/${state.currentWsId}/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sql: $("sql-input").value, max_rows: 500 }),
      });
      $("sql-meta").textContent = `${res.row_count} rows`;
      renderResultTable($("sql-results"), res.columns, res.rows);
    } catch (err) {
      toast(err.message, true);
    }
  };

  $("btn-refresh-jobs").onclick = loadJobs;
  $("btn-refresh-tools").onclick = loadAdminData;

  $("btn-reset-ephemeral").onclick = async () => {
    if (!confirm("Reset the Public Ephemeral Sandbox to its default sample state?")) return;
    try {
      await apiFetch("/admin/ephemeral/reset", { method: "POST" });
      toast("Public Ephemeral Sandbox reset.");
      await bootstrap();
    } catch (err) {
      toast(err.message, true);
    }
  };

  $("btn-wipe-kb").onclick = async () => {
    if (!confirm(`Wipe RDF Knowledge Base for workspace '${state.currentWsId}'?`)) return;
    try {
      await apiFetch(`/admin/workspaces/${state.currentWsId}/kb`, { method: "DELETE" });
      toast("Workspace KB wiped.");
      refreshWorkspaceData();
    } catch (err) {
      toast(err.message, true);
    }
  };

  $("form-create-space").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const quotaMb = fd.get("quota_mb");
    try {
      await apiFetch("/admin/spaces", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slug: fd.get("slug"),
          name: fd.get("name"),
          description: fd.get("description") || "",
          visibility: fd.get("visibility"),
          storage_quota_bytes: quotaMb ? Number(quotaMb) * 1024 * 1024 : null,
          root_path: fd.get("root_path") || null,
        }),
      });
      e.target.reset();
      toast("Space created.");
      await loadSpacesAndWorkspaces();
    } catch (err) {
      toast(err.message, true);
    }
  };

  $("form-create-workspace").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const quotaMb = fd.get("quota_mb");
    try {
      await apiFetch("/admin/workspaces", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slug: fd.get("slug"),
          name: fd.get("name"),
          space_id: fd.get("space_id") || null,
          visibility: fd.get("visibility"),
          team_id: fd.get("team_id") || null,
          storage_quota_bytes: quotaMb ? Number(quotaMb) * 1024 * 1024 : null,
          path: fd.get("path") || null,
          initialize: true,
          create_dirs: true,
        }),
      });
      e.target.reset();
      toast("Workspace created.");
      await loadSpacesAndWorkspaces();
    } catch (err) {
      toast(err.message, true);
    }
  };

  $("form-create-key").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const parseCsv = (v) =>
      String(v || "")
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    try {
      const created = await apiFetch("/admin/api-keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          label: fd.get("label"),
          tier: fd.get("tier"),
          team_ids: parseCsv(fd.get("team_ids")),
          allowed_space_ids: parseCsv(fd.get("allowed_space_ids")),
        }),
      });
      e.target.reset();
      const banner = $("new-key-banner");
      banner.textContent = `New ${created.info.tier} API key (save now, shown once): ${created.key}`;
      banner.classList.remove("hidden");
      loadAdminData();
    } catch (err) {
      toast(err.message, true);
    }
  };

  async function bootstrap() {
    await refreshWhoami();
    const nav = parseHash();
    if (nav.space) state.currentSpaceId = nav.space;
    if (nav.ws) state.currentWsId = nav.ws;
    if (nav.dir) state.currentDir = nav.dir;
    if (nav.tab) state.currentTab = nav.tab;

    await loadSpacesAndWorkspaces();
    await applyNavState(nav, true);
    syncUrl(false);

    if (state.principal.tier === "admin") loadAdminData();
  }

  bootstrap().catch((err) => toast(`Initialization error: ${err.message}`, true));
})();
