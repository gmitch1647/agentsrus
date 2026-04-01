"""
TikTok Agent
Runs after Writer. For every pending TikTok post in generated_posts,
generates a full video script with hook line, body script, CTA,
on-screen text suggestions, and estimated duration.
Writes to tiktok_scripts table.
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


def get_unscripted_tiktok_posts(user_id: str) -> list[dict]:
    """Get TikTok posts that don't have a script yet."""
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
    posts = result.data or []
    return [p for p in posts if p["id"] not in scripted_ids]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
def generate_tiktok_script(post: dict, config: dict) -> dict:
    hook_styles = config.get("hook_styles", ["question", "stat", "story"])
    target_secs = config.get("target_duration_secs", 60)

    prompt = f"""You are an expert TikTok scriptwriter specializing in business funding and credit content.

Convert this post into a complete TikTok video script optimized for maximum watch time and engagement.

POST CONTENT:
{post['content']}

HOOK LINE (from post): {post.get('hook', '')}

Create a script for a {target_secs}-second TikTok video.
Hook styles to consider: {', '.join(hook_styles)}

The hook MUST stop the scroll in the first 2-3 seconds.
Use pattern interrupts, open loops, and curiosity gaps throughout.
End with a strong CTA that drives comments or follows.

Return ONLY valid JSON with no markdown:
{{
  "hook_line": "The exact first words spoken — must be under 10 words, stops the scroll",
  "hook_duration_secs": 3,
  "body_script": "Full spoken script for the body of the video. Use line breaks between sections. Write exactly what to say.",
  "cta_line": "The exact closing call to action",
  "estimated_duration": {target_secs},
  "on_screen_text": [
    "Text overlay 1 — shown in first 3 secs",
    "Text overlay 2 — shown mid video",
    "Text overlay 3 — shown at CTA"
  ],
  "suggested_sounds": "Description of audio style that fits this content, e.g. upbeat background music, no music just voice, trending audio"
}}"""

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


def save_script(post_id: str, script: dict):
    supabase.table("tiktok_scripts").insert({
        "post_id":             post_id,
        "hook_line":           script.get("hook_line", ""),
        "hook_duration_secs":  script.get("hook_duration_secs", 3),
        "body_script":         script.get("body_script", ""),
        "cta_line":            script.get("cta_line", ""),
        "estimated_duration":  script.get("estimated_duration", 60),
        "on_screen_text":      script.get("on_screen_text", []),
        "suggested_sounds":    script.get("suggested_sounds", ""),
        "created_at":          datetime.now(timezone.utc).isoformat(),
    }).execute()


def run_tiktok(user_id: str, agent_id: str, config: dict):
    logger.info(f"[TikTok] Starting run for user {user_id}")
    run_id = start_run(agent_id)
    posts  = get_unscripted_tiktok_posts(user_id)

    if not posts:
        logger.info("[TikTok] No unscripted TikTok posts found")
        finish_run(run_id, agent_id, 0, 0, "No unscripted TikTok posts")
        return

    scripted = 0
    try:
        for post in posts:
            try:
                script = generate_tiktok_script(post, config)
                save_script(post["id"], script)
                scripted += 1
                logger.info(f"[TikTok] Scripted: {post['content'][:50]}")
            except Exception as e:
                logger.warning(f"[TikTok] Failed on post {post['id']}: {e}")
                continue

        finish_run(run_id, agent_id, len(posts), scripted,
                   f"Generated {scripted} TikTok scripts from {len(posts)} posts")
        logger.success(f"[TikTok] Done — {scripted} scripts saved")

    except Exception as e:
        logger.error(f"[TikTok] Error: {e}")
        error_run(run_id, agent_id, str(e))


def run_all_tiktok_agents():
    agents = get_all_tiktok_agents()
    logger.info(f"[TikTok] Running {len(agents)} TikTok agents")
    for agent in agents:
        run_tiktok(agent["user_id"], agent["id"], agent.get("config", {}))
