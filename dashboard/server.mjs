/**
 * dashboard/server.mjs
 * Owner/staff control dashboard sidecar for the HighRise multi-bot project.
 *
 * Architecture:
 *   Browser UI -> this Express API -> shared SQLite DB -> bots read DB settings.
 *
 * This server never edits Python source files and never exposes bot tokens or
 * AzuraCast secrets to the browser. Write actions are stored in DB tables and
 * audit logged so bot modules can opt in safely.
 */

import crypto from "crypto";
import express from "express";
import { fileURLToPath } from "url";
import path from "path";
import fs from "fs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = parseInt(process.env.PORT ?? "3000", 10);
const REMOTE_STATUS_URL = process.env.REMOTE_STATUS_URL?.trim() || null;
const AZURACAST_STREAM_URL = process.env.AZURACAST_STREAM_URL?.trim() || null;
const REMOTE_TIMEOUT_MS = parseInt(process.env.REMOTE_TIMEOUT_MS ?? "8000", 10);
const SESSION_DAYS = parseInt(process.env.DASHBOARD_SESSION_DAYS ?? "7", 10);
const PUBLIC_DIR = path.join(__dirname, "public");
const APP_MODE = process.env.NODE_ENV || process.env.APP_MODE || "production";
const VPS_ENV_PATH = "/opt/highrise-bots/.env";

