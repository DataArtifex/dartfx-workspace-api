"""Tests for multi-protocol async jobs (REST, SSE, WebSocket, Webhook) and SPARQL/SQL queries."""

import json
import time
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.conftest import ADMIN_KEY


def test_sparql_and_tabular_queries(client: TestClient) -> None:
    sparql_res = client.post(
        "/workspaces/sandbox/sparql",
        json={
            "query": (
                "PREFIX dartfx: <https://dataartifex.org/workspace/> "
                "SELECT ?path ?type WHERE { ?s a dartfx:FileResource ; dartfx:path ?path ; dartfx:fileType ?type . }"
            )
        },
    )
    assert sparql_res.status_code == 200
    assert sparql_res.json()["row_count"] == 3

    sql_res = client.post(
        "/workspaces/sandbox/query",
        json={"sql": "SELECT region, COUNT(*) AS n FROM sample_survey GROUP BY region ORDER BY region"},
    )
    assert sql_res.status_code == 200
    assert sql_res.json()["row_count"] == 4

    bad_sql = client.post("/workspaces/sandbox/query", json={"sql": "DROP TABLE sample_survey"})
    assert bad_sql.status_code == 400


def test_async_scan_rest_polling_sse_and_webhook(client: TestClient) -> None:
    webhook_payloads: list[dict] = []

    class _MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json=None):
            webhook_payloads.append({"url": url, "json": json})

    with patch("workspace_api.jobs.httpx.AsyncClient", _MockAsyncClient):
        res = client.post(
            "/workspaces/sandbox/scan",
            json={"target_path": ".", "clean": False, "webhook_url": "https://example.org/hook"},
        )
        assert res.status_code == 202
        job = res.json()
        job_id = job["id"]
        assert res.headers["location"].endswith(f"/jobs/{job_id}")

        # Poll until completed
        for _ in range(40):
            polled = client.get(f"/jobs/{job_id}").json()
            if polled["status"] in ("completed", "failed"):
                break
            time.sleep(0.05)

        assert polled["status"] == "completed"
        assert polled["progress_pct"] == 100.0
        assert len(webhook_payloads) == 1
        assert webhook_payloads[0]["url"] == "https://example.org/hook"
        assert webhook_payloads[0]["json"]["status"] == "completed"

    # Stream SSE events (returns terminal snapshot cleanly)
    with client.stream("GET", f"/jobs/{job_id}/events") as sse:
        assert sse.status_code == 200
        body = "".join(sse.iter_text())
        assert "event: task_event" in body
        assert '"status":"completed"' in body


def test_websocket_jobs_and_mocked_fairify(client: TestClient) -> None:
    with client.websocket_connect("/ws/jobs") as ws:
        ws.send_text(json.dumps({"action": "ping"}))
        pong = ws.receive_json()
        assert pong == {"action": "pong"}

        class _Proc:
            returncode = 0
            stdout = "ok"
            stderr = ""

        with (
            patch("dartfx.workspace.tasks.shutil.which", return_value="/usr/bin/qsv"),
            patch("dartfx.workspace.tasks.subprocess.run", return_value=_Proc()),
        ):
            res = client.post(
                "/workspaces/sandbox/fairify",
                json={"source_path": "data/sample_survey.csv", "describe": False},
            )
            assert res.status_code == 202
            job_id = res.json()["id"]

            seen_terminal = False
            for _ in range(25):
                ev = ws.receive_json()
                if ev.get("task_id") == job_id and ev.get("status") in ("completed", "failed"):
                    seen_terminal = True
                    assert ev["status"] == "completed"
                    break
            assert seen_terminal

    # Verify Admin system tools endpoint
    tools_res = client.get("/system/tools", headers={"X-API-Key": ADMIN_KEY})
    assert tools_res.status_code == 200
    assert "qsv" in tools_res.json()


def test_fairify_up_to_date_blocking_and_force(client: TestClient, test_settings) -> None:
    # 1. Upload a fresh CSV file
    csv_content = b"col1,col2\nfoo,bar\n"
    up_res = client.post(
        "/workspaces/sandbox/files/upload",
        files={"files": ("experiment.csv", csv_content, "text/csv")},
        data={"target_dir": "data", "overwrite": "true", "auto_scan": "true"},
    )
    assert up_res.status_code == 201

    # Check files listing: fairify is applicable, status is available
    files_res = client.get("/workspaces/sandbox/files", params={"path": "data"})
    assert files_res.status_code == 200
    exp_entry = next(f for f in files_res.json() if f["name"] == "experiment.csv")
    assert exp_entry["fairify_status"] == "available"
    assert "fairify" in exp_entry["applicable_actions"]

    # 2. Write mock fairify artifacts
    ws_info = client.get("/workspaces/sandbox").json()
    data_dir = Path(ws_info["path"]) / "data"
    time.sleep(0.05)
    (data_dir / "experiment.csv.stats.csv").write_text("stats", encoding="utf-8")
    (data_dir / "experiment.freqs.csv").write_text("freqs", encoding="utf-8")
    (data_dir / "experiment.csv.schema.json").write_text("schema", encoding="utf-8")

    # Re-fetch files: fairify_status is now up_to_date, fairify omitted from applicable_actions
    files_res2 = client.get("/workspaces/sandbox/files", params={"path": "data"})
    exp_entry2 = next(f for f in files_res2.json() if f["name"] == "experiment.csv")
    assert exp_entry2["fairify_status"] == "up_to_date"
    assert "fairify" not in exp_entry2["applicable_actions"]

    # 3. POST /fairify without force should be rejected with 400
    fail_res = client.post(
        "/workspaces/sandbox/fairify",
        json={"source_path": "data/experiment.csv", "force": False},
    )
    assert fail_res.status_code == 400
    assert "already been FAIRified and has not changed" in fail_res.json()["detail"]

    # 4. POST /fairify with force=True should be accepted (202)
    with (
        patch("dartfx.workspace.tasks.shutil.which", return_value="/usr/bin/qsv"),
        patch("dartfx.workspace.tasks.subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "ok"
        mock_run.return_value.stderr = ""
        force_res = client.post(
            "/workspaces/sandbox/fairify",
            json={"source_path": "data/experiment.csv", "force": True},
        )
        assert force_res.status_code == 202
        assert force_res.json()["task_type"] == "fairify"
