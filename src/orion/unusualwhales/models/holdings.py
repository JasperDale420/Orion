from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.market_general_sector import MarketGeneralSector
from ..types import UNSET, Unset

T = TypeVar("T", bound="Holdings")


@_attrs_define
class Holdings:
    """An object containing information about the holdings of an ETF

    Example:
        {'avg30_volume': '52433648', 'bearish_premium': '32565174', 'bullish_premium': '22987045', 'call_premium':
            '45254976', 'call_volume': 197685, 'close': '194.84', 'has_options': True, 'high': '196.579', 'low': '194.41',
            'name': 'APPLE INC', 'prev_price': '197.14', 'put_premium': '16338631', 'put_volume': 106773, 'sector':
            'Technology', 'shares': 169938760, 'short_name': 'APPLE', 'ticker': 'AAPL', 'type': 'stock', 'volume': 12314310,
            'week52_high': '199.62', 'week52_low': '123.15', 'weight': '7.335'}

    Attributes:
        avg30_volume (Union[Unset, str]): The avg stock volume for the stock last 30 days. Example: 55973002.
        bearish_premium (Union[Unset, str]): The bearish premium is defined as (call premium bid side) + (put premium
            ask side). Example: 143198625.
        bullish_premium (Union[Unset, str]): The bullish premium is defined as (call premium ask side) + (put premium
            bid side). Example: 196261414.
        call_premium (Union[Unset, str]): The sum of the premium of all the call transactions that executed. Example:
            9908777.0.
        call_volume (Union[Unset, int]): The sum of the size of all the call transactions that executed. Example:
            990943.
        close (Union[Unset, str]): The closing price of the candle. Example: 56.79.
        has_options (Union[Unset, bool]): Boolean flag whether the company has options. Example: True.
        high (Union[Unset, str]): The highest price of the candle. Example: 58.12.
        low (Union[Unset, str]): The lowest price of the candle. Example: 51.90.
        name (Union[Unset, str]): The name of the company. Example: APPLE INC.
        open_ (Union[Unset, str]): The opening price of the candle. Example: 54.29.
        prev_price (Union[Unset, str]): The previous Trading Day's stock price of the ticker. Example: 189.70.
        put_premium (Union[Unset, str]): The sum of the premium of all the put transactions that executed. Example:
            163537151.
        put_volume (Union[Unset, int]): The sum of the size of all the put transactions that executed. Example: 808326.
        sector (Union[Unset, MarketGeneralSector]): The financial sector of the ticker. Empty if unknown or not
            applicable such as ETF/Index. Example: Technology.
        ticker (Union[Unset, str]): The stock ticker. Example: AAPL.
        volume (Union[Unset, int]): The volume of the ticker for the Trading Day. Example: 23132119.
        week_52_high (Union[Unset, str]): The 52 week high stock price of the ticker. Example: 198.23.
        week_52_low (Union[Unset, str]): The 52 week low stock price of the ticker. Example: 124.17.
    """

    avg30_volume: Union[Unset, str] = UNSET
    bearish_premium: Union[Unset, str] = UNSET
    bullish_premium: Union[Unset, str] = UNSET
    call_premium: Union[Unset, str] = UNSET
    call_volume: Union[Unset, int] = UNSET
    close: Union[Unset, str] = UNSET
    has_options: Union[Unset, bool] = UNSET
    high: Union[Unset, str] = UNSET
    low: Union[Unset, str] = UNSET
    name: Union[Unset, str] = UNSET
    open_: Union[Unset, str] = UNSET
    prev_price: Union[Unset, str] = UNSET
    put_premium: Union[Unset, str] = UNSET
    put_volume: Union[Unset, int] = UNSET
    sector: Union[Unset, MarketGeneralSector] = UNSET
    ticker: Union[Unset, str] = UNSET
    volume: Union[Unset, int] = UNSET
    week_52_high: Union[Unset, str] = UNSET
    week_52_low: Union[Unset, str] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        avg30_volume = self.avg30_volume

        bearish_premium = self.bearish_premium

        bullish_premium = self.bullish_premium

        call_premium = self.call_premium

        call_volume = self.call_volume

        close = self.close

        has_options = self.has_options

        high = self.high

        low = self.low

        name = self.name

        open_ = self.open_

        prev_price = self.prev_price

        put_premium = self.put_premium

        put_volume = self.put_volume

        sector: Union[Unset, str] = UNSET
        if not isinstance(self.sector, Unset):
            sector = self.sector.value

        ticker = self.ticker

        volume = self.volume

        week_52_high = self.week_52_high

        week_52_low = self.week_52_low

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if avg30_volume is not UNSET:
            field_dict["avg30_volume"] = avg30_volume
        if bearish_premium is not UNSET:
            field_dict["bearish_premium"] = bearish_premium
        if bullish_premium is not UNSET:
            field_dict["bullish_premium"] = bullish_premium
        if call_premium is not UNSET:
            field_dict["call_premium"] = call_premium
        if call_volume is not UNSET:
            field_dict["call_volume"] = call_volume
        if close is not UNSET:
            field_dict["close"] = close
        if has_options is not UNSET:
            field_dict["has_options"] = has_options
        if high is not UNSET:
            field_dict["high"] = high
        if low is not UNSET:
            field_dict["low"] = low
        if name is not UNSET:
            field_dict["name"] = name
        if open_ is not UNSET:
            field_dict["open"] = open_
        if prev_price is not UNSET:
            field_dict["prev_price"] = prev_price
        if put_premium is not UNSET:
            field_dict["put_premium"] = put_premium
        if put_volume is not UNSET:
            field_dict["put_volume"] = put_volume
        if sector is not UNSET:
            field_dict["sector"] = sector
        if ticker is not UNSET:
            field_dict["ticker"] = ticker
        if volume is not UNSET:
            field_dict["volume"] = volume
        if week_52_high is not UNSET:
            field_dict["week_52_high"] = week_52_high
        if week_52_low is not UNSET:
            field_dict["week_52_low"] = week_52_low

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        avg30_volume = d.pop("avg30_volume", UNSET)

        bearish_premium = d.pop("bearish_premium", UNSET)

        bullish_premium = d.pop("bullish_premium", UNSET)

        call_premium = d.pop("call_premium", UNSET)

        call_volume = d.pop("call_volume", UNSET)

        close = d.pop("close", UNSET)

        has_options = d.pop("has_options", UNSET)

        high = d.pop("high", UNSET)

        low = d.pop("low", UNSET)

        name = d.pop("name", UNSET)

        open_ = d.pop("open", UNSET)

        prev_price = d.pop("prev_price", UNSET)

        put_premium = d.pop("put_premium", UNSET)

        put_volume = d.pop("put_volume", UNSET)

        _sector = d.pop("sector", UNSET)
        sector: Union[Unset, MarketGeneralSector]
        if isinstance(_sector, Unset):
            sector = UNSET
        else:
            sector = MarketGeneralSector(_sector)

        ticker = d.pop("ticker", UNSET)

        volume = d.pop("volume", UNSET)

        week_52_high = d.pop("week_52_high", UNSET)

        week_52_low = d.pop("week_52_low", UNSET)

        holdings = cls(
            avg30_volume=avg30_volume,
            bearish_premium=bearish_premium,
            bullish_premium=bullish_premium,
            call_premium=call_premium,
            call_volume=call_volume,
            close=close,
            has_options=has_options,
            high=high,
            low=low,
            name=name,
            open_=open_,
            prev_price=prev_price,
            put_premium=put_premium,
            put_volume=put_volume,
            sector=sector,
            ticker=ticker,
            volume=volume,
            week_52_high=week_52_high,
            week_52_low=week_52_low,
        )

        holdings.additional_properties = d
        return holdings

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
