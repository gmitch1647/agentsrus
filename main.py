"""
AgentsRus — Main Runner
Orchestrates all 5 agents on their schedules. Runs 24/7 on Railway.
"""

import schedule
import time
import os
import threading
from loguru import logger

from agents.scout_agent     import run_all_scout_agents
from agents.analyst_agent   import run_all_analyst_agents
from agents.writer_agent    import run_all_writer_agents
from agents.tiktok_agent    import run_all_tiktok_agents
from agents.scheduler_agent import run_all_scheduler_agents
from agents.slack_agent     import run_all_slack_agents, start_socket_mode
from slack_bot              import start_slack_bot
import uvicorn
from api                    import app as fastapi_app


def safe_run(fn, name: str):
    try:
        logger.info(f"━━━ Starting {name} ━━━")
        fn()
        logger.success(f"━━━ {name} complete ━━━")
    except Exception as e:
        logger.error(f"━━━ {name} CRASHED: {e} ━━━")


def run_startup_sequence():
    logger.info("AgentsRus starting — running initial pipeline...")
    safe_run(run_all_scout_agents,     "Scout Agent")
    time.sleep(60)
    safe_run(run_all_analyst_agents,   "Analyst Agent")
    time.sleep(30)
    safe_run(run_all_writer_agents,    "Writer Agent")
    time.sleep(30)
    safe_run(run_all_tiktok_agents,    "TikTok Agent")
    safe_run(run_all_scheduler_agents, "Scheduler Agent")
    safe_run(run_all_slack_agents,     "Slack Agent")
    logger.success("Initial pipeline complete — switching to scheduled mode")


if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("  AgentsRus Backend Starting")
    logger.info("=" * 50)

    required = [
        "SUPABASE_URL", "SUPABASE_SERVICE_KEY",
        "APIFY_API_TOKEN", "ANTHROPIC_API_KEY", "BUFFER_ACCESS_TOKEN",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        logger.error(f"Missing env vars: {', '.join(missing)}")
        exit(1)

    skip = os.environ.get("SKIP_STARTUP_RUN", "false").lower() == "true"
    if not skip:
        run_startup_sequence()

    # Start FastAPI in background thread so Lovable can trigger agents
    port = int(os.environ.get("PORT", 8000))
    api_thread = threading.Thread(
        target=lambda: uvicorn.run(fastapi_app, host="0.0.0.0", port=port, log_level="warning"),
        daemon=True,
    )
    api_thread.start()
    logger.info(f"[API] REST API running on port {port}")

    start_socket_mode()

    slack_thread = threading.Thread(target=start_slack_bot, daemon=True)
    slack_thread.start()
    logger.info("[Slack] Bot running in background")

    schedule.every(2).hours.at(":00").do(lambda: safe_run(run_all_scout_agents,     "Scout Agent"))
    schedule.every(2).hours.at(":30").do(lambda: safe_run(run_all_analyst_agents,   "Analyst Agent"))
    schedule.every(2).hours.at(":00").do(lambda: safe_run(run_all_writer_agents,    "Writer Agent"))
    schedule.every(2).hours.at(":30").do(lambda: safe_run(run_all_tiktok_agents,    "TikTok Agent"))
    schedule.every(30).minutes.do(lambda:        safe_run(run_all_scheduler_agents, "Scheduler Agent"))
    schedule.every(5).minutes.do(lambda:         safe_run(run_all_slack_agents,     "Slack Agent"))

    logger.info("Agents running on schedule — Scout/Writer every 2h, Scheduler every 30min, Slack every 5min")

    while True:
        schedule.run_pending()
        time.sleep(30)
