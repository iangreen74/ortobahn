from __future__ import annotations

import re
from enum import Enum
from typing import Pattern

from pydantic import BaseModel, Field


class FailureType(str, Enum):
    """Types of failures that can be detected in CI logs."""

    FLAKY_TEST = "flaky_test"
    OUT_OF_MEMORY = "out_of_memory"
    DEPENDENCY_CONFLICT = "dependency_conflict"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    UNKNOWN = "unknown"


class DetectedFailure(BaseModel):
    """A detected failure from CI logs."""

    failure_type: FailureType
    message: str
    line_number: int
    confidence: float = Field(ge=0.0, le=1.0)
    context: dict[str, str] = Field(default_factory=dict)


class FailurePattern(BaseModel):
    """A pattern for matching failures in logs."""

    failure_type: FailureType
    patterns: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    extract_context: bool = False


class FailureDetector:
    """Detects common failures from CI logs using pattern matching."""

    def __init__(self) -> None:
        """Initialize the failure detector with default patterns."""
        self._patterns: dict[FailureType, list[tuple[Pattern[str], float]]] = {}
        self._detection_history: list[DetectedFailure] = []
        self._initialize_default_patterns()

    def _initialize_default_patterns(self) -> None:
        """Set up default failure detection patterns."""
        default_patterns = [
            FailurePattern(
                failure_type=FailureType.FLAKY_TEST,
                patterns=[
                    r"test.*failed.*intermittent",
                    r"FLAKY.*test",
                    r"test.*passed on retry",
                    r"AssertionError.*random",
                ],
                confidence=0.8,
            ),
            FailurePattern(
                failure_type=FailureType.OUT_OF_MEMORY,
                patterns=[
                    r"OutOfMemoryError",
                    r"MemoryError",
                    r"Killed.*OOM",
                    r"fatal: Out of memory",
                    r"Cannot allocate memory",
                ],
                confidence=0.95,
            ),
            FailurePattern(
                failure_type=FailureType.DEPENDENCY_CONFLICT,
                patterns=[
                    r"conflict.*dependency",
                    r"version conflict",
                    r"incompatible.*version",
                    r"ResolutionImpossible",
                    r"cannot install.*conflict",
                ],
                confidence=0.9,
            ),
            FailurePattern(
                failure_type=FailureType.TIMEOUT,
                patterns=[
                    r"TimeoutError",
                    r"timed out",
                    r"exceeded.*timeout",
                    r"deadline exceeded",
                ],
                confidence=0.85,
            ),
            FailurePattern(
                failure_type=FailureType.NETWORK_ERROR,
                patterns=[
                    r"ConnectionError",
                    r"network.*unreachable",
                    r"DNS.*failed",
                    r"connection refused",
                    r"HTTPError.*50[0-9]",
                ],
                confidence=0.8,
            ),
        ]

        for pattern_config in default_patterns:
            compiled_patterns = [
                (re.compile(p, re.IGNORECASE), pattern_config.confidence)
                for p in pattern_config.patterns
            ]
            self._patterns[pattern_config.failure_type] = compiled_patterns

    def detect(self, log_content: str) -> list[DetectedFailure]:
        """Detect failures in log content.

        Args:
            log_content: The CI log content to analyze

        Returns:
            List of detected failures
        """
        detected_failures: list[DetectedFailure] = []
        lines = log_content.split("\n")

        for line_number, line in enumerate(lines, start=1):
            for failure_type, patterns in self._patterns.items():
                for pattern, confidence in patterns:
                    if pattern.search(line):
                        failure = DetectedFailure(
                            failure_type=failure_type,
                            message=line.strip(),
                            line_number=line_number,
                            confidence=confidence,
                            context={"matched_pattern": pattern.pattern},
                        )
                        detected_failures.append(failure)
                        self._detection_history.append(failure)
                        break

        return detected_failures

    def learn_from_feedback(self, failure: DetectedFailure, was_correct: bool) -> None:
        """Update pattern confidence based on feedback.

        Args:
            failure: The detected failure
            was_correct: Whether the detection was correct
        """
        if failure.failure_type not in self._patterns:
            return

        adjustment = 0.05 if was_correct else -0.05
        patterns = self._patterns[failure.failure_type]
        updated_patterns = [
            (pattern, min(1.0, max(0.1, conf + adjustment)))
            for pattern, conf in patterns
        ]
        self._patterns[failure.failure_type] = updated_patterns
