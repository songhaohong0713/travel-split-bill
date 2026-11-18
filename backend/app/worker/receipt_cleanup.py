"""Retry the durable deletion queue: python -m app.worker.receipt_cleanup."""

import asyncio
import logging

from sqlalchemy.exc import SQLAlchemyError

from app.core.receipt_files import drain_cleanup
from app.db.session import SessionLocal
from app.providers.cloudbase_pg import CloudBasePgClient
from app.providers.receipt_storage import ReceiptStorage


async def cleanup_loop(application):
    while True:
        await asyncio.sleep(60)
        try:
            with SessionLocal() as session:
                await drain_cleanup(
                    getattr(application.state, "cloudbase_pg", None),
                    session,
                    application.state.receipt_storage,
                )
        except SQLAlchemyError:
            logging.getLogger(__name__).warning(
                "receipt_cleanup_database_retry_pending"
            )


async def main():
    cloud = CloudBasePgClient.from_environment()
    storage = ReceiptStorage.from_environment()
    try:
        with SessionLocal() as session:
            await drain_cleanup(cloud, session, storage)
    finally:
        await cloud.aclose()
        await storage.aclose()


if __name__ == "__main__":
    asyncio.run(main())
