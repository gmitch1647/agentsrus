"""
Scheduler Agent
Runs every 30 minutes. Checks scheduled_posts for queued posts
whose scheduled_for time has arrived, pushes them to Buffer API,
and updates status to 'posted' or 'failed'.
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
    """Get approved posts that are due to be posted right now."""
    now = datetime.now(timezone.utc).isoformat()
    result = (
        supabase.table("scheduled_posts")
        .select("*, generated_posts(content, edited_content, hashtags, platform)")
        .eq("user_id", user_id)
        .eq("status", "queued")
        .lte("scheduled_for", now)
        .order("scheduled_for", desc=False)
        .limit(10)
        .execute()
    )
    return result.data or []


def get_buffer_profiles() -> dict:
    """
    Returns a map of platform -> Buffer profile_id.
    Buffer profile IDs are fetched once from the Buffer API.
    """
    try:
        resp = httpx.get(
            f"{BUFFER_API}/profiles.json",
            params={"access_token": BUFFER_ACCESS_TOKEN},
            timeout=15,
        )
        resp.raise_for_status()
        profiles = resp.json()
        mapping  = {}
        for p in profiles:
            service = p.get("service", "").lower()
            if service == "twitter":
                service = "twitter"
            elif service == "facebook":
                service = "facebook"
            elif service == "instagram":
                service = "instagram"
            elif service == "linkedin":
                service = "linkedin"
            elif service == "tiktok":
                service = "tiktok"
            mapping[service] = p["id"]
        return mapping
    except Exception as e:
        logger.error(f"[Scheduler] Failed to fetch Buffer profiles: {e}")
        return {}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=20))
def push_to_buffer(profile_id: str, text: str) -> str:
    """Push a post to Buffer and return the Buffer update ID."""
    resp = httpx.post(
        f"{BUFFER_API}/updates/create.json",
        data={
            "access_token": BUFFER_ACCESS_TOKEN,
            "profile_ids[]": profile_id,
            "text": text,
            "now": "true",
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    updates = data.get("updates", [])
    if updates:
        return updates[0].get("id", "")
    return ""


def mark_posted(scheduled_post_id: str, buffer_id: str, post_id: str):
    now = datetime.now(timezone.utc).isoformat()
    supabase.table("scheduled_posts").update({
        "status":         "posted",
        "posted_at":      now,
        "buffer_post_id": buffer_id,
    }).eq("id", scheduled_post_id).execute()

    supabase.table("generated_posts").update({
        "status": "posted",
    }).eq("id", post_id).execute()


def mark_failed(scheduled_post_id: str, error: str):
    supabase.table("scheduled_posts").update({
        "status":        "failed",
        "error_message": error,
    }).eq("id", scheduled_post_id).execute()


def get_post_text(scheduled: dict) -> str:
    """Get the final text to post — edited content takes priority."""
    post = scheduled.get("generated_posts") or {}
    content  = post.get("edited_content") or post.get("content") or ""
    hashtags = post.get("hashtags") or []
    if hashtags:
        tag_str = " ".join(f"#{t.lstrip('#')}" for t in hashtags)
        return f"{content}\n\n{tag_str}"
    return content


def notify_posted(user_id: str, platform: str, count: int):
    supabase.table("notifications").insert({
        "user_id":    user_id,
        "type":       "post_published",
        "title":      f"{count} post{'s' if count > 1 else ''} published to {platform.title()}",
        "body":       "Your Scheduler agent just posted to your social accounts.",
        "read":       False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }).execute()


def run_scheduler(user_id: str, agent_id: str, config: dict):
    logger.info(f"[Scheduler] Starting run for user {user_id}")

    if not BUFFER_ACCESS_TOKEN or BUFFER_ACCESS_TOKEN == "your_buffer_access_token":
        logger.warning("[Scheduler] Buffer not connected — skipping")
        return

    run_id   = start_run(agent_id)
    due      = get_due_posts(user_id)

    if not due:
        logger.info("[Scheduler] No posts due")
        finish_run(run_id, agent_id, 0, 0, "No posts due for publishing")
        return

    profiles = get_buffer_profiles()
    posted   = 0
    failed   = 0

    try:
        platform_counts: dict[str, int] = {}

        for scheduled in due:
            platform = scheduled["platform"]
            profile_id = profiles.get(platform)

            if not profile_id:
                logger.warning(f"[Scheduler] No Buffer profile for {platform}")
                mark_failed(scheduled["id"], f"No Buffer profile connected for {platform}")
                failed += 1
                continue

            text = get_post_text(scheduled)
            if not text:
                mark_failed(scheduled["id"], "Empty post content")
                failed += 1
                continue

            try:
                buffer_id = push_to_buffer(profile_id, text)
                mark_posted(scheduled["id"], buffer_id, scheduled["post_id"])
                posted += 1
                platform_counts[platform] = platform_counts.get(platform, 0) + 1
                logger.info(f"[Scheduler] Posted to {platform}: {text[:50]}")
            except Exception as e:
                logger.error(f"[Scheduler] Failed to post {scheduled['id']}: {e}")
                mark_failed(scheduled["id"], str(e))
                failed += 1

        for platform, count in platform_counts.items():
            notify_posted(user_id, platform, count)

        finish_run(run_id, agent_id, len(due), posted,
                   f"Posted {posted}, failed {failed} out of {len(due)} due posts")
        logger.success(f"[Scheduler] Done — {posted} posts published")

    except Exception as e:
        logger.error(f"[Scheduler] Error: {e}")
        error_run(run_id, agent_id, str(e))


def run_all_scheduler_agents():
    agents = get_all_scheduler_agents()
    logger.info(f"[Scheduler] Running {len(agents)} scheduler agents")
    for agent in agents:
        run_scheduler(agent["user_id"], agent["id"], agent.get("config", {}))
