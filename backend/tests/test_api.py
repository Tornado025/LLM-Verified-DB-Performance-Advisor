"""HTTP validation, resumable events and real mock-job acceptance."""

import threading
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import JobRunner, create_app
from app.models import Candidate
from app.store import Store


@pytest.fixture
def client(tmp_path):
    store = Store(tmp_path / "api.sqlite")
    settings = replace(get_settings(), llm_provider="mock")
    with TestClient(create_app(settings=settings, store=store, manage_database=False)) as c:
        yield c
    store.close()


def test_settings_masks_key(client):
    data = client.get("/api/settings").json()
    assert data["openrouter_api_key"] in {"", "***"}
    assert data["pg_password"] in {"", "***"}


def test_shapes_list(client):
    data = client.get("/api/shapes").json()
    assert len(data["reports"]) == 8 and len(data["txns"]) == 5


def test_profiles_list(client):
    assert len(client.get("/api/profiles").json()) == 4


@pytest.mark.parametrize(
    "body",
    [
        {"shape_ids": ["unknown"]},
        {"shape_ids": ["recent_orders"], "k_max": 0},
        {"shape_ids": ["recent_orders"], "extra": True},
        {"shape_ids": ["recent_orders"], "profiles": []},
    ],
)
def test_job_validation_errors(client, body):
    response = client.post("/api/jobs", json=body)
    assert response.status_code == 422
    assert set(response.json()["error"]) == {"code", "message", "detail"}


def test_sse_stream(client):
    store = client.app.state.store
    job = store.create_job({})
    ids = [store.emit(job, "system", "info", "test"), store.emit(job, "decide", "result", "done")]
    store.set_job_status(job, "done")
    response = client.get(f"/api/jobs/{job}/events")
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: end" in response.text
    assert [int(line[4:]) for line in response.text.splitlines() if line.startswith("id:")] == ids
    resumed = client.get(f"/api/jobs/{job}/events", headers={"Last-Event-ID": str(ids[0])})
    assert f"id: {ids[0]}\n" not in resumed.text
    assert f"id: {ids[1]}\n" in resumed.text
    assert (
        client.get(f"/api/jobs/{job}/events", headers={"Last-Event-ID": "bad"}).status_code == 400
    )


def test_cancel(client):
    store = client.app.state.store
    job = store.create_job({})
    assert client.post(f"/api/jobs/{job}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/jobs/{job}/cancel").json()["status"] == "cancelled"


def test_approve_conflict(client):
    store = client.app.state.store
    job = store.create_job({})
    c = Candidate(
        job + ":recent_orders:01",
        job,
        "recent_orders",
        "index",
        "CREATE INDEX ON customers(city)",
        "fixture",
        0,
    )
    store.save_candidate(c)
    assert client.post(f"/api/candidates/{c.id}/approve", json={}).status_code == 409
    assert client.get(f"/api/candidates/{c.id}").json()["apply_preview"] is None
    c.status = "selected"
    store.save_candidate(c)
    preview = client.get(f"/api/candidates/{c.id}").json()
    assert "CONCURRENTLY" in preview["apply_preview"]
    assert preview["target_database"] == "qc_live"


def test_error_envelope_shape(client):
    response = client.get("/api/jobs/missing")
    assert response.status_code == 404
    assert set(response.json()["error"]) == {"code", "message", "detail"}
    assert client.get("/api/experiments/not_whitelisted").status_code == 404
    assert client.get("/api/experiments/rq4").status_code == 200
    assert "error" in client.get("/api/missing-route").json()


def test_openapi(client):
    schema = client.get("/openapi.json").json()
    assert schema["openapi"].startswith("3.")
    assert "/api/jobs/{job_id}/events" in schema["paths"]
    assert schema["components"]["schemas"]["JobRequest"]["additionalProperties"] is False


def test_job_runner_serial_queue_and_cancel(tmp_path, monkeypatch):
    import app.main as main

    store = Store(tmp_path / "queue.sqlite")
    entered, release = threading.Event(), threading.Event()

    def run(job_id, params, metadata):
        metadata.set_job_status(job_id, "running")
        entered.set()
        release.wait(5)
        metadata.set_job_status(job_id, "done")

    monkeypatch.setattr(main, "run_job", run)
    runner = JobRunner(store)
    try:
        first = runner.submit({})
        assert entered.wait(2)
        second = runner.submit({})
        assert store.get_job(first)["status"] == "running"
        assert store.get_job(second)["status"] == "queued"
        runner.cancel(second)
    finally:
        release.set()
        runner.close()
    assert store.get_job(second)["status"] == "cancelled"
    store.close()


@pytest.mark.db
def test_health_ok(client):
    data = client.get("/api/health").json()
    assert data["status"] == "ok"
    assert data["postgres"] and data["hypopg"] and data["golden_idle"]


@pytest.mark.db
@pytest.mark.slow
@pytest.mark.timeout(180)
def test_job_lifecycle_mock(client):
    response = client.post(
        "/api/jobs",
        json={
            "shape_ids": ["recent_orders"],
            "llm": "mock",
            "dry_run": True,
            "k_max": 2,
            "patience": 1,
            "budget_s": 120,
        },
    )
    assert response.status_code == 202
    job = response.json()["job_id"]
    for _ in range(300):
        status = client.get(f"/api/jobs/{job}").json()["status"]
        if status in {"done", "failed", "cancelled"}:
            break
        time.sleep(0.1)
    assert status == "done"
    assert len(client.get(f"/api/jobs/{job}/evidence").json()["cards"]) == 1
    events = client.get(f"/api/jobs/{job}/events.json").json()["events"]
    assert all(a["id"] < b["id"] for a, b in zip(events, events[1:], strict=False))
