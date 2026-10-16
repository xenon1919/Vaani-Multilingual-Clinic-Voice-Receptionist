"""FastAPI app: serves the plain HTML/JS frontend and the call WebSocket."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from app.admin_api import router as admin_router
from app.db import close_pool, init_pool
from app.live_session import run_call

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_pool()
    yield
    await close_pool()


app = FastAPI(lifespan=lifespan)


@app.websocket("/ws/call")
async def ws_call(websocket: WebSocket) -> None:
    await websocket.accept()
    logger.info("call started")
    try:
        await run_call(websocket)
    except WebSocketDisconnect:
        pass
    finally:
        logger.info("call ended")


app.include_router(admin_router)

app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
