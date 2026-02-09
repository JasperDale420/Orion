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
