"""Tests for the calibration-to-prompt feedback loop adapter."""

from __future__ import annotations

from unittest.mock import MagicMock

from ortobahn.calibration_adapter import get_calibration_context

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_db(rows):
    """Create a mock Database whose fetchall() returns the given rows."""
    db = MagicMock()
    db.fetchall.return_value = rows
    return db


def _make_row(predicted: float, actual: float, error: float | None = None):
    """Build a calibration row dict. If error is None, compute it."""
    if error is None:
        error = predicted - actual
    return {
        "predicted_confidence": predicted,
        "engagement_percentile": actual,
        "calibration_error": error,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInsufficientData:
    def test_empty_on_insufficient_data(self):
        """< 5 records returns empty string."""
        db = _make_mock_db([_make_row(0.8, 0.6) for _ in range(4)])
        result = get_calibration_context(db, "default")
        assert result == ""

    def test_empty_on_zero_records(self):
        """0 records returns empty string."""
        db = _make_mock_db([])
        result = get_calibration_context(db, "default")
        assert result == ""


class TestOverconfidentDetection:
    def test_significantly_overconfident(self):
        """Mean positive error > 0.15 returns 'significantly overconfident'."""
        rows = [_make_row(0.9, 0.6, 0.3) for _ in range(10)]
        db = _make_mock_db(rows)
        result = get_calibration_context(db, "default")
        assert "significantly overconfident" in result
        assert "Reduce your confidence scores" in result

    def test_slightly_overconfident(self):
        """Mean positive error between 0.05 and 0.15 returns 'slightly overconfident'."""
        rows = [_make_row(0.8, 0.7, 0.1) for _ in range(10)]
        db = _make_mock_db(rows)
        result = get_calibration_context(db, "default")
        assert "slightly overconfident" in result
        assert "Consider lowering confidence scores" in result


class TestUnderconfidentDetection:
    def test_significantly_underconfident(self):
        """Mean negative error < -0.15 returns 'significantly underconfident'."""
        rows = [_make_row(0.4, 0.7, -0.3) for _ in range(10)]
        db = _make_mock_db(rows)
        result = get_calibration_context(db, "default")
        assert "significantly underconfident" in result
        assert "Increase your confidence scores" in result

    def test_slightly_underconfident(self):
        """Mean negative error between -0.15 and -0.05 returns 'slightly underconfident'."""
        rows = [_make_row(0.65, 0.75, -0.1) for _ in range(10)]
        db = _make_mock_db(rows)
        result = get_calibration_context(db, "default")
        assert "slightly underconfident" in result
        assert "Consider raising confidence scores" in result


class TestWellCalibrated:
    def test_well_calibrated(self):
        """Near-zero error returns 'well-calibrated'."""
        rows = [_make_row(0.75, 0.73, 0.02) for _ in range(10)]
        db = _make_mock_db(rows)
        result = get_calibration_context(db, "default")
        assert "well-calibrated" in result
        assert "accurate" in result


class TestWorstMiscalibrations:
    def test_includes_worst_miscalibrations(self):
        """Output contains specific prediction/actual pairs for worst miscalibrations."""
        rows = [
            _make_row(0.9, 0.3, 0.6),  # Worst
            _make_row(0.8, 0.4, 0.4),  # Second worst
            _make_row(0.7, 0.5, 0.2),  # Third
            _make_row(0.6, 0.55, 0.05),
            _make_row(0.5, 0.48, 0.02),
        ]
        db = _make_mock_db(rows)
        result = get_calibration_context(db, "default")
        assert "Largest miscalibrations" in result
        assert "Predicted 0.90" in result
        assert "actual performance percentile 0.30" in result
        assert "Predicted 0.80" in result
        assert "actual performance percentile 0.40" in result


class TestNoneValueHandling:
    def test_handles_none_values_gracefully(self):
        """Some null calibration_error values don't crash."""
        rows = [
            _make_row(0.8, 0.6, 0.2),
            _make_row(0.7, 0.5, 0.2),
            _make_row(0.9, 0.4, 0.5),
            {"predicted_confidence": 0.8, "engagement_percentile": 0.6, "calibration_error": None},
            {"predicted_confidence": 0.7, "engagement_percentile": 0.5, "calibration_error": None},
            _make_row(0.75, 0.6, 0.15),
            _make_row(0.85, 0.55, 0.3),
        ]
        db = _make_mock_db(rows)
        # Should not raise and should still produce output (5 non-None errors)
        result = get_calibration_context(db, "default")
        assert result != ""
        assert "Calibration Feedback" in result

    def test_all_none_errors_returns_empty(self):
        """If all calibration_error values are None, returns empty string."""
        rows = [
            {"predicted_confidence": 0.8, "engagement_percentile": 0.6, "calibration_error": None} for _ in range(10)
        ]
        db = _make_mock_db(rows)
        result = get_calibration_context(db, "default")
        assert result == ""
