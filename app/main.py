from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.security import get_current_user
from app.db.database import Base, engine, get_db
import app.db.models  # Register model metadata before creating tables.
from app.api import auth, chat, restaurants

settings = get_settings()

# ─── Centralized Project Dependencies ───────────────────────────────────────
# All project dependencies (e.g. database session, authentication)
# are defined, listed, and re-exported here in main.py as the single source of truth.

api_dependencies = [Depends(get_db)]
protected_dependencies = [Depends(get_db), Depends(get_current_user)]

__all__ = [
    "get_db",
    "get_current_user",
    "api_dependencies",
    "protected_dependencies",
]


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/", tags=["ui"])
async def read_root():
    """Redirect unauthenticated users to the login page."""
    return RedirectResponse(url="/login", status_code=302)


@app.get("/dashboard", response_class=HTMLResponse, tags=["ui"])
async def dashboard(request: Request):
    """Main dashboard — served after successful login."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"title": settings.app_name}
    )


@app.get("/login", response_class=HTMLResponse, tags=["ui"])
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={})


@app.get("/register", response_class=HTMLResponse, tags=["ui"])
async def register_page(request: Request):
    return templates.TemplateResponse(request=request, name="register.html", context={})


# Public authentication router (registration & login open with get_db dependency)
app.include_router(auth.router, prefix=settings.api_prefix, dependencies=api_dependencies)

# Protected API routers locked centrally via main.py protected_dependencies list
for router in (chat.router, restaurants.router):
    app.include_router(router, prefix=settings.api_prefix, dependencies=protected_dependencies)
app.include_router(chat.websocket_router, prefix=settings.api_prefix)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}