function readEnvFileValue(filePath, key) {
  try {
    if (!fs.existsSync(filePath)) return null;
    for (const line of fs.readFileSync(filePath, "utf8").split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const eq = trimmed.indexOf("=");
      if (eq === -1 || trimmed.slice(0, eq).trim() !== key) continue;
      return trimmed.slice(eq + 1).trim().replace(/^['"]|['"]$/g, "") || null;
    }
  } catch (err) {
    console.error(`[DASHBOARD_CONFIG] env_file_read_failed path=${filePath} error=${err.message}`);
  }
  return null;
}

function resolveDbPath() {
  const configured =
    process.env.DB_PATH?.trim() ||
    process.env.SHARED_DB_PATH?.trim() ||
    readEnvFileValue(VPS_ENV_PATH, "SHARED_DB_PATH") ||
    path.join(__dirname, "..", "artifacts", "highrise-bot", "highrise_hangout.db");
  return path.resolve(configured);
}

const DB_PATH = resolveDbPath();

const PERMISSIONS = [
  "manage_radio",
  "manage_casino",
  "manage_games",
  "manage_titles",
  "manage_staff",
  "view_logs",
  "emergency_controls",
];

const ACTIVE_REQUEST_STATUSES = [
  "pending",
  "preparing",
  "downloading",
  "downloaded",
  "copying",
  "uploading",
  "indexing",
  "ready",
  "queued",
  "submitted",
  "playing",
];
const UPCOMING_REQUEST_STATUSES = ACTIVE_REQUEST_STATUSES.filter((s) => s !== "playing");
const TERMINAL_REQUEST_STATUSES = ["played", "cleaned", "skipped", "failed", "cancelled", "error"];
const SAFE_SETTING_KEY = /^[A-Za-z0-9_.:-]{1,120}$/;
const RATE_LIMITS = new Map();
const IMPORTANT_TABLES = [
  "schema_version",
  "dashboard_users",
  "dashboard_roles",
  "dashboard_permissions",
  "dashboard_sessions",
  "bot_settings",
  "module_flags",
  "audit_logs",
  "player_titles",
  "live_status",
  "bot_instances",
  "bot_spawns",
  "bot_command_queue",
  "jail_sentences",
  "first_find_announce_pending",
  "host_dm_queue",
  "yt_request_jobs",
  "room_settings",
  "title_catalog",
  "user_titles",
  "owner_users",
  "admin_users",
  "moderators",
  "managers",
  "owned_items",
  "admin_action_logs",
  "command_error_logs",
];
let LAST_MIGRATION_STATUS = {
  ok: false,
  ran_at: null,
  created_tables: [],
  added_columns: [],
  error: null,
};

let DatabaseCtor = null;
async function Database() {
  if (!DatabaseCtor) DatabaseCtor = (await import("better-sqlite3")).default;
  return DatabaseCtor;
}

function json(res, body, status = 200) {
  res.status(status).json(body);
}

function nowIso() {
  return new Date().toISOString();
}

function tableExists(db, name) {
  const row = db
    .prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1")
    .get(name);
  return !!row;
}

function sqlIdent(name) {
  return `"${String(name).replaceAll('"', '""')}"`;
}

function tableNames(db) {
  return db
    .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    .all()
    .map((row) => row.name);
}

function tableColumns(db, name) {
  if (!tableExists(db, name)) return [];
  return db.prepare(`PRAGMA table_info(${sqlIdent(name)})`).all().map((row) => row.name);
}

function columnExists(db, table, column) {
  return tableColumns(db, table).includes(column);
}

function addColumnIfMissing(db, table, column, definition, status) {
  if (!tableExists(db, table) || columnExists(db, table, column)) return;
  db.prepare(`ALTER TABLE ${sqlIdent(table)} ADD COLUMN ${sqlIdent(column)} ${definition}`).run();
  status.added_columns.push(`${table}.${column}`);
}

function selectColumns(db, table, desired) {
  const cols = tableColumns(db, table);
  return desired.filter((col) => cols.includes(col));
}

function normalizeRows(rows, desired) {
  return rows.map((row) => {
    const out = {};
    for (const col of desired) out[col] = Object.prototype.hasOwnProperty.call(row, col) ? row[col] : null;
    return out;
  });
}

function safeRows(db, table, desired, { where = "", params = [], orderBy = "", limit = "" } = {}) {
  try {
    if (!tableExists(db, table)) return [];
    const selected = selectColumns(db, table, desired);
    if (!selected.length) return [];
    const sql = `SELECT ${selected.map(sqlIdent).join(", ")} FROM ${sqlIdent(table)}${where ? ` WHERE ${where}` : ""}${orderBy ? ` ORDER BY ${orderBy}` : ""}${limit ? ` LIMIT ${limit}` : ""}`;
    return normalizeRows(db.prepare(sql).all(...params), desired);
  } catch (err) {
    console.error(`[DASHBOARD_DB] safeRows table=${table} error=${err.message}`);
    return [];
  }
}

function safeOne(db, table, desired, options = {}) {
  return safeRows(db, table, desired, { ...options, limit: "1" })[0] ?? null;
}

function safeJsonParse(text, fallback) {
  try {
    return text ? JSON.parse(text) : fallback;
  } catch {
    return fallback;
  }
}

function hashToken(token) {
  return crypto.createHash("sha256").update(token).digest("hex");
}

function csrfForToken(token) {
  const secret = process.env.DASHBOARD_CSRF_SECRET || `local:${DB_PATH}`;
  return crypto.createHmac("sha256", secret).update(token).digest("hex");
}

function hashPassword(password, salt = crypto.randomBytes(16).toString("hex"), iterations = 210000) {
  const hash = crypto.pbkdf2Sync(password, salt, iterations, 32, "sha256").toString("hex");
  return { hash, salt, iterations };
}

function verifyPassword(password, user) {
  const { hash } = hashPassword(password, user.salt, user.iterations);
  return crypto.timingSafeEqual(Buffer.from(hash, "hex"), Buffer.from(user.password_hash, "hex"));
}

function parseCookies(req) {
  const raw = req.headers.cookie || "";
  return Object.fromEntries(
    raw
      .split(";")
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => {
        const i = part.indexOf("=");
        return i === -1 ? [part, ""] : [part.slice(0, i), decodeURIComponent(part.slice(i + 1))];
      }),
  );
}

function setSessionCookie(res, token, expiresAt) {
  const secure = process.env.DASHBOARD_COOKIE_SECURE === "1" ? "; Secure" : "";
  res.setHeader(
    "Set-Cookie",
    `dashboard_session=${encodeURIComponent(token)}; HttpOnly; SameSite=Lax; Path=/; Expires=${new Date(expiresAt).toUTCString()}${secure}`,
  );
}

function clearSessionCookie(res) {
  res.setHeader(
    "Set-Cookie",
    "dashboard_session=; HttpOnly; SameSite=Lax; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT",
  );
}

function rateLimit({ key = "global", windowMs = 60_000, max = 120 } = {}) {
  return (req, res, next) => {
    const ident = `${key}:${req.ip || "unknown"}`;
    const now = Date.now();
    const bucket = RATE_LIMITS.get(ident) || { start: now, count: 0 };
    if (now - bucket.start > windowMs) {
      bucket.start = now;
      bucket.count = 0;
    }
    bucket.count += 1;
    RATE_LIMITS.set(ident, bucket);
    if (bucket.count > max) return json(res, { error: "rate_limited" }, 429);
    return next();
  };
}

async function openDb({ readonly = false } = {}) {
  const DB = await Database();
  if (!fs.existsSync(DB_PATH)) {
    throw new Error(`DB_PATH not found: ${DB_PATH}`);
  }
  return new DB(DB_PATH, { readonly, fileMustExist: true });
}

function execOptional(db, sql) {
  try {
    db.exec(sql);
  } catch (err) {
    console.error("[DASHBOARD_DB] migration fragment failed:", err.message);
    throw err;
  }
}

function ensureDashboardSchema(db) {
  const migration = { ok: false, ran_at: nowIso(), created_tables: [], added_columns: [], error: null };
  const beforeTables = new Set(tableNames(db));
  execOptional(
    db,
    `
    CREATE TABLE IF NOT EXISTS schema_version (
      component TEXT PRIMARY KEY,
      version INTEGER NOT NULL DEFAULT 1,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS dashboard_roles (
      role TEXT PRIMARY KEY,
      description TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS dashboard_users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT NOT NULL UNIQUE,
      password_hash TEXT NOT NULL,
      salt TEXT NOT NULL,
      iterations INTEGER NOT NULL DEFAULT 210000,
      role TEXT NOT NULL DEFAULT 'staff',
      flags_json TEXT NOT NULL DEFAULT '{}',
      disabled INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      last_login_at TEXT
    );

    CREATE TABLE IF NOT EXISTS dashboard_permissions (
      user_id INTEGER NOT NULL,
      permission TEXT NOT NULL,
      allowed INTEGER NOT NULL DEFAULT 0,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (user_id, permission)
    );

    CREATE TABLE IF NOT EXISTS dashboard_sessions (
      token_hash TEXT PRIMARY KEY,
      user_id INTEGER NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      expires_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS bot_settings (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL DEFAULT '',
      scope TEXT NOT NULL DEFAULT 'global',
      module TEXT NOT NULL DEFAULT '',
      description TEXT NOT NULL DEFAULT '',
      updated_by TEXT NOT NULL DEFAULT '',
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS module_flags (
      module TEXT PRIMARY KEY,
      enabled INTEGER NOT NULL DEFAULT 1,
      reason TEXT NOT NULL DEFAULT '',
      updated_by TEXT NOT NULL DEFAULT '',
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS audit_logs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      actor TEXT NOT NULL,
      action_type TEXT NOT NULL,
      target_type TEXT NOT NULL DEFAULT '',
      target_id TEXT NOT NULL DEFAULT '',
      old_value TEXT NOT NULL DEFAULT '',
      new_value TEXT NOT NULL DEFAULT '',
      ip_address TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS player_titles (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id TEXT NOT NULL DEFAULT '',
      username TEXT NOT NULL DEFAULT '',
      title_id TEXT NOT NULL,
      display TEXT NOT NULL DEFAULT '',
      color TEXT NOT NULL DEFAULT '',
      source TEXT NOT NULL DEFAULT 'dashboard',
      created_by TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS live_status (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL DEFAULT '',
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    `,
  );
  const afterTables = tableNames(db);
  migration.created_tables = afterTables.filter((name) => !beforeTables.has(name) && IMPORTANT_TABLES.includes(name));

  const seedRole = db.prepare(
    "INSERT OR IGNORE INTO dashboard_roles (role, description) VALUES (?, ?)",
  );
  seedRole.run("owner", "Full dashboard access");
  seedRole.run("staff", "Limited staff access controlled by permission flags");

  addColumnIfMissing(db, "dashboard_users", "username", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "dashboard_users", "password_hash", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "dashboard_users", "salt", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "dashboard_users", "iterations", "INTEGER NOT NULL DEFAULT 210000", migration);
  addColumnIfMissing(db, "dashboard_users", "role", "TEXT NOT NULL DEFAULT 'staff'", migration);
  addColumnIfMissing(db, "dashboard_users", "flags_json", "TEXT NOT NULL DEFAULT '{}'", migration);
  addColumnIfMissing(db, "dashboard_users", "disabled", "INTEGER NOT NULL DEFAULT 0", migration);
  addColumnIfMissing(db, "dashboard_users", "created_at", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "dashboard_users", "updated_at", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "dashboard_users", "last_login_at", "TEXT", migration);

  addColumnIfMissing(db, "dashboard_permissions", "user_id", "INTEGER NOT NULL DEFAULT 0", migration);
  addColumnIfMissing(db, "dashboard_permissions", "permission", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "dashboard_permissions", "allowed", "INTEGER NOT NULL DEFAULT 0", migration);
  addColumnIfMissing(db, "dashboard_permissions", "updated_at", "TEXT NOT NULL DEFAULT ''", migration);

  addColumnIfMissing(db, "dashboard_sessions", "token_hash", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "dashboard_sessions", "user_id", "INTEGER NOT NULL DEFAULT 0", migration);
  addColumnIfMissing(db, "dashboard_sessions", "created_at", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "dashboard_sessions", "expires_at", "TEXT NOT NULL DEFAULT ''", migration);

  addColumnIfMissing(db, "bot_settings", "key", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "bot_settings", "value", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "bot_settings", "scope", "TEXT NOT NULL DEFAULT 'global'", migration);
  addColumnIfMissing(db, "bot_settings", "module", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "bot_settings", "description", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "bot_settings", "updated_by", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "bot_settings", "updated_at", "TEXT NOT NULL DEFAULT ''", migration);

  addColumnIfMissing(db, "module_flags", "module", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "module_flags", "enabled", "INTEGER NOT NULL DEFAULT 1", migration);
  addColumnIfMissing(db, "module_flags", "reason", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "module_flags", "updated_by", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "module_flags", "updated_at", "TEXT NOT NULL DEFAULT ''", migration);

  addColumnIfMissing(db, "audit_logs", "actor", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "audit_logs", "action_type", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "audit_logs", "target_type", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "audit_logs", "target_id", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "audit_logs", "old_value", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "audit_logs", "new_value", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "audit_logs", "ip_address", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "audit_logs", "created_at", "TEXT NOT NULL DEFAULT ''", migration);

  addColumnIfMissing(db, "player_titles", "user_id", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "player_titles", "username", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "player_titles", "title_id", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "player_titles", "display", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "player_titles", "color", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "player_titles", "source", "TEXT NOT NULL DEFAULT 'dashboard'", migration);
  addColumnIfMissing(db, "player_titles", "created_by", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "player_titles", "created_at", "TEXT NOT NULL DEFAULT ''", migration);

  addColumnIfMissing(db, "live_status", "key", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "live_status", "value", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "live_status", "updated_at", "TEXT NOT NULL DEFAULT ''", migration);

  addColumnIfMissing(db, "schema_version", "component", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "schema_version", "version", "INTEGER NOT NULL DEFAULT 1", migration);
  addColumnIfMissing(db, "schema_version", "updated_at", "TEXT NOT NULL DEFAULT ''", migration);

  for (const moduleName of ["radio", "casino", "games", "titles", "staff", "settings"]) {
    const existingFlag = db.prepare("SELECT module FROM module_flags WHERE module=? LIMIT 1").get(moduleName);
    if (!existingFlag) {
      db.prepare("INSERT INTO module_flags (module, enabled, reason, updated_by) VALUES (?, 1, '', 'system')").run(moduleName);
    }
  }
  const existingVersion = db.prepare("SELECT component FROM schema_version WHERE component='dashboard' LIMIT 1").get();
  if (existingVersion) {
    db.prepare("UPDATE schema_version SET version=1, updated_at=CURRENT_TIMESTAMP WHERE component='dashboard'").run();
  } else {
    db.prepare("INSERT INTO schema_version (component, version, updated_at) VALUES ('dashboard', 1, CURRENT_TIMESTAMP)").run();
  }
  migration.ok = true;
  LAST_MIGRATION_STATUS = migration;
  return migration;
}

function bootstrapOwner(db) {
  const username = process.env.DASHBOARD_BOOTSTRAP_OWNER?.trim();
  const password = process.env.DASHBOARD_BOOTSTRAP_PASSWORD?.trim();
  if (!username || !password) return;

  const existing = db
    .prepare("SELECT id FROM dashboard_users WHERE lower(username)=lower(?) LIMIT 1")
    .get(username);
  if (existing) return;

  const pw = hashPassword(password);
  const flags = Object.fromEntries(PERMISSIONS.map((p) => [p, true]));
  const info = db
    .prepare(
      "INSERT INTO dashboard_users (username, password_hash, salt, iterations, role, flags_json) VALUES (?, ?, ?, ?, 'owner', ?)",
    )
    .run(username, pw.hash, pw.salt, pw.iterations, JSON.stringify(flags));
  const insertPerm = db.prepare(
    "INSERT OR REPLACE INTO dashboard_permissions (user_id, permission, allowed) VALUES (?, ?, 1)",
  );
  for (const perm of PERMISSIONS) insertPerm.run(info.lastInsertRowid, perm);
  audit(db, "system", "bootstrap_owner", "dashboard_user", username, "", "created", "");
  console.log(`[DASHBOARD_AUTH] bootstrap_owner_created username=${username}`);
}

function audit(db, actor, actionType, targetType, targetId, oldValue, newValue, ip) {
  db.prepare(
    "INSERT INTO audit_logs (actor, action_type, target_type, target_id, old_value, new_value, ip_address) VALUES (?, ?, ?, ?, ?, ?, ?)",
  ).run(
    actor || "unknown",
    actionType,
    targetType || "",
    String(targetId || ""),
    typeof oldValue === "string" ? oldValue : JSON.stringify(oldValue ?? ""),
    typeof newValue === "string" ? newValue : JSON.stringify(newValue ?? ""),
    ip || "",
  );
}

function readUserPermissions(db, user) {
  const flags = safeJsonParse(user.flags_json, {});
  const rows = db
    .prepare("SELECT permission, allowed FROM dashboard_permissions WHERE user_id=?")
    .all(user.id);
  for (const row of rows) flags[row.permission] = !!row.allowed;
  if (user.role === "owner") {
    for (const perm of PERMISSIONS) flags[perm] = true;
  }
  return flags;
}

function publicUser(db, user) {
  return {
    id: user.id,
    username: user.username,
    role: user.role,
    permissions: readUserPermissions(db, user),
  };
}

function validSettingKey(key) {
  return SAFE_SETTING_KEY.test(String(key || ""));
}

async function requireAuth(req, res, next) {
  let db = null;
  try {
    const token = parseCookies(req).dashboard_session;
    if (!token) return json(res, { error: "login_required" }, 401);
    db = await openDb();
    ensureDashboardSchema(db);
    const tokenHash = hashToken(token);
    const row = db
      .prepare(
        "SELECT u.* FROM dashboard_sessions s JOIN dashboard_users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at > datetime('now') AND u.disabled=0",
      )
      .get(tokenHash);
    if (!row) return json(res, { error: "login_required" }, 401);
    if (!["GET", "HEAD", "OPTIONS"].includes(req.method)) {
      const csrf = String(req.get("x-csrf-token") || "");
      if (!csrf || csrf !== csrfForToken(token)) {
        return json(res, { error: "csrf_failed" }, 403);
      }
    }
    req.db = db;
    req.user = row;
    req.permissions = readUserPermissions(db, row);
    req.csrfToken = csrfForToken(token);
    let closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      try {
        db.close();
      } catch {}
    };
    res.on("finish", close);
    res.on("close", close);
    return next();
  } catch (err) {
    if (db) db.close();
    return json(res, { error: "auth_failed" }, 500);
  }
}

