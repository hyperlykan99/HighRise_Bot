# ChillTopia DJ Dashboard — VPS Deployment

Standalone web dashboard for the DJ_DUDU / ChillTopia radio system.
Reads live queue, now-playing, and stats from the shared bot database.

## Requirements

- Node.js 20+
- Access to the bot's `highrise_hangout.db` SQLite file (read-only)

---

## Run dashboard on VPS

### 1. Build the frontend (run once in the Replit monorepo)

```bash
bash dashboard/build.sh
```

This builds the React app with the correct base path (`/`) and copies the
output into `dashboard/public/`. Run this whenever the dashboard UI changes.

### 2. Copy the dashboard folder to your VPS

```bash
rsync -av --exclude='.env' dashboard/ user@your-vps:/srv/chilltopia-dashboard/
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

Key variables:

| Variable | Required | Description |
|---|---|---|
| `PORT` | no | HTTP port (default: `3000`) |
| `DB_PATH` | **yes** | Absolute path to `highrise_hangout.db` |
| `AZURACAST_STREAM_URL` | no | Public audio stream URL shown to listeners |

**Security note:** Never add `AZURA_API_KEY` to this server's config.
The AzuraCast API key is used only by the Python bot and must never be
sent to the browser.

### 5. Start the server

```bash
npm start
```

The dashboard is now available at `http://your-vps:3000`.

---

## Keep the database in sync

The dashboard reads from the same SQLite file the bot writes to. Options:

**Option A — Shared filesystem (easiest)**
If your bot and dashboard run on the same machine, point `DB_PATH` directly
at the bot's database file. SQLite's WAL mode allows safe concurrent reads.

```
DB_PATH=/home/chilltopia/highrise-bot/highrise_hangout.db
```

**Option B — Periodic rsync (remote VPS)**
Add a cron job that syncs the DB file from the bot server every few minutes:

```cron
*/2 * * * * rsync -az --checksum user@bot-server:/path/to/highrise_hangout.db /srv/chilltopia-dashboard/highrise_hangout.db
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

The dashboard polls `/api/dj/status` every **15 seconds** and shows a
countdown ring in the header. No changes needed — this is built into the
React frontend.

---

## Reverse proxy with nginx (optional)

```nginx
server {
    server_name dj.your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Then add SSL with Certbot:
```bash
certbot --nginx -d dj.your-domain.com
```
