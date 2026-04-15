from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from app.api.whatsapp_webhook import router as whatsapp_router
from app.api.oauth_routes import router as oauth_router
from app.api.cron_routes import router as cron_router


app = FastAPI(title="otto API")
app.include_router(whatsapp_router, prefix="", tags=["WhatsApp Webhook"])
app.include_router(oauth_router, prefix="", tags=["Google OAuth"])
app.include_router(cron_router, prefix="", tags=["Cron"])


@app.get("/")
async def health() -> dict:
    """
    Health check endpoint used by Cloud Run and monitoring systems
    to verify the service is alive.
    """
    return {"status": "otto Running"}

