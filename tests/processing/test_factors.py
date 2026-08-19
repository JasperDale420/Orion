"""Known-input/known-output tests for the pure decision-time factors."""

import math
from datetime import UTC, datetime, timedelta

import pytest

from orion.processing.factors import (
    f_abs_delta,
    f_bucket,
    f_dte,
    f_hujacobs,
    f_moneyness_std,
    f_premium_usd,
    f_prior_flow_align,
    f_rv20,
    f_spread_pct,
    f_vrp,
)

pytestmark = pytest.mark.unit

AS_OF = datetime(2026, 8, 14, 18, 0, 0, tzinfo=UTC)


def _print(**kwargs):
    base = {
        "ticker": "AAPL",
        "ts": AS_OF - timedelta(hours=1),
        "put_call": "C",
        "ask_prem": 0.0,
        "bid_prem": 0.0,
    }
    base.update(kwargs)
    return base


# --- f_rv20 -----------------------------------------------------------------


def test_rv20_flat_series_is_zero():
    assert f_rv20([100.0] * 20) == 0.0


def test_rv20_matches_hand_derived_alternating_series():
    # closes alternate 1,2,1,2,... (15 values) -> 14 log returns of +/- ln 2,
    # exactly 7 of each, so mean = 0 and the ddof=1 variance is
    # sum(r^2)/13 = 14*(ln2)^2/13. Annualized by sqrt(252).
    closes = [1.0 if i % 2 == 0 else 2.0 for i in range(15)]
    expected = math.log(2) * math.sqrt(14 / 13) * math.sqrt(252)
    assert f_rv20(closes) == pytest.approx(expected)


def test_rv20_uses_only_the_last_20_closes():
    tail = [1.0 if i % 2 == 0 else 2.0 for i in range(20)]
    # Older closes that would wildly change the answer if they were included.
    assert f_rv20([500.0, 3.0, 900.0, *tail]) == pytest.approx(f_rv20(tail))


def test_rv20_requires_at_least_15_closes():
    assert f_rv20([100.0] * 14) is None
    assert f_rv20([100.0] * 15) == 0.0


def test_rv20_returns_none_instead_of_raising_when_a_ratio_underflows():
    # 5e-324 / 1.7e308 underflows to 0.0, and log(0) raises: finite, positive,
    # in-range inputs must still come back as a missing measurement.
    assert f_rv20([1.7e308, 5e-324] * 8) is None


def test_rv20_rejects_missing_zero_negative_and_nonfinite_closes():
    assert f_rv20(None) is None
    assert f_rv20([]) is None
    assert f_rv20([100.0] * 14 + [0.0]) is None
    assert f_rv20([100.0] * 14 + [-5.0]) is None
    assert f_rv20([100.0] * 14 + [float("nan")]) is None
    assert f_rv20([100.0] * 14 + ["abc"]) is None


# --- f_vrp ------------------------------------------------------------------


def test_vrp_is_log_ratio_of_realized_over_implied():
    assert f_vrp(0.30, 0.20) == pytest.approx(math.log(1.5))


def test_vrp_sign_marks_cheap_vol_for_a_buyer():
    assert f_vrp(0.40, 0.20) > 0  # realized above implied -> options cheap
    assert f_vrp(0.10, 0.20) < 0


def test_vrp_requires_both_inputs_strictly_positive():
    assert f_vrp(None, 0.2) is None
    assert f_vrp(0.2, None) is None
    assert f_vrp(0.0, 0.2) is None
    assert f_vrp(0.2, 0.0) is None
    assert f_vrp(-0.2, 0.2) is None
    assert f_vrp(0.2, -0.2) is None
    assert f_vrp("abc", 0.2) is None


def test_vrp_returns_none_instead_of_raising_at_the_edges_of_the_float_range():
    assert f_vrp(5e-324, 1.7e308) is None  # ratio underflows to 0.0
    assert f_vrp(1.7e308, 5e-324) is None  # ratio overflows to inf


# --- f_hujacobs -------------------------------------------------------------


def test_hujacobs_is_negative_rv_for_calls_and_positive_for_puts():
    assert f_hujacobs(0.30, "CALL") == pytest.approx(-0.30)
    assert f_hujacobs(0.30, "PUT") == pytest.approx(0.30)
    assert f_hujacobs(0.30, "c") == pytest.approx(-0.30)
    assert f_hujacobs(0.30, "p") == pytest.approx(0.30)


def test_hujacobs_rejects_missing_or_unknown_inputs():
    assert f_hujacobs(None, "CALL") is None
    assert f_hujacobs(0.30, None) is None
    assert f_hujacobs(0.30, "STRADDLE") is None
    assert f_hujacobs(-0.30, "CALL") is None


