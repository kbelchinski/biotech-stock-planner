import time

from fastapi.testclient import TestClient

from app.main import create_app
from app.scan.export import _cell
from tests.helpers import demo_settings

SECRETS = ("bpiq-SECRET-111", "AKSECRET222", "alpaca-SECRET-333")


def secret_settings(tmp_path, **overrides):
    return demo_settings(
        tmp_path, bpiq_api_key=SECRETS[0], alpaca_api_key_id=SECRETS[1], alpaca_api_secret_key=SECRETS[2], **overrides
    )


def wait_for_job(client, job_id):
    for _ in range(200):
        job = client.get(f"/api/scans/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("scan job did not finish")


def test_demo_scan_flow_and_no_credentials_in_responses(tmp_path):
    with TestClient(create_app(secret_settings(tmp_path))) as client:
        status = client.get("/api/status")
        assert status.status_code == 200
        assert status.json()["mode"] == "demo"
        assert status.json()["runway"]["available"] is True

        started = client.post("/api/scans", json={"criteria": {}, "demo_scenario": "normal"})
        assert started.status_code == 202
        job = wait_for_job(client, started.json()["id"])
        assert job["status"] == "completed"

        scan = client.get(f"/api/scans/{job['scan_id']}")
        assert scan.json()["outcome"] == "success"
        csv_response = client.get(f"/api/scans/{job['scan_id']}/export.csv")
        assert csv_response.headers["content-type"].startswith("text/csv")
        assert "DEMO MOCK" in csv_response.text
        assert csv_response.text.startswith("data_mode,")

        latest = client.get("/api/scans/latest").json()
        assert latest["latest_successful"]["id"] == job["scan_id"]

        for response in (status, scan, csv_response, client.get("/api/scans")):
            for secret in SECRETS:
                assert secret not in response.text


def test_invalid_criteria_rejected(tmp_path):
    with TestClient(create_app(demo_settings(tmp_path))) as client:
        response = client.post("/api/scans", json={"criteria": {"catalyst_min_days": 95, "catalyst_max_days": 60}})
        assert response.status_code == 422


def test_live_mode_status_reports_missing_configuration(tmp_path):
    with TestClient(create_app(demo_settings(tmp_path, app_mode="live"))) as client:
        status = client.get("/api/status").json()
        assert status["mode"] == "live"
        assert status["runway"]["available"] is False
        assert status["demo_scenarios"] == []
        assert any("BPIQ_API_KEY" in p for p in status["live_configuration_problems"])
        assert client.post("/api/scans", json={"demo_scenario": "normal"}).status_code == 400


def test_csv_formula_injection_is_neutralised():
    assert _cell("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert _cell("AURX") == "AURX"
    assert _cell(5) == 5
