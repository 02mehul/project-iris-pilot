import pytest
import os
import json
from iris_pilot.runner import StageRunner
from iris_pilot.stages import (
    SetupDatabaseStage,
    PrerequisiteCheckStage,
    ValidateDataContractStage,
    PromoteDataStage,
    RefreshViewsStage,
    ExportDossiersStage,
    Stage,
    StageError
)
from iris_pilot.manifest import compute_freshness, STAGE_KEYS
from iris_pilot.db import get_connection

@pytest.fixture(scope="module", autouse=True)
def setup_db():
    stage = SetupDatabaseStage()
    stage.run()
    yield

def test_full_success_pipeline():
    stages = [
        PromoteDataStage(),
        RefreshViewsStage(),
        ExportDossiersStage()
    ]
    runner = StageRunner(stages)
    result = runner.run()
    
    assert result.success is True
    assert result.failed_stage is None
    assert len(result.stage_results) == 3
    
    # Check if files were exported
    assert os.path.exists("exports/bess_candidates.json")
    assert os.path.exists("exports/peatland_candidates.json")
    
    with open("exports/bess_candidates.json") as f:
        data = json.load(f)
        assert len(data) == 2
        assert data[0]["country_code"] == "GBR"

def test_idempotency():
    # Rerunning should be safe and produce the same outputs
    stages = [PromoteDataStage(), RefreshViewsStage()]
    
    # Run once
    runner = StageRunner(stages)
    result1 = runner.run()
    assert result1.success is True
    
    # Run again - should complete without errors
    runner = StageRunner(stages)
    result2 = runner.run()
    assert result2.success is True
    assert result2.stage_results[0]["details"]["promoted_rows"] == 4  # Truncate and re-insert is our idempotent strategy

class FailingCriticalStage(Stage):
    def __init__(self):
        super().__init__("Fail Badly", is_critical=True)
    
    def run(self):
        raise StageError("Simulated critical failure")

def test_failed_critical_stage_halts_pipeline():
    stages = [
        PromoteDataStage(),
        FailingCriticalStage(),
        RefreshViewsStage() # Should not be executed
    ]

    runner = StageRunner(stages)
    result = runner.run()

    assert result.success is False
    assert result.failed_stage == "Fail Badly"
    assert len(result.stage_results) == 2 # PromoteData + FailingCritical
    assert result.stage_results[-1]["status"] == "failed"


def test_prerequisite_check_fails_when_no_source_data():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE source_sites RESTART IDENTITY CASCADE;")
        conn.commit()

    stages = [PrerequisiteCheckStage()]
    runner = StageRunner(stages)
    result = runner.run()

    assert result.success is False
    assert result.failed_stage == "Check Prerequisites"

    # restore fixtures for subsequent tests
    stage = SetupDatabaseStage()
    stage.run()


def test_stale_state_visible_after_failed_run():
    planned = list(STAGE_KEYS.keys())

    # First, a fully successful run marks every core stage fresh.
    success_stages = [PromoteDataStage(), RefreshViewsStage(), ExportDossiersStage()]
    result_ok = StageRunner(success_stages).run()
    assert result_ok.success is True

    freshness_ok, state_after_ok = compute_freshness({}, result_ok.stage_results, planned)
    assert all(info["stale"] is False for info in freshness_ok.values())
    assert all(info["last_success"] is not None for info in freshness_ok.values())

    # Now a run that fails before reaching refresh/export must mark those stale,
    # while carrying forward the previous last_success timestamps.
    failing_stages = [PromoteDataStage(), FailingCriticalStage()]
    result_fail = StageRunner(failing_stages).run()
    assert result_fail.success is False

    freshness_fail, _ = compute_freshness(state_after_ok, result_fail.stage_results, planned)
    assert freshness_fail["promote"]["stale"] is False
    assert freshness_fail["refresh_views"]["stale"] is True
    assert freshness_fail["refresh_views"]["reason"] == "not_reached_this_run"
    assert freshness_fail["refresh_views"]["last_success"] == state_after_ok["refresh_views"]["last_success"]
    assert freshness_fail["export"]["stale"] is True
    assert freshness_fail["export"]["reason"] == "not_reached_this_run"


