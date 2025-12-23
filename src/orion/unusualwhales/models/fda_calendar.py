from typing import Any, Dict, List, Type, TypeVar, Union

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="FdaCalendar")


@_attrs_define
class FdaCalendar:
    """The fda calendar for the current week

    Example:
        {'data': [{'catalyst': 'PDUFA Date', 'description': 'The FDA extended the new target action date for a decision
            under the Prescription Drug User Fee Act (PDUFA) to February 24, 2024, but agreed to work with Iovance to
            expedite the remaining review for a potentially earlier approval date.', 'drug': 'Lifileucel', 'end_date':
            datetime.date(2024, 2, 24), 'indication': 'for the Treatment of Advanced Melanoma', 'start_date':
            datetime.date(2024, 2, 24), 'status': 'NDA', 'ticker': 'IOVA'}, {'catalyst': '', 'description': 'FDA decision on
            Roluperidone for the treatment of negative symptoms in schizophrenia', 'drug': 'Roluperidone', 'end_date':
            datetime.date(2024, 2, 26), 'indication': '', 'start_date': datetime.date(2024, 2, 26), 'status': 'NDA',
            'ticker': 'NERV'}]}

    Attributes:
        catalyst (Union[Unset, str]): The kind of upcoming date causing the event. Example: PDUFA Date.
        description (Union[Unset, str]): The description of the event. Example: The FDA extended the new target action
            date for a decision under the Prescription Drug User Fee Act (PDUFA) to February 24, 2024, but agreed to work
            with Iovance to expedite the remaining review for a potentially earlier approval date..
        drug (Union[Unset, str]): The name of the drug. Example: Lifileucel.
        end_date (Union[Unset, str]): The event end date in ISO date format. Example: 2024-02-24.
        indication (Union[Unset, str]): The sickness or symptom the drug is used to treat. Example: for the Treatment of
            Advanced Melanoma.
        start_date (Union[Unset, str]): The event start date in ISO date format. Example: 2024-02-24.
        status (Union[Unset, str]): The status of the drug admission. Example: NDA.
        ticker (Union[Unset, str]): The ticker of the company applying for drug admission. Example: IOVA.
    """

    catalyst: Union[Unset, str] = UNSET
    description: Union[Unset, str] = UNSET
    drug: Union[Unset, str] = UNSET
    end_date: Union[Unset, str] = UNSET
    indication: Union[Unset, str] = UNSET
    start_date: Union[Unset, str] = UNSET
    status: Union[Unset, str] = UNSET
    ticker: Union[Unset, str] = UNSET
    additional_properties: Dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        catalyst = self.catalyst

        description = self.description

        drug = self.drug

        end_date = self.end_date

        indication = self.indication

        start_date = self.start_date

        status = self.status

        ticker = self.ticker

        field_dict: Dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if catalyst is not UNSET:
            field_dict["catalyst"] = catalyst
        if description is not UNSET:
            field_dict["description"] = description
        if drug is not UNSET:
            field_dict["drug"] = drug
        if end_date is not UNSET:
            field_dict["end_date"] = end_date
        if indication is not UNSET:
            field_dict["indication"] = indication
        if start_date is not UNSET:
            field_dict["start_date"] = start_date
        if status is not UNSET:
            field_dict["status"] = status
        if ticker is not UNSET:
            field_dict["ticker"] = ticker

        return field_dict

    @classmethod
    def from_dict(cls: Type[T], src_dict: Dict[str, Any]) -> T:
        d = src_dict.copy()
        catalyst = d.pop("catalyst", UNSET)

        description = d.pop("description", UNSET)

        drug = d.pop("drug", UNSET)

        end_date = d.pop("end_date", UNSET)

        indication = d.pop("indication", UNSET)

        start_date = d.pop("start_date", UNSET)

        status = d.pop("status", UNSET)

        ticker = d.pop("ticker", UNSET)

        fda_calendar = cls(
            catalyst=catalyst,
            description=description,
            drug=drug,
            end_date=end_date,
            indication=indication,
            start_date=start_date,
            status=status,
            ticker=ticker,
        )

        fda_calendar.additional_properties = d
        return fda_calendar

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
