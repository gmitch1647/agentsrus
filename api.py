"""
AgentsRus REST API
Lightweight FastAPI server that runs alongside the scheduler.
Lovable calls these endpoints to trigger agents and fetch status.
"""

import os
import threading
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from config import supabase

app = FastAPI(title="AgentsRus API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

API_SECRET_KEY = os.environ.get("API_SECRET_KEY", "")


def _require_auth(x_api_key: str | None):
    if not API_SECRET_KEY:
        return  # no key set — open (dev mode)
    if x_api_key != API_SECRET_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ── Health ────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


# ── Agents ────────────────────────────────────────────────────

@app.get("/agents")
def get_agents(x_api_key: str | None = Header(default=None)):
    _require_auth(x_api_key)
    result = supabase.table("agents").select("*").execute()
    return result.data or []


@app.post("/agents/{agent_type}/trigger")
def trigger_agent(agent_type: str, x_api_key: str | None = Header(default=None)):
    _require_auth(x_api_key)

    agent_map = {
        "scout":     "agents.scout_agent:run_all_scout_agents",
        "analyst":   "agents.analyst_agent:run_all_analyst_agents",
        "writer":    "agents.writer_agent:run_all_writer_agents",
        "tiktok":    "agents.tiktok_agent:run_all_tiktok_agents",
        "scheduler": "agents.scheduler_agent:run_all_scheduler_agents",
        "slack":     "agents.slack_agent:run_all_slack_agents",
    }

    if agent_type not in agent_map:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown agent '{agent_type}'. Valid: {list(agent_map)}"
        )

    module_path, fn_name = agent_map[agent_type].split(":")

    def _run():
        import importlib
        try:
            mod = importlib.import_module(module_path)
            getattr(mod, fn_name)()
            logger.success(f"[API] Manual trigger complete: {agent_type}")
        except Exception as e:
            logger.error(f"[API] Manual trigger failed for {agent_type}: {e}")

    threading.Thread(target=_run, daemon=True).start()
    logger.info(f"[API] Triggered {agent_type} agent via API")
    return {"status": "triggered", "agent": agent_type}


# ── Agent Runs ────────────────────────────────────────────────

@app.get("/runs")
def get_runs(limit: int = 50, x_api_key: str | None = Header(default=None)):
    _require_auth(x_api_key)
    result = (
        supabase.table("agent_runs")
        .select("*, agents(type)")
        .order("started_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


# ── Posts ─────────────────────────────────────────────────────

@app.get("/posts")
def get_posts(status: str = "pending", limit: int = 20,
              x_api_key: str | None = Header(default=None)):
    _require_auth(x_api_key)
    result = (
        supabase.table("generated_posts")
        .select("*")
        .eq("status", status)
        .order("generated_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []
