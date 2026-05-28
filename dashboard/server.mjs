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
const BOT_ROOT = path.join(__dirname, "..", "artifacts", "highrise-bot");
const BOT_DATA_DIR = path.join(BOT_ROOT, "data");
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

const PERMISSION_REGISTRY = {
  view_dashboard: { group: "System", label: "View Dashboard" },
  manage_radio: { group: "Radio", label: "Manage Radio" },
  manage_casino: { group: "Games", label: "Manage Casino" },
  manage_games: { group: "Games", label: "Manage Games" },
  manage_mining: { group: "Mining", label: "Manage Mining" },
  manage_fishing: { group: "Fishing", label: "Manage Fishing" },
  manage_room: { group: "Room", label: "Manage Room" },
  manage_events: { group: "Events", label: "Manage Events" },
  manage_emotes: { group: "Emotes", label: "Manage Emotes" },
  manage_players: { group: "Players", label: "Manage Players" },
  manage_economy: { group: "Economy", label: "Manage Economy" },
  manage_inventory: { group: "Players", label: "Manage Inventory" },
  manage_moderation: { group: "Players", label: "Manage Moderation" },
  manage_staff: { group: "Staff", label: "Manage Staff" },
  manage_bots: { group: "Bots", label: "Manage Bots" },
  manage_bot_config: { group: "Bots", label: "Manage Bot Config" },
  view_logs: { group: "Logs", label: "View Logs" },
  emergency_controls: { group: "Emergency", label: "Emergency Controls" },
  db_admin: { group: "System", label: "Database Admin" },
};
const PERMISSIONS = Object.keys(PERMISSION_REGISTRY);

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
  seedRole.run("admin", "Broad staff access controlled by permission flags");
  seedRole.run("staff", "Limited staff access controlled by permission flags");
  seedRole.run("moderator", "Moderation-focused staff access");
  seedRole.run("viewer", "Read-only dashboard access");

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

  addColumnIfMissing(db, "bot_command_queue", "result_text", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "bot_command_queue", "error_text", "TEXT NOT NULL DEFAULT ''", migration);

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
  const middleware = (req, res, next) => {
    if (req.user?.role === "owner" || req.permissions?.[permission]) return next();
    return json(res, { error: "forbidden", permission }, 403);
  };
  middleware._security = { type: "permission", permissions: [permission] };
  return middleware;
}

function requireAnyPermission(...permissions) {
  const middleware = (req, res, next) => {
    if (req.user?.role === "owner" || permissions.some((p) => req.permissions?.[p])) return next();
    return json(res, { error: "forbidden", permission: permissions.join("|") }, 403);
  };
  middleware._security = { type: "anyPermission", permissions };
  return middleware;
}

