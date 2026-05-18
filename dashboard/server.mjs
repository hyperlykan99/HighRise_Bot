/**
 * dashboard/server.mjs
 * Standalone ChillTopia DJ Dashboard server — for VPS hosting.
 *
 * Usage:
 *   cp .env.example .env && nano .env
 *   npm install
 *   npm start
 *
 * Serves:
 *   GET /            → React dashboard (static files from ./public/)
 *   GET /api/dj/status → live DJ/queue data read from the bot's SQLite DB
 *   GET /api/healthz → health check
 *
 * Environment variables (see .env.example for full list):
 *   PORT                  HTTP port (default: 3000)
 *   DB_PATH               Absolute path to highrise_hangout.db (required)
 *   AZURACAST_STREAM_URL  Public audio stream URL shown to listeners
 *                         Overrides the radio_url value stored in the DB.
 *                         This is the stream URL only — the AzuraCast API
 *                         key is NEVER read by this server and NEVER sent
 *                         to the browser.
 */

import express from "express";
import Database from "better-sqlite3";
import { fileURLToPath } from "url";
import path from "path";
import fs from "fs";

// ── Config ────────────────────────────────────────────────────────────────────

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const PORT   = parseInt(process.env.PORT ?? "3000", 10);
const DB_PATH = process.env.DB_PATH
  ? path.resolve(process.env.DB_PATH)
  : path.join(__dirname, "highrise_hangout.db");

// Public AzuraCast stream URL (audio stream only — safe for frontend).
// Never use AZURA_API_KEY here; it must stay server-side in the bot only.
const AZURACAST_STREAM_URL = process.env.AZURACAST_STREAM_URL?.trim() || null;

const PUBLIC_DIR = path.join(__dirname, "public");

// ── Helpers ───────────────────────────────────────────────────────────────────

function openDb() {
  if (!fs.existsSync(DB_PATH)) {
    throw new Error(
      `DB_PATH not found: ${DB_PATH}\n` +
      `Set DB_PATH in your .env to the absolute path of highrise_hangout.db`
    );
  }
  return new Database(DB_PATH, { readonly: true, fileMustExist: true });
}

function getSetting(db, key, fallback = "") {
  const row = db
    .prepare("SELECT value FROM room_settings WHERE key = ? LIMIT 1")
    .get(key);
  return row?.value ?? fallback;
}

// ── Express app ───────────────────────────────────────────────────────────────

const app = express();
app.disable("x-powered-by");

// Serve the built React frontend from ./public/
if (fs.existsSync(PUBLIC_DIR)) {
  app.use(express.static(PUBLIC_DIR));
} else {
  console.warn(
    "[DASHBOARD] WARNING: ./public/ not found — run `npm run build` first, " +
    "or copy the built frontend files into dashboard/public/"
  );
}

// Health check
app.get("/api/healthz", (_req, res) => {
  res.json({ status: "ok", service: "chilltopia-dj-dashboard" });
});

// DJ status API — mirrors artifacts/api-server/src/routes/dj.ts
// AZURACAST_STREAM_URL overrides radio_url from the DB when set.
// No API keys are ever included in this response.
app.get("/api/dj/status", (req, res) => {
  let db = null;
  try {
    db = openDb();

    const nowPlaying = db
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

    const totalFavs = db
      .prepare("SELECT COUNT(*) AS n FROM dj_favorites")
      .get().n;

    const totalLikes = db
      .prepare("SELECT COUNT(*) AS n FROM dj_ratings WHERE rating = 'like'")
      .get().n;

    const today = new Date().toISOString().slice(0, 10);
    const playedToday = db
      .prepare(
        "SELECT COUNT(*) AS n FROM dj_requests " +
        "WHERE status = 'played' AND played_at >= ?"
      )
      .get(today).n;

    // radio_url: env override wins; fall back to DB setting
    const dbRadioUrl = getSetting(db, "dj_radio_url").trim() || null;
    const radioUrl   = AZURACAST_STREAM_URL ?? dbRadioUrl;

    const queueLocked = getSetting(db, "dj_queue_locked") === "1";

    const rawRadioType = getSetting(db, "dj_radio_type", "external").trim().toLowerCase();
    const radioType    = ["icecast", "azuracast", "external"].includes(rawRadioType)
      ? rawRadioType : "external";
    const radioMount           = getSetting(db, "dj_radio_mount").trim() || null;
    const radioMetadataEnabled = getSetting(db, "dj_radio_metadata", "off").trim().toLowerCase() === "on";

    res.json({
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
    });
  } catch (err) {
    console.error("[DASHBOARD] DJ status read failed:", err.message);
    const notFound = err.message?.includes("not found");
    res.status(notFound ? 500 : 503).json({
      error: notFound
        ? "Database not found — check DB_PATH in your .env"
        : "DJ status unavailable — bot may not be running yet",
    });
  } finally {
    db?.close();
  }
});

// SPA fallback — all non-API routes serve index.html
app.get("*", (req, res) => {
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
  console.log(`[DASHBOARD] stage=dashboard_startup enabled=true port=${PORT}`);
  console.log(`[DASHBOARD] db=${DB_PATH}`);
  console.log(`[DASHBOARD] stream_url=${AZURACAST_STREAM_URL ?? "(from DB)"}`);
  console.log(`[DASHBOARD] Open: http://localhost:${PORT}`);
});
