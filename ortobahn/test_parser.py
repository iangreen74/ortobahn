"""Structured pytest error parser — extract assertion diffs, stack traces, and error categorization."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("ortobahn.test_parser")


@dataclass
class StackFrame:
    """A single frame from a Python traceback."""

    file_path: str
    line_number: int
    function_name: str
    code_line: str


@dataclass
class AssertionDiff:
    """Expected-vs-actual from a pytest assertion introspection."""

    expected: str
    actual: str
    diff_lines: list[str] = field(default_factory=list)


@dataclass
class ParsedTestError:
    """Structured representation of a single test failure."""

    test_name: str
    test_file: str
    error_type: str  # "assertion", "import", "attribute", "type", "runtime", "timeout", "fixture"
    error_message: str
    stack_frames: list[StackFrame] = field(default_factory=list)
    assertion_diff: AssertionDiff | None = None


# ---------------------------------------------------------------------------
# Error category keywords
# ---------------------------------------------------------------------------

_ERROR_CATEGORIES: list[tuple[str, list[str]]] = [
    ("assertion", ["AssertionError", "assert ", "AssertionError"]),
    ("import", ["ImportError", "ModuleNotFoundError"]),
    ("attribute", ["AttributeError"]),
    ("type", ["TypeError"]),
    ("fixture", ["fixture", "ERRORS", "fixture not found", "usefixtures"]),
    ("timeout", ["TimeoutError", "Timeout", "timed out"]),
    ("runtime", ["RuntimeError", "Exception", "Error"]),
]


class TestErrorParser:
    """Parse pytest output into structured error objects."""

    # Matches the FAILURES section header: "_ test_name _"
    _FAILURE_HEADER_RE = re.compile(r"^_{2,}\s+(.+?)\s+_{2,}$", re.MULTILINE)

    # Matches short test summary line: "FAILED tests/test_foo.py::test_bar - reason"
    _SHORT_SUMMARY_RE = re.compile(
        r"^FAILED\s+(\S+\.py)::(\S+?)(?:\s+-\s+(.+))?$",
        re.MULTILINE,
    )

    # Stack trace line: "file.py:line: in function_name"
    _TRACEBACK_RE = re.compile(
        r"^(\S+\.py):(\d+):\s+in\s+(\S+)\s*$",
        re.MULTILINE,
    )

    # Pytest E-line: "E       assert X == Y" or "E       AssertionError: ..."
    _E_LINE_RE = re.compile(r"^E\s+(.+)$", re.MULTILINE)

    # Assertion diff markers
    _EXPECTED_RE = re.compile(r"^E\s+(?:assert\s+)?(.+?)\s*==\s*(.+)$")
    _WHERE_RE = re.compile(r"^E\s+where\s+(.+)$")

    def parse(self, pytest_output: str) -> list[ParsedTestError]:
        """Parse full pytest output and return structured errors."""
        errors: list[ParsedTestError] = []

        # Split on failure headers
        failure_blocks = self._split_failure_blocks(pytest_output)

        for test_id, block in failure_blocks:
            test_file, test_name = self._parse_test_id(test_id)
            stack_frames = self.extract_stack_frames(block)
            e_lines = self._E_LINE_RE.findall(block)
            error_text = "\n".join(e_lines) if e_lines else ""
            error_type = self.categorize_error(error_text or block)
            assertion_diff = self.extract_assertion_diff(block)

            # If header had no file info, infer from stack frames
            if not test_file and stack_frames:
                for frame in stack_frames:
                    if frame.file_path.startswith("tests/") or "test_" in frame.file_path:
                        test_file = frame.file_path
                        break

            error_message = e_lines[0].strip() if e_lines else ""
            if not error_message:
                # Try to extract from the last line of the block
                lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
                error_message = lines[-1] if lines else ""

            errors.append(
                ParsedTestError(
                    test_name=f"{test_file}::{test_name}" if test_file else test_name,
                    test_file=test_file,
                    error_type=error_type,
                    error_message=error_message,
                    stack_frames=stack_frames,
                    assertion_diff=assertion_diff,
                )
            )

        # If no failure headers found, try short summary lines
        if not errors:
            for match in self._SHORT_SUMMARY_RE.finditer(pytest_output):
                test_file = match.group(1)
                test_name = match.group(2)
                reason = match.group(3) or ""
                error_type = self.categorize_error(reason)
                errors.append(
                    ParsedTestError(
                        test_name=f"{test_file}::{test_name}",
                        test_file=test_file,
                        error_type=error_type,
                        error_message=reason.strip(),
                    )
                )

        return errors

    def categorize_error(self, error_text: str) -> str:
        """Categorize an error string into a known type."""
        if not error_text:
            return "unknown"

        for category, keywords in _ERROR_CATEGORIES:
            for keyword in keywords:
                if keyword in error_text:
                    return category

        return "runtime"

    def extract_assertion_diff(self, error_block: str) -> AssertionDiff | None:
        """Extract expected vs actual values from an assertion error block."""
        e_lines = self._E_LINE_RE.findall(error_block)
        if not e_lines:
            return None

        expected = ""
        actual = ""
        diff_lines: list[str] = []

        for line in e_lines:
            line = line.strip()
            # Pattern: "assert X == Y"
            eq_match = re.match(r"assert\s+(.+?)\s*==\s*(.+)", line)
            if eq_match:
                actual = eq_match.group(1).strip()
                expected = eq_match.group(2).strip()
                continue

            # Pattern: "AssertionError: X != Y" or "AssertionError: message"
            ae_match = re.match(r"AssertionError:\s*(.+)", line)
            if ae_match:
                diff_lines.append(ae_match.group(1).strip())
                continue

            # Collect remaining E lines as diff context
            diff_lines.append(line)

        if expected or actual or diff_lines:
            return AssertionDiff(
                expected=expected,
                actual=actual,
                diff_lines=diff_lines,
            )
        return None

    def extract_stack_frames(self, traceback_text: str) -> list[StackFrame]:
        """Extract stack frames from a pytest traceback block."""
        frames: list[StackFrame] = []
        lines = traceback_text.splitlines()

        i = 0
        while i < len(lines):
            match = self._TRACEBACK_RE.match(lines[i])
            if match:
                file_path = match.group(1)
                line_number = int(match.group(2))
                function_name = match.group(3)

                # The next line is typically the code
                code_line = ""
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    # Skip E lines — those are assertion messages
                    if not next_line.startswith("E "):
                        code_line = next_line

                frames.append(
                    StackFrame(
                        file_path=file_path,
                        line_number=line_number,
                        function_name=function_name,
                        code_line=code_line,
                    )
                )
            i += 1

        return frames

    def format_for_llm(self, errors: list[ParsedTestError]) -> str:
        """Format parsed errors into a structured text block for LLM context."""
        if not errors:
            return ""

        parts: list[str] = []
        for err in errors:
            lines = [
                f"## Test Failure: {err.test_name}",
                f"Error type: {err.error_type}",
                f"Message: {err.error_message}",
            ]

            if err.stack_frames:
                lines.append("Stack trace:")
                for frame in err.stack_frames:
                    code = f"\n    {frame.code_line}" if frame.code_line else ""
                    lines.append(f"  {frame.file_path}:{frame.line_number} in {frame.function_name}(){code}")

            if err.assertion_diff:
                diff = err.assertion_diff
                if diff.expected:
                    lines.append(f"Expected: {diff.expected}")
                if diff.actual:
                    lines.append(f"Actual: {diff.actual}")
                if diff.diff_lines:
                    lines.append("Diff:")
                    for dl in diff.diff_lines:
                        lines.append(f"  {dl}")

            parts.append("\n".join(lines))

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _split_failure_blocks(self, output: str) -> list[tuple[str, str]]:
        """Split pytest output into (test_id, block) tuples based on failure headers."""
        blocks: list[tuple[str, str]] = []
        matches = list(self._FAILURE_HEADER_RE.finditer(output))

        for i, match in enumerate(matches):
            test_id = match.group(1).strip()
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(output)
            block = output[start:end]
            blocks.append((test_id, block))

        return blocks

    def _parse_test_id(self, test_id: str) -> tuple[str, str]:
        """Split 'tests/test_foo.py::TestClass::test_method' into (file, name)."""
        if "::" in test_id:
            parts = test_id.split("::", 1)
            return parts[0], parts[1]
        return "", test_id