function closeDb(req, _res, next) {
  next();
}

function requirePermission(permission) {
  return (req, res, next) => {
    if (req.user?.role === "owner" || req.permissions?.[permission]) return next();
    return json(res, { error: "forbidden", permission }, 403);
  };
}

function requireAnyPermission(...permissions) {
  return (req, res, next) => {
    if (req.user?.role === "owner" || permissions.some((p) => req.permissions?.[p])) return next();
    return json(res, { error: "forbidden", permission: permissions.join("|") }, 403);
  };
}

function getSetting(db, key, fallback = "") {
  try {
    const row = db.prepare("SELECT value FROM room_settings WHERE key=? LIMIT 1").get(key);
    return row?.value ?? fallback;
  } catch {
    return fallback;
  }
}

function setRoomSetting(db, key, value) {
  db.prepare("INSERT OR REPLACE INTO room_settings (key, value) VALUES (?, ?)").run(key, value);
}

function getDashboardSetting(db, key) {
  return db.prepare("SELECT * FROM bot_settings WHERE key=? LIMIT 1").get(key) ?? null;
}

function getDashboardSettingValue(db, key, fallback = "") {
  return getDashboardSetting(db, key)?.value ?? fallback;
}

function upsertDashboardSetting(db, key, value, moduleName, actor, description = "") {
  const old = getDashboardSetting(db, key);
  if (old) {
    db.prepare("UPDATE bot_settings SET value=?, module=?, description=?, updated_by=?, updated_at=CURRENT_TIMESTAMP WHERE key=?").run(
      String(value),
      moduleName || "",
      description,
      actor,
      key,
    );
  } else {
    db.prepare(
      "INSERT INTO bot_settings (key, value, scope, module, description, updated_by, updated_at) VALUES (?, ?, 'global', ?, ?, ?, CURRENT_TIMESTAMP)",
    ).run(key, String(value), moduleName || "", description, actor);
  }
  audit(db, actor, "setting_update", "bot_settings", key, old?.value ?? "", String(value), "");
}

async function fetchRemoteStatus() {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), REMOTE_TIMEOUT_MS);
  try {
    const resp = await fetch(REMOTE_STATUS_URL, {
      signal: ctrl.signal,
      headers: { Accept: "application/json" },
    });
    if (!resp.ok) throw new Error(`Remote returned HTTP ${resp.status}`);
    const data = await resp.json();
    if (AZURACAST_STREAM_URL && data && typeof data === "object") {
      data.radio_url = AZURACAST_STREAM_URL;
    }
    return data;
  } finally {
    clearTimeout(timer);
  }
}

