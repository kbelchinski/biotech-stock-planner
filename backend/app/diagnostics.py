"""Connection check for live providers.

    python -m app.diagnostics            # check credentials, permissions, and schemas
    python -m app.diagnostics --labels   # also list BPIQ stage/event labels for classification review

Makes a few small read-only requests. Never prints credentials. Writes a summary (no secrets)
to backend/data/diagnostics.json so the UI can show when live access was last verified.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import BPIQ_TRIAL_HORIZON_DAYS, BpiqAccessTier, get_settings
from app.providers.bpiq.normalize import RejectedRecord, normalize_catalyst, parse_catalyst_envelope
from app.providers.errors import ProviderError
from app.providers.factory import ConfigurationError, build_live_providers
from app.screening.market_calendar import MarketCalendar, new_york_today

CHECK_SYMBOL = "SPY"


def _line(ok: bool | None, label: str, detail: str) -> None:
    mark = {True: "PASS", False: "FAIL", None: "INFO"}[ok]
    print(f"[{mark}] {label}: {detail}")


async def run(show_labels: bool) -> int:
    settings = get_settings()
    calendar = MarketCalendar()
    now = datetime.now(UTC)
    today = new_york_today(now)
    latest = calendar.latest_completed_session(now)
    report: dict[str, Any] = {"ran_at": now.isoformat(), "checks": {}}

    _line(None, "APP_MODE", settings.app_mode.value)
    _line(None, "BPIQ_API_KEY", "set" if settings.bpiq_configured else "missing")
    _line(None, "BPIQ_ACCESS_TIER", settings.bpiq_access_tier.value)
    _line(None, "ALPACA keys", "set" if settings.alpaca_configured else "missing")
    _line(None, "ALPACA_ACCOUNT_TYPE", f"{settings.alpaca_account_type.value} ({settings.alpaca_trading_base_url})")

    try:
        bundle = build_live_providers(settings)
    except ConfigurationError as exc:
        for problem in exc.problems:
            _line(False, "Configuration", problem)
        print("\nAdd credentials to .env (copy .env.example) and rerun.")
        return 1

    all_ok = True
    try:
        # BPIQ: one small catalyst page within the trial-safe horizon.
        try:
            payload = await bundle.bpiq._http.get_json(
                f"{settings.bpiq_base_url.rstrip('/')}/catalysts/",
                {
                    "limit": "5",
                    "offset": "0",
                    "catalyst_date_min": today.isoformat(),
                    "catalyst_date_max": (today + timedelta(days=BPIQ_TRIAL_HORIZON_DAYS)).isoformat(),
                },
            )
            envelope = parse_catalyst_envelope(payload)
            normalized = [normalize_catalyst(r, now) for r in envelope.results]
            rejected = [n for n in normalized if isinstance(n, RejectedRecord)]
            _line(
                not rejected,
                "BPIQ /catalysts/",
                f"authenticated; count={envelope.count}; {len(envelope.results)} sample record(s), "
                f"{len(rejected)} failed schema validation",
            )
            for r in rejected:
                _line(False, "  rejected", f"id {r.record_id}: {r.reason}")
            report["checks"]["bpiq_catalysts"] = {"ok": not rejected, "count": envelope.count}
            all_ok &= not rejected
        except ProviderError as exc:
            _report_error("BPIQ /catalysts/", exc, report, "bpiq_catalysts")
            all_ok = False

        if show_labels and report["checks"].get("bpiq_catalysts", {}).get("ok"):
            horizon = BPIQ_TRIAL_HORIZON_DAYS if settings.bpiq_access_tier is BpiqAccessTier.APEX_TRIAL else 90
            try:
                fetched = await bundle.bpiq.fetch_upcoming_catalysts(
                    today=today, date_min=today, date_max=today + timedelta(days=horizon)
                )
                print(f"\nBPIQ stage/event labels in the next {horizon} days ({fetched.pages} pages):")
                for (stage, event), count in fetched.label_inventory.most_common():
                    print(f"  {count:4d}  stage={stage!r:30} event={event!r}")
            except ProviderError as exc:
                _report_error("BPIQ label inventory", exc, report, "bpiq_labels")

        # Alpaca market data: SIP daily bars for a liquid symbol.
        try:
            bars = await bundle.market_data.fetch_daily_bars([CHECK_SYMBOL], latest - timedelta(days=10), latest)
            history = bars.histories[CHECK_SYMBOL]
            has_latest = any(b.session_date == latest for b in history.bars)
            with_vwap = sum(b.vwap is not None for b in history.bars)
            ok = bool(history.bars) and has_latest
            _line(
                ok,
                "Alpaca /v2/stocks/bars feed=sip",
                f"{len(history.bars)} bar(s) for {CHECK_SYMBOL}; latest completed session {latest} "
                f"{'present' if has_latest else 'MISSING'}; {with_vwap} with VWAP",
            )
            report["checks"]["alpaca_bars"] = {"ok": ok, "bars": len(history.bars)}
            all_ok &= ok
        except ProviderError as exc:
            _report_error("Alpaca /v2/stocks/bars feed=sip", exc, report, "alpaca_bars")
            all_ok = False

        # Alpaca trading API: asset lookup used for listing verification.
        try:
            assets = await bundle.assets.fetch_assets([CHECK_SYMBOL])
            listing = assets.listings.get(CHECK_SYMBOL)
            if CHECK_SYMBOL in assets.errors:
                raise assets.errors[CHECK_SYMBOL]
            ok = listing is not None
            _line(
                ok,
                "Alpaca /v2/assets/{symbol}",
                f"{CHECK_SYMBOL} → {listing.exchange}, {listing.status}" if listing else "symbol not found",
            )
            report["checks"]["alpaca_assets"] = {"ok": ok}
            all_ok &= ok
        except ProviderError as exc:
            _report_error("Alpaca /v2/assets/{symbol}", exc, report, "alpaca_assets")
            all_ok = False
    finally:
        await bundle.aclose()

    _line(
        None,
        "Cash runway",
        "not available from BPIQ Apex or Alpaca; live runway is reported as Unknown / unavailable",
    )
    report["ok"] = all_ok
    path = settings.database_path.parent / "diagnostics.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n{'All live checks passed.' if all_ok else 'Some checks failed.'} Summary saved to {path}")
    return 0 if all_ok else 1


def _report_error(label: str, exc: ProviderError, report: dict[str, Any], key: str) -> None:
    _line(False, label, f"{exc.kind.value}: {exc.message}")
    if exc.hint:
        print(f"       hint: {exc.hint}")
    report["checks"][key] = {"ok": False, "kind": exc.kind.value, "message": exc.message}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--labels", action="store_true", help="list BPIQ stage/event labels")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.labels)))


if __name__ == "__main__":
    main()
