from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter, ValidationInfo, field_validator, model_validator

from alpaca.broker.enums import (
    AgreementType,
    ClearingBroker,
    EmploymentStatus,
    FundingSource,
    TaxIdType,
    VisaType,
)
from alpaca.broker.models.documents import AccountDocument
from alpaca.common.models import ModelWithID
from alpaca.common.models import ValidateBaseModel as BaseModel
from alpaca.trading.enums import AccountStatus
from alpaca.trading.models import TradeAccount as BaseTradeAccount


class KycResults(BaseModel):
    """
    Hold information about the result of KYC.
    ref. https://docs.alpaca.markets/reference/getaccount

    Attributes:
        reject (Optional[Dict[str, Any]]): The reason for the rejection
        accept (Optional[Dict[str, Any]]): The reason for the acceptance
        indeterminate (Optional[Dict[str, Any]]): The reason for the indeterminate result
        additional_information (Optional[str]): Used to display a custom message
        summary (Optional[str]): Either pass or fail. Used to indicate if KYC has completed and passed or not.
    """

    reject: dict[str, Any] | None = None
    accept: dict[str, Any] | None = None
    indeterminate: dict[str, Any] | None = None
    additional_information: str | None = None
    summary: str | None = None


class Contact(BaseModel):
    """User contact details within Account Model

    see https://alpaca.markets/docs/broker/api-references/accounts/accounts/#the-account-model

    Attributes:
        email_address (str): The user's email address
        phone_number (str): The user's phone number. It should include the country code.
        street_address (List[str]): The user's street address lines.
        unit (Optional[str]): The user's apartment unit, if any.
        city (str): The city the user resides in.
        state (Optional[str]): The state the user resides in. This is required if country is 'USA'.
        postal_code (str): The user's postal
        country (str): The country the user resides in. 3 letter country code is permissible.
    """

    email_address: str
    phone_number: str | None = None
    street_address: list[str]
    unit: str | None = None
    city: str
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None

    @field_validator("state")

    @classmethod
    def usa_state_has_value(cls, v: str, validation: ValidationInfo, **kwargs) -> str:
        """Validates that the state has a value if the country is USA

        Args:
            v (str): The state field's value
            values (dict): The values of each field

        Raises:
            ValueError: State is required for country USA

        Returns:
            str: The value of the state field
        """
        values: dict = validation.data
        if "country" in values and values["country"] == "USA" and v is None:
            raise ValueError("State is required for country USA.")
        return v


class Identity(BaseModel):
    """User identity details within Account Model

    see https://alpaca.markets/docs/broker/api-references/accounts/accounts/#the-account-model

    Attributes:
        given_name (str): The user's first name
        middle_name (Optional[str]): The user's middle name, if any
        family_name (str): The user's last name
        date_of_birth (str): The user's date of birth
        tax_id (Optional[str]): The user's country specific tax id, required if tax_id_type is provided
        tax_id_type (Optional[TaxIdType]): The tax_id_type for the tax_id provided, required if tax_id provided
        country_of_citizenship (Optional[str]): The country the user is a citizen
        country_of_birth (Optional[str]): The country the user was born
        country_of_tax_residence (str): The country the user files taxes
        visa_type (Optional[VisaType]): Only used to collect visa types for users residing in the USA.
        visa_expiration_date (Optional[str]): The date of expiration for visa, Required if visa_type is set.
        date_of_departure_from_usa (Optional[str]): Required if visa_type = B1 or B2
        permanent_resident (Optional[bool]): Only used to collect permanent residence status in the USA.
        funding_source (Optional[List[FundingSource]]): How the user will fund their account
        annual_income_min (Optional[float]): The minimum of the user's income range
        annual_income_max (Optional[float]): The maximum of the user's income range
        liquid_net_worth_min (Optional[float]): The minimum of the user's liquid net worth range
        liquid_net_worth_max (Optional[float]): The maximum of the user's liquid net worth range
        total_net_worth_min (Optional[float]): The minimum of the user's total net worth range
        total_net_worth_max (Optional[float]): The maximum of the user's total net worth range
    """

    given_name: str
    middle_name: str | None = None
    family_name: str
    date_of_birth: str | None = None
    tax_id: str | None = None
    tax_id_type: TaxIdType | None = None
    country_of_citizenship: str | None = None
    country_of_birth: str | None = None
    country_of_tax_residence: str
    visa_type: VisaType | None = None
    visa_expiration_date: str | None = None
    date_of_departure_from_usa: str | None = None
    permanent_resident: bool | None = None
    funding_source: list[FundingSource] | None = None
    annual_income_min: float | None = None
    annual_income_max: float | None = None
    liquid_net_worth_min: float | None = None
    liquid_net_worth_max: float | None = None
    total_net_worth_min: float | None = None
    total_net_worth_max: float | None = None