function requireOwner(req, res, next) {
  if (req.user?.role === "owner") return next();
  return json(res, { error: "forbidden", permission: "owner" }, 403);
}
requireAuth._security = { type: "auth" };
requireOwner._security = { type: "owner" };

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
  let failedToday = 0;
  let playedToday = 0;
  let topRequester = null;
  if (hasYtJobs) {
    const desired = ["id", "title", "artist", "username", "user_id", "url", "status", "error", "azura_file_id", "azura_song_id", "filename", "source_type", "payment_type", "coins_charged", "priority", "started_at", "finished_at", "played_at", "cleaned_at"];
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
      where: "status IN ('played','cleaned','skipped','failed','error','cancelled')",
      orderBy: orderId,
      limit: "50",
    });
    try {
      counts = Object.fromEntries(
        db
          .prepare("SELECT status, COUNT(*) AS n FROM yt_request_jobs GROUP BY status")
          .all()
          .map((r) => [r.status, r.n]),
      );
      const dateExpr = columnExists(db, "yt_request_jobs", "played_at") ? "COALESCE(played_at, finished_at, started_at)" : "started_at";
      playedToday = db.prepare(`SELECT COUNT(*) AS n FROM yt_request_jobs WHERE status IN ('played','cleaned') AND date(${dateExpr})=date('now')`).get().n ?? 0;
      failedToday = db.prepare(`SELECT COUNT(*) AS n FROM yt_request_jobs WHERE status IN ('failed','failed_download','error','cancelled') AND date(${dateExpr})=date('now')`).get().n ?? 0;
      topRequester = db.prepare("SELECT username, COUNT(*) AS requests FROM yt_request_jobs WHERE COALESCE(username,'')!='' GROUP BY username ORDER BY requests DESC LIMIT 1").get() ?? null;
    } catch (err) {
      console.error(`[DASHBOARD_DB] radio_counts_failed error=${err.message}`);
    }
  }
  const radioUrl = AZURACAST_STREAM_URL ?? getSetting(db, "dj_radio_url", "");
  const dashboardGate = getDashboardSettingValue(db, "requests_enabled", "true") !== "false";
  const roomGate = getSetting(db, "radio_requests_enabled", "true").toLowerCase() !== "false";
  const djBot = safeOne(db, "bot_instances", ["bot_username","bot_mode","status","last_seen_at","last_heartbeat_at","last_error"], {
    where: "lower(bot_mode)='dj' OR lower(bot_username)='dj_dudu'",
    orderBy: columnExists(db, "bot_instances", "last_seen_at") ? "last_seen_at DESC" : "",
  });
  const pendingCommands = safeRows(db, "bot_command_queue", BOT_COMMAND_QUEUE_COLUMNS, {
    where: "target_bot='dj' AND status IN ('pending','claimed','running','queued')",
    orderBy: columnExists(db, "bot_command_queue", "created_at") ? "created_at DESC" : "",
    limit: "20",
  });
  const recentCommands = safeRows(db, "bot_command_queue", BOT_COMMAND_QUEUE_COLUMNS, {
    where: "target_bot='dj'",
    orderBy: columnExists(db, "bot_command_queue", "created_at") ? "created_at DESC" : "",
    limit: "30",
  });
  const settings = {
    requests_enabled: dashboardGate && roomGate,
    dashboard_requests_enabled: dashboardGate,
    radio_requests_enabled: roomGate,
    max_active_queue: getSetting(db, "radio_max_active_queue", "20"),
    per_user_queue_limit: getSetting(db, "radio_per_user_queue_limit", "3"),
    request_cooldown: getSetting(db, "radio_request_cooldown", "300"),
    request_price: getSetting(db, "radio_request_price", "500"),
    voteskip_threshold: getSetting(db, "radio_voteskip_threshold", "3"),
    skip_on_leave: getSetting(db, "radio_skip_on_leave", "true"),
    refund_on_leave: getSetting(db, "radio_refund_on_leave", "true"),
    admin_ignore_leave: getSetting(db, "radio_admin_ignore_leave", "true"),
  };
  const blocklist = {
    requesters: safeTableRows(db, "request_blocked_requesters", { orderBy: columnExists(db, "request_blocked_requesters", "added_at") ? "added_at DESC" : "", limit: "200" }),
    tracks: safeTableRows(db, "request_blocked_tracks", { orderBy: columnExists(db, "request_blocked_tracks", "added_at") ? "added_at DESC" : "", limit: "200" }),
  };
  const localLibrary = {
    playlists: safeRows(db, "radio_playlists", ["id","user_id","username","name","created_at","updated_at"], { orderBy: columnExists(db, "radio_playlists", "updated_at") ? "updated_at DESC" : "", limit: "100" }),
    songs: safeRows(db, "radio_playlist_songs", ["id","playlist_id","user_id","source_type","title","artist","youtube_url","video_id","azura_song_id","azura_file_id","position","added_at"], { orderBy: columnExists(db, "radio_playlist_songs", "added_at") ? "added_at DESC" : "", limit: "100" }),
    replay_jobs: safeTableRows(db, "local_replay_jobs", { orderBy: columnExists(db, "local_replay_jobs", "created_at") ? "created_at DESC" : "", limit: "100" }),
  };
  const stats = {
    radio_user_stats: safeTableRows(db, "radio_user_stats", { orderBy: columnExists(db, "radio_user_stats", "updated_at") ? "updated_at DESC" : "", limit: "100" }),
    radio_song_stats: safeTableRows(db, "radio_song_stats", { orderBy: columnExists(db, "radio_song_stats", "updated_at") ? "updated_at DESC" : "", limit: "100" }),
    rewards: safeTableRows(db, "radio_reward_log", { orderBy: columnExists(db, "radio_reward_log", "created_at") ? "created_at DESC" : "", limit: "100" }),
    top_requesters: hasYtJobs ? rowsOrEmpty(db, "yt_request_jobs", "SELECT username, COUNT(*) AS requests FROM yt_request_jobs WHERE COALESCE(username,'')!='' GROUP BY username ORDER BY requests DESC LIMIT 20") : [],
  };
  const workerHealth = {
    queue_heartbeat: getSetting(db, "radio_worker_heartbeat_queue", ""),
    playback_heartbeat: getSetting(db, "radio_worker_heartbeat_playback", ""),
    cleanup_requested: getSetting(db, "cleanup_requested", "0"),
  };
  return {
    now_playing: nowPlaying,
    queue,
    recent,
    recently_played: recent,
    counts,
    settings,
    blocklist,
    stats,
    local_library: localLibrary,
    logs: {
      audit: safeRows(db, "audit_logs", ["id","actor","action_type","target_type","target_id","old_value","new_value","ip_address","created_at"], { where: "action_type LIKE 'radio_%' OR target_type='yt_request_jobs' OR target_type='bot_command_queue'", orderBy: columnExists(db, "audit_logs", "created_at") ? "created_at DESC" : "", limit: "100" }),
      command_errors: safeTableRows(db, "command_error_logs", { orderBy: columnExists(db, "command_error_logs", "created_at") ? "created_at DESC" : "", limit: "100" }),
    },
    command_queue: { pending: pendingCommands, recent: recentCommands },
    health: {
      radio_online: String(djBot?.status || "").toLowerCase() === "online",
      dj_bot: djBot,
      queue_size: queue.length,
      in_pipeline: queue.filter((row) => !["ready","queued","submitted"].includes(String(row.status || ""))).length,
      failed_today: failedToday,
      played_today: playedToday,
      top_requester: topRequester,
      worker_health: workerHealth,
      azuracast: { stream_configured: Boolean(radioUrl), status: radioUrl ? "stream_url_configured" : "not_configured" },
    },
    terminal_statuses: TERMINAL_REQUEST_STATUSES,
    queue_open: dashboardGate && roomGate,
    radio_url: radioUrl || null,
    stream: { radio_url: radioUrl || null, azuracast_api_keys_exposed: false },
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

function safeTableRows(db, table, { orderBy = "", limit = "50" } = {}) {
  try {
    if (!tableExists(db, table)) return [];
    const cols = tableColumns(db, table);
    if (!cols.length) return [];
    const sql = `SELECT ${cols.map(sqlIdent).join(", ")} FROM ${sqlIdent(table)}${orderBy ? ` ORDER BY ${orderBy}` : ""}${limit ? ` LIMIT ${limit}` : ""}`;
    return db.prepare(sql).all();
  } catch (err) {
    console.error(`[DASHBOARD_DB] safeTableRows table=${table} error=${err.message}`);
    return [];
  }
}

function enqueueBotCommand(db, { targetBot, actionName, payload, requesterId }) {
  if (!tableExists(db, "bot_command_queue")) throw new Error("bot_command_queue_missing");
  const cols = tableColumns(db, "bot_command_queue");
  const required = ["target_bot", "action", "payload", "status", "requester_id", "created_at"];
  const missing = required.filter((col) => !cols.includes(col));
  if (missing.length) throw new Error(`bot_command_queue_missing_columns:${missing.join(",")}`);
  const insertCols = required;
  const values = [
    targetBot,
    actionName,
    JSON.stringify(payload ?? {}),
    "pending",
    requesterId,
  ];
  const placeholders = ["?", "?", "?", "?", "?", "CURRENT_TIMESTAMP"].join(", ");
  const info = db.prepare(`INSERT INTO bot_command_queue (${insertCols.map(sqlIdent).join(", ")}) VALUES (${placeholders})`).run(...values);
  return { id: info.lastInsertRowid, target_bot: targetBot, action: actionName, status: "pending" };
}

const ROOM_DASHBOARD_TABLES = [
  "room_welcome_seen",
  "room_warnings",
  "room_bans",
  "room_tags",
  "room_tag_members",
  "room_social_logs",
  "room_emote_loops",
  "rotating_announcements",
  "subscriber_announcements",
  "big_announcement_settings",
  "big_announcement_logs",
  "first_find_announce_pending",
  "event_definitions",
  "event_history",
  "event_points",
  "event_settings",
  "event_votes",
  "processed_events",
  "release_announcements",
  "scheduled_events",
  "module_flags",
  "admin_action_logs",
];

function roomTableInfo(db, table, limit = "50") {
  const exists = tableExists(db, table);
  const cols = exists ? tableColumns(db, table) : [];
  const orderBy = cols.includes("created_at") ? "created_at DESC"
    : cols.includes("set_at") ? "set_at DESC"
    : cols.includes("played_at") ? "played_at DESC"
    : cols.includes("starts_at") ? "starts_at ASC"
    : cols.includes("id") ? "id DESC"
    : "";
  return { exists, columns: cols, rows: safeTableRows(db, table, { orderBy, limit }) };
}

function readRoomDashboard(db) {
  const allSettings = safeRows(db, "room_settings", ["key", "value"], { orderBy: "key" });
  const settings = Object.fromEntries(allSettings.map((row) => [row.key, row.value]));
  const tables = Object.fromEntries(ROOM_DASHBOARD_TABLES.map((name) => [name, roomTableInfo(db, name)]));
  const scheduled = tables.scheduled_events?.rows || [];
  const activeEvent = Object.fromEntries((tables.event_settings?.rows || []).map((row) => [row.key, row.value]));
  const hostCommands = safeRows(db, "bot_command_queue", BOT_COMMAND_QUEUE_COLUMNS, {
    where: "target_bot IN ('host','eventhost','ChillTopiaMC') OR action IN ('announce','event_start','event_stop','event_schedule')",
    orderBy: columnExists(db, "bot_command_queue", "created_at") ? "created_at DESC" : "",
    limit: "50",
  });
  const auditRows = safeRows(db, "audit_logs", ["id", "actor", "action_type", "target_type", "target_id", "old_value", "new_value", "ip_address", "created_at"], {
    where: "action_type LIKE 'room_%' OR action_type LIKE 'event_%' OR target_type IN ('room_settings','event_settings','rotating_announcements','bot_command_queue')",
    orderBy: columnExists(db, "audit_logs", "created_at") ? "created_at DESC" : "",
    limit: "100",
  });
  const knownKeys = [
    "room_id",
    "welcome_enabled",
    "welcome_message",
    "welcome_first_time_message",
    "welcome_returning_message",
    "welcome_delay_sec",
    "welcome_vip_extra",
    "maintenance_mode",
    "public_emotes_enabled",
    "social_enabled",
    "self_teleport_enabled",
    "teleport_enabled",
    "requests_enabled",
    "current_vibe",
    "bots_enabled",
    "announcements_enabled",
    "announcement_interval_min",
    "daily_enabled",
    "mining_enabled",
    "fishing_enabled",
    "room_rules",
    "how_to_play",
    "vip_info",
    "staff_list",
  ];
  const known = {};
  for (const row of allSettings) if (knownKeys.includes(row.key)) known[row.key] = row.value;
  const extra = allSettings.filter((row) => !knownKeys.includes(row.key));
  const recentErrors = auditRows.filter((row) => String(row.action_type || "").toLowerCase().includes("error")).slice(0, 10);
  return {
    overview: {
      room_id: settings.room_id || settings.highrise_room_id || "",
      room_users: settings.room_users || settings.current_room_users || "",
      welcome_enabled: settings.welcome_enabled ?? "",
      announcements_enabled: settings.announcements_enabled ?? "",
      active_event: activeEvent.event_active === "1" ? (activeEvent.event_name || "active") : "",
      scheduled_events_count: scheduled.length,
      recent_errors: recentErrors,
    },
    settings,
    room_settings: allSettings,
    known_settings: known,
    extra_settings: extra,
    known_keys: knownKeys,
    tables,
    command_queue: {
      pending: hostCommands.filter((row) => ["pending", "queued", "claimed", "running"].includes(String(row.status || ""))),
      recent: hostCommands,
    },
    audit_logs: auditRows,
  };
}

function upsertKeyValue(db, table, key, value) {
  db.prepare(`INSERT OR REPLACE INTO ${sqlIdent(table)} (key, value) VALUES (?, ?)`).run(key, String(value));
}

function readJsonFileSafe(filePath, fallback = null) {
  try {
    if (!fs.existsSync(filePath)) return fallback;
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (err) {
    console.error(`[DASHBOARD_FILE] json_read_failed path=${filePath} error=${err.message}`);
    return fallback;
  }
}

function tableInfo(db, table, limit = "50") {
  const exists = tableExists(db, table);
  const cols = exists ? tableColumns(db, table) : [];
  const orderBy = cols.includes("updated_at") ? "updated_at DESC"
    : cols.includes("created_at") ? "created_at DESC"
    : cols.includes("timestamp") ? "timestamp DESC"
    : cols.includes("last_given_at") ? "last_given_at DESC"
    : cols.includes("id") ? "id DESC"
    : "";
  return { exists, columns: cols, rows: safeTableRows(db, table, { orderBy, limit }) };
}

function emoteRegistryRows() {
  const rows = [];
  const main = readJsonFileSafe(path.join(BOT_DATA_DIR, "emotes.json"), {});
  for (const [alias, entry] of Object.entries(main || {})) {
    rows.push({
      alias,
      name: entry?.name || alias,
      emote_id: entry?.id || "",
      category: entry?.category || "uncategorized",
      duration: entry?.time ?? "",
      bot: entry?.bot === false ? "no" : "yes",
      player: entry?.player === false ? "no" : "yes",
      enabled: "yes",
      source: "data/emotes.json",
    });
  }
  const custom = readJsonFileSafe(path.join(BOT_DATA_DIR, "custom_emotes.json"), {});
  for (const [kind, group] of Object.entries(custom || {})) {
    if (!group || typeof group !== "object") continue;
    for (const [alias, entry] of Object.entries(group)) {
      if (!entry || typeof entry !== "object") continue;
      rows.push({
        alias,
        name: entry.name || alias,
        emote_id: entry.id || "",
        category: kind,
        duration: entry.time ?? "",
        bot: kind.includes("bot") ? "yes" : "",
        player: kind.includes("player") ? "yes" : "",
        enabled: "yes",
        source: "data/custom_emotes.json",
      });
    }
  }
  return rows;
}

function readEmotesDashboard(db) {
  const settingsRows = safeRows(db, "room_settings", ["key", "value"], { orderBy: "key" });
  const settings = Object.fromEntries(settingsRows.map((row) => [row.key, row.value]));
  const registry = emoteRegistryRows();
  const tables = Object.fromEntries([
    "active_emotes",
    "fav_emotes",
    "custom_emote_packs",
    "custom_loop_sessions",
    "dancefloor_packs",
    "room_emote_loops",
    "sync_relations",
    "room_hearts",
    "room_heart_totals",
    "room_social_logs",
    "audit_logs",
    "bot_command_queue",
  ].map((name) => [name, tableInfo(db, name, name === "bot_command_queue" ? "75" : "100")]));
  const botEmotes = Object.entries(settings)
    .filter(([key]) => key.startsWith("bot_emote_"))
    .map(([key, value]) => ({
      bot: key.replace(/^bot_emote_/, ""),
      current_emote: value || "",
      loop_status: value ? "active" : "stopped",
      persistent: value ? "yes" : "no",
      source: "room_settings",
    }));
  const emoteCommands = safeRows(db, "bot_command_queue", BOT_COMMAND_QUEUE_COLUMNS, {
    where: "action IN ('trigger_emote','stop_emote','botemote_set','botemote_stop','dancefloor_start','dancefloor_stop','dancefloor_clear','dancefloor_status','dancefloor_sequence','dancefloor_random','dancefloor_randomtimed','sync_start','sync_stop','sync_persist')",
    orderBy: columnExists(db, "bot_command_queue", "created_at") ? "created_at DESC" : "",
    limit: "75",
  });
  const emoteAudit = safeRows(db, "audit_logs", ["id", "actor", "action_type", "target_type", "target_id", "old_value", "new_value", "ip_address", "created_at"], {
    where: "action_type LIKE 'emote_%' OR action_type LIKE 'dancefloor_%' OR action_type LIKE 'sync_%' OR target_type IN ('custom_emote_packs','dancefloor_packs','sync_relations','bot_command_queue')",
    orderBy: columnExists(db, "audit_logs", "created_at") ? "created_at DESC" : "",
    limit: "100",
  });
  const activeSync = (tables.sync_relations.rows || []).filter((row) => String(row.is_active) === "1");
  const customPacks = tables.custom_emote_packs.rows || [];
  const dancePacks = tables.dancefloor_packs.rows || [];
  return {
    overview: {
      registry_count: registry.length,
      active_bot_emotes: botEmotes.filter((row) => row.current_emote).length,
      dancefloor_status: settings.dancefloor_active === "true" ? "active" : "stopped",
      sync_active_count: activeSync.length,
      custom_packs_count: customPacks.length + dancePacks.length,
      heart_social_status: settings.social_enabled ?? settings.public_emotes_enabled ?? "",
      recent_errors: emoteAudit.filter((row) => String(row.action_type || "").toLowerCase().includes("error")).slice(0, 10),
    },
    settings,
    room_settings: settingsRows,
    registry,
    bot_emotes: botEmotes,
    custom_packs: customPacks,
    dancefloor: {
      status: settings.dancefloor_active === "true" ? "active" : "stopped",
      box: settings.dancefloor_box || "",
      p1: settings.dancefloor_p1 || "",
      p2: settings.dancefloor_p2 || "",
      emotes: settings.dancefloor_emotes || "",
      sequence_json: settings.dancefloor_sequence_json || "",
      mode: settings.dancefloor_sequence_mode || "",
      packs: dancePacks,
    },
    sync: {
      enabled: settings.sync_dance_enabled ?? "",
      active: activeSync,
      all: tables.sync_relations.rows || [],
    },
    social: {
      heart_settings: Object.entries(settings).filter(([key]) => key.includes("heart") || key.includes("social")).map(([key, value]) => ({ key, value, source: "room_settings" })),
      hearts: tables.room_hearts.rows || [],
      totals: tables.room_heart_totals.rows || [],
      logs: tables.room_social_logs.rows || [],
    },
    tables,
    command_queue: {
      pending: emoteCommands.filter((row) => ["pending", "queued", "claimed", "running"].includes(String(row.status || ""))),
      recent: emoteCommands,
    },
    audit_logs: emoteAudit,
    raw_files: [
      { file: "data/emotes.json", exists: fs.existsSync(path.join(BOT_DATA_DIR, "emotes.json")), rows: registry.filter((r) => r.source === "data/emotes.json").length },
      { file: "data/custom_emotes.json", exists: fs.existsSync(path.join(BOT_DATA_DIR, "custom_emotes.json")), rows: registry.filter((r) => r.source === "data/custom_emotes.json").length },
      { file: "data/highrise_emotes.json", exists: fs.existsSync(path.join(BOT_DATA_DIR, "highrise_emotes.json")), rows: Object.keys(readJsonFileSafe(path.join(BOT_DATA_DIR, "highrise_emotes.json"), {}) || {}).length },
    ],
  };
}

const BOT_COMMAND_QUEUE_COLUMNS = [
  "id",
  "target_bot",
  "action",
  "payload",
  "status",
  "requester_id",
  "created_at",
  "claimed_at",
  "claimed_by",
  "completed_at",
  "result_text",
  "error_text",
];

const ACTIVE_BLACKJACK_COLUMNS = [
  "id",
  "rbj_enabled",
  "min_bet",
  "max_bet",
  "max_players",
  "rbj_action_timer",
  "decks",
  "shuffle_used_percent",
  "win_payout",
  "blackjack_payout",
  "dealer_hits_soft_17",
  "lobby_countdown",
  "rbj_daily_win_limit",
];

function readActiveBlackjackSettings(db) {
  const fallback = {
    source: "rbj_settings",
    bot_username: "AceSinatra",
    rbj_enabled: 1,
    min_bet: 10,
    max_bet: 1000,
    max_players: 6,
    rbj_action_timer: 30,
    decks: 6,
    shuffle_used_percent: 75,
    win_payout: 2.0,
    blackjack_payout: 2.5,
    dealer_hits_soft_17: 1,
    lobby_countdown: 15,
    rbj_daily_win_limit: 5000,
  };
  if (!tableExists(db, "rbj_settings")) return fallback;
  const row = safeOne(db, "rbj_settings", ACTIVE_BLACKJACK_COLUMNS, { where: "id=1" });
  return { ...fallback, ...(row || {}), source: "rbj_settings", bot_username: "AceSinatra" };
}

function normalizeBlackjackSettingsBody(body) {
  const num = (key, fallback = 0) => {
    const raw = body?.[key];
    if (raw === undefined || raw === null || raw === "") return fallback;
    const n = Number(raw);
    if (!Number.isFinite(n)) throw new Error(`${key}_must_be_number`);
    return n;
  };
  const out = {
    rbj_enabled: body?.rbj_enabled === true || body?.rbj_enabled === "true" || body?.rbj_enabled === "1" || body?.rbj_enabled === 1 ? 1 : 0,
    min_bet: Math.trunc(num("min_bet", 10)),
    max_bet: Math.trunc(num("max_bet", 1000)),
    max_players: Math.trunc(num("max_players", 6)),
    rbj_action_timer: Math.trunc(num("rbj_action_timer", 30)),
    decks: Math.trunc(num("decks", 6)),
    shuffle_used_percent: num("shuffle_used_percent", 75),
    win_payout: num("win_payout", 2.0),
    blackjack_payout: num("blackjack_payout", 2.5),
    dealer_hits_soft_17: body?.dealer_hits_soft_17 === true || body?.dealer_hits_soft_17 === "true" || body?.dealer_hits_soft_17 === "1" || body?.dealer_hits_soft_17 === 1 ? 1 : 0,
    lobby_countdown: Math.trunc(num("lobby_countdown", 15)),
  };
  if (body?.rbj_daily_win_limit !== undefined && body?.rbj_daily_win_limit !== "") {
    out.rbj_daily_win_limit = Math.trunc(num("rbj_daily_win_limit", 5000));
  }
  if (out.min_bet < 1) throw new Error("min_bet_too_low");
  if (out.max_bet !== 0 && out.max_bet < out.min_bet) throw new Error("max_bet_less_than_min_bet");
  if (out.max_players < 1 || out.max_players > 20) throw new Error("max_players_out_of_range");
  if (out.rbj_action_timer < 10 || out.rbj_action_timer > 90) throw new Error("action_timer_out_of_range");
  if (out.decks < 1 || out.decks > 8) throw new Error("decks_out_of_range");
  if (out.shuffle_used_percent < 1 || out.shuffle_used_percent > 100) throw new Error("shuffle_percent_out_of_range");
  if (out.win_payout < 1 || out.win_payout > 5) throw new Error("win_payout_out_of_range");
  if (out.blackjack_payout < 1 || out.blackjack_payout > 5) throw new Error("blackjack_payout_out_of_range");
  if (out.lobby_countdown < 5 || out.lobby_countdown > 120) throw new Error("lobby_countdown_out_of_range");
  if (out.rbj_daily_win_limit !== undefined && out.rbj_daily_win_limit < 1) throw new Error("daily_win_limit_out_of_range");
  return out;
}

const ACTIVE_POKER_FIELDS = [
  { field: "enabled", dbKey: "poker_enabled", mirrorKey: "v2_paused", mirrorType: "inverse_toggle", type: "toggle", fallback: "1" },
  { field: "min_buyin", dbKey: "min_buyin", mirrorKey: "v2_min_buyin", type: "int", fallback: 100, min: 1 },
  { field: "max_buyin", dbKey: "max_buyin", mirrorKey: "v2_max_buyin", type: "int", fallback: 50000, min: 1 },
  { field: "max_players", dbKey: "max_players", mirrorKey: "v2_max_players", type: "int", fallback: 6, min: 2, max: 6 },
  { field: "turn_timer", dbKey: "turn_timer", mirrorKey: "v2_turn_seconds", type: "int", fallback: 30, min: 10, max: 120 },
  { field: "small_blind", dbKey: "small_blind", mirrorKey: "v2_small_blind", type: "int", fallback: 50, min: 1 },
  { field: "big_blind", dbKey: "big_blind", mirrorKey: "v2_big_blind", type: "int", fallback: 100, min: 2 },
];

function readPokerSettingsMap(db) {
  if (!tableExists(db, "poker_settings")) return {};
  return Object.fromEntries(
    rowsOrEmpty(db, "poker_settings", "SELECT key, value FROM poker_settings ORDER BY key").map((r) => [r.key, r.value]),
  );
}

function readActivePokerSettings(db) {
  const raw = readPokerSettingsMap(db);
  const out = { source: "poker_settings", bot_username: "ChipSoprano", raw };
  for (const spec of ACTIVE_POKER_FIELDS) {
    const value = raw[spec.dbKey];
    if (spec.type === "toggle") {
      const enabled = value === undefined
        ? ["1", "true", "yes", "on"].includes(String(spec.fallback).toLowerCase())
        : ["1", "true", "yes", "on"].includes(String(value).toLowerCase());
      out[spec.field] = enabled ? 1 : 0;
      out[spec.dbKey] = enabled ? "1" : "0";
    } else {
      out[spec.field] = value === undefined ? spec.fallback : value;
      out[spec.dbKey] = value === undefined ? String(spec.fallback) : value;
    }
  }
  return out;
}

function normalizePokerSettingsBody(body) {
  const has = (key) => Object.prototype.hasOwnProperty.call(body || {}, key);
  const num = (key, fallback = 0) => {
    const raw = body?.[key];
    if (raw === undefined || raw === null || raw === "") return fallback;
    const n = Number(raw);
    if (!Number.isFinite(n)) throw new Error(`${key}_must_be_number`);
    return Math.trunc(n);
  };
  const out = {};
  for (const spec of ACTIVE_POKER_FIELDS) {
    if (!has(spec.field)) continue;
    if (spec.type === "toggle") {
      const enabled = body?.[spec.field] === true || body?.[spec.field] === "true" || body?.[spec.field] === "1" || body?.[spec.field] === 1;
      out[spec.dbKey] = enabled ? "1" : "0";
      if (spec.mirrorKey && spec.mirrorType === "inverse_toggle") out[spec.mirrorKey] = enabled ? "0" : "1";
      continue;
    }
    const value = num(spec.field, spec.fallback);
    if (spec.min !== undefined && value < spec.min) throw new Error(`${spec.field}_too_low`);
    if (spec.max !== undefined && value > spec.max) throw new Error(`${spec.field}_too_high`);
    out[spec.dbKey] = String(value);
    if (spec.mirrorKey) out[spec.mirrorKey] = String(value);
  }
  const minBuyin = Number(out.min_buyin ?? body?.min_buyin);
  const maxBuyin = Number(out.max_buyin ?? body?.max_buyin);
  if (Number.isFinite(minBuyin) && Number.isFinite(maxBuyin) && maxBuyin < minBuyin) throw new Error("max_buyin_less_than_min_buyin");
  const smallBlind = Number(out.small_blind ?? body?.small_blind);
  const bigBlind = Number(out.big_blind ?? body?.big_blind);
  if (Number.isFinite(smallBlind) && Number.isFinite(bigBlind) && bigBlind <= smallBlind) throw new Error("big_blind_must_exceed_small_blind");
  return out;
}

const ACTIVE_MINING_FIELDS = [
  { field: "mining_enabled", label: "Mining Enabled", table: "mining_settings", key: "mining_enabled", type: "bool_true_false", fallback: "true" },
  { field: "base_cooldown_seconds", label: "Mine Cooldown", table: "mining_settings", key: "base_cooldown_seconds", type: "int", fallback: "30", min: 5, max: 3600 },
  { field: "mining_requires_room", label: "Requires Room", table: "mining_settings", key: "mining_requires_room", type: "bool_true_false", fallback: "true" },
  { field: "mining_announce_enabled", label: "Mining Announcements", table: "mining_settings", key: "mining_announce_enabled", type: "bool_10", fallback: "1" },
  { field: "mining_announce_min_rarity", label: "Announce Minimum Rarity", table: "mining_settings", key: "mining_announce_min_rarity", type: "enum", fallback: "legendary", values: ["common", "uncommon", "rare", "epic", "legendary", "mythic", "ultra_rare", "exotic"] },
  { field: "normal_multiplier_cap", label: "Normal Multiplier Cap", table: "mining_settings", key: "normal_multiplier_cap", type: "float", fallback: "3.0", min: 0, max: 100 },
  { field: "blessing_multiplier_cap", label: "Blessing Multiplier Cap", table: "mining_settings", key: "blessing_multiplier_cap", type: "float", fallback: "5.0", min: 0, max: 100 },
  { field: "weights_enabled", label: "Ore Weights Enabled", table: "mining_weight_settings", key: "weights_enabled", type: "bool_10", fallback: "1", optionalTable: true },
  { field: "weight_value_multiplier_scale", label: "Ore Value Weight Scale", table: "mining_weight_settings", key: "weight_value_multiplier_scale", type: "float", fallback: "1.0", min: 0, max: 100, optionalTable: true },
  { field: "weight_lb_mode", label: "Weight Leaderboard Mode", table: "mining_weight_settings", key: "weight_lb_mode", type: "enum", fallback: "best", values: ["best", "all"], optionalTable: true },
  { field: "automine_enabled", label: "Auto Mining Enabled", table: "auto_activity_settings", key: "automine_enabled", type: "bool_10", fallback: "1", optionalTable: true },
];

const ACTIVE_FISHING_FIELDS = [
  { field: "autofish_enabled", label: "AutoFish Enabled", table: "auto_activity_settings", key: "autofish_enabled", type: "bool_10", fallback: "1", min: 0, max: 1 },
  { field: "fish_base_duration", label: "Base Auto Time", table: "auto_activity_settings", key: "fish_base_duration", type: "int", fallback: "5", min: 1, max: 1440 },
  { field: "fish_base_interval", label: "Base Cast Interval", table: "auto_activity_settings", key: "fish_base_interval", type: "int", fallback: "12", min: 3, max: 120 },
  { field: "fish_base_luck", label: "Base Luck", table: "auto_activity_settings", key: "fish_base_luck", type: "int", fallback: "1", min: 0, max: 50 },
  { field: "fish_vip_luck", label: "VIP Luck Bonus", table: "auto_activity_settings", key: "fish_vip_luck", type: "int", fallback: "2", min: 0, max: 20 },
  { field: "fish_vip_duration", label: "VIP Duration Bonus", table: "auto_activity_settings", key: "fish_vip_duration", type: "int", fallback: "10", min: 0, max: 120 },
  { field: "fish_vip_speed", label: "VIP Speed Bonus", table: "auto_activity_settings", key: "fish_vip_speed", type: "int", fallback: "1", min: 0, max: 20 },
  { field: "fish_min_interval", label: "Minimum Cast Interval", table: "auto_activity_settings", key: "fish_min_interval", type: "int", fallback: "5", min: 2, max: 60 },
  { field: "autofish_duration_minutes", label: "AutoFish Session Duration", table: "auto_activity_settings", key: "autofish_duration_minutes", type: "int", fallback: "30", min: 5, max: 120 },
  { field: "autofish_max_attempts", label: "AutoFish Max Attempts", table: "auto_activity_settings", key: "autofish_max_attempts", type: "int", fallback: "30", min: 5, max: 200 },
  { field: "autofish_daily_cap_minutes", label: "AutoFish Daily Cap", table: "auto_activity_settings", key: "autofish_daily_cap_minutes", type: "int", fallback: "120", min: 30, max: 480 },
];

function readKeyValueMap(db, table) {
  if (!tableExists(db, table)) return {};
  return Object.fromEntries(
    rowsOrEmpty(db, table, `SELECT key, value FROM ${sqlIdent(table)} ORDER BY key`).map((r) => [r.key, r.value]),
  );
}

function readActiveMiningSettings(db) {
  const tableMaps = {};
  for (const table of [...new Set(ACTIVE_MINING_FIELDS.map((f) => f.table))]) tableMaps[table] = readKeyValueMap(db, table);
  const values = { source: "mining_settings + mining_weight_settings + auto_activity_settings" };
  for (const spec of ACTIVE_MINING_FIELDS) {
    const exists = tableExists(db, spec.table);
    const raw = tableMaps[spec.table]?.[spec.key];
    values[spec.field] = raw ?? spec.fallback;
    values[`${spec.field}_source`] = `${spec.table}.${spec.key}`;
    values[`${spec.field}_table_exists`] = exists;
  }
  return values;
}

function normalizeMiningSettingsBody(db, body) {
  const out = [];
  const boolish = (v) => v === true || v === "true" || v === "1" || v === 1 || v === "on";
  const has = (key) => Object.prototype.hasOwnProperty.call(body || {}, key);
  for (const spec of ACTIVE_MINING_FIELDS) {
    if (!has(spec.field)) continue;
    if (!tableExists(db, spec.table)) {
      if (spec.optionalTable) continue;
      throw new Error(`${spec.table}_missing`);
    }
    let value;
    if (spec.type === "bool_true_false") {
      value = boolish(body[spec.field]) ? "true" : "false";
    } else if (spec.type === "bool_10") {
      value = boolish(body[spec.field]) ? "1" : "0";
    } else if (spec.type === "int") {
      const n = Number(body[spec.field]);
      if (!Number.isFinite(n)) throw new Error(`${spec.field}_must_be_number`);
      const i = Math.trunc(n);
      if (spec.min !== undefined && i < spec.min) throw new Error(`${spec.field}_too_low`);
      if (spec.max !== undefined && i > spec.max) throw new Error(`${spec.field}_too_high`);
      value = String(i);
    } else if (spec.type === "float") {
      const n = Number(body[spec.field]);
      if (!Number.isFinite(n)) throw new Error(`${spec.field}_must_be_number`);
      if (spec.min !== undefined && n < spec.min) throw new Error(`${spec.field}_too_low`);
      if (spec.max !== undefined && n > spec.max) throw new Error(`${spec.field}_too_high`);
      value = String(Math.round(n * 10000) / 10000);
    } else if (spec.type === "enum") {
      value = String(body[spec.field] ?? "").trim().toLowerCase();
      if (!spec.values.includes(value)) throw new Error(`${spec.field}_invalid`);
    } else {
      continue;
    }
    out.push({ table: spec.table, key: spec.key, value, field: spec.field });
  }
  return out;
}

function readActiveFishingSettings(db) {
  const tableMaps = {};
  for (const table of [...new Set(ACTIVE_FISHING_FIELDS.map((f) => f.table))]) tableMaps[table] = readKeyValueMap(db, table);
  const values = { source: "auto_activity_settings" };
  for (const spec of ACTIVE_FISHING_FIELDS) {
    const exists = tableExists(db, spec.table);
    const raw = tableMaps[spec.table]?.[spec.key];
    values[spec.field] = raw ?? spec.fallback;
    values[`${spec.field}_source`] = `${spec.table}.${spec.key}`;
    values[`${spec.field}_table_exists`] = exists;
  }
  return values;
}

function normalizeFishingSettingsBody(db, body) {
  const out = [];
  const boolish = (v) => v === true || v === "true" || v === "1" || v === 1 || v === "on";
  const has = (key) => Object.prototype.hasOwnProperty.call(body || {}, key);
  for (const spec of ACTIVE_FISHING_FIELDS) {
    if (!has(spec.field)) continue;
    if (!tableExists(db, spec.table)) throw new Error(`${spec.table}_missing`);
    let value;
    if (spec.type === "bool_10") {
      value = boolish(body[spec.field]) ? "1" : "0";
    } else if (spec.type === "int") {
      const n = Number(body[spec.field]);
      if (!Number.isFinite(n)) throw new Error(`${spec.field}_must_be_number`);
      const i = Math.trunc(n);
      if (spec.min !== undefined && i < spec.min) throw new Error(`${spec.field}_too_low`);
      if (spec.max !== undefined && i > spec.max) throw new Error(`${spec.field}_too_high`);
      value = String(i);
    } else {
      continue;
    }
    out.push({ table: spec.table, key: spec.key, value, field: spec.field });
  }
  return out;
}

const MINING_RARITY_PROBS = {
  common: 65.00,
  uncommon: 22.00,
  rare: 10.00,
  epic: 2.75,
  legendary: 0.23,
  mythic: 0.015,
  ultra_rare: 0.004,
  prismatic: 0.0008,
  exotic: 0.0002,
};

const PICKAXE_CATALOG = [
  ["pickaxe_lv1", "Worn Pickaxe", 1, 30],
  ["pickaxe_lv2", "Copper Pickaxe", 2, 55],
  ["pickaxe_lv3", "Iron Pickaxe", 3, 50],
  ["pickaxe_lv4", "Steel Pickaxe", 4, 45],
  ["pickaxe_lv5", "Silver Pickaxe", 5, 40],
  ["pickaxe_lv6", "Gold Pickaxe", 6, 35],
  ["pickaxe_lv7", "Tungsten Pickaxe", 7, 30],
  ["pickaxe_lv8", "Platinum Pickaxe", 8, 25],
  ["pickaxe_lv9", "Titanium Pickaxe", 9, 20],
  ["pickaxe_lv10", "Master Pickaxe", 10, 15],
].map(([item_id, name, required_level, cooldown_seconds]) => ({
  item_id,
  name,
  display_name: name,
  required_level,
  cooldown_seconds,
  enabled: 1,
  source: "modules/mining.py PICKAXE_NAMES + COOLDOWNS",
  writable: false,
}));

const FISHING_MODULE_PATH = path.join(__dirname, "..", "artifacts", "highrise-bot", "modules", "fishing.py");

function parsePythonLiteralObject(text) {
  const normalized = text
    .replace(/\bTrue\b/g, "true")
    .replace(/\bFalse\b/g, "false")
    .replace(/\bNone\b/g, "null");
  return JSON.parse(normalized);
}

function readFishingCodeCatalog() {
  try {
    const src = fs.readFileSync(FISHING_MODULE_PATH, "utf8");
    const fish = [];
    for (const match of src.matchAll(/\{[^\n]*"fish_id"\s*:[^\n]*\}/g)) {
      try { fish.push({ ...parsePythonLiteralObject(match[0]), source: "modules/fishing.py FISH_CATALOG", writable: false }); } catch {}
    }
    const rods = [];
    const rodBlock = src.match(/FISHING_RODS:\s*dict\[str,\s*dict\]\s*=\s*\{([\s\S]*?)\n\}/);
    if (rodBlock) {
      for (const match of rodBlock[1].matchAll(/"([^"]+)":\s*(\{[^\n]+\})/g)) {
        try {
          const info = parsePythonLiteralObject(match[2]);
          rods.push({
            item_id: match[1].toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, ""),
            name: match[1],
            display_name: match[1],
            ...info,
            enabled: 1,
            source: "modules/fishing.py FISHING_RODS",
            writable: false,
          });
        } catch {}
      }
    }
    return { fish, rods, source: "code" };
  } catch (err) {
    return { fish: [], rods: [], source: "missing", error: err.message };
  }
}

