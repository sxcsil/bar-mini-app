import asyncio
import logging
import os

import uvicorn

from .api import app
from .bot import run_bot
from .config import settings


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    # Hosting platforms commonly inject PORT at runtime.
    port = int(os.getenv("PORT", str(settings.api_port)))
    config = uvicorn.Config(app, host=settings.api_host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await asyncio.gather(server.serve(), run_bot())


if __name__ == "__main__":
    asyncio.run(main())
