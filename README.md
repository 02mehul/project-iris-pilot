# Project IRIS - One-Command Pilot Orchestration

## Overview
This repository contains a lean, repeatable one-command orchestration CLI for the Project IRIS pilot. It handles promoting data, refreshing materialized views (BESS and peatland), and exporting geospatial dossiers with failure visibility and idempotency.

## Architecture Choices
- **CLI & Orchestration:** Built with Typer for a clean CLI interface. The custom `StageRunner` engine sequentially processes stages, explicitly checking for `StageError` exceptions. If a stage marked as `critical` fails, the runner immediately halts to prevent downstream propagation (e.g., exporting stale data) and flags the pipeline state as failed. The exit code is non-zero whenever a critical stage fails, so a failed run can never be mistaken for a success by a calling script.
- **Prerequisite Check:** A dedicated, always-run `Check Prerequisites` stage validates the PostGIS extension, the presence of the `source_sites` table, and that it has rows, before any promotion is attempted. This turns an obscure SQL error into a clear, actionable failure message.
- **Data Contract Validation:** A dedicated, always-run `Validate Data Contracts` stage enforces the data contracts named in the engineering constraints — completeness, CRS, units, source date, and geometry validity — *before* any row is promoted. It rejects (rather than silently coercing) rows below a completeness threshold, missing `source_date`, with an invalid geometry (`ST_IsValid`), the wrong SRID, an unrecognized `area_unit`, or a malformed `country_code`, and fails critically with the offending row ids and reasons. This is a hard promotion gate: bad data never reaches `core_candidate_sites`, the views, or the exports.
- **Idempotency Strategy:** Idempotency is enforced by using `TRUNCATE` then `INSERT` in our mocked core tables, and `REFRESH MATERIALIZED VIEW` for spatial aggregations. This ensures it is always safe to rerun.
- **Run Manifest & Stale-State Visibility:** Every invocation writes a persisted run manifest to `exports/run_manifest.json` containing per-stage status, timestamps, row counts, and a `freshness` block for the three core outputs (`promote`, `refresh_views`, `export`). A small state file (`exports/pipeline_state.json`) tracks the last time each of those stages actually succeeded. If a run halts or fails partway through, the manifest and CLI output explicitly mark the stages that were *not* refreshed this run as `"stale": true` (with a `reason` of `failed_this_run` or `not_reached_this_run`), so stale views/exports left over from an earlier successful run are never silently presented as current.
- **Database & Geometries:** Utilizes PostgreSQL 16+ with PostGIS 3.4+. All spatial logic uses a canonical column named `geom` under EPSG:4326. `country_code` is strictly enforced as non-null and format-checked (`^[A-Z]{3}$`) on every business entity. Both `source_sites` and `core_candidate_sites` carry a `UNIQUE (country_code, id)` constraint (with a supporting index on `country_code`), so identifiers and any future joins are always scoped through `country_code` rather than a bare cross-country `id`.
- **Units as an Explicit Contract:** `area_unit` is a first-class column (not just implied by the `area_sqm` name), defaulted to `'sqm'` in fixtures and validated by the contract stage — a row reporting e.g. hectares is rejected rather than silently treated as square meters. `area_unit` is carried through promotion and into the exported dossiers.
- **Data Export:** The final output is generated as JSON files containing GeoJSON geometries. A deliberate simplification for this pilot is writing the dossiers to local JSON files (`exports/`) rather than uploading to a cloud blob storage. A static, committed example of this output lives in `sample_output/` (see below) so a reviewer can see the shape of the data without running anything.

## Setup & Execution

### Prerequisites
- Docker Desktop
- Python 3.12+

### Quick Start
1. **Start the Database:**
   ```bash
   docker compose up -d
   ```
2. **Setup Virtual Environment:**
   ```bash
   python -m venv venv
   # Windows:
   .\venv\Scripts\Activate.ps1
   # Mac/Linux:
   source venv/bin/activate
   
   pip install -r requirements.txt
   ```
3. **Run the Pipeline (One-Command):**
   ```bash
   # Make sure you set PYTHONPATH on Windows if running directly:
   $env:PYTHONPATH = (Get-Location).Path
   python -m iris_pilot.cli run --setup
   ```
   *Note: The `--setup` flag initializes the schema and inserts the deterministic fixtures.*

   On subsequent runs (schema/fixtures already present), omit `--setup`:
   ```bash
   python -m iris_pilot.cli run
   ```

   Or, on a machine with `make` (starts the DB and runs the pipeline in one step):
   ```bash
   make run
   ```

4. **Check status without rerunning anything:**
   ```bash
   python -m iris_pilot.cli status
   ```
   Reads the persisted `exports/run_manifest.json` from the last `run` and prints its outcome plus per-output freshness (`[FRESH]`/`[STALE]`) — no database connection required. Exits non-zero if the last run failed or left anything stale, so it's scriptable (e.g. a pre-deploy check).

5. **Inspect the run manifest directly:**
   ```bash
   cat exports/run_manifest.json
   ```
   This persisted file (also echoed to stdout by `run`) shows stage-by-stage status, timestamps, counts, and a `freshness` section flagging any view/export left stale by a failed or incomplete run.

### Testing
Run the automated test suite to verify success, idempotency, prerequisite checking, stale-state tracking, and failure halting:
```bash
pytest
```

### Sample Output (no run required)
`sample_output/` contains a static, committed snapshot of a real successful run — `bess_candidates.json`, `peatland_candidates.json`, and `run_manifest.json` — generated from the fixtures in this repo. This is distinct from the live `exports/` directory (gitignored, regenerated by every `run`); it exists purely so a reviewer can see the shape of the dossiers and manifest without setting up Docker/Python.

## Assumptions & Simplifications
- **Mock Data:** Upstream remote connections are simulated using local SQL inserts into a `source_sites` table (`02_fixtures.sql`).
- **Exports:** Saved as local `.json` files. For production, this stage would be refactored to stream to S3/GCS.
- **Orchestrator:** A lightweight custom sequential runner is used to meet the "lean one-command" requirement without heavy orchestration platforms. For a larger multi-region production workload, this would naturally evolve into an Airflow, Dagster, or Prefect workflow.
- **Freshness state:** Persisted as a small local JSON file (`exports/pipeline_state.json`) rather than a database table. For production this would move into Postgres itself (e.g. a `pipeline_runs` audit table) so freshness state survives independently of the filesystem and can be queried/alerted on centrally.
