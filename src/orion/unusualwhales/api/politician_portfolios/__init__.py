import types

from . import (
    get_holders_by_ticker,
    get_politicians_list,
    get_portfolio,
    get_recent_trades,
)


class PoliticianPortfoliosEndpoints:
    @classmethod
    def get_holders_by_ticker(cls) -> types.ModuleType:
        """
        Politician Portfolio Holders by Ticker
        """
        return get_holders_by_ticker

    @classmethod
    def get_politicians_list(cls) -> types.ModuleType:
        """
        Politicians List
        """
        return get_politicians_list

    @classmethod
    def get_portfolio(cls) -> types.ModuleType:
        """
        Politician Portfolios
        """
        return get_portfolio

    @classmethod
    def get_recent_trades(cls) -> types.ModuleType:
        """
        Politician Trades
        """
        return get_recent_trades
