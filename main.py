from contextlib import asynccontextmanager

from fastapi import FastAPI

from database import Base, engine
import models  # noqa: F401 - imported so the Workout table is registered on Base


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once on startup: create any tables that don't exist yet.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/")
def read_root():
    return {"status": "ok"}
