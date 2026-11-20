import asyncio

import uvicorn

from .api import app
from .bot import run_bot
from .config import settings


async def main() -> None:
    config = uvicorn.Config(app, host=settings.api_host, port=settings.api_port, log_level="info")
    server = uvicorn.Server(config)
    await asyncio.gather(server.serve(), run_bot())


if __name__ == "__main__":
    asyncio.run(main())
