from __future__ import annotations

from types import SimpleNamespace

from orion import main_execution


def test_should_apply_options_exit_rules_only_for_option_positions() -> None:
    option_position = SimpleNamespace(option_chain="AAPL260220C00200000")
    equity_position = SimpleNamespace(option_chain=None)
    empty_contract_position = SimpleNamespace(option_chain="")

    assert main_execution._should_apply_options_exit_rules(option_position) is True
    assert main_execution._should_apply_options_exit_rules(equity_position) is False
    assert main_execution._should_apply_options_exit_rules(empty_contract_position) is False


def test_scope_recent_flow_for_position_filters_to_matching_option_chain() -> None:
    position = SimpleNamespace(option_chain="AAPL260220C00200000")
    recent_flow = [
        SimpleNamespace(option_chain="AAPL260220C00200000", premium_usd=1000),
        SimpleNamespace(option_chain="AAPL260227C00200000", premium_usd=2000),
        SimpleNamespace(option_chain=None, premium_usd=3000),
    ]

    scoped = main_execution._scope_recent_flow_for_position(position, recent_flow)

    assert len(scoped) == 1
    assert scoped[0].option_chain == "AAPL260220C00200000"


def test_scope_recent_flow_for_position_returns_all_for_non_option_positions() -> None:
    position = SimpleNamespace(option_chain=None)
    recent_flow = [
        SimpleNamespace(option_chain="AAPL260220C00200000"),
        SimpleNamespace(option_chain="AAPL260227C00200000"),
    ]

    scoped = main_execution._scope_recent_flow_for_position(position, recent_flow)

    assert scoped == recent_flow
