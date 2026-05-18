/**
 * dashboard/server.mjs
 * Standalone ChillTopia DJ Dashboard server — for VPS hosting.
 *
 * DATA SOURCE (pick one via .env):
 *
 *   Mode A — Remote (Replit live data, no local DB needed):
 *     REMOTE_STATUS_URL=https://YOUR-REPL.replit.dev/api/dj/status
 *     The server proxies that URL and returns the data to the browser.
 *     No Highrise token, no AzuraCast API key is ever read or forwarded.
 *
 *   Mode B — Local SQLite (bot running on same machine or DB synced via rsync):
 *     DB_PATH=/absolute/path/to/highrise_hangout.db
 *     Reads directly from the bot's SQLite file (read-only).
 *
 *   If REMOTE_STATUS_URL is set it takes priority.
 *   DB_PATH is the fallback when REMOTE_STATUS_URL is absent.
 *
 * Usage:
 *   cp .env.example .env && nano .env
 *   npm install
 *   npm start
 *
 * Serves:
 *   GET /              → React dashboard (static files from ./public/)
 *   GET /api/dj/status → live DJ/queue data (remote proxy or local SQLite)
 *   GET /api/healthz   → health check
 */

import express from "express";
import { fileURLToPath } from "url";
import path from "path";
import fs from "fs";

// ── Config ────────────────────────────────────────────────────────────────────

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const PORT = parseInt(process.env.PORT ?? "3000", 10);

// Mode A: proxy live data from Replit (or any remote API server).
// Set this to your Replit dev/prod URL + /api/dj/status
// e.g. https://abc123.replit.dev/api/dj/status
const REMOTE_STATUS_URL = process.env.REMOTE_STATUS_URL?.trim() || null;

// Mode B fallback: local SQLite path
const DB_PATH = process.env.DB_PATH
  ? path.resolve(process.env.DB_PATH)
  : path.join(__dirname, "highrise_hangout.db");

// Optional: override stream URL in the API response (audio stream, safe for browser).
// NEVER put AZURA_API_KEY here — that stays on the bot server only.
const AZURACAST_STREAM_URL = process.env.AZURACAST_STREAM_URL?.trim() || null;

// Fetch timeout for remote mode (ms)
const REMOTE_TIMEOUT_MS = parseInt(process.env.REMOTE_TIMEOUT_MS ?? "8000", 10);

const PUBLIC_DIR = path.join(__dirname, "public");

// ── Remote helper (Mode A) ────────────────────────────────────────────────────

async function fetchRemoteStatus() {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), REMOTE_TIMEOUT_MS);
  try {
    const resp = await fetch(REMOTE_STATUS_URL, {
      signal: ctrl.signal,
      headers: { "Accept": "application/json" },
    });
    if (!resp.ok) {
      throw new Error(`Remote returned HTTP ${resp.status}`);
    }
    const data = await resp.json();
    // Apply local AZURACAST_STREAM_URL override if set
    if (AZURACAST_STREAM_URL && data && typeof data === "object") {
      data.radio_url = AZURACAST_STREAM_URL;
    }
    return data;
  } finally {
    clearTimeout(timer);
  }
}

// ── Local SQLite helper (Mode B) ──────────────────────────────────────────────

