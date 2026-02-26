"""Tests for pipeline checkpoint/resume system."""

from __future__ import annotations

import json

from ortobahn.db import Database
from ortobahn.migrations import EXPECTED_SCHEMA, _get_schema_version


class TestMigration032:
    def test_schema_version(self, tmp_path):
        db = Database(tmp_path / "m32.db")
        assert _get_schema_version(db) == 40
        db.close()

    def test_phase_columns_exist(self, tmp_path):
        db = Database(tmp_path / "m32b.db")
        db.fetchall("SELECT current_phase, completed_phases, failed_phase, phase_data FROM pipeline_runs LIMIT 1")
        db.close()

    def test_expected_schema_includes_phase_columns(self):
        cols = EXPECTED_SCHEMA.get("pipeline_runs", [])
        assert "current_phase" in cols
        assert "completed_phases" in cols
        assert "failed_phase" in cols
        assert "phase_data" in cols


class TestPhaseTracking:
    def test_update_phase(self, tmp_path):
        db = Database(tmp_path / "phase.db")
        db.start_pipeline_run("run-1", mode="single")
        db.update_pipeline_phase("run-1", "intelligence")
        row = db.fetchone("SELECT current_phase FROM pipeline_runs WHERE id = 'run-1'")
        assert row["current_phase"] == "intelligence"
        db.close()

    def test_complete_phase(self, tmp_path):
        db = Database(tmp_path / "phase2.db")
        db.start_pipeline_run("run-1", mode="single")
        db.update_pipeline_phase("run-1", "intelligence")
        db.complete_pipeline_phase("run-1", "intelligence", {"trending_count": 5})

        row = db.fetchone("SELECT completed_phases, phase_data, current_phase FROM pipeline_runs WHERE id = 'run-1'")
        assert row["current_phase"] is None
        completed = json.loads(row["completed_phases"])
        assert "intelligence" in completed
        data = json.loads(row["phase_data"])
        assert data["intelligence"]["trending_count"] == 5
        db.close()

    def test_fail_phase(self, tmp_path):
        db = Database(tmp_path / "phase3.db")
        db.start_pipeline_run("run-1", mode="single")
        db.update_pipeline_phase("run-1", "decision")
        db.fail_pipeline_phase("run-1", "decision", ["CEO agent crashed"])

        row = db.fetchone("SELECT failed_phase, status, errors FROM pipeline_runs WHERE id = 'run-1'")
        assert row["failed_phase"] == "decision"
        assert row["status"] == "failed"
        errors = json.loads(row["errors"])
        assert "CEO agent crashed" in errors
        db.close()

    def test_get_resumable_run(self, tmp_path):
        db = Database(tmp_path / "resume.db")
        db.start_pipeline_run("run-1", mode="single", client_id="client-a")
        db.complete_pipeline_phase("run-1", "intelligence")
        db.fail_pipeline_phase("run-1", "decision", ["error"])

        resumable = db.get_resumable_run("client-a")
        assert resumable is not None
        assert resumable["id"] == "run-1"
        assert resumable["failed_phase"] == "decision"

        completed = json.loads(resumable["completed_phases"])
        assert "intelligence" in completed
        db.close()

    def test_no_resumable_run_when_completed(self, tmp_path):
        db = Database(tmp_path / "noresume.db")
        db.start_pipeline_run("run-1", mode="single", client_id="client-a")
        db.complete_pipeline_run("run-1", posts_published=3)

        assert db.get_resumable_run("client-a") is None
        db.close()

    def test_multiple_phases_tracked(self, tmp_path):
        db = Database(tmp_path / "multi.db")
        db.start_pipeline_run("run-1", mode="single")

        db.update_pipeline_phase("run-1", "intelligence")
        db.complete_pipeline_phase("run-1", "intelligence")
        db.update_pipeline_phase("run-1", "decision")
        db.complete_pipeline_phase("run-1", "decision")
        db.update_pipeline_phase("run-1", "execution")
        db.fail_pipeline_phase("run-1", "execution", ["creator failed"])

        row = db.fetchone("SELECT completed_phases, failed_phase FROM pipeline_runs WHERE id = 'run-1'")
        completed = json.loads(row["completed_phases"])
        assert completed == ["intelligence", "decision"]
        assert row["failed_phase"] == "execution"
        db.close()
