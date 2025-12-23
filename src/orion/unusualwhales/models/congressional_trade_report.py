from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.insider_trades_member_type import InsiderTradesMemberType
from ..models.insider_trades_transaction_type import InsiderTradesTransactionType
from ..types import UNSET, Unset

T = TypeVar("T", bound="CongressionalTradeReport")


@_attrs_define
class CongressionalTradeReport:
    """Congressional Stock Filings

    Example:
        {'data': [{'amounts': '$15,001 - $50,000', 'filed_at_date': datetime.date(2023, 2, 13), 'issuer': 'not-
            disclosed', 'member_type': 'house', 'notes': 'Subholding Of: Stephens Advantage Account Description: FT UNIT
            10479 PFD mutual-fund 32500', 'reporter': 'Stephen Cohen', 'ticker': 'FHWVOX', 'transaction_date':
            datetime.date(2023, 2, 6), 'txn_type': 'Buy'}]}

    Attributes:
        amounts (Union[Unset, str]): The reported amount range of the transaction. Example: $1,000 - $15,000.
        filed_at_date (Union[Unset, str]): The filing date as ISO date. Example: 2023-12-13.
        issuer (Union[Unset, str]): The person who executed the transaction. Example: spouse.
        member_type (Union[Unset, InsiderTradesMemberType]): The type of person who executed the transaction. Example:
            house.
        notes (Union[Unset, str]): Notes of the filing. Example: Subholding Of: Stephens Advantage Account Description:
            FT UNIT 10479 PFD mutual-fund 32500.
        reporter (Union[Unset, str]): The person who reported the transaction. Example: Stephen Cohen.
        ticker (Union[Unset, str]): The stock ticker. Example: AAPL.
        transaction_date (Union[Unset, str]): The transaction date as ISO date. Example: 2023-12-06.
        txn_type (Union[Unset, InsiderTradesTransactionType]): The transaction type. Example: Sell.
    """

    amounts: Union[Unset, str] = UNSET
    filed_at_date: Union[Unset, str] = UNSET
    issuer: Union[Unset, str] = UNSET
    member_type: Union[Unset, InsiderTradesMemberType] = UNSET
    notes: Union[Unset, str] = UNSET
    reporter: Union[Unset, str] = UNSET
    ticker: Union[Unset, str] = UNSET
    transaction_date: Union[Unset, str] = UNSET
    txn_type: Union[Unset, InsiderTradesTransactionType] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        amounts = self.amounts

        filed_at_date = self.filed_at_date

        issuer = self.issuer

        member_type: Union[Unset, str] = UNSET
        if not isinstance(self.member_type, Unset):
            member_type = self.member_type.value

        notes = self.notes

        reporter = self.reporter

        ticker = self.ticker

        transaction_date = self.transaction_date

        txn_type: Union[Unset, str] = UNSET
        if not isinstance(self.txn_type, Unset):
            txn_type = self.txn_type.value

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if amounts is not UNSET:
            field_dict["amounts"] = amounts
        if filed_at_date is not UNSET:
            field_dict["filed_at_date"] = filed_at_date
        if issuer is not UNSET:
            field_dict["issuer"] = issuer
        if member_type is not UNSET:
            field_dict["member_type"] = member_type
        if notes is not UNSET:
            field_dict["notes"] = notes
        if reporter is not UNSET:
            field_dict["reporter"] = reporter
        if ticker is not UNSET:
            field_dict["ticker"] = ticker
        if transaction_date is not UNSET:
            field_dict["transaction_date"] = transaction_date
        if txn_type is not UNSET:
            field_dict["txn_type"] = txn_type

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        amounts = d.pop("amounts", UNSET)

        filed_at_date = d.pop("filed_at_date", UNSET)

        issuer = d.pop("issuer", UNSET)

        _member_type = d.pop("member_type", UNSET)
        member_type: Union[Unset, InsiderTradesMemberType]
        if isinstance(_member_type, Unset):
            member_type = UNSET
        else:
            member_type = InsiderTradesMemberType(_member_type)

        notes = d.pop("notes", UNSET)

        reporter = d.pop("reporter", UNSET)

        ticker = d.pop("ticker", UNSET)

        transaction_date = d.pop("transaction_date", UNSET)

        _txn_type = d.pop("txn_type", UNSET)
        txn_type: Union[Unset, InsiderTradesTransactionType]
        if isinstance(_txn_type, Unset):
            txn_type = UNSET
        else:
            txn_type = InsiderTradesTransactionType(_txn_type)

        congressional_trade_report = cls(
            amounts=amounts,
            filed_at_date=filed_at_date,
            issuer=issuer,
            member_type=member_type,
            notes=notes,
            reporter=reporter,
            ticker=ticker,
            transaction_date=transaction_date,
            txn_type=txn_type,
        )

        congressional_trade_report.additional_properties = d
        return congressional_trade_report

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