async function readLocalStatus() {
  // Dynamic import so the package is not required when running in remote mode
  const Database = (await import("better-sqlite3")).default;

  if (!fs.existsSync(DB_PATH)) {
    throw new Error(
      `DB_PATH not found: ${DB_PATH}\n` +
      `Set DB_PATH in .env (local mode) or set REMOTE_STATUS_URL (remote mode).`
    );
  }

  const db = new Database(DB_PATH, { readonly: true, fileMustExist: true });
  try {
    function getSetting(key, fallback = "") {
      const row = db
        .prepare("SELECT value FROM room_settings WHERE key = ? LIMIT 1")
        .get(key);
      return row?.value ?? fallback;
    }

    const nowPlaying =
      db
        .prepare(
          "SELECT id, title, username, dedication, youtube_url, priority " +
          "FROM dj_requests WHERE status = 'playing' ORDER BY id DESC LIMIT 1"
        )
        .get() ?? null;

    const queueRows = db
      .prepare(
        "SELECT id, title, username, dedication, priority " +
        "FROM dj_requests WHERE status = 'pending' " +
        "ORDER BY priority DESC, requested_at ASC LIMIT 25"
      )
      .all();

    const recent = db
      .prepare(
        "SELECT title, username, played_at " +
        "FROM dj_requests WHERE status = 'played' " +
        "ORDER BY played_at DESC LIMIT 10"
      )
      .all();

    const totalFavs  = db.prepare("SELECT COUNT(*) AS n FROM dj_favorites").get().n;
    const totalLikes = db.prepare("SELECT COUNT(*) AS n FROM dj_ratings WHERE rating = 'like'").get().n;

    const today      = new Date().toISOString().slice(0, 10);
    const playedToday = db
      .prepare(
        "SELECT COUNT(*) AS n FROM dj_requests " +
        "WHERE status = 'played' AND played_at >= ?"
      )
      .get(today).n;

    const dbRadioUrl = getSetting("dj_radio_url").trim() || null;
    const radioUrl   = AZURACAST_STREAM_URL ?? dbRadioUrl;
    const queueLocked = getSetting("dj_queue_locked") === "1";

    const rawRadioType = getSetting("dj_radio_type", "external").trim().toLowerCase();
    const radioType    = ["icecast", "azuracast", "external"].includes(rawRadioType)
      ? rawRadioType : "external";
    const radioMount           = getSetting("dj_radio_mount").trim() || null;
    const radioMetadataEnabled = getSetting("dj_radio_metadata", "off").trim().toLowerCase() === "on";

    return {
      now_playing: nowPlaying,
      queue: queueRows.map((r, i) => ({ ...r, pos: i + 1 })),
      recent,
      stats: {
        total_queued: queueRows.length,
        total_played_today: playedToday,
        total_favorites: totalFavs,
        total_likes: totalLikes,
      },
      radio_url: radioUrl,
      queue_open: !queueLocked,
      updated_at: new Date().toISOString(),
      radio_type: radioType,
      radio_mount: radioMount,
      radio_metadata_enabled: radioMetadataEnabled,
      listener_count: null,
      stream_live: null,
    };
  } finally {
    db.close();
  }
}

// ── Express app ───────────────────────────────────────────────────────────────

const app = express();
app.disable("x-powered-by");

// Serve the built React frontend from ./public/
if (fs.existsSync(PUBLIC_DIR)) {
  app.use(express.static(PUBLIC_DIR));
} else {
  console.warn(
    "[DASHBOARD] WARNING: ./public/ not found.\n" +
    "  Run `npm run build` from the monorepo root, then copy\n" +
    "  artifacts/dj-status/dist/public/ into dashboard/public/"
  );
}

// Health check
app.get("/api/healthz", (_req, res) => {
  res.json({
    status: "ok",
    service: "chilltopia-dj-dashboard",
    mode: REMOTE_STATUS_URL ? "remote" : "local",
  });
});

// DJ status — proxies Replit (Mode A) or reads local SQLite (Mode B)
app.get("/api/dj/status", async (req, res) => {
  try {
    const data = REMOTE_STATUS_URL
      ? await fetchRemoteStatus()
      : await readLocalStatus();
    res.json(data);
  } catch (err) {
    const msg = err.message ?? String(err);
    const isAbort  = err.name === "AbortError";
    const notFound = msg.includes("not found");

    console.error(
      `[DASHBOARD] DJ status failed (${REMOTE_STATUS_URL ? "remote" : "local"}):`,
      msg
    );

    res.status(isAbort || !notFound ? 503 : 500).json({
      error: isAbort
        ? `Remote timed out after ${REMOTE_TIMEOUT_MS}ms — check REMOTE_STATUS_URL`
        : notFound
        ? "Database not found — check DB_PATH in .env"
        : REMOTE_STATUS_URL
        ? "Remote status unavailable — is the Replit bot running?"
        : "DJ status unavailable — bot may not be running yet",
    });
  }
});

// SPA fallback
app.get("*", (_req, res) => {
  const index = path.join(PUBLIC_DIR, "index.html");
  if (fs.existsSync(index)) {
    res.sendFile(index);
  } else {
    res.status(503).send(
      "Dashboard not built yet. Run `npm run build` from the monorepo root, " +
      "then copy artifacts/dj-status/dist/public/ into dashboard/public/"
    );
  }
});

// ── Start ─────────────────────────────────────────────────────────────────────

app.listen(PORT, "0.0.0.0", () => {
  const mode = REMOTE_STATUS_URL ? "remote" : "local";
  console.log(`[DASHBOARD] stage=dashboard_startup mode=${mode} port=${PORT}`);
  if (REMOTE_STATUS_URL) {
    console.log(`[DASHBOARD] data_source=remote url=${REMOTE_STATUS_URL}`);
  } else {
    console.log(`[DASHBOARD] data_source=local db=${DB_PATH}`);
  }
  if (AZURACAST_STREAM_URL) {
    console.log(`[DASHBOARD] stream_url_override=${AZURACAST_STREAM_URL}`);
  }
  console.log(`[DASHBOARD] Open: http://localhost:${PORT}`);
});