class Disclosures(BaseModel):
    """User disclosures within Account Model

    see https://alpaca.markets/docs/broker/api-references/accounts/accounts/#the-account-model

    Attributes:
        is_control_person (bool): Whether user holds a controlling position in a publicly traded company
        is_affiliated_exchange_or_finra (bool): If user is affiliated with any exchanges or FINRA
        is_politically_exposed (bool): If user is politically exposed
        immediate_family_exposed (bool): If user’s immediate family member is either politically exposed or holds a control position.
        employment_status (EmploymentStatus): The employment status of the user
        employer_name (str): The user's employer's name, if any
        employer_address (str): The user's employer's address, if any
        employment_position (str): The user's employment position, if any
    """

    is_control_person: bool | None = None
    is_affiliated_exchange_or_finra: bool | None = None
    is_politically_exposed: bool | None = None
    immediate_family_exposed: bool
    employment_status: EmploymentStatus | None = None
    employer_name: str | None = None
    employer_address: str | None = None
    employment_position: str | None = None


class Agreement(BaseModel):
    """User agreements signed within Account Model

    see https://alpaca.markets/docs/broker/api-references/accounts/accounts/#the-account-model

    Attributes:
        agreement (Agreement): The type of agreement signed by the user
        signed_at (str): The timestamp the agreement was signed
        ip_address (str): The ip_address the signed agreements were sent from by the user
        revision (str): The revision date
    """

    agreement: AgreementType
    signed_at: str
    ip_address: str
    revision: str | None = None


class TrustedContact(BaseModel):
    """User's trusted contact details within Account Model

    see https://alpaca.markets/docs/broker/api-references/accounts/accounts/#the-account-model

    Attributes:given_name
        given_name (str): The first name of the user's trusted contact
        family_name (str): The last name of the user's trusted contact
        email_address (Optional[str]): The email address of the user's trusted contact
        phone_number (Optional[str]): The email address of the user's trusted contact
        city (Optional[str]): The email address of the user's trusted contact
        state (Optional[str]): The email address of the user's trusted contact
        postal_code (Optional[str]): The email address of the user's trusted contact
        country (Optional[str]): The email address of the user's trusted contact
    """

    given_name: str
    family_name: str
    email_address: str | None = None
    phone_number: str | None = None
    street_address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None

    @model_validator(mode="before")

    @classmethod
    def root_validator(cls, values: dict) -> dict:
        has_phone_number = (
            "phone_number" in values and values["phone_number"] is not None
        )
        has_street_address = (
            "street_address" in values and values["street_address"] is not None
        )
        has_email_address = (
            "email_address" in values and values["email_address"] is not None
        )

        if has_phone_number or has_street_address or has_email_address:
            return values

        raise ValueError("At least one method of contact required for trusted contact")


