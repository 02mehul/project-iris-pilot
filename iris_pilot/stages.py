import json
import os
import re
from datetime import datetime
from iris_pilot.db import get_connection, execute_sql_file

class StageError(Exception):
    pass

class Stage:
    name: str
    is_critical: bool

    def __init__(self, name: str, is_critical: bool = True):
        self.name = name
        self.is_critical = is_critical

    def run(self):
        raise NotImplementedError()

class PrerequisiteCheckStage(Stage):
    def __init__(self):
        super().__init__("Check Prerequisites", is_critical=True)

    def run(self):
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM pg_extension WHERE extname = 'postgis';"
                    )
                    if cur.fetchone() is None:
                        raise StageError(
                            "PostGIS extension is not installed on the database."
                        )

                    cur.execute("SELECT to_regclass('public.source_sites') AS reg;")
                    if cur.fetchone()["reg"] is None:
                        raise StageError(
                            "Required table 'source_sites' does not exist. "
                            "Run with --setup first."
                        )

                    cur.execute("SELECT COUNT(*) AS n FROM source_sites;")
                    count = cur.fetchone()["n"]
                    if count == 0:
                        raise StageError(
                            "No source data found in 'source_sites'. Nothing to promote."
                        )
            return {"status": "success", "source_rows": count}
        except StageError:
            raise
        except Exception as e:
            raise StageError(f"Prerequisite check failed (DB connectivity?): {str(e)}")


MIN_COMPLETENESS_SCORE = 0.5
EXPECTED_SRID = 4326
EXPECTED_AREA_UNIT = "sqm"
VALID_COUNTRY_CODE_RE = r"^[A-Z]{3}$"


class ValidateDataContractStage(Stage):
    """
    Enforces the data contracts called out in the project's engineering
    constraints: CRS, units, completeness, source date and geometry validity
    must all hold before anything is promoted. Violations fail the stage
    with the offending row ids and reasons rather than silently coercing or
    skipping bad data.
    """

    def __init__(self):
        super().__init__("Validate Data Contracts", is_critical=True)

    def run(self):
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT
                            id,
                            country_code,
                            area_unit,
                            data_completeness_score,
                            source_date,
                            geom IS NULL AS geom_missing,
                            CASE WHEN geom IS NOT NULL THEN ST_IsValid(geom) END AS geom_valid,
                            CASE WHEN geom IS NOT NULL THEN ST_SRID(geom) END AS srid
                        FROM source_sites;
                        """
                    )
                    rows = cur.fetchall()
        except Exception as e:
            raise StageError(f"Data contract validation failed to run: {str(e)}")

        violations = []
        for row in rows:
            reasons = []
            if not row["country_code"] or not re.match(VALID_COUNTRY_CODE_RE, row["country_code"]):
                reasons.append(f"missing/invalid country_code (got {row['country_code']!r})")
            if row["area_unit"] != EXPECTED_AREA_UNIT:
                reasons.append(
                    f"unexpected area_unit (got {row['area_unit']!r}, expected {EXPECTED_AREA_UNIT!r})"
                )
            score = row["data_completeness_score"]
            if score is None or score < MIN_COMPLETENESS_SCORE:
                reasons.append(
                    f"data_completeness_score below {MIN_COMPLETENESS_SCORE} (got {score})"
                )
            if row["source_date"] is None:
                reasons.append("missing source_date")
            if row["geom_missing"]:
                reasons.append("missing geom")
            elif not row["geom_valid"]:
                reasons.append("invalid geometry (ST_IsValid returned false)")
            elif row["srid"] != EXPECTED_SRID:
                reasons.append(
                    f"unexpected CRS/SRID (got {row['srid']}, expected {EXPECTED_SRID})"
                )

            if reasons:
                violations.append({"id": row["id"], "reasons": reasons})

        if violations:
            raise StageError(
                f"{len(violations)} source row(s) failed data-contract validation: {violations}"
            )

        return {"status": "success", "rows_validated": len(rows)}


class SetupDatabaseStage(Stage):
    def __init__(self):
        super().__init__("Setup Database & Fixtures", is_critical=True)
    
    def run(self):
        try:
            with get_connection() as conn:
                execute_sql_file(conn, "sql/01_schema.sql")
                execute_sql_file(conn, "sql/02_fixtures.sql")
            return {"status": "success", "message": "Schema and fixtures loaded"}
        except Exception as e:
            raise StageError(f"Failed to setup database: {str(e)}")

class PromoteDataStage(Stage):
    def __init__(self):
        super().__init__("Promote Accepted Data", is_critical=True)
    
    def run(self):
        sql = """
        INSERT INTO core_candidate_sites (country_code, site_type, area_sqm, area_unit, eco_points, source_date, geom, uncertainty_notes)
        SELECT
            country_code,
            site_type,
            area_sqm,
            area_unit,
            area_sqm * 8,
            source_date,
            geom,
            uncertainty_notes
        FROM source_sites
        ON CONFLICT DO NOTHING;
        """
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("TRUNCATE TABLE core_candidate_sites CASCADE;")
                    cur.execute(sql)
                    rowcount = cur.rowcount
                conn.commit()
            return {"status": "success", "promoted_rows": rowcount}
        except Exception as e:
            raise StageError(f"Failed to promote data: {str(e)}")

class RefreshViewsStage(Stage):
    def __init__(self):
        super().__init__("Refresh Materialized Views", is_critical=True)
    
    def run(self):
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("REFRESH MATERIALIZED VIEW mv_bess_candidates;")
                    cur.execute("REFRESH MATERIALIZED VIEW mv_peatland_candidates;")
                conn.commit()
            return {"status": "success", "views_refreshed": ["mv_bess_candidates", "mv_peatland_candidates"]}
        except Exception as e:
            raise StageError(f"Failed to refresh views: {str(e)}")

class ExportDossiersStage(Stage):
    def __init__(self):
        super().__init__("Export Dossiers", is_critical=False)
    
    def run(self):
        try:
            os.makedirs("exports", exist_ok=True)
            files_exported = []
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Export BESS
                    cur.execute("SELECT id, country_code, area_sqm, area_unit, eco_points, ST_AsGeoJSON(geom) as geom, reference_wording FROM mv_bess_candidates;")
                    bess_rows = cur.fetchall()
                    for row in bess_rows:
                        row['geom'] = json.loads(row['geom'])
                    with open("exports/bess_candidates.json", "w") as f:
                        json.dump(bess_rows, f, indent=2, default=str)
                    files_exported.append("exports/bess_candidates.json")
                    
                    # Export PEATLAND
                    cur.execute("SELECT id, country_code, area_sqm, area_unit, eco_points, ST_AsGeoJSON(geom) as geom, reference_wording FROM mv_peatland_candidates;")
                    peatland_rows = cur.fetchall()
                    for row in peatland_rows:
                        row['geom'] = json.loads(row['geom'])
                    with open("exports/peatland_candidates.json", "w") as f:
                        json.dump(peatland_rows, f, indent=2, default=str)
                    files_exported.append("exports/peatland_candidates.json")
                    
            return {"status": "success", "files_exported": files_exported}
        except Exception as e:
            raise StageError(f"Failed to export dossiers: {str(e)}")
