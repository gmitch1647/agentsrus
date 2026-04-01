"""
Scout Agent
Runs every 2 hours. Scrapes TikTok, Instagram, Facebook and LinkedIn
for viral funding/credit content via Apify. Writes results to
scraped_trends table and logs the run in agent_runs.
"""

from datetime import datetime, timezone
from config import supabase, apify_client
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential


# ── Apify actor IDs ───────────────────────────────────────────────────────────
ACTORS = {
    "tiktok":    "clockworks/free-tiktok-scraper",
    "instagram": "apify/instagram-hashtag-scraper",
    "facebook":  "apify/facebook-posts-scraper",
    "linkedin":  "curious_coder/linkedin-post-search-scraper",
}

# ── Keywords to search across all platforms ───────────────────────────────────
KEYWORDS = [
    "business funding",
    "business credit",
    "SBA loans",
    "startup grants",
    "business line of credit",
    "credit score business",
]


def get_agent(user_id: str) -> dict | None:
    result = (
        supabase.table("agents")
        .select("*")
        .eq("user_id", user_id)
        .eq("type", "scout")
        .single()
        .execute()
    )
    return result.data


def get_all_scout_agents() -> list[dict]:
    result = (
        supabase.table("agents")
        .select("*")
        .eq("type", "scout")
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
        "status":      "running",
        "updated_at":  datetime.now(timezone.utc).isoformat(),
    }).eq("id", agent_id).execute()
    return result.data[0]["id"]


def finish_run(run_id: str, agent_id: str, found: int, processed: int, notes: str = ""):
    supabase.table("agent_runs").update({
        "completed_at":   datetime.now(timezone.utc).isoformat(),
        "status":         "success",
        "items_found":    found,
        "items_processed": processed,
        "notes":          notes,
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


def is_duplicate(user_id: str, content: str) -> bool:
    """Check if very similar content already exists for this user."""
    existing = (
        supabase.table("scraped_trends")
        .select("original_content")
        .eq("user_id", user_id)
        .limit(500)
        .execute()
    )
    if not existing.data:
        return False
    content_lower = content.lower()[:100]
    for row in existing.data:
        if content_lower in row["original_content"].lower()[:100]:
            return True
    return False


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
def scrape_tiktok(keyword: str) -> list[dict]:
    logger.info(f"[Scout] Scraping TikTok for: {keyword}")
    run = apify_client.actor(ACTORS["tiktok"]).call(run_input={
        "keywords":   [keyword],
        "maxItems":   20,
        "type":       "search",
    })
    items = []
    for item in apify_client.dataset(run["defaultDatasetId"]).iterate_items():
        text = item.get("text") or item.get("desc") or ""
        if not text:
            continue
        items.append({
            "source_platform":  "tiktok",
            "source_url":       item.get("webVideoUrl") or item.get("shareUrl") or "",
            "source_handle":    item.get("authorMeta", {}).get("name") or "",
            "original_content": text,
            "engagement_count": (
                int(item.get("diggCount", 0)) +
                int(item.get("commentCount", 0)) +
                int(item.get("shareCount", 0))
            ),
            "topic_tags":       [keyword],
        })
    return items


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
def scrape_instagram(keyword: str) -> list[dict]:
    logger.info(f"[Scout] Scraping Instagram for: {keyword}")
    hashtag = keyword.replace(" ", "")
    run = apify_client.actor(ACTORS["instagram"]).call(run_input={
        "hashtags":    [hashtag],
        "resultsLimit": 20,
    })
    items = []
    for item in apify_client.dataset(run["defaultDatasetId"]).iterate_items():
        text = item.get("caption") or ""
        if not text:
            continue
        items.append({
            "source_platform":  "instagram",
            "source_url":       item.get("url") or "",
            "source_handle":    item.get("ownerUsername") or "",
            "original_content": text,
            "engagement_count": (
                int(item.get("likesCount", 0)) +
                int(item.get("commentsCount", 0))
            ),
            "topic_tags":       [keyword],
        })
    return items


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
def scrape_facebook(keyword: str) -> list[dict]:
    logger.info(f"[Scout] Scraping Facebook for: {keyword}")
    run = apify_client.actor(ACTORS["facebook"]).call(run_input={
        "query":      keyword,
        "maxItems":   15,
    })
    items = []
    for item in apify_client.dataset(run["defaultDatasetId"]).iterate_items():
        text = item.get("text") or item.get("message") or ""
        if not text:
            continue
        items.append({
            "source_platform":  "facebook",
            "source_url":       item.get("url") or "",
            "source_handle":    item.get("pageName") or "",
            "original_content": text,
            "engagement_count": (
                int(item.get("likes", 0)) +
                int(item.get("comments", 0)) +
                int(item.get("shares", 0))
            ),
            "topic_tags":       [keyword],
        })
    return items


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
def scrape_linkedin(keyword: str) -> list[dict]:
    logger.info(f"[Scout] Scraping LinkedIn for: {keyword}")
    run = apify_client.actor(ACTORS["linkedin"]).call(run_input={
        "keywords": keyword,
        "count":    15,
    })
    items = []
    for item in apify_client.dataset(run["defaultDatasetId"]).iterate_items():
        text = item.get("text") or item.get("commentary") or ""
        if not text:
            continue
        items.append({
            "source_platform":  "linkedin",
            "source_url":       item.get("postUrl") or "",
            "source_handle":    item.get("author", {}).get("name") or "",
            "original_content": text,
            "engagement_count": (
                int(item.get("numLikes", 0)) +
                int(item.get("numComments", 0))
            ),
            "topic_tags":       [keyword],
        })
    return items


def save_trends(user_id: str, run_id: str, trends: list[dict]) -> int:
    saved = 0
    for trend in trends:
        if is_duplicate(user_id, trend["original_content"]):
            continue
        supabase.table("scraped_trends").insert({
            **trend,
            "user_id":      user_id,
            "agent_run_id": run_id,
            "viral_score":  0,
            "is_duplicate": False,
            "scraped_at":   datetime.now(timezone.utc).isoformat(),
        }).execute()
        saved += 1
    return saved


def run_scout(user_id: str, agent_id: str, config: dict):
    logger.info(f"[Scout] Starting run for user {user_id}")
    run_id = start_run(agent_id)
    all_trends = []
    keywords   = config.get("keywords", KEYWORDS)
    platforms  = config.get("platforms", ["tiktok", "instagram", "facebook", "linkedin"])
    min_eng    = config.get("min_engagement", 1000)

    try:
        for keyword in keywords:
            if "tiktok"    in platforms:
                all_trends += scrape_tiktok(keyword)
            if "instagram" in platforms:
                all_trends += scrape_instagram(keyword)
            if "facebook"  in platforms:
                all_trends += scrape_facebook(keyword)
            if "linkedin"  in platforms:
                all_trends += scrape_linkedin(keyword)

        # filter by minimum engagement
        filtered = [t for t in all_trends if t["engagement_count"] >= min_eng]
        saved    = save_trends(user_id, run_id, filtered)

        finish_run(run_id, agent_id, len(all_trends), saved,
                   f"Scraped {len(all_trends)} items, saved {saved} after dedup/filter")
        logger.success(f"[Scout] Done — {saved} trends saved for user {user_id}")

    except Exception as e:
        logger.error(f"[Scout] Error: {e}")
        error_run(run_id, agent_id, str(e))


def run_all_scout_agents():
    agents = get_all_scout_agents()
    logger.info(f"[Scout] Running {len(agents)} scout agents")
    for agent in agents:
        run_scout(agent["user_id"], agent["id"], agent.get("config", {}))