function miningItemRows(db, includeDisabled = true) {
  const cols = ["item_id", "name", "emoji", "rarity", "item_type", "sell_value", "drop_enabled", "created_at"];
  return safeRows(db, "mining_items", cols, {
    where: includeDisabled ? "" : "drop_enabled=1",
    orderBy: "rarity, sell_value, name",
    limit: "1000",
  });
}

function calculateMiningDropRows(db) {
  const items = miningItemRows(db, false);
  const byRarity = items.reduce((acc, item) => {
    const rarity = item.rarity || "common";
    acc[rarity] = acc[rarity] || [];
    acc[rarity].push(item);
    return acc;
  }, {});
  return items.map((item) => {
    const rarity = item.rarity || "common";
    const rarityChance = Number(MINING_RARITY_PROBS[rarity] || 0);
    const peers = Math.max(1, byRarity[rarity]?.length || 1);
    const chance = rarityChance / peers;
    return {
      item_id: item.item_id,
      ore: item.name,
      rarity,
      weight: chance,
      chance_percent: chance,
      rarity_chance_percent: rarityChance,
      enabled: item.drop_enabled,
      event_only: 0,
      source: "modules/mining.py RARITIES split across enabled mining_items",
      writable: false,
    };
  });
}

function calculateFishDropRows() {
  const { fish } = readFishingCodeCatalog();
  const total = fish.reduce((sum, row) => sum + Number(row.drop_weight || 0), 0);
  return fish.map((row) => ({
    fish_id: row.fish_id,
    fish: row.name,
    rarity: row.rarity,
    weight: row.drop_weight,
    chance_percent: total ? (Number(row.drop_weight || 0) / total) * 100 : 0,
    enabled: 1,
    event_only: 0,
    source: "modules/fishing.py FISH_CATALOG drop_weight",
    writable: false,
  }));
}

function countTable(db, table, where = "") {
  if (!tableExists(db, table)) return 0;
  try {
    return db.prepare(`SELECT COUNT(*) AS count FROM ${sqlIdent(table)}${where ? ` WHERE ${where}` : ""}`).get()?.count ?? 0;
  } catch {
    return 0;
  }
}

function miningOverview(db) {
  const todayWhere = columnExists(db, "mining_payout_logs", "mined_at") ? "date(mined_at)=date('now')" : "";
  const rareWhere = columnExists(db, "mining_payout_logs", "rarity") ? "rarity IN ('legendary','mythic','ultra_rare','prismatic','exotic')" : "";
  return {
    settings: readActiveMiningSettings(db),
    stats: {
      mining_enabled: readActiveMiningSettings(db).mining_enabled,
      total_miners: countTable(db, "mining_players"),
      total_ores_mined: oneOrNull(db, "mining_players", "SELECT COALESCE(SUM(total_ores), 0) AS total FROM mining_players")?.total ?? 0,
      todays_mining: todayWhere ? countTable(db, "mining_payout_logs", todayWhere) : countTable(db, "mining_logs", "date(timestamp)=date('now')"),
      rare_finds: rareWhere ? countTable(db, "mining_payout_logs", rareWhere) : 0,
      gold_rain_enabled: readKeyValueMap(db, "gold_rain_settings").gold_rain_enabled ?? readKeyValueMap(db, "gold_settings").gold_rain_enabled ?? null,
    },
    table_status: Object.fromEntries(["mining_settings", "mining_weight_settings", "mining_players", "mining_inventory", "mining_items", "mining_logs", "mining_events", "mining_payout_logs", "forced_mining_drops", "ore_weight_records", "gold_settings", "gold_rain_settings", "gold_tip_events"].map((t) => [t, tableExists(db, t)])),
    raw: {
      mining_settings: Object.entries(readKeyValueMap(db, "mining_settings")).map(([key, value]) => ({ key, value })),
      mining_weight_settings: Object.entries(readKeyValueMap(db, "mining_weight_settings")).map(([key, value]) => ({ key, value })),
      auto_activity_settings: Object.entries(readKeyValueMap(db, "auto_activity_settings")).filter(([key]) => key.startsWith("mine") || key.startsWith("automine")).map(([key, value]) => ({ key, value })),
    },
  };
}

function fishingOverview(db) {
  const codeCatalog = readFishingCodeCatalog();
  return {
    settings: readActiveFishingSettings(db),
    stats: {
      fishing_enabled: readActiveFishingSettings(db).autofish_enabled,
      total_fishers: countTable(db, "fish_profiles"),
      total_fish_caught: countTable(db, "fish_catch_records"),
      todays_catches: countTable(db, "fish_catch_records", "date(caught_at)=date('now')"),
      biggest_catch: oneOrNull(db, "fish_catch_records", "SELECT fish_name, username, weight, final_value FROM fish_catch_records ORDER BY weight DESC LIMIT 1"),
      auto_sell_rows: countTable(db, "fish_auto_sell_settings"),
    },
    catalog: { fish_count: codeCatalog.fish.length, rod_count: codeCatalog.rods.length, source: codeCatalog.source },
    table_status: Object.fromEntries(["auto_activity_settings", "fish_profiles", "fish_catch_records", "fish_inventory", "fish_auto_sell_settings", "forced_fishing_drops", "player_rods", "owned_items"].map((t) => [t, tableExists(db, t)])),
    raw: {
      auto_activity_settings: Object.entries(readKeyValueMap(db, "auto_activity_settings")).filter(([key]) => key.startsWith("fish_") || key.startsWith("autofish")).map(([key, value]) => ({ key, value })),
      room_settings: Object.entries(readKeyValueMap(db, "room_settings")).filter(([key]) => key.startsWith("fishing_") || key.startsWith("fish_weight_")).map(([key, value]) => ({ key, value })),
    },
  };
}

function unverifiedSchema(res, message) {
  return json(res, { error: "unverified_schema", message }, 400);
}

function validCatalogId(id) {
  return /^[a-zA-Z0-9_.:-]{1,80}$/.test(String(id || ""));
}

