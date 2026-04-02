"""
Writer Agent
Takes top-scored trends and generates platform-specific posts
using Claude API and the user's brand voice. Writes to generated_posts.
"""

from datetime import datetime, timezone
from config import supabase, anthropic_client
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential
import json

MIN_VIRAL_SCORE = 55


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
    result = (
        supabase.table("brand_profiles")
        .select("*")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def get_ready_trends(user_id: str) -> list[dict]:
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
    return [t for t in (result.data or []) if t["id"] not in written_ids]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
def generate_posts(trend: dict, profile: dict) -> dict:
    persona_map = {
        "expert":  "a trusted expert in business funding and credit",
        "coach":   "a relatable coach who helps entrepreneurs get funded",
        "insider": "an industry insider who reveals funding secrets most people miss",
    }
    persona = persona_map.get(profile.get("persona", "expert"), persona_map["expert"])
    topics  = ", ".join(profile.get("topics", []))
    avoid   = ", ".join(profile.get("avoid_list", []))

    system = f"""You are {persona}.
Brand: {profile.get('brand_name', 'AgentsRus')}
Tone: {profile.get('tone', 'educational and authoritative')}
Topics: {topics or 'business funding, credit, SBA loans'}
Avoid: {avoid or 'predatory lenders, guaranteed approval claims'}
Never copy source content. Transform the insight into your own original angle."""

    prompt = f"""Transform this viral content into 4 original platform-specific posts.

Source: "{trend['original_content'][:600]}"
Engagement: {trend['engagement_count']:,}

Return ONLY valid JSON, no markdown:
{{
  "facebook":  {{"content": "2-4 paragraphs, hook + value + CTA", "hook": "first sentence", "hashtags": []}},
  "instagram": {{"content": "punchy lines, relatable", "hook": "first sentence", "hashtags": ["tag1","tag2","tag3","tag4","tag5","tag6","tag7","tag8"]}},
  "linkedin":  {{"content": "professional, 150-200 words", "hook": "first sentence", "hashtags": ["tag1","tag2","tag3"]}},
  "twitter":   {{"content": "under 270 chars, punchy", "hook": "same as content", "hashtags": ["tag1","tag2"]}}
}}"""

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        system=system,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


def run_writer(user_id: str, agent_id: str, config: dict):
    logger.info(f"[Writer] Starting run for user {user_id}")
    run_id  = start_run(agent_id)
    profile = get_brand_profile(user_id)

    if not profile:
        finish_run(run_id, agent_id, 0, 0, "No brand profile found")
        return

    trends      = get_ready_trends(user_id)
    total_saved = 0

    try:
        for trend in trends:
            try:
                posts = generate_posts(trend, profile)
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
                    total_saved += 1
            except Exception as e:
                logger.warning(f"[Writer] Failed on trend {trend['id']}: {e}")
                continue

        if total_saved > 0:
            supabase.table("notifications").insert({
                "user_id":    user_id,
                "type":       "posts_ready",
                "title":      f"{total_saved} new posts ready for review",
                "body":       "Head to Content Feed to approve your posts.",
                "read":       False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).execute()

        finish_run(run_id, agent_id, len(trends), total_saved,
                   f"Generated {total_saved} posts from {len(trends)} trends")
        logger.success(f"[Writer] Done — {total_saved} posts saved")

    except Exception as e:
        logger.error(f"[Writer] Error: {e}")
        error_run(run_id, agent_id, str(e))


def run_all_writer_agents():
    for agent in get_all_writer_agents():
        run_writer(agent["user_id"], agent["id"], agent.get("config", {}))
