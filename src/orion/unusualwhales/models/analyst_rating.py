from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.analyst_field_action import AnalystFieldAction
from ..models.analyst_field_recommendation import AnalystFieldRecommendation
from ..models.market_general_sector import MarketGeneralSector
from ..types import UNSET, Unset

T = TypeVar("T", bound="AnalystRating")


@_attrs_define
class AnalystRating:
    """A rating of an analyst.

    Example:
        {'data': [{'action': 'maintained', 'analyst_name': 'Tyler Radke', 'firm': 'Citi', 'rating': '3.5',
            'recommendation': 'Buy', 'sector': 'Technology', 'target': '420.0', 'ticker': 'MSFT', 'timestamp':
            datetime.datetime(2023, 9, 11, 11, 21, 12, tzinfo=datetime.timezone.utc), 'title': "Citi opens 'positive
            catalyst watch' on Microsoft into 'catalyst path'"}, {'action': 'maintained', 'analyst_name': 'Mark Rothschild',
            'firm': 'Canaccord Genuity', 'rating': '4.91', 'recommendation': 'Hold', 'sector': 'Conglomerates', 'target':
            '11.75', 'ticker': 'DRETF', 'timestamp': datetime.datetime(2023, 9, 11, 11, 11, 32,
            tzinfo=datetime.timezone.utc), 'title': 'Canaccord Genuity Keeps Their Hold Rating on Dream Office Real Estate
            Investment (DRETF)'}]}

    Attributes:
        action (Union[Unset, AnalystFieldAction]): The action of the recommendation. Example: upgraded.
        analyst_name (Union[Unset, str]): The name of the analyst. Example: David Vogt.
        firm (Union[Unset, str]): The firm the analyst is working for. Example: UBS.
        rating (Union[Unset, float]): The rating of the analyst. Example: 2.44.
        recommendation (Union[Unset, AnalystFieldRecommendation]): The recommendation the analyst gave out. Example:
            Buy.
        sector (Union[Unset, MarketGeneralSector]): The financial sector of the ticker. Empty if unknown or not
            applicable such as ETF/Index. Example: Technology.
        target (Union[Unset, str]): The target price of the rating.
        ticker (Union[Unset, str]): The stock ticker. Example: AAPL.
        timestamp (Union[Unset, str]): The UTC timestamp as a string, when the rating was released. Example: 2023-09-11
            11:21:12+00:00.
        title (Union[Unset, str]): The news title of the rating that went out. Example: China news could create demand
            headwind for Apple in Dec. quarter, says UBS.
    """

    action: Union[Unset, AnalystFieldAction] = UNSET
    analyst_name: Union[Unset, str] = UNSET
    firm: Union[Unset, str] = UNSET
    rating: Union[Unset, float] = UNSET
    recommendation: Union[Unset, AnalystFieldRecommendation] = UNSET
    sector: Union[Unset, MarketGeneralSector] = UNSET
    target: Union[Unset, str] = UNSET
    ticker: Union[Unset, str] = UNSET
    timestamp: Union[Unset, str] = UNSET
    title: Union[Unset, str] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        action: Union[Unset, str] = UNSET
        if not isinstance(self.action, Unset):
            action = self.action.value

        analyst_name = self.analyst_name

        firm = self.firm

        rating = self.rating

        recommendation: Union[Unset, str] = UNSET
        if not isinstance(self.recommendation, Unset):
            recommendation = self.recommendation.value

        sector: Union[Unset, str] = UNSET
        if not isinstance(self.sector, Unset):
            sector = self.sector.value

        target = self.target

        ticker = self.ticker

        timestamp = self.timestamp

        title = self.title

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if action is not UNSET:
            field_dict["action"] = action
        if analyst_name is not UNSET:
            field_dict["analyst_name"] = analyst_name
        if firm is not UNSET:
            field_dict["firm"] = firm
        if rating is not UNSET:
            field_dict["rating"] = rating
        if recommendation is not UNSET:
            field_dict["recommendation"] = recommendation
        if sector is not UNSET:
            field_dict["sector"] = sector
        if target is not UNSET:
            field_dict["target"] = target
        if ticker is not UNSET:
            field_dict["ticker"] = ticker
        if timestamp is not UNSET:
            field_dict["timestamp"] = timestamp
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        _action = d.pop("action", UNSET)
        action: Union[Unset, AnalystFieldAction]
        if isinstance(_action, Unset):
            action = UNSET
        else:
            action = AnalystFieldAction(_action)

        analyst_name = d.pop("analyst_name", UNSET)

        firm = d.pop("firm", UNSET)

        rating = d.pop("rating", UNSET)

        _recommendation = d.pop("recommendation", UNSET)
        recommendation: Union[Unset, AnalystFieldRecommendation]
        if isinstance(_recommendation, Unset):
            recommendation = UNSET
        else:
            recommendation = AnalystFieldRecommendation(_recommendation)

        _sector = d.pop("sector", UNSET)
        sector: Union[Unset, MarketGeneralSector]
        if isinstance(_sector, Unset):
            sector = UNSET
        else:
            sector = MarketGeneralSector(_sector)

        target = d.pop("target", UNSET)

        ticker = d.pop("ticker", UNSET)

        timestamp = d.pop("timestamp", UNSET)

        title = d.pop("title", UNSET)

        analyst_rating = cls(
            action=action,
            analyst_name=analyst_name,
            firm=firm,
            rating=rating,
            recommendation=recommendation,
            sector=sector,
            target=target,
            ticker=ticker,
            timestamp=timestamp,
            title=title,
        )

        analyst_rating.additional_properties = d
        return analyst_rating

    @property
    def additional_keys(self) -> List[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