function sqlString(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

const KEY_VALUE_SETTING_TABLES = new Set([
  "poker_settings",
  "bank_settings",
  "economy_settings",
  "mining_settings",
  "mining_weight_settings",
  "auto_activity_settings",
  "gold_settings",
  "gold_rain_settings",
  "room_settings",
  "bot_settings",
  "event_settings",
]);

const auditRow = ({
  status = "BROKEN",
  dashboardConnected = false,
  writeEndpoint = "",
  readSource,
  ...row
}) => ({
  status,
  dashboard_connected: dashboardConnected,
  write_endpoint: writeEndpoint,
  read_source: readSource || (
    KEY_VALUE_SETTING_TABLES.has(row.db_table)
      ? `SELECT value FROM ${row.db_table} WHERE key='${row.db_key_or_column}'`
      : `SELECT ${row.db_key_or_column} FROM ${row.db_table} WHERE id=1`
  ),
  ...row,
});

const SETTINGS_AUDIT_DEFINITIONS = [
  ...[
    ["!rbj settings / !bjadmin settings", "Enabled", "rbj_enabled"],
    ["!setrbjminbet", "Min Bet", "min_bet"],
    ["!setrbjmaxbet", "Max Bet", "max_bet"],
    ["No in-room setter found", "Max Players", "max_players"],
    ["!setrbjactiontimer", "Action Timer", "rbj_action_timer"],
    ["!setrbjdecks", "Number of Decks", "decks"],
    ["!setrbjshuffle", "Shuffle Used Percent", "shuffle_used_percent"],
    ["!setrbjwinpayout", "Win Payout", "win_payout"],
    ["!setrbjblackjackpayout", "Blackjack Payout", "blackjack_payout"],
    ["!bj setsoft17 hit|stand", "Dealer Hits Soft 17", "dealer_hits_soft_17"],
    ["!setrbjcountdown", "Lobby Countdown", "lobby_countdown"],
  ].map(([command, displayName, key]) => auditRow({
    module: "realistic_blackjack",
    command,
    display_name: displayName,
    dashboard_page: "Casino",
    dashboard_section: "Blackjack Settings — AceSinatra",
    db_table: "rbj_settings",
    db_key_or_column: key,
    writeEndpoint: "PUT /api/casino/blackjack-settings",
    dashboardConnected: true,
    status: "CONNECTED",
    notes: key === "max_players"
      ? "Active code reads rbj_settings.max_players, but no in-room setter was found."
      : "Active blackjack setting. AceSinatra path reads database.get_rbj_settings().",
  })),
  auditRow({
    module: "realistic_blackjack",
    command: "!setrbjdailywinlimit",
    display_name: "Daily Win Limit",
    dashboard_page: "Casino",
    dashboard_section: "Blackjack Settings — AceSinatra",
    db_table: "rbj_settings",
    db_key_or_column: "rbj_daily_win_limit",
    writeEndpoint: "PUT /api/casino/blackjack-settings",
    dashboardConnected: true,
    status: "CONNECTED",
    notes: "Active RBJ limit setting; currently included in API payload and audit source.",
  }),
  ...["min_bet", "max_bet", "max_players", "bj_action_timer", "decks", "shuffle_used_percent", "win_payout", "blackjack_payout"].map((key) => auditRow({
    module: "blackjack",
    command: "!setbj* / /bj settings",
    display_name: key,
    dashboard_page: "Casino",
    dashboard_section: "Advanced / Legacy Blackjack",
    db_table: "bj_settings",
    db_key_or_column: key,
    status: "LEGACY",
    notes: "Legacy standard blackjack source. Not used for the visible AceSinatra/RBJ dashboard card.",
  })),
  ...[
    ["!join buy-in min / !poker minbuyin", "Min Buy-In", "min_buyin"],
    ["!join buy-in max / !poker maxbuyin", "Max Buy-In", "max_buyin"],
    ["!poker maxplayers", "Max Players", "max_players"],
    ["!poker blinds", "Small Blind", "small_blind"],
    ["!poker blinds", "Big Blind", "big_blind"],
    ["!poker timer", "Turn Timer", "turn_timer"],
    ["!poker on|off", "Enabled", "poker_enabled"],
  ].map(([command, displayName, key]) => auditRow({
    module: "poker_v2",
    command,
    display_name: displayName,
    dashboard_page: "Casino",
    dashboard_section: "Poker Settings — ChipSoprano",
    db_table: "poker_settings",
    db_key_or_column: key,
    writeEndpoint: "PUT /api/casino/poker-settings",
    dashboardConnected: true,
    status: "CONNECTED",
    notes: key === "max_buyin"
      ? "The !join rejection uses _T.max_buyin. Dashboard writes poker_settings.max_buyin and mirrors v2_max_buyin for Poker V2 restart compatibility."
      : "Visible dashboard source for active poker controls; V2 mirror keys are kept in Advanced for compatibility.",
  })),
  ...[
    ["Poker V2 mirror", "Min Buy-In Mirror", "v2_min_buyin"],
    ["Poker V2 mirror", "Max Buy-In Mirror", "v2_max_buyin"],
    ["Poker V2 mirror", "Max Players Mirror", "v2_max_players"],
    ["Poker V2 mirror", "Small Blind Mirror", "v2_small_blind"],
    ["Poker V2 mirror", "Big Blind Mirror", "v2_big_blind"],
    ["Poker V2 mirror", "Turn Timer Mirror", "v2_turn_seconds"],
    ["Poker V2 mirror", "Paused Mirror", "v2_paused"],
  ].map(([command, displayName, key]) => auditRow({
    module: "poker_v2",
    command,
    display_name: displayName,
    dashboard_page: "Casino",
    dashboard_section: "Advanced / Poker Raw Settings",
    db_table: "poker_settings",
    db_key_or_column: key,
    status: "LEGACY",
    notes: "Compatibility mirror key. Not the visible dashboard source.",
  })),
  ...[
    ["setpokerplayers", "Min Players", "min_players"],
    ["setpokerlobbytimer", "Lobby Countdown", "lobby_countdown"],
    ["setpokerante", "Ante", "ante"],
    ["!poker allin on|off", "All-In Enabled", "allin_enabled"],
    ["!poker rebuy on|off", "Rebuy Enabled", "rebuy_enabled"],
    ["!poker autostart on|off", "Auto Start Next Hand", "auto_start_next_hand"],
    ["setpokernexthandtimer", "Next Hand Delay", "next_hand_delay"],
  ].map(([command, displayName, key]) => auditRow({
    module: "poker",
    command,
    display_name: displayName,
    dashboard_page: "Casino",
    dashboard_section: "Advanced / Poker Raw Settings",
    db_table: "poker_settings",
    db_key_or_column: key,
    status: "LEGACY",
    notes: "Legacy poker.py key. Current live player actions route through poker_v2, which does not load this key.",
  })),
  ...[
    ["!setdailycoins", "Daily Coins", "economy_settings", "daily_coins"],
    ["!setgamereward trivia", "Trivia Reward", "economy_settings", "trivia_reward"],
    ["!setgamereward scramble", "Scramble Reward", "economy_settings", "scramble_reward"],
    ["!setgamereward riddle", "Riddle Reward", "economy_settings", "riddle_reward"],
    ["!setmaxbalance", "Max Balance", "economy_settings", "max_balance"],
    ["!setminsend", "Minimum Send", "bank_settings", "min_send_amount"],
    ["!setmaxsend", "Maximum Send", "bank_settings", "max_send_amount"],
    ["!setsendlimit", "Daily Send Limit", "bank_settings", "daily_send_limit"],
    ["!setsendtax", "Transfer Tax", "bank_settings", "send_tax_percent"],
    ["!setnewaccountdays", "New Account Days", "bank_settings", "new_account_days"],
    ["!setminlevelsend", "Minimum Send Level", "bank_settings", "min_level_to_send"],
    ["!setmintotalearned", "Minimum Total Earned", "bank_settings", "min_total_earned_to_send"],
    ["!setmindailyclaims", "Minimum Daily Claims", "bank_settings", "min_daily_claim_days_to_send"],
    ["!sethighriskblocks", "High Risk Blocks", "bank_settings", "high_risk_blocks"],
  ].map(([command, displayName, table, key]) => auditRow({
    module: table === "bank_settings" ? "bank" : "economy",
    command,
    display_name: displayName,
    dashboard_page: "Economy & Rewards",
    dashboard_section: table === "bank_settings" ? "Bank Settings" : "Economy Settings",
    db_table: table,
    db_key_or_column: key,
    status: "BROKEN",
    notes: "Verified command source. Keep dashboard writes hidden until an exact endpoint writes this key.",
  })),
  ...[
    ["!setminecooldown", "Mine Cooldown", "mining_settings", "base_cooldown_seconds"],
    ["!mining on|off", "Mining Enabled", "mining_settings", "mining_enabled"],
    ["!mineconfig", "Requires Room", "mining_settings", "mining_requires_room"],
    ["!setmineannounce", "Mining Announce Enabled", "mining_settings", "mining_announce_enabled"],
    ["!setmineannounce", "Mining Announce Rarity", "mining_settings", "mining_announce_min_rarity"],
    ["ore value multiplier cap", "Normal Multiplier Cap", "mining_settings", "normal_multiplier_cap"],
    ["ore value multiplier cap", "Blessing Multiplier Cap", "mining_settings", "blessing_multiplier_cap"],
    ["!setmineweights", "Weights Enabled", "mining_weight_settings", "weights_enabled"],
    ["!setweightlbmode", "Weight Leaderboard Mode", "mining_weight_settings", "weight_lb_mode"],
    ["!setweightscale", "Weight Value Scale", "mining_weight_settings", "weight_value_multiplier_scale"],
    ["!setrarityweightrange", "Rarity Weight Ranges", "mining_weight_settings", "rarity_weight_ranges_json"],
    ["!automine", "Auto Mining Enabled", "auto_activity_settings", "automine_enabled"],
  ].map(([command, displayName, table, key]) => auditRow({
    module: "mining",
    command,
    display_name: displayName,
    dashboard_page: "Economy & Rewards",
    dashboard_section: "Mining Settings",
    db_table: table,
    db_key_or_column: key,
    writeEndpoint: key === "rarity_weight_ranges_json" ? "" : "PUT /api/mining-settings",
    dashboardConnected: key !== "rarity_weight_ranges_json",
    status: key === "rarity_weight_ranges_json" ? "LEGACY" : "CONNECTED",
    notes: key === "rarity_weight_ranges_json"
      ? "Verified source, but kept read-only because it is edited through a structured rarity range command."
      : "Verified mining command/runtime source and connected to the dashboard mining settings endpoint.",
  })),
  auditRow({
    module: "mining",
    command: "!orelist / !oreprices / !mine",
    display_name: "Ore Catalog",
    dashboard_page: "Mining",
    dashboard_section: "Ores",
    db_table: "mining_items",
    db_key_or_column: "item_id,name,rarity,sell_value,drop_enabled",
    readSource: "SELECT * FROM mining_items ORDER BY rarity, sell_value, name",
    writeEndpoint: "POST/PUT/DELETE /api/mining/ores",
    dashboardConnected: true,
    status: "CONNECTED",
    notes: "Active mining runtime reads mining_items through database.get_all_mining_items(). Dashboard writes this table and soft-disables with drop_enabled=0.",
  }),
  auditRow({
    module: "mining",
    command: "!minechances / !orechances",
    display_name: "Ore Drop Chances",
    dashboard_page: "Mining",
    dashboard_section: "Drop Chances",
    db_table: "modules/mining.py",
    db_key_or_column: "RARITIES",
    readSource: "modules/mining.py RARITIES split across enabled mining_items",
    status: "READ ONLY",
    notes: "Active drop chances are code constants, not DB weights. Dashboard calculates percentages but does not pretend writes are connected.",
  }),
  auditRow({
    module: "mining",
    command: "!tool / !pickaxe / !upgradetool",
    display_name: "Pickaxe Catalog",
    dashboard_page: "Mining",
    dashboard_section: "Pickaxes",
    db_table: "modules/mining.py",
    db_key_or_column: "PICKAXE_NAMES,COOLDOWNS,UPGRADE_REQS",
    readSource: "modules/mining.py runtime constants",
    status: "READ ONLY",
    notes: "Pickaxes are represented by mining_players.tool_level and runtime constants; no verified pickaxe catalog table exists.",
  }),
  ...[
    ["!setautofish", "AutoFish Enabled", "autofish_enabled"],
    ["!fishadmin set baseduration", "Base Auto Time", "fish_base_duration"],
    ["!fishadmin set baseinterval", "Base Cast Interval", "fish_base_interval"],
    ["!fishadmin set baseluck", "Base Luck", "fish_base_luck"],
    ["!fishadmin set vipluck", "VIP Luck Bonus", "fish_vip_luck"],
    ["!fishadmin set vipduration", "VIP Duration Bonus", "fish_vip_duration"],
    ["!fishadmin set vipspeed", "VIP Speed Bonus", "fish_vip_speed"],
    ["!fishadmin set mininterval", "Minimum Cast Interval", "fish_min_interval"],
    ["!setautofishduration", "AutoFish Session Duration", "autofish_duration_minutes"],
    ["!setautofishattempts", "AutoFish Max Attempts", "autofish_max_attempts"],
    ["!setautofishdailycap", "AutoFish Daily Cap", "autofish_daily_cap_minutes"],
  ].map(([command, displayName, key]) => auditRow({
    module: "fishing",
    command,
    display_name: displayName,
    dashboard_page: "Economy & Rewards",
    dashboard_section: "Fishing Settings",
    db_table: "auto_activity_settings",
    db_key_or_column: key,
    writeEndpoint: "PUT /api/fishing-settings",
    dashboardConnected: true,
    status: "CONNECTED",
    notes: "Verified active fishing/AutoFish source. fishing.py and luck_stack.py read this key through database.get_auto_activity_setting().",
  })),
  ...[
    ["!setfishcooldown", "Manual Fish Cooldown Override", "room_settings", "fishing_base_cooldown"],
    ["!setfishweights", "Fishing Weights Enabled", "room_settings", "fishing_weights_enabled"],
    ["!setfishweightscale", "Fish Weight Scale", "room_settings", "fishing_weight_scale"],
    ["!setfishannounce", "Fish Announcement Enabled", "room_settings", "fishing_announce_enabled"],
    ["!setfishannounce", "Fish Announcement Minimum Rarity", "room_settings", "fishing_announce_min_rarity"],
    ["!setfishrarityweightrange", "Fish Rarity Weight Ranges", "room_settings", "fish_weight_range_<rarity>"],
    ["!autosellfish / !autosellrare", "Fish Auto Sell", "fish_auto_sell_settings", "auto_sell_enabled"],
  ].map(([command, displayName, table, key]) => auditRow({
    module: "fishing",
    command,
    display_name: displayName,
    dashboard_page: "Economy & Rewards",
    dashboard_section: "Advanced / Unverified Fishing",
    db_table: table,
    db_key_or_column: key,
    readSource: table === "fish_auto_sell_settings"
      ? "SELECT auto_sell_enabled, auto_sell_rare_enabled FROM fish_auto_sell_settings WHERE user_id=?"
      : undefined,
    status: table === "fish_auto_sell_settings" ? "UNKNOWN" : "LEGACY",
    notes: table === "fish_auto_sell_settings"
      ? "Per-user setting; shown read-only in fishing data instead of as a global dashboard field."
      : "Command writes this key, but the active manual catch path does not read it as a normal global setting. Kept out of normal controls.",
  })),
  auditRow({
    module: "fishing",
    command: "!fishlist / !fishprices / !fish",
    display_name: "Fish Catalog",
    dashboard_page: "Fishing",
    dashboard_section: "Fish Catalog",
    db_table: "modules/fishing.py",
    db_key_or_column: "FISH_CATALOG",
    readSource: "modules/fishing.py FISH_CATALOG",
    status: "READ ONLY",
    notes: "Active fishing runtime reads the Python FISH_CATALOG constant. Dashboard parses it read-only and rejects writes as unverified_schema.",
  }),
  auditRow({
    module: "fishing",
    command: "!fishchances",
    display_name: "Fish Catch Chances",
    dashboard_page: "Fishing",
    dashboard_section: "Catch Chances",
    db_table: "modules/fishing.py",
    db_key_or_column: "FISH_CATALOG.drop_weight",
    readSource: "modules/fishing.py FISH_CATALOG drop_weight",
    status: "READ ONLY",
    notes: "Catch chance percentages are calculated from FISH_CATALOG drop_weight. No verified DB weight table is used by active fishing.",
  }),
  auditRow({
    module: "fishing",
    command: "!rods / !rodshop / !buyrod / !equiprod",
    display_name: "Rod Catalog",
    dashboard_page: "Fishing",
    dashboard_section: "Rods",
    db_table: "modules/fishing.py",
    db_key_or_column: "FISHING_RODS",
    readSource: "modules/fishing.py FISHING_RODS",
    status: "READ ONLY",
    notes: "Active rod stats are runtime constants; ownership is stored in player_rods. Dashboard shows rods read-only.",
  }),
  ...[
    ["!setroomsetting", "Room Setting", "room_settings", "<dynamic key>"],
    ["!setwelcome", "Welcome Message", "room_settings", "welcome_message"],
    ["!setemoteloopinterval", "Emote Loop Interval", "room_settings", "emote_loop_interval"],
    ["!setemote <alias> time", "Emote Timing Override", "room_settings", "emote_timing_overrides"],
  ].map(([command, displayName, table, key]) => auditRow({
    module: "room_utils",
    command,
    display_name: displayName,
    dashboard_page: "Room & Content",
    dashboard_section: "Room/Emotes",
    db_table: table,
    db_key_or_column: key,
    status: key.includes("<") ? "UNKNOWN" : "BROKEN",
    notes: key.includes("<") ? "Dynamic key; audit confirms table but not a single dashboard field." : "Verified source; dashboard writes should use the exact room_settings key only.",
  })),
];

const UNKNOWN_SETTINGS_COMMANDS = [
  "setsync",
  "setdance",
  "setevent",
  "setfish",
  "setpokerpace",
  "setpokerstack",
  "setpokercardmarker",
  "casinolimits",
  "casinotoggles",
];

function readAuditCurrentValue(db, item) {
  try {
    if (!item.db_table || !tableExists(db, item.db_table)) return null;
    if (KEY_VALUE_SETTING_TABLES.has(item.db_table)) {
      if (!tableColumns(db, item.db_table).includes("key")) return null;
      if (!tableColumns(db, item.db_table).includes("value")) return null;
      if (String(item.db_key_or_column || "").includes("<")) return null;
      return db.prepare(`SELECT value FROM ${sqlIdent(item.db_table)} WHERE key=?`).get(item.db_key_or_column)?.value ?? null;
    }
    const cols = tableColumns(db, item.db_table);
    if (!cols.includes(item.db_key_or_column)) return null;
    if (cols.includes("id")) {
      return db.prepare(`SELECT ${sqlIdent(item.db_key_or_column)} AS value FROM ${sqlIdent(item.db_table)} WHERE id=1`).get()?.value ?? null;
    }
    return null;
  } catch (err) {
    return `error:${err.message}`;
  }
}

function buildSettingsAudit(db) {
  const rows = SETTINGS_AUDIT_DEFINITIONS.map((item) => ({
    ...item,
    current_value: readAuditCurrentValue(db, item),
  }));
  const connected = rows.filter((r) => r.status === "CONNECTED" && r.dashboard_connected);
  const nonBrokenStatuses = new Set(["LEGACY", "UNKNOWN", "READ ONLY", "UNVERIFIED", "MISSING TABLE", "MISSING COLUMN"]);
  const broken = rows.filter((r) => r.status === "BROKEN" || (r.dashboard_page && !r.dashboard_connected && !nonBrokenStatuses.has(r.status)));
  return {
    rows,
    connected_count: connected.length,
    broken_count: broken.length,
    missing_dashboard_fields: broken.map((r) => ({
      module: r.module,
      command: r.command,
      display_name: r.display_name,
      dashboard_page: r.dashboard_page,
      db_source: `${r.db_table}.${r.db_key_or_column}`,
    })),
    unknown_commands: UNKNOWN_SETTINGS_COMMANDS,
    duplicate_or_legacy_settings: rows
      .filter((r) => r.status === "LEGACY")
      .map((r) => ({
        module: r.module,
        command: r.command,
        display_name: r.display_name,
        db_source: `${r.db_table}.${r.db_key_or_column}`,
        notes: r.notes,
      })),
  };
}

const app = express();
const ROUTE_SECURITY = [];

function routePathLabel(pathValue) {
  if (typeof pathValue === "string") return pathValue;
  return String(pathValue);
}

function routeSecurityFrom(method, pathValue, handlers) {
  const securities = handlers.map((handler) => handler?._security).filter(Boolean);
  const hasAuth = securities.some((s) => s.type === "auth");
  const ownerOnly = securities.some((s) => s.type === "owner");
  const permissions = [...new Set(securities.flatMap((s) => s.permissions || []))];
  const pathLabel = routePathLabel(pathValue);
  const publicRoute =
    pathLabel === "/"
    || pathLabel === "/api/healthz"
    || pathLabel === "/api/dj/status"
    || pathLabel.startsWith("/api/public/")
    || pathLabel === "/api/auth/login"
    || pathLabel === "/.*/";
  const authStatus = publicRoute ? "public" : ownerOnly ? "ownerOnly" : hasAuth ? "requireAuth" : "unprotected";
  return {
    method: method.toUpperCase(),
    path: pathLabel,
    auth: authStatus,
    owner_only: ownerOnly,
    public: publicRoute,
    permissions,
    required_permission: ownerOnly ? "owner" : permissions.join("|"),
  };
}

for (const method of ["get", "post", "put", "delete", "patch"]) {
  const original = app[method].bind(app);
  app[method] = (pathValue, ...handlers) => {
    ROUTE_SECURITY.push(routeSecurityFrom(method, pathValue, handlers.flat()));
    return original(pathValue, ...handlers);
  };
}

function buildPermissionsAudit() {
  const warnings = [];
  const protectedRoutes = ROUTE_SECURITY.filter((route) => route.public || route.auth !== "unprotected").length;
  for (const route of ROUTE_SECURITY) {
    const isApi = route.path.startsWith("/api/");
    const isWrite = ["POST", "PUT", "PATCH", "DELETE"].includes(route.method);
    if (isApi && !route.public && route.auth === "unprotected") {
      warnings.push({ path: route.path, method: route.method, warning: "unprotected_api_route" });
    }
    if (route.path === "/api/auth/logout") continue;
    if (isApi && isWrite && !route.public && route.auth === "requireAuth" && !route.permissions.length && !route.owner_only) {
      warnings.push({ path: route.path, method: route.method, warning: "write_route_missing_permission" });
    }
    if (/bot-config|db\/inspect|settings-audit|permissions\/audit/.test(route.path) && !route.owner_only) {
      warnings.push({ path: route.path, method: route.method, warning: "owner_sensitive_route_not_owner_only" });
    }
  }
  return {
    generated_at: nowIso(),
    permissions: PERMISSION_REGISTRY,
    protected_count: protectedRoutes,
    route_count: ROUTE_SECURITY.length,
    warning_count: warnings.length,
    warnings,
    routes: ROUTE_SECURITY,
  };
}

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

app.get("/api/db/inspect", requireAuth, requireOwner, (req, res) => {
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

app.get("/api/settings-audit", requireAuth, requireOwner, (req, res) => {
  json(res, {
    generated_at: nowIso(),
    ...buildSettingsAudit(req.db),
  });
}, closeDb);

app.get("/api/permissions/audit", requireAuth, requireOwner, (_req, res) => {
  json(res, buildPermissionsAudit());
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
  const botAudit = readCanonicalBotAudit(db);
  const bots = botAudit.bots;
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
    bot_audit: botAudit.summary,
    module_flags: flags,
    command_errors: commandErrors,
    radio: readLocalRadioStatus(db),
    updated_at: nowIso(),
  });
}, closeDb);

app.get("/api/live", requireAuth, (req, res) => {
  const botAudit = readCanonicalBotAudit(req.db);
  const liveRows = safeRows(req.db, "live_status", ["key", "value", "updated_at"], { orderBy: "key" });
  const commands = rowsOrEmpty(
    req.db,
    "command_error_logs",
    "SELECT * FROM command_error_logs ORDER BY id DESC LIMIT 25",
  );
  json(res, { bots: botAudit.bots, bot_audit: botAudit.summary, live_status: liveRows, recent_commands_or_errors: commands, updated_at: nowIso() });
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

app.post("/api/staff", requireAuth, requireOwner, (req, res) => {
  const username = String(req.body?.username ?? "").trim();
  const password = String(req.body?.password ?? "");
  const requestedRole = String(req.body?.role || "staff").toLowerCase();
  const role = ["owner", "admin", "staff", "moderator", "viewer"].includes(requestedRole) ? requestedRole : "staff";
  const permissions = req.body?.permissions && typeof req.body.permissions === "object" ? req.body.permissions : {};
  if (!username || !password) return json(res, { error: "username_password_required" }, 400);
  if (role === "owner" && req.user.role !== "owner") return json(res, { error: "owner_creation_forbidden" }, 403);
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

app.put("/api/staff/:id", requireAuth, requireOwner, (req, res) => {
  const id = Number(req.params.id);
  const old = req.db.prepare("SELECT * FROM dashboard_users WHERE id=?").get(id);
  if (!old) return json(res, { error: "not_found" }, 404);
  const requestedRole = String(req.body?.role || old.role || "staff").toLowerCase();
  const role = ["owner", "admin", "staff", "moderator", "viewer"].includes(requestedRole) ? requestedRole : "staff";
  const disabled = req.body?.disabled ? 1 : 0;
  const permissions = req.body?.permissions && typeof req.body.permissions === "object" ? req.body.permissions : {};
  if ((role === "owner" || old.role === "owner") && req.user.role !== "owner") return json(res, { error: "owner_update_forbidden" }, 403);
  if (old.id === req.user.id && disabled) return json(res, { error: "cannot_disable_self" }, 409);
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

app.delete("/api/staff/:id", requireAuth, requireOwner, (req, res) => {
  const id = Number(req.params.id);
  const old = req.db.prepare("SELECT * FROM dashboard_users WHERE id=?").get(id);
  if (!old) return json(res, { error: "not_found" }, 404);
  if (old.id === req.user.id) return json(res, { error: "cannot_remove_self" }, 409);
  if (old.role === "owner" && req.user.role !== "owner") return json(res, { error: "owner_remove_forbidden" }, 403);
  req.db.prepare("UPDATE dashboard_users SET disabled=1, updated_at=CURRENT_TIMESTAMP WHERE id=?").run(id);
  audit(req.db, req.user.username, "dashboard_user_remove", "dashboard_user", old.username, old, "disabled", req.ip);
  json(res, { ok: true });
}, closeDb);

app.post("/api/staff/bot-role", requireAuth, requireOwner, (req, res) => {
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

const BOT_TOKEN_KEYS = [
  "BOT_TOKEN", "MAIN_BOT_TOKEN", "HOST_BOT_TOKEN", "BLACKJACK_BOT_TOKEN",
  "POKER_BOT_TOKEN", "MINER_BOT_TOKEN", "BANKER_BOT_TOKEN", "SHOP_BOT_TOKEN",
  "SECURITY_BOT_TOKEN", "DJ_BOT_TOKEN", "EVENT_BOT_TOKEN", "FISHER_BOT_TOKEN",
];

app.get("/api/public/home", async (req, res) => {
  let db = null;
  try {
    db = await openDb({ readonly: true });
    const { bots } = readCanonicalBotAudit(db);
    const onlineBots = bots.filter((b) => b.status === "online").length;
    const radio = readLocalRadioStatus(db);
    const nowPlaying = radio.now_playing || null;
    const queueCount = radio.queue?.length ?? 0;
    const roomUsers = (() => {
      try { return Number(db.prepare("SELECT value FROM live_status WHERE key='room_user_count' LIMIT 1").get()?.value ?? 0); } catch { return 0; }
    })();
    const vibe = (() => {
      try { return db.prepare("SELECT value FROM room_settings WHERE key='current_vibe' LIMIT 1").get()?.value ?? "Chill vibes"; } catch { return "Chill vibes"; }
    })();
    json(res, { online_bots: onlineBots, total_bots: bots.length, room_users: roomUsers, queue_count: queueCount, now_playing: nowPlaying ? { title: nowPlaying.title, artist: nowPlaying.artist } : null, vibe });
  } catch (err) {
    json(res, { online_bots: 0, total_bots: 0, room_users: 0, queue_count: 0, now_playing: null, vibe: "Chill vibes" });
  } finally {
    if (db) try { db.close(); } catch {}
  }
});

app.get("/api/public/radio", async (req, res) => {
  let db = null;
  try {
    db = await openDb({ readonly: true });
    const radio = readLocalRadioStatus(db);
    const safeQueue = (radio.queue || []).map((r) => ({ pos: r.pos, title: r.title, artist: r.artist, username: r.username, status: r.status }));
    const safeRecent = (radio.recently_played || []).map((r) => ({ title: r.title, artist: r.artist, username: r.username }));
    const queueOpen = (() => {
      try { return db.prepare("SELECT value FROM bot_settings WHERE key='requests_enabled' LIMIT 1").get()?.value !== "false"; } catch { return true; }
    })();
    json(res, {
      now_playing: radio.now_playing ? { title: radio.now_playing.title, artist: radio.now_playing.artist, username: radio.now_playing.username } : null,
      queue: safeQueue,
      recently_played: safeRecent,
      queue_open: queueOpen,
      stream_url: AZURACAST_STREAM_URL || null,
    });
  } catch (err) {
    json(res, { now_playing: null, queue: [], recently_played: [], queue_open: true, stream_url: null });
  } finally {
    if (db) try { db.close(); } catch {}
  }
});

app.get("/api/public/events", async (req, res) => {
  let db = null;
  try {
    db = await openDb({ readonly: true });
    const roomCurrent = safeRows(db, "room_settings", ["key","value"], { where: "key LIKE 'event.%' OR key = 'active_event'", limit: "20" });
    const eventCurrent = safeRows(db, "event_settings", ["key","value"], { where: "key IN ('event_active','event_name','event_expires_at')", limit: "20" });
    const scheduled = safeRows(db, "scheduled_events", ["id","name","description","starts_at","ends_at","points","reward"], { orderBy: "starts_at ASC", limit: "10" });
    json(res, { current_settings: [...roomCurrent, ...eventCurrent], scheduled });
  } catch {
    json(res, { current_settings: [], scheduled: [] });
  } finally {
    if (db) try { db.close(); } catch {}
  }
});

app.get("/api/public/room-info", async (_req, res) => {
  let db = null;
  try {
    db = await openDb({ readonly: true });
    const settings = readKeyValueMap(db, "room_settings");
    const safeKeys = ["room_rules", "how_to_play", "vip_info", "staff_list", "current_vibe"];
    const info = Object.fromEntries(safeKeys.map((key) => [key, settings[key] || ""]));
    const announcements = safeRows(db, "rotating_announcements", ["id", "message", "text", "body", "content", "enabled"], {
      where: columnExists(db, "rotating_announcements", "enabled") ? "enabled IN ('1', 1, 'true', 'enabled')" : "",
      limit: "5",
    }).map((row) => ({
      id: row.id,
      message: row.message || row.text || row.body || row.content || "",
    })).filter((row) => row.message);
    json(res, { info, announcements });
  } catch {
    json(res, { info: {}, announcements: [] });
  } finally {
    if (db) try { db.close(); } catch {}
  }
});

app.get("/api/public/rankings", async (req, res) => {
  let db = null;
  try {
    db = await openDb({ readonly: true });
    const userCols = tableExists(db, "users") ? tableColumns(db, "users") : [];
    const balanceCol = userCols.includes("balance") ? "balance" : userCols.includes("coins") ? "coins" : null;
    const richList = balanceCol
      ? safeRows(db, "users", ["username",balanceCol,"level"], { orderBy: `${sqlIdent(balanceCol)} DESC`, limit: "10" })
          .map((row) => ({ ...row, coins: row.balance ?? row.coins ?? 0, balance: row.balance ?? row.coins ?? 0 }))
      : [];
    const miners = safeRows(db, "mining_profiles", ["username","total_weight","total_finds"], { orderBy: "total_weight DESC", limit: "10" });
    const fishers = safeRows(db, "fishing_profiles", ["username","total_weight","total_catches"], { orderBy: "total_weight DESC", limit: "10" });
    const topCasino = userCols.includes("casino_winnings")
      ? safeRows(db, "users", ["username","casino_winnings"], { where: "casino_winnings > 0", orderBy: "casino_winnings DESC", limit: "10" })
      : [];
    const topRequesters = safeRows(db, "yt_request_jobs", ["username"], { where: "status='played'", orderBy: "id DESC", limit: "200" })
      .reduce((acc, r) => { acc[r.username] = (acc[r.username] || 0) + 1; return acc; }, {});
    const topRequestersList = Object.entries(topRequesters).sort((a,b) => b[1]-a[1]).slice(0,10).map(([username,count]) => ({ username, count }));
    json(res, { rich_list: richList, miners, fishers, casino: topCasino, top_requesters: topRequestersList });
  } catch (err) {
    json(res, { rich_list: [], miners: [], fishers: [], casino: [], top_requesters: [] });
  } finally {
    if (db) try { db.close(); } catch {}
  }
});

app.get("/api/bot-config", requireAuth, requireOwner, (req, res) => {
  const tokens = {};
  for (const key of BOT_TOKEN_KEYS) {
    tokens[key] = process.env[key] ? "set" : "empty";
  }
  let roomId = process.env.ROOM_ID || "";
  let botsEnabled = process.env.BOTS_ENABLED || "";
  try {
    if (!roomId) roomId = getDashboardSettingValue(req.db, "bot_config.room_id", "");
    if (!botsEnabled) botsEnabled = getDashboardSettingValue(req.db, "bot_config.bots_enabled", "");
  } catch {}
  json(res, { room_id: roomId, bots_enabled: botsEnabled, tokens });
}, closeDb);

app.post("/api/bot-config", requireAuth, requireOwner, (req, res) => {
  if (req.user?.role !== "owner") return json(res, { error: "forbidden" }, 403);
  const roomId = String(req.body?.room_id ?? "").trim();
  const botsEnabled = String(req.body?.bots_enabled ?? "").trim();
  if (roomId) upsertDashboardSetting(req.db, "bot_config.room_id", roomId, "bot_config", req.user.username, "Room ID override from dashboard.");
  if (botsEnabled !== undefined) upsertDashboardSetting(req.db, "bot_config.bots_enabled", botsEnabled, "bot_config", req.user.username, "BOTS_ENABLED override from dashboard.");
  audit(req.db, req.user.username, "bot_config_update", "bot_config", "config", "", { room_id: !!roomId, bots_enabled: botsEnabled }, req.ip);
  json(res, { ok: true });
}, closeDb);

app.post("/api/bot-config/restart", requireAuth, requireOwner, (req, res) => {
  if (req.user?.role !== "owner") return json(res, { error: "forbidden" }, 403);
  upsertDashboardSetting(req.db, "bot_config.restart_requested", nowIso(), "bot_config", req.user.username, "Restart request from dashboard.");
  audit(req.db, req.user.username, "bot_restart_requested", "bot_config", "restart", "", nowIso(), req.ip);
  json(res, { ok: true, note: "Restart flag written to DB. Bot will restart on next heartbeat check." });
}, closeDb);

app.get("/api/radio", requireAuth, requireAnyPermission("manage_radio", "view_logs"), (req, res) => {
  json(res, readLocalRadioStatus(req.db));
}, closeDb);

for (const radioReadPath of [
  "/api/radio/overview",
  "/api/radio/queue",
  "/api/radio/recent",
  "/api/radio/stats",
  "/api/radio/blocklist",
  "/api/radio/logs",
  "/api/radio/status",
  "/api/dj/status",
]) {
  app.get(radioReadPath, requireAuth, requireAnyPermission("manage_radio", "view_logs"), (req, res) => {
    json(res, readLocalRadioStatus(req.db));
  }, closeDb);
}

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
  try {
    const queued = enqueueBotCommand(req.db, { targetBot: "dj", actionName: "radio_clear", payload: {}, requesterId: req.user.username });
    audit(req.db, req.user.username, "radio_queue_clear_enqueue", "bot_command_queue", queued.id, "", { target_bot: "dj", action: "radio_clear" }, req.ip);
    json(res, { ok: true, command: queued, message: "Radio clear queued for DJ_DUDU. Bot must consume bot_command_queue." });
  } catch (err) {
    json(res, { error: err.message || "enqueue_failed" }, 500);
  }
}, closeDb);

app.post("/api/radio/skip", requireAuth, requirePermission("manage_radio"), (req, res) => {
  try {
    const queued = enqueueBotCommand(req.db, { targetBot: "dj", actionName: "radio_skip", payload: {}, requesterId: req.user.username });
    audit(req.db, req.user.username, "radio_skip_enqueue", "bot_command_queue", queued.id, "", { target_bot: "dj", action: "radio_skip" }, req.ip);
    json(res, { ok: true, command: queued, message: "Radio skip queued for DJ_DUDU. Bot must consume bot_command_queue." });
  } catch (err) {
    json(res, { error: err.message || "enqueue_failed" }, 500);
  }
}, closeDb);

app.put("/api/radio/requests-enabled", requireAuth, requirePermission("manage_radio"), (req, res) => {
  const enabled = req.body?.enabled ? "true" : "false";
  upsertDashboardSetting(req.db, "requests_enabled", enabled, "radio", req.user.username, "Dashboard radio request gate consumed by bot modules.");
  setRoomSetting(req.db, "radio_requests_enabled", enabled);
  audit(req.db, req.user.username, "radio_requests_enabled_update", "room_settings", "radio_requests_enabled", "", enabled, req.ip);
  json(res, { ok: true, enabled: enabled === "true" });
}, closeDb);

app.put("/api/radio/settings", requireAuth, requirePermission("manage_radio"), (req, res) => {
  const schema = {
    max_active_queue: { key: "radio_max_active_queue", min: 1, max: 50 },
    per_user_queue_limit: { key: "radio_per_user_queue_limit", min: 0, max: 20 },
    request_cooldown: { key: "radio_request_cooldown", min: 30, max: 86400 },
    request_price: { key: "radio_request_price", min: 0, max: 1000000 },
    voteskip_threshold: { key: "radio_voteskip_threshold", min: 2, max: 25 },
  };
  const updates = {};
  for (const [field, cfg] of Object.entries(schema)) {
    if (!Object.prototype.hasOwnProperty.call(req.body || {}, field)) continue;
    const value = Number(req.body[field]);
    if (!Number.isFinite(value)) return json(res, { error: "invalid_number", field }, 400);
    const clamped = Math.min(cfg.max, Math.max(cfg.min, Math.trunc(value)));
    setRoomSetting(req.db, cfg.key, String(clamped));
    updates[cfg.key] = String(clamped);
  }
  for (const field of ["skip_on_leave", "refund_on_leave", "admin_ignore_leave"]) {
    if (!Object.prototype.hasOwnProperty.call(req.body || {}, field)) continue;
    const key = `radio_${field}`;
    const value = req.body[field] ? "true" : "false";
    setRoomSetting(req.db, key, value);
    updates[key] = value;
  }
  audit(req.db, req.user.username, "radio_settings_update", "room_settings", "radio", "", updates, req.ip);
  json(res, { ok: true, updates, radio: readLocalRadioStatus(req.db) });
}, closeDb);

app.post("/api/radio/blocklist/requester", requireAuth, requirePermission("manage_radio"), (req, res) => {
  if (!tableExists(req.db, "request_blocked_requesters")) return json(res, { error: "blocklist_table_missing" }, 404);
  const username = String(req.body?.username || "").trim().replace(/^@/, "").slice(0, 80);
  if (!username) return json(res, { error: "username_required" }, 400);
  req.db.prepare("INSERT OR IGNORE INTO request_blocked_requesters (username, added_by) VALUES (?, ?)").run(username, req.user.username);
  audit(req.db, req.user.username, "radio_block_requester", "request_blocked_requesters", username, "", username, req.ip);
  json(res, { ok: true, blocklist: readLocalRadioStatus(req.db).blocklist });
}, closeDb);

app.delete("/api/radio/blocklist/requester/:username", requireAuth, requirePermission("manage_radio"), (req, res) => {
  if (!tableExists(req.db, "request_blocked_requesters")) return json(res, { error: "blocklist_table_missing" }, 404);
  const username = String(req.params.username || "").trim().replace(/^@/, "");
  const info = req.db.prepare("DELETE FROM request_blocked_requesters WHERE lower(username)=lower(?)").run(username);
  audit(req.db, req.user.username, "radio_unblock_requester", "request_blocked_requesters", username, "", { removed: info.changes }, req.ip);
  json(res, { ok: true, removed: info.changes });
}, closeDb);

app.post("/api/radio/blocklist/track", requireAuth, requirePermission("manage_radio"), (req, res) => {
  if (!tableExists(req.db, "request_blocked_tracks")) return json(res, { error: "blocklist_table_missing" }, 404);
  const pattern = String(req.body?.pattern || "").trim().slice(0, 200);
  if (!pattern) return json(res, { error: "pattern_required" }, 400);
  req.db.prepare("INSERT OR IGNORE INTO request_blocked_tracks (pattern, added_by) VALUES (?, ?)").run(pattern, req.user.username);
  audit(req.db, req.user.username, "radio_block_track", "request_blocked_tracks", pattern, "", pattern, req.ip);
  json(res, { ok: true, blocklist: readLocalRadioStatus(req.db).blocklist });
}, closeDb);

app.delete("/api/radio/blocklist/track/:id", requireAuth, requirePermission("manage_radio"), (req, res) => {
  if (!tableExists(req.db, "request_blocked_tracks")) return json(res, { error: "blocklist_table_missing" }, 404);
  const id = Number(req.params.id);
  const info = req.db.prepare("DELETE FROM request_blocked_tracks WHERE id=?").run(id);
  audit(req.db, req.user.username, "radio_unblock_track", "request_blocked_tracks", id, "", { removed: info.changes }, req.ip);
  json(res, { ok: true, removed: info.changes });
}, closeDb);

app.post("/api/radio/maintenance/cleanup", requireAuth, requirePermission("manage_radio"), (req, res) => {
  try {
    const queued = enqueueBotCommand(req.db, { targetBot: "dj", actionName: "radio_cleanup", payload: {}, requesterId: req.user.username });
    audit(req.db, req.user.username, "radio_cleanup_enqueue", "bot_command_queue", queued.id, "", { target_bot: "dj", action: "radio_cleanup" }, req.ip);
    json(res, { ok: true, command: queued, message: "Radio cleanup queued for DJ_DUDU. Bot must consume bot_command_queue." });
  } catch (err) {
    json(res, { error: err.message || "enqueue_failed" }, 500);
  }
}, closeDb);

app.get("/api/casino", requireAuth, requirePermission("manage_casino"), (req, res) => {
  const blackjackSettings = readActiveBlackjackSettings(req.db);
  const activePokerSettings = readActivePokerSettings(req.db);
  const pokerSettings = Object.entries(activePokerSettings.raw || {}).map(([key, value]) => ({ key, value }));
  const legacyBlackjackSettings = tableExists(req.db, "bj_settings")
    ? safeTableRows(req.db, "bj_settings", { limit: "1" })[0] || null
    : null;
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
  json(res, {
    settings,
    blackjack_settings: blackjackSettings,
    active_poker_settings: activePokerSettings,
    poker_settings: pokerSettings,
    active_blackjack_source: "rbj_settings",
    legacy_blackjack_settings: legacyBlackjackSettings,
    module_flag: req.db.prepare("SELECT * FROM module_flags WHERE module='casino'").get() ?? null,
  });
}, closeDb);

app.put("/api/casino/blackjack-settings", requireAuth, requirePermission("manage_casino"), (req, res) => {
  if (!tableExists(req.db, "rbj_settings")) return json(res, { error: "rbj_settings_missing" }, 404);
  let next;
  try {
    next = normalizeBlackjackSettingsBody(req.body || {});
  } catch (err) {
    return json(res, { error: err.message || "invalid_blackjack_settings" }, 400);
  }
  try {
    req.db.prepare("INSERT OR IGNORE INTO rbj_settings (id) VALUES (1)").run();
    const cols = tableColumns(req.db, "rbj_settings");
    const updates = Object.entries(next).filter(([key]) => cols.includes(key));
    for (const [key, value] of updates) {
      req.db.prepare(`UPDATE rbj_settings SET ${sqlIdent(key)}=? WHERE id=1`).run(value);
    }
    audit(req.db, req.user.username, "casino_blackjack_settings_update", "rbj_settings", "1", "", next, req.ip);
    json(res, { ok: true, source: "rbj_settings", blackjack_settings: readActiveBlackjackSettings(req.db) });
  } catch (err) {
    json(res, { error: err.message || "blackjack_settings_update_failed" }, 500);
  }
}, closeDb);

app.put("/api/casino/poker-settings", requireAuth, requirePermission("manage_casino"), (req, res) => {
  if (!tableExists(req.db, "poker_settings")) return json(res, { error: "poker_settings_missing" }, 404);
  let next;
  try {
    next = normalizePokerSettingsBody(req.body || {});
  } catch (err) {
    return json(res, { error: err.message || "invalid_poker_settings" }, 400);
  }
  if (!Object.keys(next).length) return json(res, { error: "no_verified_poker_settings" }, 400);
  try {
    const before = readActivePokerSettings(req.db);
    for (const [key, value] of Object.entries(next)) {
      req.db.prepare("INSERT OR REPLACE INTO poker_settings (key, value) VALUES (?, ?)").run(key, String(value));
    }
    const after = readActivePokerSettings(req.db);
    audit(req.db, req.user.username, "casino_poker_settings_update", "poker_settings", "v2", before, next, req.ip);
    json(res, { ok: true, source: "poker_settings", poker_settings: after });
  } catch (err) {
    json(res, { error: err.message || "poker_settings_update_failed" }, 500);
  }
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

app.get("/api/mining-settings", requireAuth, requirePermission("manage_mining"), (req, res) => {
  const kvRows = (table) => Object.entries(readKeyValueMap(req.db, table)).map(([key, value]) => ({ key, value }));
  const autoRows = kvRows("auto_activity_settings").filter((row) => row.key.startsWith("mine") || row.key.startsWith("automine"));
  const goldRows = [
    ...kvRows("gold_settings").map((row) => ({ table: "gold_settings", ...row })),
    ...kvRows("gold_rain_settings").map((row) => ({ table: "gold_rain_settings", ...row })),
  ];
  json(res, {
    settings: readActiveMiningSettings(req.db),
    raw: {
      mining_settings: kvRows("mining_settings"),
      mining_weight_settings: kvRows("mining_weight_settings"),
      auto_activity_settings: autoRows,
      gold_settings: goldRows,
    },
    tables: {
      mining_items: safeRows(req.db, "mining_items", ["item_id", "name", "emoji", "rarity", "item_type", "sell_value", "drop_enabled"], { orderBy: "rarity, sell_value DESC, name", limit: "200" }),
      forced_mining_drops: safeTableRows(req.db, "forced_mining_drops", { orderBy: "id DESC", limit: "50" }),
      mining_events: safeTableRows(req.db, "mining_events", { orderBy: "id DESC", limit: "20" }),
      ore_weight_records: safeTableRows(req.db, "ore_weight_records", { orderBy: "id DESC", limit: "50" }),
    },
    table_status: {
      mining_settings: tableExists(req.db, "mining_settings"),
      mining_weight_settings: tableExists(req.db, "mining_weight_settings"),
      auto_activity_settings: tableExists(req.db, "auto_activity_settings"),
      mining_items: tableExists(req.db, "mining_items"),
      forced_mining_drops: tableExists(req.db, "forced_mining_drops"),
      ore_weight_records: tableExists(req.db, "ore_weight_records"),
    },
  });
}, closeDb);

app.put("/api/mining-settings", requireAuth, requirePermission("manage_mining"), (req, res) => {
  let updates;
  try {
    updates = normalizeMiningSettingsBody(req.db, req.body || {});
  } catch (err) {
    return json(res, { error: err.message || "invalid_mining_settings" }, 400);
  }
  if (!updates.length) return json(res, { error: "no_verified_mining_settings" }, 400);
  try {
    const before = readActiveMiningSettings(req.db);
    for (const update of updates) {
      req.db.prepare(`INSERT OR REPLACE INTO ${sqlIdent(update.table)} (key, value) VALUES (?, ?)`).run(update.key, update.value);
    }
    const after = readActiveMiningSettings(req.db);
    audit(req.db, req.user.username, "mining_settings_update", "mining_settings", "verified_keys", before, updates, req.ip);
    json(res, { ok: true, settings: after });
  } catch (err) {
    json(res, { error: err.message || "mining_settings_update_failed" }, 500);
  }
}, closeDb);

app.get("/api/fishing-settings", requireAuth, requirePermission("manage_fishing"), (req, res) => {
  const kvRows = (table) => Object.entries(readKeyValueMap(req.db, table)).map(([key, value]) => ({ key, value }));
  const autoRows = kvRows("auto_activity_settings").filter((row) => row.key.startsWith("fish_") || row.key.startsWith("autofish"));
  const roomRows = kvRows("room_settings").filter((row) => row.key.startsWith("fishing_") || row.key.startsWith("fish_weight_"));
  const count = (table) => oneOrNull(req.db, table, `SELECT COUNT(*) AS count FROM ${sqlIdent(table)}`)?.count ?? 0;
  const unsoldInventory = oneOrNull(req.db, "fish_inventory", "SELECT COUNT(*) AS count FROM fish_inventory WHERE sold=0")?.count ?? 0;
  const totalInventoryValue = oneOrNull(req.db, "fish_inventory", "SELECT COALESCE(SUM(value), 0) AS total FROM fish_inventory WHERE sold=0")?.total ?? 0;
  json(res, {
    settings: readActiveFishingSettings(req.db),
    stats: {
      fish_profiles: count("fish_profiles"),
      catch_records: count("fish_catch_records"),
      inventory_rows: count("fish_inventory"),
      unsold_inventory: unsoldInventory,
      unsold_inventory_value: totalInventoryValue,
      auto_sell_rows: count("fish_auto_sell_settings"),
      forced_drops: count("forced_fishing_drops"),
    },
    raw: {
      auto_activity_settings: autoRows,
      room_settings: roomRows,
      fish_weight_settings: safeTableRows(req.db, "fish_weight_settings", { orderBy: "key", limit: "200" }),
    },
    tables: {
      fish_profiles: safeRows(req.db, "fish_profiles", ["user_id", "username", "fishing_level", "fishing_xp", "total_catches", "equipped_rod", "best_fish_name", "best_fish_weight", "best_fish_value", "last_fish_at"], { orderBy: "total_catches DESC, fishing_level DESC", limit: "100" }),
      fish_catch_records: safeRows(req.db, "fish_catch_records", ["id", "user_id", "username", "fish_name", "rarity", "weight", "base_value", "final_value", "fxp_earned", "caught_at"], { orderBy: "id DESC", limit: "100" }),
      fish_inventory: safeRows(req.db, "fish_inventory", ["id", "user_id", "username", "fish_name", "rarity", "weight", "value", "sold", "sold_at", "caught_at"], { orderBy: "id DESC", limit: "100" }),
      fish_auto_sell_settings: safeRows(req.db, "fish_auto_sell_settings", ["user_id", "username", "auto_sell_enabled", "auto_sell_rare_enabled", "updated_at"], { orderBy: "updated_at DESC", limit: "100" }),
      forced_fishing_drops: safeTableRows(req.db, "forced_fishing_drops", { orderBy: "id DESC", limit: "50" }),
    },
    table_status: {
      auto_activity_settings: tableExists(req.db, "auto_activity_settings"),
      room_settings: tableExists(req.db, "room_settings"),
      fish_profiles: tableExists(req.db, "fish_profiles"),
      fish_catch_records: tableExists(req.db, "fish_catch_records"),
      fish_inventory: tableExists(req.db, "fish_inventory"),
      fish_auto_sell_settings: tableExists(req.db, "fish_auto_sell_settings"),
      forced_fishing_drops: tableExists(req.db, "forced_fishing_drops"),
      fish_weight_settings: tableExists(req.db, "fish_weight_settings"),
    },
  });
}, closeDb);

app.put("/api/fishing-settings", requireAuth, requirePermission("manage_fishing"), (req, res) => {
  let updates;
  try {
    updates = normalizeFishingSettingsBody(req.db, req.body || {});
  } catch (err) {
    return json(res, { error: err.message || "invalid_fishing_settings" }, 400);
  }
  if (!updates.length) return json(res, { error: "no_verified_fishing_settings" }, 400);
  try {
    const before = readActiveFishingSettings(req.db);
    for (const update of updates) {
      req.db.prepare(`INSERT OR REPLACE INTO ${sqlIdent(update.table)} (key, value) VALUES (?, ?)`).run(update.key, update.value);
    }
    const after = readActiveFishingSettings(req.db);
    audit(req.db, req.user.username, "fishing_settings_update", "fishing_settings", "verified_keys", before, updates, req.ip);
    json(res, { ok: true, settings: after });
  } catch (err) {
    json(res, { error: err.message || "fishing_settings_update_failed" }, 500);
  }
}, closeDb);

app.get("/api/mining", requireAuth, requirePermission("manage_mining"), (req, res) => {
  json(res, {
    ...miningOverview(req.db),
    ores: miningItemRows(req.db, true).slice(0, 100),
    players: safeTableRows(req.db, "mining_players", { orderBy: "total_mines DESC", limit: "25" }),
    inventory: safeRows(req.db, "mining_inventory", ["username", "item_id", "quantity"], { orderBy: "username, item_id", limit: "100" }),
    logs: {
      mining_logs: safeTableRows(req.db, "mining_logs", { orderBy: "id DESC", limit: "50" }),
      mining_payout_logs: safeTableRows(req.db, "mining_payout_logs", { orderBy: "id DESC", limit: "50" }),
      mining_events: safeTableRows(req.db, "mining_events", { orderBy: "id DESC", limit: "25" }),
      forced_mining_drops: safeTableRows(req.db, "forced_mining_drops", { orderBy: "id DESC", limit: "50" }),
    },
  });
}, closeDb);

app.get("/api/mining/ores", requireAuth, requirePermission("manage_mining"), (req, res) => {
  const rows = miningItemRows(req.db, true);
  json(res, { rows, schema_verified: tableExists(req.db, "mining_items"), writable: tableExists(req.db, "mining_items"), table: "mining_items", columns: tableColumns(req.db, "mining_items") });
}, closeDb);

app.post("/api/mining/ores", requireAuth, requirePermission("manage_mining"), (req, res) => {
  if (!tableExists(req.db, "mining_items")) return unverifiedSchema(res, "mining_items table is missing.");
  const cols = tableColumns(req.db, "mining_items");
  const itemId = String(req.body?.item_id || "").trim();
  if (!validCatalogId(itemId)) return json(res, { error: "invalid_item_id" }, 400);
  const before = null;
  const row = {
    item_id: itemId,
    name: String(req.body?.name || itemId).trim(),
    emoji: String(req.body?.emoji || "").trim(),
    rarity: String(req.body?.rarity || "common").trim().toLowerCase(),
    item_type: "ore",
    sell_value: Math.max(0, Math.trunc(Number(req.body?.sell_value || 0))),
    drop_enabled: req.body?.drop_enabled === false || req.body?.drop_enabled === "0" ? 0 : 1,
    created_at: new Date().toISOString(),
  };
  const insertCols = Object.keys(row).filter((c) => cols.includes(c));
  try {
    req.db.prepare(`INSERT INTO mining_items (${insertCols.map(sqlIdent).join(", ")}) VALUES (${insertCols.map(() => "?").join(", ")})`).run(...insertCols.map((c) => row[c]));
    audit(req.db, req.user.username, "mining_ore_create", "mining_items", itemId, before, row, req.ip);
    json(res, { ok: true, row: req.db.prepare("SELECT * FROM mining_items WHERE item_id=?").get(itemId) });
  } catch (err) {
    json(res, { error: err.message || "mining_ore_create_failed" }, 400);
  }
}, closeDb);

app.put("/api/mining/ores/:id", requireAuth, requirePermission("manage_mining"), (req, res) => {
  if (!tableExists(req.db, "mining_items")) return unverifiedSchema(res, "mining_items table is missing.");
  const id = String(req.params.id || "").trim();
  if (!validCatalogId(id)) return json(res, { error: "invalid_item_id" }, 400);
  const before = req.db.prepare("SELECT * FROM mining_items WHERE item_id=?").get(id);
  if (!before) return json(res, { error: "ore_not_found" }, 404);
  const cols = tableColumns(req.db, "mining_items");
  const allowed = {
    name: req.body?.name,
    emoji: req.body?.emoji,
    rarity: req.body?.rarity,
    sell_value: req.body?.sell_value === undefined ? undefined : Math.max(0, Math.trunc(Number(req.body.sell_value || 0))),
    drop_enabled: req.body?.drop_enabled === undefined ? undefined : (req.body.drop_enabled === true || req.body.drop_enabled === "1" || req.body.drop_enabled === 1 ? 1 : 0),
  };
  const updates = Object.entries(allowed).filter(([key, value]) => value !== undefined && cols.includes(key));
  if (!updates.length) return json(res, { error: "no_verified_columns" }, 400);
  req.db.prepare(`UPDATE mining_items SET ${updates.map(([key]) => `${sqlIdent(key)}=?`).join(", ")} WHERE item_id=?`).run(...updates.map(([, value]) => String(value).trim()), id);
  const after = req.db.prepare("SELECT * FROM mining_items WHERE item_id=?").get(id);
  audit(req.db, req.user.username, "mining_ore_update", "mining_items", id, before, after, req.ip);
  json(res, { ok: true, row: after });
}, closeDb);

app.delete("/api/mining/ores/:id", requireAuth, requirePermission("manage_mining"), (req, res) => {
  if (!tableExists(req.db, "mining_items")) return unverifiedSchema(res, "mining_items table is missing.");
  const id = String(req.params.id || "").trim();
  const before = req.db.prepare("SELECT * FROM mining_items WHERE item_id=?").get(id);
  if (!before) return json(res, { error: "ore_not_found" }, 404);
  const hard = req.body?.hard === true || req.query.hard === "1";
  if (!hard) {
    if (!columnExists(req.db, "mining_items", "drop_enabled")) return unverifiedSchema(res, "mining_items.drop_enabled is missing; cannot soft-disable safely.");
    req.db.prepare("UPDATE mining_items SET drop_enabled=0 WHERE item_id=?").run(id);
    audit(req.db, req.user.username, "mining_ore_disable", "mining_items", id, before, { ...before, drop_enabled: 0 }, req.ip);
    return json(res, { ok: true, mode: "soft_disable" });
  }
  if (req.user.role !== "owner") return json(res, { error: "owner_required" }, 403);
  if (String(req.body?.confirmation || "") !== "DELETE ORE") return json(res, { error: "typed_confirmation_required" }, 400);
  const refs = countTable(req.db, "mining_inventory", `item_id=${sqlString(id)}`);
  if (refs && req.body?.force !== true) return json(res, { error: "inventory_references_exist", refs }, 409);
  req.db.prepare("DELETE FROM mining_items WHERE item_id=?").run(id);
  audit(req.db, req.user.username, "mining_ore_hard_delete", "mining_items", id, before, { hard_delete: true }, req.ip);
  json(res, { ok: true, mode: "hard_delete" });
}, closeDb);

app.get("/api/mining/pickaxes", requireAuth, requirePermission("manage_mining"), (_req, res) => {
  json(res, { rows: PICKAXE_CATALOG, writable: false, schema_verified: false, source: "runtime_code", message: "Pickaxes are runtime constants/tool_level, not a DB catalog." });
});
app.post("/api/mining/pickaxes", requireAuth, requirePermission("manage_mining"), (_req, res) => unverifiedSchema(res, "Pickaxes are runtime constants/tool_level, not a verified DB catalog."));
app.put("/api/mining/pickaxes/:id", requireAuth, requirePermission("manage_mining"), (_req, res) => unverifiedSchema(res, "Pickaxes are runtime constants/tool_level, not a verified DB catalog."));
app.delete("/api/mining/pickaxes/:id", requireAuth, requirePermission("manage_mining"), (_req, res) => unverifiedSchema(res, "Pickaxes are runtime constants/tool_level, not a verified DB catalog."));

app.get("/api/mining/drop-weights", requireAuth, requirePermission("manage_mining"), (req, res) => {
  json(res, { rows: calculateMiningDropRows(req.db), writable: false, schema_verified: false, source: "runtime_code", message: "Active mining drop chances are code rarity probabilities split across enabled mining_items." });
}, closeDb);
app.put("/api/mining/drop-weights", requireAuth, requirePermission("manage_mining"), (_req, res) => unverifiedSchema(res, "Active mining drop weights are not stored in a verified DB weight table."));

app.get("/api/mining/players", requireAuth, requirePermission("manage_mining"), (req, res) => {
  json(res, { rows: safeTableRows(req.db, "mining_players", { orderBy: "total_mines DESC", limit: "250" }), table: "mining_players" });
}, closeDb);
app.get("/api/mining/inventory", requireAuth, requirePermission("manage_mining"), (req, res) => {
  const rows = rowsOrEmpty(req.db, "mining_inventory", `SELECT mi.username, mi.item_id, mi.quantity, it.name, it.rarity, it.sell_value, (mi.quantity * it.sell_value) AS total_value FROM mining_inventory mi LEFT JOIN mining_items it ON mi.item_id=it.item_id ORDER BY mi.username, it.sell_value DESC LIMIT 500`);
  json(res, { rows, table: "mining_inventory" });
}, closeDb);
app.get("/api/mining/logs", requireAuth, requireAnyPermission("manage_mining", "view_logs"), (req, res) => {
  json(res, {
    mining_logs: safeTableRows(req.db, "mining_logs", { orderBy: "id DESC", limit: "200" }),
    mining_payout_logs: safeTableRows(req.db, "mining_payout_logs", { orderBy: "id DESC", limit: "200" }),
    mining_events: safeTableRows(req.db, "mining_events", { orderBy: "id DESC", limit: "100" }),
    forced_mining_drops: safeTableRows(req.db, "forced_mining_drops", { orderBy: "id DESC", limit: "100" }),
  });
}, closeDb);

app.get("/api/fishing", requireAuth, requirePermission("manage_fishing"), (req, res) => {
  json(res, {
    ...fishingOverview(req.db),
    players: safeTableRows(req.db, "fish_profiles", { orderBy: "total_catches DESC", limit: "25" }),
    inventory: safeTableRows(req.db, "fish_inventory", { orderBy: "id DESC", limit: "100" }),
    logs: {
      fish_catch_records: safeTableRows(req.db, "fish_catch_records", { orderBy: "id DESC", limit: "100" }),
      forced_fishing_drops: safeTableRows(req.db, "forced_fishing_drops", { orderBy: "id DESC", limit: "50" }),
      fish_auto_sell_settings: safeTableRows(req.db, "fish_auto_sell_settings", { orderBy: "updated_at DESC", limit: "100" }),
    },
  });
}, closeDb);

app.get("/api/fishing/fish", requireAuth, requirePermission("manage_fishing"), (_req, res) => {
  const { fish, source, error } = readFishingCodeCatalog();
  json(res, { rows: fish, writable: false, schema_verified: false, source, error, message: "Active fish catalog is defined in modules/fishing.py FISH_CATALOG; dashboard keeps it read-only." });
});
app.post("/api/fishing/fish", requireAuth, requirePermission("manage_fishing"), (_req, res) => unverifiedSchema(res, "Fish catalog is a runtime code catalog, not a verified DB table."));
app.put("/api/fishing/fish/:id", requireAuth, requirePermission("manage_fishing"), (_req, res) => unverifiedSchema(res, "Fish catalog is a runtime code catalog, not a verified DB table."));
app.delete("/api/fishing/fish/:id", requireAuth, requirePermission("manage_fishing"), (_req, res) => unverifiedSchema(res, "Fish catalog is a runtime code catalog, not a verified DB table."));

app.get("/api/fishing/rods", requireAuth, requirePermission("manage_fishing"), (_req, res) => {
  const { rods, source, error } = readFishingCodeCatalog();
  json(res, { rows: rods, writable: false, schema_verified: false, source, error, message: "Active rod catalog is defined in modules/fishing.py FISHING_RODS; dashboard keeps it read-only." });
});
app.post("/api/fishing/rods", requireAuth, requirePermission("manage_fishing"), (_req, res) => unverifiedSchema(res, "Rod catalog is a runtime code catalog, not a verified DB table."));
app.put("/api/fishing/rods/:id", requireAuth, requirePermission("manage_fishing"), (_req, res) => unverifiedSchema(res, "Rod catalog is a runtime code catalog, not a verified DB table."));
app.delete("/api/fishing/rods/:id", requireAuth, requirePermission("manage_fishing"), (_req, res) => unverifiedSchema(res, "Rod catalog is a runtime code catalog, not a verified DB table."));

app.get("/api/fishing/drop-weights", requireAuth, requirePermission("manage_fishing"), (_req, res) => {
  json(res, { rows: calculateFishDropRows(), writable: false, schema_verified: false, source: "runtime_code", message: "Active fish chances are modules/fishing.py FISH_CATALOG drop_weight values." });
});
app.put("/api/fishing/drop-weights", requireAuth, requirePermission("manage_fishing"), (_req, res) => unverifiedSchema(res, "Fish drop weights are not stored in a verified DB table."));
app.get("/api/fishing/players", requireAuth, requirePermission("manage_fishing"), (req, res) => {
  const rows = tableExists(req.db, "fish_auto_sell_settings")
    ? rowsOrEmpty(req.db, "fish_profiles", `SELECT fp.*, fas.auto_sell_enabled, fas.auto_sell_rare_enabled FROM fish_profiles fp LEFT JOIN fish_auto_sell_settings fas ON fp.user_id=fas.user_id ORDER BY fp.total_catches DESC LIMIT 250`)
    : safeTableRows(req.db, "fish_profiles", { orderBy: "total_catches DESC", limit: "250" });
  json(res, { rows, table: "fish_profiles" });
}, closeDb);
app.get("/api/fishing/inventory", requireAuth, requirePermission("manage_fishing"), (req, res) => {
  json(res, { rows: safeTableRows(req.db, "fish_inventory", { orderBy: "id DESC", limit: "500" }), table: "fish_inventory" });
}, closeDb);
app.get("/api/fishing/logs", requireAuth, requireAnyPermission("manage_fishing", "view_logs"), (req, res) => {
  json(res, {
    fish_catch_records: safeTableRows(req.db, "fish_catch_records", { orderBy: "id DESC", limit: "200" }),
    forced_fishing_drops: safeTableRows(req.db, "forced_fishing_drops", { orderBy: "id DESC", limit: "100" }),
    fish_auto_sell_settings: safeTableRows(req.db, "fish_auto_sell_settings", { orderBy: "updated_at DESC", limit: "100" }),
  });
}, closeDb);

app.get("/api/titles", requireAuth, requireAnyPermission("manage_players", "manage_inventory"), (req, res) => {
  const catalog = rowsOrEmpty(req.db, "title_catalog", "SELECT * FROM title_catalog ORDER BY tier, title_id LIMIT 500");
  const assigned = rowsOrEmpty(req.db, "user_titles", "SELECT * FROM user_titles ORDER BY unlocked_at DESC LIMIT 250");
  const dashboardTitles = req.db.prepare("SELECT * FROM player_titles ORDER BY id DESC LIMIT 250").all();
  json(res, { catalog, assigned, dashboard_titles: dashboardTitles });
}, closeDb);

app.post("/api/titles/assign", requireAuth, requireOwner, (req, res) => {
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
  const status = String(req.query.status || "").trim();
  if (status) {
    where.push("(action_type LIKE ? OR old_value LIKE ? OR new_value LIKE ?)");
    const q = `%${status.slice(0, 80)}%`;
    params.push(q, q, q);
  }
  const target = String(req.query.target || "").trim();
  if (target) {
    where.push("(target_type LIKE ? OR target_id LIKE ?)");
    const q = `%${target.slice(0, 80)}%`;
    params.push(q, q);
  }
  const date = String(req.query.date || "").trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    where.push("date(created_at)=date(?)");
    params.push(date);
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

/* ── Bot Control (canonical audit, read-only) ────────── */
const CANONICAL_BOTS = [
  { username: "DJ_DUDU", mode: "dj", card_title: "DJ Bot", modules: ["DJ Queue", "Radio"] },
  { username: "ChillTopiaMC", mode: "host", card_title: "Room Host", modules: ["Host", "Announcements", "Event Host"] },
  { username: "KeanuShield", mode: "security", card_title: "Security", modules: ["Security", "Moderation"] },
  { username: "AceSinatra", mode: "blackjack", card_title: "Casino Dealer", modules: ["BlackJack", "Realistic BlackJack"] },
  { username: "ChipSoprano", mode: "poker", card_title: "Poker Host", modules: ["Poker"] },
  { username: "GreatestProspector", mode: "miner", card_title: "Miner", modules: ["Mining"] },
  { username: "MasterAngler", mode: "fisher", card_title: "Fisher", modules: ["Fishing"] },
  { username: "BankingBot", mode: "banker", card_title: "Banking Bot", modules: ["Bank", "Economy", "Daily", "Shop"] },
];

const CANONICAL_BY_MODE = new Map(CANONICAL_BOTS.map((bot) => [bot.mode, bot]));
const CANONICAL_BY_USERNAME = new Map(CANONICAL_BOTS.map((bot) => [bot.username.toLowerCase(), bot]));
const BOT_MODE_ALIASES = new Map([
  ["shopkeeper", "banker"],
  ["shop", "banker"],
  ["eventhost", "host"],
  ["event_host", "host"],
  ["rbj", "blackjack"],
  ["realistic_blackjack", "blackjack"],
  ["realistic-blackjack", "blackjack"],
  ["realistic blackjack", "blackjack"],
]);
const DEBUG_ONLY_MODES = new Set(["all", "main"]);
const BOT_INSTANCE_COLUMNS = ["bot_id", "bot_mode", "bot_username", "status", "enabled", "last_heartbeat_at", "last_error", "current_room_id"];

function botKey(value) {
  return String(value || "").trim().toLowerCase();
}

function readBotInstanceRows(db) {
  const orderBy = tableExists(db, "bot_instances") && columnExists(db, "bot_instances", "last_heartbeat_at")
    ? "last_heartbeat_at DESC"
    : "";
  return safeRows(db, "bot_instances", BOT_INSTANCE_COLUMNS, { orderBy });
}

function classifyBotRow(row) {
  const mode = botKey(row.bot_mode);
  const username = botKey(row.bot_username);
  if (DEBUG_ONLY_MODES.has(mode)) return { debugOnly: true, reason: `${mode} is orchestrator/debug only` };
  const aliasMode = BOT_MODE_ALIASES.get(mode);
  if (aliasMode) return { canonicalMode: aliasMode, reason: `${mode} aliases ${aliasMode}` };
  if (CANONICAL_BY_MODE.has(mode)) return { canonicalMode: mode, reason: "canonical mode" };
  const byUsername = CANONICAL_BY_USERNAME.get(username);
  if (byUsername) return { canonicalMode: byUsername.mode, reason: "canonical username" };
  return { debugOnly: true, reason: "not a canonical bot account" };
}

function isRealBotUsername(row) {
  const mode = botKey(row.bot_mode);
  const username = botKey(row.bot_username);
  return !!username && username !== mode;
}

function chooseCanonicalBotRow(rows, canonical) {
  return [...rows].sort((a, b) => {
    const aCanonicalName = botKey(a.bot_username) === canonical.username.toLowerCase() ? 0 : 1;
    const bCanonicalName = botKey(b.bot_username) === canonical.username.toLowerCase() ? 0 : 1;
    if (aCanonicalName !== bCanonicalName) return aCanonicalName - bCanonicalName;
    const aReal = isRealBotUsername(a) ? 0 : 1;
    const bReal = isRealBotUsername(b) ? 0 : 1;
    if (aReal !== bReal) return aReal - bReal;
    const aOnline = botKey(a.status) === "online" ? 0 : 1;
    const bOnline = botKey(b.status) === "online" ? 0 : 1;
    if (aOnline !== bOnline) return aOnline - bOnline;
    return String(b.last_heartbeat_at || "").localeCompare(String(a.last_heartbeat_at || ""));
  })[0] || null;
}

function readCanonicalBotAudit(db) {
  const rawRows = readBotInstanceRows(db);
  const groups = new Map(CANONICAL_BOTS.map((bot) => [bot.mode, []]));
  const rawDebugRows = [];
  const cleanupPreview = [];

  for (const row of rawRows) {
    const classified = classifyBotRow(row);
    if (!classified.canonicalMode) {
      rawDebugRows.push({ ...row, audit_action: "debug_only", audit_reason: classified.reason });
      cleanupPreview.push({ action: "hide_from_bot_control", reason: classified.reason, bot_mode: row.bot_mode, bot_username: row.bot_username });
      continue;
    }
    const annotated = {
      ...row,
      canonical_mode: classified.canonicalMode,
      canonical_username: CANONICAL_BY_MODE.get(classified.canonicalMode)?.username || "",
      audit_reason: classified.reason,
    };
    groups.get(classified.canonicalMode).push(annotated);
  }

  const bots = CANONICAL_BOTS.map((canonical) => {
    const rows = groups.get(canonical.mode) || [];
    const hasRealUsername = rows.some(isRealBotUsername);
    const best = chooseCanonicalBotRow(rows, canonical);
    const hiddenRows = rows.filter((row) => {
      if (!best || row === best) return false;
      const genericWithReal = hasRealUsername && botKey(row.bot_username) === botKey(row.bot_mode);
      const alias = botKey(row.bot_mode) !== canonical.mode;
      return genericWithReal || alias || rows.length > 1;
    });
    for (const row of hiddenRows) {
      rawDebugRows.push({
        ...row,
        audit_action: "merged",
        audit_reason: row.audit_reason || `merged into ${canonical.username}`,
      });
      cleanupPreview.push({
        action: "hide_or_merge_preview",
        canonical_username: canonical.username,
        canonical_mode: canonical.mode,
        reason: row.audit_reason || "duplicate canonical row",
        bot_mode: row.bot_mode,
        bot_username: row.bot_username,
      });
    }
    return {
      bot_id: best?.bot_id ?? null,
      bot_mode: canonical.mode,
      bot_username: canonical.username,
      display_name: canonical.username,
      card_title: canonical.card_title,
      modules: canonical.modules,
      status: best?.status || "missing",
      enabled: best?.enabled ?? null,
      last_heartbeat_at: best?.last_heartbeat_at || null,
      last_error: best?.last_error || null,
      current_room_id: best?.current_room_id || null,
      raw_row_count: rows.length,
      raw_duplicate_count: rows.length,
      source_bot_mode: best?.bot_mode || null,
      source_bot_username: best?.bot_username || null,
    };
  });

  return {
    bots,
    raw_rows: rawRows,
    raw_debug_rows: rawDebugRows,
    cleanup_preview: cleanupPreview,
    summary: {
      canonical_count: bots.length,
      raw_count: rawRows.length,
      debug_only_count: rawDebugRows.filter((row) => row.audit_action === "debug_only").length,
      merged_count: rawDebugRows.filter((row) => row.audit_action === "merged").length,
      cleanup_preview_count: cleanupPreview.length,
    },
  };
}

app.get("/api/bot-control", requireAuth, requireAnyPermission("manage_bots", "view_logs"), (req, res) => {
  const auditResult = readCanonicalBotAudit(req.db);
  const pendingCommands = safeRows(req.db, "bot_command_queue", BOT_COMMAND_QUEUE_COLUMNS, {
    where: "status IN ('pending','queued','claimed','running')",
    orderBy: columnExists(req.db, "bot_command_queue", "created_at") ? "created_at DESC" : "",
    limit: "25",
  });
  const recentCommands = safeRows(req.db, "bot_command_queue", BOT_COMMAND_QUEUE_COLUMNS, {
    orderBy: columnExists(req.db, "bot_command_queue", "created_at") ? "created_at DESC" : "",
    limit: "25",
  });
  json(res, {
    bots: auditResult.bots,
    raw_count: auditResult.raw_rows.length,
    raw_bot_instances: auditResult.raw_rows,
    raw_duplicate_rows: auditResult.raw_debug_rows,
    cleanup_preview: auditResult.cleanup_preview,
    audit_summary: auditResult.summary,
    command_queue: { pending: pendingCommands, recent: recentCommands },
  });
}, closeDb);

app.get("/api/bot-audit", requireAuth, requireAnyPermission("manage_bots", "view_logs"), (req, res) => {
  if (req.user?.role !== "owner") return json(res, { error: "forbidden", permission: "owner" }, 403);
  const auditResult = readCanonicalBotAudit(req.db);
  const pendingCommands = safeRows(req.db, "bot_command_queue", BOT_COMMAND_QUEUE_COLUMNS, {
    where: "status IN ('pending','queued','claimed','running')",
    orderBy: columnExists(req.db, "bot_command_queue", "created_at") ? "created_at DESC" : "",
    limit: "25",
  });
  const recentCommands = safeRows(req.db, "bot_command_queue", BOT_COMMAND_QUEUE_COLUMNS, {
    orderBy: columnExists(req.db, "bot_command_queue", "created_at") ? "created_at DESC" : "",
    limit: "25",
  });
  json(res, {
    canonical_bots: auditResult.bots,
    raw_bot_instances: auditResult.raw_rows,
    raw_debug_rows: auditResult.raw_debug_rows,
    cleanup_preview: auditResult.cleanup_preview,
    command_queue: { pending: pendingCommands, recent: recentCommands },
    summary: auditResult.summary,
    note: "Read-only cleanup preview. No dashboard endpoint deletes bot_instances rows.",
  });
}, closeDb);

/* ── Economy Overview (read-only) ───────────────────── */
app.get("/api/economy/overview", requireAuth, requireAnyPermission("manage_economy","manage_casino","manage_games","emergency_controls"), (req, res) => {
  const stats = (() => {
    try {
      if (!tableExists(req.db, "users")) return null;
      const cols = tableColumns(req.db, "users");
      const balanceCol = cols.includes("balance") ? "balance" : cols.includes("coins") ? "coins" : null;
      if (!balanceCol) return { player_count: req.db.prepare("SELECT COUNT(*) AS player_count FROM users").get()?.player_count ?? 0 };
      const hasTix = cols.includes("tickets");
      const selTix = hasTix ? ", COALESCE(SUM(tickets),0) AS total_tickets" : "";
      return req.db.prepare(
        `SELECT COUNT(*) AS player_count, COALESCE(SUM(${sqlIdent(balanceCol)}),0) AS total_balance${selTix}, COALESCE(AVG(${sqlIdent(balanceCol)}),0) AS avg_balance, COALESCE(MAX(${sqlIdent(balanceCol)}),0) AS richest_balance FROM users`
      ).get();
    } catch { return null; }
  })();
  const userCols = tableExists(req.db, "users") ? tableColumns(req.db, "users") : [];
  const balanceCol = userCols.includes("balance") ? "balance" : userCols.includes("coins") ? "coins" : null;
  const topRich = balanceCol
    ? safeRows(req.db, "users", ["user_id","username",balanceCol,"level","xp"], { where: `${sqlIdent(balanceCol)} > 0`, orderBy: `${sqlIdent(balanceCol)} DESC`, limit: "15" })
        .map((row) => ({ ...row, balance: row.balance ?? row.coins ?? 0 }))
    : [];
  const topXp = userCols.includes("xp")
    ? safeRows(req.db, "users", ["user_id","username","xp","level"], { where: "xp > 0", orderBy: "xp DESC", limit: "10" })
    : [];
  const recentTransactions = {
    ledger: safeRows(req.db, "ledger", ["id","timestamp","user_id","username","change_amount","reason","balance_before","balance_after","related_user","metadata"], {
      orderBy: columnExists(req.db, "ledger", "timestamp") ? "timestamp DESC" : "",
      limit: "50",
    }),
    economy_transactions: safeRows(req.db, "economy_transactions", ["id","tx_id","user_id","username","currency","amount","direction","source","details","event_id","created_at"], {
      orderBy: columnExists(req.db, "economy_transactions", "created_at") ? "created_at DESC" : "",
      limit: "50",
    }),
    bank_transactions: safeRows(req.db, "bank_transactions", ["id","timestamp","sender_username","receiver_username","amount_sent","fee","amount_received","status"], {
      orderBy: columnExists(req.db, "bank_transactions", "timestamp") ? "timestamp DESC" : "",
      limit: "50",
    }),
  };
  json(res, { stats, top_rich: topRich, top_xp: topXp, transactions: recentTransactions, balance_column: balanceCol });
}, closeDb);

app.get("/api/economy/transactions", requireAuth, requireAnyPermission("manage_economy","view_logs","emergency_controls"), (req, res) => {
  json(res, {
    ledger: safeRows(req.db, "ledger", ["id","timestamp","user_id","username","change_amount","reason","balance_before","balance_after","related_user","metadata"], {
      orderBy: columnExists(req.db, "ledger", "timestamp") ? "timestamp DESC" : "",
      limit: "100",
    }),
    economy_transactions: safeRows(req.db, "economy_transactions", ["id","tx_id","user_id","username","currency","amount","direction","source","details","event_id","created_at"], {
      orderBy: columnExists(req.db, "economy_transactions", "created_at") ? "created_at DESC" : "",
      limit: "100",
    }),
    bank_transactions: safeRows(req.db, "bank_transactions", ["id","timestamp","sender_username","receiver_username","amount_sent","fee","amount_received","status"], {
      orderBy: columnExists(req.db, "bank_transactions", "timestamp") ? "timestamp DESC" : "",
      limit: "100",
    }),
  });
}, closeDb);

/* ── Player Search ──────────────────────────────────── */
function readPlayerProfile(db, idOrQuery) {
  const q = String(idOrQuery || "").trim().slice(0, 100);
  if (!q) return null;
  const desired = [
    "user_id","username","balance","coins","tickets","xp","level","total_games_won","total_coins_earned",
    "equipped_badge","equipped_title","equipped_badge_id","equipped_title_id","tip_coins_earned",
  ];
  if (!tableExists(db, "users")) return null;
  let player = safeOne(db, "users", desired, { where: "lower(username)=lower(?) OR user_id=?", params: [q, q] });
  if (!player) player = safeOne(db, "users", desired, { where: "lower(username) LIKE ?", params: [`%${q.toLowerCase()}%`] });
  if (!player) return null;
  if (player.balance == null && player.coins != null) player.balance = player.coins;
  delete player.coins;
  const userId = player.user_id || "";
  const username = player.username || "";
  const ownedItems = safeRows(db, "owned_items", ["user_id","item_id","item_type"], {
    where: "user_id=?",
    params: [userId],
    orderBy: columnExists(db, "owned_items", "item_type") && columnExists(db, "owned_items", "item_id") ? "item_type, item_id" : "",
    limit: "200",
  });
  const ownedCount = (() => {
    try { return tableExists(db, "owned_items") ? (db.prepare("SELECT COUNT(*) AS n FROM owned_items WHERE user_id=?").get(userId)?.n ?? 0) : 0; } catch { return 0; }
  })();

  const miningInventory = safeRows(db, "mining_inventory", ["user_id","username","item_id","quantity"], {
    where: columnExists(db, "mining_inventory", "user_id") ? "user_id=? OR lower(username)=lower(?)" : "lower(username)=lower(?)",
    params: columnExists(db, "mining_inventory", "user_id") ? [userId, username] : [username],
    orderBy: columnExists(db, "mining_inventory", "item_id") ? "item_id" : "",
    limit: "200",
  });
  const fishInventory = safeRows(db, "fish_inventory", ["id","user_id","username","fish_name","rarity","weight","value","quantity","sold","sold_at","caught_at"], {
    where: columnExists(db, "fish_inventory", "user_id") ? "user_id=? OR lower(username)=lower(?)" : "lower(username)=lower(?)",
    params: columnExists(db, "fish_inventory", "user_id") ? [userId, username] : [username],
    orderBy: columnExists(db, "fish_inventory", "caught_at") ? "caught_at DESC" : "",
    limit: "200",
  });
  const titles = {
    user_titles: safeRows(db, "user_titles", ["user_id","username","title_id","source","unlocked_at","expires_at"], {
      where: "user_id=? OR lower(username)=lower(?)",
      params: [userId, username],
      limit: "100",
    }),
    player_titles: safeRows(db, "player_titles", ["id","user_id","username","title_id","display","color","source","created_by","created_at"], {
      where: "user_id=? OR lower(username)=lower(?)",
      params: [userId, username],
      limit: "100",
    }),
  };
  const badges = {
    user_badges: safeRows(db, "user_badges", ["id","username","badge_id","acquired_at","source","equipped","locked"], {
      where: "lower(username)=lower(?)",
      params: [username],
      limit: "100",
    }),
  };
  const moderation = {
    warnings: safeRows(db, "warnings", ["id","user_id","username","warned_by","reason","created_at"], {
      where: "user_id=? OR lower(username)=lower(?)",
      params: [userId, username],
      orderBy: columnExists(db, "warnings", "created_at") ? "created_at DESC" : "",
      limit: "50",
    }),
    mutes: safeRows(db, "mutes", ["user_id","username","muted_by","muted_at","expires_at"], {
      where: "user_id=? OR lower(username)=lower(?)",
      params: [userId, username],
      limit: "20",
    }),
    reports: safeRows(db, "reports", ["id","timestamp","reporter_username","target_username","report_type","reason","status","handled_by"], {
      where: "lower(target_username)=lower(?)",
      params: [username],
      orderBy: columnExists(db, "reports", "timestamp") ? "timestamp DESC" : "",
      limit: "50",
    }),
    logs: safeRows(db, "moderation_logs", ["id","action_id","staff_id","staff_name","target_id","target_name","action","reason","duration_minutes","created_at"], {
      where: "target_id=? OR lower(target_name)=lower(?)",
      params: [userId, username],
      orderBy: columnExists(db, "moderation_logs", "created_at") ? "created_at DESC" : "",
      limit: "50",
    }),
  };
  const ledgerRows = safeRows(db, "ledger", ["id","timestamp","user_id","username","change_amount","reason","balance_before","balance_after","related_user","metadata"], {
    where: "user_id=? OR lower(username)=lower(?)",
    params: [userId, username],
    orderBy: columnExists(db, "ledger", "timestamp") ? "timestamp DESC" : "",
    limit: "50",
  });
  const economyRows = safeRows(db, "economy_transactions", ["id","tx_id","user_id","username","currency","amount","direction","source","details","event_id","created_at"], {
    where: "user_id=? OR lower(username)=lower(?)",
    params: [userId, username],
    orderBy: columnExists(db, "economy_transactions", "created_at") ? "created_at DESC" : "",
    limit: "50",
  });
  const bankRows = safeRows(db, "bank_transactions", ["id","timestamp","sender_id","sender_username","receiver_id","receiver_username","amount_sent","fee","amount_received","status"], {
    where: "sender_id=? OR receiver_id=? OR lower(sender_username)=lower(?) OR lower(receiver_username)=lower(?)",
    params: [userId, userId, username, username],
    orderBy: columnExists(db, "bank_transactions", "timestamp") ? "timestamp DESC" : "",
    limit: "50",
  });
  return {
    ...player,
    owned_items_count: ownedCount,
    owned_item_count: ownedCount,
    owned_items: ownedItems,
    mining_inventory: miningInventory,
    fishing_inventory: fishInventory,
    titles,
    badges,
    moderation,
    recent_activity: { ledger: ledgerRows, economy_transactions: economyRows, bank_transactions: bankRows },
    summaries: {
      mining_inventory_count: miningInventory.length,
      fishing_inventory_count: fishInventory.length,
      warnings_count: moderation.warnings.length,
      mutes_count: moderation.mutes.length,
      reports_count: moderation.reports.length,
      is_vip: ownedItems.some((item) => String(item.item_id).toLowerCase() === "vip"),
    },
  };
}

app.get("/api/player/search", requireAuth, (req, res) => {
  const q = String(req.query.q || "").trim().slice(0, 80);
  if (!q) return json(res, { player: null, error: "query_required" }, 400);
  if (!tableExists(req.db, "users")) return json(res, { player: null, error: "users_table_missing" }, 404);
  const player = readPlayerProfile(req.db, q);
  if (!player) return json(res, { player: null });
  json(res, { player });
}, closeDb);

app.get("/api/player/:id/history", requireAuth, requireAnyPermission("manage_players", "manage_moderation", "view_logs"), (req, res) => {
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "not_found" }, 404);
  json(res, { player, history: { ...player.recent_activity, moderation: player.moderation } });
}, closeDb);

function insertPlayerLedger(db, player, changeAmount, reason, before, after, actor) {
  if (!tableExists(db, "ledger")) return;
  const cols = tableColumns(db, "ledger");
  const desired = ["user_id","username","change_amount","reason","balance_before","balance_after","related_user","metadata"];
  if (!desired.every((col) => cols.includes(col))) return;
  db.prepare(
    "INSERT INTO ledger (user_id, username, change_amount, reason, balance_before, balance_after, related_user, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
  ).run(player.user_id, player.username, changeAmount, reason, before, after, actor, JSON.stringify({ source: "dashboard", actor }));
}

function insertEconomyTransaction(db, player, amount, direction, reason, actor) {
  if (!tableExists(db, "economy_transactions")) return;
  const cols = tableColumns(db, "economy_transactions");
  const desired = ["tx_id","user_id","username","currency","amount","direction","source","details"];
  if (!desired.every((col) => cols.includes(col))) return;
  db.prepare(
    "INSERT INTO economy_transactions (tx_id, user_id, username, currency, amount, direction, source, details) VALUES (?, ?, ?, 'chillcoins', ?, ?, 'dashboard', ?)",
  ).run(crypto.randomUUID(), player.user_id, player.username, Math.abs(amount), direction, JSON.stringify({ reason, actor }));
}

app.post("/api/player/:id/economy", requireAuth, requireOwner, (req, res) => {
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "not_found" }, 404);
  const actionName = String(req.body?.action || "").trim();
  const reason = String(req.body?.reason || "").trim();
  const amount = Number(req.body?.amount);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  if (!Number.isFinite(amount)) return json(res, { error: "amount_required" }, 400);
  const usersCols = tableColumns(req.db, "users");
  const oldValues = { balance: Number(player.balance || 0), xp: Number(player.xp || 0), level: Number(player.level || 1) };
  const updates = {};
  const balanceAction = ["set_balance", "add_balance", "remove_balance"].includes(actionName);
  if (balanceAction && !usersCols.includes("balance")) return json(res, { error: "balance_column_missing" }, 409);
  if (actionName === "set_balance") updates.balance = Math.trunc(amount);
  else if (actionName === "add_balance") updates.balance = oldValues.balance + Math.trunc(amount);
  else if (actionName === "remove_balance") updates.balance = oldValues.balance - Math.trunc(amount);
  else if (actionName === "set_xp") updates.xp = Math.trunc(amount);
  else if (actionName === "add_xp") updates.xp = oldValues.xp + Math.trunc(amount);
  else if (actionName === "remove_xp") updates.xp = oldValues.xp - Math.trunc(amount);
  else if (actionName === "set_level") updates.level = Math.trunc(amount);
  else return json(res, { error: "action_not_allowed" }, 400);
  if (updates.balance != null && updates.balance < 0 && !req.body?.allow_negative_balance) {
    return json(res, { error: "negative_balance_disallowed" }, 400);
  }
  if (updates.xp != null && updates.xp < 0) updates.xp = 0;
  if (updates.level != null && updates.level < 1) updates.level = 1;
  const updateCols = Object.keys(updates).filter((col) => usersCols.includes(col));
  if (!updateCols.length) return json(res, { error: "no_verified_columns" }, 409);
  const setSql = updateCols.map((col) => `${sqlIdent(col)}=?`).join(", ");
  req.db.prepare(`UPDATE users SET ${setSql} WHERE user_id=?`).run(...updateCols.map((col) => updates[col]), player.user_id);
  if (updates.balance != null) {
    const delta = updates.balance - oldValues.balance;
    insertPlayerLedger(req.db, player, delta, reason, oldValues.balance, updates.balance, req.user.username);
    insertEconomyTransaction(req.db, player, delta, delta >= 0 ? "credit" : "debit", reason, req.user.username);
  }
  const updatedPlayer = readPlayerProfile(req.db, player.user_id);
  audit(req.db, req.user.username, `player_economy_${actionName}`, "users", player.user_id, oldValues, { updates, reason, player: updatedPlayer?.username }, req.ip);
  json(res, { ok: true, player: updatedPlayer });
}, closeDb);

app.post("/api/player/:id/items", requireAuth, requireOwner, (req, res) => {
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "not_found" }, 404);
  if (!tableExists(req.db, "owned_items")) return json(res, { error: "owned_items_missing" }, 404);
  const itemId = String(req.body?.item_id || "").trim().slice(0, 120);
  const itemType = String(req.body?.item_type || "").trim().slice(0, 80);
  if (!itemId || !itemType) return json(res, { error: "item_id_and_type_required" }, 400);
  const before = safeOne(req.db, "owned_items", ["user_id","item_id","item_type"], { where: "user_id=? AND item_id=?", params: [player.user_id, itemId] });
  req.db.prepare("INSERT OR IGNORE INTO owned_items (user_id, item_id, item_type) VALUES (?, ?, ?)").run(player.user_id, itemId, itemType);
  audit(req.db, req.user.username, "player_item_add", "owned_items", `${player.user_id}:${itemId}`, before, { item_id: itemId, item_type: itemType, reason: req.body?.reason || "" }, req.ip);
  json(res, { ok: true, player: readPlayerProfile(req.db, player.user_id) });
}, closeDb);

app.delete("/api/player/:id/items/:item_id", requireAuth, requireOwner, (req, res) => {
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "not_found" }, 404);
  if (!tableExists(req.db, "owned_items")) return json(res, { error: "owned_items_missing" }, 404);
  const itemId = String(req.params.item_id || "").trim();
  const before = safeOne(req.db, "owned_items", ["user_id","item_id","item_type"], { where: "user_id=? AND item_id=?", params: [player.user_id, itemId] });
  const info = req.db.prepare("DELETE FROM owned_items WHERE user_id=? AND item_id=?").run(player.user_id, itemId);
  audit(req.db, req.user.username, "player_item_remove", "owned_items", `${player.user_id}:${itemId}`, before, { removed: info.changes, reason: req.body?.reason || "" }, req.ip);
  json(res, { ok: true, removed: info.changes, player: readPlayerProfile(req.db, player.user_id) });
}, closeDb);

app.post("/api/player/:id/titles", requireAuth, requireOwner, (req, res) => {
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "not_found" }, 404);
  if (!tableExists(req.db, "user_titles")) return json(res, { error: "user_titles_missing" }, 404);
  const titleId = String(req.body?.title_id || "").trim().slice(0, 120);
  if (!titleId) return json(res, { error: "title_id_required" }, 400);
  req.db.prepare("INSERT OR IGNORE INTO user_titles (user_id, username, title_id, source) VALUES (?, ?, ?, 'dashboard')").run(player.user_id, player.username, titleId);
  audit(req.db, req.user.username, "player_title_add", "user_titles", `${player.user_id}:${titleId}`, "", { title_id: titleId, reason: req.body?.reason || "" }, req.ip);
  json(res, { ok: true, player: readPlayerProfile(req.db, player.user_id) });
}, closeDb);

app.delete("/api/player/:id/titles/:title_id", requireAuth, requireOwner, (req, res) => {
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "not_found" }, 404);
  const titleId = String(req.params.title_id || "").trim();
  const before = safeOne(req.db, "user_titles", ["user_id","username","title_id","source","unlocked_at"], { where: "user_id=? AND title_id=?", params: [player.user_id, titleId] });
  const info = tableExists(req.db, "user_titles") ? req.db.prepare("DELETE FROM user_titles WHERE user_id=? AND title_id=?").run(player.user_id, titleId) : { changes: 0 };
  audit(req.db, req.user.username, "player_title_remove", "user_titles", `${player.user_id}:${titleId}`, before, { removed: info.changes, reason: req.body?.reason || "" }, req.ip);
  json(res, { ok: true, removed: info.changes, player: readPlayerProfile(req.db, player.user_id) });
}, closeDb);

