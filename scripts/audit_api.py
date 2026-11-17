#!/usr/bin/env python3
"""Programmatic audit and alignment tool for Data Artifex API services.

Verifies that an API project repository complies 100% with the canonical
template specifications (files, package versions, Docker isolation, compose
overrides, Nginx/Kong reverse proxy integration, and health checks).

Usage:
    # Audit current directory:
    python scripts/audit_api_project.py

    # Audit a specific target repository:
    python scripts/audit_api_project.py /path/to/target-api

    # Automatically fix missing configuration files and gitignore entries:
    python scripts/audit_api_project.py --fix

    # Output machine-readable JSON:
    python scripts/audit_api_project.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# =============================================================================
# Canonical Template Standards
# =============================================================================

REQUIRED_CORE_PACKAGES: dict[str, str] = {
    "fastapi": r"fastapi>=0\.135\.1",
    "uvicorn": r"uvicorn>=0\.41\.0",
    "gunicorn": r"gunicorn>=23\.0\.0",
    "pydantic": r"pydantic>=2\.0\.0",
    "python-dotenv": r"python-dotenv>=1\.0\.0",
    "httpx": r"httpx>=0\.28\.1",
}

REQUIRED_DEV_PACKAGES: dict[str, str] = {
    "ruff": r"ruff>=0\.3\.0",
    "pyrefly": r"pyrefly>=1\.0\.0",
    "pytest": r"pytest>=9",
    "coverage": r"coverage\[toml\]>=7\.0\.0",
}

CANONICAL_DOCKERIGNORE = """# Git & CI
.git
.gitignore
.github
.pre-commit-config.yaml

# Virtual environments & caches
.venv
venv
env
.hatch
.pytest_cache
.ruff_cache
.mypy_cache
.pyre
.pytype
.benchmarks
.cache
.coverage
.coverage.*
htmlcov
__pycache__
*.py[cod]
*$py.class

# Build artifacts & docs
build
dist
*.egg-info
docs
site

# Local environment & compose overrides (prevent leaking secrets/local paths into image)
.env
.env.*
docker-compose*.yaml
docker-compose*.yml
compose*.yaml
compose*.yml

# Temp & OS files
temp
tmp
.DS_Store
.vscode
.idea
*.log
"""

CANONICAL_GITIGNORE_SNIPPET = """
# Environments
.env
.env.*
!.env.example

# Docker Compose local/server overrides (keep .example tracked)
docker-compose.override.yaml
docker-compose.override.yml
compose.override.yaml
compose.override.yml
"""

CANONICAL_ENV_EXAMPLE = """# =============================================================================
# Environment Configuration Template for {project_name}
# Copy this file to .env on staging/production servers:
#   cp .env.example .env
# (.env is git-ignored so git pull will never overwrite server settings)
# =============================================================================

# Docker image tag to pull/run (e.g., latest, staging, 0.1.0)
IMAGE_TAG=latest

# Container name override
CONTAINER_NAME={project_name}

# Host interface & port binding
# Use 127.0.0.1 when running behind a local reverse proxy/gateway (Nginx, Kong, Caddy)
# Use 0.0.0.0 to expose the port on all network interfaces
HOST_BIND_IP=127.0.0.1
HOST_PORT=8000

# Reverse Proxy / API Gateway (Nginx & Kong) settings
# Note: Kong (with strip_path=true) and Nginx sending X-Forwarded-Prefix are detected
# automatically. You only need UVICORN_ROOT_PATH if your proxy strips the prefix without
# sending the X-Forwarded-Prefix header.
UVICORN_ROOT_PATH=
UVICORN_WORKERS=1
FORWARDED_ALLOW_IPS=*

