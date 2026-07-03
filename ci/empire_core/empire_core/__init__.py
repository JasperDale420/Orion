"""Empire Core — shared infrastructure for all Empire services.

Provides unified logging, HTTP client, error handling, and config.
"""

from empire_core.errors import CommonErrorCode, EmpireError
from empire_core.excursion import ExcursionProfile, ExcursionTracker, compute_excursion

__all__ = [
    "EmpireError",
    "CommonErrorCode",
    "ExcursionProfile",
    "ExcursionTracker",
    "compute_excursion",
]