app.post("/api/player/:id/badges", requireAuth, requireOwner, (req, res) => {
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "not_found" }, 404);
  if (!tableExists(req.db, "user_badges")) return json(res, { error: "user_badges_missing" }, 404);
  const badgeId = String(req.body?.badge_id || "").trim().slice(0, 120);
  if (!badgeId) return json(res, { error: "badge_id_required" }, 400);
  req.db.prepare("INSERT OR IGNORE INTO user_badges (username, badge_id, acquired_at, source) VALUES (?, ?, CURRENT_TIMESTAMP, 'dashboard')").run(player.username, badgeId);
  audit(req.db, req.user.username, "player_badge_add", "user_badges", `${player.username}:${badgeId}`, "", { badge_id: badgeId, reason: req.body?.reason || "" }, req.ip);
  json(res, { ok: true, player: readPlayerProfile(req.db, player.user_id) });
}, closeDb);

app.delete("/api/player/:id/badges/:badge_id", requireAuth, requireOwner, (req, res) => {
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "not_found" }, 404);
  const badgeId = String(req.params.badge_id || "").trim();
  const before = safeOne(req.db, "user_badges", ["id","username","badge_id","acquired_at","source","equipped","locked"], { where: "lower(username)=lower(?) AND badge_id=?", params: [player.username, badgeId] });
  const info = tableExists(req.db, "user_badges") ? req.db.prepare("DELETE FROM user_badges WHERE lower(username)=lower(?) AND badge_id=?").run(player.username, badgeId) : { changes: 0 };
  audit(req.db, req.user.username, "player_badge_remove", "user_badges", `${player.username}:${badgeId}`, before, { removed: info.changes, reason: req.body?.reason || "" }, req.ip);
  json(res, { ok: true, removed: info.changes, player: readPlayerProfile(req.db, player.user_id) });
}, closeDb);

