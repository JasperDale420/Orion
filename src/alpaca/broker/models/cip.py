from datetime import datetime
from uuid import UUID

from alpaca.broker.enums import CIPApprovalStatus, CIPProvider, CIPResult, CIPStatus
from alpaca.common.models import ModelWithID
from alpaca.common.models import ValidateBaseModel as BaseModel


class CIPKYCInfo(BaseModel):
    """
    Represents Know Your Customer (KYC) info for a CIPInfo

    Attributes:
        id (str): Your internal ID of check
        risk_score (Optional[int]): Overall risk score returned by KYC provider or assessed
        risk_level (Optional[str]): Overall risk level returned by KYC provider or assessed
        risk_categories (Optional[List[str]]): The list of risk categories returned by the KYC provider or assessed
        applicant_name (Optional[str]): Given and family name of applicant
        email_address (Optional[str]): email address of applicant
        nationality (Optional[str]): nationality of applicant
        date_of_birth (Optional[datetime]): DOB of applicant
        address (Optional[str]): Concatenated street address, city, state and country of applicant
        postal_code (Optional[str]): postal code for `address` field
        country_of_residency (Optional[str]): country for `address` field
        kyc_completed_at (Optional[datetime]): Datetime that KYC check was completed at
        ip_address (Optional[str]): ip address of applicant at time of KYC check
        check_initiated_at (Optional[datetime]): start datetime of KYC check
        check_completed_at (Optional[datetime]): completion datetime of KYC check
        approval_status (Optional[CIPApprovalStatus]): Approval status of KYC check
        approved_by (Optional[str]): Identifier of who approved KYC check
        approved_reason (Optional[str]): Reason for approving this KYC check
        approved_at (Optional[datetime]): Datetime that this KYC check was approved
    """

    id: str
    risk_score: int | None = None
    risk_level: str | None = None
    risk_categories: list[str] | None = None
    applicant_name: str | None = None
    email_address: str | None = None
    nationality: str | None = None
    date_of_birth: datetime | None = None
    address: str | None = None
    postal_code: str | None = None
    country_of_residency: str | None = None
    kyc_completed_at: datetime | None = None
    ip_address: str | None = None
    check_initiated_at: datetime | None = None
    check_completed_at: datetime | None = None
    approval_status: CIPApprovalStatus | None = None
    approved_by: str | None = None
    approved_reason: str | None = None
    approved_at: datetime | None = None


class CIPDocument(BaseModel):
    """
    Represents results of checking a document for CIPInfo

    Attributes:
        id (str): Your internal ID of check
        result (Optional[CIPResult]): Overall result of specific check
        status (Optional[CIPStatus]): Overall status of specific check
        created_at (Optional[datetime]): Datetime for when this check was done
        date_of_birth (Optional[datetime]): DOB for applicant if found on document
        date_of_expiry (Optional[datetime]): date of expiry for the checked document
        document_numbers (Optional[List[str]]): Number of the document that was checked
        document_type (Optional[str]): Type of the document that was checked
        first_name (Optional[str]): First name extracted from the document
        last_name (Optional[str]): Last name extracted from the document
        gender (Optional[str]): Gender info extracted from the document
        issuing_country (Optional[str]): Country for which issued the document
        nationality (Optional[str]): Nationality extracted from the document
        age_validation (Optional[CIPResult]): Result of checks on whether the age calculated from the document’s date
          of birth data point is greater than or equal to the minimum accepted age set at account level
        compromised_document (Optional[CIPResult]): Result of check on whether the image of the document has been found
          in our internal database of compromised documents
        police_record (Optional[CIPStatus]): Result of check on whether the document has been identified as lost,
          stolen or otherwise compromised
        data_comparison (Optional[CIPResult]): Result of check on whether data on the document is consistent with data
          provided when creating an applicant through the API
        data_comparison_breakdown (Optional[str]): json object representing the results of the various sub-checks
          done when calculating the result on `data_comparison`. Example: {“date_of_birth”: “clear”,
          “date_of_expiry”: “clear” “document_numbers”: “clear”, “document_type”: “clear”, “first_name”: “clear”,
          “gender”: “clear”, “issuing_country”: “clear”, “last_name”: “clear”}
        image_integrity (Optional[CIPResult]): Result of checks on whether the document was of sufficient quality to
          verify
        image_integrity_breakdown (Optional[str]): json object representing the results of the various sub-checks done
          when calculating the result on `image_integrity`. Example: example: {“colour_picture”: “clear”,
          “conclusive_document_quality”: “clear”, “image_quality”: “clear”, “supported_document”: “clear”}
        visual_authenticity (Optional[str]): json object representing the the various sub-checks done when determening
          whether visual (non-textual) elements are correct given the document type. Example: {
          “digital_tampering”: “clear”, “face_detection”: “clear”, “fonts”: “clear”, “original_document_present”:
          “clear”, “picture_face_integrity”: “clear”, “security_features”: “clear”, “template”: “clear”}

    """

    id: str
    result: CIPResult | None = None
    status: CIPStatus | None = None
    created_at: datetime | None = None
    date_of_birth: datetime | None = None
    date_of_expiry: datetime | None = None
    document_numbers: list[str] | None = None
    document_type: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    gender: str | None = None
    issuing_country: str | None = None
    nationality: str | None = None
    age_validation: CIPResult | None = None
    compromised_document: CIPResult | None = None
    police_record: CIPStatus | None = None
    data_comparison: CIPResult | None = None
    data_comparison_breakdown: str | None = None
    image_integrity: CIPResult | None = None
    image_integrity_breakdown: str | None = None
    visual_authenticity: str | None = None


