import pytest
from app.api.v1.auth import validate_auth_configuration


def test_production_cannot_use_public_development_signing_key():
    with pytest.raises(RuntimeError):
        validate_auth_configuration({"APP_ENV": "production"})
    with pytest.raises(RuntimeError):
        validate_auth_configuration({"APP_ENV": "production", "JWT_SECRET": "short"})
    validate_auth_configuration(
        {
            "APP_ENV": "production",
            "JWT_SECRET": "private-test-key-that-is-long-enough-123",
        }
    )
    validate_auth_configuration({})