function readLocalRadioStatus(db) {
  const hasYtJobs = tableExists(db, "yt_request_jobs") && columnExists(db, "yt_request_jobs", "status");
  let nowPlaying = null;
  let queue = [];
  let recent = [];
  let counts = {};
  if (hasYtJobs) {
    const desired = ["id", "title", "artist", "username", "user_id", "status", "azura_file_id", "azura_song_id", "filename", "source_type", "started_at", "played_at", "cleaned_at"];
    const orderId = columnExists(db, "yt_request_jobs", "id") ? "id DESC" : "rowid DESC";
    nowPlaying = safeOne(db, "yt_request_jobs", desired, { where: "status='playing'", orderBy: orderId });
    const ph = UPCOMING_REQUEST_STATUSES.map(() => "?").join(",");
    const orderBy = columnExists(db, "yt_request_jobs", "priority")
      ? `priority DESC, ${columnExists(db, "yt_request_jobs", "id") ? "id ASC" : "rowid ASC"}`
      : `${columnExists(db, "yt_request_jobs", "id") ? "id ASC" : "rowid ASC"}`;
    queue = safeRows(db, "yt_request_jobs", desired, {
      where: `status IN (${ph})`,
      params: UPCOMING_REQUEST_STATUSES,
      orderBy,
      limit: "50",
    }).map((row, i) => ({ ...row, artist: row.artist || "", pos: i + 1 }));
    recent = safeRows(db, "yt_request_jobs", desired, {
      where: "status IN ('played','cleaned','skipped')",
      orderBy: orderId,
      limit: "20",
    });
    try {
      counts = Object.fromEntries(
        db
          .prepare("SELECT status, COUNT(*) AS n FROM yt_request_jobs GROUP BY status")
          .all()
          .map((r) => [r.status, r.n]),
      );
    } catch (err) {
      console.error(`[DASHBOARD_DB] radio_counts_failed error=${err.message}`);
    }
  }
  const radioUrl = AZURACAST_STREAM_URL ?? getSetting(db, "dj_radio_url", "");
  return {
    now_playing: nowPlaying,
    queue,
    recent,
    counts,
    terminal_statuses: TERMINAL_REQUEST_STATUSES,
    queue_open: getDashboardSettingValue(db, "requests_enabled", getSetting(db, "requests_enabled", "true")) !== "false",
    radio_url: radioUrl || null,
    updated_at: nowIso(),
  };
}

async function readStatusForPublicEndpoint() {
  if (REMOTE_STATUS_URL) return fetchRemoteStatus();
  const db = await openDb({ readonly: true });
  try {
    return readLocalRadioStatus(db);
  } finally {
    db.close();
  }
}

function rowsOrEmpty(db, table, sql, ...params) {
  try {
    if (!tableExists(db, table)) return [];
    return db.prepare(sql).all(...params);
  } catch (err) {
    console.error(`[DASHBOARD_DB] rowsOrEmpty table=${table} error=${err.message}`);
    return [];
  }
}

function oneOrNull(db, table, sql, ...params) {
  try {
    if (!tableExists(db, table)) return null;
    return db.prepare(sql).get(...params) ?? null;
  } catch (err) {
    console.error(`[DASHBOARD_DB] oneOrNull table=${table} error=${err.message}`);
    return null;
  }
}

