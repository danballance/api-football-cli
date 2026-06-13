"""FastAPI driving adapter: routers translate HTTP/SSE into use-case calls.

No business logic lives here (architecture §9).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from api_football_cli.adapters.inbound.web.schemas import (
    CommentaryDTO,
    CommentatorDTO,
    EventDTO,
    FixtureDTO,
)
from api_football_cli.adapters.inbound.web.sse import commentary_sse
from api_football_cli.application.ports.repositories import (
    CommentaryRepository,
    CommentatorRepository,
    EventRepository,
    FixtureRepository,
    NotFoundError,
)
from api_football_cli.application.services.stream_commentary import StreamCommentary


class WebDeps:
    """Everything the web adapter needs, injected by the composition root."""

    def __init__(
        self,
        *,
        fixtures: FixtureRepository,
        events: EventRepository,
        commentary: CommentaryRepository,
        commentators: CommentatorRepository,
        stream: StreamCommentary,
        sse_ping_seconds: float,
    ) -> None:
        self.fixtures = fixtures
        self.events = events
        self.commentary = commentary
        self.commentators = commentators
        self.stream = stream
        self.sse_ping_seconds = sse_ping_seconds


def _deps(request: Request) -> WebDeps:
    deps = request.app.state.deps
    if not isinstance(deps, WebDeps):
        raise RuntimeError("app.state.deps must be a WebDeps instance")
    return deps


def create_app(*, deps: WebDeps, frontend_dir: Path | None) -> FastAPI:
    app = FastAPI(title="Live AI Football Commentary", version="0.1.0")
    app.state.deps = deps

    @app.get("/fixtures")
    async def list_fixtures(request: Request) -> list[FixtureDTO]:
        fixtures = await _deps(request).fixtures.list_all()
        return [FixtureDTO.from_domain(fixture) for fixture in fixtures]

    @app.get("/fixtures/{fixture_id}")
    async def get_fixture(request: Request, fixture_id: int) -> FixtureDTO:
        try:
            fixture = await _deps(request).fixtures.get(fixture_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FixtureDTO.from_domain(fixture)

    @app.get("/fixtures/{fixture_id}/events")
    async def list_events(request: Request, fixture_id: int) -> list[EventDTO]:
        events = await _deps(request).events.list_for_fixture(fixture_id)
        return [EventDTO.from_domain(event) for event in events]

    @app.get("/commentators")
    async def list_commentators(request: Request) -> list[CommentatorDTO]:
        commentators = await _deps(request).commentators.list_all()
        return [CommentatorDTO.from_domain(commentator) for commentator in commentators]

    @app.get("/fixtures/{fixture_id}/commentary")
    async def list_commentary(
        request: Request, fixture_id: int, after_id: int = 0
    ) -> list[CommentaryDTO]:
        messages = await _deps(request).commentary.list_after(
            fixture_id=fixture_id, after_message_id=after_id
        )
        return [CommentaryDTO.from_domain(message) for message in messages]

    @app.get("/fixtures/{fixture_id}/commentary/stream")
    async def stream_commentary(
        request: Request,
        fixture_id: int,
        after_id: int = 0,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse:
        # Browser reconnects send Last-Event-ID; it wins over the query param.
        effective_after = int(last_event_id) if last_event_id is not None else after_id
        deps = _deps(request)
        frames = commentary_sse(
            stream=deps.stream,
            fixture_id=fixture_id,
            after_id=effective_after,
            ping_seconds=deps.sse_ping_seconds,
        )
        return StreamingResponse(
            frames,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if frontend_dir is not None:
        if not frontend_dir.is_dir():
            raise FileNotFoundError(f"frontend directory not found: {frontend_dir}")
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    return app
