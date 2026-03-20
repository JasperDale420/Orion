"""Risk management package — decomposed from the monolithic risk_manager.py.

Provides the same public API via the RiskManager class, with internal
concerns delegated to focused sub-modules.
"""

from orion.execution.risk.manager import RiskManager

__all__ = ["RiskManager"]
