"""Local ASGI process entry point."""

from __future__ import annotations

import uvicorn

from .server import create_app


def main() -> None:
    app = create_app()
    settings = app.state.settings
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
