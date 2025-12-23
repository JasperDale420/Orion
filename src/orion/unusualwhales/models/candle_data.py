from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.market_general_market_time import MarketGeneralMarketTime
from ..types import UNSET, Unset

T = TypeVar("T", bound="CandleData")


@_attrs_define
class CandleData:
    """The candle data for the requested timeframe.

    Example:
        {'data': [{'close': '56.79', 'end_time': datetime.datetime(2023, 9, 7, 20, 11, tzinfo=datetime.timezone.utc),
            'high': '56.79', 'low': '56.79', 'market_time': 'po', 'open': '56.79', 'start_time': datetime.datetime(2023, 9,
            7, 20, 10, tzinfo=datetime.timezone.utc), 'total_volume': 13774488, 'volume': 29812}, {'close': '56.79',
            'end_time': datetime.datetime(2023, 9, 7, 20, 7, tzinfo=datetime.timezone.utc), 'high': '56.79', 'low': '56.79',
            'market_time': 'po', 'open': '56.79', 'start_time': datetime.datetime(2023, 9, 7, 20, 6,
            tzinfo=datetime.timezone.utc), 'total_volume': 13744676, 'volume': 10699}]}

    Attributes:
        close (Union[Unset, str]): The closing price of the candle. Example: 56.79.
        end_time (Union[Unset, str]): The end time of the candle as UTC timestamp. Example: 2023-09-07 20:07:00+00:00.
        high (Union[Unset, str]): The highest price of the candle. Example: 58.12.
        low (Union[Unset, str]): The lowest price of the candle. Example: 51.90.
        market_time (Union[Unset, MarketGeneralMarketTime]): The market time:
            - pr = premarket
            - r  = regular
            - po = postmarket
             Example: pr.
        open_ (Union[Unset, str]): The opening price of the candle. Example: 54.29.
        start_time (Union[Unset, str]): The start time of the candle as UTC timestamp. Example: 2023-09-07
            20:06:00+00:00.
        total_volume (Union[Unset, int]): The total volume of the ticker for the full trading till now. Example:
            13744676.
        volume (Union[Unset, int]): The volume of the candle. Example: 10699.
    """

    close: Union[Unset, str] = UNSET
    end_time: Union[Unset, str] = UNSET
    high: Union[Unset, str] = UNSET
    low: Union[Unset, str] = UNSET
    market_time: Union[Unset, MarketGeneralMarketTime] = UNSET
    open_: Union[Unset, str] = UNSET
    start_time: Union[Unset, str] = UNSET
    total_volume: Union[Unset, int] = UNSET
    volume: Union[Unset, int] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        close = self.close
        end_time = self.end_time
        high = self.high
        low = self.low
        market_time: Union[Unset, str] = UNSET
        if not isinstance(self.market_time, Unset):
            market_time = self.market_time.value
        open_ = self.open_
        start_time = self.start_time
        total_volume = self.total_volume
        volume = self.volume
        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if close is not UNSET:
            field_dict["close"] = close
        if end_time is not UNSET:
            field_dict["end_time"] = end_time
        if high is not UNSET:
            field_dict["high"] = high
        if low is not UNSET:
            field_dict["low"] = low
        if market_time is not UNSET:
            field_dict["market_time"] = market_time
        if open_ is not UNSET:
            field_dict["open"] = open_
        if start_time is not UNSET:
            field_dict["start_time"] = start_time
        if total_volume is not UNSET:
            field_dict["total_volume"] = total_volume
        if volume is not UNSET:
            field_dict["volume"] = volume
        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        close = d.pop("close", UNSET)

        end_time = d.pop("end_time", UNSET)

        high = d.pop("high", UNSET)

        low = d.pop("low", UNSET)

        _market_time = d.pop("market_time", UNSET)
        market_time: Union[Unset, MarketGeneralMarketTime]
        if isinstance(_market_time, Unset):
            market_time = UNSET
        else:
            market_time = MarketGeneralMarketTime(_market_time)

        open_ = d.pop("open", UNSET)

        start_time = d.pop("start_time", UNSET)

        total_volume = d.pop("total_volume", UNSET)

        volume = d.pop("volume", UNSET)

        candle_data = cls(
            close=close,
            end_time=end_time,
            high=high,
            low=low,
            market_time=market_time,
            open_=open_,
            start_time=start_time,
            total_volume=total_volume,
            volume=volume,
        )

        candle_data.additional_properties = d
        return candle_data

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