function writeModerationLog(db, actor, player, actionName, reason, duration = 0) {
  if (!tableExists(db, "moderation_logs")) return;
  const cols = tableColumns(db, "moderation_logs");
  const desired = ["action_id","staff_name","target_id","target_name","action","reason","duration_minutes"];
  if (!desired.every((col) => cols.includes(col))) return;
  db.prepare("INSERT INTO moderation_logs (action_id, staff_name, target_id, target_name, action, reason, duration_minutes) VALUES (?, ?, ?, ?, ?, ?, ?)")
    .run(crypto.randomUUID(), actor, player.user_id, player.username, actionName, reason, duration);
}

app.post("/api/player/:id/warn", requireAuth, requireAnyPermission("manage_moderation","emergency_controls"), (req, res) => {
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "not_found" }, 404);
  if (!tableExists(req.db, "warnings")) return json(res, { error: "warnings_missing" }, 404);
  const reason = String(req.body?.reason || "").trim();
  if (!reason) return json(res, { error: "reason_required" }, 400);
  req.db.prepare("INSERT INTO warnings (user_id, username, warned_by, reason) VALUES (?, ?, ?, ?)").run(player.user_id, player.username, req.user.username, reason);
  writeModerationLog(req.db, req.user.username, player, "warn", reason, 0);
  audit(req.db, req.user.username, "player_warn", "warnings", player.user_id, "", { reason }, req.ip);
  json(res, { ok: true, player: readPlayerProfile(req.db, player.user_id) });
}, closeDb);

