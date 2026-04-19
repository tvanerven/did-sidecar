from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import Base, engine
from app.routers import admin_router, did_log_router, prepublish_router, resolve_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title="DID PID Sidecar", lifespan=lifespan)
app.include_router(prepublish_router)
app.include_router(resolve_router)
app.include_router(did_log_router)
app.include_router(admin_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
