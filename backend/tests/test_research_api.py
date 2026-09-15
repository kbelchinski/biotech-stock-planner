"""End-to-end research flows through the HTTP API in demo mode (same pipeline as live)."""

import time
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.domain.models import DataMode
from app.main import create_app
from app.screening.market_calendar import MarketCalendar
from tests.helpers import demo_settings


def wait(client, url):
    for _ in range(400):
        job = client.get(url).json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def run_scan(client, scenario="normal"):
    job = client.post("/api/scans", json={"criteria": {}, "demo_scenario": scenario}).json()
    return wait(client, f"/api/scans/jobs/{job['id']}")


def refresh(client, scenario=None, tickers=None):
    job = client.post("/api/watchlist/refresh", json={"demo_scenario": scenario, "tickers": tickers}).json()
    return wait(client, f"/api/watchlist/refresh/{job['id']}")


def test_watchlist_tracks_outside_window_and_records_revisions(tmp_path):
    with TestClient(create_app(demo_settings(tmp_path, http_max_retries=0))) as client:
        for ticker in ("AURX", "BRVN", "KSTL"):
            assert client.post("/api/watchlist", json={"ticker": ticker}).status_code == 201
        report = refresh(client)["report"]
        assert all(r["ok"] for r in report["tickers"].values())
        items = {i["ticker"]: i for i in client.get("/api/watchlist").json()["items"]}
        # KSTL's only catalyst (+45 days) is outside the 60–90 day discovery window but stays tracked.
        assert items["KSTL"]["catalyst_count"] == 1
        # All BRVN catalysts are kept, not only the qualifying one, including the earlier +30 event.
        assert items["BRVN"]["catalyst_count"] == 3

        report = refresh(client, scenario="bpiq_date_revised")["report"]
        items = {i["ticker"]: i for i in client.get("/api/watchlist").json()["items"]}
        assert items["AURX"]["date_revision_count"] == 1
        assert items["BRVN"]["not_returned_count"] == 1
        kinds = {n["kind"] for n in client.get("/api/notifications").json()["items"]}
        assert {"date_changed", "not_returned"} <= kinds

        # Provider failure keeps stored data and is reported, never turned into "no catalysts".
        report = refresh(client, scenario="bpiq_unavailable")["report"]
        assert report["tickers"]["AURX"]["ok"] is False
        items = {i["ticker"]: i for i in client.get("/api/watchlist").json()["items"]}
        assert items["AURX"]["catalyst_count"] == 1 and items["AURX"]["last_refresh_error"]
        assert any(n["kind"] == "refresh_failed" for n in client.get("/api/notifications").json()["items"])

        research = client.get("/api/companies/AURX/research").json()
        assert research["catalysts"]["origin"] == "tracked"
        revision = next(r for r in research["catalysts"]["revisions"] if r["kind"] == "date_changed")
        assert revision["previous"] != revision["current"]

        assert client.delete("/api/watchlist/KSTL").status_code == 200
        assert client.post("/api/notifications/read", json={"ids": None}).json()["unread"] == 0


def test_scan_creates_forward_paper_trades_and_research_page_sections(tmp_path):
    with TestClient(create_app(demo_settings(tmp_path))) as client:
        job = run_scan(client)
        scan = client.get(f"/api/scans/{job['scan_id']}").json()
        assert scan["rule_version"] != "" and not scan["rule_version"].startswith("unversioned")
        paper = client.get("/api/trades?kind=paper").json()
        qualifying = {r["ticker"] for r in scan["results"] if r["eligibility"] == "qualifies"}
        assert {t["ticker"] for t in paper} == qualifying
        cal = MarketCalendar()
        finished = datetime.fromisoformat(scan["finished_at"])
        for t in paper:
            assert t["status"] == "pending_entry" and t["entry_price"] is None
            entry = datetime.fromisoformat(t["entry_date"]).date()
            assert entry > datetime.fromisoformat(scan["latest_completed_session"]).date()
            assert cal.session_open(entry) > finished  # no entry before the decision time
        # A second scan does not duplicate open paper trades.
        run_scan(client)
        assert len(client.get("/api/trades?kind=paper").json()) == len(paper)

        research = client.get("/api/companies/AURX/research").json()
        assert research["screening"]["result"]["eligibility"] == "qualifies"
        assert research["catalysts"]["origin"] == "live_query"
        assert len(research["catalysts"]["historical"]) == 1
        metrics = {m["key"]: m for m in research["price_context"]["metrics"]}
        assert metrics["return_60"]["status"] == "ok" and metrics["relative_60"]["status"] == "ok"
        assert metrics["return_since_first_seen"]["status"] in ("ok", "missing_bars")
        assert research["financials"]["mcp"]["state"] == "unavailable"
        assert len(research["questions"]) == 8
        assert {e["category"] for e in research["evidence"]} >= {"supporting", "missing_or_stale", "invalidation_conditions"}

        saved = client.post("/api/companies/AURX/analyses", json={"note": "first look"})
        assert saved.status_code == 201
        again = client.get("/api/companies/AURX/research").json()
        assert again["changes"]["previous_saved_at"] and isinstance(again["changes"]["items"], list)

        critique = client.post("/api/companies/AURX/critique", json={}).json()
        assert critique["available"] is False and critique["evidence"]


