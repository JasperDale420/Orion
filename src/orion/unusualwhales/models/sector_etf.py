from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SectorETF")


@_attrs_define
class SectorETF:
    """
    Example:
        {'avg30_call_volume': '3636459.000000000000', 'avg30_put_volume': '4796289.166666666667', 'avg30_stock_volume':
            '74402355', 'avg_30_day_call_volume': '3636459.000000000000', 'avg_30_day_put_volume': '4796289.166666666667',
            'avg_7_day_call_volume': '3343061.285714285714', 'avg_7_day_put_volume': '4521616.428571428571',
            'bearish_premium': '258905527', 'bullish_premium': '238729761', 'call_premium': '293824502', 'call_volume':
            1844830, 'full_name': 'S&P 500 Index', 'high': '447.11', 'last': '446.15', 'low': '444.8', 'marketcap':
            '406517275500', 'open': '444.93', 'prev_close': '444.85', 'prev_date': datetime.date(2023, 9, 7), 'put_premium':
            '244159205', 'put_volume': 2009005, 'ticker': 'SPY', 'volume': 23132119, 'week52_high': '459.44', 'week52_low':
            '342.65'}

    Attributes:
        avg30_stock_volume (Union[Unset, str]): Avg 30 day stock volume. Example: 74402355.
        avg_30_day_call_volume (Union[Unset, str]): Avg 30 day call volume. Example: 679430.000000000000.
        avg_30_day_put_volume (Union[Unset, str]): Avg 30 day put volume. Example: 401961.285714285714.
        avg_7_day_call_volume (Union[Unset, str]): Avg 7 day call volume. Example: 679145.333333333333.
        avg_7_day_put_volume (Union[Unset, str]): Avg 7 day put volume. Example: 388676.000000000000.
        bearish_premium (Union[Unset, str]): The bearish premium is defined as (call premium bid side) + (put premium
            ask side). Example: 143198625.
        bullish_premium (Union[Unset, str]): The bullish premium is defined as (call premium ask side) + (put premium
            bid side). Example: 196261414.
        call_premium (Union[Unset, str]): The sum of the premium of all the call transactions that executed. Example:
            9908777.0.
        call_volume (Union[Unset, int]): The sum of the size of all the call transactions that executed. Example:
            990943.
        close (Union[Unset, str]): The closing price of the candle. Example: 56.79.
        full_name (Union[Unset, str]): The name/sector of the SPDR sector ETF. Example: S&P 500 Index.
        high (Union[Unset, str]): The highest price of the candle. Example: 58.12.
        low (Union[Unset, str]): The lowest price of the candle. Example: 51.90.
        marketcap (Union[Unset, str]): The marketcap of the underlying ticker. If the issue type of the ticker is ETF
            then the marketcap represents the AUM. Example: 2965813810400.
        open_ (Union[Unset, str]): The opening price of the candle. Example: 54.29.
        prev_close (Union[Unset, str]): The previous Trading Day's stock price of the ticker. Example: 189.70.
        prev_date (Union[Unset, str]): The date of the previous Trading Day in ISO format. Example: 2023-09-07.
        put_premium (Union[Unset, str]): The sum of the premium of all the put transactions that executed. Example:
            163537151.
        put_volume (Union[Unset, int]): The sum of the size of all the put transactions that executed. Example: 808326.
        ticker (Union[Unset, str]): The stock ticker. Example: AAPL.
        volume (Union[Unset, int]): The volume of the ticker for the Trading Day. Example: 23132119.
        week_52_high (Union[Unset, str]): The 52 week high stock price of the ticker. Example: 198.23.
        week_52_low (Union[Unset, str]): The 52 week low stock price of the ticker. Example: 124.17.
    """

    avg30_stock_volume: Union[Unset, str] = UNSET
    avg_30_day_call_volume: Union[Unset, str] = UNSET
    avg_30_day_put_volume: Union[Unset, str] = UNSET
    avg_7_day_call_volume: Union[Unset, str] = UNSET
    avg_7_day_put_volume: Union[Unset, str] = UNSET
    bearish_premium: Union[Unset, str] = UNSET
    bullish_premium: Union[Unset, str] = UNSET
    call_premium: Union[Unset, str] = UNSET
    call_volume: Union[Unset, int] = UNSET
    close: Union[Unset, str] = UNSET
    full_name: Union[Unset, str] = UNSET
    high: Union[Unset, str] = UNSET
    low: Union[Unset, str] = UNSET
    marketcap: Union[Unset, str] = UNSET
    open_: Union[Unset, str] = UNSET
    prev_close: Union[Unset, str] = UNSET
    prev_date: Union[Unset, str] = UNSET
    put_premium: Union[Unset, str] = UNSET
    put_volume: Union[Unset, int] = UNSET
    ticker: Union[Unset, str] = UNSET
    volume: Union[Unset, int] = UNSET
    week_52_high: Union[Unset, str] = UNSET
    week_52_low: Union[Unset, str] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        avg30_stock_volume = self.avg30_stock_volume

        avg_30_day_call_volume = self.avg_30_day_call_volume

        avg_30_day_put_volume = self.avg_30_day_put_volume

        avg_7_day_call_volume = self.avg_7_day_call_volume

        avg_7_day_put_volume = self.avg_7_day_put_volume

        bearish_premium = self.bearish_premium

        bullish_premium = self.bullish_premium

        call_premium = self.call_premium

        call_volume = self.call_volume

        close = self.close

        full_name = self.full_name

        high = self.high

        low = self.low

        marketcap = self.marketcap

        open_ = self.open_

        prev_close = self.prev_close

        prev_date = self.prev_date

        put_premium = self.put_premium

        put_volume = self.put_volume

        ticker = self.ticker

        volume = self.volume

        week_52_high = self.week_52_high

        week_52_low = self.week_52_low

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if avg30_stock_volume is not UNSET:
            field_dict["avg30_stock_volume"] = avg30_stock_volume
        if avg_30_day_call_volume is not UNSET:
            field_dict["avg_30_day_call_volume"] = avg_30_day_call_volume
        if avg_30_day_put_volume is not UNSET:
            field_dict["avg_30_day_put_volume"] = avg_30_day_put_volume
        if avg_7_day_call_volume is not UNSET:
            field_dict["avg_7_day_call_volume"] = avg_7_day_call_volume
        if avg_7_day_put_volume is not UNSET:
            field_dict["avg_7_day_put_volume"] = avg_7_day_put_volume
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
        if full_name is not UNSET:
            field_dict["full_name"] = full_name
        if high is not UNSET:
            field_dict["high"] = high
        if low is not UNSET:
            field_dict["low"] = low
        if marketcap is not UNSET:
            field_dict["marketcap"] = marketcap
        if open_ is not UNSET:
            field_dict["open"] = open_
        if prev_close is not UNSET:
            field_dict["prev_close"] = prev_close
        if prev_date is not UNSET:
            field_dict["prev_date"] = prev_date
        if put_premium is not UNSET:
            field_dict["put_premium"] = put_premium
        if put_volume is not UNSET:
            field_dict["put_volume"] = put_volume
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
        avg30_stock_volume = d.pop("avg30_stock_volume", UNSET)

        avg_30_day_call_volume = d.pop("avg_30_day_call_volume", UNSET)

        avg_30_day_put_volume = d.pop("avg_30_day_put_volume", UNSET)

        avg_7_day_call_volume = d.pop("avg_7_day_call_volume", UNSET)

        avg_7_day_put_volume = d.pop("avg_7_day_put_volume", UNSET)

        bearish_premium = d.pop("bearish_premium", UNSET)

        bullish_premium = d.pop("bullish_premium", UNSET)

        call_premium = d.pop("call_premium", UNSET)

        call_volume = d.pop("call_volume", UNSET)

        close = d.pop("close", UNSET)

        full_name = d.pop("full_name", UNSET)

        high = d.pop("high", UNSET)

        low = d.pop("low", UNSET)

        marketcap = d.pop("marketcap", UNSET)

        open_ = d.pop("open", UNSET)

        prev_close = d.pop("prev_close", UNSET)

        prev_date = d.pop("prev_date", UNSET)

        put_premium = d.pop("put_premium", UNSET)

        put_volume = d.pop("put_volume", UNSET)

        ticker = d.pop("ticker", UNSET)

        volume = d.pop("volume", UNSET)

        week_52_high = d.pop("week_52_high", UNSET)

        week_52_low = d.pop("week_52_low", UNSET)

        sector_etf = cls(
            avg30_stock_volume=avg30_stock_volume,
            avg_30_day_call_volume=avg_30_day_call_volume,
            avg_30_day_put_volume=avg_30_day_put_volume,
            avg_7_day_call_volume=avg_7_day_call_volume,
            avg_7_day_put_volume=avg_7_day_put_volume,
            bearish_premium=bearish_premium,
            bullish_premium=bullish_premium,
            call_premium=call_premium,
            call_volume=call_volume,
            close=close,
            full_name=full_name,
            high=high,
            low=low,
            marketcap=marketcap,
            open_=open_,
            prev_close=prev_close,
            prev_date=prev_date,
            put_premium=put_premium,
            put_volume=put_volume,
            ticker=ticker,
            volume=volume,
            week_52_high=week_52_high,
            week_52_low=week_52_low,
        )

        sector_etf.additional_properties = d
        return sector_etf

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
