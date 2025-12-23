from typing import Type

from .alerts import AlertsEndpoints
from .congress import CongressEndpoints
from .contract import ContractEndpoints
from .darkpool import DarkpoolEndpoints
from .earnings import EarningsEndpoints
from .etfs import EtfsEndpoints
from .flow import FlowEndpoints
from .group_flow import GroupFlowEndpoints
from .institutions import InstitutionsEndpoints
from .market import MarketEndpoints
from .net_flow import NetFlowEndpoints
from .news import NewsEndpoints
from .politician_portfolios import PoliticianPortfoliosEndpoints
from .screener import ScreenerEndpoints
from .seasonality import SeasonalityEndpoints
from .shorts import ShortsEndpoints
from .stock import StockEndpoints


class Endpoints:
    @classmethod
    def alerts(cls) -> Type[AlertsEndpoints]:
        return AlertsEndpoints

    @classmethod
    def congress(cls) -> Type[CongressEndpoints]:
        return CongressEndpoints

    @classmethod
    def darkpool(cls) -> Type[DarkpoolEndpoints]:
        return DarkpoolEndpoints

    @classmethod
    def earnings(cls) -> Type[EarningsEndpoints]:
        return EarningsEndpoints

    @classmethod
    def etfs(cls) -> Type[EtfsEndpoints]:
        return EtfsEndpoints

    @classmethod
    def market(cls) -> Type[MarketEndpoints]:
        return MarketEndpoints

    @classmethod
    def flow(cls) -> Type[FlowEndpoints]:
        return FlowEndpoints

    @classmethod
    def contract(cls) -> Type[ContractEndpoints]:
        return ContractEndpoints

    @classmethod
    def screener(cls) -> Type[ScreenerEndpoints]:
        return ScreenerEndpoints

    @classmethod
    def seasonality(cls) -> Type[SeasonalityEndpoints]:
        return SeasonalityEndpoints

    @classmethod
    def stock(cls) -> Type[StockEndpoints]:
        return StockEndpoints

    @classmethod
    def group_flow(cls) -> Type[GroupFlowEndpoints]:
        return GroupFlowEndpoints

    @classmethod
    def institutions(cls) -> Type[InstitutionsEndpoints]:
        return InstitutionsEndpoints

    @classmethod
    def net_flow(cls) -> Type[NetFlowEndpoints]:
        return NetFlowEndpoints

    @classmethod
    def news(cls) -> Type[NewsEndpoints]:
        return NewsEndpoints

    @classmethod
    def politician_portfolios(cls) -> Type[PoliticianPortfoliosEndpoints]:
        return PoliticianPortfoliosEndpoints

    @classmethod
    def shorts(cls) -> Type[ShortsEndpoints]:
        return ShortsEndpoints
