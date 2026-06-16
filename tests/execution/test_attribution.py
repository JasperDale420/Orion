"""Tests for the centralized Orion attribution helpers.

H-12 from predict 260430-1751-execution-reliability flagged six duplicated
client-order-id-prefix checks across execution_engine and fill_processor as a
fragile attribution architecture — the 2026-04 shared-account bug came from a
truthiness check on the empty-set return collapsing "Orion has no orders" into
"skip the filter entirely". The fix consolidates the prefix and lookup
semantics into `orion.execution.attribution`. These tests pin the contract.
"""

from __future__ import annotations

import pytest

from orion.execution.attribution import (
    ORDER_ID_PREFIX,
    is_orion_owned,
    mint_orion_order_id,
    orion_order_id_sql_pattern,
)


@pytest.mark.unit
class TestIsOrionOwned:
    def test_minted_id_is_owned(self) -> None:
        assert is_orion_owned(mint_orion_order_id()) is True

    def test_explicit_prefix_is_owned(self) -> None:
        assert is_orion_owned("orion_abc123") is True

    def test_foreign_prefix_is_not_owned(self) -> None:
        assert is_orion_owned("3roses_abc") is False
        assert is_orion_owned("cerberus_xyz") is False

    def test_no_prefix_is_not_owned(self) -> None:
        assert is_orion_owned("abc123") is False

    def test_none_is_not_owned(self) -> None:
        # Default-deny — this is the bug fix from 2026-04. An absent
        # client_order_id must NEVER be attributed to Orion.
        assert is_orion_owned(None) is False

    def test_empty_string_is_not_owned(self) -> None:
        # Same default-deny contract as None.
        assert is_orion_owned("") is False

    def test_prefix_alone_is_owned(self) -> None:
        # Edge case: the bare prefix matches startswith(). Not actually a real
        # mint output, but documenting the contract.
        assert is_orion_owned("orion_") is True

    def test_partial_prefix_is_not_owned(self) -> None:
        # "orio" is not "orion_" — no false positive on shorter prefixes.
        assert is_orion_owned("orio_abc") is False
        assert is_orion_owned("orion") is False  # missing trailing underscore


@pytest.mark.unit
class TestMintOrionOrderId:
    def test_mint_starts_with_prefix(self) -> None:
        oid = mint_orion_order_id()
        assert oid.startswith(ORDER_ID_PREFIX)

    def test_mint_is_unique_across_calls(self) -> None:
        ids = {mint_orion_order_id() for _ in range(50)}
        assert len(ids) == 50

    def test_minted_ids_are_round_trip_attributable(self) -> None:
        for _ in range(10):
            oid = mint_orion_order_id()
            assert is_orion_owned(oid) is True


@pytest.mark.unit
class TestSqlPattern:
    def test_pattern_matches_prefix_plus_wildcard(self) -> None:
        assert orion_order_id_sql_pattern() == f"{ORDER_ID_PREFIX}%"

    def test_pattern_uses_canonical_prefix(self) -> None:
        # If anyone ever changes the prefix without updating the SQL pattern,
        # this fails — locks the two together.
        assert orion_order_id_sql_pattern().startswith(ORDER_ID_PREFIX)


@pytest.mark.unit
class TestExecutionEngineReExport:
    """ORDER_ID_PREFIX is re-exported from execution_engine for backward
    compatibility — pin the re-export so existing imports keep working."""

    def test_reexport_is_same_string(self) -> None:
        from orion.execution.execution_engine import ORDER_ID_PREFIX as engine_prefix

        assert engine_prefix == ORDER_ID_PREFIX

    def test_reexport_in_engine_all(self) -> None:
        from orion.execution import execution_engine

        assert "ORDER_ID_PREFIX" in execution_engine.__all__