const app = express();
app.disable("x-powered-by");
app.use(rateLimit({ key: "dashboard", windowMs: 60_000, max: 180 }));
app.use((_req, res, next) => {
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("Referrer-Policy", "no-referrer");
  res.setHeader("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
  next();
});
app.use(express.json({ limit: "128kb" }));

app.get("/api/healthz", async (_req, res) => {
  const dbExists = fs.existsSync(DB_PATH);
  const body = {
    status: dbExists ? "ok" : "db_missing",
    service: "chilltopia-owner-dashboard",
    app_mode: APP_MODE,
    port: PORT,
    resolved_db_path: DB_PATH,
    db_exists: dbExists,
    migration_status: LAST_MIGRATION_STATUS,
    known_tables: Object.fromEntries(IMPORTANT_TABLES.map((name) => [name, false])),
    remote_status: !!REMOTE_STATUS_URL,
  };
  if (!dbExists) return json(res, body, 503);
  let db = null;
  try {
    db = await openDb({ readonly: true });
    const present = new Set(tableNames(db));
    body.known_tables = Object.fromEntries(IMPORTANT_TABLES.map((name) => [name, present.has(name)]));
    return json(res, body);
  } catch (err) {
    body.status = "db_error";
    body.error = err.message;
    return json(res, body, 503);
  } finally {
    db?.close();
  }
});

app.post("/api/auth/login", rateLimit({ key: "login", windowMs: 60_000, max: 12 }), async (req, res) => {
  let db = null;
  try {
    const username = String(req.body?.username ?? "").trim();
    const password = String(req.body?.password ?? "");
    if (!username || !password) return json(res, { error: "missing_credentials" }, 400);
    db = await openDb();
    ensureDashboardSchema(db);
    bootstrapOwner(db);
    const user = db
      .prepare("SELECT * FROM dashboard_users WHERE lower(username)=lower(?) AND disabled=0 LIMIT 1")
      .get(username);
    if (!user || !verifyPassword(password, user)) {
      audit(db, username, "login_failed", "dashboard_user", username, "", "", req.ip);
      return json(res, { error: "invalid_login" }, 401);
    }
    const token = crypto.randomBytes(32).toString("hex");
    const expiresAt = new Date(Date.now() + SESSION_DAYS * 86400 * 1000).toISOString();
    db.prepare("INSERT INTO dashboard_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)").run(
      hashToken(token),
      user.id,
      expiresAt,
    );
    db.prepare("UPDATE dashboard_users SET last_login_at=CURRENT_TIMESTAMP WHERE id=?").run(user.id);
    audit(db, user.username, "login", "dashboard_user", user.username, "", "success", req.ip);
    setSessionCookie(res, token, expiresAt);
    return json(res, { user: publicUser(db, user), csrf_token: csrfForToken(token) });
  } catch (err) {
    console.error("[DASHBOARD_AUTH] login failed:", err.message);
    return json(res, { error: "login_failed" }, 500);
  } finally {
    db?.close();
  }
});

app.post("/api/auth/logout", requireAuth, (req, res) => {
  const token = parseCookies(req).dashboard_session;
  if (token) req.db.prepare("DELETE FROM dashboard_sessions WHERE token_hash=?").run(hashToken(token));
  audit(req.db, req.user.username, "logout", "dashboard_user", req.user.username, "", "success", req.ip);
  clearSessionCookie(res);
  json(res, { ok: true });
}, closeDb);

app.get("/api/auth/me", requireAuth, (req, res) => {
  json(res, { user: publicUser(req.db, req.user), csrf_token: req.csrfToken });
}, closeDb);

app.get("/api/db/inspect", requireAuth, (req, res) => {
  if (req.user?.role !== "owner") return json(res, { error: "forbidden", permission: "owner" }, 403);
  const tables = tableNames(req.db);
  const inspect = {};
  for (const table of tables) {
    let count = null;
    try {
      count = req.db.prepare(`SELECT COUNT(*) AS n FROM ${sqlIdent(table)}`).get().n;
    } catch (err) {
      count = `error:${err.message}`;
    }
    inspect[table] = {
      columns: tableColumns(req.db, table),
      row_count: count,
      important: IMPORTANT_TABLES.includes(table),
    };
  }
  json(res, {
    resolved_db_path: DB_PATH,
    db_exists: fs.existsSync(DB_PATH),
    migration_status: LAST_MIGRATION_STATUS,
    tables: inspect,
  });
}, closeDb);

app.get("/api/dj/status", async (_req, res) => {
  try {
    res.setHeader("Cache-Control", "no-store");
    json(res, await readStatusForPublicEndpoint());
  } catch (err) {
    console.error("[DASHBOARD] DJ status failed:", err.message);
    json(res, { error: "status_unavailable", message: err.message }, 503);
  }
});

app.get("/api/overview", requireAuth, (req, res) => {
  const db = req.db;
  const bots = safeRows(
    db,
    "bot_instances",
    ["bot_id", "bot_mode", "bot_username", "status", "enabled", "last_heartbeat_at", "last_error", "current_room_id"],
    { orderBy: columnExists(db, "bot_instances", "bot_mode") && columnExists(db, "bot_instances", "bot_username") ? "bot_mode, bot_username" : "" },
  );
  const flags = safeRows(db, "module_flags", ["module", "enabled", "reason", "updated_by", "updated_at"], { orderBy: "module" });
  const onlineBots = bots.filter((b) => String(b.status || "").toLowerCase() === "online").length;
  const roomIds = [...new Set(bots.map((b) => b.current_room_id).filter(Boolean))];
  const commandErrors = rowsOrEmpty(
    db,
    "command_error_logs",
    "SELECT * FROM command_error_logs ORDER BY id DESC LIMIT 10",
  );
  json(res, {
    metrics: {
      online_bots: onlineBots,
      total_bots: bots.length,
      current_room_users: oneOrNull(db, "live_status", "SELECT value FROM live_status WHERE key='room_user_count'")?.value ?? null,
      queue_count: tableExists(db, "yt_request_jobs") && columnExists(db, "yt_request_jobs", "status")
        ? db.prepare(`SELECT COUNT(*) AS n FROM yt_request_jobs WHERE status IN (${UPCOMING_REQUEST_STATUSES.map(() => "?").join(",")})`).get(...UPCOMING_REQUEST_STATUSES).n
        : 0,
      active_games: flags.filter((f) => ["games", "casino"].includes(f.module) && Number(f.enabled) === 1).length,
      staff_online: oneOrNull(db, "live_status", "SELECT value FROM live_status WHERE key='staff_online_count'")?.value ?? null,
      room_ids: roomIds,
    },
    bots,
    module_flags: flags,
    command_errors: commandErrors,
    radio: readLocalRadioStatus(db),
    updated_at: nowIso(),
  });
}, closeDb);

app.get("/api/live", requireAuth, (req, res) => {
  const bots = safeRows(
    req.db,
    "bot_instances",
    ["bot_id", "bot_mode", "bot_username", "status", "enabled", "last_heartbeat_at", "last_error", "current_room_id"],
    { orderBy: columnExists(req.db, "bot_instances", "bot_mode") && columnExists(req.db, "bot_instances", "bot_username") ? "bot_mode, bot_username" : "" },
  );
  const liveRows = safeRows(req.db, "live_status", ["key", "value", "updated_at"], { orderBy: "key" });
  const commands = rowsOrEmpty(
    req.db,
    "command_error_logs",
    "SELECT * FROM command_error_logs ORDER BY id DESC LIMIT 25",
  );
  json(res, { bots, live_status: liveRows, recent_commands_or_errors: commands, updated_at: nowIso() });
}, closeDb);

app.get("/api/settings", requireAuth, requirePermission("emergency_controls"), (req, res) => {
  const roomSettings = rowsOrEmpty(
    req.db,
    "room_settings",
    "SELECT key, value, 'room_settings' AS source FROM room_settings ORDER BY key LIMIT 500",
  );
  const botSettings = req.db.prepare("SELECT *, 'bot_settings' AS source FROM bot_settings ORDER BY module, key").all();
  const flags = req.db.prepare("SELECT * FROM module_flags ORDER BY module").all();
  json(res, { room_settings: roomSettings, bot_settings: botSettings, module_flags: flags });
}, closeDb);

app.put("/api/settings/:key", requireAuth, requirePermission("emergency_controls"), (req, res) => {
  const key = req.params.key.trim();
  const value = String(req.body?.value ?? "");
  const source = String(req.body?.source ?? "bot_settings");
  const moduleName = String(req.body?.module ?? "");
  if (!validSettingKey(key)) return json(res, { error: "bad_key" }, 400);
  if (value.length > 2000) return json(res, { error: "value_too_long" }, 400);
  if (source === "room_settings") {
    const old = getSetting(req.db, key, "");
    setRoomSetting(req.db, key, value);
    audit(req.db, req.user.username, "room_setting_update", "room_settings", key, old, value, req.ip);
  } else {
    upsertDashboardSetting(req.db, key, value, moduleName, req.user.username);
  }
  json(res, { ok: true });
}, closeDb);

app.put("/api/modules/:module", requireAuth, requireAnyPermission("emergency_controls", "manage_radio", "manage_casino", "manage_games"), (req, res) => {
  const moduleName = req.params.module.trim().toLowerCase();
  if (!/^[a-z0-9_-]{1,50}$/.test(moduleName)) return json(res, { error: "bad_module" }, 400);
  const modulePermission = { radio: "manage_radio", casino: "manage_casino", games: "manage_games" }[moduleName];
  if (req.user?.role !== "owner" && !req.permissions?.emergency_controls) {
    if (!modulePermission || !req.permissions?.[modulePermission]) {
      return json(res, { error: "forbidden", permission: modulePermission || "emergency_controls" }, 403);
    }
  }
  const enabled = req.body?.enabled ? 1 : 0;
  const reason = String(req.body?.reason ?? "");
  const old = req.db.prepare("SELECT * FROM module_flags WHERE module=?").get(moduleName) ?? null;
  if (old) {
    req.db
      .prepare("UPDATE module_flags SET enabled=?, reason=?, updated_by=?, updated_at=CURRENT_TIMESTAMP WHERE module=?")
      .run(enabled, reason, req.user.username, moduleName);
  } else {
    req.db
      .prepare("INSERT INTO module_flags (module, enabled, reason, updated_by, updated_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)")
      .run(moduleName, enabled, reason, req.user.username);
  }
  audit(req.db, req.user.username, "module_flag_update", "module_flags", moduleName, old, { enabled, reason }, req.ip);
  json(res, { ok: true });
}, closeDb);

app.get("/api/staff", requireAuth, requirePermission("manage_staff"), (req, res) => {
  const users = req.db
    .prepare("SELECT id, username, role, flags_json, disabled, created_at, last_login_at FROM dashboard_users ORDER BY role, username")
    .all()
    .map((u) => ({ ...u, permissions: readUserPermissions(req.db, u), flags_json: undefined }));
  const botRoles = {
    owners: rowsOrEmpty(req.db, "owner_users", "SELECT username FROM owner_users ORDER BY username"),
    admins: rowsOrEmpty(req.db, "admin_users", "SELECT username FROM admin_users ORDER BY username"),
    moderators: rowsOrEmpty(req.db, "moderators", "SELECT username FROM moderators ORDER BY username"),
    managers: rowsOrEmpty(req.db, "managers", "SELECT username FROM managers ORDER BY username"),
    vip: rowsOrEmpty(req.db, "owned_items", "SELECT user_id FROM owned_items WHERE item_id='vip' LIMIT 250"),
  };
  json(res, { dashboard_users: users, bot_roles: botRoles, permissions: PERMISSIONS });
}, closeDb);

app.post("/api/staff", requireAuth, requirePermission("manage_staff"), (req, res) => {
  const username = String(req.body?.username ?? "").trim();
  const password = String(req.body?.password ?? "");
  const role = req.body?.role === "owner" ? "owner" : "staff";
  const permissions = req.body?.permissions && typeof req.body.permissions === "object" ? req.body.permissions : {};
  if (!username || !password) return json(res, { error: "username_password_required" }, 400);
  const pw = hashPassword(password);
  const info = req.db
    .prepare(
      "INSERT INTO dashboard_users (username, password_hash, salt, iterations, role, flags_json) VALUES (?, ?, ?, ?, ?, ?)",
    )
    .run(username, pw.hash, pw.salt, pw.iterations, role, JSON.stringify(permissions));
  const setPerm = req.db.prepare(
    "INSERT OR REPLACE INTO dashboard_permissions (user_id, permission, allowed) VALUES (?, ?, ?)",
  );
  for (const perm of PERMISSIONS) setPerm.run(info.lastInsertRowid, perm, permissions[perm] ? 1 : 0);
  audit(req.db, req.user.username, "dashboard_user_create", "dashboard_user", username, "", { role, permissions }, req.ip);
  json(res, { ok: true, id: info.lastInsertRowid }, 201);
}, closeDb);

app.put("/api/staff/:id", requireAuth, requirePermission("manage_staff"), (req, res) => {
  const id = Number(req.params.id);
  const old = req.db.prepare("SELECT * FROM dashboard_users WHERE id=?").get(id);
  if (!old) return json(res, { error: "not_found" }, 404);
  const role = req.body?.role === "owner" ? "owner" : "staff";
  const disabled = req.body?.disabled ? 1 : 0;
  const permissions = req.body?.permissions && typeof req.body.permissions === "object" ? req.body.permissions : {};
  req.db.prepare("UPDATE dashboard_users SET role=?, disabled=?, flags_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?").run(
    role,
    disabled,
    JSON.stringify(permissions),
    id,
  );
  const setPerm = req.db.prepare(
    "INSERT OR REPLACE INTO dashboard_permissions (user_id, permission, allowed) VALUES (?, ?, ?)",
  );
  for (const perm of PERMISSIONS) setPerm.run(id, perm, permissions[perm] ? 1 : 0);
  audit(req.db, req.user.username, "dashboard_user_update", "dashboard_user", old.username, old, { role, disabled, permissions }, req.ip);
  json(res, { ok: true });
}, closeDb);

app.delete("/api/staff/:id", requireAuth, requirePermission("manage_staff"), (req, res) => {
  const id = Number(req.params.id);
  const old = req.db.prepare("SELECT * FROM dashboard_users WHERE id=?").get(id);
  if (!old) return json(res, { error: "not_found" }, 404);
  if (old.id === req.user.id) return json(res, { error: "cannot_remove_self" }, 409);
  req.db.prepare("UPDATE dashboard_users SET disabled=1, updated_at=CURRENT_TIMESTAMP WHERE id=?").run(id);
  req.db.prepare("DELETE FROM dashboard_sessions WHERE user_id=?").run(id);
  audit(req.db, req.user.username, "dashboard_user_remove", "dashboard_user", old.username, old, "disabled", req.ip);
  json(res, { ok: true });
}, closeDb);

app.post("/api/staff/bot-role", requireAuth, requirePermission("manage_staff"), (req, res) => {
  const username = String(req.body?.username ?? "").trim().toLowerCase();
  const role = String(req.body?.role ?? "").trim().toLowerCase();
  const action = req.body?.action === "remove" ? "remove" : "add";
  const table = {
    owner: "owner_users",
    admin: "admin_users",
    manager: "managers",
    mod: "moderators",
    moderator: "moderators",
    dj: "dj_users",
    vip: "owned_items",
  }[role];
  if (!username || !table) return json(res, { error: "bad_role" }, 400);
  if (table === "dj_users") {
    req.db.exec("CREATE TABLE IF NOT EXISTS dj_users (username TEXT PRIMARY KEY, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)");
  }
  let existed = null;
  if (table === "owned_items") {
    const userId =
      String(req.body?.user_id ?? "").trim() ||
      oneOrNull(req.db, "users", "SELECT user_id FROM users WHERE lower(username)=lower(?)", username)?.user_id ||
      "";
    if (!userId) return json(res, { error: "vip_requires_known_user_id" }, 400);
    existed = oneOrNull(req.db, "owned_items", "SELECT user_id, item_id FROM owned_items WHERE user_id=? AND item_id='vip'", userId);
    if (action === "add") req.db.prepare("INSERT OR IGNORE INTO owned_items (user_id, item_id, item_type) VALUES (?, 'vip', 'vip')").run(userId);
    else req.db.prepare("DELETE FROM owned_items WHERE user_id=? AND item_id='vip'").run(userId);
  } else {
    existed = oneOrNull(req.db, table, `SELECT username FROM ${table} WHERE username=?`, username);
    if (action === "add") req.db.prepare(`INSERT OR IGNORE INTO ${table} (username) VALUES (?)`).run(username);
    else req.db.prepare(`DELETE FROM ${table} WHERE username=?`).run(username);
  }
  audit(req.db, req.user.username, `bot_role_${action}`, table, username, existed ?? "", { role, username }, req.ip);
  json(res, { ok: true });
}, closeDb);

app.get("/api/radio", requireAuth, requirePermission("manage_radio"), (req, res) => {
  json(res, readLocalRadioStatus(req.db));
}, closeDb);

app.post("/api/radio/requests/:id/remove", requireAuth, requirePermission("manage_radio"), (req, res) => {
  const id = Number(req.params.id);
  if (!tableExists(req.db, "yt_request_jobs")) return json(res, { error: "radio_table_missing" }, 404);
  const row = req.db.prepare("SELECT * FROM yt_request_jobs WHERE id=?").get(id);
  if (!row) return json(res, { error: "not_found" }, 404);
  if (TERMINAL_REQUEST_STATUSES.includes(row.status) || row.status === "playing") {
    return json(res, { error: "not_removable_status", status: row.status }, 409);
  }
  req.db.prepare("UPDATE yt_request_jobs SET status='cancelled', error=? WHERE id=?").run(
    `removed_by_dashboard:${req.user.username}`,
    id,
  );
  audit(req.db, req.user.username, "radio_request_remove", "yt_request_jobs", id, row, "cancelled", req.ip);
  json(res, { ok: true });
}, closeDb);

app.post("/api/radio/clear", requireAuth, requirePermission("manage_radio"), (req, res) => {
  if (!tableExists(req.db, "yt_request_jobs")) return json(res, { error: "radio_table_missing" }, 404);
  const ph = UPCOMING_REQUEST_STATUSES.map(() => "?").join(",");
  const before = req.db.prepare(`SELECT COUNT(*) AS n FROM yt_request_jobs WHERE status IN (${ph})`).get(...UPCOMING_REQUEST_STATUSES).n;
  req.db
    .prepare(`UPDATE yt_request_jobs SET status='cancelled', error=? WHERE status IN (${ph})`)
    .run(`cleared_by_dashboard:${req.user.username}`, ...UPCOMING_REQUEST_STATUSES);
  audit(req.db, req.user.username, "radio_queue_clear", "yt_request_jobs", "active_queue", String(before), "cancelled", req.ip);
  json(res, { ok: true, cancelled: before });
}, closeDb);

app.post("/api/radio/skip", requireAuth, requirePermission("manage_radio"), (req, res) => {
  upsertDashboardSetting(req.db, "radio.skip_requested", nowIso(), "radio", req.user.username, "Bot should consume this flag if dashboard skip support is enabled.");
  json(res, { ok: true, note: "skip request stored in bot_settings; radio bot must consume radio.skip_requested" });
}, closeDb);

app.put("/api/radio/requests-enabled", requireAuth, requirePermission("manage_radio"), (req, res) => {
  const enabled = req.body?.enabled ? "true" : "false";
  upsertDashboardSetting(req.db, "requests_enabled", enabled, "radio", req.user.username, "Dashboard radio request gate consumed by bot modules.");
  json(res, { ok: true, enabled: enabled === "true" });
}, closeDb);

app.get("/api/casino", requireAuth, requirePermission("manage_casino"), (req, res) => {
  const roomKeys = rowsOrEmpty(
    req.db,
    "room_settings",
    "SELECT key, value FROM room_settings WHERE key LIKE 'casino%' OR key LIKE 'bj_%' OR key LIKE 'rbj_%' OR key LIKE 'poker%' OR key LIKE 'daily_%' ORDER BY key",
  );
  const botKeys = req.db
    .prepare("SELECT * FROM bot_settings WHERE module='casino' OR key LIKE 'casino%' OR key LIKE 'bj_%' OR key LIKE 'rbj_%' OR key LIKE 'poker%' OR key LIKE 'daily_%' ORDER BY key")
    .all();
  const seen = new Set();
  const settings = [...botKeys, ...roomKeys.map((r) => ({ ...r, source: "room_settings", module: "casino" }))]
    .filter((row) => {
      if (seen.has(row.key)) return false;
      seen.add(row.key);
      return true;
    });
  json(res, { settings, module_flag: req.db.prepare("SELECT * FROM module_flags WHERE module='casino'").get() ?? null });
}, closeDb);

app.put("/api/casino/:key", requireAuth, requirePermission("manage_casino"), (req, res) => {
  const key = req.params.key.trim();
  const value = String(req.body?.value ?? "");
  if (!validSettingKey(key) || !/^(casino|bj_|rbj_|poker|daily_)/.test(key)) return json(res, { error: "unsupported_casino_key" }, 400);
  if (value.length > 2000) return json(res, { error: "value_too_long" }, 400);
  upsertDashboardSetting(req.db, key, value, "casino", req.user.username, "Dashboard casino/game setting. Bot modules should read from bot_settings before room_settings.");
  json(res, { ok: true });
}, closeDb);

app.get("/api/games", requireAuth, requirePermission("manage_games"), (req, res) => {
  const settings = req.db
    .prepare("SELECT * FROM bot_settings WHERE module='games' OR key LIKE 'games.%' OR key LIKE 'trivia.%' OR key LIKE 'scramble.%' OR key LIKE 'riddle.%' ORDER BY key")
    .all();
  json(res, { settings, module_flag: req.db.prepare("SELECT * FROM module_flags WHERE module='games'").get() ?? null });
}, closeDb);

app.put("/api/games/:key", requireAuth, requirePermission("manage_games"), (req, res) => {
  const key = req.params.key.trim();
  const value = String(req.body?.value ?? "");
  if (!validSettingKey(key) || !/^(games\.|trivia\.|scramble\.|riddle\.)/.test(key)) return json(res, { error: "unsupported_games_key" }, 400);
  if (value.length > 2000) return json(res, { error: "value_too_long" }, 400);
  upsertDashboardSetting(req.db, key, value, "games", req.user.username, "Dashboard game setting. Bot modules should read from bot_settings.");
  json(res, { ok: true });
}, closeDb);

app.get("/api/titles", requireAuth, requirePermission("manage_titles"), (req, res) => {
  const catalog = rowsOrEmpty(req.db, "title_catalog", "SELECT * FROM title_catalog ORDER BY tier, title_id LIMIT 500");
  const assigned = rowsOrEmpty(req.db, "user_titles", "SELECT * FROM user_titles ORDER BY unlocked_at DESC LIMIT 250");
  const dashboardTitles = req.db.prepare("SELECT * FROM player_titles ORDER BY id DESC LIMIT 250").all();
  json(res, { catalog, assigned, dashboard_titles: dashboardTitles });
}, closeDb);

app.post("/api/titles/assign", requireAuth, requirePermission("manage_titles"), (req, res) => {
  const userId = String(req.body?.user_id ?? "").trim();
  const username = String(req.body?.username ?? "").trim();
  const titleId = String(req.body?.title_id ?? "").trim();
  const display = String(req.body?.display ?? titleId).trim();
  const color = String(req.body?.color ?? "").trim();
  if (!titleId || (!userId && !username)) return json(res, { error: "user_and_title_required" }, 400);
  req.db.prepare(
    "INSERT INTO player_titles (user_id, username, title_id, display, color, created_by) VALUES (?, ?, ?, ?, ?, ?)",
  ).run(userId, username, titleId, display, color, req.user.username);
  if (tableExists(req.db, "user_titles") && userId) {
    req.db
      .prepare("INSERT OR IGNORE INTO user_titles (user_id, username, title_id, source) VALUES (?, ?, ?, 'Dashboard')")
      .run(userId, username, titleId);
  }
  audit(req.db, req.user.username, "title_assign", "player_titles", titleId, "", { userId, username, display, color }, req.ip);
  json(res, { ok: true });
}, closeDb);

app.get("/api/logs", requireAuth, requirePermission("view_logs"), (req, res) => {
  const limit = Math.min(Math.max(parseInt(String(req.query.limit || "50"), 10) || 50, 1), 250);
  const offset = Math.min(Math.max(parseInt(String(req.query.offset || "0"), 10) || 0, 0), 10_000);
  const where = [];
  const params = [];
  for (const [field, value] of [
    ["action_type", req.query.action_type],
    ["actor", req.query.user],
    ["target_type", req.query.module],
  ]) {
    const text = String(value || "").trim();
    if (!text) continue;
    where.push(`${field} LIKE ?`);
    params.push(`%${text.slice(0, 80)}%`);
  }
  const sqlWhere = where.length ? `WHERE ${where.join(" AND ")}` : "";
  const auditRows = req.db
    .prepare(`SELECT * FROM audit_logs ${sqlWhere} ORDER BY id DESC LIMIT ? OFFSET ?`)
    .all(...params, limit, offset);
  const adminRows = rowsOrEmpty(req.db, "admin_action_logs", "SELECT * FROM admin_action_logs ORDER BY id DESC LIMIT 100");
  const commandErrors = rowsOrEmpty(req.db, "command_error_logs", "SELECT * FROM command_error_logs ORDER BY id DESC LIMIT 100");
  json(res, { audit_logs: auditRows, admin_action_logs: adminRows, command_error_logs: commandErrors, limit, offset });
}, closeDb);

app.post("/api/emergency", requireAuth, requirePermission("emergency_controls"), (req, res) => {
  const flags = req.body?.flags && typeof req.body.flags === "object" ? req.body.flags : {};
  const allowed = {
    disable_radio_requests: ["radio", "requests_enabled", "false"],
    disable_casino: ["casino", "casino.enabled", "false"],
    disable_games: ["games", "games.enabled", "false"],
  };
  const applied = [];
  for (const [flag, payload] of Object.entries(allowed)) {
    if (!flags[flag]) continue;
    const [moduleName, key, value] = payload;
    upsertDashboardSetting(req.db, key, value, moduleName, req.user.username, "Emergency dashboard flag; bot modules should refuse new work while false.");
    const oldFlag = req.db.prepare("SELECT module FROM module_flags WHERE module=? LIMIT 1").get(moduleName);
    if (oldFlag) {
      req.db
        .prepare("UPDATE module_flags SET enabled=0, reason=?, updated_by=?, updated_at=CURRENT_TIMESTAMP WHERE module=?")
        .run("dashboard emergency", req.user.username, moduleName);
    } else {
      req.db
        .prepare("INSERT INTO module_flags (module, enabled, reason, updated_by, updated_at) VALUES (?, 0, ?, ?, CURRENT_TIMESTAMP)")
        .run(moduleName, "dashboard emergency", req.user.username);
    }
    applied.push(flag);
  }
  if (flags.clear_queue && tableExists(req.db, "yt_request_jobs")) {
    const ph = UPCOMING_REQUEST_STATUSES.map(() => "?").join(",");
    req.db
      .prepare(`UPDATE yt_request_jobs SET status='cancelled', error=? WHERE status IN (${ph})`)
      .run(`emergency_clear_by_dashboard:${req.user.username}`, ...UPCOMING_REQUEST_STATUSES);
    applied.push("clear_queue");
  }
  audit(req.db, req.user.username, "emergency_controls", "module_flags", "bulk", "", applied, req.ip);
  json(res, { ok: true, applied });
}, closeDb);

const DASHBOARD_HTML = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ChillTopia Control</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin:0; background:#111412; color:#edf2ee; }
    button,input,select { font:inherit; }
    .login { max-width:360px; margin:12vh auto; padding:24px; border:1px solid #2a342d; background:#171d19; border-radius:8px; }
    .app { display:grid; grid-template-columns:220px 1fr; min-height:100vh; }
    aside { background:#151a17; border-right:1px solid #27312a; padding:16px; }
    main { padding:18px; }
    nav button { display:block; width:100%; text-align:left; margin:4px 0; padding:10px; border:0; border-radius:6px; background:transparent; color:#cbd5ce; cursor:pointer; }
    nav button.active, nav button:hover { background:#243025; color:#fff; }
    .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:12px; }
    .card { border:1px solid #2a342d; background:#171d19; border-radius:8px; padding:14px; }
    .row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
    input,select { background:#0e120f; color:#fff; border:1px solid #334037; border-radius:6px; padding:8px; max-width:100%; }
    button.primary { background:#3f7e4c; color:#fff; border:0; border-radius:6px; padding:8px 10px; cursor:pointer; }
    button.danger { background:#9b3b3b; color:#fff; border:0; border-radius:6px; padding:8px 10px; cursor:pointer; }
    table { width:100%; border-collapse:collapse; font-size:14px; }
    th,td { border-bottom:1px solid #29342d; padding:7px; text-align:left; vertical-align:top; }
    code,.muted { color:#9daf9f; }
    .err { color:#ffaaa5; }
    .ok { color:#9ff0b2; }
    @media (max-width:760px){ .app{grid-template-columns:1fr} aside{position:static} }
  </style>
</head>
<body>
  <div id="root"></div>
  <script>
    const pages = ["Overview","Radio","Casino","Games","Titles","Staff & Permissions","Settings","Logs","Emergency"];
    let state = { user:null, page:"Overview", data:null, error:"" };
    const $ = (s) => document.querySelector(s);
    async function api(path, opts={}) {
      const res = await fetch(path, { credentials:"include", headers:{ "Content-Type":"application/json" }, ...opts });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || res.statusText);
      return data;
    }
    async function login(ev) {
      ev.preventDefault();
      const form = new FormData(ev.target);
      try { const r = await api("/api/auth/login", { method:"POST", body:JSON.stringify(Object.fromEntries(form)) }); state.user=r.user; state.error=""; await load(); }
      catch(e){ state.error=e.message; render(); }
    }
    async function logout(){ await api("/api/auth/logout", {method:"POST"}).catch(()=>{}); state.user=null; render(); }
    async function init(){ try{ const r=await api("/api/auth/me"); state.user=r.user; await load(); }catch{ render(); } }
    async function load(){
      const map = {
        "Overview":"/api/overview", "Radio":"/api/radio", "Casino":"/api/casino", "Games":"/api/settings",
        "Titles":"/api/titles", "Staff & Permissions":"/api/staff", "Settings":"/api/settings", "Logs":"/api/logs", "Emergency":"/api/settings"
      };
      try { state.data = await api(map[state.page]); state.error=""; } catch(e){ state.data=null; state.error=e.message; }
      render();
    }
    function esc(v){ return String(v ?? "").replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
    function table(rows){
      if (!rows?.length) return '<p class="muted">No data available.</p>';
      const keys = Object.keys(rows[0]).slice(0,8);
      return '<table><thead><tr>'+keys.map(k=>'<th>'+esc(k)+'</th>').join('')+'</tr></thead><tbody>'+
        rows.map(r=>'<tr>'+keys.map(k=>'<td>'+esc(typeof r[k]==="object"?JSON.stringify(r[k]):r[k])+'</td>').join('')+'</tr>').join('')+
        '</tbody></table>';
    }
    function renderLogin(){
      const root = document.getElementById("root");
      root.innerHTML = '<form class="login" onsubmit="login(event)"><h1>ChillTopia Control</h1><p class="muted">Owner/staff login</p>'+
      (state.error?'<p class="err">'+esc(state.error)+'</p>':'')+
      '<p><input name="username" placeholder="Username" autocomplete="username" required /></p>'+
      '<p><input name="password" type="password" placeholder="Password" autocomplete="current-password" required /></p>'+
      '<button class="primary">Log in</button></form>';
    }
    function render(){
      const root = document.getElementById("root");
      if (!state.user) return renderLogin();
      root.innerHTML = '<div class="app"><aside><h2>Control</h2><p class="muted">@'+esc(state.user.username)+' · '+esc(state.user.role)+'</p><nav>'+
        pages.map(p=>'<button class="'+(p===state.page?'active':'')+'" onclick="state.page=\\''+p+'\\';load()">'+p+'</button>').join('')+
        '</nav><button class="primary" onclick="logout()">Log out</button></aside><main><h1>'+state.page+'</h1>'+
        (state.error?'<p class="err">'+esc(state.error)+'</p>':'')+renderPage()+'</main></div>';
    }
    function renderPage(){
      const d = state.data || {};
      if (state.page==="Overview") return '<div class="grid"><div class="card"><h3>Bots</h3>'+table(d.bots)+'</div><div class="card"><h3>Modules</h3>'+table(d.module_flags)+'</div><div class="card"><h3>Radio</h3><pre>'+esc(JSON.stringify(d.radio,null,2))+'</pre></div></div>';
      if (state.page==="Radio") return '<div class="grid"><div class="card"><h3>Now Playing</h3><pre>'+esc(JSON.stringify(d.now_playing,null,2))+'</pre><button class="primary" onclick="api(\\'/api/radio/skip\\',{method:\\'POST\\'}).then(load)">Request Skip</button></div><div class="card"><h3>Queue</h3>'+table(d.queue)+'<p><button class="danger" onclick="api(\\'/api/radio/clear\\',{method:\\'POST\\'}).then(load)">Clear Queue</button></p></div></div>';
      if (state.page==="Casino") return '<div class="card"><h3>Casino/Game Settings</h3>'+table(d.settings)+'</div>';
      if (state.page==="Games") return '<div class="card"><h3>Module Flags and Settings</h3>'+table(d.module_flags)+'<p class="muted">Use Settings for exact key edits.</p></div>';
      if (state.page==="Titles") return '<div class="grid"><div class="card"><h3>Catalog</h3>'+table(d.catalog)+'</div><div class="card"><h3>Assigned</h3>'+table(d.assigned)+'</div></div>';
      if (state.page==="Staff & Permissions") return '<div class="grid"><div class="card"><h3>Dashboard Users</h3>'+table(d.dashboard_users)+'</div><div class="card"><h3>Bot Roles</h3><pre>'+esc(JSON.stringify(d.bot_roles,null,2))+'</pre></div></div>';
      if (state.page==="Settings") return '<div class="grid"><div class="card"><h3>Bot Settings</h3>'+table(d.bot_settings)+'</div><div class="card"><h3>Room Settings</h3>'+table(d.room_settings)+'</div></div>';
      if (state.page==="Logs") return '<div class="grid"><div class="card"><h3>Audit Logs</h3>'+table(d.audit_logs)+'</div><div class="card"><h3>Admin Logs</h3>'+table(d.admin_action_logs)+'</div></div>';
      if (state.page==="Emergency") return '<div class="card"><h3>Emergency Controls</h3><p>These write DB flags only.</p><div class="row">'+
        '<button class="danger" onclick="api(\\'/api/emergency\\',{method:\\'POST\\',body:JSON.stringify({flags:{disable_radio_requests:true}})}).then(load)">Disable Radio Requests</button>'+
        '<button class="danger" onclick="api(\\'/api/emergency\\',{method:\\'POST\\',body:JSON.stringify({flags:{disable_casino:true}})}).then(load)">Disable Casino</button>'+
        '<button class="danger" onclick="api(\\'/api/emergency\\',{method:\\'POST\\',body:JSON.stringify({flags:{disable_games:true}})}).then(load)">Disable Games</button>'+
        '<button class="danger" onclick="api(\\'/api/emergency\\',{method:\\'POST\\',body:JSON.stringify({flags:{clear_queue:true}})}).then(load)">Clear Queue</button></div></div>';
      return '<pre>'+esc(JSON.stringify(d,null,2))+'</pre>';
    }
    init();
  </script>
</body>
</html>`;

app.get("/", (_req, res) => {
  const index = path.join(PUBLIC_DIR, "index.html");
  if (fs.existsSync(index)) return res.sendFile(index);
  res.type("html").send(DASHBOARD_HTML);
});

if (fs.existsSync(PUBLIC_DIR)) {
  app.use(express.static(PUBLIC_DIR));
}

app.get(/.*/, (_req, res) => {
  const index = path.join(PUBLIC_DIR, "index.html");
  if (fs.existsSync(index)) return res.sendFile(index);
  res.type("html").send(DASHBOARD_HTML);
});

(async () => {
  try {
    const db = await openDb();
    ensureDashboardSchema(db);
    bootstrapOwner(db);
    db.close();
  } catch (err) {
    LAST_MIGRATION_STATUS = {
      ok: false,
      ran_at: nowIso(),
      created_tables: [],
      added_columns: [],
      error: err.message,
    };
    console.error("[DASHBOARD_DB] startup schema check failed:", err.message);
    console.error("[DASHBOARD_DB] Set DB_PATH to the shared bot SQLite file before using control APIs.");
  }
  app.listen(PORT, "0.0.0.0", () => {
    console.log(`[DASHBOARD] stage=dashboard_startup mode=owner_staff port=${PORT}`);
    console.log(`[DASHBOARD_CONFIG] resolved_db_path=${DB_PATH} db_exists=${fs.existsSync(DB_PATH)} port=${PORT} app_mode=${APP_MODE}`);
    if (REMOTE_STATUS_URL) console.log(`[DASHBOARD] remote_status_url=${REMOTE_STATUS_URL}`);
    console.log(`[DASHBOARD] Open: http://localhost:${PORT}`);
  });
})();
