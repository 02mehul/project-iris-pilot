import typer
import json
from datetime import datetime
from iris_pilot.runner import StageRunner
from iris_pilot.stages import (
    SetupDatabaseStage,
    PrerequisiteCheckStage,
    ValidateDataContractStage,
    PromoteDataStage,
    RefreshViewsStage,
    ExportDossiersStage,
)
from iris_pilot.manifest import (
    STAGE_KEYS,
    MANIFEST_PATH,
    load_state,
    save_state,
    compute_freshness,
    build_summary,
    write_manifest,
    read_manifest,
)

app = typer.Typer(help="One-Command Pilot Orchestration CLI for Project IRIS")


# A Typer app with exactly one command and no callback silently collapses
# into a bare command (dropping the subcommand name). Registering this
# no-op callback keeps `run` a real, required subcommand, so the documented
# invocation `python -m iris_pilot.cli run --setup` behaves as written.
@app.callback()
def main():
    """One-Command Pilot Orchestration CLI for Project IRIS."""


def default_json_serializer(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


@app.command()
def run(
    setup: bool = typer.Option(
        False, "--setup", help="Run database setup (schema + fixtures) before pipeline"
    ),
):
    """
    Run the full data promotion, view refresh, and export orchestration pipeline.
    """
    stages = []
    if setup:
        stages.append(SetupDatabaseStage())

    stages.append(PrerequisiteCheckStage())
    stages.append(ValidateDataContractStage())
    stages.extend(
        [
            PromoteDataStage(),
            RefreshViewsStage(),
            ExportDossiersStage(),
        ]
    )

    runner = StageRunner(stages)
    typer.echo("Starting orchestration run...")
    result = runner.run()

    # Compare this run's outcome against the last persisted state to work out
    # which core outputs (views/exports) are stale relative to promoted data.
    state = load_state()
    planned_stage_names = list(STAGE_KEYS.keys())
    freshness, new_state = compute_freshness(state, result.stage_results, planned_stage_names)
    save_state(new_state)

    summary = build_summary(result, freshness)
    write_manifest(summary, serializer=default_json_serializer)

    summary_json = json.dumps(summary, indent=2, default=default_json_serializer)
    typer.echo("\n--- RUN SUMMARY ---")
    typer.echo(summary_json)
    typer.echo("\nRun manifest written to exports/run_manifest.json")

    stale_keys = [key for key, info in freshness.items() if info["stale"]]
    if stale_keys:
        typer.secho(
            f"\n[STALE] The following outputs were NOT refreshed this run and are stale: {stale_keys}",
            fg=typer.colors.YELLOW,
        )

    if not result.success:
        typer.secho(
            f"\n[CRITICAL FAILURE] Pipeline halted at stage: {result.failed_stage}. "
            "Stale view/export state may exist (see 'freshness' in the run manifest).",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    else:
        typer.secho("\n[SUCCESS] One-command run completed successfully.", fg=typer.colors.GREEN)


@app.command()
def status():
    """
    Show the outcome and freshness of the last recorded run, without touching
    the database. Reads the persisted run manifest from a prior `run`.
    """
    manifest = read_manifest()
    if manifest is None:
        typer.secho(
            f"No run manifest found at {MANIFEST_PATH}. "
            "Run `python -m iris_pilot.cli run --setup` first.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=1)

    typer.echo(f"Last run: {'SUCCESS' if manifest['success'] else 'FAILED'}")
    typer.echo(f"  start_time: {manifest['start_time']}")
    typer.echo(f"  end_time:   {manifest['end_time']}")
    typer.echo(f"  duration_seconds: {manifest['duration_seconds']}")
    if manifest["failed_stage"]:
        typer.secho(f"  failed_stage: {manifest['failed_stage']}", fg=typer.colors.RED)

    typer.echo("\nFreshness:")
    any_stale = False
    for key, info in manifest["freshness"].items():
        if info["stale"]:
            any_stale = True
            typer.secho(
                f"  [STALE] {key}: last_success={info['last_success']} reason={info.get('reason')}",
                fg=typer.colors.YELLOW,
            )
        else:
            typer.secho(
                f"  [FRESH] {key}: last_success={info['last_success']}", fg=typer.colors.GREEN
            )

    if any_stale:
        typer.secho("\nSome outputs are stale - rerun the pipeline.", fg=typer.colors.YELLOW)

    raise typer.Exit(code=0 if manifest["success"] and not any_stale else 1)


if __name__ == "__main__":
    app()
