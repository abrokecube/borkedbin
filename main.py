import asyncio
import logging
import uvicorn
import os
from contextvars import ContextVar
from datetime import datetime, timezone
from sqlmodel import Session, select
from database import engine, ContentItem
from config import settings
from utils import setup_logging

with open('pid', 'w') as f:
    f.write(str(os.getpid()))
    
# Configure logging
setup_logging(level=logging.INFO)
logger = logging.getLogger("main")

# Both uvicorn servers log under the same "uvicorn.*" logger names, so tag each record
# with the server that emitted it (e.g. "uvicorn.public.access").
# Server-level messages (startup/shutdown) are tagged via a context var set in the
# server's task; per-connection messages are tagged by the protocol class below, since
# the context var isn't reliably inherited by connection callbacks on every event loop.
current_server: ContextVar[str | None] = ContextVar("current_server", default=None)
_base_record_factory = logging.getLogRecordFactory()

def _record_factory(*args, **kwargs):
    record = _base_record_factory(*args, **kwargs)
    server = current_server.get()
    if server and record.name.startswith("uvicorn.") and not record.name.startswith(f"uvicorn.{server}."):
        record.name = f"uvicorn.{server}.{record.name[len('uvicorn.'):]}"
    return record

logging.setLogRecordFactory(_record_factory)

def http_protocol_for(server_name: str):
    """HTTP protocol class (same auto-selection as uvicorn) that logs under uvicorn.<server_name>.*"""
    try:
        import httptools  # noqa: F401
        from uvicorn.protocols.http.httptools_impl import HttpToolsProtocol as Base
    except ImportError:
        from uvicorn.protocols.http.h11_impl import H11Protocol as Base

    class ServerHttpProtocol(Base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.logger = logging.getLogger(f"uvicorn.{server_name}.error")
            self.access_logger = logging.getLogger(f"uvicorn.{server_name}.access")
            self.access_log = self.access_logger.hasHandlers()

    return ServerHttpProtocol

async def run_public():
    current_server.set("public")
    config = uvicorn.Config("main_public:app", host=settings.public_host, port=settings.public_port, log_config=None, http=http_protocol_for("public"))
    server = uvicorn.Server(config)
    await server.serve()

async def run_admin():
    current_server.set("admin")
    config = uvicorn.Config("main_admin:app", host=settings.admin_host, port=settings.admin_port, log_config=None, http=http_protocol_for("admin"))
    server = uvicorn.Server(config)
    await server.serve()

async def prune_expired_items():
    while True:
        try:
            with Session(engine) as session:
                now = datetime.now(timezone.utc)
                statement = select(ContentItem).where(ContentItem.expires_at <= now)  # type: ignore
                expired_items = session.exec(statement).all()
                if expired_items:
                    for item in expired_items:
                        # Attempt to remove associated file on disk
                        if item.file_path and os.path.exists(item.file_path):
                            try:
                                os.remove(item.file_path)
                            except OSError as e:
                                logger.warning(f"Failed to delete file {item.file_path}: {e}")
                        
                        session.delete(item)
                    session.commit()
                    logger.info(f"Pruned {len(expired_items)} expired items.")
        except Exception as e:
            logger.error(f"Error while pruning expired items: {e}")
        
        # Wait specified seconds before checking again
        await asyncio.sleep(settings.prune_interval_seconds)

async def main():
    logger.info("Starting BorkedBin Servers and Background Tasks...")
    # Gather runs all awaitables concurrently
    await asyncio.gather(
        run_public(),
        run_admin(),
        prune_expired_items()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Servers stopped.")
