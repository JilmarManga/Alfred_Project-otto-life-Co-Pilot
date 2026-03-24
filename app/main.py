from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from app.api.whatsapp_webhook import router as whatsapp_router


app = FastAPI(title="Alfred API")
app.include_router(whatsapp_router, prefix="", tags=["WhatsApp Webhook"])


@app.get("/")
async def health() -> dict:
    """
    Health check endpoint used by Cloud Run and monitoring systems
    to verify the service is alive.
    """
    return {"status": "Alfred Running"}

