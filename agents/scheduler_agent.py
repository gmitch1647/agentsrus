"""
Scheduler Agent
Runs every 30 minutes. Pushes approved scheduled posts to Buffer API.
Updates status to posted or failed.
"""

from datetime import datetime, timezone
from config import supabase, BUFFER_ACCESS_TOKEN
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential
import httpx

BUFFER_API = "https://api.bufferapp.com/1"


def get_all_scheduler_agents() -> list[dict]:
    result = (
        supabase.table("agents")
        .select("*")
        .eq("type", "scheduler")
        .neq("status", "paused")
        .execute()
    )
    return result.data or []


def start_run(agent_id: str) -> str:
    result = (
        supabase.table("agent_runs")
        .insert({
            "agent_id":   agent_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status":     "running",
        })
        .execute()
    )
    supabase.table("agents").update({
        "status":     "running",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", agent_id).execute()
    return result.data[0]["id"]


def finish_run(run_id: str, agent_id: str, found: int, processed: int, notes: str = ""):
    supabase.table("agent_runs").update({
        "completed_at":    datetime.now(timezone.utc).isoformat(),
        "status":          "success",
        "items_found":     found,
        "items_processed": processed,
        "notes":           notes,
    }).eq("id", run_id).execute()
    supabase.table("agents").update({
        "status":      "idle",
        "last_run_at": datetime.now(timezone.utc).isoformat(),
        "updated_at":  datetime.now(timezone.utc).isoformat(),
    }).eq("id", agent_id).execute()


def error_run(run_id: str, agent_id: str, error: str):
    supabase.table("agent_runs").update({
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "status":       "error",
        "notes":        error,
    }).eq("id", run_id).execute()
    supabase.table("agents").update({
        "status":    "error",
        "error_log": error,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", agent_id).execute()


def get_due_posts(user_id: str) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    result = (
        supabase.table("scheduled_posts")
        .select("*, generated_posts(content, edited_content, hashtags)")
        .eq("user_id", user_id)
        .eq("status", "queued")
        .lte("scheduled_for", now)
        .order("scheduled_for", desc=False)
        .limit(10)
        .execute()
    )
    return result.data or []


def get_buffer_profiles() -> dict:
    try:
        resp = httpx.get(
            f"{BUFFER_API}/profiles.json",
            params={"access_token": BUFFER_ACCESS_TOKEN},
            timeout=15,
        )
        resp.raise_for_status()
        return {p.get("service", "").lower(): p["id"] for p in resp.json()}
    except Exception as e:
        logger.error(f"[Scheduler] Failed to fetch Buffer profiles: {e}")
        return {}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=20))
def push_to_buffer(profile_id: str, text: str) -> str:
    resp = httpx.post(
        f"{BUFFER_API}/updates/create.json",
        data={
            "access_token":   BUFFER_ACCESS_TOKEN,
            "profile_ids[]":  profile_id,
            "text":           text,
            "now":            "true",
        },
        timeout=20,
    )
    resp.raise_for_status()
    updates = resp.json().get("updates", [])
    return updates[0].get("id", "") if updates else ""


def run_scheduler(user_id: str, agent_id: str, config: dict):
    logger.info(f"[Scheduler] Starting run for user {user_id}")

    if not BUFFER_ACCESS_TOKEN or BUFFER_ACCESS_TOKEN == "your-buffer-token":
        logger.warning("[Scheduler] Buffer not connected — skipping")
        return

    run_id   = start_run(agent_id)
    due      = get_due_posts(user_id)
    profiles = get_buffer_profiles()
    posted   = 0
    failed   = 0

    try:
        for scheduled in due:
            platform   = scheduled["platform"]
            profile_id = profiles.get(platform)

            if not profile_id:
                supabase.table("scheduled_posts").update({
                    "status": "failed",
                    "error_message": f"No Buffer profile for {platform}",
                }).eq("id", scheduled["id"]).execute()
                failed += 1
                continue

            post    = scheduled.get("generated_posts") or {}
            content = post.get("edited_content") or post.get("content") or ""
            tags    = post.get("hashtags") or []
            text    = f"{content}\n\n{' '.join(f'#{t.lstrip(chr(35))}' for t in tags)}" if tags else content

            try:
                buffer_id = push_to_buffer(profile_id, text)
                supabase.table("scheduled_posts").update({
                    "status":         "posted",
                    "posted_at":      datetime.now(timezone.utc).isoformat(),
                    "buffer_post_id": buffer_id,
                }).eq("id", scheduled["id"]).execute()
                supabase.table("generated_posts").update({
                    "status": "posted"
                }).eq("id", scheduled["post_id"]).execute()
                posted += 1
            except Exception as e:
                supabase.table("scheduled_posts").update({
                    "status":        "failed",
                    "error_message": str(e),
                }).eq("id", scheduled["id"]).execute()
                failed += 1

        if posted > 0:
            supabase.table("notifications").insert({
                "user_id":    user_id,
                "type":       "post_published",
                "title":      f"{posted} posts published successfully",
                "body":       "Your Scheduler agent just posted to your social accounts.",
                "read":       False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).execute()

        finish_run(run_id, agent_id, len(due), posted,
                   f"Posted {posted}, failed {failed} of {len(due)} due posts")
        logger.success(f"[Scheduler] Done — {posted} posts published")

    except Exception as e:
        logger.error(f"[Scheduler] Error: {e}")
        error_run(run_id, agent_id, str(e))


def run_all_scheduler_agents():
    for agent in get_all_scheduler_agents():
        run_scheduler(agent["user_id"], agent["id"], agent.get("config", {}))
