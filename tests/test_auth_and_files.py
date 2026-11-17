"""Tests for 3-tier authentication, spaces, workspace lifecycle, file upload/download, and KB resources."""

import io
import json
import zipfile

from fastapi.testclient import TestClient

from tests.conftest import ADMIN_KEY, USER_KEY


def test_three_tier_access_and_api_key_lifecycle(client: TestClient) -> None:
    # 1. Anonymous user sees only public space and sandbox
    who_anon = client.get("/auth/whoami")
    assert who_anon.status_code == 200
    assert who_anon.json()["tier"] == "anonymous"

    spaces_anon = client.get("/spaces").json()
    assert [s["id"] for s in spaces_anon] == ["public"]

    ws_anon = client.get("/workspaces").json()
    assert [w["id"] for w in ws_anon] == ["sandbox"]

    # Anonymous cannot access admin endpoints
    assert client.get("/system/tools").status_code == 403
    assert client.post("/admin/ephemeral/reset").status_code == 403

    # Invalid API key is rejected
    assert client.get("/auth/whoami", headers={"X-API-Key": "invalid-token"}).status_code == 401

    # 2. Admin creates a team Space, team Workspace, private Workspace, and a Team User API key
    admin_hdr = {"X-API-Key": ADMIN_KEY}
    who_admin = client.get("/auth/whoami", headers=admin_hdr)
    assert who_admin.json()["tier"] == "admin"

    sp_res = client.post(
        "/admin/spaces",
        headers=admin_hdr,
        json={"slug": "team-alpha-space", "name": "Team Alpha Space", "visibility": "team"},
    )
    assert sp_res.status_code == 201
    space_id = sp_res.json()["id"]

    ws_team_res = client.post(
        "/admin/workspaces",
        headers=admin_hdr,
        json={
            "slug": "alpha-data",
            "name": "Alpha Shared Data",
            "space_id": space_id,
            "visibility": "team",
            "team_id": "alpha",
        },
    )
    assert ws_team_res.status_code == 201

    ws_priv_res = client.post(
        "/admin/workspaces",
        headers=admin_hdr,
        json={
            "slug": "secret-ws",
            "name": "Secret Workspace",
            "space_id": space_id,
            "visibility": "private",
            "owner_id": "someone-else",
        },
    )
    assert ws_priv_res.status_code == 201

    # Issue a DB-backed user key scoped to team "alpha"
    key_res = client.post(
        "/admin/api-keys",
        headers=admin_hdr,
        json={
            "label": "Alice Alpha",
            "tier": "user",
            "team_ids": ["alpha"],
            "allowed_space_ids": [space_id],
        },
    )
    assert key_res.status_code == 201
    alice_key = key_res.json()["key"]
    assert alice_key.startswith("dartfx_")
    key_id = key_res.json()["info"]["id"]

    # 3. Anonymous cannot access team or private workspaces
    assert client.get("/workspaces/alpha-data").status_code == 401
    assert client.get("/workspaces/secret-ws").status_code == 401

    # Env user (no teams) cannot access team-alpha or someone-else's private workspace
    env_user_hdr = {"Authorization": f"Bearer {USER_KEY}"}
    assert client.get("/workspaces/alpha-data", headers=env_user_hdr).status_code == 403

    # Alice can access sandbox + alpha-data, but NOT secret-ws
    alice_hdr = {"X-API-Key": alice_key}
    alice_ws_ids = {w["id"] for w in client.get("/workspaces", headers=alice_hdr).json()}
    assert {"sandbox", "alpha-data"}.issubset(alice_ws_ids)
    assert "secret-ws" not in alice_ws_ids
    assert client.get("/workspaces/secret-ws", headers=alice_hdr).status_code == 403

    # Revoke Alice's key
    assert client.delete(f"/admin/api-keys/{key_id}", headers=admin_hdr).status_code == 200
    assert client.get("/auth/whoami", headers=alice_hdr).status_code == 401


