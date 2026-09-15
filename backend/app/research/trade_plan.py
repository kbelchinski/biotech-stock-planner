"""Manual trade-plan calculator. Planning arithmetic only; places no orders.

Long positions only. Invalid inputs produce explicit errors instead of partial numbers.
"""

from __future__ import annotations

import math
from datetime import date

from app.domain.research import LossScenario, TradePlanInput, TradePlanOutput

DISCLAIMER = (
    "Planning estimate only. A stop price is not a guaranteed maximum loss: prices can gap through a stop, "
    "orders may fill worse than planned, and a binary catalyst can move the price far past it. No order is placed."
)
SIZE_MISMATCH_TOLERANCE = 0.01


def _positive(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and value > 0


def calculate_plan(plan: TradePlanInput, *, today: date) -> TradePlanOutput:
    errors: list[str] = []
    warnings: list[str] = []

    numeric = {
        "Entry price": plan.entry_price,
        "Loss budget": plan.loss_budget_usd,
        "Stop price": plan.stop_price,
        "Position size (shares)": plan.position_shares,
        "Capital allocation": plan.capital_allocation_usd,
        "Portfolio value": plan.portfolio_value_usd,
    }
    for label, value in numeric.items():
        if value is not None and (not math.isfinite(value) or value <= 0):
            errors.append(f"{label} must be a positive number when entered.")
    for pct in plan.decline_scenarios_pct:
        if not math.isfinite(pct) or pct <= 0 or pct > 100:
            errors.append(f"Decline scenario {pct:g}% must be greater than 0 and at most 100.")
    if plan.entry_price is None:
        errors.append("Entry price is required.")
    if plan.planned_exit_date is not None and plan.planned_exit_date <= today:
        warnings.append("Planned exit date is today or in the past.")

    entry = plan.entry_price if _positive(plan.entry_price) else None
    risk_per_share: float | None = None
    stop_shares: int | None = None
    stop_capital: float | None = None
    stop_note: str | None = None

    if entry is not None and plan.stop_price is not None and _positive(plan.stop_price):
        if plan.stop_price >= entry:
            errors.append("Stop/invalidation price must be below the entry price for a long position.")
        else:
            risk_per_share = entry - plan.stop_price
            if _positive(plan.loss_budget_usd):
                shares = math.floor(plan.loss_budget_usd / risk_per_share)  # type: ignore[operator]
                if shares < 1:
                    stop_note = (
                        f"Loss budget ${plan.loss_budget_usd:,.2f} is smaller than the ${risk_per_share:,.2f} risk per share; "
                        "no whole-share position fits."
                    )
                else:
                    stop_shares = shares
                    stop_capital = shares * entry
                    stop_note = "floor(loss budget ÷ (entry − stop)) whole shares."
            else:
                stop_note = "Enter a loss budget to size the position from the stop."
    elif plan.stop_price is None and _positive(plan.loss_budget_usd):
        stop_note = "No stop price entered, so a stop-based size cannot be calculated."

    capital: float | None = None
    basis: str | None = None
    implied_shares: float | None = None
    if entry is not None:
        if _positive(plan.position_shares):
            capital = plan.position_shares * entry  # type: ignore[operator]
            implied_shares = plan.position_shares
            basis = "Proposed shares × entry price"
            if _positive(plan.capital_allocation_usd) and abs(capital - plan.capital_allocation_usd) > SIZE_MISMATCH_TOLERANCE * plan.capital_allocation_usd:  # type: ignore[operator]
                warnings.append(
                    f"Proposed shares imply ${capital:,.2f}, which differs from the capital allocation ${plan.capital_allocation_usd:,.2f}. Shares were used."
                )
        elif _positive(plan.capital_allocation_usd):
            capital = plan.capital_allocation_usd
            implied_shares = capital / entry  # type: ignore[operator]
            basis = "Capital allocation"
        elif stop_capital is not None:
            capital = stop_capital
            implied_shares = stop_shares
            basis = "Stop-based size"
    if capital is not None and stop_capital is not None and capital > stop_capital * (1 + SIZE_MISMATCH_TOLERANCE):
        warnings.append(
            f"Proposed capital ${capital:,.2f} exceeds the stop-based size ${stop_capital:,.2f}; the loss at the stop would exceed the loss budget."
        )

    exposure = capital / plan.portfolio_value_usd if capital is not None and _positive(plan.portfolio_value_usd) else None  # type: ignore[operator]
    if exposure is not None and exposure > 1:
        warnings.append("Planned capital exceeds the entered portfolio value.")

    scenarios = [
        LossScenario(
            decline_pct=pct,
            loss_usd=capital * pct / 100 if capital is not None else None,
            portfolio_impact_pct=(capital * pct / 100) / plan.portfolio_value_usd * 100
            if capital is not None and _positive(plan.portfolio_value_usd)
            else None,
        )
        for pct in sorted(set(p for p in plan.decline_scenarios_pct if math.isfinite(p) and 0 < p <= 100))
    ]
    if capital is None and not errors:
        warnings.append("Enter shares, a capital allocation, or a stop with a loss budget to calculate capital and loss scenarios.")

    return TradePlanOutput(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        capital_commitment_usd=capital if not errors else None,
        capital_basis=basis if not errors else None,
        implied_shares=implied_shares if not errors else None,
        stop_based_shares=stop_shares if not errors else None,
        stop_based_capital_usd=stop_capital if not errors else None,
        stop_based_note=stop_note,
        risk_per_share_usd=risk_per_share if not errors else None,
        portfolio_exposure_pct=exposure * 100 if exposure is not None and not errors else None,
        loss_scenarios=scenarios if not errors else [],
        disclaimer=DISCLAIMER,
    )
