"""CLI entrypoint: ``python -m huible.api`` boots the uvicorn server.

Equivalent to ``uvicorn huible.api.app:app`` but reads host / port / log level /
reload from :class:`huible.api.settings.Settings` (``.env`` or environment).
"""

from __future__ import annotations

import uvicorn

from huible.api.settings import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "huible.api.app:app",
        host=settings.huible_host,
        port=settings.huible_port,
        log_level=settings.log_level.lower(),
        reload=settings.is_development,
    )


if __name__ == "__main__":
    main()
