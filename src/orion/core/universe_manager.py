import logging
import time
from collections.abc import Iterable
from datetime import UTC, date, datetime

from sqlalchemy import select

from orion.config import STATIC_WATCHLIST, UNIVERSE_TTL_SECONDS
from orion.storage.db import async_session_factory
from orion.storage.models import BronzeEvent
from orion.storage.models_silver import SilverUWAlert

logger = logging.getLogger(__name__)


class UniverseManager:
    """
    Manages the active universe of tickers to ingest data for.
    Tracks state by source (Config, Alert, Activity) and applies TTL.
    """

    def __init__(self) -> None:
        # Map of ticker -> last_active_utc_timestamp (float seconds)
        # Note: We use system time here for TTL tracking.
        self.active_tickers: dict[str, float] = {}

        # Explicit static set that never expires (config watchlists)
        self.static_universe: set[str] = set(STATIC_WATCHLIST)

        # Positions currently held should always be in-universe.
        self.held_tickers: set[str] = set()

        # Alerts should keep a ticker in-universe longer than generic activity.
        self.alert_tickers: dict[str, float] = {}

        # Tickers with explicit option expiry dates (e.g. from Flow/Alerts)
        # These should remain active until the option expires.
        self.expiry_tickers: dict[str, date] = {}

    async def hydrate_from_db(self) -> None:
        """
        Hydrates the universe from the database, specifically looking for
        active alerts with future expiration dates.
        """
        logger.info("Hydrating Universe from DB for active option contexts...")
        today_str = datetime.now(UTC).strftime("%Y-%m-%d")

        count = 0
        try:
            async with async_session_factory() as session:
                # Query distinct tickers with future expiry from Alerts
                stmt = (
                    select(SilverUWAlert.ticker, SilverUWAlert.expiry)
                    .where(SilverUWAlert.expiry >= today_str)
                    .group_by(SilverUWAlert.ticker, SilverUWAlert.expiry)
                )

                result = await session.execute(stmt)
                for row in result:
                    ticker = row.ticker
                    expiry_str = row.expiry

                    if not ticker or not expiry_str:
                        continue

                    try:
                        exp_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()

                        # Add to expiry tracking
                        current_exp = self.expiry_tickers.get(ticker)
                        if not current_exp or exp_date > current_exp:
                            self.expiry_tickers[ticker] = exp_date

                        # Ensure it's marked as active so Alpaca picks it up immediately
                        self.active_tickers[ticker] = time.time()
                        self.alert_tickers[ticker] = time.time()
                        count += 1
                    except ValueError:
                        continue

            logger.info(f"Hydrated {count} active contexts from DB with future options expiry.")

        except Exception as e:
            logger.error(f"Failed to hydrate universe from DB: {e}")

    def update_from_config(self, tickers: list[str]) -> None:
        """
        Updates the static watchlist (runtime config update).
        """
        self.static_universe = set(tickers)
        logger.info(f"Updated static universe. Count: {len(self.static_universe)}")

    def update_from_positions(self, tickers: Iterable[str]) -> None:
        """
        Pins tickers that are currently held (positions currently held).
        """
        self.held_tickers = {t for t in tickers if t}

    def add_ticker(self, ticker: str) -> None:
        """Promote a ticker into the active universe by name."""
        if ticker:
            self.active_tickers[ticker] = time.time()

    def update_from_event(self, event: BronzeEvent) -> None:
        """
        Promotes a ticker based on an incoming event (e.g. Alert or significant print).
        """
        raw_ticker = event.payload.get("ticker") or event.ticker
        if not raw_ticker:
            return

        # Extract underlying ticker from option symbols (e.g., IWM260220P00236000 -> IWM)
        ticker = self._extract_underlying_ticker(raw_ticker)
        if not ticker:
            return

        # Simple logic: Any alert or dark pool print bumps the ticker activity
        now = time.time()
        self.active_tickers[ticker] = now

        if getattr(event, "event_type", None) in {"UW_ALERT", "UW_FLOW_ALERT"}:
            self.alert_tickers[ticker] = now

        # Parse Expiry if present (UW Flow/Alerts)
        expiry_str = event.payload.get("expiry")
        if expiry_str:
            try:
                # Expect YYYY-MM-DD
                exp_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
                current_exp = self.expiry_tickers.get(ticker)

                if not current_exp or exp_date > current_exp:
                    self.expiry_tickers[ticker] = exp_date
            except ValueError:
                pass  # Invalid date format, ignore

    def _extract_underlying_ticker(self, symbol: str) -> str:
        """
        Extracts the underlying stock ticker from an option symbol.

        Option symbols: AAPL240119C00150000 -> AAPL
        Stock symbols: AAPL -> AAPL
        """
        import re

        if not symbol:
            return ""

        # Option symbol pattern: <TICKER><YYMMDD><C|P><STRIKE>
        # Examples: AAPL240119C00150000, IWM260220P00236000, SPXW250103C05850000
        match = re.match(r"^([A-Z]+)\d{6}[CP]\d+$", symbol)
        if match:
            return match.group(1)

        # If it doesn't match option pattern, assume it's already a stock ticker
        # But validate it doesn't contain numbers in unexpected positions
        if re.match(r"^[A-Z]{1,5}$", symbol):
            return symbol

        # For symbols like SPXW, BRK.A, etc. - strip trailing special chars
        clean = re.match(r"^([A-Z]+)", symbol)
        return clean.group(1) if clean else symbol

    def cleanup(self, ttl_seconds: int = UNIVERSE_TTL_SECONDS, alert_ttl_seconds: int | None = None) -> None:
        """
        Removes tickers that haven't been active within the TTL window.
        Respects Option Expiry dates (keeps ticker active if expiry >= today).
        """
        if alert_ttl_seconds is None:
            # Alerts are explicitly called out in the PRD as a driver of the universe;
            # keep them around longer than generic activity by default.
            alert_ttl_seconds = ttl_seconds * 2

        now = time.time()
        today = datetime.now(UTC).date()

        expired_activity: list[str] = []
        for ticker, last_ts in self.active_tickers.items():
            # Expiry Check Override
            if ticker in self.expiry_tickers:
                exp = self.expiry_tickers[ticker]
                if exp >= today:
                    continue  # Still valid, do not expire logic

            if now - last_ts > ttl_seconds:
                expired_activity.append(ticker)

        for t in expired_activity:
            del self.active_tickers[t]

        expired_alerts: list[str] = []
        for ticker, last_ts in self.alert_tickers.items():
            # Expiry Check Override
            if ticker in self.expiry_tickers:
                exp = self.expiry_tickers[ticker]
                if exp >= today:
                    continue  # Still valid

            if now - last_ts > alert_ttl_seconds:
                expired_alerts.append(ticker)

        for t in expired_alerts:
            del self.alert_tickers[t]

        # Cleanup expiry_tickers map itself (remove past dates)
        expired_expiry_keys = [t for t, d in self.expiry_tickers.items() if d < today]
        for t in expired_expiry_keys:
            del self.expiry_tickers[t]

        if expired_activity or expired_alerts or expired_expiry_keys:
            logger.info(
                "Cleaned up tickers from universe: activity=%s alerts=%s expired_contexts=%s",
                len(expired_activity),
                len(expired_alerts),
                len(expired_expiry_keys),
            )

    def get_active_universe(self) -> list[str]:
        """
        Returns the unique union of static and active dynamic tickers.
        """
        # Union of sets
        combined = (
            self.static_universe.union(self.held_tickers)
            .union(self.active_tickers.keys())
            .union(self.alert_tickers.keys())
        )
        return list(combined)