def test_trades_are_separated_and_validated(tmp_path):
    with TestClient(create_app(demo_settings(tmp_path))) as client:
        bad = client.post("/api/trades", json={"kind": "actual", "ticker": "AURX", "status": "open"})
        assert bad.status_code == 422
        actual = client.post(
            "/api/trades",
            json={"kind": "actual", "ticker": "aurx", "status": "closed", "entry_date": "2026-08-03", "entry_price": 10, "exit_date": "2026-08-20", "exit_price": 11},
        )
        assert actual.status_code == 201 and actual.json()["ticker"] == "AURX"
        client.post(
            "/api/trades",
            json={"kind": "hypothetical", "ticker": "BRVN", "status": "closed", "entry_date": "2026-08-03", "entry_price": 10, "exit_date": "2026-08-20", "exit_price": 5},
        )
        planned = client.post("/api/trades", json={"kind": "planned", "ticker": "KSTL", "plan": {"ticker": "KSTL", "entry_price": 9}})
        assert planned.status_code == 201
        # Watching never implies ownership.
        client.post("/api/watchlist", json={"ticker": "VNTA"})
        assert all(t["ticker"] != "VNTA" for t in client.get("/api/trades").json())
        perf = client.get("/api/performance").json()["summaries"]
        assert perf["actual"]["closed_count"] == 1 and perf["actual"]["win_rate"] == 1.0
        assert perf["hypothetical"]["closed_count"] == 1 and perf["hypothetical"]["win_rate"] == 0.0
        assert perf["paper"]["closed_count"] == 0

        plan = client.post(
            "/api/trade-plans/calculate",
            json={"plan": {"ticker": "BRVN", "entry_price": 20, "stop_price": 16, "loss_budget_usd": 400, "planned_exit_date": (datetime.now().date() + timedelta(days=70)).isoformat()}},
        ).json()
        assert plan["output"]["stop_based_shares"] == 100
        assert len(plan["catalysts_before_exit"]) >= 2  # BRV-220 (+30) and Velostrin PDUFA (+62)

        entry = client.post("/api/journal", json={"ticker": "AURX", "decision": "skip", "reasons": "Price already moved"}).json()
        assert client.get("/api/journal").json()[0]["id"] == entry["id"]


def test_no_order_routes_and_live_mode_reports_unconfigured(tmp_path):
    app = create_app(demo_settings(tmp_path, app_mode="live"))
    paths = [getattr(r, "path", "") for r in app.routes]
    assert not any("order" in p.lower() for p in paths)
    with TestClient(app) as client:
        client.post("/api/watchlist", json={"ticker": "AURX"})
        report = refresh(client)["report"]
        assert any("not configured" in i for i in report["issues"])
        status = client.get("/api/integrations/status").json()
        assert status["bpiq_mcp"]["configured"] is False and status["ai"]["enabled"] is False
        assert "only while this backend process is running" in status["monitoring"]["behaviour"]
        assert client.post("/api/watchlist/refresh", json={"demo_scenario": "normal"}).status_code == 400
        # Demo watchlist is separate from live.
        assert [i["ticker"] for i in client.get("/api/watchlist").json()["items"]] == ["AURX"]
    with TestClient(create_app(demo_settings(tmp_path))) as client:
        assert client.get("/api/watchlist").json()["items"] == []


def test_scan_snapshots_are_immutable(tmp_path):
    import pytest

    from app.storage.repository import ImmutableScanError, ScanRepository

    with TestClient(create_app(demo_settings(tmp_path))) as client:
        job = run_scan(client)
    repo = ScanRepository(tmp_path / "scans.sqlite3")
    run = repo.get(job["scan_id"])
    with pytest.raises(ImmutableScanError):
        repo.save(run.model_copy(update={"notices": ["tampered"]}))
    assert repo.first_seen(DataMode.DEMO, "AURX").basis == "first_qualified"
