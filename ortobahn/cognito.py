"""AWS Cognito helpers for email/password authentication."""

from __future__ import annotations

import logging

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger("ortobahn.cognito")


class CognitoError(Exception):
    """Raised for Cognito operation failures."""

    def __init__(self, message: str, code: str = ""):
        self.message = message
        self.code = code
        super().__init__(message)


class CognitoClient:
    """Thin wrapper around boto3 cognito-idp."""

    def __init__(self, user_pool_id: str, client_id: str, region: str = "us-west-2"):
        self.user_pool_id = user_pool_id
        self.client_id = client_id
        self.idp = boto3.client("cognito-idp", region_name=region)

    def sign_up(self, email: str, password: str, client_id: str) -> str:
        """Register a new user. Returns the Cognito 'sub' (user UUID)."""
        try:
            resp = self.idp.sign_up(
                ClientId=self.client_id,
                Username=email,
                Password=password,
                UserAttributes=[
                    {"Name": "email", "Value": email},
                    {"Name": "custom:client_id", "Value": client_id},
                ],
            )
            return resp["UserSub"]
        except ClientError as e:
            code = e.response["Error"]["Code"]
            msg = e.response["Error"]["Message"]
            if code == "UsernameExistsException":
                raise CognitoError("An account with this email already exists.", code) from e
            if code == "InvalidPasswordException":
                raise CognitoError(msg, code) from e
            logger.error("Cognito sign_up failed: %s %s", code, msg)
            raise CognitoError(msg, code) from e

    def confirm_sign_up(self, email: str, code: str) -> None:
        """Confirm a user's email with the verification code."""
        try:
            self.idp.confirm_sign_up(
                ClientId=self.client_id,
                Username=email,
                ConfirmationCode=code,
            )
        except ClientError as e:
            raise CognitoError(
                e.response["Error"]["Message"],
                e.response["Error"]["Code"],
            ) from e

    def login(self, email: str, password: str) -> dict:
        """Authenticate with email/password. Returns Cognito tokens dict."""
        try:
            resp = self.idp.initiate_auth(
                ClientId=self.client_id,
                AuthFlow="USER_PASSWORD_AUTH",
                AuthParameters={"USERNAME": email, "PASSWORD": password},
            )
            return resp["AuthenticationResult"]
        except ClientError as e:
            code = e.response["Error"]["Code"]
            msg = e.response["Error"]["Message"]
            if code in ("NotAuthorizedException", "UserNotFoundException"):
                raise CognitoError("Invalid email or password.", code) from e
            if code == "UserNotConfirmedException":
                raise CognitoError("Please verify your email first.", code) from e
            logger.error("Cognito login failed: %s %s", code, msg)
            raise CognitoError(msg, code) from e

    def forgot_password(self, email: str) -> None:
        """Initiate forgot-password flow (sends code to email)."""
        try:
            self.idp.forgot_password(ClientId=self.client_id, Username=email)
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "UserNotFoundException":
                return  # Don't reveal whether email exists
            raise CognitoError(e.response["Error"]["Message"], code) from e

    def confirm_forgot_password(self, email: str, code: str, new_password: str) -> None:
        """Complete forgot-password with verification code."""
        try:
            self.idp.confirm_forgot_password(
                ClientId=self.client_id,
                Username=email,
                ConfirmationCode=code,
                Password=new_password,
            )
        except ClientError as e:
            raise CognitoError(
                e.response["Error"]["Message"],
                e.response["Error"]["Code"],
            ) from e

    def resend_confirmation(self, email: str) -> None:
        """Resend the email verification code."""
        try:
            self.idp.resend_confirmation_code(ClientId=self.client_id, Username=email)
        except ClientError as e:
            raise CognitoError(
                e.response["Error"]["Message"],
                e.response["Error"]["Code"],
            ) from e

    def admin_create_user(self, email: str, client_id: str) -> str:
        """Admin-create a user (for migration). Returns sub."""
        try:
            resp = self.idp.admin_create_user(
                UserPoolId=self.user_pool_id,
                Username=email,
                UserAttributes=[
                    {"Name": "email", "Value": email},
                    {"Name": "email_verified", "Value": "true"},
                    {"Name": "custom:client_id", "Value": client_id},
                ],
                MessageAction="SUPPRESS",
            )
            attrs = {a["Name"]: a["Value"] for a in resp["User"]["Attributes"]}
            return attrs["sub"]
        except ClientError as e:
            raise CognitoError(
                e.response["Error"]["Message"],
                e.response["Error"]["Code"],
            ) from e

    def admin_set_password(self, email: str, password: str) -> None:
        """Admin-set a permanent password (for migration)."""
        try:
            self.idp.admin_set_user_password(
                UserPoolId=self.user_pool_id,
                Username=email,
                Password=password,
                Permanent=True,
            )
        except ClientError as e:
            raise CognitoError(
                e.response["Error"]["Message"],
                e.response["Error"]["Code"],
            ) from e