class CIPPhoto(BaseModel):
    """
    Represents the results of checking a Photo for CIPInfo

    Attributes:
        id (str): Your internal ID of check
        result (Optional[CIPResult]): Overall result of check
        status (Optional[CIPStatus]): Overall status of check
        created_at (Optional[datetime]): datetime of when check happened
        face_comparision (Optional[CIPResult]): Checks whether the face in the document matches the face in the
          live photo
        face_comparison_breakdown (Optional[str]): a json object representing the breakdown of sub-checks done in
          `face_comparison`. Example: {“face_match”:{“result”: “clear”,“properties”:{“score”: “80”}}}
        image_integrity (Optional[CIPResult]): Checks whether the quality and integrity of the uploaded files were
          sufficient to perform a face comparison
        image_integrity_breakdown (Optional[str]): a json object representing the breakdown of sub-checks done in
          `image_integrity`. Example  {“face_detected”:{“result”: “clear”},“source_integrity”: {“result”: “clear”}}
        visual_authenticity (Optional[CIPResult]): Checks whether the person in the live photo is real (not a spoof)
        visual_authenticity_breakdown (Optional[str]): a json object representing the breakdown of sub-checks don in
          `visual_authenticity`. Example {“spoofing_detection”: {“result”: “clear”,“properties”: {“score”: “26”}}}}
    """

    id: str
    result: CIPResult | None = None
    status: CIPStatus | None = None
    created_at: datetime | None = None
    face_comparision: CIPResult | None = None
    face_comparison_breakdown: str | None = None
    image_integrity: CIPResult | None = None
    image_integrity_breakdown: str | None = None
    visual_authenticity: CIPResult | None = None
    visual_authenticity_breakdown: str | None = None


