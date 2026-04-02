-- ============================================================
-- AgentsRus — Full Supabase Schema
-- Paste this entire file into: Supabase → SQL Editor → Run
-- ============================================================


-- ── 1. agents ────────────────────────────────────────────────
-- One row per agent per user. Config drives agent behaviour.
CREATE TABLE IF NOT EXISTS agents (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    type         text NOT NULL CHECK (type IN ('scout','analyst','writer','tiktok','scheduler','slack')),
    status       text NOT NULL DEFAULT 'idle' CHECK (status IN ('idle','running','error','paused')),
    config       jsonb NOT NULL DEFAULT '{}',
    last_run_at  timestamptz,
    error_log    text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS agents_user_id_idx  ON agents(user_id);
CREATE INDEX IF NOT EXISTS agents_type_idx     ON agents(type);
CREATE INDEX IF NOT EXISTS agents_status_idx   ON agents(status);


-- ── 2. agent_runs ─────────────────────────────────────────────
-- Audit log of every agent execution.
CREATE TABLE IF NOT EXISTS agent_runs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        uuid NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    started_at      timestamptz NOT NULL DEFAULT now(),
    completed_at    timestamptz,
    status          text NOT NULL DEFAULT 'running' CHECK (status IN ('running','success','error')),
    items_found     integer NOT NULL DEFAULT 0,
    items_processed integer NOT NULL DEFAULT 0,
    notes           text
);

CREATE INDEX IF NOT EXISTS agent_runs_agent_id_idx ON agent_runs(agent_id);
CREATE INDEX IF NOT EXISTS agent_runs_status_idx   ON agent_runs(status);


