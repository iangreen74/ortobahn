"""Tests for AWS Cognito authentication wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from ortobahn.cognito import CognitoClient, CognitoError


def _client_error(code: str, message: str = "test error") -> ClientError:
    """Build a botocore ClientError with the given code and message."""
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        operation_name="TestOp",
    )


@pytest.fixture
def cognito():
    """CognitoClient with a fully mocked boto3 idp client."""
    with patch("ortobahn.cognito.boto3") as mock_boto:
        mock_idp = MagicMock()
        mock_boto.client.return_value = mock_idp
        client = CognitoClient(
            user_pool_id="us-west-2_TESTPOOL",
            client_id="test-client-id",
            region="us-west-2",
        )
        # Expose the mock for assertions
        client._mock_idp = mock_idp
        yield client


class TestSignUp:
    def test_success_returns_sub(self, cognito):
        cognito._mock_idp.sign_up.return_value = {
            "UserSub": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "UserConfirmed": False,
        }

        sub = cognito.sign_up("user@example.com", "Str0ng!Pass", "client-abc")

        assert sub == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        cognito._mock_idp.sign_up.assert_called_once_with(
            ClientId="test-client-id",
            Username="user@example.com",
            Password="Str0ng!Pass",
            UserAttributes=[
                {"Name": "email", "Value": "user@example.com"},
                {"Name": "custom:client_id", "Value": "client-abc"},
            ],
        )

    def test_duplicate_email_raises_cognito_error(self, cognito):
        cognito._mock_idp.sign_up.side_effect = _client_error(
            "UsernameExistsException",
            "An account with the given email already exists.",
        )

        with pytest.raises(CognitoError) as exc_info:
            cognito.sign_up("dup@example.com", "Str0ng!Pass", "client-abc")

        assert exc_info.value.code == "UsernameExistsException"
        assert "already exists" in exc_info.value.message

    def test_weak_password_raises_cognito_error(self, cognito):
        cognito._mock_idp.sign_up.side_effect = _client_error(
            "InvalidPasswordException",
            "Password does not meet requirements.",
        )

        with pytest.raises(CognitoError) as exc_info:
            cognito.sign_up("user@example.com", "weak", "client-abc")

        assert exc_info.value.code == "InvalidPasswordException"
        assert "Password" in exc_info.value.message


class TestLogin:
    def test_success_returns_tokens(self, cognito):
        tokens = {
            "IdToken": "id-token-value",
            "AccessToken": "access-token-value",
            "RefreshToken": "refresh-token-value",
            "ExpiresIn": 3600,
            "TokenType": "Bearer",
        }
        cognito._mock_idp.initiate_auth.return_value = {
            "AuthenticationResult": tokens,
        }

        result = cognito.login("user@example.com", "Str0ng!Pass")

        assert result == tokens
        assert result["AccessToken"] == "access-token-value"
        cognito._mock_idp.initiate_auth.assert_called_once_with(
            ClientId="test-client-id",
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": "user@example.com", "PASSWORD": "Str0ng!Pass"},
        )

    def test_bad_credentials_raises_cognito_error(self, cognito):
        cognito._mock_idp.initiate_auth.side_effect = _client_error(
            "NotAuthorizedException",
            "Incorrect username or password.",
        )

        with pytest.raises(CognitoError) as exc_info:
            cognito.login("user@example.com", "wrong-pass")

        assert exc_info.value.code == "NotAuthorizedException"
        assert "Invalid email or password" in exc_info.value.message

    def test_unconfirmed_user_raises_cognito_error(self, cognito):
        cognito._mock_idp.initiate_auth.side_effect = _client_error(
            "UserNotConfirmedException",
            "User is not confirmed.",
        )

        with pytest.raises(CognitoError) as exc_info:
            cognito.login("unverified@example.com", "Str0ng!Pass")

        assert exc_info.value.code == "UserNotConfirmedException"
        assert "verify your email" in exc_info.value.message


class TestConfirmSignUp:
    def test_success(self, cognito):
        cognito._mock_idp.confirm_sign_up.return_value = {}

        # Should not raise
        cognito.confirm_sign_up("user@example.com", "123456")

        cognito._mock_idp.confirm_sign_up.assert_called_once_with(
            ClientId="test-client-id",
            Username="user@example.com",
            ConfirmationCode="123456",
        )

    def test_invalid_code_raises_cognito_error(self, cognito):
        cognito._mock_idp.confirm_sign_up.side_effect = _client_error(
            "CodeMismatchException",
            "Invalid verification code provided.",
        )

        with pytest.raises(CognitoError) as exc_info:
            cognito.confirm_sign_up("user@example.com", "000000")

        assert exc_info.value.code == "CodeMismatchException"
        assert "Invalid verification code" in exc_info.value.message


class TestForgotPassword:
    def test_success(self, cognito):
        cognito._mock_idp.forgot_password.return_value = {
            "CodeDeliveryDetails": {
                "Destination": "u***@example.com",
                "DeliveryMedium": "EMAIL",
            },
        }

        # Should not raise
        result = cognito.forgot_password("user@example.com")

        assert result is None
        cognito._mock_idp.forgot_password.assert_called_once_with(
            ClientId="test-client-id",
            Username="user@example.com",
        )

    def test_nonexistent_user_does_not_raise(self, cognito):
        cognito._mock_idp.forgot_password.side_effect = _client_error(
            "UserNotFoundException",
            "Username/client id combination not found.",
        )

        # Should silently return None to avoid revealing whether email exists
        result = cognito.forgot_password("nobody@example.com")

        assert result is None


class TestConfirmForgotPassword:
    def test_success(self, cognito):
        cognito._mock_idp.confirm_forgot_password.return_value = {}

        # Should not raise
        cognito.confirm_forgot_password("user@example.com", "123456", "N3w!Passw0rd")

        cognito._mock_idp.confirm_forgot_password.assert_called_once_with(
            ClientId="test-client-id",
            Username="user@example.com",
            ConfirmationCode="123456",
            Password="N3w!Passw0rd",
        )


class TestAdminCreateUser:
    def test_success_returns_sub(self, cognito):
        cognito._mock_idp.admin_create_user.return_value = {
            "User": {
                "Username": "user@example.com",
                "Attributes": [
                    {"Name": "sub", "Value": "11111111-2222-3333-4444-555555555555"},
                    {"Name": "email", "Value": "user@example.com"},
                    {"Name": "email_verified", "Value": "true"},
                    {"Name": "custom:client_id", "Value": "client-xyz"},
                ],
                "Enabled": True,
                "UserStatus": "FORCE_CHANGE_PASSWORD",
            },
        }

        sub = cognito.admin_create_user("user@example.com", "client-xyz")

        assert sub == "11111111-2222-3333-4444-555555555555"
        cognito._mock_idp.admin_create_user.assert_called_once_with(
            UserPoolId="us-west-2_TESTPOOL",
            Username="user@example.com",
            UserAttributes=[
                {"Name": "email", "Value": "user@example.com"},
                {"Name": "email_verified", "Value": "true"},
                {"Name": "custom:client_id", "Value": "client-xyz"},
            ],
            MessageAction="SUPPRESS",
        )