class Account(ModelWithID):
    """Contains information pertaining to a specific brokerage account

    see https://alpaca.markets/docs/broker/api-references/accounts/accounts/#the-account-model

    The fields contact, identity, disclosures, agreements, documents, trusted_contact, and trading_configurations
    are all optional and won't always be provided by the api depending on what endpoint you use and what options you
    pass

    Attributes:
        id (str): The account uuid used to reference this account
        account_number (str): A more human friendly identifier for this account
        status (AccountStatus): The approval status of this account
        crypto_status (Optional[AccountStatus]): The crypto trading status. Only present if crypto trading is enabled.
        kyc_results (Optional[KycResult]): Hold information about the result of KYC.
        currency (str): The currency the account's values are returned in
        last_equity (str): The total equity value stored in the account
        created_at (str): The timestamp when the account was created
        contact (Optional[Contact]): The contact details for the account holder
        identity (Optional[Identity]): The identity details for the account holder
        disclosures (Optional[Disclosures]): The account holder's political disclosures
        agreements (Optional[List[Agreement]]): The agreements the account holder has signed
        documents (Optional[List[AccountDocument]]): The documents the account holder has submitted
        trusted_contact (Optional[TrustedContact]): The account holder's trusted contact details
    """

    account_number: str
    status: AccountStatus
    crypto_status: AccountStatus | None = None
    kyc_results: KycResults | None = None
    currency: str
    last_equity: str
    created_at: str
    contact: Contact | None = None
    identity: Identity | None = None
    disclosures: Disclosures | None = None
    agreements: list[Agreement] | None = None
    documents: list[AccountDocument] | None = None
    trusted_contact: TrustedContact | None = None

    def __init__(self, **response):
        super().__init__(
            id=(UUID(response["id"])),
            account_number=(response["account_number"]),
            status=(response["status"]),
            crypto_status=(
                response.get("crypto_status")
            ),
            kyc_results=(
                TypeAdapter(KycResults).validate_python(response["kyc_results"])
                if "kyc_results" in response and response["kyc_results"] is not None
                else None
            ),
            currency=(response["currency"]),
            last_equity=(response["last_equity"]),
            created_at=(response["created_at"]),
            contact=(
                TypeAdapter(Contact).validate_python(response["contact"])
                if "contact" in response
                else None
            ),
            identity=(
                TypeAdapter(Identity).validate_python(response["identity"])
                if "identity" in response
                else None
            ),
            disclosures=(
                TypeAdapter(Disclosures).validate_python(response["disclosures"])
                if "disclosures" in response
                else None
            ),
            agreements=(
                TypeAdapter(list[Agreement]).validate_python(response["agreements"])
                if "agreements" in response
                else None
            ),
            documents=(
                TypeAdapter(list[AccountDocument]).validate_python(
                    response["documents"]
                )
                if "documents" in response
                else None
            ),
            trusted_contact=(
                TypeAdapter(TrustedContact).validate_python(response["trusted_contact"])
                if "trusted_contact" in response
                else None
            ),
        )


class TradeAccount(BaseTradeAccount):
    """
    See Base TradeAccount model in common for full details on available fields.
    Represents trading account information for an Account.

    Attributes:
        cash_withdrawable (Optional[str]): Cash available for withdrawal from the account
        cash_transferable (Optional[str]): Cash available for transfer (JNLC) from the account
        previous_close (Optional[datetime]): Previous sessions close time
        last_long_market_value (Optional[str]): Value of all long positions as of previous trading day at 16:00:00 ET
        last_short_market_value (Optional[str]): Value of all short positions as of previous trading day at 16:00:00 ET
        last_cash (Optional[str]): Value of all cash as of previous trading day at 16:00:00 ET
        last_initial_margin (Optional[str]): Value of initial_margin as of previous trading day at 16:00:00 ET
        last_regt_buying_power (Optional[str]): Value of regt_buying_power as of previous trading day at 16:00:00 ET
        last_daytrading_buying_power (Optional[str]): Value of daytrading_buying_power as of previous trading day at 16:00:00 ET
        last_daytrade_count (Optional[int]): Value of daytrade_count as of previous trading day at 16:00:00 ET
        last_buying_power (Optional[str]): Value of buying_power as of previous trading day at 16:00:00 ET
        clearing_broker (Optional[ClearingBroker]): The Clearing broker for this account
    """

    cash_withdrawable: str | None
    cash_transferable: str | None
    previous_close: datetime | None
    last_long_market_value: str | None
    last_short_market_value: str | None
    last_cash: str | None
    last_initial_margin: str | None
    last_regt_buying_power: str | None
    last_daytrading_buying_power: str | None
    last_daytrade_count: int | None
    last_buying_power: str | None
    clearing_broker: ClearingBroker | None
