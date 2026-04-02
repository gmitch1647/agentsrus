"""
AgentsRus — Auto Seed
Runs on startup. Creates agent rows and brand profile if they don't exist.
Also ensures the trigger_run column exists and RLS is disabled.
"""

import os
from loguru import logger


AGENT_TYPES = [
    ("Scout Agent",     "scout",     '{"platforms":["tiktok","instagram","facebook","linkedin"],"min_engagement":1000}'),
    ("Analyst Agent",   "analyst",   '{"min_viral_score":55}'),
    ("Writer Agent",    "writer",    '{}'),
    ("TikTok Agent",    "tiktok",    '{"target_duration_secs":60}'),
    ("Scheduler Agent", "scheduler", '{}'),
    ("Slack Agent",     "slack",     '{}'),
]


def ensure_schema(supabase):
    """Add trigger_run column and disable RLS if needed."""
    try:
        # Add trigger_run column if missing
        supabase.rpc("exec_sql", {
            "sql": "ALTER TABLE agents ADD COLUMN IF NOT EXISTS trigger_run boolean NOT NULL DEFAULT false;"
        }).execute()
    except Exception:
        pass  # column may already exist or rpc not available

    try:
        tables = ["agents","agent_runs","scraped_trends","brand_profiles",
                  "generated_posts","tiktok_scripts","scheduled_posts","notifications"]
        for table in tables:
            supabase.rpc("exec_sql", {
                "sql": f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;"
            }).execute()
    except Exception:
        pass  # best effort


def seed(supabase):
    user_id = os.environ.get("SEED_USER_ID", "")
    if not user_id:
        # Try to get first user from auth
        try:
            result = supabase.table("agents").select("user_id").limit(1).execute()
            if result.data:
                user_id = result.data[0]["user_id"]
                logger.info(f"[Seed] Found existing user_id: {user_id}")
        except Exception:
            pass

    if not user_id:
        logger.warning("[Seed] No SEED_USER_ID set and no existing agents — skipping seed")
        return

    # Check if agents already exist
    try:
        existing = supabase.table("agents").select("id").eq("user_id", user_id).execute()
        if existing.data:
            logger.info(f"[Seed] Agents already exist ({len(existing.data)} rows) — skipping seed")
            # Still trigger analyst + writer if scout has run but they haven't
            try:
                unscored = supabase.table("scraped_trends").select("id").eq("viral_score", 0).limit(1).execute()
                if unscored.data:
                    supabase.table("agents").update({"trigger_run": True}).eq("user_id", user_id).in_("type", ["analyst", "writer"]).execute()
                    logger.info("[Seed] Triggered analyst + writer agents to process scraped trends")
            except Exception:
                pass
            return
    except Exception as e:
        logger.warning(f"[Seed] Could not check existing agents: {e}")
        return

    # Insert agent rows
    logger.info(f"[Seed] Seeding agents for user {user_id}")
    for name, agent_type, config in AGENT_TYPES:
        try:
            supabase.table("agents").insert({
                "user_id": user_id,
                "name":    name,
                "type":    agent_type,
                "status":  "idle",
                "config":  config,
            }).execute()
            logger.info(f"[Seed] Created {name}")
        except Exception as e:
            logger.warning(f"[Seed] Could not create {name}: {e}")

    # Insert brand profile if missing
    try:
        existing_profile = supabase.table("brand_profiles").select("id").eq("user_id", user_id).limit(1).execute()
        if not existing_profile.data:
            supabase.table("brand_profiles").insert({
                "user_id":    user_id,
                "brand_name": "AgentsRus",
                "persona":    "expert",
                "tone":       "educational and authoritative",
                "topics":     ["business funding","business credit","SBA loans","startup grants","cash flow"],
                "avoid_list": ["predatory lenders","guaranteed approval claims"],
                "is_default": True,
            }).execute()
            logger.info("[Seed] Created default brand profile")
    except Exception as e:
        logger.warning(f"[Seed] Could not create brand profile: {e}")

    logger.success("[Seed] Done")
