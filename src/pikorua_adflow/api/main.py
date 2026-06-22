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

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import STATIC_DIR
from .routes import (analytics, assets, audience, autopilot, campaigns, deploy,
                     pages, visuals)

app = FastAPI(
    title="Pikorua Campaign Portal",
    description="Internal tool — launches AI-generated ad campaigns for the Pikorua Realty team.",
    version="2.0.0",
)

# Static assets (app.js, bundled CSS/icons). Created on first import if absent.
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Register routers. Pages last so API routes take precedence on any path overlap.
app.include_router(campaigns.router)
app.include_router(visuals.router)
app.include_router(audience.router)
app.include_router(deploy.router)
app.include_router(analytics.router)
app.include_router(autopilot.router)
app.include_router(assets.router)
app.include_router(pages.router)
