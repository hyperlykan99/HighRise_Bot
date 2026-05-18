# ChillTopia DJ Dashboard — VPS Deployment

Standalone web dashboard for the DJ_DUDU / ChillTopia radio system.
Supports two data sources — pick the one that fits your current setup.

## Requirements

- Node.js 20+
- One of:
  - **Replit bot running** → use `REMOTE_STATUS_URL` (recommended, no DB copy needed)
  - **Bot on same VPS** → use `DB_PATH` pointing at the shared SQLite file

---

## Run dashboard on VPS

### 1. Build the frontend (run once in the Replit monorepo)

```bash
bash dashboard/build.sh
```

This builds the React app with base path `/` and copies output into
`dashboard/public/`. Run again whenever the dashboard UI changes.

### 2. Copy the dashboard folder to your VPS

```bash
rsync -av --exclude='.env' --exclude='node_modules' \
  dashboard/ user@your-vps:/srv/chilltopia-dashboard/
```

### 3. Install dependencies on the VPS

```bash
cd /srv/chilltopia-dashboard
npm install --omit=dev
```

### 4. Configure environment variables

```bash
cp .env.example .env
nano .env
```

---

## Data source: Mode A — Remote (Replit live data, recommended now)

Use this when the bot is running on Replit and you don't want to run a second
bot instance on the VPS. The dashboard proxies the Replit API — no database
copy, no duplicate DJ_DUDU login.

**How to find your Replit URL:**
1. Open your Replit project
2. Click "Open in new tab" in the preview pane header
3. Your domain looks like `https://abc123.username.replit.dev`
4. Append `/api/dj/status` to confirm it returns JSON

Set in `.env`:
```env
REMOTE_STATUS_URL=https://YOUR-REPL-SLUG.replit.dev/api/dj/status
```

Leave `DB_PATH` commented out — it is not used in remote mode.

**What data flows and what doesn't:**

| Flows to VPS dashboard | Never leaves Replit |
|---|---|
| now_playing, queue, recent, stats | BOT_TOKEN (Highrise) |
| radio_url (stream URL only) | AZURA_API_KEY |
| queue_open, updated_at | Room passwords |
| radio_type, radio_mount | Any user PII |

### 5. Start the server

```bash
npm start
```

Expected startup output:
```
[DASHBOARD] stage=dashboard_startup mode=remote port=3000
[DASHBOARD] data_source=remote url=https://YOUR-REPL.replit.dev/api/dj/status
[DASHBOARD] Open: http://localhost:3000
```

---

## Data source: Mode B — Local SQLite (future VPS migration)

Use this when the bot moves to the VPS and shares the same machine.
Set `DB_PATH` to the absolute path of `highrise_hangout.db` and leave
`REMOTE_STATUS_URL` unset.

```env
DB_PATH=/home/chilltopia/highrise-bot/highrise_hangout.db
```

SQLite WAL mode allows safe concurrent reads while the bot is writing.

**Keeping a remote DB in sync** (if bot stays on Replit temporarily):
```cron
*/2 * * * * rsync -az --checksum \
  user@replit-server:/path/to/highrise_hangout.db \
  /srv/chilltopia-dashboard/highrise_hangout.db
```

---

## Run as a systemd service (recommended)

```ini
# /etc/systemd/system/chilltopia-dashboard.service
[Unit]
Description=ChillTopia DJ Dashboard
After=network.target

[Service]
Type=simple
User=chilltopia
WorkingDirectory=/srv/chilltopia-dashboard
EnvironmentFile=/srv/chilltopia-dashboard/.env
ExecStart=/usr/bin/node server.mjs
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now chilltopia-dashboard
```

---

## Refresh rate

The dashboard polls `/api/dj/status` every **15 seconds** (built into the
React frontend). In remote mode the VPS server forwards that request to Replit.
No configuration needed.

---

## Reverse proxy with nginx + SSL (recommended for public access)

```nginx
server {
    server_name dj.your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

Add SSL:
```bash
certbot --nginx -d dj.your-domain.com
```

---

## AZURACAST_STREAM_URL (optional override)

If you want to override the stream URL shown to listeners (e.g. to point at a
different CDN edge), set this in `.env`:

```env
AZURACAST_STREAM_URL=https://radio.example.com/listen/chilltopia/radio.mp3
```

This replaces whatever `radio_url` comes from Replit or the local DB.
It is the **audio stream URL only** — never the AzuraCast API key.
