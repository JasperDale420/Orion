from __future__ import annotations

import pickle
import warnings
from pathlib import Path
from typing import Any

from sklearn.exceptions import InconsistentVersionWarning


def load_compatible_pickle(path: Path) -> Any:
    """Load a trusted local model, rejecting unsupported sklearn artifacts."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", InconsistentVersionWarning)
        with path.open("rb") as model_file:
            return pickle.load(model_file)  # noqa: S301
