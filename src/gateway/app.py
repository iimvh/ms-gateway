"""App factory, shared state, and the disconnect policy's one helper."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .config import Settings
from .errors import AnthropicError
from .log import configure_logging, get_logger
from .tap import Tap
from .upstream import UpstreamClient

log = get_logger("app")

# A client can hang up while its M365 turn is still running. The turn must finish
# (spec §7.10), so its task is kept referenced until it settles — otherwise the
# event loop may collect it mid-flight.
_orphans: set[asyncio.Task[Any]] = set()


def _retire(task: asyncio.Task[Any]) -> None:
    _orphans.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        log.warning("a discarded upstream turn failed: %s", error)


async def run_shielded(coro: Any) -> Any:
    """Await `coro`, but do not propagate the caller's cancellation into it.

    Propagating a disconnect upstream would leave M365 believing a message was
    sent that the proxy never counted, so the next delta arrives as a bare tool
    result (spec §7.10). The turn runs to completion and its result is dropped.
    """
    task = asyncio.ensure_future(coro)
    _orphans.add(task)
    task.add_done_callback(_retire)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        log.warning("client disconnected: letting the M365 turn finish and discarding its result")
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await app.state.upstream.aclose()


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings if settings is not None else Settings.from_env()
    configure_logging()

    app = FastAPI(
        title="m365-anthropic-gateway",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.upstream = UpstreamClient(resolved)
    app.state.tap = Tap(resolved)
    app.state.known_models = None
    app.state.known_models_at = 0.0

    @app.exception_handler(AnthropicError)
    async def _anthropic_error(_: Request, exc: AnthropicError) -> JSONResponse:
        return JSONResponse(exc.to_body(), status_code=exc.status, headers=exc.headers())

    # Imported here: the route module needs `run_shielded` from this module, and
    # this module only needs the route registration once the app exists.
    from . import routes

    routes.register(app)
    return app