# CORS configuration
# Set to "*" (default), comma-separated origins ("https://app.example.com"),
# or "" (empty) to disable app-level CORS when Kong's CORS plugin handles it.
CORS_ORIGINS=*
"""

CANONICAL_OVERRIDE_EXAMPLE = """# =============================================================================
# Docker Compose Override Template for {project_name}
# Copy this file to docker-compose.override.yaml on staging/production/dev hosts:
#   cp docker-compose.override.example.yaml docker-compose.override.yaml
# (docker-compose.override.yaml is git-ignored and automatically merged by Docker Compose)
# =============================================================================

services:
  {project_name}:
    # --- Example 1: Connect directly to an external Kong / Nginx Docker network ---
    # When Kong runs in Docker on the same host (e.g., network "kong-net"), attach this
    # service to that network so Kong can route to http://{project_name}:8000 directly
    # without needing host port publishing:
    # networks:
    #   - dartfx
    #   - kong-net

    # --- Example 2: Environment-specific overrides ---
    # environment:
    #   - UVICORN_WORKERS=4
    #   - CORS_ORIGINS=   # Disable app CORS if Kong CORS plugin is active

    # --- Example 3: Persistent volume mounts ---
    # volumes:
    #   - ./cache:/app/cache

    # --- Example 4: Resource limits for production ---
    # deploy:
    #   resources:
    #     limits:
    #       cpus: "1.0"
    #       memory: 512M
    environment: []

