"""
Analyst Agent
Runs after every Scout run. Reads unscored trends from scraped_trends,
scores each one for viral potential (0-100), marks duplicates,
and tags by topic. Updates viral_score and is_duplicate in place.
"""

from datetime import datetime, timezone
from config import supabase, anthropic_client
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential
import json


MIN_VIRAL_SCORE = 55  # only high-quality trends pass through to Writer


def get_all_analyst_agents() -> list[dict]:
    result = (
        supabase.table("agents")
        .select("*")
        .eq("type", "analyst")
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


def get_unscored_trends(user_id: str) -> list[dict]:
    result = (
        supabase.table("scraped_trends")
        .select("*")
        .eq("user_id", user_id)
        .eq("viral_score", 0)
        .eq("is_duplicate", False)
        .order("scraped_at", desc=True)
        .limit(50)
        .execute()
    )
    return result.data or []


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=20))
def score_trend(content: str, platform: str, engagement: int) -> dict:
    """
    Ask Claude to score this trend for viral potential and extract topic tags.
    Returns { viral_score: float, topic_tags: list[str], reason: str }
    """
    prompt = f"""You are an expert social media analyst specializing in business funding and credit content.

Analyze this {platform} post and score its viral potential for a business funding/credit education account.

POST:
{content[:800]}

ENGAGEMENT: {engagement:,} interactions

Score the viral potential from 0-100 based on:
- Relevance to business funding, credit, SBA loans, grants (0-30 pts)
- Hook strength — does it grab attention in first line (0-25 pts)  
- Educational value — teaches something actionable (0-25 pts)
- Engagement bait — question, controversy, relatable pain point (0-20 pts)

Also extract topic tags from this list only:
business credit, SBA loans, funding strategies, grants, revenue-based financing, 
DUNS/EIN setup, angel investors, cash flow, pitch deck, personal credit

Respond ONLY with valid JSON, no markdown:
{{
  "viral_score": <number 0-100>,
  "topic_tags": ["tag1", "tag2"],
  "reason": "<one sentence explanation>"
}}"""

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip()
    return json.loads(raw)


def mark_duplicate(trend_id: str):
    supabase.table("scraped_trends").update({
        "is_duplicate": True
    }).eq("id", trend_id).execute()


def update_trend_score(trend_id: str, score: float, tags: list[str]):
    supabase.table("scraped_trends").update({
        "viral_score": round(score, 2),
        "topic_tags":  tags,
    }).eq("id", trend_id).execute()


def run_analyst(user_id: str, agent_id: str, config: dict):
    logger.info(f"[Analyst] Starting run for user {user_id}")
    run_id  = start_run(agent_id)
    trends  = get_unscored_trends(user_id)
    min_score = config.get("min_viral_score", MIN_VIRAL_SCORE)

    if not trends:
        logger.info("[Analyst] No unscored trends found")
        finish_run(run_id, agent_id, 0, 0, "No unscored trends to process")
        return

    scored   = 0
    passed   = 0
    rejected = 0

    try:
        for trend in trends:
            try:
                result = score_trend(
                    trend["original_content"],
                    trend["source_platform"],
                    trend["engagement_count"],
                )
                vs = float(result.get("viral_score", 0))
                tags = result.get("topic_tags", trend.get("topic_tags", []))

                update_trend_score(trend["id"], vs, tags)
                scored += 1

                if vs < min_score:
                    rejected += 1
                    logger.debug(f"[Analyst] Score {vs:.0f} — rejected: {trend['original_content'][:60]}")
                else:
                    passed += 1
                    logger.info(f"[Analyst] Score {vs:.0f} — passed: {trend['original_content'][:60]}")

            except Exception as e:
                logger.warning(f"[Analyst] Could not score trend {trend['id']}: {e}")
                continue

        finish_run(run_id, agent_id, len(trends), scored,
                   f"Scored {scored} trends. {passed} passed (>={min_score}), {rejected} rejected")
        logger.success(f"[Analyst] Done — {passed} trends ready for Writer")

    except Exception as e:
        logger.error(f"[Analyst] Error: {e}")
        error_run(run_id, agent_id, str(e))


def run_all_analyst_agents():
    agents = get_all_analyst_agents()
    logger.info(f"[Analyst] Running {len(agents)} analyst agents")
    for agent in agents:
        run_analyst(agent["user_id"], agent["id"], agent.get("config", {}))
