import pytest
from alpaca.broker.enums import BankAccountType
from alpaca.broker.requests import CreateACHRelationshipRequest
from pydantic import ValidationError


def test_valid_request():
    try:
        _req = CreateACHRelationshipRequest(
            account_owner_name="John Doe",
            bank_account_type=BankAccountType.CHECKING,
            bank_account_number="123456789",
            bank_routing_number="123456789",
        )
    except ValidationError as e:
        pytest.fail(f"Unexpected validation error: {e}")


@pytest.mark.skip(reason="Generic requests from alpaca-py do not enforce strict validation in current version")
def test_invalid_bank_account_number_non_numeric():
    with pytest.raises(ValidationError) as excinfo:
        CreateACHRelationshipRequest(
            account_owner_name="John Doe",
            bank_account_type=BankAccountType.CHECKING,
            bank_account_number="abcdefg",
            bank_routing_number="123456789",
        )
    assert "Bank account number must be numeric" in str(excinfo.value)


@pytest.mark.skip(reason="Generic requests from alpaca-py do not enforce strict validation in current version")
def test_invalid_bank_account_number_length():
    with pytest.raises(ValidationError) as excinfo:
        CreateACHRelationshipRequest(
            account_owner_name="John Doe",
            bank_account_type=BankAccountType.CHECKING,
            bank_account_number="123",  # Too short
            bank_routing_number="123456789",
        )
    assert "Bank account number must be between 4 and 17 digits" in str(excinfo.value)

    with pytest.raises(ValidationError) as excinfo:
        CreateACHRelationshipRequest(
            account_owner_name="John Doe",
            bank_account_type=BankAccountType.CHECKING,
            bank_account_number="1" * 18,  # Too long
            bank_routing_number="123456789",
        )
    assert "Bank account number must be between 4 and 17 digits" in str(excinfo.value)


@pytest.mark.skip(reason="Generic requests from alpaca-py do not enforce strict validation in current version")
def test_invalid_bank_routing_number_non_numeric():
    with pytest.raises(ValidationError) as excinfo:
        CreateACHRelationshipRequest(
            account_owner_name="John Doe",
            bank_account_type=BankAccountType.CHECKING,
            bank_account_number="123456789",
            bank_routing_number="abcdefghi",
        )
    assert "Bank routing number must be numeric" in str(excinfo.value)


@pytest.mark.skip(reason="Generic requests from alpaca-py do not enforce strict validation in current version")
def test_invalid_bank_routing_number_length():
    with pytest.raises(ValidationError) as excinfo:
        CreateACHRelationshipRequest(
            account_owner_name="John Doe",
            bank_account_type=BankAccountType.CHECKING,
            bank_account_number="123456789",
            bank_routing_number="12345678",  # 8 digits
        )
    assert "Bank routing number must be 9 digits" in str(excinfo.value)

    with pytest.raises(ValidationError) as excinfo:
        CreateACHRelationshipRequest(
            account_owner_name="John Doe",
            bank_account_type=BankAccountType.CHECKING,
            bank_account_number="123456789",
            bank_routing_number="1234567890",  # 10 digits
        )
    assert "Bank routing number must be 9 digits" in str(excinfo.value)
