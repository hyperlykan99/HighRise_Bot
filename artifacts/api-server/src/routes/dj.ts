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

// Statuses that represent songs in the prepare pipeline — mirrors display_jobs() in request_queue.py.
// "ready" = uploaded and registered in AzuraCast Requests playlist, awaiting playback
// Excludes: playing (on air), played/error (terminal)
const PENDING_STATUSES = ["pending", "downloading", "downloaded", "uploading", "ready"] as const;

interface NowPlaying {
  id: number;
  title: string;
  artist: string;
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

function openDbWrite(): Database.Database {
  return new Database(DB_PATH, { readonly: false, fileMustExist: true });
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
          "SELECT id, title, COALESCE(artist, '') AS artist, username, url, priority " +
          "FROM yt_request_jobs WHERE status = 'playing' ORDER BY id DESC LIMIT 1",
        )
        .get() as
        | { id: number; title: string; artist: string; username: string; url: string; priority: number }
        | undefined;
      if (row) {
        nowPlaying = {
          id: row.id,
          title: row.title ?? "",
          artist: row.artist ?? "",
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
    const today = new Date().toISOString().slice(0, 10);
    const hasRatings = tableExists(db, "dj_ratings");

    let totalFavs = 0;
    if (tableExists(db, "dj_favorites")) {
      totalFavs = (
        db.prepare("SELECT COUNT(*) AS n FROM dj_favorites").get() as { n: number }
      ).n;
    }

    let totalLikes = 0;
    let likesToday = 0;
    let dislikesToday = 0;
    if (hasRatings) {
      totalLikes = (
        db.prepare("SELECT COUNT(*) AS n FROM dj_ratings WHERE rating = 'like'").get() as { n: number }
      ).n;
      likesToday = (
        db.prepare("SELECT COUNT(*) AS n FROM dj_ratings WHERE rating = 'like' AND rated_at >= ?").get(today) as { n: number }
      ).n;
      dislikesToday = (
        db.prepare("SELECT COUNT(*) AS n FROM dj_ratings WHERE rating = 'dislike' AND rated_at >= ?").get(today) as { n: number }
      ).n;
    }

    let playedToday = 0;
    let requestsToday = 0;
    let failedToday = 0;
    let readyCount = 0;
    let preparingCount = 0;
    let topRequesterToday: string | null = null;
    if (hasYtJobs) {
      playedToday = (
        db.prepare("SELECT COUNT(*) AS n FROM yt_request_jobs WHERE status = 'played' AND played_at >= ?").get(today) as { n: number }
      ).n;
      requestsToday = (
        db.prepare("SELECT COUNT(*) AS n FROM yt_request_jobs WHERE date(started_at) = ?").get(today) as { n: number }
      ).n;
      failedToday = (
        db.prepare("SELECT COUNT(*) AS n FROM yt_request_jobs WHERE status = 'error' AND date(started_at) = ?").get(today) as { n: number }
      ).n;
      readyCount = (
        db.prepare("SELECT COUNT(*) AS n FROM yt_request_jobs WHERE status = 'ready'").get() as { n: number }
      ).n;
      preparingCount = (
        db.prepare(
          "SELECT COUNT(*) AS n FROM yt_request_jobs WHERE status IN ('pending','downloading','downloaded','uploading')"
        ).get() as { n: number }
      ).n;
      const topRow = db
        .prepare(
          "SELECT username, COUNT(*) AS cnt FROM yt_request_jobs " +
          "WHERE date(started_at) = ? AND username != '' " +
          "GROUP BY lower(username) ORDER BY cnt DESC LIMIT 1"
        )
        .get(today) as { username: string; cnt: number } | undefined;
      topRequesterToday = topRow?.username ?? null;
    }

    // Current vibe from room_settings
    const currentVibe = getSetting(db, "vibe", "chill");

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
        total_queued:         queueRows.length,
        total_played_today:   playedToday,
        total_favorites:      totalFavs,
        total_likes:          totalLikes,
        requests_today:       requestsToday,
        likes_today:          likesToday,
        dislikes_today:       dislikesToday,
        failed_today:         failedToday,
        top_requester_today:  topRequesterToday,
        current_vibe:         currentVibe,
        queue_size:           queueRows.length,
        ready_count:          readyCount,
        preparing_count:      preparingCount,
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

// ─── POST /dj/cleanup — queue a requests-playlist reconciliation ─────────────
// Sets a flag in room_settings; the Python bot polls and runs reconcile within 60s.
router.post("/dj/cleanup", (_req, res) => {
  res.setHeader("Cache-Control", "no-store");
  let wdb: Database.Database | null = null;
  try {
    wdb = openDbWrite();
    wdb
      .prepare(
        "INSERT INTO room_settings (key, value) VALUES ('cleanup_requested', '1') " +
        "ON CONFLICT(key) DO UPDATE SET value = '1'",
      )
      .run();
    res.json({ ok: true, message: "Cleanup queued — the bot will process it within 60s." });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    logger.error({ err }, "DJ cleanup trigger failed");
    res.status(500).json({ error: msg });
  } finally {
    wdb?.close();
  }
});

export default router;
