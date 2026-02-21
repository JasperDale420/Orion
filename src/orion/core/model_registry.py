import logging
import os
import threading
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
    _cache_lock = threading.Lock()

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

        # Check Cache (thread-safe read)
        with cls._cache_lock:
            if path in cls._cache:
                return cls._cache[path]

        # Load outside lock to avoid holding it during I/O
        if not os.path.exists(path):
            logger.warning(f"Model file not found at {path}")
            return None

        try:
            model = joblib.load(path)
        except Exception as e:
            raise RuntimeError(f"Failed to load model from {path}: {e}") from e

        # Update Cache (thread-safe write)
        with cls._cache_lock:
            if len(cls._cache) > 20:
                cls._cache.pop(next(iter(cls._cache)))
            cls._cache[path] = model

        logger.info(f"Loaded model from {path}")
        return model

    @classmethod
    def clear_cache(cls) -> None:
        with cls._cache_lock:
            cls._cache.clear()
