from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="FlowPerStrike")


@_attrs_define
class FlowPerStrike:
    """The flow data per strike for a Trading Day.

    Example:
        [{'call_otm_premium': '9908777.0', 'call_otm_trades': 6338, 'call_otm_volume': 40385, 'call_premium':
            '9908777.0', 'call_premium_ask_side': '5037703.0', 'call_premium_bid_side': '4055973.0', 'call_trades': 6338,
            'call_volume': 40385, 'call_volume_ask_side': 20145, 'call_volume_bid_side': 16923, 'date': datetime.date(2024,
            1, 22), 'put_otm_premium': '0.0', 'put_otm_trades': 0, 'put_otm_volume': 0, 'put_premium': '2746872.0',
            'put_premium_ask_side': '799873.0', 'put_premium_bid_side': '1614848.0', 'put_trades': 841, 'put_volume': 4306,
            'put_volume_ask_side': 1398, 'put_volume_bid_side': 2323, 'strike': '70.0', 'ticker': 'BABA'},
            {'call_otm_premium': '4208428.0', 'call_otm_trades': 2875, 'call_otm_volume': 28218, 'call_premium':
            '4208428.0', 'call_premium_ask_side': '1543361.0', 'call_premium_bid_side': '1589925.0', 'call_trades': 2875,
            'call_volume': 28218, 'call_volume_ask_side': 14048, 'call_volume_bid_side': 10418, 'date': datetime.date(2024,
            1, 22), 'put_otm_premium': '0.0', 'put_otm_trades': 0, 'put_otm_volume': 0, 'put_premium': '1996364.0',
            'put_premium_ask_side': '432005.0', 'put_premium_bid_side': '1317894.0', 'put_trades': 323, 'put_volume': 2270,
            'put_volume_ask_side': 482, 'put_volume_bid_side': 1545, 'strike': '75.0', 'ticker': 'BABA'}]

    Attributes:
        call_otm_premium (Union[Unset, str]): The sum of the premium of all the out the money (OTM) call transactions
            that executed. Example: 9908777.0.
        call_otm_trades (Union[Unset, int]): The amount of out the money (OTM) call transactions that executed. Example:
            6338.
        call_otm_volume (Union[Unset, int]): The sum of the size of all the out the money (OTM) call transactions that
            executed. Example: 40385.
        call_premium (Union[Unset, str]): The sum of the premium of all the call transactions that executed. Example:
            9908777.0.
        call_premium_ask_side (Union[Unset, str]): The sum of the premium of all the call transactions that executed on
            the ask side. Example: 5037703.0.
        call_premium_bid_side (Union[Unset, str]): The sum of the premium of all the call transactions that executed on
            the bid side. Example: 4055973.0.
        call_trades (Union[Unset, int]): The amount of call transactions that executed. Example: 6338.
        call_volume (Union[Unset, int]): The sum of the size of all the call transactions that executed. Example:
            990943.
        call_volume_ask_side (Union[Unset, int]): The sum of the size of all the call transactions that executed on the
            ask side. Example: 417251.
        call_volume_bid_side (Union[Unset, int]): The sum of the size of all the call transactions that executed on the
            bid side. Example: 498271.
        date (Union[Unset, str]): A trading date in ISO format YYYY-MM-DD Example: 2023-09-08.
        put_otm_premium (Union[Unset, str]): The sum of the premium of all the out the money (OTM) put transactions that
            executed. Example: 1204570.0.
        put_otm_trades (Union[Unset, int]): The amount of out the money (OTM) put transactions that executed. Example:
            4270.
        put_otm_volume (Union[Unset, int]): The sum of the size of all the out the money (OTM) put transactions that
            executed. Example: 12638.
        put_premium (Union[Unset, str]): The sum of the premium of all the put transactions that executed. Example:
            163537151.
        put_premium_ask_side (Union[Unset, str]): The sum of the premium of all the put transactions that executed on
            the ask side. Example: 799873.0.
        put_premium_bid_side (Union[Unset, str]): The sum of the premium of all the put transactions that executed on
            the bid side. Example: 4055973.0.
        put_trades (Union[Unset, int]): The amount of put transactions that executed. Example: 841.
        put_volume (Union[Unset, int]): The sum of the size of all the put transactions that executed. Example: 808326.
        put_volume_ask_side (Union[Unset, int]): The sum of the size of all the put transactions that executed on the
            ask side. Example: 431791.
        put_volume_bid_side (Union[Unset, int]): The sum of the size of all the put transactions that executed on the
            bid side. Example: 314160.
        strike (Union[Unset, str]): The contract strike. Example: 375.
        ticker (Union[Unset, str]): The stock ticker. Example: AAPL.
    """

    call_otm_premium: Union[Unset, str] = UNSET
    call_otm_trades: Union[Unset, int] = UNSET
    call_otm_volume: Union[Unset, int] = UNSET
    call_premium: Union[Unset, str] = UNSET
    call_premium_ask_side: Union[Unset, str] = UNSET
    call_premium_bid_side: Union[Unset, str] = UNSET
    call_trades: Union[Unset, int] = UNSET
    call_volume: Union[Unset, int] = UNSET
    call_volume_ask_side: Union[Unset, int] = UNSET
    call_volume_bid_side: Union[Unset, int] = UNSET
    date: Union[Unset, str] = UNSET
    put_otm_premium: Union[Unset, str] = UNSET
    put_otm_trades: Union[Unset, int] = UNSET
    put_otm_volume: Union[Unset, int] = UNSET
    put_premium: Union[Unset, str] = UNSET
    put_premium_ask_side: Union[Unset, str] = UNSET
    put_premium_bid_side: Union[Unset, str] = UNSET
    put_trades: Union[Unset, int] = UNSET
    put_volume: Union[Unset, int] = UNSET
    put_volume_ask_side: Union[Unset, int] = UNSET
    put_volume_bid_side: Union[Unset, int] = UNSET
    strike: Union[Unset, str] = UNSET
    ticker: Union[Unset, str] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        call_otm_premium = self.call_otm_premium

        call_otm_trades = self.call_otm_trades

        call_otm_volume = self.call_otm_volume

        call_premium = self.call_premium

        call_premium_ask_side = self.call_premium_ask_side

        call_premium_bid_side = self.call_premium_bid_side

        call_trades = self.call_trades

        call_volume = self.call_volume

        call_volume_ask_side = self.call_volume_ask_side

        call_volume_bid_side = self.call_volume_bid_side

        date = self.date

        put_otm_premium = self.put_otm_premium

        put_otm_trades = self.put_otm_trades

        put_otm_volume = self.put_otm_volume

        put_premium = self.put_premium

        put_premium_ask_side = self.put_premium_ask_side

        put_premium_bid_side = self.put_premium_bid_side

        put_trades = self.put_trades

        put_volume = self.put_volume

        put_volume_ask_side = self.put_volume_ask_side

        put_volume_bid_side = self.put_volume_bid_side

        strike = self.strike

        ticker = self.ticker

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if call_otm_premium is not UNSET:
            field_dict["call_otm_premium"] = call_otm_premium
        if call_otm_trades is not UNSET:
            field_dict["call_otm_trades"] = call_otm_trades
        if call_otm_volume is not UNSET:
            field_dict["call_otm_volume"] = call_otm_volume
        if call_premium is not UNSET:
            field_dict["call_premium"] = call_premium
        if call_premium_ask_side is not UNSET:
            field_dict["call_premium_ask_side"] = call_premium_ask_side
        if call_premium_bid_side is not UNSET:
            field_dict["call_premium_bid_side"] = call_premium_bid_side
        if call_trades is not UNSET:
            field_dict["call_trades"] = call_trades
        if call_volume is not UNSET:
            field_dict["call_volume"] = call_volume
        if call_volume_ask_side is not UNSET:
            field_dict["call_volume_ask_side"] = call_volume_ask_side
        if call_volume_bid_side is not UNSET:
            field_dict["call_volume_bid_side"] = call_volume_bid_side
        if date is not UNSET:
            field_dict["date"] = date
        if put_otm_premium is not UNSET:
            field_dict["put_otm_premium"] = put_otm_premium
        if put_otm_trades is not UNSET:
            field_dict["put_otm_trades"] = put_otm_trades
        if put_otm_volume is not UNSET:
            field_dict["put_otm_volume"] = put_otm_volume
        if put_premium is not UNSET:
            field_dict["put_premium"] = put_premium
        if put_premium_ask_side is not UNSET:
            field_dict["put_premium_ask_side"] = put_premium_ask_side
        if put_premium_bid_side is not UNSET:
            field_dict["put_premium_bid_side"] = put_premium_bid_side
        if put_trades is not UNSET:
            field_dict["put_trades"] = put_trades
        if put_volume is not UNSET:
            field_dict["put_volume"] = put_volume
        if put_volume_ask_side is not UNSET:
            field_dict["put_volume_ask_side"] = put_volume_ask_side
        if put_volume_bid_side is not UNSET:
            field_dict["put_volume_bid_side"] = put_volume_bid_side
        if strike is not UNSET:
            field_dict["strike"] = strike
        if ticker is not UNSET:
            field_dict["ticker"] = ticker

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        call_otm_premium = d.pop("call_otm_premium", UNSET)

        call_otm_trades = d.pop("call_otm_trades", UNSET)

        call_otm_volume = d.pop("call_otm_volume", UNSET)

        call_premium = d.pop("call_premium", UNSET)

        call_premium_ask_side = d.pop("call_premium_ask_side", UNSET)

        call_premium_bid_side = d.pop("call_premium_bid_side", UNSET)

        call_trades = d.pop("call_trades", UNSET)

        call_volume = d.pop("call_volume", UNSET)

        call_volume_ask_side = d.pop("call_volume_ask_side", UNSET)

        call_volume_bid_side = d.pop("call_volume_bid_side", UNSET)

        date = d.pop("date", UNSET)

        put_otm_premium = d.pop("put_otm_premium", UNSET)

        put_otm_trades = d.pop("put_otm_trades", UNSET)

        put_otm_volume = d.pop("put_otm_volume", UNSET)

        put_premium = d.pop("put_premium", UNSET)

        put_premium_ask_side = d.pop("put_premium_ask_side", UNSET)

        put_premium_bid_side = d.pop("put_premium_bid_side", UNSET)

        put_trades = d.pop("put_trades", UNSET)

        put_volume = d.pop("put_volume", UNSET)

        put_volume_ask_side = d.pop("put_volume_ask_side", UNSET)

        put_volume_bid_side = d.pop("put_volume_bid_side", UNSET)

        strike = d.pop("strike", UNSET)

        ticker = d.pop("ticker", UNSET)

        flow_per_strike = cls(
            call_otm_premium=call_otm_premium,
            call_otm_trades=call_otm_trades,
            call_otm_volume=call_otm_volume,
            call_premium=call_premium,
            call_premium_ask_side=call_premium_ask_side,
            call_premium_bid_side=call_premium_bid_side,
            call_trades=call_trades,
            call_volume=call_volume,
            call_volume_ask_side=call_volume_ask_side,
            call_volume_bid_side=call_volume_bid_side,
            date=date,
            put_otm_premium=put_otm_premium,
            put_otm_trades=put_otm_trades,
            put_otm_volume=put_otm_volume,
            put_premium=put_premium,
            put_premium_ask_side=put_premium_ask_side,
            put_premium_bid_side=put_premium_bid_side,
            put_trades=put_trades,
            put_volume=put_volume,
            put_volume_ask_side=put_volume_ask_side,
            put_volume_bid_side=put_volume_bid_side,
            strike=strike,
            ticker=ticker,
        )

        flow_per_strike.additional_properties = d
        return flow_per_strike

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
