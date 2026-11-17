"""Tests for root landing page, ForwardedPrefixMiddleware, health probes, AsyncAPI, and /ui."""

from fastapi.testclient import TestClient

from workspace_api.__about__ import __version__


def test_health_and_status(client: TestClient) -> None:
    for endpoint in ("/status", "/healthz"):
        res = client.get(endpoint)
        assert res.status_code == 200
        payload = res.json()
        assert payload["status"] == "pass"
        assert payload["version"] == __version__
        assert "releaseId" in payload


def test_forwarded_prefix_middleware(client: TestClient) -> None:
    headers = {"X-Forwarded-Prefix": "/api/workspace"}

    root_res = client.get("/", headers=headers)
    assert root_res.status_code == 200
    assert "/api/workspace/ui" in root_res.text
    assert "/api/workspace/docs" in root_res.text
    assert "/api/workspace/redoc" in root_res.text
    assert "/api/workspace/openapi.json" in root_res.text
    assert "/api/workspace/asyncapi.json" in root_res.text

    docs_res = client.get("/docs", headers=headers)
    assert docs_res.status_code == 200
    assert "/api/workspace/openapi.json" in docs_res.text

    ui_res = client.get("/ui", headers=headers)
    assert ui_res.status_code == 200
    assert 'content="/api/workspace"' in ui_res.text
    assert "/api/workspace/ui/static/app.js" in ui_res.text


def test_asyncapi_schema_and_web_client_assets(client: TestClient) -> None:
    res = client.get("/asyncapi.json")
    assert res.status_code == 200
    spec = res.json()
    assert spec["asyncapi"] == "3.0.0"
    assert "jobsWebSocket" in spec["channels"]
    assert "TaskEvent" in spec["components"]["schemas"]

    ui_html = client.get("/ui")
    assert ui_html.status_code == 200
    assert "Data Artifex Workspace" in ui_html.text

    js_res = client.get("/ui/static/app.js")
    assert js_res.status_code == 200
    assert "files/upload" in js_res.text

    css_res = client.get("/ui/static/styles.css")
    assert css_res.status_code == 200
