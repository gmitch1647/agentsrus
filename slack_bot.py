import os
import threading
from datetime import datetime, timezone
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from config import supabase
from loguru import logger

app = App(token=os.environ["SLACK_BOT_TOKEN"])

CHANNEL = os.environ["SLACK_CHANNEL_ID"]


def get_agents():
    result = supabase.table("agents").select("*").execute()
    return result.data or []


def get_pending_posts():
    result = (
        supabase.table("generated_posts")
        .select("*")
        .eq("status", "pending")
        .order("generated_at", desc=True)
        .limit(5)
        .execute()
    )
    return result.data or []


def format_status_block():
    agents = get_agents()
    status_map = {
        "running": "🔵",
        "idle":    "✅",
        "error":   "🔴",
        "paused":  "⏸️",
    }
    lines = ["*AgentsRus — Agent Status*\n"]
    for agent in agents:
        emoji = status_map.get(agent["status"], "⚪")
        last  = agent.get("last_run_at", "never")
        if last and last != "never":
            last = last[:16].replace("T", " ")
        lines.append(f"{emoji} *{agent['type']}* — {agent['status']} · last run: {last}")
    return "\n".join(lines)


@app.command("/status")
def handle_status(ack, respond):
    ack()
    respond(format_status_block())


@app.command("/run")
def handle_run(ack, respond, command):
    ack()
    agent_type = command["text"].strip().lower()
    valid = ["scout", "analyst", "writer", "tiktok", "scheduler"]
    if agent_type not in valid:
        respond(f"Unknown agent. Valid options: {', '.join(valid)}")
        return
    supabase.table("agents").update({
        "status":     "running",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("type", agent_type).execute()
    respond(f"✅ {agent_type.title()} Agent triggered — check logs in Railway")


@app.command("/approve")
def handle_approve(ack, respond, command):
    ack()
    post_id = command["text"].strip()
    if not post_id:
        posts = get_pending_posts()
        if not posts:
            respond("No pending posts right now.")
            return
        lines = ["*Pending posts — use /approve [id] to approve:*\n"]
        for p in posts:
            short_id = p["id"][:8]
            preview  = p["content"][:80].replace("\n", " ")
            lines.append(f"• `{short_id}` [{p['platform']}] {preview}...")
        respond("\n".join(lines))
        return
    matches = (
        supabase.table("generated_posts")
        .select("*")
        .ilike("id", f"{post_id}%")
        .limit(1)
        .execute()
    )
    if not matches.data:
        respond(f"No post found matching ID: {post_id}")
        return
    post = matches.data[0]
    supabase.table("generated_posts").update({
        "status":     "approved",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", post["id"]).execute()
    respond(f"✅ Post approved — [{post['platform']}] {post['content'][:80]}...")


@app.command("/reject")
def handle_reject(ack, respond, command):
    ack()
    post_id = command["text"].strip()
    if not post_id:
        respond("Usage: /reject [post-id] — use /approve to see pending post IDs")
        return
    matches = (
        supabase.table("generated_posts")
        .select("*")
        .ilike("id", f"{post_id}%")
        .limit(1)
        .execute()
    )
    if not matches.data:
        respond(f"No post found matching ID: {post_id}")
        return
    post = matches.data[0]
    supabase.table("generated_posts").update({
        "status": "rejected",
    }).eq("id", post["id"]).execute()
    respond(f"❌ Post rejected — [{post['platform']}] {post['content'][:80]}...")


@app.command("/queue")
def handle_queue(ack, respond):
    ack()
    result = (
        supabase.table("scheduled_posts")
        .select("*, generated_posts(content, platform)")
        .eq("status", "queued")
        .order("scheduled_for", desc=False)
        .limit(10)
        .execute()
    )
    posts = result.data or []
    if not posts:
        respond("No posts in queue right now.")
        return
    lines = ["*Upcoming scheduled posts:*\n"]
    for p in posts:
        time_str = p["scheduled_for"][:16].replace("T", " ")
        post     = p.get("generated_posts") or {}
        preview  = (post.get("content") or "")[:60]
        platform = p["platform"]
        lines.append(f"• {time_str} [{platform}] {preview}...")
    respond("\n".join(lines))


@app.command("/pause")
def handle_pause(ack, respond, command):
    ack()
    agent_type = command["text"].strip().lower()
    valid = ["scout", "analyst", "writer", "tiktok", "scheduler"]
    if agent_type not in valid:
        respond(f"Unknown agent. Valid options: {', '.join(valid)}")
        return
    supabase.table("agents").update({
        "status":     "paused",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("type", agent_type).execute()
    respond(f"⏸️ {agent_type.title()} Agent paused")


@app.command("/resume")
def handle_resume(ack, respond, command):
    ack()
    agent_type = command["text"].strip().lower()
    valid = ["scout", "analyst", "writer", "tiktok", "scheduler"]
    if agent_type not in valid:
        respond(f"Unknown agent. Valid options: {', '.join(valid)}")
        return
    supabase.table("agents").update({
        "status":     "idle",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("type", agent_type).execute()
    respond(f"▶️ {agent_type.title()} Agent resumed")


def send_notification(message: str):
    try:
        app.client.chat_postMessage(channel=CHANNEL, text=message)
    except Exception as e:
        logger.error(f"[Slack] Failed to send notification: {e}")


def start_slack_bot():
    logger.info("[Slack] Starting Slack bot...")
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
