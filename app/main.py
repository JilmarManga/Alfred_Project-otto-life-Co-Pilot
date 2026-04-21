from dotenv import load_dotenv
load_dotenv()

import logging
import os
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.whatsapp_webhook import router as whatsapp_router
from app.api.oauth_routes import router as oauth_router
from app.api.cron_routes import router as cron_router, run_cron_job

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_cron_job, CronTrigger(minute="0,15,30,45"), id="cron_job")
    scheduler.start()
    logger.info("Scheduler started — cron_job every 15 min at :00/:15/:30/:45")
    yield
    scheduler.shutdown()
    logger.info("Scheduler stopped")


app = FastAPI(title="otto API", lifespan=lifespan)

_static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=_static_dir), name="static")

app.include_router(whatsapp_router, prefix="", tags=["WhatsApp Webhook"])
app.include_router(oauth_router, prefix="", tags=["Google OAuth"])
app.include_router(cron_router, prefix="", tags=["Cron"])


@app.get("/")
async def health() -> dict:
    """
    Health check endpoint used by Railway and monitoring systems
    to verify the service is alive.
    """
    return {"status": "otto Running"}
