"""ASGI entry point: `uvicorn gateway.main:app` (or `python -m gateway.main`)."""

from __future__ import annotations

from .app import create_app

app = create_app()


def main() -> None:
    import uvicorn

    settings = app.state.settings
    uvicorn.run(app, host="127.0.0.1", port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
