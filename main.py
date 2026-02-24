import asyncio
import logging
import uvicorn

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("brokebin.runner")

async def run_public():
    config = uvicorn.Config("main_public:app", host="0.0.0.0", port=8000)
    server = uvicorn.Server(config)
    await server.serve()

async def run_admin():
    config = uvicorn.Config("main_admin:app", host="127.0.0.1", port=8001)
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    logger.info("Starting Brokebin Servers...")
    # Gather runs both awaitables concurrently
    await asyncio.gather(
        run_public(),
        run_admin()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Servers stopped.")
