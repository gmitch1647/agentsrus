import os
from dotenv import load_dotenv
from supabase import create_client, Client
from anthropic import Anthropic
from apify_client import ApifyClient
from loguru import logger

load_dotenv()

# ── Supabase ──────────────────────────────────────────────
SUPABASE_URL         = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

# ── Apify ─────────────────────────────────────────────────
APIFY_API_TOKEN      = os.environ["APIFY_API_TOKEN"]

# ── Anthropic ─────────────────────────────────────────────
ANTHROPIC_API_KEY    = os.environ["ANTHROPIC_API_KEY"]

# ── Buffer ────────────────────────────────────────────────
BUFFER_ACCESS_TOKEN  = os.environ["BUFFER_ACCESS_TOKEN"]

# ── Shared clients (imported by all agents) ───────────────
supabase: Client  = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
anthropic_client  = Anthropic(api_key=ANTHROPIC_API_KEY)
apify_client      = ApifyClient(APIFY_API_TOKEN)

logger.add("logs/agentsrus.log", rotation="10 MB", retention="7 days", level="INFO")
