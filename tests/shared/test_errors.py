from orion.core.errors import ErrorCode, OrionError, ProviderError


def test_error_code_enum():
    assert ErrorCode.PROVIDER_AUTH_FAILED.value == "PROVIDER_AUTH_FAILED"
    assert ErrorCode.UNKNOWN_ERROR.value == "UNKNOWN_ERROR"


def test_orion_error_structure():
    err = OrionError("test message", code=ErrorCode.VALIDATION_ERROR, details={"foo": "bar"})
    assert str(err) == "test message"
    assert err.code == ErrorCode.VALIDATION_ERROR.value
    assert err.details == {"foo": "bar"}


def test_provider_error_inheritance():
    err = ProviderError("api fail", code=ErrorCode.PROVIDER_TIMEOUT)
    assert isinstance(err, OrionError)
    assert err.code == ErrorCode.PROVIDER_TIMEOUT
