from __future__ import annotations

import pickle
import warnings
from pathlib import Path

import pytest
from sklearn.exceptions import InconsistentVersionWarning

from orion.ml.model_loading import load_compatible_pickle


class _VersionMismatch:
    def __getstate__(self) -> dict[str, bool]:
        return {"restore": True}

    def __setstate__(self, state: dict[str, bool]) -> None:
        warnings.warn(
            InconsistentVersionWarning(
                estimator_name="test estimator",
                current_sklearn_version="1.9.0",
                original_sklearn_version="1.8.0",
            ),
            stacklevel=2,
        )
        self.__dict__.update(state)


def test_model_pickle_loader_rejects_sklearn_version_mismatch(tmp_path: Path) -> None:
    model_path = tmp_path / "model.pkl"
    model_path.write_bytes(pickle.dumps(_VersionMismatch()))

    with pytest.raises(InconsistentVersionWarning):
        load_compatible_pickle(model_path)

    compatible_path = tmp_path / "compatible.pkl"
    compatible_path.write_bytes(pickle.dumps({"model": "ok"}))
    assert load_compatible_pickle(compatible_path) == {"model": "ok"}
