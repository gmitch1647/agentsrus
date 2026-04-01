# AgentsRus — Railway Backend

5 AI agents that run 24/7 to automate your social media content pipeline.

## Agents
- **Scout** — scrapes TikTok, Instagram, Facebook, LinkedIn via Apify every 2 hours
- **Analyst** — scores every trend for viral potential using Claude AI
- **Writer** — generates platform-specific posts in your brand voice using Claude AI
- **TikTok** — writes full video scripts with hook, body, CTA for every TikTok post
- **Scheduler** — publishes approved posts to your social accounts via Buffer every 30 min

---

## Deploy to Railway (step by step)

### Step 1 — Push this code to GitHub
1. Create a new GitHub repo called `agentsrus-backend`
2. Upload all these files to it
3. Make sure the folder structure looks like this:
```
agentsrus-backend/
  agents/
    scout_agent.py
    analyst_agent.py
    writer_agent.py
    tiktok_agent.py
    scheduler_agent.py
  main.py
  config.py
  requirements.txt
  Procfile
  railway.toml
  README.md
```

### Step 2 — Create Railway project
1. Go to railway.app
2. Click "New Project"
3. Click "Deploy from GitHub repo"
4. Select your `agentsrus-backend` repo
5. Railway will detect Python and start building

### Step 3 — Add environment variables
In Railway → your project → Variables tab, add these exactly:

```
SUPABASE_URL            = https://your-project-id.supabase.co
SUPABASE_SERVICE_KEY    = your-supabase-service-role-key
APIFY_API_TOKEN         = your-apify-api-token
ANTHROPIC_API_KEY       = your-anthropic-api-key
BUFFER_ACCESS_TOKEN     = your-buffer-access-token
SKIP_STARTUP_RUN        = false
```

### Step 4 — Deploy
1. Click "Deploy" in Railway
2. Watch the logs — you should see:
   ```
   AgentsRus Backend Starting
   Running initial pipeline...
   Scout Agent complete
   Analyst Agent complete
   Writer Agent complete
   ```
3. After ~5 minutes check your Supabase table editor
   — you should see rows appearing in scraped_trends and generated_posts

---

## Monitoring
- Railway → your project → Logs tab shows all agent activity in real time
- Your Lovable dashboard shows agent status updated live via Supabase real-time
- Errors are written to the error_log column in the agents table

## Environment variables reference

| Variable | Where to find it |
|---|---|
| SUPABASE_URL | Supabase → Settings → API → Project URL |
| SUPABASE_SERVICE_KEY | Supabase → Settings → API → service_role key |
| APIFY_API_TOKEN | apify.com → Settings → Integrations |
| ANTHROPIC_API_KEY | console.anthropic.com → API Keys |
| BUFFER_ACCESS_TOKEN | buffer.com → Account → Apps |
| SKIP_STARTUP_RUN | Set to `true` to skip the initial run on boot |

## Costs (estimated monthly)
- Railway: ~$5-10/month
- Apify: ~$49/month (starter)
- Anthropic API: ~$10-20/month depending on volume
- Buffer: ~$15/month
