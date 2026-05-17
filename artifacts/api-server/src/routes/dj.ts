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
  const row = db
    .prepare("SELECT value FROM room_settings WHERE key = ? LIMIT 1")
    .get(key) as { value: string } | undefined;
  return row?.value ?? fallback;
}

router.get("/dj/status", (_req, res) => {
  let db: Database.Database | null = null;
  try {
    db = openDb();

    const nowPlaying = db
      .prepare(
        "SELECT id, title, username, dedication, youtube_url, priority " +
        "FROM dj_requests WHERE status = 'playing' ORDER BY id DESC LIMIT 1",
      )
      .get() as NowPlaying | undefined;

    const queueRows = db
      .prepare(
        "SELECT id, title, username, dedication, priority " +
        "FROM dj_requests WHERE status = 'pending' " +
        "ORDER BY priority DESC, requested_at ASC LIMIT 25",
      )
      .all() as Omit<QueueEntry, "pos">[];

    const recent = db
      .prepare(
        "SELECT title, username, played_at " +
        "FROM dj_requests WHERE status = 'played' " +
        "ORDER BY played_at DESC LIMIT 10",
      )
      .all() as RecentEntry[];

    const totalFavs = (
      db.prepare("SELECT COUNT(*) AS n FROM dj_favorites").get() as { n: number }
    ).n;

    const totalLikes = (
      db
        .prepare("SELECT COUNT(*) AS n FROM dj_ratings WHERE rating = 'like'")
        .get() as { n: number }
    ).n;

    const today = new Date().toISOString().slice(0, 10);
    const playedToday = (
      db
        .prepare(
          "SELECT COUNT(*) AS n FROM dj_requests " +
          "WHERE status = 'played' AND played_at >= ?",
        )
        .get(today) as { n: number }
    ).n;

    const radioUrl = getSetting(db, "dj_radio_url").trim() || null;
    const queueLocked = getSetting(db, "dj_queue_locked") === "1";

    res.json({
      now_playing: nowPlaying ?? null,
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
    });
  } catch (err) {
    logger.error({ err }, "DJ status read failed");
    res.status(503).json({ error: "DJ status unavailable — bot may not be running yet" });
  } finally {
    db?.close();
  }
});

export default router;
