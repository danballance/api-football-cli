"""HttpxFootballApi: the real api-football adapter.

The single most important client-layer rule (architecture §2): api-football
reports request errors inside an HTTP 200 envelope, so the ``errors`` field is
inspected on every response and any content there raises immediately.
Rate-limit headers are tracked so ingestion can throttle / fail fast.
"""

from __future__ import annotations

from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from api_football_cli.adapters.outbound.apifootball.wire import (
    WireEventItem,
    WireFixtureItem,
    WireLeagueItem,
    WireStatusItem,
    WireTeamItem,
)
from api_football_cli.application.ports.football_api import ApiFootballError, FootballApi
from api_football_cli.domain.entities import (
    AccountStatus,
    FixtureSnapshot,
    LeagueWithSeasons,
    ObservedEvent,
    TeamWithVenue,
)

REMAINING_HEADER = "x-ratelimit-requests-remaining"

ItemT = TypeVar("ItemT", bound=BaseModel)


class HttpxFootballApi(FootballApi):  # noqa: F821
    def __init__(self, *, api_key: str, base_url: str, http_client: httpx.AsyncClient) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = http_client
        self._requests_remaining: int | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    def requests_remaining(self) -> int:
        if self._requests_remaining is None:
            raise ApiFootballError(
                f"api-football response did not include {REMAINING_HEADER!r}"
            )
        return self._requests_remaining

    async def _get(self, path: str, params: dict[str, str | int]) -> object:
        """GET one endpoint and return the envelope's ``response`` field."""
        response = await self._client.get(
            f"{self._base_url}{path}",
            params=params,
            headers={"x-apisports-key": self._api_key},
        )
        if response.status_code != 200:
            raise ApiFootballError(
                f"GET {path} returned HTTP {response.status_code}: {response.text[:300]}"
            )
        raw_remaining = response.headers.get(REMAINING_HEADER)
        if raw_remaining is None:
            raise ApiFootballError(f"GET {path} missing {REMAINING_HEADER!r} header")
        try:
            self._requests_remaining = int(raw_remaining)
        except ValueError as exc:
            raise ApiFootballError(
                f"GET {path} returned invalid {REMAINING_HEADER!r}: {raw_remaining!r}"
            ) from exc

        payload = response.json()
        if not isinstance(payload, dict):
            raise ApiFootballError(f"GET {path} returned a non-object payload: {payload!r}")
        errors = payload.get("errors")
        # Errors arrive inside HTTP 200: [] or {} when fine, otherwise populated.
        if errors:
            raise ApiFootballError(f"api-football reported errors on {path}: {errors!r}")
        return payload.get("response")

    async def _get_items(
        self, path: str, params: dict[str, str | int], item_type: type[ItemT]
    ) -> list[ItemT]:
        raw_items = await self._get(path, params)
        if not isinstance(raw_items, list):
            raise ApiFootballError(
                f"GET {path} returned an unexpected 'response' field: {raw_items!r}"
            )
        try:
            return [item_type.model_validate(raw) for raw in raw_items]
        except ValidationError as exc:
            raise ApiFootballError(f"GET {path} returned an unexpected shape: {exc}") from exc

    async def fixture(self, api_fixture_id: int) -> FixtureSnapshot:
        items = await self._get_items("/fixtures", {"id": api_fixture_id}, WireFixtureItem)
        if len(items) != 1:
            raise ApiFootballError(
                f"expected exactly one fixture for id={api_fixture_id}, got {len(items)}"
            )
        return items[0].to_domain()

    async def fixtures_events(self, api_fixture_id: int) -> list[ObservedEvent]:
        items = await self._get_items(
            "/fixtures/events", {"fixture": api_fixture_id}, WireEventItem
        )
        return [item.to_domain() for item in items]

    async def leagues(self, *, season: int) -> list[LeagueWithSeasons]:
        items = await self._get_items("/leagues", {"season": season}, WireLeagueItem)
        return [item.to_domain() for item in items]

    async def teams(self, *, league_api_id: int, season: int) -> list[TeamWithVenue]:
        items = await self._get_items(
            "/teams", {"league": league_api_id, "season": season}, WireTeamItem
        )
        return [item.to_domain() for item in items]

    async def fixtures_by_league(
        self, *, league_api_id: int, season: int
    ) -> list[FixtureSnapshot]:
        items = await self._get_items(
            "/fixtures", {"league": league_api_id, "season": season}, WireFixtureItem
        )
        return [item.to_domain() for item in items]

    async def account_status(self) -> AccountStatus:
        # /status responds with a single object in `response`, not a list.
        raw = await self._get("/status", {})
        try:
            return WireStatusItem.model_validate(raw).to_domain()
        except ValidationError as exc:
            raise ApiFootballError(f"/status returned an unexpected shape: {exc}") from exc
