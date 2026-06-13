"""HttpxFootballApi against a mock transport: parsing, errors-in-200, quota."""

from __future__ import annotations

import json

import httpx
import pytest
from api_football_cli.adapters.outbound.apifootball.http import HttpxFootballApi
from api_football_cli.application.ports.football_api import ApiFootballError
from api_football_cli.domain.entities import FixtureStatus

BASE_URL = "https://api.test"

FIXTURE_ITEM = {
    "fixture": {
        "id": 1001,
        "referee": "A. Whistler",
        "timezone": "UTC",
        "date": "2025-08-16T14:00:00+00:00",
        "timestamp": 1755352800,
        "periods": {"first": None, "second": None},
        "venue": {"id": 77, "name": "Riverton Park", "city": "Riverton"},
        "status": {"long": "Second Half", "short": "2H", "elapsed": 67, "extra": None},
    },
    "league": {
        "id": 9990,
        "name": "Demo Premier League",
        "country": "Demoland",
        "logo": "x.png",
        "flag": "y.svg",
        "season": 2025,
        "round": "Regular Season - 1",
    },
    "teams": {
        "home": {"id": 501, "name": "Riverton Albion", "logo": "h.png", "winner": True},
        "away": {"id": 502, "name": "Kingsport Wanderers", "logo": "a.png", "winner": False},
    },
    "goals": {"home": 2, "away": 1},
    "score": {
        "halftime": {"home": 1, "away": 1},
        "fulltime": {"home": None, "away": None},
        "extratime": {"home": None, "away": None},
        "penalty": {"home": None, "away": None},
    },
}

EVENT_ITEM = {
    "time": {"elapsed": 9, "extra": None},
    "team": {"id": 501, "name": "Riverton Albion", "logo": "h.png"},
    "player": {"id": 70001, "name": "D. Mercer"},
    "assist": {"id": 70002, "name": "L. Carter"},
    "type": "Goal",
    "detail": "Normal Goal",
    "comments": None,
}

LEAGUE_ITEM = {
    "league": {"id": 9990, "name": "Demo Premier League", "type": "League", "logo": "l.png"},
    "country": {"name": "Demoland", "code": "DM", "flag": "f.svg"},
    "seasons": [
        {
            "year": 2025,
            "start": "2025-08-01",
            "end": "2026-05-20",
            "current": True,
            "coverage": {
                "fixtures": {
                    "events": True,
                    "lineups": True,
                    "statistics_fixtures": False,
                    "statistics_players": False,
                },
                "standings": True,
                "players": True,
                "top_scorers": True,
                "top_assists": True,
                "top_cards": True,
                "injuries": False,
                "predictions": True,
                "odds": False,
            },
        }
    ],
}

TEAM_ITEM = {
    "team": {
        "id": 501,
        "name": "Riverton Albion",
        "code": "RIV",
        "country": "Demoland",
        "founded": 1901,
        "national": False,
        "logo": "h.png",
    },
    "venue": {
        "id": 77,
        "name": "Riverton Park",
        "address": "1 Park Lane",
        "city": "Riverton",
        "capacity": 30000,
        "surface": "grass",
        "image": "v.png",
    },
}

STATUS_RESPONSE = {
    "account": {"firstname": "Dan", "lastname": "B", "email": "dan@example.com"},
    "subscription": {"plan": "Pro", "end": "2026-12-31T00:00:00+00:00", "active": True},
    "requests": {"current": 12, "limit_day": 7500},
}


def envelope(response: object) -> dict[str, object]:
    items = response if isinstance(response, list) else [response]
    return {
        "get": "test",
        "parameters": {},
        "errors": [],
        "results": len(items) if isinstance(response, list) else 1,
        "paging": {"current": 1, "total": 1},
        "response": response,
    }


def make_api(handler: httpx.MockTransport) -> HttpxFootballApi:
    return HttpxFootballApi(
        api_key="test-key",
        base_url=BASE_URL,
        http_client=httpx.AsyncClient(transport=handler),
    )


