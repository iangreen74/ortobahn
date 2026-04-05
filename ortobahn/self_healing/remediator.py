from __future__ import annotations

import asyncio
from enum import Enum
from typing import Callable

from pydantic import BaseModel, Field

from ortobahn.self_healing.detector import DetectedFailure, FailureType


class RemediationAction(str, Enum):
    """Types of remediation actions."""

    RETRY_WITH_BACKOFF = "retry_with_backoff"
    INCREASE_MEMORY = "increase_memory"
    UPDATE_DEPENDENCIES = "update_dependencies"
    INCREASE_TIMEOUT = "increase_timeout"
    RETRY_NETWORK = "retry_network"
    NO_ACTION = "no_action"


class RemediationResult(BaseModel):
    """Result of a remediation attempt."""

    action: RemediationAction
    success: bool
    message: str
    metadata: dict[str, str | int | float] = Field(default_factory=dict)


class RemediationStrategy(BaseModel):
    """Strategy for remediating a specific failure type."""

    failure_type: FailureType
    action: RemediationAction
    max_retries: int = 3
    backoff_factor: float = 2.0
    parameters: dict[str, str | int | float] = Field(default_factory=dict)


class Remediator:
    """Implements auto-remediation for detected failures."""

    def __init__(self) -> None:
        """Initialize the remediator with default strategies."""
        self._strategies: dict[FailureType, RemediationStrategy] = {}
        self._remediation_history: list[RemediationResult] = []
        self._initialize_default_strategies()

    def _initialize_default_strategies(self) -> None:
        """Set up default remediation strategies."""
        self._strategies = {
            FailureType.FLAKY_TEST: RemediationStrategy(
                failure_type=FailureType.FLAKY_TEST,
                action=RemediationAction.RETRY_WITH_BACKOFF,
                max_retries=3,
                backoff_factor=2.0,
            ),
            FailureType.OUT_OF_MEMORY: RemediationStrategy(
                failure_type=FailureType.OUT_OF_MEMORY,
                action=RemediationAction.INCREASE_MEMORY,
                max_retries=2,
                parameters={"memory_increment_mb": 512},
            ),
            FailureType.DEPENDENCY_CONFLICT: RemediationStrategy(
                failure_type=FailureType.DEPENDENCY_CONFLICT,
                action=RemediationAction.UPDATE_DEPENDENCIES,
                max_retries=2,
            ),
            FailureType.TIMEOUT: RemediationStrategy(
                failure_type=FailureType.TIMEOUT,
                action=RemediationAction.INCREASE_TIMEOUT,
                max_retries=2,
                parameters={"timeout_multiplier": 1.5},
            ),
            FailureType.NETWORK_ERROR: RemediationStrategy(
                failure_type=FailureType.NETWORK_ERROR,
                action=RemediationAction.RETRY_NETWORK,
                max_retries=3,
                backoff_factor=1.5,
            ),
        }

    async def remediate(
        self, failure: DetectedFailure, action_executor: Callable[[RemediationAction, dict], bool] | None = None
    ) -> RemediationResult:
        """Apply remediation for a detected failure.

        Args:
            failure: The detected failure to remediate
            action_executor: Optional custom executor for actions

        Returns:
            Result of the remediation attempt
        """
        strategy = self._strategies.get(failure.failure_type)
        if not strategy:
            return RemediationResult(
                action=RemediationAction.NO_ACTION,
                success=False,
                message=f"No strategy for {failure.failure_type}",
            )

        for attempt in range(strategy.max_retries):
            await asyncio.sleep(strategy.backoff_factor**attempt)

            if action_executor:
                success = action_executor(strategy.action, strategy.parameters)
            else:
                success = await self._default_executor(strategy.action, strategy.parameters)

            if success:
                result = RemediationResult(
                    action=strategy.action,
                    success=True,
                    message=f"Remediation successful on attempt {attempt + 1}",
                    metadata={"attempts": attempt + 1},
                )
                self._remediation_history.append(result)
                return result

        result = RemediationResult(
            action=strategy.action,
            success=False,
            message=f"Remediation failed after {strategy.max_retries} attempts",
            metadata={"attempts": strategy.max_retries},
        )
        self._remediation_history.append(result)
        return result

    async def _default_executor(self, action: RemediationAction, parameters: dict) -> bool:
        """Default executor for remediation actions.

        Args:
            action: The remediation action to execute
            parameters: Action-specific parameters

        Returns:
            True if action was successful
        """
        # Placeholder implementation - in production this would interact with CI/CD system
        return True