# --- f_abs_delta ------------------------------------------------------------


def test_abs_delta_takes_magnitude_and_keeps_a_real_zero():
    assert f_abs_delta(0.42) == pytest.approx(0.42)
    assert f_abs_delta(-0.42) == pytest.approx(0.42)
    assert f_abs_delta(0.0) == 0.0
    assert f_abs_delta("0.42") == pytest.approx(0.42)


def test_abs_delta_rejects_missing_and_unparseable():
    assert f_abs_delta(None) is None
    assert f_abs_delta("") is None
    assert f_abs_delta("abc") is None
    assert f_abs_delta(float("inf")) is None


# --- f_moneyness_std --------------------------------------------------------


def test_moneyness_std_matches_the_closed_form():
    # ln(110/100) / (0.25 * sqrt(91.25/365)) with 91.25 days -> T = 0.25
    expected = math.log(1.1) / (0.25 * math.sqrt(0.25))
    assert f_moneyness_std(strike=110.0, spot=100.0, iv=0.25, dte_days=91.25) == pytest.approx(expected)


def test_moneyness_std_is_zero_at_the_money():
    assert f_moneyness_std(strike=100.0, spot=100.0, iv=0.25, dte_days=30) == pytest.approx(0.0)


def test_moneyness_std_requires_positive_strike_spot_iv_and_tenor():
    assert f_moneyness_std(strike=0.0, spot=100.0, iv=0.25, dte_days=30) is None
    assert f_moneyness_std(strike=110.0, spot=0.0, iv=0.25, dte_days=30) is None
    assert f_moneyness_std(strike=110.0, spot=100.0, iv=0.0, dte_days=30) is None
    assert f_moneyness_std(strike=110.0, spot=100.0, iv=0.25, dte_days=0) is None
    assert f_moneyness_std(strike=110.0, spot=100.0, iv=0.25, dte_days=-1) is None
    assert f_moneyness_std(strike=None, spot=100.0, iv=0.25, dte_days=30) is None


def test_moneyness_std_returns_none_instead_of_raising_at_the_edges_of_the_float_range():
    # Underflowing strike/spot ratio, and a denominator that underflows to 0.0.
    assert f_moneyness_std(strike=5e-324, spot=1.7e308, iv=0.25, dte_days=30) is None
    assert f_moneyness_std(strike=110.0, spot=100.0, iv=5e-324, dte_days=5e-324) is None


# --- f_dte ------------------------------------------------------------------


def test_dte_counts_calendar_days_between_dates():
    as_of = datetime(2026, 8, 16, 23, 30, tzinfo=UTC)
    assert f_dte(datetime(2026, 8, 20, tzinfo=UTC), as_of) == 4
    assert f_dte(datetime(2026, 8, 16, tzinfo=UTC), as_of) == 0
    assert f_dte(datetime(2026, 8, 14, tzinfo=UTC), as_of) == -2


def test_dte_treats_naive_datetimes_as_utc_and_rejects_missing():
    as_of = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    assert f_dte(datetime(2026, 8, 20), as_of) == 4
    assert f_dte(None, as_of) is None
    assert f_dte(datetime(2026, 8, 20, tzinfo=UTC), None) is None


# --- f_premium_usd ----------------------------------------------------------


def test_premium_usd_coerces_and_rejects_invalid():
    assert f_premium_usd(51870.0) == pytest.approx(51870.0)
    assert f_premium_usd("51870") == pytest.approx(51870.0)
    assert f_premium_usd(0.0) == 0.0
    assert f_premium_usd(None) is None
    assert f_premium_usd(-1.0) is None
    assert f_premium_usd("abc") is None


# --- f_spread_pct -----------------------------------------------------------


def test_spread_pct_is_width_over_mid():
    assert f_spread_pct(1.90, 2.10) == pytest.approx(0.10)
    assert f_spread_pct(0.60, 0.61) == pytest.approx(0.01 / 0.605)


def test_spread_pct_returns_negative_on_a_crossed_quote():
    assert f_spread_pct(2.10, 1.90) == pytest.approx(-0.10)


def test_spread_pct_rejects_zero_and_negative_quotes():
    assert f_spread_pct(0.0, 0.0) is None
    assert f_spread_pct(-1.0, 2.0) is None
    assert f_spread_pct(1.0, -2.0) is None
    assert f_spread_pct(None, 2.0) is None
    assert f_spread_pct("abc", 2.0) is None


# --- f_bucket ---------------------------------------------------------------


def test_bucket_matches_the_exit_rule_buckets():
    assert f_bucket(0) == "0DTE"
    assert f_bucket(2) == "SHORT_SWING"
    assert f_bucket(10) == "SWING"
    assert f_bucket(30) == "POSITION"


