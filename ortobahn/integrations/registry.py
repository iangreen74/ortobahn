"""Adapter registry for managing integration adapters."""

from __future__ import annotations

from typing import Type

from ortobahn.integrations import (
    BaseAdapter,
    FacebookAdsAdapter,
    GoogleAdsAdapter,
    IntegrationType,
    LinkedInAdsAdapter,
)


class AdapterRegistry:
    """Registry for managing integration adapters."""

    def __init__(self) -> None:
        """Initialize adapter registry."""
        self._adapters: dict[IntegrationType, Type[BaseAdapter]] = {}
        self._register_builtin_adapters()

    def _register_builtin_adapters(self) -> None:
        """Register built-in platform adapters."""
        self.register(IntegrationType.GOOGLE_ADS, GoogleAdsAdapter)
        self.register(IntegrationType.FACEBOOK_ADS, FacebookAdsAdapter)
        self.register(IntegrationType.LINKEDIN_ADS, LinkedInAdsAdapter)

    def register(self, integration_type: IntegrationType, adapter_class: Type[BaseAdapter]) -> None:
        """Register an adapter class for an integration type.

        Args:
            integration_type: The type of integration
            adapter_class: The adapter class to register
        """
        if not issubclass(adapter_class, BaseAdapter):
            raise ValueError(f"Adapter class must inherit from BaseAdapter: {adapter_class}")
        self._adapters[integration_type] = adapter_class

    def get_adapter_class(self, integration_type: IntegrationType) -> Type[BaseAdapter]:
        """Get the adapter class for an integration type.

        Args:
            integration_type: The type of integration

        Returns:
            The adapter class

        Raises:
            KeyError: If no adapter is registered for the integration type
        """
        if integration_type not in self._adapters:
            raise KeyError(f"No adapter registered for integration type: {integration_type}")
        return self._adapters[integration_type]

    def list_supported_integrations(self) -> list[IntegrationType]:
        """List all supported integration types.

        Returns:
            List of supported integration types
        """
        return list(self._adapters.keys())

    def is_supported(self, integration_type: IntegrationType) -> bool:
        """Check if an integration type is supported.

        Args:
            integration_type: The type of integration

        Returns:
            True if the integration type is supported
        """
        return integration_type in self._adapters


# Global registry instance
_registry = AdapterRegistry()


def get_registry() -> AdapterRegistry:
    """Get the global adapter registry instance.

    Returns:
        The global adapter registry
    """
    return _registry
