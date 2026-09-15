"""Price and volume context from Alpaca daily bars. Context only: never a filter or a signal.

Conventions (applied to every metric):
- Bars: Alpaca historical daily bars, feed=sip, adjustment=split. Prices AND volumes are
  split-adjusted by the provider so returns, averages and volume ratios are consistent across
  splits. Dividends are not adjusted (price return, not total return).
- Sessions: the XNYS calendar. "N sessions" means N completed regular sessions, counted on the
  exchange calendar up to the latest completed session. Holidays are not sessions.
- A missing bar for an expected session is missing data. It is never filled, interpolated, or
  replaced by an older bar. Metrics that need it return status missing_bars.
- The screener's price and liquidity criteria keep using adjustment=raw (unchanged).
"""

from __future__ import annotations

import math
from datetime import date, datetime

from app.domain.models import DailyBar
from app.domain.research import FirstSeen, Metric, MetricStatus, PriceContext, PriceGap

RETURN_WINDOWS = (5, 20, 60)
VOLUME_RECENT_SESSIONS = 5
VOLUME_BASELINE_SESSIONS = 60
VOLUME_BASELINE_MIN_BARS = 45
VOLATILITY_SESSIONS = 20
VOLATILITY_MIN_RETURNS = 15
GAP_LOOKBACK_SESSIONS = 60
GAP_THRESHOLD_PCT = 10.0
HIGH_WINDOWS = ((60, 45), (252, 200))  # (sessions, minimum bars present)
MOVING_AVERAGES = (20, 50)
TRADING_DAYS_PER_YEAR = 252

CONVENTIONS = [
    "Alpaca historical daily bars, feed=sip (consolidated), adjustment=split: prices and volumes split-adjusted; dividends not adjusted.",
    "Session counts use the XNYS exchange calendar up to the latest completed session; holidays are not sessions.",
    "Missing bars are never filled or replaced with older bars; affected metrics are marked unavailable.",
    "Alpaca notes its daily volume can differ from other consolidated-volume sources.",
    "Screening criteria (price, liquidity) still use adjustment=raw and are not affected by these metrics.",
]


def _metric(key: str, label: str, unit: str, formula: str, lookback: str) -> dict:
    return {"key": key, "label": label, "unit": unit, "formula": formula, "lookback": lookback}