class CIPIdentity(BaseModel):
    """
    Represents the results of running an identity check for a CIPInfo

    Attributes:
        id (str): Your internal ID of check
        result (Optional[CIPResult]): Overall result of check
        status (Optional[CIPStatus]): Overall status of check
        created_at (Optional[datetime]): datetime when identity check happened
        matched_address (Optional[CIPResult]): Represents of the address matched for the applicant
        matched_addresses (Optional[str]): a json object representing the results of the check done in `matched_address`
          Example: [{“id”: “19099121”,“match_types”:[“credit_agencies”,“voting_register”]}]
        sources (Optional[CIPResult]):  Shows the total number of sources found for applicant’s identity.
          (TODO: What? This doesnt make any sense its a CIPResult not a number but that's whats in the docs)
        sources_breakdown (Optional[str]): a json object representing the breakdown of `sources` field. For example:
          {“total_sources”: {“result”: “clear”,“properties”: {“total_number_of_sources”: “3”}}}
        address (Optional[CIPResult]): Result if it was cleared against a data source
        address_breakdown (Optional[str]): a json object representing the breakdown of the `address` field. For example:
          {“credit_agencies”: {“result”: “clear”,“properties”:{“number_of_matches”:“1”}}
        date_of_birth (Optional[CIPResult]): Result if it was cleared against a data source
        date_of_birth_breakdown (Optional[str]): a json object representing the breakdown of the `date_of_birth` field.
          For example: example: {“credit_agencies”:{“result”: “clear”,“properties”: {“number_of_matches”: “1”}}
        tax_id (Optional[CIPResult]): Result if it was cleared against a data source
        tax_id_breakdown (Optional[str]): a json object representing the breakdown of the `tax_id` field
    """

    id: str
    result: CIPResult | None = None
    status: CIPStatus | None = None
    created_at: datetime | None = None
    matched_address: CIPResult | None = None
    matched_addresses: str | None = None
    sources: CIPResult | None = None
    sources_breakdown: str | None = None
    address: CIPResult | None = None
    address_breakdown: str | None = None
    date_of_birth: CIPResult | None = None
    date_of_birth_breakdown: str | None = None
    tax_id: CIPResult | None = None
    tax_id_breakdown: str | None = None


class CIPWatchlist(BaseModel):
    """
    Represents the result of checking to see if the applicant is in any watchlists for a CIPInfo

    TODO: We're missing almost entirely documentation in prod for this as well as even internal documentation
      no clue what these fields are supposed to be or if they're even close to correct.

    Attributes:
        id (str): Your internal ID of check
        result (Optional[CIPResult]): Overall result of specific check
        status (Optional[CIPStatus]): Overall status of specific check
        created_at (Optional[datetime]): datetime when check happened
        records (Optional[str]): a json object. Example [{“text”: “Record info”}]
        politically_exposed_person (Optional[CIPResult]): Result if it was cleared against a data source
        sanction (Optional[CIPResult]): Result if it was cleared against a data source
        adverse_media (Optional[CIPResult]): Result if it was cleared against a data source
        monitored_lists (Optional[CIPResult]): Result if it was cleared against a data source
    """

    id: str
    result: CIPResult | None = None
    status: CIPStatus | None = None
    created_at: datetime | None = None
    records: str | None = None
    politically_exposed_person: CIPResult | None = None
    sanction: CIPResult | None = None
    adverse_media: CIPResult | None = None
    monitored_lists: CIPResult | None = None


class CIPInfo(ModelWithID):
    """
    The Customer Identification Program (CIP) API allows you to submit the CIP results received from your KYC provider.

    This model is how to represent that information when talking to Alpaca

    Args:
        id (UUID): ID of this CIPInfo
        account_id (UUID): UUID of the Account instance this CIPInfo is for
        provider_name (List[CIPProvider]): List of KYC providers this information came from
        created_at (datetime): date and time this CIPInfo was first uploaded to Alpaca
        updated_at (datetime): date and time that this CIPInfo was last update
        kyc (Optional[CIPKYCInfo]): KYC info for this Account if any
        document (Optional[CIPDocument]): Any CIP documents uploaded for this Account
        photo (Optional[CIPPhoto]): Any photos attached for CIP
        identity (Optional[CIPIdentity]): Any CIP Identity information
        watchlist (Optional[CIPWatchlist]): Any CIP watchlist information
    """

    account_id: UUID
    provider_name: list[CIPProvider]
    created_at: datetime
    updated_at: datetime
    kyc: CIPKYCInfo | None = None
    document: CIPDocument | None = None
    photo: CIPPhoto | None = None
    identity: CIPIdentity | None = None
    watchlist: CIPWatchlist | None = None

    def __init__(self, *args, **kwargs):
        # upcast into uuid
        if "id" in kwargs and type(kwargs["id"]) == str:
            kwargs["id"] = UUID(kwargs["id"])

        if "account_id" in kwargs and type(kwargs["account_id"]) == str:
            kwargs["account_id"] = UUID(kwargs["account_id"])

        super().__init__(*args, **kwargs)
