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
from .routes import (activity, analytics, assets, audience, autooptimiser, campaigns,
                     deploy, pages, users, visuals)
from .services import auth

# Paths reachable with no session cookie: the login/register flow, static assets,
# brand imagery, and the Meta webhook (an external service, not a browser session).
_PUBLIC_PATH_PREFIXES = ("/login", "/register", "/static", "/logo/", "/favicon.ico",
                         "/meta-lead-webhook", "/health")

# Paths that additionally require role == "admin" once authenticated.
_ADMIN_PATH_PREFIXES = ("/users", "/api/users")


# ── Scheduled jobs ────────────────────────────────────────────────────────────

def _daily_autooptimiser_run() -> None:
    """Daily pass at 7:30 AM IST (02:00 UTC). Auto-applies safe fixes."""
    from pikorua_adflow.analytics import activity_log
    try:
        from .services import autooptimiser as ao
        result = ao.run_autooptimiser(apply_safe=True)
        applied = len(result.get("auto_applied", []))
        capi = result.get("capi", {}) or {}
        q = len((capi.get("qualified", {}) or {}).get("fired", []))
        d = len((capi.get("disqualified", {}) or {}).get("fired", []))
        activity_log.log_event(
            "scheduler_run",
            f"Daily auto-optimisation ran — {applied} fix"
            f"{'' if applied == 1 else 'es'} applied, "
            f"{len(result.get('campaigns', []))} campaigns reviewed",
            detail=f"CAPI: {q} good + {d} bad lead events · CRM: {result.get('crm_source', '—')}",
            status="ok",
            meta={"auto_applied": applied, "capi_qualified": q, "capi_disqualified": d,
                  "crm_source": result.get("crm_source", ""),
                  "crm_fallback": result.get("crm_fallback", False)},
        )
    except Exception as exc:
        print(f"[scheduler] daily autooptimiser error: {exc}")
        activity_log.log_event("scheduler_error",
                               "Daily auto-optimisation failed",
                               detail=str(exc), status="error")


def _monthly_retarget() -> None:
    """Rung 12 — refresh targeting on all active campaigns every 30 days."""
    from pikorua_adflow.analytics import activity_log
    try:
        from .services import autooptimiser as ao
        result = ao.periodic_retarget_all()
        n = len(result.get("results", []))
        print(f"[scheduler] monthly retarget: {n} campaigns processed "
              f"(dry_run={result.get('dry_run')})")
        activity_log.log_event(
            "retarget",
            f"Monthly targeting refresh — {n} campaign{'' if n == 1 else 's'} processed",
            detail=f"dry_run={result.get('dry_run')}"
                   + (f" · {result.get('reason')}" if result.get("skipped") else ""),
            status="ok",
            meta={"processed": n, "skipped": result.get("skipped", False)},
        )
    except Exception as exc:
        print(f"[scheduler] monthly retarget error: {exc}")
        activity_log.log_event("scheduler_error",
                               "Monthly targeting refresh failed",
                               detail=str(exc), status="error")


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
        # Monthly retarget on a fixed calendar day (1st, 02:30 UTC). An "interval,
        # days=30" job resets its next-run to now+30d on every process start, so with
        # frequent redeploys it never fired; an anchored cron is restart-proof. The
        # job itself is idempotent (periodic_retarget_all skips if it ran recently).
        scheduler.add_job(_monthly_retarget, "cron",
                          day=1, hour=2, minute=30, id="monthly_retarget",
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
    """Per-user session gate, plus a role check for admin-only paths."""
    path = request.url.path
    if path == "/" or path.startswith(_PUBLIC_PATH_PREFIXES):
        return await call_next(request)

    from fastapi.responses import JSONResponse

    payload = auth.verify_session_token(request.cookies.get(auth.COOKIE_NAME))
    if payload is None:
        if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse(url=f"/login?next={path}")
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)

    if path.startswith(_ADMIN_PATH_PREFIXES) and payload.get("role") != "admin":
        if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse(url="/portal")
        return JSONResponse({"detail": "Admin access required"}, status_code=403)

    return await call_next(request)

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
app.include_router(activity.router)
app.include_router(assets.router)
app.include_router(users.router)
app.include_router(pages.router)