app.post("/api/player/:id/mute", requireAuth, requireAnyPermission("manage_moderation","emergency_controls"), (req, res) => {
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "not_found" }, 404);
  if (!tableExists(req.db, "mutes")) return json(res, { error: "mutes_missing" }, 404);
  const reason = String(req.body?.reason || "").trim();
  const minutes = Math.max(1, Math.min(10080, Math.trunc(Number(req.body?.minutes || 60))));
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const before = safeOne(req.db, "mutes", ["user_id","username","muted_by","muted_at","expires_at"], { where: "user_id=?", params: [player.user_id] });
  req.db.prepare("INSERT OR REPLACE INTO mutes (user_id, username, muted_by, expires_at) VALUES (?, ?, ?, datetime('now', ?))")
    .run(player.user_id, player.username, req.user.username, `+${minutes} minutes`);
  writeModerationLog(req.db, req.user.username, player, "mute", reason, minutes);
  audit(req.db, req.user.username, "player_mute", "mutes", player.user_id, before, { reason, minutes }, req.ip);
  json(res, { ok: true, player: readPlayerProfile(req.db, player.user_id) });
}, closeDb);

app.post("/api/player/:id/unmute", requireAuth, requireAnyPermission("manage_moderation","emergency_controls"), (req, res) => {
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "not_found" }, 404);
  const before = safeOne(req.db, "mutes", ["user_id","username","muted_by","muted_at","expires_at"], { where: "user_id=?", params: [player.user_id] });
  const info = tableExists(req.db, "mutes") ? req.db.prepare("DELETE FROM mutes WHERE user_id=?").run(player.user_id) : { changes: 0 };
  writeModerationLog(req.db, req.user.username, player, "unmute", String(req.body?.reason || ""), 0);
  audit(req.db, req.user.username, "player_unmute", "mutes", player.user_id, before, { removed: info.changes, reason: req.body?.reason || "" }, req.ip);
  json(res, { ok: true, removed: info.changes, player: readPlayerProfile(req.db, player.user_id) });
}, closeDb);

