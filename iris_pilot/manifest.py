"""Persisted run manifest and stale-state tracking.

The pipeline writes a run manifest to disk after every invocation so the
last run's outcome (success/failure, counts, timestamps) can be inspected
without re-running anything. It also persists a small state file recording
the last time each core stage (promote/refresh_views/export) succeeded, so
that when a run halts early or fails, the manifest can clearly flag which
on-disk views/exports are now stale relative to the data that was actually
promoted.
"""
import json
import os
from datetime import datetime

STATE_PATH = "exports/pipeline_state.json"
MANIFEST_PATH = "exports/run_manifest.json"

# Maps a Stage's display name to the short key used in persisted state.
STAGE_KEYS = {
    "Promote Accepted Data": "promote",
    "Refresh Materialized Views": "refresh_views",
    "Export Dossiers": "export",
}


def load_state(path=STATE_PATH):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def read_manifest(path=MANIFEST_PATH):
    """Read the last persisted run manifest, or None if no run has happened yet."""
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def save_state(state, path=STATE_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2, default=str)


def compute_freshness(state, stage_results, planned_stage_names):
    """
    Determine, for each core stage, whether this run left it fresh or stale.

    state: previously persisted {key: {"last_success": iso_str}}
    stage_results: result.stage_results from a StageRunner run
    planned_stage_names: ordered stage names the pipeline intends to run,
        e.g. ["Promote Accepted Data", "Refresh Materialized Views", "Export Dossiers"]

    Returns (freshness_dict, updated_state). A stage is "stale" if it did not
    succeed during this run (either it failed, or the run halted before
    reaching it), meaning the on-disk view/export still reflects an older run.
    """
    executed_by_name = {r["stage"]: r for r in stage_results}
    now_iso = datetime.now().isoformat()
    new_state = dict(state)
    freshness = {}

    for stage_name in planned_stage_names:
        key = STAGE_KEYS.get(stage_name)
        if key is None:
            continue
        prev = state.get(key, {})
        executed = executed_by_name.get(stage_name)
        if executed and executed["status"] == "success":
            freshness[key] = {"stale": False, "last_success": now_iso}
            new_state[key] = {"last_success": now_iso}
        else:
            last_success = prev.get("last_success")
            freshness[key] = {
                "stale": True,
                "last_success": last_success,
                "reason": "failed_this_run" if executed else "not_reached_this_run",
            }
    return freshness, new_state


def build_summary(result, freshness):
    return {
        "success": result.success,
        "start_time": result.start_time,
        "end_time": result.end_time,
        "duration_seconds": (result.end_time - result.start_time).total_seconds()
        if result.end_time
        else None,
        "stages_executed": len(result.stage_results),
        "failed_stage": result.failed_stage,
        "stage_details": result.stage_results,
        "freshness": freshness,
    }


def write_manifest(summary, path=MANIFEST_PATH, serializer=str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(summary, f, indent=2, default=serializer)
