from datetime import date

from app.domain.research import TradePlanInput
from app.research.trade_plan import calculate_plan

TODAY = date(2026, 9, 14)


def plan(**kw):
    return calculate_plan(TradePlanInput(ticker="AAAA", **kw), today=TODAY)


def test_stop_based_size_exposure_and_scenarios():
    out = plan(entry_price=10, stop_price=8, loss_budget_usd=500, portfolio_value_usd=50_000, planned_exit_date=date(2026, 11, 1))
    assert out.valid
    assert out.stop_based_shares == 250 and out.stop_based_capital_usd == 2500
    assert out.capital_commitment_usd == 2500 and out.capital_basis == "Stop-based size"
    assert out.portfolio_exposure_pct == 5.0
    assert [(s.decline_pct, s.loss_usd) for s in out.loss_scenarios] == [(10, 250), (25, 625), (50, 1250)]
    assert "not a guaranteed maximum loss" in out.disclaimer


def test_invalid_inputs_are_explicit():
    out = plan(entry_price=10, stop_price=12, loss_budget_usd=500)
    assert not out.valid and any("below the entry" in e for e in out.errors)
    assert out.capital_commitment_usd is None and out.loss_scenarios == []
    assert not plan(entry_price=None).valid
    assert not plan(entry_price=-1).valid
    assert not plan(entry_price=10, decline_scenarios_pct=[0, 150]).valid


def test_budget_smaller_than_risk_per_share_and_size_warnings():
    out = plan(entry_price=10, stop_price=5, loss_budget_usd=3)
    assert out.valid and out.stop_based_shares is None and "no whole-share position" in out.stop_based_note
    big = plan(entry_price=10, stop_price=9, loss_budget_usd=100, position_shares=500, portfolio_value_usd=1000)
    assert big.capital_commitment_usd == 5000 and big.capital_basis == "Proposed shares × entry price"
    assert any("exceeds the stop-based size" in w for w in big.warnings)
    assert any("exceeds the entered portfolio value" in w for w in big.warnings)
    past = plan(entry_price=10, capital_allocation_usd=1000, planned_exit_date=TODAY)
    assert past.implied_shares == 100 and any("past" in w for w in past.warnings)