def test_cli_run_persists_manifest_and_flags_stale_outputs():
    from typer.testing import CliRunner
    from iris_pilot.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["run", "--setup"])

    assert result.exit_code == 0
    manifest_path = "exports/run_manifest.json"
    assert os.path.exists(manifest_path)

    with open(manifest_path) as f:
        manifest = json.load(f)
    assert manifest["success"] is True
    assert set(manifest["freshness"].keys()) == {"promote", "refresh_views", "export"}
    assert all(info["stale"] is False for info in manifest["freshness"].values())


def test_data_contract_validation_passes_clean_fixtures():
    result = ValidateDataContractStage().run()
    assert result["status"] == "success"
    assert result["rows_validated"] == 4


def test_data_contract_validation_rejects_low_completeness_row():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO source_sites
                    (country_code, site_type, area_sqm, source_date, geom,
                     data_completeness_score, uncertainty_notes)
                VALUES
                    ('GBR', 'BESS', 1000, '2023-01-01',
                     ST_GeomFromText('POLYGON((0 0, 0 1, 1 1, 1 0, 0 0))', 4326),
                     0.1, 'deliberately low completeness for test');
                """
            )
        conn.commit()

    with pytest.raises(StageError) as exc_info:
        ValidateDataContractStage().run()
    assert "data_completeness_score" in str(exc_info.value)

    # restore clean fixtures so subsequent tests aren't affected
    SetupDatabaseStage().run()


def test_data_contract_validation_rejects_invalid_geometry():
    # source_date/CRS/NOT NULL are already enforced by the schema itself;
    # a self-intersecting ("bowtie") polygon is a case the schema's column
    # type (GEOMETRY(Polygon, 4326)) does NOT catch, so it's a real test of
    # the ST_IsValid contract check.
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO source_sites
                    (country_code, site_type, area_sqm, source_date, geom,
                     data_completeness_score, uncertainty_notes)
                VALUES
                    ('IRL', 'PEATLAND', 2000, '2023-01-01',
                     ST_GeomFromText('POLYGON((0 0, 1 1, 1 0, 0 1, 0 0))', 4326),
                     0.9, 'self-intersecting geometry for test');
                """
            )
        conn.commit()

    with pytest.raises(StageError) as exc_info:
        ValidateDataContractStage().run()
    assert "invalid geometry" in str(exc_info.value)

    SetupDatabaseStage().run()


def test_data_contract_validation_rejects_wrong_area_unit():
    # 'units' is one of the explicit data contracts called out in the
    # engineering constraints; a non-'sqm' unit must be rejected rather than
    # silently promoted as if it were square meters.
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO source_sites
                    (country_code, site_type, area_sqm, area_unit, source_date, geom,
                     data_completeness_score, uncertainty_notes)
                VALUES
                    ('GBR', 'BESS', 3, 'hectares', '2023-01-01',
                     ST_GeomFromText('POLYGON((0 0, 0 1, 1 1, 1 0, 0 0))', 4326),
                     0.9, 'wrong area unit for test');
                """
            )
        conn.commit()

    with pytest.raises(StageError) as exc_info:
        ValidateDataContractStage().run()
    assert "area_unit" in str(exc_info.value)

    SetupDatabaseStage().run()


def test_promote_and_export_carry_area_unit():
    stages = [PromoteDataStage(), RefreshViewsStage(), ExportDossiersStage()]
    result = StageRunner(stages).run()
    assert result.success is True

    with open("exports/bess_candidates.json") as f:
        data = json.load(f)
    assert all(row["area_unit"] == "sqm" for row in data)


def test_status_command_reports_last_manifest():
    from typer.testing import CliRunner
    from iris_pilot.cli import app

    cli_runner = CliRunner()

    # Ensure a fresh, successful manifest exists first.
    run_result = cli_runner.invoke(app, ["run", "--setup"])
    assert run_result.exit_code == 0

    status_result = cli_runner.invoke(app, ["status"])
    assert status_result.exit_code == 0
    assert "Last run: SUCCESS" in status_result.stdout
    assert "[FRESH] promote" in status_result.stdout
    assert "[FRESH] refresh_views" in status_result.stdout
    assert "[FRESH] export" in status_result.stdout
