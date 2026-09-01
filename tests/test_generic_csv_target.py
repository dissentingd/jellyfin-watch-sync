"""Exercises GenericCsvTarget, including a full round trip through
GenericCsvSource -- what this target writes has to be exactly what the
existing reader already expects, since that symmetry is the whole point of
the format being "generic" rather than YAMTrack-specific."""

from datetime import UTC, datetime

from jellyfin_watch_sync.models import MediaType, WatchRecord
from jellyfin_watch_sync.plan import ActionOutcome
from jellyfin_watch_sync.sources.generic_csv import GenericCsvSource
from jellyfin_watch_sync.targets.generic_csv import GenericCsvTarget


def test_writes_header_and_rows(tmp_path):
    out = tmp_path / "out.csv"
    target = GenericCsvTarget(out)
    records = [
        WatchRecord(media_type=MediaType.MOVIE, tmdb_id=68737, watched_at=datetime(2019, 3, 12, tzinfo=UTC), title="Seventh Son"),
        WatchRecord(media_type=MediaType.EPISODE, tmdb_id=111111, season=1, episode=1, watched_at=datetime(2025, 9, 28, tzinfo=UTC), play_count=2),
    ]

    plan = target.plan(records)
    target.apply(plan)

    assert all(a.outcome is ActionOutcome.APPLIED for a in plan.matched)
    text = out.read_text(encoding="utf-8")
    assert "media_type,tmdb_id,season,episode,watched_at,play_count,title" in text
    assert "movie,68737" in text
    assert "episode,111111,1,1" in text


def test_round_trips_through_generic_csv_source(tmp_path):
    out = tmp_path / "out.csv"
    original = [
        WatchRecord(media_type=MediaType.MOVIE, tmdb_id=68737, watched_at=datetime(2019, 3, 12, 23, 32, tzinfo=UTC), title="Seventh Son"),
        WatchRecord(media_type=MediaType.EPISODE, tmdb_id=111111, season=1, episode=1, watched_at=datetime(2025, 9, 28, tzinfo=UTC), play_count=3, title="Pilot"),
    ]

    target = GenericCsvTarget(out)
    target.apply(target.plan(original))

    read_back = list(GenericCsvSource(out).records())

    assert len(read_back) == 2
    assert {r.key for r in read_back} == {r.key for r in original}
    by_key = {r.key: r for r in read_back}
    assert by_key[original[0].key].watched_at == original[0].watched_at
    assert by_key[original[1].key].play_count == 3


def test_apply_overwrites_previous_file_contents(tmp_path):
    out = tmp_path / "out.csv"
    out.write_text("stale content that must not survive\n", encoding="utf-8")

    target = GenericCsvTarget(out)
    record = WatchRecord(media_type=MediaType.MOVIE, tmdb_id=68737, watched_at=datetime.now(UTC))
    target.apply(target.plan([record]))

    assert "stale content" not in out.read_text(encoding="utf-8")


def test_duplicate_records_are_deduped_before_writing(tmp_path):
    out = tmp_path / "out.csv"
    target = GenericCsvTarget(out)
    record = WatchRecord(media_type=MediaType.MOVIE, tmdb_id=68737, watched_at=datetime.now(UTC))

    plan = target.plan([record, record])

    assert len(plan.matched) == 1


def test_describe_includes_path(tmp_path):
    out = tmp_path / "out.csv"
    assert str(out) in GenericCsvTarget(out).describe()
