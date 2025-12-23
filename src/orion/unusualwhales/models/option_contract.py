from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="OptionContract")


@_attrs_define
class OptionContract:
    r"""All Option Chain Contracts for a Given Ticker

    Example:
        {'data': [{'ask_volume': 56916, 'avg_price': '0.77927817593516586531', 'bid_volume': 68967, 'floor_volume':
            1815, 'high_price': '5.75', 'implied_volatility': '0.542805337797143', 'last_price': '0.01', 'low_price':
            '0.01', 'mid_volume': 6393, 'multi_leg_volume': 9871, 'nbbo_ask': '0.01', 'nbbo_bid': '0', 'no_side_volume':
            6393, 'open_interest': 22868, 'option_symbol': 'AAPL240202P00185000', 'prev_oi': 20217,
            'stock_multi_leg_volume': 13, 'sweep_volume': 12893, 'total_premium': '10307980.00', 'volume': 132276},
            {'ask_volume': 54820, 'avg_price': '0.19195350495251190385', 'bid_volume': 60784, 'floor_volume': 0,
            'high_price': '0.80', 'implied_volatility': '0.462957019859562', 'last_price': '0.01', 'low_price': '0.01',
            'mid_volume': 2215, 'multi_leg_volume': 5301, 'nbbo_ask': '0.01', 'nbbo_bid': '0', 'no_side_volume': 2215,
            'open_interest': 19352, 'option_symbol': 'AAPL240202C00187500', 'prev_oi': 18135, 'stock_multi_leg_volume': 9,
            'sweep_volume': 11152, 'total_premium': '2261577.00', 'volume': 117819}]}

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
        floor_volume (Union[Unset, int]): The amount of floor volume.
            Floor volume consists of all transaction that have the floor trade code.
             Example: 142.
        high_price (Union[Unset, str]): The highest fill on that contract. Example: 2.95.
        implied_volatility (Union[Unset, str]): The implied volatility for the last transaction. Example:
            0.675815680048166.
        last_price (Union[Unset, str]): The last fill on the contract. Example: 0.03.
        low_price (Union[Unset, str]): The lowest fill on that contract. Example: 0.02.
        mid_volume (Union[Unset, int]): The amount of volume that happened in the middle of the ask and bid.

            Mid is defined as (ask + bid) / 2 == fill price.
             Example: 22707.
        multi_leg_volume (Union[Unset, int]): The amount of volume that happened as part of a multileg trade with
            another contract.
            This can be spreads/rolls/condors/butterflies and more.
             Example: 7486.
        nbbo_ask (Union[Unset, str]): The National Best Bid and Offer (NBBO) ask price. Example: 0.03.
        nbbo_bid (Union[Unset, str]): The National Best Bid and Offer (NBBO) bid price. Example: 0.03.
        no_side_volume (Union[Unset, int]): The amount of volume that happened on no identifiable side.
            This can be late, out of sequence and/or cross transactions.
        open_interest (Union[Unset, int]): The open interest for the contract. Example: 18680.
        option_symbol (Union[Unset, str]): The option symbol of the contract.

            You can use the following regex to extract underlying ticker, option type, expiry & strike:
            `^(?<symbol>[\w]*)(?<expiry>(\d{2})(\d{2})(\d{2}))(?<type>[PC])(?<strike>\d{8})$`

            Keep in mind that the strike needs to be multiplied by 1,000.
        prev_oi (Union[Unset, int]): The previous Trading Day's open interest. Example: 18680.
        stock_multi_leg_volume (Union[Unset, int]): The amount of volume that happened as part of a stock transaction
            and possibly other option contracts.
            This can be covered calls and more.
             Example: 52.
        sweep_volume (Union[Unset, int]): The amount of sweep volume.
            Sweep volume consists of all transaction that have the sweep trade code.
             Example: 18260.
        total_premium (Union[Unset, str]): The total option premium. Example: 27723806.00.
        volume (Union[Unset, int]): The contract volume. Example: 264899.
    """

    ask_volume: Union[Unset, int] = UNSET
    avg_price: Union[Unset, str] = UNSET
    bid_volume: Union[Unset, int] = UNSET
    cross_volume: Union[Unset, int] = UNSET
    floor_volume: Union[Unset, int] = UNSET
    high_price: Union[Unset, str] = UNSET
    implied_volatility: Union[Unset, str] = UNSET
    last_price: Union[Unset, str] = UNSET
    low_price: Union[Unset, str] = UNSET
    mid_volume: Union[Unset, int] = UNSET
    multi_leg_volume: Union[Unset, int] = UNSET
    nbbo_ask: Union[Unset, str] = UNSET
    nbbo_bid: Union[Unset, str] = UNSET
    no_side_volume: Union[Unset, int] = UNSET
    open_interest: Union[Unset, int] = UNSET
    option_symbol: Union[Unset, str] = UNSET
    prev_oi: Union[Unset, int] = UNSET
    stock_multi_leg_volume: Union[Unset, int] = UNSET
    sweep_volume: Union[Unset, int] = UNSET
    total_premium: Union[Unset, str] = UNSET
    volume: Union[Unset, int] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        ask_volume = self.ask_volume

        avg_price = self.avg_price

        bid_volume = self.bid_volume

        cross_volume = self.cross_volume

        floor_volume = self.floor_volume

        high_price = self.high_price

        implied_volatility = self.implied_volatility

        last_price = self.last_price

        low_price = self.low_price

        mid_volume = self.mid_volume

        multi_leg_volume = self.multi_leg_volume

        nbbo_ask = self.nbbo_ask

        nbbo_bid = self.nbbo_bid

        no_side_volume = self.no_side_volume

        open_interest = self.open_interest

        option_symbol = self.option_symbol

        prev_oi = self.prev_oi

        stock_multi_leg_volume = self.stock_multi_leg_volume

        sweep_volume = self.sweep_volume

        total_premium = self.total_premium

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
        if floor_volume is not UNSET:
            field_dict["floor_volume"] = floor_volume
        if high_price is not UNSET:
            field_dict["high_price"] = high_price
        if implied_volatility is not UNSET:
            field_dict["implied_volatility"] = implied_volatility
        if last_price is not UNSET:
            field_dict["last_price"] = last_price
        if low_price is not UNSET:
            field_dict["low_price"] = low_price
        if mid_volume is not UNSET:
            field_dict["mid_volume"] = mid_volume
        if multi_leg_volume is not UNSET:
            field_dict["multi_leg_volume"] = multi_leg_volume
        if nbbo_ask is not UNSET:
            field_dict["nbbo_ask"] = nbbo_ask
        if nbbo_bid is not UNSET:
            field_dict["nbbo_bid"] = nbbo_bid
        if no_side_volume is not UNSET:
            field_dict["no_side_volume"] = no_side_volume
        if open_interest is not UNSET:
            field_dict["open_interest"] = open_interest
        if option_symbol is not UNSET:
            field_dict["option_symbol"] = option_symbol
        if prev_oi is not UNSET:
            field_dict["prev_oi"] = prev_oi
        if stock_multi_leg_volume is not UNSET:
            field_dict["stock_multi_leg_volume"] = stock_multi_leg_volume
        if sweep_volume is not UNSET:
            field_dict["sweep_volume"] = sweep_volume
        if total_premium is not UNSET:
            field_dict["total_premium"] = total_premium
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

        floor_volume = d.pop("floor_volume", UNSET)

        high_price = d.pop("high_price", UNSET)

        implied_volatility = d.pop("implied_volatility", UNSET)

        last_price = d.pop("last_price", UNSET)

        low_price = d.pop("low_price", UNSET)

        mid_volume = d.pop("mid_volume", UNSET)

        multi_leg_volume = d.pop("multi_leg_volume", UNSET)

        nbbo_ask = d.pop("nbbo_ask", UNSET)

        nbbo_bid = d.pop("nbbo_bid", UNSET)

        no_side_volume = d.pop("no_side_volume", UNSET)

        open_interest = d.pop("open_interest", UNSET)

        option_symbol = d.pop("option_symbol", UNSET)

        prev_oi = d.pop("prev_oi", UNSET)

        stock_multi_leg_volume = d.pop("stock_multi_leg_volume", UNSET)

        sweep_volume = d.pop("sweep_volume", UNSET)

        total_premium = d.pop("total_premium", UNSET)

        volume = d.pop("volume", UNSET)

        option_contract = cls(
            ask_volume=ask_volume,
            avg_price=avg_price,
            bid_volume=bid_volume,
            cross_volume=cross_volume,
            floor_volume=floor_volume,
            high_price=high_price,
            implied_volatility=implied_volatility,
            last_price=last_price,
            low_price=low_price,
            mid_volume=mid_volume,
            multi_leg_volume=multi_leg_volume,
            nbbo_ask=nbbo_ask,
            nbbo_bid=nbbo_bid,
            no_side_volume=no_side_volume,
            open_interest=open_interest,
            option_symbol=option_symbol,
            prev_oi=prev_oi,
            stock_multi_leg_volume=stock_multi_leg_volume,
            sweep_volume=sweep_volume,
            total_premium=total_premium,
            volume=volume,
        )

        option_contract.additional_properties = d
        return option_contract

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
