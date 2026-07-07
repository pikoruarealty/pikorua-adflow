"""
FastAPI portal entry point.

Thin composition root: configure dotenv + litellm, mount static assets, and
register the route modules. All business logic lives in `services/`, all request
handlers in `routes/`, and all paths/constants in `config.py`.

Run with:
    uvicorn pikorua_adflow.api.main:app --reload --port 8000
Then open http://localhost:8000 (redirects to the campaign portal).
"""

from __future__ import annotations

# dotenv + litellm must be configured before any crew/LLM import happens.
from dotenv import load_dotenv

load_dotenv()

import litellm

litellm.drop_params = True
litellm.num_retries = 6
litellm.request_timeout = 120

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import STATIC_DIR
from .routes import (analytics, assets, audience, autooptimiser, campaigns, deploy,
                     pages, visuals, webhook)
from .services import auth

# Paths reachable with no session cookie: the login flow itself, static assets,
# brand imagery, and the Meta webhook (an external service, not a browser session).
_PUBLIC_PATH_PREFIXES = ("/login", "/static", "/logo/", "/favicon.ico",
                         "/meta-lead-webhook", "/health")


# ── Scheduled jobs ────────────────────────────────────────────────────────────

def _daily_autooptimiser_run() -> None:
    """Daily pass at 7:30 AM IST (02:00 UTC). Auto-applies safe fixes."""
    try:
        from .services import autooptimiser as ao
        ao.run_autooptimiser(apply_safe=True)
    except Exception as exc:
        print(f"[scheduler] daily autooptimiser error: {exc}")


def _monthly_retarget() -> None:
    """Rung 12 — refresh targeting on all active campaigns every 30 days."""
    try:
        from .services import autooptimiser as ao
        result = ao.periodic_retarget_all()
        n = len(result.get("results", []))
        print(f"[scheduler] monthly retarget: {n} campaigns processed "
              f"(dry_run={result.get('dry_run')})")
    except Exception as exc:
        print(f"[scheduler] monthly retarget error: {exc}")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Start APScheduler on startup; shut it down cleanly on exit."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler(timezone="UTC")
        # 7:30 AM IST = 02:00 UTC
        scheduler.add_job(_daily_autooptimiser_run, "cron",
                          hour=2, minute=0, id="daily_autooptimiser",
                          replace_existing=True)
        # Every 30 days (first run 30 days after server start)
        scheduler.add_job(_monthly_retarget, "interval",
                          days=30, id="monthly_retarget",
                          replace_existing=True)
        scheduler.start()
        print("[scheduler] APScheduler started — daily 02:00 UTC + 30-day retarget.")
    except Exception as exc:
        print(f"[scheduler] Could not start APScheduler: {exc}")
        scheduler = None  # type: ignore[assignment]

    yield

    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Pikorua Campaign Portal",
    description="Internal tool — launches AI-generated ad campaigns for the Pikorua Realty team.",
    version="2.0.0",
    lifespan=_lifespan,
)


@app.middleware("http")
async def require_session(request: Request, call_next):
    """Single shared-password gate — no accounts, just a signed session cookie."""
    path = request.url.path
    if path == "/" or path.startswith(_PUBLIC_PATH_PREFIXES):
        return await call_next(request)

    if auth.verify_session_token(request.cookies.get(auth.COOKIE_NAME)):
        return await call_next(request)

    if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
        return RedirectResponse(url=f"/login?next={path}")
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": "Not authenticated"}, status_code=401)

# Static assets (app.js, bundled CSS/icons). Created on first import if absent.
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Register routers. Pages last so API routes take precedence on any path overlap.
app.include_router(campaigns.router)
app.include_router(visuals.router)
app.include_router(audience.router)
app.include_router(deploy.router)
app.include_router(analytics.router)
app.include_router(autooptimiser.router)
app.include_router(webhook.router)
app.include_router(assets.router)
app.include_router(pages.router)
