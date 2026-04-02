"""
Slack Agent
Two-way Slack integration via Socket Mode (no public URL needed).

Outgoing: Polls notifications table every 5 min and pushes unread
          notifications to Slack.

Incoming: Handles /agentsrus slash command and interactive post-approval
          buttons from Slack.
"""

import threading
from datetime import datetime, timezone
from loguru import logger

from config import supabase, SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_CHANNEL_ID

# ── Slack app (None if tokens not configured) ─────────────────────────────────
try:
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler
    _app = App(token=SLACK_BOT_TOKEN) if SLACK_BOT_TOKEN else None
except ImportError:
    _app = None
    logger.warning("[Slack] slack-bolt not installed — Slack features disabled")


# ── Standard run helpers (matches all other agents) ───────────────────────────

def get_all_slack_agents() -> list[dict]:
    result = (
        supabase.table("agents")
        .select("*")
        .eq("type", "slack")
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


# ── Outgoing: send a Slack message ────────────────────────────────────────────

def send_message(channel: str, text: str, blocks: list | None = None) -> bool:
    if not _app:
        return False
    try:
        kwargs = {"channel": channel, "text": text}
        if blocks:
            kwargs["blocks"] = blocks
        _app.client.chat_postMessage(**kwargs)
        return True
    except Exception as e:
        logger.error(f"[Slack] Failed to send message: {e}")
        return False


def _notification_blocks(title: str, body: str) -> list:
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{title}*\n{body}"},
        }
    ]


def _post_approval_blocks(title: str, body: str, post_id: str) -> list:
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{title}*\n{body}"},
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type":      "button",
                    "text":      {"type": "plain_text", "text": "Approve"},
                    "style":     "primary",
                    "action_id": "approve_post",
                    "value":     post_id,
                }
            ],
        },
    ]


# ── Outgoing: notification push run ───────────────────────────────────────────

def run_slack_notify(user_id: str, agent_id: str, config: dict):
    if not _app:
        logger.warning("[Slack] Skipping — app not initialised")
        return

    channel = config.get("slack_channel_id") or SLACK_CHANNEL_ID
    if not channel:
        logger.warning("[Slack] No channel configured — skipping")
        return

    run_id = start_run(agent_id)
    sent   = 0

    try:
        result = (
            supabase.table("notifications")
            .select("*")
            .eq("user_id", user_id)
            .neq("slack_sent", True)
            .order("created_at", desc=False)
            .limit(20)
            .execute()
        )
        notifications = result.data or []

        for notif in notifications:
            title = notif.get("title", "")
            body  = notif.get("body", "")

            # Posts-ready notifications get an Approve button if post_id present
            if notif.get("type") == "posts_ready" and notif.get("post_id"):
                blocks = _post_approval_blocks(title, body, notif["post_id"])
            else:
                blocks = _notification_blocks(title, body)

            ok = send_message(channel, text=f"{title} — {body}", blocks=blocks)
            if ok:
                supabase.table("notifications").update({
                    "slack_sent": True,
                }).eq("id", notif["id"]).execute()
                sent += 1

        finish_run(run_id, agent_id, len(notifications), sent,
                   f"Sent {sent} of {len(notifications)} notifications to Slack")
        logger.success(f"[Slack] {sent} notifications pushed")

    except Exception as e:
        logger.error(f"[Slack] Error: {e}")
        error_run(run_id, agent_id, str(e))


def run_all_slack_agents():
    for agent in get_all_slack_agents():
        run_slack_notify(agent["user_id"], agent["id"], agent.get("config", {}))


# ── Incoming: /agentsrus slash command ───────────────────────────────────────

if _app:
    @_app.command("/agentsrus")
    def handle_command(ack, body, say):
        ack()
        text = (body.get("text") or "").strip().lower()
        parts = text.split()
        sub   = parts[0] if parts else "help"

        if sub == "status":
            _cmd_status(say)
        elif sub == "run" and len(parts) >= 2:
            _cmd_run(say, parts[1])
        else:
            _cmd_help(say)

    def _cmd_status(say):
        try:
            result = supabase.table("agents").select("type,status,last_run_at").execute()
            rows   = result.data or []
            if not rows:
                say("No agents found.")
                return
            lines = ["*Agent Status*"]
            for r in rows:
                last = r.get("last_run_at") or "never"
                if last != "never":
                    last = last[:16].replace("T", " ")
                lines.append(f"• `{r['type']}` — {r['status']} (last run: {last})")
            say("\n".join(lines))
        except Exception as e:
            say(f"Error fetching status: {e}")

    def _cmd_run(say, agent_name: str):
        agent_map = {
            "scout":     ("agents.scout_agent",     "run_all_scout_agents"),
            "analyst":   ("agents.analyst_agent",   "run_all_analyst_agents"),
            "writer":    ("agents.writer_agent",    "run_all_writer_agents"),
            "tiktok":    ("agents.tiktok_agent",    "run_all_tiktok_agents"),
            "scheduler": ("agents.scheduler_agent", "run_all_scheduler_agents"),
        }
        if agent_name not in agent_map:
            say(f"Unknown agent `{agent_name}`. Try: {', '.join(agent_map)}")
            return

        module_name, fn_name = agent_map[agent_name]
        say(f"Triggering *{agent_name}* agent... :rocket:")

        def _run():
            import importlib
            mod = importlib.import_module(module_name)
            getattr(mod, fn_name)()

        threading.Thread(target=_run, daemon=True).start()

    def _cmd_help(say):
        say(
            "*AgentsRus Slack Commands*\n"
            "• `/agentsrus status` — show all agent statuses\n"
            "• `/agentsrus run <agent>` — manually trigger an agent\n"
            "  Agents: `scout`, `analyst`, `writer`, `tiktok`, `scheduler`\n"
            "• `/agentsrus help` — show this message"
        )

    # ── Incoming: Approve Post button ─────────────────────────────────────────

    @_app.action("approve_post")
    def handle_approve(ack, body, say):
        ack()
        post_id = body["actions"][0].get("value", "")
        if not post_id:
            say("Could not find post ID.")
            return
        try:
            supabase.table("generated_posts").update({
                "status":     "approved",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", post_id).execute()
            say(f":white_check_mark: Post `{post_id[:8]}...` approved and queued for scheduling.")
        except Exception as e:
            say(f"Error approving post: {e}")


# ── Socket Mode starter (called from main.py) ─────────────────────────────────

def start_socket_mode():
    if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
        logger.warning("[Slack] SLACK_BOT_TOKEN / SLACK_APP_TOKEN not set — Socket Mode disabled")
        return
    if not _app:
        logger.warning("[Slack] App not initialised — Socket Mode disabled")
        return
    try:
        handler = SocketModeHandler(_app, SLACK_APP_TOKEN)
        thread  = threading.Thread(target=handler.start, daemon=True)
        thread.start()
        logger.info("[Slack] Socket Mode listener started")
    except Exception as e:
        logger.error(f"[Slack] Failed to start Socket Mode: {e}")
