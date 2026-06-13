"""Replay adapter: accelerated clock, cumulative events, terminal status."""

from __future__ import annotations

from pathlib import Path

import pytest

from api_football_cli.adapters.outbound.apifootball.fake import FakeFootballApi, ReplayFile
from api_football_cli.application.ports.football_api import ApiFootballError
from api_football_cli.domain.entities import FixtureStatus
from tests.factories import AWAY, make_event, make_snapshot

REPLAY = ReplayFile(
    fixture=make_snapshot(api_fixture_id=1001),
    events=(
        make_event(elapsed=9),
        make_event(elapsed=33, team=AWAY),
        make_event(elapsed=67, detail="Penalty"),
        make_event(elapsed=90, extra=3, team=AWAY),
    ),
)

DEMO_REPLAY_PATH = Path(__file__).resolve().parents[1] / "examples" / "replay-demo.json"


def make_fake(step: int) -> FakeFootballApi:
    return FakeFootballApi(replay=REPLAY, minutes_per_poll=step)


async def test_clock_advances_and_events_accumulate() -> None:
    fake = make_fake(30)

    first = await fake.fixture(1001)
    assert first.status is FixtureStatus.FIRST_HALF
    assert first.elapsed == 30
    assert len(await fake.fixtures_events(1001)) == 1
    assert (first.home_goals, first.away_goals) == (1, 0)

    second = await fake.fixture(1001)
    assert second.status is FixtureStatus.SECOND_HALF
    assert (second.home_goals, second.away_goals) == (1, 1)

    third = await fake.fixture(1001)
    assert third.status is FixtureStatus.FULL_TIME
    assert third.elapsed == 90
    assert len(await fake.fixtures_events(1001)) == 4
    assert (third.home_goals, third.away_goals) == (2, 2)


async def test_clock_never_passes_the_end() -> None:
    fake = make_fake(200)
    snapshot = await fake.fixture(1001)
    assert snapshot.status is FixtureStatus.FULL_TIME
    assert fake.minute == 90
    again = await fake.fixture(1001)
    assert again.status is FixtureStatus.FULL_TIME


async def test_wrong_fixture_id_fails_fast() -> None:
    fake = make_fake(10)
    with pytest.raises(ApiFootballError, match="holds fixture 1001"):
        await fake.fixture(999)
    with pytest.raises(ApiFootballError, match="holds fixture 1001"):
        await fake.fixtures_events(999)


def test_step_must_be_positive() -> None:
    with pytest.raises(ApiFootballError, match="minutes_per_poll"):
        FakeFootballApi(replay=REPLAY, minutes_per_poll=0)


async def test_reference_endpoints_are_unsupported() -> None:
    fake = make_fake(10)
    assert fake.requests_remaining() is None
    with pytest.raises(ApiFootballError, match="replay mode"):
        await fake.leagues(season=2025)
    with pytest.raises(ApiFootballError, match="replay mode"):
        await fake.teams(league_api_id=1, season=2025)
    with pytest.raises(ApiFootballError, match="replay mode"):
        await fake.fixtures_by_league(league_api_id=1, season=2025)
    with pytest.raises(ApiFootballError, match="replay mode"):
        await fake.account_status()


def test_replay_file_round_trips(tmp_path: Path) -> None:
    target = tmp_path / "replay.json"
    REPLAY.dump(target)
    loaded = ReplayFile.load(target)
    assert loaded == REPLAY


def test_replay_file_missing_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(ApiFootballError, match="not found"):
        ReplayFile.load(tmp_path / "missing.json")


def test_shipped_demo_replay_is_valid() -> None:
    replay = ReplayFile.load(DEMO_REPLAY_PATH)
    assert replay.fixture.api_fixture_id == 999001
    assert len(replay.events) == 10