def compute_price_context(
    *,
    ticker: str,
    bars: list[DailyBar],
    benchmark_symbol: str,
    benchmark_bars: list[DailyBar] | None,
    sessions: list[date],
    latest_session: date,
    first_seen: FirstSeen | None,
    retrieved_at: datetime | None,
    first_seen_close: float | None = None,
) -> PriceContext:
    """`sessions` are the XNYS sessions in the requested window, ascending, ending at `latest_session`.

    `first_seen_close` is the split-adjusted close on `first_seen.price_session`, if a bar exists.
    """
    sessions = [s for s in sessions if s <= latest_session]
    by_date = {b.session_date: b for b in bars}
    bench = {b.session_date: b for b in (benchmark_bars or [])}
    present = sum(1 for s in sessions if s in by_date)
    latest_bar = max((b.session_date for b in bars), default=None)
    issues: list[str] = []
    metrics: list[Metric] = []

    if latest_bar is None:
        freshness = "No bars returned for this symbol."
    elif latest_bar == latest_session:
        freshness = f"Latest bar is the latest completed session ({latest_session.isoformat()})."
    else:
        freshness = f"STALE: latest bar is {latest_bar.isoformat()}; latest completed session is {latest_session.isoformat()}."
        issues.append(freshness)

    # ------------------------------------------------------------ returns
    stock_returns: dict[str, float | None] = {}
    for n in RETURN_WINDOWS:
        spec = _metric(
            f"return_{n}",
            f"Return, {n} sessions",
            "pct",
            f"close[t] / close[t−{n}] − 1, t = latest completed session",
            f"{n} completed XNYS sessions",
        )
        value, status, detail = _window_return(by_date, sessions, n)
        stock_returns[f"return_{n}"] = value
        metrics.append(Metric(**spec, value=value, status=status, detail=detail))
        bvalue, bstatus, bdetail = _window_return(bench, sessions, n) if benchmark_bars is not None else (None, MetricStatus.UNAVAILABLE, "Benchmark bars unavailable.")
        rel = value - bvalue if value is not None and bvalue is not None else None
        metrics.append(
            Metric(
                key=f"relative_{n}",
                label=f"Relative to {benchmark_symbol}, {n} sessions",
                unit="pct_points",
                formula=f"stock return − {benchmark_symbol} return over the same {n} sessions (arithmetic difference)",
                lookback=f"{n} completed XNYS sessions",
                value=rel,
                status=MetricStatus.OK if rel is not None else (status if value is None else bstatus),
                detail=None if rel is not None else (detail if value is None else bdetail),
            )
        )

    # ------------------------------------------------------------ since first seen in a saved screen
    since_spec = _metric(
        "return_since_first_seen",
        "Return since first seen in a saved scan",
        "pct",
        "close[t] / close[first-seen price session] − 1",
        "From the first saved scan's price session",
    )
    last_bar = by_date.get(latest_session)
    if first_seen is None:
        metrics.append(Metric(**since_spec, value=None, status=MetricStatus.UNAVAILABLE, detail="Not found in any saved scan for this data mode."))
    elif first_seen.price_session is None or first_seen_close is None:
        metrics.append(
            Metric(
                **since_spec,
                value=None,
                status=MetricStatus.MISSING_BARS,
                detail=f"No split-adjusted bar for the first-seen price session ({first_seen.price_session}).",
            )
        )
    elif last_bar is None:
        metrics.append(Metric(**since_spec, value=None, status=MetricStatus.MISSING_BARS, detail="No bar for the latest completed session."))
    else:
        value = last_bar.close / first_seen_close - 1
        basis = "first qualified" if first_seen.basis == "first_qualified" else "first evaluated (never qualified)"
        metrics.append(
            Metric(
                **since_spec,
                value=value,
                status=MetricStatus.OK,
                detail=f"Basis: {basis} in scan {first_seen.scan_id[:8]} on {first_seen.scan_date}; price session {first_seen.price_session}.",
            )
        )
        bstart = bench.get(first_seen.price_session)
        bend = bench.get(latest_session)
        brel = value - (bend.close / bstart.close - 1) if bstart and bend else None
        metrics.append(
            Metric(
                key="relative_since_first_seen",
                label=f"Relative to {benchmark_symbol} since first seen",
                unit="pct_points",
                formula=f"stock return − {benchmark_symbol} return over the same sessions",
                lookback="From the first saved scan's price session",
                value=brel,
                status=MetricStatus.OK if brel is not None else MetricStatus.MISSING_BARS,
                detail=None if brel is not None else "Benchmark bar missing at the start or end session.",
            )
        )

    # ------------------------------------------------------------ volume vs baseline
    vol_spec = _metric(
        "volume_ratio",
        "Recent volume vs baseline",
        "ratio",
        f"mean(volume, last {VOLUME_RECENT_SESSIONS} sessions) / mean(volume, the {VOLUME_BASELINE_SESSIONS} sessions before them)",
        f"{VOLUME_RECENT_SESSIONS} recent + {VOLUME_BASELINE_SESSIONS} baseline sessions",
    )
    need = VOLUME_RECENT_SESSIONS + VOLUME_BASELINE_SESSIONS
    if len(sessions) < need:
        metrics.append(Metric(**vol_spec, value=None, status=MetricStatus.INSUFFICIENT_HISTORY, detail=f"Needs {need} sessions; window has {len(sessions)}."))
    else:
        recent = [by_date.get(s) for s in sessions[-VOLUME_RECENT_SESSIONS:]]
        baseline = [by_date.get(s) for s in sessions[-need:-VOLUME_RECENT_SESSIONS]]
        base_present = [b for b in baseline if b is not None]
        if any(b is None for b in recent):
            metrics.append(Metric(**vol_spec, value=None, status=MetricStatus.MISSING_BARS, detail="A recent session has no bar."))
        elif len(base_present) < VOLUME_BASELINE_MIN_BARS:
            metrics.append(
                Metric(
                    **vol_spec,
                    value=None,
                    status=MetricStatus.MISSING_BARS,
                    detail=f"Baseline has {len(base_present)} of {VOLUME_BASELINE_SESSIONS} bars; at least {VOLUME_BASELINE_MIN_BARS} required.",
                )
            )
        else:
            base_mean = sum(b.volume for b in base_present) / len(base_present)
            recent_mean = sum(b.volume for b in recent) / len(recent)  # type: ignore[union-attr]
            if base_mean <= 0:
                metrics.append(Metric(**vol_spec, value=None, status=MetricStatus.UNAVAILABLE, detail="Baseline volume is zero."))
            else:
                metrics.append(
                    Metric(
                        **vol_spec,
                        value=recent_mean / base_mean,
                        status=MetricStatus.OK,
                        detail=f"Baseline uses {len(base_present)} of {VOLUME_BASELINE_SESSIONS} sessions with bars.",
                    )
                )

    # ------------------------------------------------------------ historical volatility
    hv_spec = _metric(
        "volatility_20",
        "Historical volatility (annualized)",
        "pct",
        f"sample stdev of ln(close[i]/close[i−1]) over the last {VOLATILITY_SESSIONS} session pairs × √{TRADING_DAYS_PER_YEAR}",
        f"{VOLATILITY_SESSIONS} consecutive-session returns; ≥{VOLATILITY_MIN_RETURNS} required",
    )
    if len(sessions) < VOLATILITY_SESSIONS + 1:
        metrics.append(Metric(**hv_spec, value=None, status=MetricStatus.INSUFFICIENT_HISTORY, detail=f"Window has {len(sessions)} sessions."))
    else:
        window = sessions[-(VOLATILITY_SESSIONS + 1):]
        rets = [
            math.log(by_date[b].close / by_date[a].close)
            for a, b in zip(window, window[1:])
            if a in by_date and b in by_date
        ]
        if len(rets) < VOLATILITY_MIN_RETURNS:
            metrics.append(Metric(**hv_spec, value=None, status=MetricStatus.MISSING_BARS, detail=f"{len(rets)} usable returns (pairs of consecutive sessions with bars)."))
        else:
            mean = sum(rets) / len(rets)
            var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
            metrics.append(
                Metric(**hv_spec, value=math.sqrt(var) * math.sqrt(TRADING_DAYS_PER_YEAR), status=MetricStatus.OK, detail=f"{len(rets)} returns used.")
            )

    # ------------------------------------------------------------ distance from highs
    for n, minimum in HIGH_WINDOWS:
        spec = _metric(
            f"from_high_{n}",
            f"Distance from {n}-session high",
            "pct",
            f"close[t] / max(high over last {n} sessions) − 1",
            f"{n} sessions; ≥{minimum} bars required",
        )
        if len(sessions) < n:
            metrics.append(Metric(**spec, value=None, status=MetricStatus.INSUFFICIENT_HISTORY, detail=f"Window has {len(sessions)} sessions."))
            continue
        window_bars = [by_date[s] for s in sessions[-n:] if s in by_date]
        if last_bar is None or len(window_bars) < minimum:
            metrics.append(Metric(**spec, value=None, status=MetricStatus.MISSING_BARS, detail=f"{len(window_bars)} of {n} bars present."))
            continue
        high = max(b.high for b in window_bars)
        metrics.append(Metric(**spec, value=last_bar.close / high - 1, status=MetricStatus.OK, detail=f"High {high:.4f}; {len(window_bars)} of {n} bars."))

    # ------------------------------------------------------------ moving averages
    for n in MOVING_AVERAGES:
        spec = _metric(
            f"from_sma_{n}",
            f"Distance from {n}-session SMA",
            "pct",
            f"close[t] / mean(close over last {n} sessions) − 1",
            f"{n} sessions; all bars required",
        )
        if len(sessions) < n:
            metrics.append(Metric(**spec, value=None, status=MetricStatus.INSUFFICIENT_HISTORY, detail=f"Window has {len(sessions)} sessions."))
            continue
        window_bars = [by_date.get(s) for s in sessions[-n:]]
        if any(b is None for b in window_bars) or last_bar is None:
            missing = sum(b is None for b in window_bars)
            metrics.append(Metric(**spec, value=None, status=MetricStatus.MISSING_BARS, detail=f"{missing} session(s) in the window have no bar."))
            continue
        sma = sum(b.close for b in window_bars) / n  # type: ignore[union-attr]
        metrics.append(Metric(**spec, value=last_bar.close / sma - 1, status=MetricStatus.OK, detail=f"SMA {sma:.4f}."))

    # ------------------------------------------------------------ overnight gaps
    gaps: list[PriceGap] = []
    gap_window = sessions[-(GAP_LOOKBACK_SESSIONS + 1):]
    for prev, cur in zip(gap_window, gap_window[1:]):
        a, b = by_date.get(prev), by_date.get(cur)
        if a is None or b is None:
            continue
        pct = (b.open / a.close - 1) * 100
        if abs(pct) >= GAP_THRESHOLD_PCT:
            gaps.append(PriceGap(session=cur, previous_session=prev, previous_close=a.close, open=b.open, gap_pct=pct))

    return PriceContext(
        ticker=ticker,
        benchmark_symbol=benchmark_symbol,
        feed="sip",
        adjustment="split",
        latest_completed_session=latest_session,
        latest_bar_session=latest_bar,
        expected_sessions=len(sessions),
        present_sessions=present,
        window_start=sessions[0] if sessions else latest_session,
        retrieved_at=retrieved_at,
        freshness=freshness,
        metrics=metrics,
        gaps=gaps,
        gap_threshold_pct=GAP_THRESHOLD_PCT,
        first_seen=first_seen,
        conventions=CONVENTIONS
        + [
            f"Overnight gap = open[t] / close[previous session] − 1 over the last {GAP_LOOKBACK_SESSIONS} sessions; listed when |gap| ≥ {GAP_THRESHOLD_PCT:g}% and both bars exist.",
        ],
        issues=issues,
    )


def _window_return(by_date: dict[date, DailyBar], sessions: list[date], n: int) -> tuple[float | None, MetricStatus, str | None]:
    if len(sessions) < n + 1:
        return None, MetricStatus.INSUFFICIENT_HISTORY, f"Needs {n + 1} sessions; window has {len(sessions)}."
    end, start = sessions[-1], sessions[-1 - n]
    a, b = by_date.get(start), by_date.get(end)
    if a is None or b is None:
        missing = ", ".join(s.isoformat() for s, bar in ((start, a), (end, b)) if bar is None)
        return None, MetricStatus.MISSING_BARS, f"No bar for {missing}."
    return b.close / a.close - 1, MetricStatus.OK, f"{start.isoformat()} close {a.close:.4f} → {end.isoformat()} close {b.close:.4f}."
