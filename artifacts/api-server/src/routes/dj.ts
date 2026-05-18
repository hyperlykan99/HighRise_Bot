import { Router, type IRouter } from "express";
import Database from "better-sqlite3";
import path from "path";
import { logger } from "../lib/logger";

const router: IRouter = Router();

// SHARED_DB_PATH env var is relative to bot's CWD (artifacts/highrise-bot/)
// process.cwd() = artifacts/api-server/ at runtime; go up two levels to workspace root
const WORKSPACE_ROOT = path.resolve(process.cwd(), "../..");
const BOT_DIR = path.join(WORKSPACE_ROOT, "artifacts/highrise-bot");
const SHARED_DB = process.env.SHARED_DB_PATH ?? "highrise_hangout.db";
const DB_PATH = path.isAbsolute(SHARED_DB) ? SHARED_DB : path.join(BOT_DIR, SHARED_DB);

// Public AzuraCast stream URL — overrides the radio_url stored in the DB.
// AZURA_API_KEY is used only by the Python bot and must NEVER be forwarded here.
const AZURACAST_STREAM_URL = process.env.AZURACAST_STREAM_URL?.trim() || null;

// Statuses that represent songs waiting to play — mirrors !queue filter in radio_commands.py
// (status != 'playing'; excludes played/error/cancelled)
const PENDING_STATUSES = ["pending", "downloading", "uploading", "done", "queued"] as const;

interface NowPlaying {
  id: number;
  title: string;
  username: string;
  dedication: string;
  youtube_url: string;
  priority: number;
}

interface QueueEntry {
  id: number;
  title: string;
  username: string;
  dedication: string;
  priority: number;
  pos: number;
}

interface RecentEntry {
  title: string;
  username: string;
  played_at: string;
}

function openDb(): Database.Database {
  return new Database(DB_PATH, { readonly: true, fileMustExist: true });
}

function getSetting(db: Database.Database, key: string, fallback = ""): string {
  try {
    const row = db
      .prepare("SELECT value FROM room_settings WHERE key = ? LIMIT 1")
      .get(key) as { value: string } | undefined;
    return row?.value ?? fallback;
  } catch {
    return fallback;
  }
}

function tableExists(db: Database.Database, name: string): boolean {
  const row = db
    .prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1")
    .get(name);
  return !!row;
}

router.get("/dj/status", (_req, res) => {
  // Dashboard must always reflect live state — never serve a cached response
  res.setHeader("Cache-Control", "no-store");

  let db: Database.Database | null = null;
  try {
    db = openDb();

    const hasYtJobs = tableExists(db, "yt_request_jobs");

    // ── Now Playing ────────────────────────────────────────────────────────
    // Reads from yt_request_jobs (same source as !now / nowplaying cycle)
    let nowPlaying: NowPlaying | null = null;
    if (hasYtJobs) {
      const row = db
        .prepare(
          "SELECT id, title, username, url, priority " +
          "FROM yt_request_jobs WHERE status = 'playing' ORDER BY id DESC LIMIT 1",
        )
        .get() as
        | { id: number; title: string; username: string; url: string; priority: number }
        | undefined;
      if (row) {
        nowPlaying = {
          id: row.id,
          title: row.title ?? "",
          username: row.username ?? "",
          dedication: "",            // yt_request_jobs has no dedication column
          youtube_url: row.url ?? "",
          priority: row.priority ?? 0,
        };
      }
    }

    // ── Queue (pending only, mirrors !queue) ───────────────────────────────
    // Shows all songs not yet playing — same filter as handle_queue in radio_commands.py
    const queueRows: Omit<QueueEntry, "pos">[] = [];
    if (hasYtJobs) {
      const ph   = PENDING_STATUSES.map(() => "?").join(", ");
      const rows = db
        .prepare(
          `SELECT id, title, username, url, priority FROM yt_request_jobs ` +
          `WHERE status IN (${ph}) ORDER BY priority DESC, started_at ASC LIMIT 25`,
        )
        .all(...PENDING_STATUSES) as {
          id: number;
          title: string;
          username: string;
          url: string;
          priority: number;
        }[];
      for (const r of rows) {
        queueRows.push({
          id: r.id,
          title: r.title ?? "",
          username: r.username ?? "",
          dedication: "",
          priority: r.priority ?? 0,
        });
      }
    }

    // ── Recently Played ────────────────────────────────────────────────────
    // Reads played request history (same source as !queue history / cleanup cycle)
    let recent: RecentEntry[] = [];
    if (hasYtJobs) {
      recent = db
        .prepare(
          "SELECT title, username, played_at FROM yt_request_jobs " +
          "WHERE status = 'played' AND played_at IS NOT NULL " +
          "ORDER BY played_at DESC LIMIT 10",
        )
        .all() as RecentEntry[];
    }

    // ── Stats ──────────────────────────────────────────────────────────────
    let totalFavs = 0;
    if (tableExists(db, "dj_favorites")) {
      totalFavs = (
        db.prepare("SELECT COUNT(*) AS n FROM dj_favorites").get() as { n: number }
      ).n;
    }

    let totalLikes = 0;
    if (tableExists(db, "dj_ratings")) {
      totalLikes = (
        db
          .prepare("SELECT COUNT(*) AS n FROM dj_ratings WHERE rating = 'like'")
          .get() as { n: number }
      ).n;
    }

    let playedToday = 0;
    if (hasYtJobs) {
      const today = new Date().toISOString().slice(0, 10);
      playedToday = (
        db
          .prepare(
            "SELECT COUNT(*) AS n FROM yt_request_jobs " +
            "WHERE status = 'played' AND played_at >= ?",
          )
          .get(today) as { n: number }
      ).n;
    }

    // ── Radio config (room_settings) ───────────────────────────────────────
    const dbRadioUrl = getSetting(db, "dj_radio_url").trim() || null;
    const radioUrl   = AZURACAST_STREAM_URL ?? dbRadioUrl;

    // Queue open: check requests_enabled (new system); fall back to !dj_queue_locked (old)
    const reqEnabled  = getSetting(db, "requests_enabled", "true").trim().toLowerCase();
    const queueLocked = getSetting(db, "dj_queue_locked", "0") === "1";
    const queueOpen   = reqEnabled !== "false" && reqEnabled !== "0" && !queueLocked;

    const rawRadioType = getSetting(db, "dj_radio_type", "external").trim().toLowerCase();
    const radioType = (["icecast", "azuracast", "external"] as const).includes(
      rawRadioType as "icecast" | "azuracast" | "external",
    )
      ? (rawRadioType as "icecast" | "azuracast" | "external")
      : ("external" as const);

    const radioMount           = getSetting(db, "dj_radio_mount").trim() || null;
    const radioMetadataEnabled =
      getSetting(db, "dj_radio_metadata", "off").trim().toLowerCase() === "on";

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
      queue_open: queueOpen,
      updated_at: new Date().toISOString(),
      radio_type: radioType,
      radio_mount: radioMount,
      radio_metadata_enabled: radioMetadataEnabled,
      listener_count: null,
      stream_live: null,
    });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    const isNoDb =
      msg.includes("ENOENT") ||
      msg.includes("no such file") ||
      msg.includes("fileMustExist") ||
      msg.includes("not found");
    if (isNoDb) {
      res.status(503).json({ error: "Bot database not connected yet" });
    } else {
      logger.error({ err }, "DJ status read failed");
      res.status(503).json({ error: "DJ status unavailable — bot may not be running yet" });
    }
  } finally {
    db?.close();
  }
});

export default router;