# networks:
#   kong-net:
#     external: true
"""


# =============================================================================
# Audit Data Structures
# =============================================================================


@dataclass
class CheckItem:
    category: str
    name: str
    passed: bool
    severity: str  # "error", "warning"
    details: str
    remediation: str = ""
    can_autofix: bool = False


@dataclass
class AuditReport:
    project_path: Path
    project_name: str
    checks: list[CheckItem] = field(default_factory=list)

    def add(
        self,
        category: str,
        name: str,
        passed: bool,
        severity: str,
        details: str,
        remediation: str = "",
        can_autofix: bool = False,
    ):
        self.checks.append(
            CheckItem(
                category=category,
                name=name,
                passed=passed,
                severity=severity,
                details=details,
                remediation=remediation,
                can_autofix=can_autofix,
            )
        )

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def error_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed and c.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed and c.severity == "warning")

    @property
    def score_percentage(self) -> float:
        if not self.checks:
            return 0.0
        return round((self.passed_count / len(self.checks)) * 100, 1)

    def to_markdown(self) -> str:
        md = []
        md.append("# API Template Compliance Audit")
        md.append(f"**Target**: `{self.project_path.resolve()}` (Project: `{self.project_name}`)\n")
        status = (
            "🟢 PASS (100% Compliant)"
            if self.error_count == 0
            else f"🔴 ACTION REQUIRED ({self.score_percentage}% Compliant)"
        )
        md.append(f"**Status**: {status}")
        md.append(
            f"- Passed: `{self.passed_count}` | Errors: `{self.error_count}` | Warnings: `{self.warning_count}`\n"
        )

        categories: dict[str, list[CheckItem]] = {}
        for c in self.checks:
            categories.setdefault(c.category, []).append(c)

        for cat, items in categories.items():
            md.append(f"### {cat}")
            md.append("| Status | Check | Details |")
            md.append("| :--- | :--- | :--- |")
            for item in items:
                badge = "✅ Pass" if item.passed else ("❌ Error" if item.severity == "error" else "⚠️ Warn")
                desc = item.details
                if not item.passed and item.remediation:
                    fix_note = " [Autofixable with `--fix`]" if item.can_autofix else ""
                    desc += f"<br>_Remediation{fix_note}_: {item.remediation}"
                md.append(f"| {badge} | **{item.name}** | {desc} |")
            md.append("")

        return "\n".join(md)


# =============================================================================
# Audit Engine
# =============================================================================


def detect_project_name(repo_path: Path) -> str:
    pyproject = repo_path / "pyproject.toml"
    if pyproject.exists():
        content = pyproject.read_text(encoding="utf-8")
        m = re.search(r'name\s*=\s*["\']([^"\']+)["\']', content)
        if m:
            return m.group(1)
    return repo_path.resolve().name


def run_audit(repo_path: Path) -> AuditReport:
    name = detect_project_name(repo_path)
    report = AuditReport(project_path=repo_path, project_name=name)

    # 1. File Manifest
    manifest_files = [
        (".dockerignore", "error", "Prevents copying host caches/credentials into image", True),
        (".env.example", "error", "Template for staging/prod environment variables", True),
        ("docker-compose.override.example.yaml", "error", "Template for server-specific compose overrides", True),
        ("docker-compose.yaml", "error", "Production-safe Docker Compose definition", False),
        ("Dockerfile", "error", "Multi-stage Dockerfile", False),
        (".gitignore", "error", "Git ignore configuration", False),
        ("AGENTS.md", "warning", "AI agent context instructions", False),
        ("README.md", "error", "Project README with deployment instructions", False),
        ("DOCKERHUB.md", "warning", "Docker Hub description and quickstart", False),
        ("publish_image.sh", "error", "Multi-arch container publishing script", False),
        ("verify_images.sh", "error", "Container verification script", False),
        ("rename.sh", "warning", "Bootstrap rename script", False),
        ("pyproject.toml", "error", "Python project definition and tool configs", False),
        (".github/workflows/test.yml", "error", "GitHub Actions test workflow", False),
    ]

    for fname, sev, desc, can_fix in manifest_files:
        p = repo_path / fname
        exists = p.exists()
        msg = f"`{fname}` is present" if exists else f"Missing `{fname}` ({desc})"
        fix = f"Create `{fname}`" if not exists else ""
        report.add("File Manifest", fname, exists, sev, msg, fix, can_fix)

    # 2. Python Specs in pyproject.toml
    pyproject_p = repo_path / "pyproject.toml"
    if pyproject_p.exists():
        py_txt = pyproject_p.read_text(encoding="utf-8")

        py_ver = re.search(r'requires-python\s*=\s*["\']>=3\.12["\']', py_txt)
        report.add(
            "Python Specifications",
            "Python >= 3.12",
            bool(py_ver),
            "error",
            "requires-python is >=3.12" if py_ver else "requires-python missing or below 3.12",
            "Set requires-python = '>=3.12' in pyproject.toml",
        )

        for pkg, pat in REQUIRED_CORE_PACKAGES.items():
            found = bool(re.search(pat, py_txt))
            report.add(
                "Core Dependencies",
                f"pkg: {pkg}",
                found,
                "error",
                f"`{pkg}` satisfies template minimum spec"
                if found
                else f"`{pkg}` missing or outdated (expected `{pat}`)",
                f"Update `{pkg}` in pyproject.toml dependencies to `{pat}`",
            )

        for pkg, pat in REQUIRED_DEV_PACKAGES.items():
            found = bool(re.search(pat, py_txt))
            report.add(
                "Dev Dependencies",
                f"dev: {pkg}",
                found,
                "error",
                f"`{pkg}` satisfies template minimum spec" if found else f"`{pkg}` missing or outdated in dev group",
                f"Update `{pkg}` in [dependency-groups].dev to `{pat}`",
            )

        has_ruff = "[tool.ruff]" in py_txt and "line-length = 120" in py_txt and "target-version" in py_txt
        report.add(
            "Tool Configuration",
            "Ruff Config",
            has_ruff,
            "error",
            "Ruff configured with line-length 120 and py312 target"
            if has_ruff
            else "Incomplete or missing [tool.ruff] config",
            "Ensure [tool.ruff] has line-length = 120 and target-version = 'py312'",
        )

        has_pyrefly = "[tool.pyrefly]" in py_txt and 'python-version = "3.12.0"' in py_txt
        report.add(
            "Tool Configuration",
            "Pyrefly Config",
            has_pyrefly,
            "error",
            "Pyrefly configured for python 3.12" if has_pyrefly else "Missing or incomplete [tool.pyrefly] config",
            "Ensure [tool.pyrefly] is defined with python-version = '3.12.0'",
        )
    else:
        report.add("Python Specifications", "pyproject.toml", False, "error", "pyproject.toml does not exist")

    # 3. .gitignore Rules
    gi_p = repo_path / ".gitignore"
    if gi_p.exists():
        gi_txt = gi_p.read_text(encoding="utf-8")
        has_override = "docker-compose.override.yaml" in gi_txt or "docker-compose.override" in gi_txt
        report.add(
            "Git & Overrides",
            "docker-compose.override in .gitignore",
            has_override,
            "error",
            "docker-compose.override is ignored"
            if has_override
            else "docker-compose.override.yaml is NOT in .gitignore",
            "Add docker-compose.override.yaml to .gitignore",
            can_autofix=True,
        )

        has_env_rules = ".env.*" in gi_txt and "!.env.example" in gi_txt
        report.add(
            "Git & Overrides",
            ".env.* ignored & !.env.example tracked",
            has_env_rules,
            "error",
            ".env.* ignored and !.env.example tracked"
            if has_env_rules
            else "Missing .env.* or !.env.example rule in .gitignore",
            "Ensure .gitignore ignores .env, .env.* and tracks !.env.example",
            can_autofix=True,
        )

    # 4. .dockerignore Isolation
    di_p = repo_path / ".dockerignore"
    if di_p.exists():
        di_txt = di_p.read_text(encoding="utf-8")
        has_essential = all(k in di_txt for k in [".venv", ".git", ".env", "docker-compose"])
        report.add(
            "Docker Isolation",
            ".dockerignore rules",
            has_essential,
            "error",
            ".dockerignore excludes .venv, .git, .env, and docker-compose"
            if has_essential
            else "Incomplete .dockerignore exclusions",
            "Update .dockerignore to exclude .venv, .git, .env*, and docker-compose*",
            can_autofix=True,
        )

    # 5. Dockerfile
    df_p = repo_path / "Dockerfile"
    if df_p.exists():
        df_txt = df_p.read_text(encoding="utf-8")
        report.add(
            "Dockerfile",
            "Base Image",
            "dartfx/docker-api-base:latest" in df_txt,
            "error",
            "Uses dartfx/docker-api-base:latest"
            if "dartfx/docker-api-base:latest" in df_txt
            else "Base image is not dartfx/docker-api-base:latest",
            "Set FROM dartfx/docker-api-base:latest",
        )
        report.add(
            "Dockerfile",
            "Non-root appuser",
            "USER appuser" in df_txt,
            "error",
            "Runs as non-root USER appuser" if "USER appuser" in df_txt else "Missing USER appuser",
            "Add USER appuser before CMD",
        )
        report.add(
            "Dockerfile",
            "Native HEALTHCHECK",
            "HEALTHCHECK" in df_txt and "/healthz" in df_txt,
            "error",
            "Native HEALTHCHECK defined against /healthz"
            if ("HEALTHCHECK" in df_txt and "/healthz" in df_txt)
            else "Missing HEALTHCHECK instruction",
            "Add HEALTHCHECK instruction probing http://localhost:8000/healthz",
        )
        report.add(
            "Dockerfile",
            "Proxy Headers",
            "--proxy-headers" in df_txt,
            "error",
            "CMD passes --proxy-headers" if "--proxy-headers" in df_txt else "CMD does not pass --proxy-headers",
            "Add '--proxy-headers' to the Uvicorn CMD arguments",
        )
        report.add(
            "Dockerfile",
            "FORWARDED_ALLOW_IPS",
            "FORWARDED_ALLOW_IPS" in df_txt,
            "warning",
            "FORWARDED_ALLOW_IPS env var configured"
            if "FORWARDED_ALLOW_IPS" in df_txt
            else "FORWARDED_ALLOW_IPS is not set",
            "Set ENV FORWARDED_ALLOW_IPS='*' in Dockerfile",
        )

    # 6. Docker Compose
    dc_p = repo_path / "docker-compose.yaml"
    if dc_p.exists():
        dc_txt = dc_p.read_text(encoding="utf-8")
        report.add(
            "Docker Compose",
            "Restart Policy",
            "restart: unless-stopped" in dc_txt,
            "error",
            "restart: unless-stopped configured"
            if "restart: unless-stopped" in dc_txt
            else "Missing restart: unless-stopped",
            "Add 'restart: unless-stopped' to the service",
        )
        report.add(
            "Docker Compose",
            "Service Healthcheck",
            "healthcheck:" in dc_txt and "/healthz" in dc_txt,
            "error",
            "Service healthcheck configured against /healthz"
            if ("healthcheck:" in dc_txt and "/healthz" in dc_txt)
            else "Missing service healthcheck block",
            "Add healthcheck block probing http://localhost:8000/healthz",
        )
        report.add(
            "Docker Compose",
            "Host Bind Parameterization",
            "HOST_BIND_IP" in dc_txt,
            "error",
            "Port binding uses ${HOST_BIND_IP:-127.0.0.1}" if "HOST_BIND_IP" in dc_txt else "Port binding is hardcoded",
            "Set ports to '${HOST_BIND_IP:-127.0.0.1}:${HOST_PORT:-8000}:8000'",
        )
        report.add(
            "Docker Compose",
            "Optional .env Loading",
            "env_file:" in dc_txt and ".env" in dc_txt,
            "warning",
            "Loads optional .env file"
            if ("env_file:" in dc_txt and ".env" in dc_txt)
            else "Missing optional .env file loader",
            "Add env_file: [{path: .env, required: false}] to service",
        )

    # 7. Gateway & Reverse Proxy Integration in Code
    main_candidates = list(repo_path.glob("src/**/main.py"))
    if main_candidates:
        main_txt = main_candidates[0].read_text(encoding="utf-8")
        has_fwd = "ForwardedPrefixMiddleware" in main_txt or "x-forwarded-prefix" in main_txt.lower()
        report.add(
            "Reverse Proxy & Gateway",
            "X-Forwarded-Prefix Support",
            has_fwd,
            "error",
            "ForwardedPrefixMiddleware handles gateway subpaths"
            if has_fwd
            else "Missing ForwardedPrefixMiddleware for Kong/Nginx prefix handling",
            "Add ForwardedPrefixMiddleware in main.py",
        )
        report.add(
            "Reverse Proxy & Gateway",
            "Configurable CORS",
            "CORS_ORIGINS" in main_txt,
            "warning",
            "CORS_ORIGINS environment variable supported" if "CORS_ORIGINS" in main_txt else "CORS origins hardcoded",
            "Read CORS_ORIGINS from environment to support Kong CORS plugin",
        )
    else:
        report.add("Reverse Proxy & Gateway", "main.py entrypoint", False, "error", "src/**/main.py not found")

    # 8. Health Endpoints
    health_candidates = list(repo_path.glob("src/**/routers/health.py"))
    if health_candidates:
        health_txt = health_candidates[0].read_text(encoding="utf-8")
        has_endpoints = "/status" in health_txt and "/healthz" in health_txt
        report.add(
            "Health & Observability",
            "/status and /healthz endpoints",
            has_endpoints,
            "error",
            "Both /status and /healthz implemented" if has_endpoints else "Missing /status or /healthz",
            "Add /status and /healthz to routers/health.py",
        )
    else:
        report.add("Health & Observability", "routers/health.py", False, "error", "routers/health.py not found")

    # 9. Automated Testing
    test_candidates = list(repo_path.glob("tests/**/test_api.py"))
    if test_candidates:
        test_txt = test_candidates[0].read_text(encoding="utf-8")
        has_prefix_test = "test_forwarded_prefix_middleware" in test_txt or "x-forwarded-prefix" in test_txt.lower()
        report.add(
            "Automated Testing",
            "Gateway Prefix Test",
            has_prefix_test,
            "warning",
            "Test for X-Forwarded-Prefix is present"
            if has_prefix_test
            else "Missing test for ForwardedPrefixMiddleware",
            "Add test_forwarded_prefix_middleware to tests/test_api.py",
        )
    else:
        report.add("Automated Testing", "tests/test_api.py", False, "error", "tests/test_api.py not found")

    return report


# =============================================================================
# Automated Fix Engine
# =============================================================================


def apply_autofixes(repo_path: Path, report: AuditReport) -> list[str]:
    fixes_applied = []
    pname = report.project_name

    # 1. Create missing .dockerignore
    di_p = repo_path / ".dockerignore"
    if not di_p.exists():
        di_p.write_text(CANONICAL_DOCKERIGNORE, encoding="utf-8")
        fixes_applied.append("Created `.dockerignore`")

    # 2. Create missing .env.example
    env_ex = repo_path / ".env.example"
    if not env_ex.exists():
        env_ex.write_text(CANONICAL_ENV_EXAMPLE.format(project_name=pname), encoding="utf-8")
        fixes_applied.append("Created `.env.example`")

    # 3. Create missing docker-compose.override.example.yaml
    ov_ex = repo_path / "docker-compose.override.example.yaml"
    if not ov_ex.exists():
        ov_ex.write_text(CANONICAL_OVERRIDE_EXAMPLE.format(project_name=pname), encoding="utf-8")
        fixes_applied.append("Created `docker-compose.override.example.yaml`")

    # 4. Patch .gitignore if missing overrides
    gi_p = repo_path / ".gitignore"
    if gi_p.exists():
        gi_txt = gi_p.read_text(encoding="utf-8")
        patched = False
        if "docker-compose.override" not in gi_txt:
            gi_txt += (
                "\n# Docker Compose local/server overrides\n"
                "docker-compose.override.yaml\n"
                "docker-compose.override.yml\n"
                "compose.override.yaml\n"
                "compose.override.yml\n"
            )
            patched = True
        if ".env.*" not in gi_txt or "!.env.example" not in gi_txt:
            gi_txt += "\n# Environment files\n.env\n.env.*\n!.env.example\n"
            patched = True
        if patched:
            gi_p.write_text(gi_txt, encoding="utf-8")
            fixes_applied.append("Updated `.gitignore` with override and environment tracking rules")

    return fixes_applied


# =============================================================================
# CLI Main
# =============================================================================


def main():
    parser = argparse.ArgumentParser(description="Programmatic audit and alignment tool for Data Artifex API services.")
    parser.add_argument(
        "repo_path", nargs="?", default=".", help="Path to API repository root (default: current directory)"
    )
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    parser.add_argument(
        "--fix", action="store_true", help="Automatically create missing config templates and patch .gitignore"
    )
    args = parser.parse_args()

    target = Path(args.repo_path).resolve()
    if not target.is_dir():
        print(f"Error: Directory not found: {target}", file=sys.stderr)
        sys.exit(1)

    initial_report = run_audit(target)

    if args.fix:
        fixes = apply_autofixes(target, initial_report)
        if fixes:
            print("=== Autofixes Applied ===")
            for f in fixes:
                print(f"  ✓ {f}")
            print("")
            # Re-run audit after fixes
            initial_report = run_audit(target)
        else:
            print("No automatic file repairs needed.\n")

    if args.json:
        data = {
            "project_path": str(initial_report.project_path),
            "project_name": initial_report.project_name,
            "score_percentage": initial_report.score_percentage,
            "passed_count": initial_report.passed_count,
            "error_count": initial_report.error_count,
            "warning_count": initial_report.warning_count,
            "checks": [
                {
                    "category": c.category,
                    "name": c.name,
                    "passed": c.passed,
                    "severity": c.severity,
                    "details": c.details,
                    "remediation": c.remediation,
                    "can_autofix": c.can_autofix,
                }
                for c in initial_report.checks
            ],
        }
        print(json.dumps(data, indent=2))
    else:
        print(initial_report.to_markdown())

    if initial_report.error_count > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