-- ── 3. scraped_trends ─────────────────────────────────────────
-- Raw content scraped by Scout agent.
CREATE TABLE IF NOT EXISTS scraped_trends (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    agent_run_id     uuid REFERENCES agent_runs(id) ON DELETE SET NULL,
    source_platform  text NOT NULL CHECK (source_platform IN ('tiktok','instagram','facebook','linkedin')),
    source_url       text NOT NULL DEFAULT '',
    source_handle    text NOT NULL DEFAULT '',
    original_content text NOT NULL,
    engagement_count integer NOT NULL DEFAULT 0,
    topic_tags       text[] NOT NULL DEFAULT '{}',
    viral_score      numeric(5,2) NOT NULL DEFAULT 0,
    is_duplicate     boolean NOT NULL DEFAULT false,
    scraped_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS scraped_trends_user_id_idx     ON scraped_trends(user_id);
CREATE INDEX IF NOT EXISTS scraped_trends_viral_score_idx ON scraped_trends(viral_score DESC);
CREATE INDEX IF NOT EXISTS scraped_trends_platform_idx    ON scraped_trends(source_platform);


-- ── 4. brand_profiles ─────────────────────────────────────────
-- Writer agent uses this for brand voice / persona.
CREATE TABLE IF NOT EXISTS brand_profiles (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    brand_name  text NOT NULL DEFAULT 'AgentsRus',
    persona     text NOT NULL DEFAULT 'expert' CHECK (persona IN ('expert','coach','insider')),
    tone        text NOT NULL DEFAULT 'educational and authoritative',
    topics      text[] NOT NULL DEFAULT '{}',
    avoid_list  text[] NOT NULL DEFAULT '{}',
    is_default  boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS brand_profiles_user_id_idx ON brand_profiles(user_id);
-- Enforce only one default profile per user
CREATE UNIQUE INDEX IF NOT EXISTS brand_profiles_one_default_idx
    ON brand_profiles(user_id) WHERE is_default = true;


-- ── 5. generated_posts ────────────────────────────────────────
-- AI-generated platform posts created by Writer agent.
CREATE TABLE IF NOT EXISTS generated_posts (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    trend_id          uuid REFERENCES scraped_trends(id) ON DELETE SET NULL,
    brand_profile_id  uuid REFERENCES brand_profiles(id) ON DELETE SET NULL,
    platform          text NOT NULL CHECK (platform IN ('facebook','instagram','linkedin','twitter','tiktok')),
    content           text NOT NULL,
    edited_content    text,
    hook              text NOT NULL DEFAULT '',
    hashtags          text[] NOT NULL DEFAULT '{}',
    status            text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','scheduled','posted','rejected')),
    image_url         text,
    generated_at      timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS generated_posts_user_id_idx  ON generated_posts(user_id);
CREATE INDEX IF NOT EXISTS generated_posts_status_idx   ON generated_posts(status);
CREATE INDEX IF NOT EXISTS generated_posts_platform_idx ON generated_posts(platform);
CREATE INDEX IF NOT EXISTS generated_posts_trend_id_idx ON generated_posts(trend_id);


-- ── 6. tiktok_scripts ─────────────────────────────────────────
-- Full video scripts generated by TikTok agent.
CREATE TABLE IF NOT EXISTS tiktok_scripts (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id             uuid NOT NULL REFERENCES generated_posts(id) ON DELETE CASCADE,
    hook_line           text NOT NULL DEFAULT '',
    hook_duration_secs  integer NOT NULL DEFAULT 3,
    body_script         text NOT NULL DEFAULT '',
    cta_line            text NOT NULL DEFAULT '',
    estimated_duration  integer NOT NULL DEFAULT 60,
    on_screen_text      text[] NOT NULL DEFAULT '{}',
    suggested_sounds    text NOT NULL DEFAULT '',
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS tiktok_scripts_post_id_idx ON tiktok_scripts(post_id);


-- ── 7. scheduled_posts ────────────────────────────────────────
-- Approved posts queued for publishing via Buffer.
CREATE TABLE IF NOT EXISTS scheduled_posts (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    post_id         uuid NOT NULL REFERENCES generated_posts(id) ON DELETE CASCADE,
    platform        text NOT NULL,
    scheduled_for   timestamptz NOT NULL,
    status          text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','posted','failed')),
    posted_at       timestamptz,
    buffer_post_id  text,
    error_message   text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS scheduled_posts_user_id_idx      ON scheduled_posts(user_id);
CREATE INDEX IF NOT EXISTS scheduled_posts_status_idx       ON scheduled_posts(status);
CREATE INDEX IF NOT EXISTS scheduled_posts_scheduled_for_idx ON scheduled_posts(scheduled_for);


-- ── 8. notifications ──────────────────────────────────────────
-- In-app and Slack notifications for the user.
CREATE TABLE IF NOT EXISTS notifications (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    type        text NOT NULL CHECK (type IN ('posts_ready','post_published','agent_error','general')),
    title       text NOT NULL,
    body        text NOT NULL DEFAULT '',
    post_id     uuid REFERENCES generated_posts(id) ON DELETE SET NULL,
    read        boolean NOT NULL DEFAULT false,
    slack_sent  boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS notifications_user_id_idx   ON notifications(user_id);
CREATE INDEX IF NOT EXISTS notifications_read_idx      ON notifications(read);
CREATE INDEX IF NOT EXISTS notifications_slack_sent_idx ON notifications(slack_sent);


-- ============================================================
-- Disable RLS on all tables
-- Railway backend uses service role key (full access).
-- Lovable frontend uses anon key (read/write via open policies).
-- ============================================================

ALTER TABLE agents           DISABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runs       DISABLE ROW LEVEL SECURITY;
ALTER TABLE scraped_trends   DISABLE ROW LEVEL SECURITY;
ALTER TABLE brand_profiles   DISABLE ROW LEVEL SECURITY;
ALTER TABLE generated_posts  DISABLE ROW LEVEL SECURITY;
ALTER TABLE tiktok_scripts   DISABLE ROW LEVEL SECURITY;
ALTER TABLE scheduled_posts  DISABLE ROW LEVEL SECURITY;
ALTER TABLE notifications    DISABLE ROW LEVEL SECURITY;


-- ============================================================
-- trigger_run column for Lovable UI manual triggers
-- ============================================================

ALTER TABLE agents ADD COLUMN IF NOT EXISTS trigger_run boolean NOT NULL DEFAULT false;
