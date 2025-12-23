from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="OptionChainContract")


@_attrs_define
class OptionChainContract:
    """
    Example:
        {'ask_volume': 1766, 'avg_price': '0.01360828625235404896', 'bid_volume': 887, 'cross_volume': 0, 'date':
            datetime.date(2023, 5, 26), 'floor_volume': 0, 'high_price': '0.03', 'implied_volatility': '0.310502942482285',
            'iv_high': '0.675815680048166', 'iv_low': '0.310502942482285', 'last_price': '0.01', 'last_tape_time':
            datetime.datetime(2023, 5, 26, 21, 30, 29, tzinfo=datetime.timezone.utc), 'low_price': '0.01', 'mid_volume': 0,
            'multi_leg_volume': 393, 'neutral_volume': 2, 'open_interest': 15907, 'stock_multi_leg_volume': 0,
            'sweep_volume': 752, 'total_premium': '3613.00', 'trades': 244, 'volume': 2655}

    Attributes:
        ask_volume (Union[Unset, int]): The amount of volume that happened on the ask side.

            Ask side is defined as (ask + bid) / 2 < fill price.
             Example: 119403.
        avg_price (Union[Unset, str]): The volume weighted average fill price of the contract. Example:
            1.0465802437910297887119234370.
        bid_volume (Union[Unset, int]): The amount of volume that happened on the bid side.

            Bid side is defined as (ask + bid) / 2 > fill price.
             Example: 122789.
        cross_volume (Union[Unset, int]): The amount of cross volume.
            Cross volume consists of all transaction that have the cross trade code.
        date (Union[Unset, str]): A trading date in ISO format YYYY-MM-DD Example: 2023-09-08.
        floor_volume (Union[Unset, int]): The amount of floor volume.
            Floor volume consists of all transaction that have the floor trade code.
             Example: 142.
        high_price (Union[Unset, str]): The highest fill on that contract. Example: 2.95.
        implied_volatility (Union[Unset, str]): The implied volatility for the last transaction. Example:
            0.675815680048166.
        iv_high (Union[Unset, str]): The highest implied volatility at which a transaction occurred. Example:
            0.675815680048166.
        iv_low (Union[Unset, str]): The lowest implied volatility at which a transaction occurred. Example:
            0.310502942482285.
        last_price (Union[Unset, str]): The last fill on the contract. Example: 0.03.
        last_tape_time (Union[Unset, str]): The last time there was a transaction for the given contract as UTC
            timestamp. Example: 2023-09-08 17:45:32+00:00.
        low_price (Union[Unset, str]): The lowest fill on that contract. Example: 0.02.
        mid_volume (Union[Unset, int]): The amount of volume that happened in the middle of the ask and bid.

            Mid is defined as (ask + bid) / 2 == fill price.
             Example: 22707.
        multi_leg_volume (Union[Unset, int]): The amount of volume that happened as part of a multileg trade with
            another contract.
            This can be spreads/rolls/condors/butterflies and more.
             Example: 7486.
        no_side_volume (Union[Unset, int]): The amount of volume that happened on no identifiable side.
            This can be late, out of sequence and/or cross transactions.
        open_interest (Union[Unset, int]): The open interest for the contract. Example: 18680.
        stock_multi_leg_volume (Union[Unset, int]): The amount of volume that happened as part of a stock transaction
            and possibly other option contracts.
            This can be covered calls and more.
             Example: 52.
        sweep_volume (Union[Unset, int]): The amount of sweep volume.
            Sweep volume consists of all transaction that have the sweep trade code.
             Example: 18260.
        total_premium (Union[Unset, str]): The total option premium. Example: 27723806.00.
        trades (Union[Unset, int]): The amount of transaction for this contract. Example: 39690.
        volume (Union[Unset, int]): The contract volume. Example: 264899.
    """

    ask_volume: Union[Unset, int] = UNSET
    avg_price: Union[Unset, str] = UNSET
    bid_volume: Union[Unset, int] = UNSET
    cross_volume: Union[Unset, int] = UNSET
    date: Union[Unset, str] = UNSET
    floor_volume: Union[Unset, int] = UNSET
    high_price: Union[Unset, str] = UNSET
    implied_volatility: Union[Unset, str] = UNSET
    iv_high: Union[Unset, str] = UNSET
    iv_low: Union[Unset, str] = UNSET
    last_price: Union[Unset, str] = UNSET
    last_tape_time: Union[Unset, str] = UNSET
    low_price: Union[Unset, str] = UNSET
    mid_volume: Union[Unset, int] = UNSET
    multi_leg_volume: Union[Unset, int] = UNSET
    no_side_volume: Union[Unset, int] = UNSET
    open_interest: Union[Unset, int] = UNSET
    stock_multi_leg_volume: Union[Unset, int] = UNSET
    sweep_volume: Union[Unset, int] = UNSET
    total_premium: Union[Unset, str] = UNSET
    trades: Union[Unset, int] = UNSET
    volume: Union[Unset, int] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        ask_volume = self.ask_volume

        avg_price = self.avg_price

        bid_volume = self.bid_volume

        cross_volume = self.cross_volume

        date = self.date

        floor_volume = self.floor_volume

        high_price = self.high_price

        implied_volatility = self.implied_volatility

        iv_high = self.iv_high

        iv_low = self.iv_low

        last_price = self.last_price

        last_tape_time = self.last_tape_time

        low_price = self.low_price

        mid_volume = self.mid_volume

        multi_leg_volume = self.multi_leg_volume

        no_side_volume = self.no_side_volume

        open_interest = self.open_interest

        stock_multi_leg_volume = self.stock_multi_leg_volume

        sweep_volume = self.sweep_volume

        total_premium = self.total_premium

        trades = self.trades

        volume = self.volume

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if ask_volume is not UNSET:
            field_dict["ask_volume"] = ask_volume
        if avg_price is not UNSET:
            field_dict["avg_price"] = avg_price
        if bid_volume is not UNSET:
            field_dict["bid_volume"] = bid_volume
        if cross_volume is not UNSET:
            field_dict["cross_volume"] = cross_volume
        if date is not UNSET:
            field_dict["date"] = date
        if floor_volume is not UNSET:
            field_dict["floor_volume"] = floor_volume
        if high_price is not UNSET:
            field_dict["high_price"] = high_price
        if implied_volatility is not UNSET:
            field_dict["implied_volatility"] = implied_volatility
        if iv_high is not UNSET:
            field_dict["iv_high"] = iv_high
        if iv_low is not UNSET:
            field_dict["iv_low"] = iv_low
        if last_price is not UNSET:
            field_dict["last_price"] = last_price
        if last_tape_time is not UNSET:
            field_dict["last_tape_time"] = last_tape_time
        if low_price is not UNSET:
            field_dict["low_price"] = low_price
        if mid_volume is not UNSET:
            field_dict["mid_volume"] = mid_volume
        if multi_leg_volume is not UNSET:
            field_dict["multi_leg_volume"] = multi_leg_volume
        if no_side_volume is not UNSET:
            field_dict["no_side_volume"] = no_side_volume
        if open_interest is not UNSET:
            field_dict["open_interest"] = open_interest
        if stock_multi_leg_volume is not UNSET:
            field_dict["stock_multi_leg_volume"] = stock_multi_leg_volume
        if sweep_volume is not UNSET:
            field_dict["sweep_volume"] = sweep_volume
        if total_premium is not UNSET:
            field_dict["total_premium"] = total_premium
        if trades is not UNSET:
            field_dict["trades"] = trades
        if volume is not UNSET:
            field_dict["volume"] = volume

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        ask_volume = d.pop("ask_volume", UNSET)

        avg_price = d.pop("avg_price", UNSET)

        bid_volume = d.pop("bid_volume", UNSET)

        cross_volume = d.pop("cross_volume", UNSET)

        date = d.pop("date", UNSET)

        floor_volume = d.pop("floor_volume", UNSET)

        high_price = d.pop("high_price", UNSET)

        implied_volatility = d.pop("implied_volatility", UNSET)

        iv_high = d.pop("iv_high", UNSET)

        iv_low = d.pop("iv_low", UNSET)

        last_price = d.pop("last_price", UNSET)

        last_tape_time = d.pop("last_tape_time", UNSET)

        low_price = d.pop("low_price", UNSET)

        mid_volume = d.pop("mid_volume", UNSET)

        multi_leg_volume = d.pop("multi_leg_volume", UNSET)

        no_side_volume = d.pop("no_side_volume", UNSET)

        open_interest = d.pop("open_interest", UNSET)

        stock_multi_leg_volume = d.pop("stock_multi_leg_volume", UNSET)

        sweep_volume = d.pop("sweep_volume", UNSET)

        total_premium = d.pop("total_premium", UNSET)

        trades = d.pop("trades", UNSET)

        volume = d.pop("volume", UNSET)

        option_chain_contract = cls(
            ask_volume=ask_volume,
            avg_price=avg_price,
            bid_volume=bid_volume,
            cross_volume=cross_volume,
            date=date,
            floor_volume=floor_volume,
            high_price=high_price,
            implied_volatility=implied_volatility,
            iv_high=iv_high,
            iv_low=iv_low,
            last_price=last_price,
            last_tape_time=last_tape_time,
            low_price=low_price,
            mid_volume=mid_volume,
            multi_leg_volume=multi_leg_volume,
            no_side_volume=no_side_volume,
            open_interest=open_interest,
            stock_multi_leg_volume=stock_multi_leg_volume,
            sweep_volume=sweep_volume,
            total_premium=total_premium,
            trades=trades,
            volume=volume,
        )

        option_chain_contract.additional_properties = d
        return option_chain_contract

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
