"""Integration adapter framework for marketing platforms."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel, Field, SecretStr


class IntegrationType(str, Enum):
    """Supported integration types."""

    GOOGLE_ADS = "google_ads"
    FACEBOOK_ADS = "facebook_ads"
    LINKEDIN_ADS = "linkedin_ads"


class OAuthConfig(BaseModel):
    """OAuth configuration for platform integrations."""

    client_id: str
    client_secret: SecretStr
    redirect_uri: str
    scope: list[str] = Field(default_factory=list)
    auth_url: str
    token_url: str


class AdapterCredentials(BaseModel):
    """Base credentials model for adapters."""

    integration_type: IntegrationType
    access_token: SecretStr | None = None
    refresh_token: SecretStr | None = None
    expires_at: int | None = None


class GoogleAdsCredentials(AdapterCredentials):
    """Google Ads specific credentials."""

    integration_type: IntegrationType = IntegrationType.GOOGLE_ADS
    customer_id: str | None = None
    developer_token: SecretStr | None = None


class FacebookAdsCredentials(AdapterCredentials):
    """Facebook Ads specific credentials."""

    integration_type: IntegrationType = IntegrationType.FACEBOOK_ADS
    ad_account_id: str | None = None


class LinkedInAdsCredentials(AdapterCredentials):
    """LinkedIn Ads specific credentials."""

    integration_type: IntegrationType = IntegrationType.LINKEDIN_ADS
    organization_id: str | None = None


class BaseAdapter(ABC):
    """Base class for marketing platform adapters."""

    integration_type: ClassVar[IntegrationType]
    oauth_config: ClassVar[OAuthConfig | None] = None

    def __init__(self, credentials: AdapterCredentials) -> None:
        """Initialize adapter with credentials."""
        self.credentials = credentials

    @abstractmethod
    async def authenticate(self) -> bool:
        """Authenticate with the platform."""
        pass

    @abstractmethod
    async def refresh_credentials(self) -> AdapterCredentials:
        """Refresh expired credentials."""
        pass

    @abstractmethod
    async def get_campaigns(self) -> list[dict[str, Any]]:
        """Fetch campaigns from the platform."""
        pass

    @abstractmethod
    async def get_campaign_metrics(self, campaign_id: str) -> dict[str, Any]:
        """Fetch metrics for a specific campaign."""
        pass


class GoogleAdsAdapter(BaseAdapter):
    """Google Ads platform adapter."""

    integration_type = IntegrationType.GOOGLE_ADS

    async def authenticate(self) -> bool:
        """Authenticate with Google Ads API."""
        # Implementation will be added in Task 5
        return False

    async def refresh_credentials(self) -> AdapterCredentials:
        """Refresh Google Ads OAuth credentials."""
        # Implementation will be added in Task 5
        return self.credentials

    async def get_campaigns(self) -> list[dict[str, Any]]:
        """Fetch campaigns from Google Ads."""
        # Implementation will be added in Task 5
        return []

    async def get_campaign_metrics(self, campaign_id: str) -> dict[str, Any]:
        """Fetch metrics for a Google Ads campaign."""
        # Implementation will be added in Task 5
        return {}


class FacebookAdsAdapter(BaseAdapter):
    """Facebook Ads platform adapter."""

    integration_type = IntegrationType.FACEBOOK_ADS

    async def authenticate(self) -> bool:
        """Authenticate with Facebook Marketing API."""
        # Implementation will be added in Task 5
        return False

    async def refresh_credentials(self) -> AdapterCredentials:
        """Refresh Facebook OAuth credentials."""
        # Implementation will be added in Task 5
        return self.credentials

    async def get_campaigns(self) -> list[dict[str, Any]]:
        """Fetch campaigns from Facebook Ads."""
        # Implementation will be added in Task 5
        return []

    async def get_campaign_metrics(self, campaign_id: str) -> dict[str, Any]:
        """Fetch metrics for a Facebook Ads campaign."""
        # Implementation will be added in Task 5
        return {}


class LinkedInAdsAdapter(BaseAdapter):
    """LinkedIn Ads platform adapter."""

    integration_type = IntegrationType.LINKEDIN_ADS

    async def authenticate(self) -> bool:
        """Authenticate with LinkedIn Marketing API."""
        # Implementation will be added in Task 5
        return False

    async def refresh_credentials(self) -> AdapterCredentials:
        """Refresh LinkedIn OAuth credentials."""
        # Implementation will be added in Task 5
        return self.credentials

    async def get_campaigns(self) -> list[dict[str, Any]]:
        """Fetch campaigns from LinkedIn Ads."""
        # Implementation will be added in Task 5
        return []

    async def get_campaign_metrics(self, campaign_id: str) -> dict[str, Any]:
        """Fetch metrics for a LinkedIn Ads campaign."""
        # Implementation will be added in Task 5
        return {}
