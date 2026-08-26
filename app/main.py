from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import chat, restaurants
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.database import Base, engine
import app.db.models  # Register model metadata before creating tables.

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse, tags=["ui"])
async def read_root(request: Request):
    return templates.TemplateResponse(
        request=request, 
        name="index.html", 
        context={"title": settings.app_name}
    )

for router in (chat.router, restaurants.router):
    app.include_router(router, prefix=settings.api_prefix)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}