def test_bucket_is_none_when_dte_is_unknown():
    # Deliberately unlike bucket_for_dte's SWING default: as a factor, an
    # unknown DTE must not be recorded as a real bucket.
    assert f_bucket(None) is None
    assert f_bucket("abc") is None


# --- f_prior_flow_align -----------------------------------------------------


def test_prior_flow_align_is_zero_when_no_prints_exist():
    assert f_prior_flow_align([], ticker="AAPL", as_of=AS_OF, option_type="CALL") == 0.0


def test_prior_flow_align_agrees_with_a_call_candidate_on_ask_side_call_buying():
    prints = [_print(put_call="C", ask_prem=10_000.0)]
    assert f_prior_flow_align(prints, ticker="AAPL", as_of=AS_OF, option_type="CALL") == pytest.approx(10_000.0)
    assert f_prior_flow_align(prints, ticker="AAPL", as_of=AS_OF, option_type="PUT") == pytest.approx(-10_000.0)


def test_prior_flow_align_agrees_with_a_put_candidate_on_ask_side_put_buying():
    prints = [_print(put_call="P", ask_prem=5_000.0)]
    assert f_prior_flow_align(prints, ticker="AAPL", as_of=AS_OF, option_type="PUT") == pytest.approx(5_000.0)
    assert f_prior_flow_align(prints, ticker="AAPL", as_of=AS_OF, option_type="CALL") == pytest.approx(-5_000.0)


def test_prior_flow_align_nets_bid_side_against_ask_side():
    prints = [
        _print(put_call="C", ask_prem=10_000.0),
        _print(put_call="C", bid_prem=4_000.0),
        _print(put_call="P", ask_prem=1_000.0),
    ]
    # (10000 - 0) + (0 - 4000) = +6000 on calls, minus 1000 of put buying.
    assert f_prior_flow_align(prints, ticker="AAPL", as_of=AS_OF, option_type="CALL") == pytest.approx(5_000.0)


def test_prior_flow_align_excludes_prints_at_or_after_the_candidate():
    at_as_of = _print(ask_prem=10_000.0, ts=AS_OF)
    after = _print(ask_prem=10_000.0, ts=AS_OF + timedelta(seconds=1))
    assert f_prior_flow_align([at_as_of, after], ticker="AAPL", as_of=AS_OF, option_type="CALL") == 0.0


def test_prior_flow_align_window_boundary_is_inclusive_at_24h():
    at_edge = _print(ask_prem=10_000.0, ts=AS_OF - timedelta(hours=24))
    too_old = _print(ask_prem=10_000.0, ts=AS_OF - timedelta(hours=24, seconds=1))
    assert f_prior_flow_align([at_edge], ticker="AAPL", as_of=AS_OF, option_type="CALL") == pytest.approx(10_000.0)
    assert f_prior_flow_align([too_old], ticker="AAPL", as_of=AS_OF, option_type="CALL") == 0.0


def test_prior_flow_align_ignores_other_underlyings_and_unusable_prints():
    prints = [
        _print(ticker="MSFT", ask_prem=99_000.0),
        _print(put_call="STRADDLE", ask_prem=99_000.0),
        _print(put_call=None, ask_prem=99_000.0),
        _print(ts=None, ask_prem=99_000.0),
        _print(ask_prem="abc"),
        _print(ask_prem=7_000.0),
    ]
    assert f_prior_flow_align(prints, ticker="AAPL", as_of=AS_OF, option_type="CALL") == pytest.approx(7_000.0)


def test_prior_flow_align_treats_naive_print_timestamps_as_utc():
    prints = [_print(ask_prem=3_000.0, ts=(AS_OF - timedelta(hours=1)).replace(tzinfo=None))]
    assert f_prior_flow_align(prints, ticker="AAPL", as_of=AS_OF, option_type="CALL") == pytest.approx(3_000.0)


def test_prior_flow_align_rejects_missing_inputs():
    assert f_prior_flow_align(None, ticker="AAPL", as_of=AS_OF, option_type="CALL") is None
    assert f_prior_flow_align([], ticker="AAPL", as_of=None, option_type="CALL") is None
    assert f_prior_flow_align([], ticker="AAPL", as_of=AS_OF, option_type=None) is None
    assert f_prior_flow_align([], ticker="AAPL", as_of=AS_OF, option_type="STRADDLE") is None
    assert f_prior_flow_align([], ticker=None, as_of=AS_OF, option_type="CALL") is None


def test_prior_flow_align_never_raises_on_garbage_input():
    assert f_prior_flow_align(["not-a-dict", 42], ticker="AAPL", as_of=AS_OF, option_type="CALL") == 0.0
