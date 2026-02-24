"""Tests for structured pytest error parser (TestErrorParser)."""

from __future__ import annotations

import pytest

from ortobahn.test_parser import AssertionDiff, ParsedTestError, StackFrame, TestErrorParser


@pytest.fixture
def parser():
    return TestErrorParser()


# ---------------------------------------------------------------------------
# Error categorization
# ---------------------------------------------------------------------------


class TestCategorization:
    def test_categorize_assertion(self, parser):
        assert parser.categorize_error("AssertionError: assert 5 == 3") == "assertion"

    def test_categorize_import(self, parser):
        assert parser.categorize_error("ImportError: No module named 'foo'") == "import"

    def test_categorize_module_not_found(self, parser):
        assert parser.categorize_error("ModuleNotFoundError: No module named 'bar'") == "import"

    def test_categorize_attribute(self, parser):
        assert parser.categorize_error("AttributeError: 'NoneType' has no attribute 'x'") == "attribute"

    def test_categorize_type(self, parser):
        assert parser.categorize_error("TypeError: expected str, got int") == "type"

    def test_categorize_fixture(self, parser):
        assert parser.categorize_error("fixture 'my_fixture' not found") == "fixture"

    def test_categorize_timeout(self, parser):
        assert parser.categorize_error("TimeoutError: operation timed out") == "timeout"

    def test_categorize_runtime(self, parser):
        assert parser.categorize_error("RuntimeError: something bad happened") == "runtime"

    def test_categorize_unknown(self, parser):
        assert parser.categorize_error("") == "unknown"

    def test_categorize_generic_error_is_runtime(self, parser):
        assert parser.categorize_error("some random thing failed") == "runtime"


# ---------------------------------------------------------------------------
# Assertion diff extraction
# ---------------------------------------------------------------------------


class TestAssertionDiff:
    def test_extract_assertion_diff(self, parser):
        block = "    def test_foo():\n>       assert result == 3\nE       assert 5 == 3\nE       +5\nE       -3\n"
        diff = parser.extract_assertion_diff(block)
        assert diff is not None
        assert diff.expected == "3"
        assert diff.actual == "5"

    def test_extract_no_assertion(self, parser):
        block = "    def test_foo():\n        pass\n"
        diff = parser.extract_assertion_diff(block)
        assert diff is None

    def test_extract_assertion_error_message(self, parser):
        block = "E       AssertionError: values differ\n"
        diff = parser.extract_assertion_diff(block)
        assert diff is not None
        assert "values differ" in diff.diff_lines[0]


# ---------------------------------------------------------------------------
# Stack frame extraction
# ---------------------------------------------------------------------------


class TestStackFrames:
    def test_extract_stack_frames(self, parser):
        traceback = (
            "ortobahn/config.py:42: in load_config\n"
            "    return Config(**data)\n"
            "tests/test_config.py:15: in test_bar\n"
            "    assert result == 3\n"
        )
        frames = parser.extract_stack_frames(traceback)
        assert len(frames) == 2

        assert frames[0].file_path == "ortobahn/config.py"
        assert frames[0].line_number == 42
        assert frames[0].function_name == "load_config"
        assert "Config" in frames[0].code_line

        assert frames[1].file_path == "tests/test_config.py"
        assert frames[1].line_number == 15
        assert frames[1].function_name == "test_bar"

    def test_extract_empty_traceback(self, parser):
        frames = parser.extract_stack_frames("")
        assert frames == []


# ---------------------------------------------------------------------------
# Full parsing
# ---------------------------------------------------------------------------


class TestFullParsing:
    def test_parse_assertion_error(self, parser):
        output = "__ test_foo __\ntests/test_foo.py:10: in test_foo\n    assert result == 3\nE       assert 5 == 3\n"
        errors = parser.parse(output)
        assert len(errors) == 1
        assert errors[0].test_name == "tests/test_foo.py::test_foo"
        assert errors[0].error_type == "assertion"

    def test_parse_import_error(self, parser):
        output = (
            "__ test_import __\n"
            "tests/test_import.py:1: in test_import\n"
            "    import nonexistent\n"
            "E       ImportError: No module named 'nonexistent'\n"
        )
        errors = parser.parse(output)
        assert len(errors) == 1
        assert errors[0].error_type == "import"

    def test_parse_attribute_error(self, parser):
        output = (
            "__ test_attr __\n"
            "tests/test_attr.py:5: in test_attr\n"
            "    obj.missing_method()\n"
            "E       AttributeError: 'MyClass' has no attribute 'missing_method'\n"
        )
        errors = parser.parse(output)
        assert len(errors) == 1
        assert errors[0].error_type == "attribute"

    def test_parse_type_error(self, parser):
        output = (
            "__ test_types __\n"
            "tests/test_types.py:8: in test_types\n"
            "    func('not_an_int')\n"
            "E       TypeError: expected int\n"
        )
        errors = parser.parse(output)
        assert len(errors) == 1
        assert errors[0].error_type == "type"

    def test_parse_fixture_error(self, parser):
        output = "__ test_fix __\nE       fixture 'missing_fixture' not found\n"
        errors = parser.parse(output)
        assert len(errors) == 1
        assert errors[0].error_type == "fixture"

    def test_parse_multiple_failures(self, parser):
        output = (
            "__ test_one __\n"
            "tests/test_multi.py:10: in test_one\n"
            "    assert 1 == 2\n"
            "E       assert 1 == 2\n"
            "__ test_two __\n"
            "tests/test_multi.py:20: in test_two\n"
            "    raise ValueError('bad')\n"
            "E       ValueError: bad\n"
        )
        errors = parser.parse(output)
        assert len(errors) == 2

    def test_empty_output(self, parser):
        errors = parser.parse("")
        assert errors == []

    def test_parse_short_summary_fallback(self, parser):
        """When there are no failure headers, fall back to short summary lines."""
        output = (
            "FAILED tests/test_foo.py::test_bar - AssertionError: 1 != 2\n"
            "FAILED tests/test_baz.py::test_qux - KeyError\n"
        )
        errors = parser.parse(output)
        assert len(errors) == 2
        assert errors[0].test_file == "tests/test_foo.py"
        assert errors[1].test_file == "tests/test_baz.py"


# ---------------------------------------------------------------------------
# LLM formatting
# ---------------------------------------------------------------------------


class TestFormatForLLM:
    def test_format_for_llm(self, parser):
        errors = [
            ParsedTestError(
                test_name="tests/test_foo.py::test_bar",
                test_file="tests/test_foo.py",
                error_type="assertion",
                error_message="assert 5 == 3",
                stack_frames=[
                    StackFrame(
                        file_path="ortobahn/config.py",
                        line_number=42,
                        function_name="load_config",
                        code_line="return Config(**data)",
                    ),
                    StackFrame(
                        file_path="tests/test_foo.py",
                        line_number=15,
                        function_name="test_bar",
                        code_line="assert result == 3",
                    ),
                ],
                assertion_diff=AssertionDiff(
                    expected="3",
                    actual="5",
                    diff_lines=[],
                ),
            )
        ]
        output = parser.format_for_llm(errors)
        assert "Test Failure: tests/test_foo.py::test_bar" in output
        assert "Error type: assertion" in output
        assert "Stack trace:" in output
        assert "ortobahn/config.py:42 in load_config()" in output
        assert "Expected: 3" in output
        assert "Actual: 5" in output

    def test_format_empty_list(self, parser):
        output = parser.format_for_llm([])
        assert output == ""