async def test_fixture_parses_and_tracks_quota_and_sends_key() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers["x-apisports-key"]
        seen["id"] = request.url.params["id"]
        return httpx.Response(
            200,
            json=envelope([FIXTURE_ITEM]),
            headers={"x-ratelimit-requests-remaining": "42"},
        )

    api = make_api(httpx.MockTransport(handler))
    snapshot = await api.fixture(1001)

    assert seen == {"key": "test-key", "id": "1001"}
    assert snapshot.api_fixture_id == 1001
    assert snapshot.status is FixtureStatus.SECOND_HALF
    assert snapshot.elapsed == 67
    assert snapshot.home.name == "Riverton Albion"
    assert snapshot.home_goals == 2
    assert snapshot.league.season == 2025
    assert api.requests_remaining() == 42
    await api.aclose()


async def test_fixture_requires_exactly_one_item() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=envelope([]))
    )
    api = make_api(transport)
    with pytest.raises(ApiFootballError, match="exactly one fixture"):
        await api.fixture(1001)


async def test_errors_inside_http_200_raise() -> None:
    payload = envelope([])
    payload["errors"] = {"token": "Error/Missing application key."}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    api = make_api(transport)
    with pytest.raises(ApiFootballError, match="Missing application key"):
        await api.fixture(1001)


async def test_non_200_raises() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, text="server exploded")
    )
    api = make_api(transport)
    with pytest.raises(ApiFootballError, match="HTTP 500"):
        await api.fixtures_events(1001)


async def test_non_object_payload_raises() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=json.dumps([1, 2]))
    )
    api = make_api(transport)
    with pytest.raises(ApiFootballError, match="non-object"):
        await api.fixture(1001)


async def test_non_list_response_field_raises() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=envelope("nope"))
    )
    api = make_api(transport)
    with pytest.raises(ApiFootballError, match="unexpected 'response'"):
        await api.fixtures_events(1001)


async def test_shape_mismatch_raises() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=envelope([{"time": {"elapsed": 9}}]))
    )
    api = make_api(transport)
    with pytest.raises(ApiFootballError, match="unexpected shape"):
        await api.fixtures_events(1001)


async def test_events_leagues_teams_and_league_fixtures_parse() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/fixtures/events":
            return httpx.Response(200, json=envelope([EVENT_ITEM]))
        if path == "/leagues":
            return httpx.Response(200, json=envelope([LEAGUE_ITEM]))
        if path == "/teams":
            return httpx.Response(200, json=envelope([TEAM_ITEM]))
        if path == "/fixtures":
            return httpx.Response(200, json=envelope([FIXTURE_ITEM]))
        raise AssertionError(f"unexpected path {path}")

    api = make_api(httpx.MockTransport(handler))

    events = await api.fixtures_events(1001)
    assert len(events) == 1
    assert events[0].assist is not None and events[0].assist.name == "L. Carter"

    leagues = await api.leagues(season=2025)
    assert leagues[0].league.country.code == "DM"
    assert leagues[0].seasons[0].coverage.events is True
    assert leagues[0].seasons[0].coverage.statistics_players is False

    teams = await api.teams(league_api_id=9990, season=2025)
    assert teams[0].team.code == "RIV"
    assert teams[0].venue is not None and teams[0].venue.name == "Riverton Park"

    fixtures = await api.fixtures_by_league(league_api_id=9990, season=2025)
    assert fixtures[0].api_fixture_id == 1001


async def test_account_status_parses_object_response() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=envelope(STATUS_RESPONSE))
    )
    api = make_api(transport)
    status = await api.account_status()
    assert status.account_name == "Dan B"
    assert status.plan == "Pro"
    assert status.requests_today == 12
    assert status.daily_limit == 7500


async def test_account_status_shape_mismatch_raises() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=envelope({"unexpected": True}))
    )
    api = make_api(transport)
    with pytest.raises(ApiFootballError, match="/status"):
        await api.account_status()
