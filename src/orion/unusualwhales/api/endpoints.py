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
    def alerts(cls) -> type[AlertsEndpoints]:
        return AlertsEndpoints

    @classmethod
    def congress(cls) -> type[CongressEndpoints]:
        return CongressEndpoints

    @classmethod
    def darkpool(cls) -> type[DarkpoolEndpoints]:
        return DarkpoolEndpoints

    @classmethod
    def earnings(cls) -> type[EarningsEndpoints]:
        return EarningsEndpoints

    @classmethod
    def etfs(cls) -> type[EtfsEndpoints]:
        return EtfsEndpoints

    @classmethod
    def market(cls) -> type[MarketEndpoints]:
        return MarketEndpoints

    @classmethod
    def flow(cls) -> type[FlowEndpoints]:
        return FlowEndpoints

    @classmethod
    def contract(cls) -> type[ContractEndpoints]:
        return ContractEndpoints

    @classmethod
    def screener(cls) -> type[ScreenerEndpoints]:
        return ScreenerEndpoints

    @classmethod
    def seasonality(cls) -> type[SeasonalityEndpoints]:
        return SeasonalityEndpoints

    @classmethod
    def stock(cls) -> type[StockEndpoints]:
        return StockEndpoints

    @classmethod
    def group_flow(cls) -> type[GroupFlowEndpoints]:
        return GroupFlowEndpoints

    @classmethod
    def institutions(cls) -> type[InstitutionsEndpoints]:
        return InstitutionsEndpoints

    @classmethod
    def net_flow(cls) -> type[NetFlowEndpoints]:
        return NetFlowEndpoints

    @classmethod
    def news(cls) -> type[NewsEndpoints]:
        return NewsEndpoints

    @classmethod
    def politician_portfolios(cls) -> type[PoliticianPortfoliosEndpoints]:
        return PoliticianPortfoliosEndpoints

    @classmethod
    def shorts(cls) -> type[ShortsEndpoints]:
        return ShortsEndpoints
