"""Conversion-parity guard for the SQLAlchemy 2.0 typed-declarative models.

R6 phase 1 converted the four safety-critical model modules
(``models_execution``, ``models_risk``, ``models_gold``, ``models_dlq``) from
the legacy ``Column(...)`` style to ``Mapped[...] = mapped_column(...)``.

The whole point of the conversion is to make the nullability / detached-instance
bug class statically visible. These tests lock in that invariant so it cannot
silently rot:

1. Every mapped column declares its Python type via a ``Mapped[...]``
   annotation (no bare ``Column`` survivors).
2. A column's runtime ``nullable`` flag matches the ``Optional``-ness of its
   ``Mapped[...]`` annotation — a ``nullable=True`` column must be typed
   ``Mapped[T | None]`` and vice-versa. This is the exact mismatch the
   conversion was meant to eliminate (e.g. the old ``payload`` /
   ``stack_trace`` DLQ columns were ``nullable=True`` but typed non-Optional).
"""

from __future__ import annotations

import typing

import pytest
from sqlalchemy.orm import Mapped

from orion.storage import (
    models_dlq,
    models_execution,
    models_gold,
    models_ml,
    models_risk,
    models_silver,
)
from orion.storage.db import Base

pytestmark = pytest.mark.unit


# (module, ORM class) pairs for the converted modules.
CONVERTED_MODELS = [
    models_execution.OrderRecord,
    models_execution.FillRecord,
    models_execution.PositionRunningStats,
    models_execution.PositionSnapshot,
    models_risk.RiskState,
    models_risk.ProcessedFill,
    models_risk.PendingOrder,
    models_gold.CandidateTrade,
    models_gold.ExitDecision,
    models_gold.StrategyDecision,
    models_gold.GoldTickerRollup,
    models_gold.CandidateLabel,
    models_gold.LabelEvent,
    models_gold.LabelWindow,
    models_gold.GoldFeatureEvent,
    models_dlq.DeadLetterQueue,
    models_ml.MLPatternInsight,
    models_ml.MLFeatureImportanceHistory,
    models_ml.MLPrediction,
    models_silver.SilverSignal,
]


def _annotation_is_optional(annotation: object) -> bool:
    """True if a ``Mapped[...]`` inner type permits ``None`` (Optional / `| None`).

    ``Mapped[Any]`` is treated as permitting ``None``: ``Any`` is the top type
    and includes ``None``. This is the deliberate annotation for the pgvector
    ``embedding_vec`` column, whose Python-side value is a list/ndarray (or
    ``None``) depending on the driver — so the static type cannot lie about
    nullability the way a concrete ``Mapped[T]`` would.
    """
    args = typing.get_args(annotation)
    if not args:
        return False
    inner = args[0]
    if inner is typing.Any:
        return True
    inner_args = typing.get_args(inner)
    return type(None) in inner_args


@pytest.mark.parametrize("model", CONVERTED_MODELS, ids=lambda m: m.__name__)
def test_every_column_is_mapped_typed(model: type) -> None:
    """No bare ``Column`` survivors — every persisted column has a Mapped annotation."""
    hints = typing.get_type_hints(model, include_extras=True)
    for col in model.__table__.columns:
        attr = col.key
        assert attr in hints, f"{model.__name__}.{attr} has no type annotation (not Mapped[])"
        origin = typing.get_origin(hints[attr])
        assert origin is Mapped, f"{model.__name__}.{attr} is annotated {hints[attr]!r}, expected Mapped[...]"


@pytest.mark.parametrize("model", CONVERTED_MODELS, ids=lambda m: m.__name__)
def test_nullability_matches_annotation(model: type) -> None:
    """A column's runtime ``nullable`` must agree with its ``Mapped[...]`` Optionality.

    This is the invariant the conversion exists to enforce: the static type now
    tells the truth about whether a loaded attribute can be ``None``.
    """
    hints = typing.get_type_hints(model, include_extras=True)
    mismatches: list[str] = []
    for col in model.__table__.columns:
        annotation = hints[col.key]
        optional = _annotation_is_optional(annotation)
        if col.nullable != optional:
            mismatches.append(
                f"{model.__name__}.{col.key}: nullable={col.nullable} but annotation "
                f"{'is' if optional else 'is NOT'} Optional ({annotation!r})"
            )
    assert not mismatches, "Mapped[] nullability drift:\n" + "\n".join(mismatches)


def test_converted_tables_present_in_metadata() -> None:
    """Sanity: all converted models stayed registered on the shared Base.metadata."""
    expected = {m.__tablename__ for m in CONVERTED_MODELS}
    assert expected <= set(Base.metadata.tables.keys())
