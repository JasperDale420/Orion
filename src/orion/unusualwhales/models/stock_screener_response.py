import datetime
from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.market_general_sector import MarketGeneralSector
from ..models.stock_earnings_time import StockEarningsTime
from ..models.stock_issue_type import StockIssueType
from ..types import UNSET, Unset

T = TypeVar("T", bound="StockScreenerResponse")


@_attrs_define
class StockScreenerResponse:
    """Response schema for stock screener requests.

    Example:
        {'data': [{'avg_30_day_call_volume': '606258.533333333333', 'avg_30_day_put_volume': '436126.833333333333',
            'avg_3_day_call_volume': '679145.333333333333', 'avg_3_day_put_volume': '388676.000000000000',
            'avg_7_day_call_volume': '679430.000000000000', 'avg_7_day_put_volume': '401961.285714285714',
            'bearish_premium': '196261414', 'bullish_premium': '143198625', 'call_open_interest': 3975333, 'call_premium':
            '222941665', 'call_volume': 990943, 'call_volume_ask_side': 417251, 'call_volume_bid_side': 498271, 'close':
            '182.91', 'date': datetime.date(2023, 9, 6), 'er_time': 'unkown', 'implied_move': '2.2398043036460877',
            'implied_move_perc': '0.012247398860706955', 'is_index': False, 'issue_type': 'Common Stock', 'iv30d':
            '0.2038053572177887', 'iv30d_1d': '0.18791808187961578', 'iv30d_1m': '0.2136848270893097', 'iv30d_1w':
            '0.18398597836494446', 'iv_rank': '13.52369891956068210400', 'marketcap': '2965813810400', 'net_call_premium':
            '-29138464', 'net_put_premium': '23924325', 'next_dividend_date': None, 'next_earnings_date':
            datetime.date(2023, 10, 26), 'prev_call_oi': 3994750, 'prev_close': '189.70', 'prev_put_oi': 3679410,
            'put_call_ratio': '0.815713920982337', 'put_open_interest': 3564153, 'put_premium': '163537151', 'put_volume':
            808326, 'put_volume_ask_side': 431791, 'put_volume_bid_side': 314160, 'sector': 'Technology', 'ticker': 'AAPL',
            'total_open_interest': 7539486, 'volatility': '0.18338055163621902', 'week_52_high': '198.23', 'week_52_low':
            '124.17'}]}

    Attributes:
        avg_30_day_call_volume (Union[Unset, str]): Avg 30 day call volume. Example: 679430.000000000000.
        avg_30_day_put_volume (Union[Unset, str]): Avg 30 day put volume. Example: 401961.285714285714.
        avg_3_day_call_volume (Union[Unset, str]): Avg 3 day call volume. Example: 606258.533333333333.
        avg_3_day_put_volume (Union[Unset, str]): Avg 3 day put volume. Example: 436126.833333333333.
        avg_7_day_call_volume (Union[Unset, str]): Avg 7 day call volume. Example: 679145.333333333333.
        avg_7_day_put_volume (Union[Unset, str]): Avg 7 day put volume. Example: 388676.000000000000.
        bearish_premium (Union[Unset, str]): The bearish premium is defined as (call premium bid side) + (put premium
            ask side). Example: 143198625.
        bullish_premium (Union[Unset, str]): The bullish premium is defined as (call premium ask side) + (put premium
            bid side). Example: 196261414.
        call_open_interest (Union[Unset, int]): The sum of open interest of all the call options. Example: 3975333.
        call_premium (Union[Unset, str]): The sum of the premium of all the call transactions that executed. Example:
            9908777.0.
        call_volume (Union[Unset, int]): The sum of the size of all the call transactions that executed. Example:
            990943.
        call_volume_ask_side (Union[Unset, int]): The sum of the size of all the call transactions that executed on the
            ask side. Example: 417251.
        call_volume_bid_side (Union[Unset, int]): The sum of the size of all the call transactions that executed on the
            bid side. Example: 498271.
        close (Union[Unset, str]): The latest stock price of the ticker. Example: 182.91.
        er_time (Union[Unset, StockEarningsTime]): The time when the earnings will be released. Example: premarket.
        implied_move (Union[Unset, str]): The implied move of the underlying stock by a given date based on the money
            option contracts. It is calculated by multiplying the sum of the call and put price by 0.85. If no expiry date
            is included, then the implied move is for the nearest end of the week expiration (the nearest monthly expiration
            if there are no weekly contracts). Example: 2.2398043036460877.
        implied_move_perc (Union[Unset, str]): The implied move as a percentage of the underlying stock price. Example:
            0.012247398860706955.
        is_index (Union[Unset, bool]): Indicator, whether the ticker is an index. Example: True.
        issue_type (Union[Unset, StockIssueType]): The issue type of the ticker. Example: Common Stock.
        iv30d (Union[Unset, str]): The 30 day implied volatility. Example: 0.2038053572177887.
        iv30d_1d (Union[Unset, str]): The previous Trading Day's 30 day implied volatility. Example:
            0.18791808187961578.
        iv30d_1m (Union[Unset, str]): The implied volatility relative to the implied volatility throughout the previous
            year. A rank of 0 indicates that the implied volatility is the lowest it has been within the past year. A rank
            of 100 indicates that the implied volatility is the highest it has been within the past year. Example:
            13.52369891956068210400.
        iv30d_1w (Union[Unset, str]): The 30 day implied volatility from 1 week ago. Example: 0.18398597836494446.
        iv_rank (Union[Unset, str]): The implied volatility relative to the implied volatility throughout the previous
            year. A rank of 0 indicates that the implied volatility is the lowest it has been within the past year. A rank
            of 100 indicates that the implied volatility is the highest it has been within the past year. Example:
            13.52369891956068210400.
        marketcap (Union[Unset, str]): The marketcap of the underlying ticker. If the issue type of the ticker is ETF
            then the marketcap represents the AUM. Example: 2965813810400.
        net_call_premium (Union[Unset, str]): Defined as (call premium ask side) - (call premium bid side). Example:
            -29138464.
        net_put_premium (Union[Unset, str]): Defined as (put premium ask side) - (put premium bid side). Example:
            23924325.
        next_dividend_date (Union[Unset, datetime.date]): The next dividend date of the ticker. Null if either unknown
            as of now or the stock does not pay dividends. Example: 2023-10-26.
        next_earnings_date (Union[Unset, datetime.date]): The next earnings date of the ticker. Null if either unknown
            as of now or if the ticker does not have any earnings such as an ETF Example: 2023-10-26.
        prev_call_oi (Union[Unset, int]): The call open interest of the previous Trading Day. Example: 3994750.
        prev_close (Union[Unset, str]): The previous Trading Day's stock price of the ticker. Example: 189.70.
        prev_put_oi (Union[Unset, int]): The put open interest of the previous Trading Day. Example: 3679410.
        put_call_ratio (Union[Unset, str]): The put call ratio which is defined as put volume / call volume. Example:
            0.815713920982337.
        put_open_interest (Union[Unset, int]): The sum of the open interest of all the put options. Example: 3564153.
        put_premium (Union[Unset, str]): The sum of the premium of all the put transactions that executed. Example:
            163537151.
        put_volume (Union[Unset, int]): The sum of the size of all the put transactions that executed. Example: 808326.
        put_volume_ask_side (Union[Unset, int]): The sum of the size of all the put transactions that executed on the
            ask side. Example: 431791.
        put_volume_bid_side (Union[Unset, int]): The sum of the size of all the put transactions that executed on the
            bid side. Example: 314160.
        sector (Union[Unset, MarketGeneralSector]): The financial sector of the ticker. Empty if unknown or not
            applicable such as ETF/Index. Example: Technology.
        ticker (Union[Unset, str]): The stock ticker. Example: AAPL.
        total_open_interest (Union[Unset, int]): The sum of open interest of all chains for the given ticker.
        volatility (Union[Unset, str]): The implied volatility average of the at the money put and call option
            contracts. If no expiry date is included, then the volatility is of the nearest end of the week expiration (the
            nearest monthly expiration if there are no weekly contracts). Example: 0.18338055163621902.
        week_52_high (Union[Unset, str]): The 52 week high stock price of the ticker. Example: 198.23.
        week_52_low (Union[Unset, str]): The 52 week low stock price of the ticker. Example: 124.17.
    """

    avg_30_day_call_volume: Union[Unset, str] = UNSET
    avg_30_day_put_volume: Union[Unset, str] = UNSET
    avg_3_day_call_volume: Union[Unset, str] = UNSET
    avg_3_day_put_volume: Union[Unset, str] = UNSET
    avg_7_day_call_volume: Union[Unset, str] = UNSET
    avg_7_day_put_volume: Union[Unset, str] = UNSET
    bearish_premium: Union[Unset, str] = UNSET
    bullish_premium: Union[Unset, str] = UNSET
    call_open_interest: Union[Unset, int] = UNSET
    call_premium: Union[Unset, str] = UNSET
    call_volume: Union[Unset, int] = UNSET
    call_volume_ask_side: Union[Unset, int] = UNSET
    call_volume_bid_side: Union[Unset, int] = UNSET
    close: Union[Unset, str] = UNSET
    er_time: Union[Unset, StockEarningsTime] = UNSET
    implied_move: Union[Unset, str] = UNSET
    implied_move_perc: Union[Unset, str] = UNSET
    is_index: Union[Unset, bool] = UNSET
    issue_type: Union[Unset, StockIssueType] = UNSET
    iv30d: Union[Unset, str] = UNSET
    iv30d_1d: Union[Unset, str] = UNSET
    iv30d_1m: Union[Unset, str] = UNSET
    iv30d_1w: Union[Unset, str] = UNSET
    iv_rank: Union[Unset, str] = UNSET
    marketcap: Union[Unset, str] = UNSET
    net_call_premium: Union[Unset, str] = UNSET
    net_put_premium: Union[Unset, str] = UNSET
    next_dividend_date: Union[Unset, datetime.date] = UNSET
    next_earnings_date: Union[Unset, datetime.date] = UNSET
    prev_call_oi: Union[Unset, int] = UNSET
    prev_close: Union[Unset, str] = UNSET
    prev_put_oi: Union[Unset, int] = UNSET
    put_call_ratio: Union[Unset, str] = UNSET
    put_open_interest: Union[Unset, int] = UNSET
    put_premium: Union[Unset, str] = UNSET
    put_volume: Union[Unset, int] = UNSET
    put_volume_ask_side: Union[Unset, int] = UNSET
    put_volume_bid_side: Union[Unset, int] = UNSET
    sector: Union[Unset, MarketGeneralSector] = UNSET
    ticker: Union[Unset, str] = UNSET
    total_open_interest: Union[Unset, int] = UNSET
    volatility: Union[Unset, str] = UNSET
    week_52_high: Union[Unset, str] = UNSET
    week_52_low: Union[Unset, str] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        avg_30_day_call_volume = self.avg_30_day_call_volume

        avg_30_day_put_volume = self.avg_30_day_put_volume

        avg_3_day_call_volume = self.avg_3_day_call_volume

        avg_3_day_put_volume = self.avg_3_day_put_volume

        avg_7_day_call_volume = self.avg_7_day_call_volume

        avg_7_day_put_volume = self.avg_7_day_put_volume

        bearish_premium = self.bearish_premium

        bullish_premium = self.bullish_premium

        call_open_interest = self.call_open_interest

        call_premium = self.call_premium

        call_volume = self.call_volume

        call_volume_ask_side = self.call_volume_ask_side

        call_volume_bid_side = self.call_volume_bid_side

        close = self.close

        er_time: Union[Unset, str] = UNSET
        if not isinstance(self.er_time, Unset):
            er_time = self.er_time.value

        implied_move = self.implied_move

        implied_move_perc = self.implied_move_perc

        is_index = self.is_index

        issue_type: Union[Unset, str] = UNSET
        if not isinstance(self.issue_type, Unset):
            issue_type = self.issue_type.value

        iv30d = self.iv30d

        iv30d_1d = self.iv30d_1d

        iv30d_1m = self.iv30d_1m

        iv30d_1w = self.iv30d_1w

        iv_rank = self.iv_rank

        marketcap = self.marketcap

        net_call_premium = self.net_call_premium

        net_put_premium = self.net_put_premium

        next_dividend_date: Union[Unset, str] = UNSET
        if not isinstance(self.next_dividend_date, Unset):
            next_dividend_date = self.next_dividend_date.isoformat()

        next_earnings_date: Union[Unset, str] = UNSET
        if not isinstance(self.next_earnings_date, Unset):
            next_earnings_date = self.next_earnings_date.isoformat()

        prev_call_oi = self.prev_call_oi

        prev_close = self.prev_close

        prev_put_oi = self.prev_put_oi

        put_call_ratio = self.put_call_ratio

        put_open_interest = self.put_open_interest

        put_premium = self.put_premium

        put_volume = self.put_volume

        put_volume_ask_side = self.put_volume_ask_side

        put_volume_bid_side = self.put_volume_bid_side

        sector: Union[Unset, str] = UNSET
        if not isinstance(self.sector, Unset):
            sector = self.sector.value

        ticker = self.ticker

        total_open_interest = self.total_open_interest

        volatility = self.volatility

        week_52_high = self.week_52_high

        week_52_low = self.week_52_low

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if avg_30_day_call_volume is not UNSET:
            field_dict["avg_30_day_call_volume"] = avg_30_day_call_volume
        if avg_30_day_put_volume is not UNSET:
            field_dict["avg_30_day_put_volume"] = avg_30_day_put_volume
        if avg_3_day_call_volume is not UNSET:
            field_dict["avg_3_day_call_volume"] = avg_3_day_call_volume
        if avg_3_day_put_volume is not UNSET:
            field_dict["avg_3_day_put_volume"] = avg_3_day_put_volume
        if avg_7_day_call_volume is not UNSET:
            field_dict["avg_7_day_call_volume"] = avg_7_day_call_volume
        if avg_7_day_put_volume is not UNSET:
            field_dict["avg_7_day_put_volume"] = avg_7_day_put_volume
        if bearish_premium is not UNSET:
            field_dict["bearish_premium"] = bearish_premium
        if bullish_premium is not UNSET:
            field_dict["bullish_premium"] = bullish_premium
        if call_open_interest is not UNSET:
            field_dict["call_open_interest"] = call_open_interest
        if call_premium is not UNSET:
            field_dict["call_premium"] = call_premium
        if call_volume is not UNSET:
            field_dict["call_volume"] = call_volume
        if call_volume_ask_side is not UNSET:
            field_dict["call_volume_ask_side"] = call_volume_ask_side
        if call_volume_bid_side is not UNSET:
            field_dict["call_volume_bid_side"] = call_volume_bid_side
        if close is not UNSET:
            field_dict["close"] = close
        if er_time is not UNSET:
            field_dict["er_time"] = er_time
        if implied_move is not UNSET:
            field_dict["implied_move"] = implied_move
        if implied_move_perc is not UNSET:
            field_dict["implied_move_perc"] = implied_move_perc
        if is_index is not UNSET:
            field_dict["is_index"] = is_index
        if issue_type is not UNSET:
            field_dict["issue_type"] = issue_type
        if iv30d is not UNSET:
            field_dict["iv30d"] = iv30d
        if iv30d_1d is not UNSET:
            field_dict["iv30d_1d"] = iv30d_1d
        if iv30d_1m is not UNSET:
            field_dict["iv30d_1m"] = iv30d_1m
        if iv30d_1w is not UNSET:
            field_dict["iv30d_1w"] = iv30d_1w
        if iv_rank is not UNSET:
            field_dict["iv_rank"] = iv_rank
        if marketcap is not UNSET:
            field_dict["marketcap"] = marketcap
        if net_call_premium is not UNSET:
            field_dict["net_call_premium"] = net_call_premium
        if net_put_premium is not UNSET:
            field_dict["net_put_premium"] = net_put_premium
        if next_dividend_date is not UNSET:
            field_dict["next_dividend_date"] = next_dividend_date
        if next_earnings_date is not UNSET:
            field_dict["next_earnings_date"] = next_earnings_date
        if prev_call_oi is not UNSET:
            field_dict["prev_call_oi"] = prev_call_oi
        if prev_close is not UNSET:
            field_dict["prev_close"] = prev_close
        if prev_put_oi is not UNSET:
            field_dict["prev_put_oi"] = prev_put_oi
        if put_call_ratio is not UNSET:
            field_dict["put_call_ratio"] = put_call_ratio
        if put_open_interest is not UNSET:
            field_dict["put_open_interest"] = put_open_interest
        if put_premium is not UNSET:
            field_dict["put_premium"] = put_premium
        if put_volume is not UNSET:
            field_dict["put_volume"] = put_volume
        if put_volume_ask_side is not UNSET:
            field_dict["put_volume_ask_side"] = put_volume_ask_side
        if put_volume_bid_side is not UNSET:
            field_dict["put_volume_bid_side"] = put_volume_bid_side
        if sector is not UNSET:
            field_dict["sector"] = sector
        if ticker is not UNSET:
            field_dict["ticker"] = ticker
        if total_open_interest is not UNSET:
            field_dict["total_open_interest"] = total_open_interest
        if volatility is not UNSET:
            field_dict["volatility"] = volatility
        if week_52_high is not UNSET:
            field_dict["week_52_high"] = week_52_high
        if week_52_low is not UNSET:
            field_dict["week_52_low"] = week_52_low

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        avg_30_day_call_volume = d.pop("avg_30_day_call_volume", UNSET)

        avg_30_day_put_volume = d.pop("avg_30_day_put_volume", UNSET)

        avg_3_day_call_volume = d.pop("avg_3_day_call_volume", UNSET)

        avg_3_day_put_volume = d.pop("avg_3_day_put_volume", UNSET)

        avg_7_day_call_volume = d.pop("avg_7_day_call_volume", UNSET)

        avg_7_day_put_volume = d.pop("avg_7_day_put_volume", UNSET)

        bearish_premium = d.pop("bearish_premium", UNSET)

        bullish_premium = d.pop("bullish_premium", UNSET)

        call_open_interest = d.pop("call_open_interest", UNSET)

        call_premium = d.pop("call_premium", UNSET)

        call_volume = d.pop("call_volume", UNSET)

        call_volume_ask_side = d.pop("call_volume_ask_side", UNSET)

        call_volume_bid_side = d.pop("call_volume_bid_side", UNSET)

        close = d.pop("close", UNSET)

        _er_time = d.pop("er_time", UNSET)
        er_time: Union[Unset, StockEarningsTime]
        if isinstance(_er_time, Unset):
            er_time = UNSET
        else:
            er_time = StockEarningsTime(_er_time)

        implied_move = d.pop("implied_move", UNSET)

        implied_move_perc = d.pop("implied_move_perc", UNSET)

        is_index = d.pop("is_index", UNSET)

        _issue_type = d.pop("issue_type", UNSET)
        issue_type: Union[Unset, StockIssueType]
        if isinstance(_issue_type, Unset):
            issue_type = UNSET
        else:
            issue_type = StockIssueType(_issue_type)

        iv30d = d.pop("iv30d", UNSET)

        iv30d_1d = d.pop("iv30d_1d", UNSET)

        iv30d_1m = d.pop("iv30d_1m", UNSET)

        iv30d_1w = d.pop("iv30d_1w", UNSET)

        iv_rank = d.pop("iv_rank", UNSET)

        marketcap = d.pop("marketcap", UNSET)

        net_call_premium = d.pop("net_call_premium", UNSET)

        net_put_premium = d.pop("net_put_premium", UNSET)

        _next_dividend_date = d.pop("next_dividend_date", UNSET)
        next_dividend_date: Union[Unset, datetime.date]
        if isinstance(_next_dividend_date, Unset):
            next_dividend_date = UNSET
        else:
            next_dividend_date = isoparse(_next_dividend_date).date()

        _next_earnings_date = d.pop("next_earnings_date", UNSET)
        next_earnings_date: Union[Unset, datetime.date]
        if isinstance(_next_earnings_date, Unset):
            next_earnings_date = UNSET
        else:
            next_earnings_date = isoparse(_next_earnings_date).date()

        prev_call_oi = d.pop("prev_call_oi", UNSET)

        prev_close = d.pop("prev_close", UNSET)

        prev_put_oi = d.pop("prev_put_oi", UNSET)

        put_call_ratio = d.pop("put_call_ratio", UNSET)

        put_open_interest = d.pop("put_open_interest", UNSET)

        put_premium = d.pop("put_premium", UNSET)

        put_volume = d.pop("put_volume", UNSET)

        put_volume_ask_side = d.pop("put_volume_ask_side", UNSET)

        put_volume_bid_side = d.pop("put_volume_bid_side", UNSET)

        _sector = d.pop("sector", UNSET)
        sector: Union[Unset, MarketGeneralSector]
        if isinstance(_sector, Unset):
            sector = UNSET
        else:
            sector = MarketGeneralSector(_sector)

        ticker = d.pop("ticker", UNSET)

        total_open_interest = d.pop("total_open_interest", UNSET)

        volatility = d.pop("volatility", UNSET)

        week_52_high = d.pop("week_52_high", UNSET)

        week_52_low = d.pop("week_52_low", UNSET)

        stock_screener_response = cls(
            avg_30_day_call_volume=avg_30_day_call_volume,
            avg_30_day_put_volume=avg_30_day_put_volume,
            avg_3_day_call_volume=avg_3_day_call_volume,
            avg_3_day_put_volume=avg_3_day_put_volume,
            avg_7_day_call_volume=avg_7_day_call_volume,
            avg_7_day_put_volume=avg_7_day_put_volume,
            bearish_premium=bearish_premium,
            bullish_premium=bullish_premium,
            call_open_interest=call_open_interest,
            call_premium=call_premium,
            call_volume=call_volume,
            call_volume_ask_side=call_volume_ask_side,
            call_volume_bid_side=call_volume_bid_side,
            close=close,
            er_time=er_time,
            implied_move=implied_move,
            implied_move_perc=implied_move_perc,
            is_index=is_index,
            issue_type=issue_type,
            iv30d=iv30d,
            iv30d_1d=iv30d_1d,
            iv30d_1m=iv30d_1m,
            iv30d_1w=iv30d_1w,
            iv_rank=iv_rank,
            marketcap=marketcap,
            net_call_premium=net_call_premium,
            net_put_premium=net_put_premium,
            next_dividend_date=next_dividend_date,
            next_earnings_date=next_earnings_date,
            prev_call_oi=prev_call_oi,
            prev_close=prev_close,
            prev_put_oi=prev_put_oi,
            put_call_ratio=put_call_ratio,
            put_open_interest=put_open_interest,
            put_premium=put_premium,
            put_volume=put_volume,
            put_volume_ask_side=put_volume_ask_side,
            put_volume_bid_side=put_volume_bid_side,
            sector=sector,
            ticker=ticker,
            total_open_interest=total_open_interest,
            volatility=volatility,
            week_52_high=week_52_high,
            week_52_low=week_52_low,
        )

        stock_screener_response.additional_properties = d
        return stock_screener_response

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
