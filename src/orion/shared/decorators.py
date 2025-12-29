"""
Standard retry decorators for Orion.

Provides reusable retry configurations to eliminate duplication across the codebase.
"""

from tenacity import retry, stop_after_attempt, wait_exponential

# Standard retry for database operations
# Used for: DB writes, persistence operations
db_retry = retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))

# Standard retry for API calls with longer backoff
# Used for: External API calls, network operations
api_retry = retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
