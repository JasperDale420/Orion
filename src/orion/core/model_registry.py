import logging
import os
from typing import Any, Dict, Optional

import joblib

from orion.shared.patterns import AsyncSingleton

logger = logging.getLogger(__name__)


class ModelRegistry(AsyncSingleton):
    """
    Singleton registry for loading and caching ML models.
    Supports local file paths (joblib/pickle).
    """

    _cache: Dict[str, Any] = {}

    def __init__(self) -> None:
        super().__init__()

    @classmethod
    def get(cls, model_uri: str) -> Optional[Any]:
        """
        Retrieves a model from the registry. Uses LRU-like caching.

        Args:
            model_uri: formatted string, e.g. "file:///path/to/model.joblib"
                       or just relative path "artifacts/models/v1.joblib"
        """
        if not model_uri:
            return None

        # Normalization
        if model_uri.startswith("file://"):
            path = model_uri.replace("file://", "")
        else:
            path = model_uri

        # Check Cache
        if path in cls._cache:
            return cls._cache[path]

        # Load
        try:
            if not os.path.exists(path):
                # Try relative to artifacts dir if not absolute
                # We assume running from project root usually, but let's be robust
                # If path is just "v1.joblib", maybe look in artifacts/models?
                # For now, simplistic check.
                logger.warning(f"Model file not found at {path}")
                return None

            model = joblib.load(path)

            # Update Cache (Simple unbounded for now, or naive check)
            if len(cls._cache) > 20:
                # Evict one
                cls._cache.pop(next(iter(cls._cache)))

            cls._cache[path] = model
            logger.info(f"Loaded model from {path}")
            return model

        except Exception as e:
            raise RuntimeError(f"Failed to load model from {path}: {e}") from e

    @classmethod
    def clear_cache(cls) -> None:
        cls._cache.clear()
