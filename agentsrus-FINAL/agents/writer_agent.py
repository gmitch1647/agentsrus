"""
Writer Agent
Runs after Analyst. Takes top-scored trends and generates 4 platform-specific
posts per trend using Claude API and the user's brand voice profile.
Writes results to generated_posts table.
"""

from datetime import datetime, timezone
from config import supabase, anthropic_client
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential
import json

MIN_VIRAL_SCORE = 55
PLATFORMS       = ["facebook", "instagram", "linkedin", "twitter"]


def get_all_writer_agents() -> list[dict]:
    result = (
        supabase.table("agents")
        .select("*")
        .eq("type", "writer")
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


def get_brand_profile(user_id: str) -> dict | None:
    result = (
        supabase.table("brand_profiles")
        .select("*")
        .eq("user_id", user_id)
        .eq("is_default", True)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]
    # fall back to any profile
    result = (
        supabase.table("brand_profiles")
        .select("*")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def get_ready_trends(user_id: str) -> list[dict]:
    """Get trends that scored high enough and haven't been written yet."""
    written = (
        supabase.table("generated_posts")
        .select("trend_id")
        .eq("user_id", user_id)
        .execute()
    )
    written_ids = {r["trend_id"] for r in (written.data or []) if r["trend_id"]}

    result = (
        supabase.table("scraped_trends")
        .select("*")
        .eq("user_id", user_id)
        .eq("is_duplicate", False)
        .gte("viral_score", MIN_VIRAL_SCORE)
        .order("viral_score", desc=True)
        .limit(20)
        .execute()
    )
    trends = result.data or []
    return [t for t in trends if t["id"] not in written_ids]


def build_system_prompt(profile: dict) -> str:
    persona_map = {
        "expert":  "a trusted expert in business funding and credit — authoritative, clear, and data-backed",
        "coach":   "a relatable coach who helps entrepreneurs get funded — warm, direct, and story-driven",
        "insider": "an industry insider who reveals funding secrets most people miss — uses 'most people don't know this' hooks",
    }
    persona = persona_map.get(profile.get("persona", "expert"), persona_map["expert"])
    topics  = ", ".join(profile.get("topics", []))
    avoid   = ", ".join(profile.get("avoid_list", []))
    notes   = profile.get("custom_notes", "")

    return f"""You are {persona}.
Brand: {profile.get('brand_name', 'AgentsRus')}
Niche: {profile.get('niche', 'Business funding & credit')}
Tone: {profile.get('tone', 'educational and authoritative')}
Topics you cover: {topics or 'business funding, credit, SBA loans'}
Always avoid: {avoid or 'predatory lenders, guaranteed approval claims'}
CTA style: {profile.get('cta_style', 'question')}
Additional notes: {notes}

Your job is to rewrite viral content into original posts in your brand voice.
NEVER copy the source content. Transform the core insight into your own angle.
Always write in your persona. Stay on-brand."""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
def generate_posts(trend: dict, profile: dict) -> dict:
    system = build_system_prompt(profile)

    user_prompt = f"""Transform this viral {trend['source_platform']} content into 4 original social media posts.

Source content: "{trend['original_content'][:600]}"
Original engagement: {trend['engagement_count']:,}
Topics: {', '.join(trend.get('topic_tags', []))}

Write one post for each platform. Return ONLY valid JSON with no markdown:
{{
  "facebook": {{
    "content": "2-4 paragraphs, storytelling hook, end with a question or CTA",
    "hook": "first sentence only",
    "hashtags": []
  }},
  "instagram": {{
    "content": "punchy opening, 6-8 short lines, relatable",
    "hook": "first sentence only",
    "hashtags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8"]
  }},
  "linkedin": {{
    "content": "professional, insight-driven, 150-200 words",
    "hook": "first sentence only",
    "hashtags": ["tag1", "tag2", "tag3"]
  }},
  "twitter": {{
    "content": "under 270 characters, punchy, retweetable",
    "hook": "same as content",
    "hashtags": ["tag1", "tag2"]
  }}
}}"""

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        system=system,
        messages=[{"role": "user", "content": user_prompt}]
    )
    raw = response.content[0].text.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


def save_posts(user_id: str, trend: dict, profile: dict, posts: dict) -> int:
    saved = 0
    for platform, data in posts.items():
        if not data.get("content"):
            continue
        supabase.table("generated_posts").insert({
            "user_id":          user_id,
            "trend_id":         trend["id"],
            "brand_profile_id": profile["id"],
            "platform":         platform,
            "content":          data["content"],
            "hook":             data.get("hook", ""),
            "hashtags":         data.get("hashtags", []),
            "status":           "pending",
            "generated_at":     datetime.now(timezone.utc).isoformat(),
        }).execute()
        saved += 1
    return saved


def notify_user(user_id: str, count: int):
    supabase.table("notifications").insert({
        "user_id":    user_id,
        "type":       "posts_ready",
        "title":      f"{count} new posts ready for review",
        "body":       "Your Writer agent just generated new content. Head to the Content Feed to approve.",
        "read":       False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }).execute()


def run_writer(user_id: str, agent_id: str, config: dict):
    logger.info(f"[Writer] Starting run for user {user_id}")
    run_id  = start_run(agent_id)
    profile = get_brand_profile(user_id)

    if not profile:
        msg = "No brand profile found — skipping"
        logger.warning(f"[Writer] {msg}")
        finish_run(run_id, agent_id, 0, 0, msg)
        return

    trends = get_ready_trends(user_id)
    if not trends:
        logger.info("[Writer] No ready trends found")
        finish_run(run_id, agent_id, 0, 0, "No high-scoring unwritten trends")
        return

    total_saved = 0
    try:
        for trend in trends:
            try:
                posts = generate_posts(trend, profile)
                saved = save_posts(user_id, trend, profile, posts)
                total_saved += saved
                logger.info(f"[Writer] Generated {saved} posts from trend: {trend['original_content'][:50]}")
            except Exception as e:
                logger.warning(f"[Writer] Failed on trend {trend['id']}: {e}")
                continue

        if total_saved > 0:
            notify_user(user_id, total_saved)

        finish_run(run_id, agent_id, len(trends), total_saved,
                   f"Processed {len(trends)} trends, generated {total_saved} posts")
        logger.success(f"[Writer] Done — {total_saved} posts saved for user {user_id}")

    except Exception as e:
        logger.error(f"[Writer] Error: {e}")
        error_run(run_id, agent_id, str(e))


def run_all_writer_agents():
    agents = get_all_writer_agents()
    logger.info(f"[Writer] Running {len(agents)} writer agents")
    for agent in agents:
        run_writer(agent["user_id"], agent["id"], agent.get("config", {}))