/* ── Bot Spawns / Command Queue ─────────────────────── */
app.get("/api/bot-spawns", requireAuth, requireAnyPermission("manage_bots", "emergency_controls"), (req, res) => {
  const rows = safeRows(req.db, "bot_spawns", ["bot_username","spawn_name","x","y","z","facing","set_by","set_at"], {
    orderBy: columnExists(req.db, "bot_spawns", "bot_username") && columnExists(req.db, "bot_spawns", "spawn_name") ? "bot_username, spawn_name" : "",
  });
  const grouped = {};
  for (const row of rows) {
    const key = row.bot_username || "unknown";
    if (!grouped[key]) grouped[key] = [];
    grouped[key].push(row);
  }
  json(res, { spawns: rows, grouped });
}, closeDb);

const ALLOWED_BOT_COMMAND_ACTIONS = new Set([
  "return_home",
  "stop_emote",
  "restart_requested",
  "announce",
  "trigger_emote",
  "radio_skip",
  "radio_clear",
  "radio_cleanup",
  "radio_reload",
  "event_start",
  "event_stop",
  "event_schedule",
  "dancefloor_start",
  "dancefloor_stop",
  "dancefloor_clear",
  "dancefloor_status",
  "dancefloor_sequence",
  "dancefloor_random",
  "dancefloor_randomtimed",
  "sync_start",
  "sync_stop",
  "sync_persist",
  "botemote_set",
  "botemote_stop",
]);

app.post("/api/bot-command", requireAuth, requireAnyPermission("emergency_controls","manage_radio","manage_games","manage_room","manage_events","manage_emotes","manage_bots"), (req, res) => {
  const targetBot = String(req.body?.target_bot || "").trim().slice(0, 80);
  const actionName = String(req.body?.action || "").trim();
  const payload = req.body?.payload && typeof req.body.payload === "object" && !Array.isArray(req.body.payload) ? req.body.payload : {};
  if (!targetBot) return json(res, { error: "target_bot_required" }, 400);
  if (!ALLOWED_BOT_COMMAND_ACTIONS.has(actionName)) return json(res, { error: "action_not_allowed" }, 400);
  if (JSON.stringify(payload).length > 2000) return json(res, { error: "payload_too_large" }, 400);
  try {
    const queued = enqueueBotCommand(req.db, { targetBot, actionName, payload, requesterId: req.user.username });
    audit(req.db, req.user.username, "bot_command_enqueue", "bot_command_queue", queued.id, "", { target_bot: targetBot, action: actionName, payload }, req.ip);
    json(res, { ok: true, command: queued, message: "Command queued. Bot must consume bot_command_queue." });
  } catch (err) {
    json(res, { error: err.message || "enqueue_failed" }, 500);
  }
}, closeDb);

app.get("/api/bot-command-queue", requireAuth, requireAnyPermission("manage_bots","manage_radio","manage_room","manage_events","manage_emotes","view_logs"), (req, res) => {
  const pending = safeRows(req.db, "bot_command_queue", BOT_COMMAND_QUEUE_COLUMNS, {
    where: "status IN ('pending','queued','claimed','running')",
    orderBy: columnExists(req.db, "bot_command_queue", "created_at") ? "created_at DESC" : "",
    limit: "50",
  });
  const recent = safeRows(req.db, "bot_command_queue", BOT_COMMAND_QUEUE_COLUMNS, {
    orderBy: columnExists(req.db, "bot_command_queue", "created_at") ? "created_at DESC" : "",
    limit: "50",
  });
  json(res, { pending, recent });
}, closeDb);

app.get("/api/emotes/overview", requireAuth, requireAnyPermission("emergency_controls", "manage_emotes"), (req, res) => {
  json(res, readEmotesDashboard(req.db));
}, closeDb);

app.get("/api/emotes/registry", requireAuth, requireAnyPermission("emergency_controls", "manage_emotes"), (req, res) => {
  const d = readEmotesDashboard(req.db);
  json(res, { registry: d.registry, active_emotes: d.tables.active_emotes, raw_files: d.raw_files });
}, closeDb);

app.get("/api/emotes/bot-status", requireAuth, requireAnyPermission("emergency_controls", "manage_emotes"), (req, res) => {
  const d = readEmotesDashboard(req.db);
  json(res, { bot_emotes: d.bot_emotes, room_emote_loops: d.tables.room_emote_loops, command_queue: d.command_queue });
}, closeDb);

app.get("/api/emotes/custom-packs", requireAuth, requireAnyPermission("emergency_controls", "manage_emotes"), (req, res) => {
  const d = readEmotesDashboard(req.db);
  json(res, { custom_packs: d.custom_packs, dancefloor_packs: d.dancefloor.packs, tables: { custom_emote_packs: d.tables.custom_emote_packs, dancefloor_packs: d.tables.dancefloor_packs } });
}, closeDb);

app.get("/api/emotes/dancefloor", requireAuth, requireAnyPermission("emergency_controls", "manage_emotes"), (req, res) => {
  const d = readEmotesDashboard(req.db);
  json(res, { dancefloor: d.dancefloor, settings: d.settings, command_queue: d.command_queue });
}, closeDb);

app.get("/api/emotes/sync", requireAuth, requireAnyPermission("emergency_controls", "manage_emotes"), (req, res) => {
  const d = readEmotesDashboard(req.db);
  json(res, { sync: d.sync, command_queue: d.command_queue });
}, closeDb);

app.get("/api/emotes/social", requireAuth, requireAnyPermission("emergency_controls", "manage_emotes"), (req, res) => {
  const d = readEmotesDashboard(req.db);
  json(res, { social: d.social, settings: d.settings });
}, closeDb);

app.get("/api/emotes/logs", requireAuth, requireAnyPermission("emergency_controls", "manage_emotes", "view_logs"), (req, res) => {
  const d = readEmotesDashboard(req.db);
  json(res, { audit_logs: d.audit_logs, room_social_logs: d.tables.room_social_logs, command_queue: d.command_queue });
}, closeDb);

function enqueueEmoteAction(req, res, { targetBot, actionName, payload, auditAction }) {
  if (!ALLOWED_BOT_COMMAND_ACTIONS.has(actionName)) return json(res, { error: "action_not_allowed" }, 400);
  if (JSON.stringify(payload || {}).length > 2000) return json(res, { error: "payload_too_large" }, 400);
  try {
    const queued = enqueueBotCommand(req.db, { targetBot, actionName, payload, requesterId: req.user.username });
    audit(req.db, req.user.username, auditAction || `${actionName}_enqueue`, "bot_command_queue", queued.id, "", { target_bot: targetBot, action: actionName, payload }, req.ip);
    return json(res, { ok: true, command: queued, message: "Command queued. Bot must consume bot_command_queue." });
  } catch (err) {
    return json(res, { error: err.message || "enqueue_failed" }, 500);
  }
}

app.post("/api/emotes/trigger", requireAuth, requireAnyPermission("emergency_controls", "manage_emotes"), (req, res) => {
  const targetBot = String(req.body?.target_bot || "dj").trim().slice(0, 80);
  const emote = String(req.body?.emote || "").trim();
  const duration = req.body?.duration;
  if (!emote) return json(res, { error: "emote_required" }, 400);
  return enqueueEmoteAction(req, res, { targetBot, actionName: "trigger_emote", payload: { emote, target: "self", duration }, auditAction: "emote_trigger_enqueue" });
}, closeDb);

app.post("/api/emotes/stop", requireAuth, requireAnyPermission("emergency_controls", "manage_emotes"), (req, res) => {
  const targetBot = String(req.body?.target_bot || "dj").trim().slice(0, 80);
  return enqueueEmoteAction(req, res, { targetBot, actionName: "stop_emote", payload: {}, auditAction: "emote_stop_enqueue" });
}, closeDb);

app.post("/api/dancefloor/command", requireAuth, requireAnyPermission("emergency_controls", "manage_emotes"), (req, res) => {
  const command = String(req.body?.command || "").trim();
  const actionMap = {
    start: "dancefloor_start",
    stop: "dancefloor_stop",
    clear: "dancefloor_clear",
    status: "dancefloor_status",
    sequence: "dancefloor_sequence",
    random: "dancefloor_random",
    randomtimed: "dancefloor_randomtimed",
  };
  const actionName = actionMap[command];
  if (!actionName) return json(res, { error: "command_not_allowed" }, 400);
  const payload = req.body?.payload && typeof req.body.payload === "object" && !Array.isArray(req.body.payload) ? req.body.payload : {};
  return enqueueEmoteAction(req, res, { targetBot: "dj", actionName, payload, auditAction: `dancefloor_${command}_enqueue` });
}, closeDb);

app.post("/api/sync/command", requireAuth, requireAnyPermission("emergency_controls", "manage_emotes"), (req, res) => {
  const command = String(req.body?.command || "").trim();
  const actionMap = { start: "sync_start", stop: "sync_stop", persist: "sync_persist" };
  const actionName = actionMap[command];
  if (!actionName) return json(res, { error: "command_not_allowed" }, 400);
  const payload = req.body?.payload && typeof req.body.payload === "object" && !Array.isArray(req.body.payload) ? req.body.payload : {};
  return enqueueEmoteAction(req, res, { targetBot: "dj", actionName, payload, auditAction: `sync_${command}_enqueue` });
}, closeDb);

app.post("/api/room/announce", requireAuth, requireAnyPermission("emergency_controls","manage_room","manage_events"), (req, res) => {
  const message = String(req.body?.message || "").trim();
  if (!message) return json(res, { error: "message_required" }, 400);
  if (message.length > 500) return json(res, { error: "message_too_long" }, 400);
  try {
    const queued = enqueueBotCommand(req.db, {
      targetBot: "host",
      actionName: "announce",
      payload: { message },
      requesterId: req.user.username,
    });
    audit(req.db, req.user.username, "room_announce_enqueue", "bot_command_queue", queued.id, "", { message }, req.ip);
    json(res, { ok: true, command: queued, message: "Command queued. Bot must consume bot_command_queue." });
  } catch (err) {
    json(res, { error: err.message || "enqueue_failed" }, 500);
  }
}, closeDb);

app.post("/api/room/announcements", requireAuth, requireAnyPermission("emergency_controls", "manage_room", "manage_events"), (req, res) => {
  if (!tableExists(req.db, "rotating_announcements")) return json(res, { error: "missing_table", message: "rotating_announcements is not present." }, 400);
  const cols = tableColumns(req.db, "rotating_announcements");
  const messageCol = ["message", "text", "body", "content"].find((col) => cols.includes(col));
  if (!messageCol) return json(res, { error: "unverified_schema", message: "No message/text/body/content column found." }, 400);
  const message = String(req.body?.message || "").trim();
  if (!message) return json(res, { error: "message_required" }, 400);
  if (message.length > 500) return json(res, { error: "message_too_long" }, 400);
  const insertCols = [messageCol];
  const values = [message];
  if (cols.includes("enabled")) { insertCols.push("enabled"); values.push("1"); }
  if (cols.includes("set_by")) { insertCols.push("set_by"); values.push(req.user.username); }
  if (cols.includes("created_by")) { insertCols.push("created_by"); values.push(req.user.username); }
  if (cols.includes("created_at")) insertCols.push("created_at");
  const placeholders = insertCols.map((col) => col === "created_at" ? "CURRENT_TIMESTAMP" : "?").join(", ");
  const info = req.db.prepare(`INSERT INTO rotating_announcements (${insertCols.map(sqlIdent).join(", ")}) VALUES (${placeholders})`).run(...values);
  audit(req.db, req.user.username, "room_rotating_announcement_create", "rotating_announcements", info.lastInsertRowid, "", { message }, req.ip);
  json(res, { ok: true, id: info.lastInsertRowid });
}, closeDb);

app.put("/api/room/announcements/:id", requireAuth, requireAnyPermission("emergency_controls", "manage_room", "manage_events"), (req, res) => {
  if (!tableExists(req.db, "rotating_announcements")) return json(res, { error: "missing_table" }, 400);
  const cols = tableColumns(req.db, "rotating_announcements");
  if (!cols.includes("id")) return json(res, { error: "unverified_schema", message: "rotating_announcements.id is required." }, 400);
  const id = String(req.params.id || "").trim();
  const old = req.db.prepare("SELECT * FROM rotating_announcements WHERE id=? LIMIT 1").get(id);
  if (!old) return json(res, { error: "not_found" }, 404);
  const updates = [];
  const values = [];
  const messageCol = ["message", "text", "body", "content"].find((col) => cols.includes(col));
  if (messageCol && req.body?.message !== undefined) {
    const message = String(req.body.message || "").trim();
    if (!message) return json(res, { error: "message_required" }, 400);
    updates.push(`${sqlIdent(messageCol)}=?`);
    values.push(message);
  }
  if (cols.includes("enabled") && req.body?.enabled !== undefined) {
    updates.push(`${sqlIdent("enabled")}=?`);
    values.push(req.body.enabled ? "1" : "0");
  }
  if (cols.includes("updated_at")) updates.push(`${sqlIdent("updated_at")}=CURRENT_TIMESTAMP`);
  if (!updates.length) return json(res, { error: "no_verified_updates" }, 400);
  values.push(id);
  req.db.prepare(`UPDATE rotating_announcements SET ${updates.join(", ")} WHERE id=?`).run(...values);
  const next = req.db.prepare("SELECT * FROM rotating_announcements WHERE id=? LIMIT 1").get(id);
  audit(req.db, req.user.username, "room_rotating_announcement_update", "rotating_announcements", id, old, next, req.ip);
  json(res, { ok: true, row: next });
}, closeDb);

/* ── Events ─────────────────────────────────────────── */
app.get("/api/events", requireAuth, requireAnyPermission("manage_events", "view_logs"), (req, res) => {
  const tables = {};
  for (const name of ["event_definitions","event_history","event_points","event_settings","event_votes","processed_events","scheduled_events"]) {
    tables[name] = roomTableInfo(req.db, name);
  }
  const settingsMap = Object.fromEntries((tables.event_settings.rows || []).map((row) => [row.key, row.value]));
  const eventCommands = safeRows(req.db, "bot_command_queue", BOT_COMMAND_QUEUE_COLUMNS, {
    where: "action IN ('event_start','event_stop','event_schedule')",
    orderBy: columnExists(req.db, "bot_command_queue", "created_at") ? "created_at DESC" : "",
    limit: "25",
  });
  json(res, {
    tables,
    definitions: tables.event_definitions.rows,
    history: tables.event_history.rows,
    points: tables.event_points.rows,
    settings: tables.event_settings.rows,
    settings_map: settingsMap,
    votes: tables.event_votes.rows,
    processed: tables.processed_events.rows,
    scheduled: tables.scheduled_events.rows,
    active_event: settingsMap.event_active === "1" ? { event_id: settingsMap.event_name, expires_at: settingsMap.event_expires_at } : null,
    command_queue: {
      pending: eventCommands.filter((row) => ["pending", "queued", "claimed", "running"].includes(String(row.status || ""))),
      recent: eventCommands,
    },
  });
}, closeDb);

app.get("/api/events/definitions", requireAuth, requireAnyPermission("manage_events", "view_logs"), (req, res) => {
  json(res, { definitions: roomTableInfo(req.db, "event_definitions").rows });
}, closeDb);

app.put("/api/events/settings", requireAuth, requireAnyPermission("emergency_controls", "manage_events"), (req, res) => {
  const updates = req.body?.updates && typeof req.body.updates === "object" ? req.body.updates : {};
  const source = String(req.body?.source || "event_settings");
  if (!["event_settings", "auto_event_settings"].includes(source)) return json(res, { error: "source_not_allowed" }, 400);
  if (!tableExists(req.db, source) || !columnExists(req.db, source, "key") || !columnExists(req.db, source, "value")) {
    return json(res, { error: "unverified_schema", message: `${source} key/value schema is required.` }, 400);
  }
  const blocked = new Set(["event_active", "event_name", "event_expires_at"]);
  const old = readKeyValueMap(req.db, source);
  const changed = {};
  for (const [key, value] of Object.entries(updates)) {
    const cleanKey = String(key || "").trim();
    if (!cleanKey || blocked.has(cleanKey)) continue;
    if (!Object.prototype.hasOwnProperty.call(old, cleanKey) && source === "event_settings") continue;
    upsertKeyValue(req.db, source, cleanKey, value);
    changed[cleanKey] = String(value);
  }
  if (!Object.keys(changed).length) return json(res, { error: "no_verified_updates" }, 400);
  audit(req.db, req.user.username, "event_settings_update", source, Object.keys(changed).join(","), old, changed, req.ip);
  json(res, { ok: true, updated: changed, settings: readKeyValueMap(req.db, source) });
}, closeDb);

function enqueueEventAction(req, res, actionName, payload) {
  if (JSON.stringify(payload || {}).length > 2000) return json(res, { error: "payload_too_large" }, 400);
  try {
    const queued = enqueueBotCommand(req.db, { targetBot: "host", actionName, payload, requesterId: req.user.username });
    audit(req.db, req.user.username, `${actionName}_enqueue`, "bot_command_queue", queued.id, "", payload, req.ip);
    return json(res, { ok: true, command: queued, message: "Command queued. Bot must consume bot_command_queue." });
  } catch (err) {
    return json(res, { error: err.message || "enqueue_failed" }, 500);
  }
}

app.post("/api/events/start", requireAuth, requireAnyPermission("emergency_controls", "manage_events"), (req, res) => {
  const eventId = String(req.body?.event_id || req.body?.id || "").trim().toLowerCase();
  const minutes = Number(req.body?.minutes || 30);
  if (!eventId) return json(res, { error: "event_id_required" }, 400);
  if (!Number.isFinite(minutes) || minutes < 1 || minutes > 480) return json(res, { error: "minutes_out_of_range" }, 400);
  return enqueueEventAction(req, res, "event_start", { event_id: eventId, minutes });
}, closeDb);

app.post("/api/events/stop", requireAuth, requireAnyPermission("emergency_controls", "manage_events"), (req, res) => {
  const target = String(req.body?.target || "all").trim().toLowerCase() || "all";
  return enqueueEventAction(req, res, "event_stop", { target });
}, closeDb);

app.post("/api/events/schedule", requireAuth, requireAnyPermission("emergency_controls", "manage_events"), (req, res) => {
  const eventId = String(req.body?.event_id || "").trim().toLowerCase();
  const startsAt = String(req.body?.starts_at || "").trim();
  const minutes = Number(req.body?.minutes || 30);
  if (!eventId || !startsAt) return json(res, { error: "event_id_and_starts_at_required" }, 400);
  if (!Number.isFinite(minutes) || minutes < 1 || minutes > 480) return json(res, { error: "minutes_out_of_range" }, 400);
  return enqueueEventAction(req, res, "event_schedule", { event_id: eventId, starts_at: startsAt, minutes });
}, closeDb);

/* ── Room Control (read) ────────────────────────────── */
app.get("/api/room-control", requireAuth, requireAnyPermission("manage_room", "manage_events", "manage_emotes", "view_logs"), (req, res) => {
  json(res, readRoomDashboard(req.db));
}, closeDb);

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
