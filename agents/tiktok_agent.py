"""
TikTok Agent
Generates full video scripts with hook, body, CTA for every
pending TikTok post. Writes to tiktok_scripts table.
"""

from datetime import datetime, timezone
from config import supabase, anthropic_client
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential
import json


def get_all_tiktok_agents() -> list[dict]:
    result = (
        supabase.table("agents")
        .select("*")
        .eq("type", "tiktok")
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


def get_unscripted_posts(user_id: str) -> list[dict]:
    scripted = (
        supabase.table("tiktok_scripts")
        .select("post_id")
        .execute()
    )
    scripted_ids = {r["post_id"] for r in (scripted.data or [])}
    result = (
        supabase.table("generated_posts")
        .select("*")
        .eq("user_id", user_id)
        .eq("platform", "tiktok")
        .eq("status", "pending")
        .order("generated_at", desc=True)
        .limit(20)
        .execute()
    )
    return [p for p in (result.data or []) if p["id"] not in scripted_ids]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
def generate_script(post: dict, config: dict) -> dict:
    target = config.get("target_duration_secs", 60)
    prompt = f"""You are an expert TikTok scriptwriter for business funding content.

Convert this post into a complete TikTok script for a {target}-second video.

POST: {post['content']}

The hook MUST stop the scroll in 2-3 seconds. Use open loops and curiosity gaps.
End with a strong CTA driving comments or follows.

Return ONLY valid JSON, no markdown:
{{
  "hook_line": "exact first words — under 10 words",
  "hook_duration_secs": 3,
  "body_script": "full spoken script with line breaks between sections",
  "cta_line": "exact closing call to action",
  "estimated_duration": {target},
  "on_screen_text": ["overlay 1", "overlay 2", "overlay 3"],
  "suggested_sounds": "audio style description"
}}"""

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


def run_tiktok(user_id: str, agent_id: str, config: dict):
    logger.info(f"[TikTok] Starting run for user {user_id}")
    run_id   = start_run(agent_id)
    posts    = get_unscripted_posts(user_id)
    scripted = 0

    try:
        for post in posts:
            try:
                script = generate_script(post, config)
                supabase.table("tiktok_scripts").insert({
                    "post_id":            post["id"],
                    "hook_line":          script.get("hook_line", ""),
                    "hook_duration_secs": script.get("hook_duration_secs", 3),
                    "body_script":        script.get("body_script", ""),
                    "cta_line":           script.get("cta_line", ""),
                    "estimated_duration": script.get("estimated_duration", 60),
                    "on_screen_text":     script.get("on_screen_text", []),
                    "suggested_sounds":   script.get("suggested_sounds", ""),
                    "created_at":         datetime.now(timezone.utc).isoformat(),
                }).execute()
                scripted += 1
            except Exception as e:
                logger.warning(f"[TikTok] Failed on post {post['id']}: {e}")
                continue

        finish_run(run_id, agent_id, len(posts), scripted,
                   f"Generated {scripted} TikTok scripts")
        logger.success(f"[TikTok] Done — {scripted} scripts saved")

    except Exception as e:
        logger.error(f"[TikTok] Error: {e}")
        error_run(run_id, agent_id, str(e))


def run_all_tiktok_agents():
    for agent in get_all_tiktok_agents():
        run_tiktok(agent["user_id"], agent["id"], agent.get("config", {}))
