import datetime
from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.market_general_sector import MarketGeneralSector
from ..models.stock_earnings_time import StockEarningsTime
from ..types import UNSET, Unset

T = TypeVar("T", bound="OptionContractScreenerItem")


@_attrs_define
class OptionContractScreenerItem:
    r"""
    Example:
        {'data': [{'ask_side_volume': 119403, 'avg_price': '1.0465802437910297887119234370', 'bid_side_volume': 122789,
            'chain_prev_close': '1.29', 'close': '0.03', 'cross_volume': 0, 'er_time': 'unkown', 'floor_volume': 142,
            'high': '2.95', 'last_fill': datetime.datetime(2023, 9, 8, 17, 45, 32, tzinfo=datetime.timezone.utc), 'low':
            '0.02', 'mid_volume': 22707, 'multileg_volume': 7486, 'next_earnings_date': datetime.date(2023, 10, 18),
            'no_side_volume': 0, 'open': '0.92', 'open_interest': 18680, 'option_symbol': 'TSLA230908C00255000', 'premium':
            '27723806.00', 'sector': 'Consumer Cyclical', 'stock_multi_leg_volume': 52, 'stock_price': '247.94',
            'sweep_volume': 18260, 'ticker_vol': 2546773, 'total_ask_changes': 44343, 'total_bid_changes': 43939, 'trades':
            39690, 'volume': 264899}]}

    Attributes:
        ask_side_volume (Union[Unset, int]): The amount of volume that happened on the ask side.

            Ask side is defined as (ask + bid) / 2 < fill price.
             Example: 119403.
        avg_price (Union[Unset, str]): The volume weighted average fill price of the contract. Example:
            1.0465802437910297887119234370.
        bid_side_volume (Union[Unset, int]): The amount of volume that happened on the bid side.

            Bid side is defined as (ask + bid) / 2 > fill price.
             Example: 122789.
        chain_prev_close (Union[Unset, str]): The previous Trading Day's contract price. Example: 1.29.
        close (Union[Unset, str]): The last fill on the contract. Example: 0.03.
        cross_volume (Union[Unset, int]): The amount of cross volume.
            Cross volume consists of all transaction that have the cross trade code.
        er_time (Union[Unset, StockEarningsTime]): The time when the earnings will be released. Example: premarket.
        floor_volume (Union[Unset, int]): The amount of floor volume.
            Floor volume consists of all transaction that have the floor trade code.
             Example: 142.
        high (Union[Unset, str]): The highest fill on that contract. Example: 2.95.
        last_fill (Union[Unset, str]): The last time there was a transaction for the given contract as UTC timestamp.
            Example: 2023-09-08 17:45:32+00:00.
        low (Union[Unset, str]): The lowest fill on that contract. Example: 0.02.
        mid_volume (Union[Unset, int]): The amount of volume that happened in the middle of the ask and bid.

            Mid is defined as (ask + bid) / 2 == fill price.
             Example: 22707.
        multileg_volume (Union[Unset, int]): The amount of volume that happened as part of a multileg trade with another
            contract.
            This can be spreads/rolls/condors/butterflies and more.
             Example: 7486.
        next_earnings_date (Union[Unset, datetime.date]): The next earnings date of the ticker. Null if either unknown
            as of now or if the ticker does not have any earnings such as an ETF Example: 2023-10-26.
        no_side_volume (Union[Unset, int]): The amount of volume that happened on no identifiable side.
            This can be late, out of sequence and/or cross transactions.
        open_ (Union[Unset, str]): The first fill on that contract. Example: 0.92.
        open_interest (Union[Unset, int]): The open interest for the contract. Example: 18680.
        option_symbol (Union[Unset, str]): The option symbol of the contract.

            You can use the following regex to extract underlying ticker, option type, expiry & strike:
            `^(?<symbol>[\w]*)(?<expiry>(\d{2})(\d{2})(\d{2}))(?<type>[PC])(?<strike>\d{8})$`

            Keep in mind that the strike needs to be multiplied by 1,000.
        premium (Union[Unset, str]): The total option premium. Example: 27723806.00.
        sector (Union[Unset, MarketGeneralSector]): The financial sector of the ticker. Empty if unknown or not
            applicable such as ETF/Index. Example: Technology.
        stock_multi_leg_volume (Union[Unset, int]): The amount of volume that happened as part of a stock transaction
            and possibly other option contracts.
            This can be covered calls and more.
             Example: 52.
        stock_price (Union[Unset, str]): The latest stock price of the ticker. Example: 182.91.
        sweep_volume (Union[Unset, int]): The amount of sweep volume.
            Sweep volume consists of all transaction that have the sweep trade code.
             Example: 18260.
        ticker_vol (Union[Unset, int]): The total amount of options volume for the given ticker.
        total_ask_changes (Union[Unset, int]): The amount of time the ask changed for the given contract. Example:
            44343.
        total_bid_changes (Union[Unset, int]): The amount of time the bid changed for the given contract. Example:
            43939.
        trades (Union[Unset, int]): The amount of transaction for this contract. Example: 39690.
        volume (Union[Unset, int]): The contract volume. Example: 264899.
    """

    ask_side_volume: Union[Unset, int] = UNSET
    avg_price: Union[Unset, str] = UNSET
    bid_side_volume: Union[Unset, int] = UNSET
    chain_prev_close: Union[Unset, str] = UNSET
    close: Union[Unset, str] = UNSET
    cross_volume: Union[Unset, int] = UNSET
    er_time: Union[Unset, StockEarningsTime] = UNSET
    floor_volume: Union[Unset, int] = UNSET
    high: Union[Unset, str] = UNSET
    last_fill: Union[Unset, str] = UNSET
    low: Union[Unset, str] = UNSET
    mid_volume: Union[Unset, int] = UNSET
    multileg_volume: Union[Unset, int] = UNSET
    next_earnings_date: Union[Unset, datetime.date] = UNSET
    no_side_volume: Union[Unset, int] = UNSET
    open_: Union[Unset, str] = UNSET
    open_interest: Union[Unset, int] = UNSET
    option_symbol: Union[Unset, str] = UNSET
    premium: Union[Unset, str] = UNSET
    sector: Union[Unset, MarketGeneralSector] = UNSET
    stock_multi_leg_volume: Union[Unset, int] = UNSET
    stock_price: Union[Unset, str] = UNSET
    sweep_volume: Union[Unset, int] = UNSET
    ticker_vol: Union[Unset, int] = UNSET
    total_ask_changes: Union[Unset, int] = UNSET
    total_bid_changes: Union[Unset, int] = UNSET
    trades: Union[Unset, int] = UNSET
    volume: Union[Unset, int] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        ask_side_volume = self.ask_side_volume

        avg_price = self.avg_price

        bid_side_volume = self.bid_side_volume

        chain_prev_close = self.chain_prev_close

        close = self.close

        cross_volume = self.cross_volume

        er_time: Union[Unset, str] = UNSET
        if not isinstance(self.er_time, Unset):
            er_time = self.er_time.value

        floor_volume = self.floor_volume

        high = self.high

        last_fill = self.last_fill

        low = self.low

        mid_volume = self.mid_volume

        multileg_volume = self.multileg_volume

        next_earnings_date: Union[Unset, str] = UNSET
        if not isinstance(self.next_earnings_date, Unset):
            next_earnings_date = self.next_earnings_date.isoformat()

        no_side_volume = self.no_side_volume

        open_ = self.open_

        open_interest = self.open_interest

        option_symbol = self.option_symbol

        premium = self.premium

        sector: Union[Unset, str] = UNSET
        if not isinstance(self.sector, Unset):
            sector = self.sector.value

        stock_multi_leg_volume = self.stock_multi_leg_volume

        stock_price = self.stock_price

        sweep_volume = self.sweep_volume

        ticker_vol = self.ticker_vol

        total_ask_changes = self.total_ask_changes

        total_bid_changes = self.total_bid_changes

        trades = self.trades

        volume = self.volume

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if ask_side_volume is not UNSET:
            field_dict["ask_side_volume"] = ask_side_volume
        if avg_price is not UNSET:
            field_dict["avg_price"] = avg_price
        if bid_side_volume is not UNSET:
            field_dict["bid_side_volume"] = bid_side_volume
        if chain_prev_close is not UNSET:
            field_dict["chain_prev_close"] = chain_prev_close
        if close is not UNSET:
            field_dict["close"] = close
        if cross_volume is not UNSET:
            field_dict["cross_volume"] = cross_volume
        if er_time is not UNSET:
            field_dict["er_time"] = er_time
        if floor_volume is not UNSET:
            field_dict["floor_volume"] = floor_volume
        if high is not UNSET:
            field_dict["high"] = high
        if last_fill is not UNSET:
            field_dict["last_fill"] = last_fill
        if low is not UNSET:
            field_dict["low"] = low
        if mid_volume is not UNSET:
            field_dict["mid_volume"] = mid_volume
        if multileg_volume is not UNSET:
            field_dict["multileg_volume"] = multileg_volume
        if next_earnings_date is not UNSET:
            field_dict["next_earnings_date"] = next_earnings_date
        if no_side_volume is not UNSET:
            field_dict["no_side_volume"] = no_side_volume
        if open_ is not UNSET:
            field_dict["open"] = open_
        if open_interest is not UNSET:
            field_dict["open_interest"] = open_interest
        if option_symbol is not UNSET:
            field_dict["option_symbol"] = option_symbol
        if premium is not UNSET:
            field_dict["premium"] = premium
        if sector is not UNSET:
            field_dict["sector"] = sector
        if stock_multi_leg_volume is not UNSET:
            field_dict["stock_multi_leg_volume"] = stock_multi_leg_volume
        if stock_price is not UNSET:
            field_dict["stock_price"] = stock_price
        if sweep_volume is not UNSET:
            field_dict["sweep_volume"] = sweep_volume
        if ticker_vol is not UNSET:
            field_dict["ticker_vol"] = ticker_vol
        if total_ask_changes is not UNSET:
            field_dict["total_ask_changes"] = total_ask_changes
        if total_bid_changes is not UNSET:
            field_dict["total_bid_changes"] = total_bid_changes
        if trades is not UNSET:
            field_dict["trades"] = trades
        if volume is not UNSET:
            field_dict["volume"] = volume

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        ask_side_volume = d.pop("ask_side_volume", UNSET)

        avg_price = d.pop("avg_price", UNSET)

        bid_side_volume = d.pop("bid_side_volume", UNSET)

        chain_prev_close = d.pop("chain_prev_close", UNSET)

        close = d.pop("close", UNSET)

        cross_volume = d.pop("cross_volume", UNSET)

        _er_time = d.pop("er_time", UNSET)
        er_time: Union[Unset, StockEarningsTime]
        if isinstance(_er_time, Unset):
            er_time = UNSET
        else:
            er_time = StockEarningsTime(_er_time)

        floor_volume = d.pop("floor_volume", UNSET)

        high = d.pop("high", UNSET)

        last_fill = d.pop("last_fill", UNSET)

        low = d.pop("low", UNSET)

        mid_volume = d.pop("mid_volume", UNSET)

        multileg_volume = d.pop("multileg_volume", UNSET)

        _next_earnings_date = d.pop("next_earnings_date", UNSET)
        next_earnings_date: Union[Unset, datetime.date]
        if isinstance(_next_earnings_date, Unset):
            next_earnings_date = UNSET
        else:
            next_earnings_date = isoparse(_next_earnings_date).date()

        no_side_volume = d.pop("no_side_volume", UNSET)

        open_ = d.pop("open", UNSET)

        open_interest = d.pop("open_interest", UNSET)

        option_symbol = d.pop("option_symbol", UNSET)

        premium = d.pop("premium", UNSET)

        _sector = d.pop("sector", UNSET)
        sector: Union[Unset, MarketGeneralSector]
        if isinstance(_sector, Unset):
            sector = UNSET
        else:
            sector = MarketGeneralSector(_sector)

        stock_multi_leg_volume = d.pop("stock_multi_leg_volume", UNSET)

        stock_price = d.pop("stock_price", UNSET)

        sweep_volume = d.pop("sweep_volume", UNSET)

        ticker_vol = d.pop("ticker_vol", UNSET)

        total_ask_changes = d.pop("total_ask_changes", UNSET)

        total_bid_changes = d.pop("total_bid_changes", UNSET)

        trades = d.pop("trades", UNSET)

        volume = d.pop("volume", UNSET)

        option_contract_screener_item = cls(
            ask_side_volume=ask_side_volume,
            avg_price=avg_price,
            bid_side_volume=bid_side_volume,
            chain_prev_close=chain_prev_close,
            close=close,
            cross_volume=cross_volume,
            er_time=er_time,
            floor_volume=floor_volume,
            high=high,
            last_fill=last_fill,
            low=low,
            mid_volume=mid_volume,
            multileg_volume=multileg_volume,
            next_earnings_date=next_earnings_date,
            no_side_volume=no_side_volume,
            open_=open_,
            open_interest=open_interest,
            option_symbol=option_symbol,
            premium=premium,
            sector=sector,
            stock_multi_leg_volume=stock_multi_leg_volume,
            stock_price=stock_price,
            sweep_volume=sweep_volume,
            ticker_vol=ticker_vol,
            total_ask_changes=total_ask_changes,
            total_bid_changes=total_bid_changes,
            trades=trades,
            volume=volume,
        )

        option_contract_screener_item.additional_properties = d
        return option_contract_screener_item

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
