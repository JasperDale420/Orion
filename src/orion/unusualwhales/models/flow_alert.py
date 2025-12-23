from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.option_contract_type import OptionContractType
from ..types import UNSET, Unset

T = TypeVar("T", bound="FlowAlert")


@_attrs_define
class FlowAlert:
    r"""Representation of a flow alert.

    Example:
        {'data': [{'alert_rule': 'RepeatedHits', 'all_opening_trades': False, 'created_at': datetime.datetime(2023, 12,
            12, 16, 35, 52, 168490, tzinfo=datetime.timezone.utc), 'expiry': datetime.date(2023, 12, 22), 'expiry_count': 1,
            'has_floor': False, 'has_multileg': False, 'has_singleleg': True, 'has_sweep': True, 'open_interest': 7913,
            'option_chain': 'MSFT231222C00375000', 'price': '4.05', 'strike': '375', 'ticker': 'MSFT',
            'total_ask_side_prem': '151875', 'total_bid_side_prem': '405', 'total_premium': '186705', 'total_size': 461,
            'trade_count': 32, 'type': 'call', 'underlying_price': '372.99', 'volume': 2442, 'volume_oi_ratio':
            '0.30860609124226'}]}

    Attributes:
        alert_rule (Union[Unset, str]): The name of the alert rule. Example: RepeatedHits.
        all_opening_trades (Union[Unset, bool]):
        created_at (Union[Unset, str]): A UTC timestamp. Example: 2023-12-12 16:35:52.168490+00:00.
        expiry (Union[Unset, str]): The contract expiry date in ISO format. Example: 2023-12-22.
        expiry_count (Union[Unset, int]): The amount of expiries belonging to the trade. This is only greater than 1 if
            it is a multileg trade. Example: 2.
        has_floor (Union[Unset, bool]):
        has_multileg (Union[Unset, bool]): Whether the trade is a multileg trade.
        has_singleleg (Union[Unset, bool]): Whether the trade is a singleleg trade. Example: True.
        has_sweep (Union[Unset, bool]): Whether the trade is a sweep. Example: True.
        open_interest (Union[Unset, float]): The price of the tick Example: 19.32.
        option_chain (Union[Unset, str]): The option symbol of the contract.

            You can use the following regex to extract underlying ticker, option type, expiry & strike:
            `^(?<symbol>[\w]*)(?<expiry>(\d{2})(\d{2})(\d{2}))(?<type>[PC])(?<strike>\d{8})$`

            Keep in mind that the strike needs to be multiplied by 1,000.
        price (Union[Unset, float]): The price of the tick Example: 19.32.
        strike (Union[Unset, str]): The contract strike. Example: 375.
        ticker (Union[Unset, float]): The price of the tick Example: 19.32.
        total_ask_side_prem (Union[Unset, float]): The price of the tick Example: 19.32.
        total_bid_side_prem (Union[Unset, float]): The price of the tick Example: 19.32.
        total_premium (Union[Unset, float]): The price of the tick Example: 19.32.
        total_size (Union[Unset, float]): The price of the tick Example: 19.32.
        trade_count (Union[Unset, float]): The price of the tick Example: 19.32.
        type (Union[Unset, OptionContractType]): The contract type. Example: call or put.
        underlying_price (Union[Unset, float]): The price of the tick Example: 19.32.
        volume (Union[Unset, float]): The price of the tick Example: 19.32.
        volume_oi_ratio (Union[Unset, float]): The price of the tick Example: 19.32.
    """

    alert_rule: Union[Unset, str] = UNSET
    all_opening_trades: Union[Unset, bool] = UNSET
    created_at: Union[Unset, str] = UNSET
    expiry: Union[Unset, str] = UNSET
    expiry_count: Union[Unset, int] = UNSET
    has_floor: Union[Unset, bool] = UNSET
    has_multileg: Union[Unset, bool] = UNSET
    has_singleleg: Union[Unset, bool] = UNSET
    has_sweep: Union[Unset, bool] = UNSET
    open_interest: Union[Unset, float] = UNSET
    option_chain: Union[Unset, str] = UNSET
    price: Union[Unset, float] = UNSET
    strike: Union[Unset, str] = UNSET
    ticker: Union[Unset, float] = UNSET
    total_ask_side_prem: Union[Unset, float] = UNSET
    total_bid_side_prem: Union[Unset, float] = UNSET
    total_premium: Union[Unset, float] = UNSET
    total_size: Union[Unset, float] = UNSET
    trade_count: Union[Unset, float] = UNSET
    type: Union[Unset, OptionContractType] = UNSET
    underlying_price: Union[Unset, float] = UNSET
    volume: Union[Unset, float] = UNSET
    volume_oi_ratio: Union[Unset, float] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        alert_rule = self.alert_rule

        all_opening_trades = self.all_opening_trades

        created_at = self.created_at

        expiry = self.expiry

        expiry_count = self.expiry_count

        has_floor = self.has_floor

        has_multileg = self.has_multileg

        has_singleleg = self.has_singleleg

        has_sweep = self.has_sweep

        open_interest = self.open_interest

        option_chain = self.option_chain

        price = self.price

        strike = self.strike

        ticker = self.ticker

        total_ask_side_prem = self.total_ask_side_prem

        total_bid_side_prem = self.total_bid_side_prem

        total_premium = self.total_premium

        total_size = self.total_size

        trade_count = self.trade_count

        type: Union[Unset, str] = UNSET
        if not isinstance(self.type, Unset):
            type = self.type.value

        underlying_price = self.underlying_price

        volume = self.volume

        volume_oi_ratio = self.volume_oi_ratio

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if alert_rule is not UNSET:
            field_dict["alert_rule"] = alert_rule
        if all_opening_trades is not UNSET:
            field_dict["all_opening_trades"] = all_opening_trades
        if created_at is not UNSET:
            field_dict["created_at"] = created_at
        if expiry is not UNSET:
            field_dict["expiry"] = expiry
        if expiry_count is not UNSET:
            field_dict["expiry_count"] = expiry_count
        if has_floor is not UNSET:
            field_dict["has_floor"] = has_floor
        if has_multileg is not UNSET:
            field_dict["has_multileg"] = has_multileg
        if has_singleleg is not UNSET:
            field_dict["has_singleleg"] = has_singleleg
        if has_sweep is not UNSET:
            field_dict["has_sweep"] = has_sweep
        if open_interest is not UNSET:
            field_dict["open_interest"] = open_interest
        if option_chain is not UNSET:
            field_dict["option_chain"] = option_chain
        if price is not UNSET:
            field_dict["price"] = price
        if strike is not UNSET:
            field_dict["strike"] = strike
        if ticker is not UNSET:
            field_dict["ticker"] = ticker
        if total_ask_side_prem is not UNSET:
            field_dict["total_ask_side_prem"] = total_ask_side_prem
        if total_bid_side_prem is not UNSET:
            field_dict["total_bid_side_prem"] = total_bid_side_prem
        if total_premium is not UNSET:
            field_dict["total_premium"] = total_premium
        if total_size is not UNSET:
            field_dict["total_size"] = total_size
        if trade_count is not UNSET:
            field_dict["trade_count"] = trade_count
        if type is not UNSET:
            field_dict["type"] = type
        if underlying_price is not UNSET:
            field_dict["underlying_price"] = underlying_price
        if volume is not UNSET:
            field_dict["volume"] = volume
        if volume_oi_ratio is not UNSET:
            field_dict["volume_oi_ratio"] = volume_oi_ratio

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        alert_rule = d.pop("alert_rule", UNSET)

        all_opening_trades = d.pop("all_opening_trades", UNSET)

        created_at = d.pop("created_at", UNSET)

        expiry = d.pop("expiry", UNSET)

        expiry_count = d.pop("expiry_count", UNSET)

        has_floor = d.pop("has_floor", UNSET)

        has_multileg = d.pop("has_multileg", UNSET)

        has_singleleg = d.pop("has_singleleg", UNSET)

        has_sweep = d.pop("has_sweep", UNSET)

        open_interest = d.pop("open_interest", UNSET)

        option_chain = d.pop("option_chain", UNSET)

        price = d.pop("price", UNSET)

        strike = d.pop("strike", UNSET)

        ticker = d.pop("ticker", UNSET)

        total_ask_side_prem = d.pop("total_ask_side_prem", UNSET)

        total_bid_side_prem = d.pop("total_bid_side_prem", UNSET)

        total_premium = d.pop("total_premium", UNSET)

        total_size = d.pop("total_size", UNSET)

        trade_count = d.pop("trade_count", UNSET)

        _type = d.pop("type", UNSET)
        type: Union[Unset, OptionContractType]
        if isinstance(_type, Unset):
            type = UNSET
        else:
            type = OptionContractType(_type)

        underlying_price = d.pop("underlying_price", UNSET)

        volume = d.pop("volume", UNSET)

        volume_oi_ratio = d.pop("volume_oi_ratio", UNSET)

        flow_alert = cls(
            alert_rule=alert_rule,
            all_opening_trades=all_opening_trades,
            created_at=created_at,
            expiry=expiry,
            expiry_count=expiry_count,
            has_floor=has_floor,
            has_multileg=has_multileg,
            has_singleleg=has_singleleg,
            has_sweep=has_sweep,
            open_interest=open_interest,
            option_chain=option_chain,
            price=price,
            strike=strike,
            ticker=ticker,
            total_ask_side_prem=total_ask_side_prem,
            total_bid_side_prem=total_bid_side_prem,
            total_premium=total_premium,
            total_size=total_size,
            trade_count=trade_count,
            type=type,
            underlying_price=underlying_price,
            volume=volume,
            volume_oi_ratio=volume_oi_ratio,
        )

        flow_alert.additional_properties = d
        return flow_alert

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
