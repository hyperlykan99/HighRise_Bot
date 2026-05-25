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
const DB_PATH = process.env.DB_PATH
  ? path.resolve(process.env.DB_PATH)
  : path.join(__dirname, "..", "artifacts", "highrise-bot", "highrise_hangout.db");

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
  execOptional(
    db,
    `
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

  const seedRole = db.prepare(
    "INSERT OR IGNORE INTO dashboard_roles (role, description) VALUES (?, ?)",
  );
  seedRole.run("owner", "Full dashboard access");
  seedRole.run("staff", "Limited staff access controlled by permission flags");

  for (const sql of [
    "ALTER TABLE bot_settings ADD COLUMN scope TEXT NOT NULL DEFAULT 'global'",
    "ALTER TABLE bot_settings ADD COLUMN module TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE bot_settings ADD COLUMN description TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE bot_settings ADD COLUMN updated_by TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE bot_settings ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
  ]) {
    try {
      db.prepare(sql).run();
    } catch (err) {
      if (!String(err.message || "").includes("duplicate column")) throw err;
    }
  }

  const seedFlag = db.prepare(
    "INSERT OR IGNORE INTO module_flags (module, enabled, reason, updated_by) VALUES (?, 1, '', 'system')",
  );
  for (const moduleName of ["radio", "casino", "games", "titles", "staff", "settings"]) {
    seedFlag.run(moduleName);
  }
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
    req.db = db;
    req.user = row;
    req.permissions = readUserPermissions(db, row);
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

function upsertDashboardSetting(db, key, value, moduleName, actor, description = "") {
  const old = getDashboardSetting(db, key);
  db.prepare(
    "INSERT INTO bot_settings (key, value, scope, module, description, updated_by, updated_at) VALUES (?, ?, 'global', ?, ?, ?, CURRENT_TIMESTAMP) " +
      "ON CONFLICT(key) DO UPDATE SET value=excluded.value, module=excluded.module, description=excluded.description, updated_by=excluded.updated_by, updated_at=CURRENT_TIMESTAMP",
  ).run(key, String(value), moduleName || "", description, actor);
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
  const hasYtJobs = tableExists(db, "yt_request_jobs");
  let nowPlaying = null;
  let queue = [];
  let recent = [];
  let counts = {};
  if (hasYtJobs) {
    nowPlaying =
      db
        .prepare(
          "SELECT id, title, COALESCE(artist,'') AS artist, username, user_id, status, azura_file_id, azura_song_id, filename, source_type, started_at, played_at " +
            "FROM yt_request_jobs WHERE status='playing' ORDER BY id DESC LIMIT 1",
        )
        .get() ?? null;
    const ph = UPCOMING_REQUEST_STATUSES.map(() => "?").join(",");
    queue = db
      .prepare(
        `SELECT id, title, COALESCE(artist,'') AS artist, username, user_id, status, azura_file_id, azura_song_id, filename, source_type, started_at ` +
          `FROM yt_request_jobs WHERE status IN (${ph}) ORDER BY priority DESC, id ASC LIMIT 50`,
      )
      .all(...UPCOMING_REQUEST_STATUSES)
      .map((row, i) => ({ ...row, pos: i + 1 }));
    recent = db
      .prepare(
        "SELECT id, title, username, status, played_at, cleaned_at FROM yt_request_jobs WHERE status IN ('played','cleaned','skipped') ORDER BY COALESCE(played_at, cleaned_at, started_at) DESC LIMIT 20",
      )
      .all();
    counts = Object.fromEntries(
      db
        .prepare("SELECT status, COUNT(*) AS n FROM yt_request_jobs GROUP BY status")
        .all()
        .map((r) => [r.status, r.n]),
    );
  }
  const radioUrl = AZURACAST_STREAM_URL ?? getSetting(db, "dj_radio_url", "");
  return {
    now_playing: nowPlaying,
    queue,
    recent,
    counts,
    terminal_statuses: TERMINAL_REQUEST_STATUSES,
    queue_open: getSetting(db, "requests_enabled", "true") !== "false",
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
  if (!tableExists(db, table)) return [];
  return db.prepare(sql).all(...params);
}

function oneOrNull(db, table, sql, ...params) {
  if (!tableExists(db, table)) return null;
  return db.prepare(sql).get(...params) ?? null;
}

const app = express();
app.disable("x-powered-by");
app.use(express.json({ limit: "128kb" }));

app.get("/api/healthz", (_req, res) => {
  res.json({
    status: "ok",
    service: "chilltopia-owner-dashboard",
    port: PORT,
    db_path: DB_PATH,
    db_path_present: !!DB_PATH,
    remote_status: !!REMOTE_STATUS_URL,
  });
});

app.post("/api/auth/login", async (req, res) => {
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
    return json(res, { user: publicUser(db, user) });
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
  json(res, { user: publicUser(req.db, req.user) });
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
  const bots = rowsOrEmpty(
    db,
    "bot_instances",
    "SELECT bot_id, bot_mode, bot_username, status, enabled, last_heartbeat_at, last_error, current_room_id FROM bot_instances ORDER BY bot_mode, bot_username",
  );
  const flags = db.prepare("SELECT * FROM module_flags ORDER BY module").all();
  const commandErrors = rowsOrEmpty(
    db,
    "command_error_logs",
    "SELECT * FROM command_error_logs ORDER BY id DESC LIMIT 10",
  );
  json(res, {
    bots,
    module_flags: flags,
    command_errors: commandErrors,
    radio: readLocalRadioStatus(db),
    updated_at: nowIso(),
  });
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
  if (!key || key.length > 120) return json(res, { error: "bad_key" }, 400);
  if (source === "room_settings") {
    const old = getSetting(req.db, key, "");
    setRoomSetting(req.db, key, value);
    audit(req.db, req.user.username, "room_setting_update", "room_settings", key, old, value, req.ip);
  } else {
    upsertDashboardSetting(req.db, key, value, moduleName, req.user.username);
  }
  json(res, { ok: true });
}, closeDb);

app.put("/api/modules/:module", requireAuth, requirePermission("emergency_controls"), (req, res) => {
  const moduleName = req.params.module.trim().toLowerCase();
  const enabled = req.body?.enabled ? 1 : 0;
  const reason = String(req.body?.reason ?? "");
  const old = req.db.prepare("SELECT * FROM module_flags WHERE module=?").get(moduleName) ?? null;
  req.db
    .prepare(
      "INSERT INTO module_flags (module, enabled, reason, updated_by, updated_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) " +
        "ON CONFLICT(module) DO UPDATE SET enabled=excluded.enabled, reason=excluded.reason, updated_by=excluded.updated_by, updated_at=CURRENT_TIMESTAMP",
    )
    .run(moduleName, enabled, reason, req.user.username);
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

app.get("/api/casino", requireAuth, requirePermission("manage_casino"), (req, res) => {
  const keys = rowsOrEmpty(
    req.db,
    "room_settings",
    "SELECT key, value FROM room_settings WHERE key LIKE 'casino%' OR key LIKE 'bj_%' OR key LIKE 'rbj_%' OR key LIKE 'poker%' OR key LIKE 'daily_%' ORDER BY key",
  );
  json(res, { settings: keys, module_flag: req.db.prepare("SELECT * FROM module_flags WHERE module='casino'").get() ?? null });
}, closeDb);

app.put("/api/casino/:key", requireAuth, requirePermission("manage_casino"), (req, res) => {
  const key = req.params.key.trim();
  const value = String(req.body?.value ?? "");
  if (!/^(casino|bj_|rbj_|poker|daily_)/.test(key)) return json(res, { error: "unsupported_casino_key" }, 400);
  upsertDashboardSetting(req.db, key, value, "casino", req.user.username, "Dashboard casino/game setting. Bot modules should read from bot_settings before room_settings.");
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
  const auditRows = req.db.prepare("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 250").all();
  const adminRows = rowsOrEmpty(req.db, "admin_action_logs", "SELECT * FROM admin_action_logs ORDER BY id DESC LIMIT 100");
  const commandErrors = rowsOrEmpty(req.db, "command_error_logs", "SELECT * FROM command_error_logs ORDER BY id DESC LIMIT 100");
  json(res, { audit_logs: auditRows, admin_action_logs: adminRows, command_error_logs: commandErrors });
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
    req.db.prepare(
      "INSERT INTO module_flags (module, enabled, reason, updated_by, updated_at) VALUES (?, 0, ?, ?, CURRENT_TIMESTAMP) " +
        "ON CONFLICT(module) DO UPDATE SET enabled=0, reason=excluded.reason, updated_by=excluded.updated_by, updated_at=CURRENT_TIMESTAMP",
    ).run(moduleName, "dashboard emergency", req.user.username);
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
    console.error("[DASHBOARD_DB] startup schema check failed:", err.message);
    console.error("[DASHBOARD_DB] Set DB_PATH to the shared bot SQLite file before using control APIs.");
  }
  app.listen(PORT, "0.0.0.0", () => {
    console.log(`[DASHBOARD] stage=dashboard_startup mode=owner_staff port=${PORT}`);
    console.log(`[DASHBOARD_CONFIG] db_path=${DB_PATH} port=${PORT}`);
    if (REMOTE_STATUS_URL) console.log(`[DASHBOARD] remote_status_url=${REMOTE_STATUS_URL}`);
    console.log(`[DASHBOARD] Open: http://localhost:${PORT}`);
  });
})();
