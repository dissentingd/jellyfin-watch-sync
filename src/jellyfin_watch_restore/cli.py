"""Command-line entry point.

Safety model: `restore` ALWAYS computes and prints a plan first. Nothing is
written to Jellyfin unless you pass --apply. This isn't a suggestion you have
to remember -- it's the only way the command writes anything.
"""

from __future__ import annotations

import functools
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .collections.plan import CollectionPlan
from .collections.sources import JellyfinCollectionsSource, YamtrackListsSource
from .collections.targets import JellyfinCollectionsTarget, YamtrackListsTarget
from .errors import describe_error
from .models import WatchRecord
from .plan import ActionOutcome, RestorePlan
from .sources import GenericCsvSource, JellyfinBackupSource, YamtrackCsvSource
from .sources.base import Source
from .targets.jellyfin import JellyfinTarget
from .targets.jellyfin_client import JellyfinClient

app = typer.Typer(help="Restore watch history (and collections) into Jellyfin from another source.")
console = Console()


def _friendly_errors(fn):
    """Wraps a command so a known misconfiguration (bad API key, bad user
    id, unreachable server, wrong DSN) prints a plain-language diagnostic
    and exits cleanly, instead of a raw traceback. Anything not recognized
    by `describe_error` re-raises untouched -- this never hides an
    unfamiliar error, only translates the common, expected ones."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (typer.Exit, typer.BadParameter):
            raise
        except Exception as exc:
            message = describe_error(exc)
            if message is None:
                raise
            console.print(f"\n[red]{message}[/red]")
            raise typer.Exit(code=1) from None

    return wrapper


class SourceType(str, Enum):
    yamtrack_csv = "yamtrack-csv"
    yamtrack_db = "yamtrack-db"
    generic_csv = "generic-csv"
    jellyfin_backup = "jellyfin-backup"


def _build_source(
    source_type: SourceType,
    source_path: Path | None,
    source_dsn: str | None,
    username: str | None,
    yamtrack_user_id: int | None,
) -> Source:
    needs_path = (SourceType.yamtrack_csv, SourceType.generic_csv, SourceType.jellyfin_backup)
    if source_type in needs_path and source_path is None:
        raise typer.BadParameter(f"--source-path is required for {source_type.value}")

    if source_type is SourceType.yamtrack_csv:
        return YamtrackCsvSource(source_path)
    if source_type is SourceType.generic_csv:
        return GenericCsvSource(source_path)
    if source_type is SourceType.jellyfin_backup:
        if not username:
            raise typer.BadParameter("--username is required for jellyfin-backup")
        return JellyfinBackupSource(source_path, username)
    if source_type is SourceType.yamtrack_db:
        if not source_dsn or yamtrack_user_id is None:
            raise typer.BadParameter("--source-dsn and --yamtrack-user-id are required for yamtrack-db")
        from .sources.yamtrack_db import YamtrackDbSource  # optional extra

        return YamtrackDbSource(source_dsn, yamtrack_user_id)
    raise AssertionError(source_type)  # pragma: no cover


@app.command()
@_friendly_errors
def restore(
    source_type: Annotated[SourceType, typer.Option(help="Where watch history is read from.")],
    jellyfin_url: Annotated[str, typer.Option(envvar="JELLYFIN_URL")],
    jellyfin_api_key: Annotated[str, typer.Option(envvar="JELLYFIN_API_KEY")],
    jellyfin_user_id: Annotated[str, typer.Option(envvar="JELLYFIN_USER_ID", help="Jellyfin user GUID to restore watch state for.")],
    source_path: Annotated[Path | None, typer.Option(help="CSV path or Jellyfin backup zip/dir.")] = None,
    source_dsn: Annotated[str | None, typer.Option(help="Postgres DSN, for --source-type=yamtrack-db.")] = None,
    username: Annotated[str | None, typer.Option(help="Jellyfin username, for --source-type=jellyfin-backup.")] = None,
    yamtrack_user_id: Annotated[int | None, typer.Option(help="YAMTrack users_user.id, for --source-type=yamtrack-db.")] = None,
    limit: Annotated[int | None, typer.Option(help="Only consider the first N records (for testing).")] = None,
    force: Annotated[bool, typer.Option(help="Overwrite even if Jellyfin's current watched date is already as recent.")] = False,
    apply: Annotated[bool, typer.Option("--apply", help="Actually write to Jellyfin. Without this, only a plan is printed.")] = False,
    sample: Annotated[int, typer.Option(help="How many example matched/unmatched records to print.")] = 10,
) -> None:
    """Plan (and optionally apply) restoring watch history into Jellyfin."""
    source = _build_source(source_type, source_path, source_dsn, username, yamtrack_user_id)

    records: list[WatchRecord] = []
    with console.status(f"reading {source.describe()}..."):
        for i, record in enumerate(source.records()):
            if limit is not None and i >= limit:
                break
            records.append(record)
    console.print(f"read {len(records)} watch records from {source.describe()}")

    with JellyfinClient(jellyfin_url, jellyfin_api_key, jellyfin_user_id) as client:
        target = JellyfinTarget(client)
        with console.status("fetching current Jellyfin library..."):
            target.build_index()
        with console.status("matching records against the Jellyfin library..."):
            plan = target.plan(records, force=force)
        plan.source_description = source.describe()

        _print_plan(plan, sample=sample)

        if not apply:
            console.print("\n[yellow]Dry run only -- pass --apply to actually write to Jellyfin.[/yellow]")
            return

        if not plan.matched:
            console.print("\nNothing to apply.")
            return

        console.print(f"\n[bold]Applying {len(plan.matched)} matched record(s)...[/bold]")
        with console.status("writing to Jellyfin..."):
            target.apply(plan)

        applied = sum(1 for m in plan.matched if m.outcome is ActionOutcome.APPLIED)
        failed = [m for m in plan.matched if m.outcome is ActionOutcome.FAILED]
        console.print(f"applied: {applied}   failed: {len(failed)}")
        for f in failed[:sample]:
            console.print(f"  [red]FAILED[/red] {f.record.title or f.record.tmdb_id}: {f.error}")


def _print_plan(plan: RestorePlan, *, sample: int) -> None:
    console.print(f"\n[bold]{plan.summary()}[/bold]\n")

    if plan.matched:
        table = Table(title=f"Would restore (showing up to {sample})")
        table.add_column("Title")
        table.add_column("TMDB")
        table.add_column("S/E")
        table.add_column("Watched at")
        for m in plan.matched[:sample]:
            r = m.record
            se = f"S{r.season}E{r.episode}" if r.season is not None else ""
            table.add_row(m.target_title or r.title or "?", str(r.tmdb_id), se, str(r.watched_at))
        console.print(table)

    if plan.unmatched:
        table = Table(title=f"Unmatched -- no item currently in Jellyfin (showing up to {sample})")
        table.add_column("Title")
        table.add_column("TMDB")
        table.add_column("S/E")
        table.add_column("Reason")
        for u in plan.unmatched[:sample]:
            r = u.record
            se = f"S{r.season}E{r.episode}" if r.season is not None else ""
            table.add_row(r.title or "?", str(r.tmdb_id), se, u.reason)
        console.print(table)


@app.command()
@_friendly_errors
def backup_collections(
    jellyfin_url: Annotated[str, typer.Option(envvar="JELLYFIN_URL")],
    jellyfin_api_key: Annotated[str, typer.Option(envvar="JELLYFIN_API_KEY")],
    jellyfin_user_id: Annotated[str, typer.Option(envvar="JELLYFIN_USER_ID")],
    yamtrack_dsn: Annotated[str, typer.Option(envvar="YAMTRACK_DSN", help="Postgres DSN for YAMTrack's database.")],
    yamtrack_user_id: Annotated[int, typer.Option(envvar="YAMTRACK_USER_ID", help="YAMTrack users_user.id to own the backed-up lists.")],
    apply: Annotated[bool, typer.Option("--apply", help="Actually write to YAMTrack. Without this, only a plan is printed.")] = False,
    sample: Annotated[int, typer.Option()] = 10,
) -> None:
    """Back up Jellyfin Collections into YAMTrack Lists (one List per Collection)."""
    with JellyfinClient(jellyfin_url, jellyfin_api_key, jellyfin_user_id) as client:
        source = JellyfinCollectionsSource(client)
        with console.status(f"reading {source.describe()}..."):
            records = list(source.records())
    console.print(f"read {len(records)} collections from {source.describe()}")

    target = YamtrackListsTarget(yamtrack_dsn, yamtrack_user_id)
    with console.status("planning..."):
        plan = target.plan(records)
    plan.source_description = source.describe()

    _print_collection_plan(plan, sample=sample)

    if not apply:
        console.print("\n[yellow]Dry run only -- pass --apply to actually write to YAMTrack.[/yellow]")
        return
    if not plan.actions:
        console.print("\nNothing to apply.")
        return

    console.print(f"\n[bold]Applying {len(plan.actions)} collection(s)...[/bold]")
    with console.status("writing to YAMTrack..."):
        target.apply(plan)
    _print_apply_results(plan, sample=sample)


@app.command()
@_friendly_errors
def restore_collections(
    yamtrack_dsn: Annotated[str, typer.Option(envvar="YAMTRACK_DSN")],
    yamtrack_user_id: Annotated[int, typer.Option(envvar="YAMTRACK_USER_ID", help="YAMTrack users_user.id whose Lists to read.")],
    jellyfin_url: Annotated[str, typer.Option(envvar="JELLYFIN_URL")],
    jellyfin_api_key: Annotated[str, typer.Option(envvar="JELLYFIN_API_KEY")],
    jellyfin_user_id: Annotated[str, typer.Option(envvar="JELLYFIN_USER_ID")],
    apply: Annotated[bool, typer.Option("--apply", help="Actually write to Jellyfin. Without this, only a plan is printed.")] = False,
    sample: Annotated[int, typer.Option()] = 10,
) -> None:
    """Restore YAMTrack Lists into Jellyfin as Collections. Never destructive:
    an existing collection with a matching name is only ever added to."""
    source = YamtrackListsSource(yamtrack_dsn, yamtrack_user_id)
    with console.status(f"reading {source.describe()}..."):
        records = list(source.records())
    console.print(f"read {len(records)} lists from {source.describe()}")

    with JellyfinClient(jellyfin_url, jellyfin_api_key, jellyfin_user_id) as client:
        target = JellyfinCollectionsTarget(client)
        with console.status("fetching current Jellyfin library and collections..."):
            plan = target.plan(records)
        plan.source_description = source.describe()

        _print_collection_plan(plan, sample=sample)

        if not apply:
            console.print("\n[yellow]Dry run only -- pass --apply to actually write to Jellyfin.[/yellow]")
            return
        if not plan.actions:
            console.print("\nNothing to apply.")
            return

        console.print(f"\n[bold]Applying {len(plan.actions)} collection(s)...[/bold]")
        with console.status("writing to Jellyfin..."):
            target.apply(plan)
        _print_apply_results(plan, sample=sample)


def _print_collection_plan(plan: CollectionPlan, *, sample: int) -> None:
    console.print(f"\n[bold]{plan.summary()}[/bold]\n")

    if plan.actions:
        table = Table(title=f"Collections (showing up to {sample})")
        table.add_column("Name")
        table.add_column("Members")
        table.add_column("Resolved")
        table.add_column("Status")
        for a in plan.actions[:sample]:
            status = "update existing" if a.existing_target_id else "create new"
            table.add_row(a.record.name, str(len(a.members)), str(len(a.matched_ids)), status)
        console.print(table)

    if plan.skipped_empty:
        console.print(
            f"\n[yellow]{len(plan.skipped_empty)} collection(s) skipped entirely "
            f"-- no members resolved (showing up to {sample}):[/yellow]"
        )
        for r in plan.skipped_empty[:sample]:
            console.print(f"  {r.name} ({len(r.members)} member(s), none matched)")


def _print_apply_results(plan: CollectionPlan, *, sample: int) -> None:
    applied = sum(1 for a in plan.actions if a.outcome is ActionOutcome.APPLIED)
    failed = [a for a in plan.actions if a.outcome is ActionOutcome.FAILED]
    console.print(f"applied: {applied}   failed: {len(failed)}")
    for a in failed[:sample]:
        console.print(f"  [red]FAILED[/red] {a.record.name}: {a.error}")


if __name__ == "__main__":
    app()
