"""
AgentsRus — Main Runner
Runs on Railway 24/7. Orchestrates all 5 agents on their schedules.

Schedule:
  Scout     → every 2 hours
  Analyst   → every 2 hours (30 min after Scout)
  Writer    → every 2 hours (60 min after Scout)
  TikTok    → every 2 hours (90 min after Writer)
  Scheduler → every 30 minutes
"""

import schedule
import time
import os
from loguru import logger

from agents.scout_agent     import run_all_scout_agents
from agents.analyst_agent   import run_all_analyst_agents
from agents.writer_agent    import run_all_writer_agents
from agents.tiktok_agent    import run_all_tiktok_agents
from agents.scheduler_agent import run_all_scheduler_agents


def safe_run(fn, name: str):
    """Wrap agent runs so one failure never kills the scheduler."""
    try:
        logger.info(f"━━━ Starting {name} ━━━")
        fn()
        logger.success(f"━━━ {name} complete ━━━")
    except Exception as e:
        logger.error(f"━━━ {name} CRASHED: {e} ━━━")


def run_scout():
    safe_run(run_all_scout_agents, "Scout Agent")

def run_analyst():
    safe_run(run_all_analyst_agents, "Analyst Agent")

def run_writer():
    safe_run(run_all_writer_agents, "Writer Agent")

def run_tiktok():
    safe_run(run_all_tiktok_agents, "TikTok Agent")

def run_scheduler():
    safe_run(run_all_scheduler_agents, "Scheduler Agent")


def setup_schedules():
    # Scout runs every 2 hours at :00
    schedule.every(2).hours.at(":00").do(run_scout)

    # Analyst runs every 2 hours at :30 (gives Scout 30 min head start)
    schedule.every(2).hours.at(":30").do(run_analyst)

    # Writer runs every 2 hours at :00 on odd hours (1 hour after analyst)
    schedule.every(2).hours.at(":00").do(run_writer)

    # TikTok runs every 2 hours at :30 on odd hours (after Writer)
    schedule.every(2).hours.at(":30").do(run_tiktok)

    # Scheduler runs every 30 minutes
    schedule.every(30).minutes.do(run_scheduler)


def run_startup_sequence():
    """
    On first boot, run each agent once in order so the pipeline
    is primed immediately without waiting for the schedule.
    """
    logger.info("🚀 AgentsRus starting up — running initial pipeline...")
    run_scout()
    time.sleep(60)   # give Scout time to write to DB
    run_analyst()
    time.sleep(30)
    run_writer()
    time.sleep(30)
    run_tiktok()
    run_scheduler()
    logger.success("✅ Initial pipeline complete — switching to scheduled mode")


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("  AgentsRus Backend Starting")
    logger.info("=" * 60)

    # Validate environment
    required = [
        "SUPABASE_URL",
        "SUPABASE_SERVICE_KEY",
        "APIFY_API_TOKEN",
        "ANTHROPIC_API_KEY",
        "BUFFER_ACCESS_TOKEN",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        logger.error(f"Missing environment variables: {', '.join(missing)}")
        logger.error("Set these in Railway → Variables before deploying")
        exit(1)

    # Run startup sequence then switch to schedule
    skip_startup = os.environ.get("SKIP_STARTUP_RUN", "false").lower() == "true"
    if not skip_startup:
        run_startup_sequence()

    setup_schedules()
    logger.info("📅 Schedules set — agents running autonomously")
    logger.info("  Scout    → every 2h at :00")
    logger.info("  Analyst  → every 2h at :30")
    logger.info("  Writer   → every 2h at :00 (offset)")
    logger.info("  TikTok   → every 2h at :30 (offset)")
    logger.info("  Scheduler → every 30 min")

    while True:
        schedule.run_pending()
        time.sleep(30)