def test_file_upload_download_archive_and_quota_enforcement(client: TestClient) -> None:
    # Sandbox starts with sample files inside data, docs, meta
    files_res = client.get("/workspaces/sandbox/files", params={"recursive": "true", "type": "^data"})
    assert files_res.status_code == 200
    assert any(f["path"] == "data/sample_survey.csv" for f in files_res.json())

    tree_res = client.get("/workspaces/sandbox/tree")
    assert tree_res.status_code == 200
    assert tree_res.json()["is_dir"] is True

    preview_res = client.get("/workspaces/sandbox/files/preview", params={"path": "data/sample_survey.csv", "lines": 2})
    assert preview_res.status_code == 200
    assert len(preview_res.json()["lines"]) == 2

    # Upload a new CSV file to data/
    upload_res = client.post(
        "/workspaces/sandbox/files/upload",
        data={"target_dir": "data", "overwrite": "false", "auto_scan": "true"},
        files=[("files", ("metrics.csv", b"k,v\nx,10\ny,20\n", "text/csv"))],
    )
    assert upload_res.status_code == 201
    uploaded = upload_res.json()[0]
    assert uploaded["path"] == "data/metrics.csv"
    assert uploaded["registered"] is True
    assert uploaded["uuid"] is not None

    # Exceeding public quota (4096 bytes in test fixture) returns 413
    oversized_res = client.post(
        "/workspaces/sandbox/files/upload",
        data={"target_dir": "data"},
        files=[("files", ("huge.bin", b"A" * 10000, "application/octet-stream"))],
    )
    assert oversized_res.status_code == 413

    # Download by path and by UUID
    dl_path = client.get("/workspaces/sandbox/files/download", params={"path": "data/metrics.csv"})
    assert dl_path.status_code == 200
    assert dl_path.content == b"k,v\nx,10\ny,20\n"
    assert "ETag" in dl_path.headers

    dl_uuid = client.get("/workspaces/sandbox/files/download", params={"uuid": uploaded["uuid"]})
    assert dl_uuid.status_code == 200
    assert dl_uuid.content == b"k,v\nx,10\ny,20\n"

    # Download ZIP archive of data/
    zip_res = client.get("/workspaces/sandbox/files/archive", params={"path": "data"})
    assert zip_res.status_code == 200
    assert zip_res.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(zip_res.content), "r") as zf:
        names = set(zf.namelist())
        assert "data/sample_survey.csv" in names
        assert "data/metrics.csv" in names

    # Resource metadata + RDF export + PATCH update
    res_detail = client.get(f"/workspaces/sandbox/resources/{uploaded['uuid']}")
    assert res_detail.status_code == 200
    assert res_detail.json()["file_format"] == "csv"

    patch_res = client.patch(
        f"/workspaces/sandbox/resources/{uploaded['uuid']}",
        json={"attributes": {"domain": "economics"}},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["attributes"]["domain"] == "economics"

    ttl_res = client.get(f"/workspaces/sandbox/resources/{uploaded['uuid']}/rdf", params={"format": "turtle"})
    assert ttl_res.status_code == 200
    assert "economics" in ttl_res.text

    jsonld_res = client.get(f"/workspaces/sandbox/resources/{uploaded['uuid']}/rdf", params={"format": "json-ld"})
    assert jsonld_res.status_code == 200
    assert json.loads(jsonld_res.text)

    # Folder mkdir, move, copy, delete
    assert client.post("/workspaces/sandbox/files/mkdir", json={"path": "prod/release"}).status_code == 201
    assert (
        client.post(
            "/workspaces/sandbox/files/copy",
            json={"src_path": "data/metrics.csv", "dst_path": "prod/release/metrics_copy.csv"},
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/workspaces/sandbox/files/move",
            json={"src_path": "prod/release/metrics_copy.csv", "dst_path": "prod/release/metrics_final.csv"},
        ).status_code
        == 200
    )
    assert (
        client.delete("/workspaces/sandbox/files", params={"path": "prod/release/metrics_final.csv"}).status_code == 200
    )

    # Admin reset ephemeral restores initial state without metrics.csv
    reset_res = client.post("/admin/ephemeral/reset", headers={"X-API-Key": ADMIN_KEY})
    assert reset_res.status_code == 200
    assert client.get("/workspaces/sandbox/files/download", params={"path": "data/metrics.csv"}).status_code == 404
