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
import { execFile } from "child_process";
import { promisify } from "util";

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
const execFileAsync = promisify(execFile);

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
const DB_BACKUP_DIR = "/opt/highrise-bots/artifacts/highrise-bot/backups/dashboard";
const DASHBOARD_BACKUP_DIR = "/opt/highrise-bots/dashboard/backups";
const APPROVED_BACKUP_DIRS = [DB_BACKUP_DIR, DASHBOARD_BACKUP_DIR].map((p) => path.resolve(p));

const PERMISSION_REGISTRY = {
  view_dashboard: { group: "System", label: "View Dashboard" },
  manage_radio: { group: "Radio", label: "Manage Radio" },
  manage_casino: { group: "Games", label: "Manage Casino" },
  manage_games: { group: "Games", label: "Manage Games" },
  manage_mining: { group: "Mining", label: "Manage Mining" },
  manage_fishing: { group: "Fishing", label: "Manage Fishing" },
  manage_room: { group: "Room", label: "Manage Room" },
  manage_events: { group: "Events", label: "Manage Events" },
  manage_automation: { group: "Room", label: "Manage Automation" },
  manage_emotes: { group: "Emotes", label: "Manage Emotes" },
  manage_players: { group: "Players", label: "Manage Players" },
  manage_economy: { group: "Economy", label: "Manage Economy" },
  manage_inventory: { group: "Players", label: "Manage Inventory" },
  manage_rewards: { group: "Economy", label: "Manage Rewards" },
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
  "dashboard_scheduled_announcements",
  "game_rarity_settings",
  "mining_item_weights",
  "fish_catalog",
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

function fileMeta(filePath) {
  try {
    const st = fs.statSync(filePath);
    return { path: filePath, exists: true, size: st.size, modified_at: st.mtime.toISOString(), mode: (st.mode & 0o777).toString(8) };
  } catch {
    return { path: filePath, exists: false, size: 0, modified_at: null, mode: null };
  }
}

function formatStamp(date = new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}_${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`;
}

function ensureDir(dirPath, mode = 0o700) {
  fs.mkdirSync(dirPath, { recursive: true, mode });
  try { fs.chmodSync(dirPath, mode); } catch {}
}

function isInsideDir(filePath, dirPath) {
  const resolvedFile = path.resolve(filePath);
  const resolvedDir = path.resolve(dirPath);
  return resolvedFile === resolvedDir || resolvedFile.startsWith(`${resolvedDir}${path.sep}`);
}

function isApprovedBackupPath(filePath, dirs = [DB_BACKUP_DIR]) {
  return dirs.some((dir) => isInsideDir(filePath, dir));
}

function envMetadata(filePath = VPS_ENV_PATH) {
  const meta = fileMeta(filePath);
  const tokenKeys = {};
  const keyPattern = /(?:TOKEN|SECRET|KEY|PASSWORD|PASS|AZURA|HIGHRISE)/i;
  if (meta.exists) {
    try {
      for (const line of fs.readFileSync(filePath, "utf8").split(/\r?\n/)) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
        const eq = trimmed.indexOf("=");
        const key = trimmed.slice(0, eq).trim();
        const value = trimmed.slice(eq + 1).trim().replace(/^['"]|['"]$/g, "");
        if (keyPattern.test(key)) tokenKeys[key] = value ? "SET" : "EMPTY";
      }
    } catch (err) {
      meta.read_error = err.message;
    }
  }
  return { env_path: filePath, file_exists: meta.exists, modified_at: meta.modified_at, size: meta.size, token_keys: tokenKeys };
}

function backupFileList() {
  const rows = [];
  for (const dir of APPROVED_BACKUP_DIRS) {
    if (!fs.existsSync(dir)) continue;
    for (const name of fs.readdirSync(dir)) {
      const full = path.join(dir, name);
      const meta = fileMeta(full);
      if (!meta.exists) continue;
      rows.push({
        ...meta,
        name,
        folder: dir,
        type: name.endsWith(".db") ? "sqlite_db" : name.includes(".env") ? "env_metadata" : "dashboard_file",
      });
    }
  }
  return rows.sort((a, b) => String(b.modified_at || "").localeCompare(String(a.modified_at || "")));
}

function dbSidecarFiles(dbPath = DB_PATH) {
  return [`${dbPath}-wal`, `${dbPath}-shm`].map(fileMeta);
}

function rowCountSafe(db, table) {
  try {
    if (!tableExists(db, table)) return null;
    return db.prepare(`SELECT COUNT(*) AS n FROM ${sqlIdent(table)}`).get().n ?? 0;
  } catch {
    return null;
  }
}

function dbHealthSnapshot(db) {
  const dbMeta = fileMeta(DB_PATH);
  const sidecars = dbSidecarFiles();
  let integrity = "not_run";
  let quick = "not_run";
  try { integrity = db.prepare("PRAGMA integrity_check").get()?.integrity_check ?? "unknown"; } catch (err) { integrity = `error:${err.message}`; }
  try { quick = db.prepare("PRAGMA quick_check").get()?.quick_check ?? "unknown"; } catch (err) { quick = `error:${err.message}`; }
  const tables = tableNames(db);
  const largest = tables.map((name) => ({ table: name, rows: rowCountSafe(db, name) ?? 0 }))
    .sort((a, b) => b.rows - a.rows)
    .slice(0, 15);
  const commandPending = tableExists(db, "bot_command_queue") && columnExists(db, "bot_command_queue", "status")
    ? db.prepare("SELECT COUNT(*) AS n FROM bot_command_queue WHERE status IN ('pending','queued','claimed','running')").get().n
    : 0;
  const commandFailed = tableExists(db, "bot_command_queue") && columnExists(db, "bot_command_queue", "status")
    ? db.prepare(`SELECT COUNT(*) AS n FROM bot_command_queue WHERE ${activeFailedCommandWhere(db)}`).get().n
    : 0;
  const staleBots = tableExists(db, "bot_instances") && columnExists(db, "bot_instances", "last_heartbeat_at")
    ? db.prepare("SELECT COUNT(*) AS n FROM bot_instances WHERE last_heartbeat_at IS NULL OR datetime(last_heartbeat_at) < datetime('now','-10 minutes')").get().n
    : 0;
  const failedRadio = tableExists(db, "yt_request_jobs") && columnExists(db, "yt_request_jobs", "status")
    ? db.prepare("SELECT COUNT(*) AS n FROM yt_request_jobs WHERE status IN ('failed','failed_download','error','cancelled')").get().n
    : 0;
  const orphanedOwnedItems = tableExists(db, "owned_items") && tableExists(db, "users")
    ? db.prepare("SELECT COUNT(*) AS n FROM owned_items oi LEFT JOIN users u ON u.user_id=oi.user_id WHERE u.user_id IS NULL").get().n
    : null;
  const warnings = [];
  if (integrity !== "ok") warnings.push({ level: "error", message: `SQLite integrity_check: ${integrity}` });
  if (quick !== "ok") warnings.push({ level: "warn", message: `SQLite quick_check: ${quick}` });
  if ((sidecars.find((f) => f.path.endsWith("-wal"))?.size || 0) > 50 * 1024 * 1024) warnings.push({ level: "warn", message: "WAL file is larger than 50 MB." });
  if (commandFailed > 0) warnings.push({ level: "warn", message: `${commandFailed} failed bot command queue rows.` });
  return {
    db_path: DB_PATH,
    db_file: dbMeta,
    sidecars,
    wal_size: sidecars.find((f) => f.path.endsWith("-wal"))?.size || 0,
    integrity_check: integrity,
    quick_check: quick,
    table_count: tables.length,
    largest_tables: largest,
    stale_bot_instances: staleBots,
    pending_bot_command_queue: commandPending,
    failed_bot_command_queue: commandFailed,
    failed_yt_request_jobs: failedRadio,
    orphaned_inventory_rows: orphanedOwnedItems,
    warnings,
    warning_count: warnings.length,
  };
}

function cleanupPreview(db, retentionDays = 30) {
  const days = Math.max(1, Math.min(365, Number(retentionDays) || 30));
  const candidates = [];
  const oldDateSql = `datetime('now','-${days} days')`;
  if (tableExists(db, "bot_instances")) {
    const duplicateRows = safeRows(db, "bot_instances", ["bot_username", "bot_mode"], { limit: "1000" });
    const seen = new Map();
    for (const row of duplicateRows) {
      const key = `${row.bot_username || ""}:${row.bot_mode || ""}`;
      seen.set(key, (seen.get(key) || 0) + 1);
    }
    const duplicateCount = [...seen.values()].reduce((sum, n) => sum + Math.max(0, n - 1), 0);
    candidates.push({ cleanup_type: "stale_bot_instances", description: "Duplicate/stale bot_instances rows", count: duplicateCount, dry_run_only: true });
  }
  if (tableExists(db, "bot_command_queue") && columnExists(db, "bot_command_queue", "created_at") && columnExists(db, "bot_command_queue", "status")) {
    const oldCompleted = db.prepare(`SELECT COUNT(*) AS n FROM bot_command_queue WHERE status='completed' AND datetime(created_at) < ${oldDateSql}`).get().n;
    const oldFailed = db.prepare(`SELECT COUNT(*) AS n FROM bot_command_queue WHERE status='failed' AND datetime(created_at) < ${oldDateSql}`).get().n;
    candidates.push({ cleanup_type: "old_completed_bot_commands", description: `Completed bot commands older than ${days} days`, count: oldCompleted, dry_run_only: true });
    candidates.push({ cleanup_type: "old_failed_bot_commands", description: `Failed bot commands older than ${days} days`, count: oldFailed, dry_run_only: true });
  }
  if (tableExists(db, "yt_request_jobs") && columnExists(db, "yt_request_jobs", "status")) {
    const failedRadio = db.prepare("SELECT COUNT(*) AS n FROM yt_request_jobs WHERE status IN ('failed','failed_download','error','cancelled')").get().n;
    candidates.push({ cleanup_type: "failed_radio_jobs", description: "Terminal failed/cancelled radio request jobs", count: failedRadio, dry_run_only: true });
  }
  const backups = backupFileList();
  const cutoff = Date.now() - days * 86400 * 1000;
  const oldBackups = backups.filter((b) => b.modified_at && new Date(b.modified_at).getTime() < cutoff).length;
  candidates.push({ cleanup_type: "old_backup_files", description: `Backup files older than ${days} days`, count: oldBackups, dry_run_only: true });
  return { retention_days: days, candidates, note: "DB row cleanup is dry-run only in this dashboard build. No rows are deleted by preview." };
}

async function pm2Snapshot() {
  try {
    const { stdout } = await execFileAsync("pm2", ["jlist"], { timeout: 2500, maxBuffer: 1024 * 1024 });
    const list = safeJsonParse(stdout, []);
    return {
      available: true,
      processes: (Array.isArray(list) ? list : []).map((p) => ({
        name: p.name,
        pm_id: p.pm_id,
        status: p.pm2_env?.status,
        restart_time: p.pm2_env?.restart_time,
        uptime: p.pm2_env?.pm_uptime,
        memory: p.monit?.memory,
        cpu: p.monit?.cpu,
      })),
    };
  } catch (err) {
    return { available: false, error: err.code === "ENOENT" ? "pm2_not_found" : err.message, processes: [] };
  }
}

const RELEASE_GIT_COMMANDS = new Set([
  "rev-parse --abbrev-ref HEAD",
  "rev-parse HEAD",
  "rev-parse origin/dashboard-redesign",
  "status --short",
  "status --short --branch",
  "log --oneline -12",
  "diff --name-only",
]);

async function safeGit(args, fallback = "") {
  const key = args.join(" ");
  if (!RELEASE_GIT_COMMANDS.has(key)) return { ok: false, stdout: fallback, error: "git_command_not_allowed" };
  try {
    const { stdout } = await execFileAsync("git", args, { cwd: path.resolve(__dirname, ".."), timeout: 3500, maxBuffer: 512 * 1024 });
    return { ok: true, stdout: stdout.trim(), error: "" };
  } catch (err) {
    return { ok: false, stdout: fallback, error: err.message };
  }
}

async function gitReleaseSnapshot() {
  const [branch, local, remote, statusShort, statusBranch, log, diffNames] = await Promise.all([
    safeGit(["rev-parse", "--abbrev-ref", "HEAD"]),
    safeGit(["rev-parse", "HEAD"]),
    safeGit(["rev-parse", "origin/dashboard-redesign"]),
    safeGit(["status", "--short"]),
    safeGit(["status", "--short", "--branch"]),
    safeGit(["log", "--oneline", "-12"]),
    safeGit(["diff", "--name-only"]),
  ]);
  const statusLine = statusBranch.stdout.split(/\r?\n/)[0] || "";
  const localCommit = local.stdout || null;
  const remoteCommit = remote.stdout || null;
  const dirtyFiles = statusShort.stdout ? statusShort.stdout.split(/\r?\n/).filter(Boolean) : [];
  const changedFiles = diffNames.stdout ? diffNames.stdout.split(/\r?\n/).filter(Boolean) : [];
  let sync_status = "unknown";
  if (localCommit && remoteCommit && localCommit === remoteCommit && !dirtyFiles.length) sync_status = "synced";
  else if (localCommit && remoteCommit && localCommit === remoteCommit && dirtyFiles.length) sync_status = "dirty";
  else if (statusLine.includes("[ahead") || statusLine.includes("[behind")) sync_status = statusLine.match(/\[(.+)\]/)?.[1] || "different";
  else if (localCommit && remoteCommit && localCommit !== remoteCommit) sync_status = "different";
  return {
    branch: branch.stdout || "unknown",
    local_commit: localCommit,
    remote_commit: remoteCommit,
    sync_status,
    dirty: dirtyFiles.length > 0,
    dirty_files: dirtyFiles,
    changed_files: changedFiles,
    status_short: statusShort.stdout,
    status_branch: statusBranch.stdout,
    recent_commits: (log.stdout ? log.stdout.split(/\r?\n/) : []).map((line) => {
      const [sha, ...rest] = line.split(" ");
      return { sha, message: rest.join(" ") };
    }),
    errors: [branch, local, remote, statusShort, statusBranch, log, diffNames].filter((r) => !r.ok).map((r) => r.error).filter(Boolean),
  };
}

async function createDashboardDbBackup(db, actor, ip, action = "maintenance_db_backup") {
  const stamp = formatStamp();
  ensureDir(DB_BACKUP_DIR);
  const dest = path.join(DB_BACKUP_DIR, `highrise_hangout.dashboard_backup_${stamp}.db`);
  try {
    try { db.prepare("PRAGMA wal_checkpoint(PASSIVE)").get(); } catch {}
    await db.backup(dest);
    for (const sidecar of [`${DB_PATH}-wal`, `${DB_PATH}-shm`]) {
      if (!fs.existsSync(sidecar)) continue;
      fs.copyFileSync(sidecar, path.join(DB_BACKUP_DIR, `${path.basename(dest)}${sidecar.endsWith("-wal") ? "-wal" : "-shm"}`));
    }
    audit(db, actor, action, "backup_file", dest, "", { db_path: DB_PATH, backup: dest }, ip);
    return { ok: true, backup: fileMeta(dest), sidecars: dbSidecarFiles(dest), message: "SQLite DB backup created." };
  } catch (err) {
    audit(db, actor, `${action}_failed`, "backup_file", dest, "", err.message, ip);
    return { ok: false, error: "backup_failed", message: err.message, status: 500 };
  }
}

async function maintenanceOverview(db) {
  const backups = backupFileList();
  const health = dbHealthSnapshot(db);
  const pm2 = await pm2Snapshot();
  const dashboardProc = pm2.processes.find((p) => /dashboard/i.test(p.name || "")) || null;
  const botProcs = pm2.processes.filter((p) => /bot|chilltopia|highrise/i.test(p.name || ""));
  return {
    db_path: DB_PATH,
    db_file: fileMeta(DB_PATH),
    wal_file: fileMeta(`${DB_PATH}-wal`),
    last_backup: backups.find((b) => b.type === "sqlite_db") || null,
    backup_count: backups.length,
    last_dashboard_restart: dashboardProc?.uptime ? new Date(dashboardProc.uptime).toISOString() : null,
    last_bot_restart: botProcs.map((p) => p.uptime).filter(Boolean).sort()[0] ? new Date(botProcs.map((p) => p.uptime).filter(Boolean).sort()[0]).toISOString() : null,
    pm2_dashboard_status: dashboardProc?.status || (pm2.available ? "not_found" : "pm2_unavailable"),
    pm2_bot_status: botProcs.map((p) => ({ name: p.name, status: p.status, restart_time: p.restart_time })),
    sqlite_integrity_status: health.integrity_check,
    wal_size: health.wal_size,
    warning_count: health.warning_count,
    warnings: health.warnings,
    approved_backup_folders: APPROVED_BACKUP_DIRS,
  };
}

async function readReleaseStatus(db) {
  const [git, pm2, operations] = await Promise.all([
    gitReleaseSnapshot(),
    pm2Snapshot(),
    readOperationsSnapshot(db),
  ]);
  const qa = buildQaAudit();
  const e2e = buildE2eAudit(db);
  const dbHealth = dbHealthSnapshot(db);
  const backups = backupFileList();
  const latestBackup = backups.find((b) => b.type === "sqlite_db") || null;
  const backupFresh = latestBackup && Date.now() - new Date(latestBackup.modified_at).getTime() <= 24 * 60 * 60 * 1000;
  const dashboardProc = pm2.processes.find((p) => /dashboard/i.test(p.name || "")) || null;
  const botProcs = pm2.processes.filter((p) => /bot|chilltopia|highrise/i.test(p.name || ""));
  const blockers = [];
  const warnings = [];
  if (git.dirty) blockers.push("Working tree has uncommitted changes.");
  if (git.sync_status !== "synced") warnings.push(`Git sync status is ${git.sync_status}.`);
  if ((e2e.critical_issues || []).length) blockers.push(`${e2e.critical_issues.length} E2E critical issue(s).`);
  if (dbHealth.integrity_check && dbHealth.integrity_check !== "ok") blockers.push(`SQLite integrity_check is ${dbHealth.integrity_check}.`);
  if (!latestBackup) warnings.push("No release DB backup found.");
  else if (!backupFresh) warnings.push("Latest DB backup is older than 24 hours.");
  if ((operations.queue?.counts?.failed || 0) > 0) warnings.push(`${operations.queue.counts.failed} failed command queue row(s) need review.`);
  if (operations.overview?.system_status === "CRITICAL") blockers.push("Operations Center reports CRITICAL health.");
  else if (operations.overview?.system_status === "WARNING") warnings.push("Operations Center reports WARNING health.");
  const readiness = blockers.length ? "BLOCKED" : warnings.length ? "WARNING" : "READY";
  return {
    generated_at: nowIso(),
    readiness,
    blockers,
    warnings,
    git,
    current_version: {
      local_dashboard_commit: git.local_commit,
      remote_origin_dashboard_redesign_commit: git.remote_commit,
      branch: git.branch,
      sync_status: git.sync_status,
      dirty: git.dirty,
      dirty_files: git.dirty_files,
      changed_files: git.changed_files,
    },
    pm2: {
      available: pm2.available,
      dashboard_status: dashboardProc?.status || (pm2.available ? "not_found" : "pm2_unavailable"),
      bot_status: botProcs.map((p) => ({ name: p.name, status: p.status, restart_time: p.restart_time, uptime: p.uptime })),
      processes: pm2.processes,
    },
    backup_status: {
      latest_db_backup: latestBackup,
      db_backup_fresh: !!backupFresh,
      backup_count: backups.length,
    },
    audits: {
      e2e_critical_count: (e2e.critical_issues || []).length,
      e2e_warning_count: (e2e.warnings || []).length,
      qa_issue_count: (qa.issues || []).length,
      qa_critical_count: (qa.issues || []).filter((i) => i.severity === "CRITICAL").length,
      e2e_status: (e2e.critical_issues || []).length ? "CRITICAL" : ((e2e.warnings || []).length ? "WARNING" : "PASS"),
      qa_status: (qa.issues || []).some((i) => i.severity === "CRITICAL") ? "CRITICAL" : ((qa.issues || []).length ? "WARNING" : "PASS"),
    },
    operations: {
      system_status: operations.overview?.system_status,
      bots_online: operations.overview?.bots_online,
      bots_total: operations.overview?.bots_total,
      radio_status: operations.overview?.radio_status,
      command_queue_pending: operations.overview?.command_queue_pending,
      command_queue_failed: operations.overview?.command_queue_failed,
      last_restart: operations.overview?.last_restart,
      alerts: operations.alerts || [],
    },
    database: {
      db_path: DB_PATH,
      integrity_check: dbHealth.integrity_check,
      quick_check: dbHealth.quick_check,
      db_file: dbHealth.db_file,
      wal_size: dbHealth.wal_size,
    },
    rollback_guide: releaseRollbackGuide(git),
    raw: { git, dbHealth, qa_summary: qa.issues, e2e_summary: e2e.critical_issues, operations_overview: operations.overview },
  };
}

function releaseRollbackGuide(git) {
  const previous = git.recent_commits?.[1]?.sha || "<previous_commit_sha>";
  return [
    { title: "Rollback dashboard files to previous commit", command: `git fetch origin && git checkout ${previous} -- dashboard/server.mjs dashboard/public/app.js dashboard/public/styles.css dashboard/public/index.html` },
    { title: "Rollback runtime files to previous commit", command: `git fetch origin && git checkout ${previous} -- artifacts/highrise-bot` },
    { title: "Restore DB backup preview", command: "Use System → Maintenance Center → Restore, select the approved DB backup, then type RESTORE DATABASE." },
    { title: "Restart dashboard", command: "pm2 restart ChillTopia-Dashboard" },
    { title: "Restart bots", command: "pm2 restart ChillTopia-8Bots" },
  ];
}

async function runReleaseChecklist(db) {
  const checks = [];
  const add = (item, status, detail = "") => checks.push({ item, status, detail });
  const runCheck = async (item, command, args) => {
    try {
      const { stderr } = await execFileAsync(command, args, { cwd: path.resolve(__dirname, ".."), timeout: 8000, maxBuffer: 512 * 1024 });
      add(item, "PASS", stderr.trim());
    } catch (err) {
      add(item, "FAIL", err.message);
    }
  };
  await runCheck("Dashboard syntax passed", "node", ["--check", path.join(__dirname, "server.mjs")]);
  await runCheck("Public app syntax passed", "node", ["--check", path.join(PUBLIC_DIR, "app.js")]);
  if (fs.existsSync(path.join(BOT_ROOT, "main.py"))) {
    await runCheck("Bot Python syntax passed", "python3", ["-m", "py_compile", path.join(BOT_ROOT, "main.py")]);
  } else add("Bot Python syntax passed", "SKIP", "main.py not found");
  const e2e = buildE2eAudit(db);
  add("E2E Audit critical = 0", (e2e.critical_issues || []).length ? "FAIL" : "PASS", `${(e2e.critical_issues || []).length} critical`);
  const queue = readOperationsQueue(db);
  add("Queue failed = 0 or reviewed", (queue.counts?.failed || 0) ? "WARN" : "PASS", `${queue.counts?.failed || 0} failed`);
  const latestBackup = backupFileList().find((b) => b.type === "sqlite_db") || null;
  add("DB backup created", latestBackup ? "PASS" : "WARN", latestBackup?.modified_at || "No DB backup found");
  const publicRoutes = ["home", "radio", "how-to-play", "casino", "mining", "fishing", "events", "rankings", "room-info"];
  add("Public pages load", publicRoutes.every((name) => routeExists(`/api/public/${name}`)) ? "PASS" : "FAIL", "Public API route registry");
  add("Owner dashboard loads", routeExists("/api/overview") && routeExists("/api/healthz") ? "PASS" : "FAIL", "Owner core API routes");
  const ops = await readOperationsSnapshot(db);
  add("Bots online", ops.overview?.bots_online === ops.overview?.bots_total ? "PASS" : "WARN", `${ops.overview?.bots_online || 0}/${ops.overview?.bots_total || 0}`);
  add("Radio online", String(ops.overview?.radio_status || "").toLowerCase() === "online" ? "PASS" : "WARN", ops.overview?.radio_status || "unknown");
  add("Mining works", routeExists("/api/mining") && tableExists(db, "mining_items") ? "PASS" : "WARN", "Dashboard/runtime source available");
  add("Fishing works", routeExists("/api/fishing") && tableExists(db, "fish_catalog") ? "PASS" : "WARN", "Dashboard/runtime source available");
  add("Player search works", routeExists("/api/player/search") && tableExists(db, "users") ? "PASS" : "WARN", "users table and API route");
  return { generated_at: nowIso(), checks, status: checks.some((c) => c.status === "FAIL") ? "FAIL" : checks.some((c) => c.status === "WARN") ? "WARN" : "PASS" };
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

    CREATE TABLE IF NOT EXISTS dashboard_scheduled_announcements (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL DEFAULT '',
      message TEXT NOT NULL,
      target_bot TEXT NOT NULL DEFAULT 'host',
      schedule_type TEXT NOT NULL DEFAULT 'manual',
      interval_minutes INTEGER,
      next_run_at TEXT,
      last_sent_at TEXT,
      enabled INTEGER NOT NULL DEFAULT 1,
      archived INTEGER NOT NULL DEFAULT 0,
      created_by TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS game_rarity_settings (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      system TEXT NOT NULL,
      rarity TEXT NOT NULL,
      base_weight REAL,
      base_chance REAL,
      enabled INTEGER NOT NULL DEFAULT 1,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(system, rarity)
    );

    CREATE TABLE IF NOT EXISTS mining_item_weights (
      item_id TEXT PRIMARY KEY,
      drop_weight REAL DEFAULT 1,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS fish_catalog (
      fish_id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      rarity TEXT NOT NULL,
      base_value INTEGER DEFAULT 0,
      min_weight REAL,
      max_weight REAL,
      catch_weight REAL DEFAULT 1,
      catch_enabled INTEGER DEFAULT 1,
      event_only INTEGER DEFAULT 0,
      emoji TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
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

  addColumnIfMissing(db, "dashboard_scheduled_announcements", "title", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "dashboard_scheduled_announcements", "message", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "dashboard_scheduled_announcements", "target_bot", "TEXT NOT NULL DEFAULT 'host'", migration);
  addColumnIfMissing(db, "dashboard_scheduled_announcements", "schedule_type", "TEXT NOT NULL DEFAULT 'manual'", migration);
  addColumnIfMissing(db, "dashboard_scheduled_announcements", "interval_minutes", "INTEGER", migration);
  addColumnIfMissing(db, "dashboard_scheduled_announcements", "next_run_at", "TEXT", migration);
  addColumnIfMissing(db, "dashboard_scheduled_announcements", "last_sent_at", "TEXT", migration);
  addColumnIfMissing(db, "dashboard_scheduled_announcements", "enabled", "INTEGER NOT NULL DEFAULT 1", migration);
  addColumnIfMissing(db, "dashboard_scheduled_announcements", "archived", "INTEGER NOT NULL DEFAULT 0", migration);
  addColumnIfMissing(db, "dashboard_scheduled_announcements", "created_by", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "dashboard_scheduled_announcements", "created_at", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "dashboard_scheduled_announcements", "updated_at", "TEXT NOT NULL DEFAULT ''", migration);

  addColumnIfMissing(db, "game_rarity_settings", "system", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "game_rarity_settings", "rarity", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "game_rarity_settings", "base_weight", "REAL", migration);
  addColumnIfMissing(db, "game_rarity_settings", "base_chance", "REAL", migration);
  addColumnIfMissing(db, "game_rarity_settings", "enabled", "INTEGER NOT NULL DEFAULT 1", migration);
  addColumnIfMissing(db, "game_rarity_settings", "updated_at", "TEXT NOT NULL DEFAULT ''", migration);

  addColumnIfMissing(db, "mining_item_weights", "item_id", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "mining_item_weights", "drop_weight", "REAL DEFAULT 1", migration);
  addColumnIfMissing(db, "mining_item_weights", "updated_at", "TEXT NOT NULL DEFAULT ''", migration);

  addColumnIfMissing(db, "fish_catalog", "fish_id", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "fish_catalog", "name", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "fish_catalog", "rarity", "TEXT NOT NULL DEFAULT 'common'", migration);
  addColumnIfMissing(db, "fish_catalog", "base_value", "INTEGER DEFAULT 0", migration);
  addColumnIfMissing(db, "fish_catalog", "min_weight", "REAL", migration);
  addColumnIfMissing(db, "fish_catalog", "max_weight", "REAL", migration);
  addColumnIfMissing(db, "fish_catalog", "catch_weight", "REAL DEFAULT 1", migration);
  addColumnIfMissing(db, "fish_catalog", "catch_enabled", "INTEGER DEFAULT 1", migration);
  addColumnIfMissing(db, "fish_catalog", "event_only", "INTEGER DEFAULT 0", migration);
  addColumnIfMissing(db, "fish_catalog", "emoji", "TEXT", migration);
  addColumnIfMissing(db, "fish_catalog", "created_at", "TEXT DEFAULT CURRENT_TIMESTAMP", migration);
  addColumnIfMissing(db, "fish_catalog", "updated_at", "TEXT DEFAULT CURRENT_TIMESTAMP", migration);
  if (tableExists(db, "mining_items") && tableExists(db, "mining_item_weights")) {
    db.prepare(`
      INSERT OR IGNORE INTO mining_item_weights (item_id, drop_weight, updated_at)
      SELECT item_id, 1, datetime('now')
      FROM mining_items
      WHERE item_type='ore'
    `).run();
  }
  if (tableExists(db, "game_rarity_settings")) {
    const seedRarity = db.prepare(`
      INSERT OR IGNORE INTO game_rarity_settings (system, rarity, base_weight, base_chance, enabled, updated_at)
      VALUES ('mining', ?, ?, ?, 1, datetime('now'))
    `);
    for (const rarity of MINING_RARITY_ORDER) {
      const weight = Number(MINING_RARITY_PROBS[rarity] || 1);
      seedRarity.run(rarity, weight, weight);
    }
  }
  if (tableExists(db, "fish_catalog")) {
    try {
      const { fish } = readFishingCodeCatalog();
      const seedFish = db.prepare(`
        INSERT OR IGNORE INTO fish_catalog
          (fish_id, name, rarity, base_value, min_weight, max_weight, catch_weight, catch_enabled, event_only, emoji, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, ?, datetime('now'), datetime('now'))
      `);
      const rarityWeights = {};
      for (const row of fish) {
        const fishId = String(row.fish_id || "").trim();
        if (!fishId) continue;
        const rarity = normalizeRarity(row.rarity);
        const weight = Number(row.drop_weight || row.catch_weight || 1);
        rarityWeights[rarity] = (rarityWeights[rarity] || 0) + Math.max(0, weight);
        seedFish.run(fishId, row.name || fishId, rarity, Math.trunc(Number(row.base_value || 0)), row.min_weight ?? null, row.max_weight ?? null, weight, row.emoji || "");
      }
      const seedFishingRarity = db.prepare(`
        INSERT OR IGNORE INTO game_rarity_settings (system, rarity, base_weight, base_chance, enabled, updated_at)
        VALUES ('fishing', ?, ?, ?, 1, datetime('now'))
      `);
      const seedFishingRarities = ["common", "uncommon", "epic", "legendary", "mythic", "prismatic", "exotic"];
      for (const rarity of [...Object.keys(rarityWeights), ...seedFishingRarities.filter((rarity) => !Object.prototype.hasOwnProperty.call(rarityWeights, rarity))]) {
        const weight = Number(rarityWeights[rarity] || 0);
        seedFishingRarity.run(rarity, weight, weight);
      }
    } catch {}
  }

  addColumnIfMissing(db, "schema_version", "component", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "schema_version", "version", "INTEGER NOT NULL DEFAULT 1", migration);
  addColumnIfMissing(db, "schema_version", "updated_at", "TEXT NOT NULL DEFAULT ''", migration);

  addColumnIfMissing(db, "bot_command_queue", "result_text", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "bot_command_queue", "error_text", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "bot_command_queue", "reviewed_at", "TEXT NOT NULL DEFAULT ''", migration);
  addColumnIfMissing(db, "bot_command_queue", "reviewed_by", "TEXT NOT NULL DEFAULT ''", migration);

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

const RADIO_SETTING_DEFAULTS = {
  music_shop_enabled: ["true", "bool"],
  music_disc_display_name: ["Song Request 💽", "str"],
  music_disc_price_coins: ["500", "int"],
  music_disc_price_luxe: ["50", "int"],
  music_disc_purchase_coins_enabled: ["true", "bool"],
  music_disc_purchase_luxe_enabled: ["true", "bool"],
  music_disc_max_purchase_per_command: ["10", "int"],
  music_disc_daily_purchase_limit: ["50", "int"],
  request_disc_cost_normal: ["1", "int"],
  request_disc_cost_vip: ["1", "int"],
  request_disc_cost_staff: ["0", "int"],
  request_disc_cost_owner: ["0", "int"],
  radio_enabled: ["true", "bool"],
  radio_poll_interval_secs: ["3", "int"],
  radio_submit_ready_immediately: ["true", "bool"],
  radio_request_prequeue_enabled: ["true", "bool"],
  radio_request_prequeue_count: ["1", "int"],
  now_announce_song_changes: ["true", "bool"],
  now_announce_autodj: ["true", "bool"],
  now_announce_requests: ["true", "bool"],
  now_command_response_mode: ["whisper", "str"],
  now_show_progress_bar: ["true", "bool"],
  now_show_likes_dislikes: ["true", "bool"],
  now_show_request_play_count: ["true", "bool"],
  now_footer_text: ["🎶 !play to request a song", "str"],
  max_song_duration_normal_secs: ["300", "int"],
  max_song_duration_vip_secs: ["480", "int"],
  max_song_duration_staff_secs: ["600", "int"],
  max_song_duration_owner_secs: ["0", "int"],
  youtube_direct_url_enabled: ["true", "bool"],
  youtube_search_enabled: ["true", "bool"],
  youtube_search_result_count: ["5", "int"],
  youtube_search_session_timeout_secs: ["120", "int"],
  radio_favorites_enabled: ["true", "bool"],
  radio_favorites_max_per_user: ["25", "int"],
  youtube_reject_playlists: ["true", "bool"],
  youtube_reject_mixes: ["true", "bool"],
  youtube_reject_livestreams: ["true", "bool"],
  youtube_reject_shorts: ["false", "bool"],
  block_requests_when_azura_unhealthy: ["true", "bool"],
};

function ensureRadioSettings(db) {
  db.prepare(`CREATE TABLE IF NOT EXISTS radio_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    value_type TEXT,
    updated_by TEXT,
    updated_at TEXT
  )`).run();
  const stmt = db.prepare(`INSERT OR IGNORE INTO radio_settings
    (key, value, value_type, updated_by, updated_at)
    VALUES (?, ?, ?, 'system', datetime('now'))`);
  for (const [key, [value, type]] of Object.entries(RADIO_SETTING_DEFAULTS)) {
    stmt.run(key, value, type);
  }
}

function getRadioSetting(db, key, fallback = "") {
  try {
    ensureRadioSettings(db);
    const row = db.prepare("SELECT value FROM radio_settings WHERE key=? LIMIT 1").get(key);
    return row?.value ?? fallback;
  } catch {
    return fallback;
  }
}

function setRadioSetting(db, key, value, valueType = "str", updatedBy = "dashboard") {
  ensureRadioSettings(db);
  db.prepare(`INSERT INTO radio_settings (key, value, value_type, updated_by, updated_at)
    VALUES (?, ?, ?, ?, datetime('now'))
    ON CONFLICT(key) DO UPDATE SET
      value=excluded.value,
      value_type=excluded.value_type,
      updated_by=excluded.updated_by,
      updated_at=datetime('now')`).run(key, String(value), valueType, updatedBy || "dashboard");
}

function ensureMusicDiscSchema(db) {
  db.prepare(`CREATE TABLE IF NOT EXISTS music_disc_balances (
    user_id TEXT PRIMARY KEY,
    username TEXT,
    disc_balance INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
  )`).run();
  db.prepare(`CREATE TABLE IF NOT EXISTS music_disc_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    username TEXT,
    amount INTEGER,
    action TEXT,
    reason TEXT,
    balance_after INTEGER,
    actor TEXT,
    created_at TEXT
  )`).run();
}

function resolveDashboardUser(db, query) {
  const q = String(query || "").trim().replace(/^@/, "");
  if (!q) return null;
  try {
    return db.prepare(
      "SELECT user_id, username, balance FROM users WHERE lower(username)=lower(?) OR user_id=? LIMIT 1",
    ).get(q, q) ?? null;
  } catch {
    return null;
  }
}

function readMusicDiscIdentity(db, query) {
  ensureMusicDiscSchema(db);
  const user = resolveDashboardUser(db, query);
  if (!user) return null;
  db.prepare(`INSERT OR IGNORE INTO music_disc_balances
    (user_id, username, disc_balance, created_at, updated_at)
    VALUES (?, ?, 0, datetime('now'), datetime('now'))`).run(user.user_id, user.username);
  db.prepare(`UPDATE music_disc_balances
    SET username=?, updated_at=datetime('now')
    WHERE user_id=? AND COALESCE(username, '') != ?`).run(user.username, user.user_id, user.username);
  const balance = db.prepare(
    "SELECT disc_balance FROM music_disc_balances WHERE user_id=? LIMIT 1",
  ).get(user.user_id)?.disc_balance ?? 0;
  const history = db.prepare(
    `SELECT id, username, amount, action, reason, balance_after, actor, created_at
     FROM music_disc_transactions
     WHERE user_id=?
     ORDER BY id DESC
     LIMIT 10`,
  ).all(user.user_id);
  return { username: user.username, balance: Number(balance || 0), history };
}

function getRadioRuntimeState(db, key, fallback = "") {
  try {
    if (!tableExists(db, "radio_runtime_state")) return fallback;
    const row = db.prepare("SELECT value FROM radio_runtime_state WHERE key=? LIMIT 1").get(key);
    return row?.value ?? fallback;
  } catch {
    return fallback;
  }
}

function parseJsonState(raw, fallback = null) {
  try {
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function activeQueueCountFromRadioRequests(db) {
  try {
    if (!tableExists(db, "radio_requests")) return 0;
    return Number(
      db.prepare(
        "SELECT COUNT(*) AS n FROM radio_requests WHERE status IN ('pending','preparing','ready','submitted','playing')",
      ).get()?.n || 0,
    );
  } catch {
    return 0;
  }
}

function boolFromSetting(value, fallback = false) {
  if (value === undefined || value === null || value === "") return fallback;
  return ["1", "true", "yes", "on", "enabled"].includes(String(value).trim().toLowerCase());
}

function publicRankingsHideStaffBots(db) {
  return boolFromSetting(getSetting(db, "public_rankings_hide_staff_bots", "true"), true);
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
  const radioVersion = String(process.env.RADIO_SYSTEM_VERSION || "v1").toLowerCase();
  const useV3 = radioVersion === "v3" && tableExists(db, "radio_v3_requests") && columnExists(db, "radio_v3_requests", "status");
  let nowPlaying = null;
  let queue = [];
  let recent = [];
  let counts = {};
  let failedToday = 0;
  let playedToday = 0;
  let topRequester = null;
  if (useV3) {
    const desired = ["id", "title", "artist", "username", "user_id", "status", "error", "azura_file_id", "azura_song_id", "temp_filename", "source_type", "payment_type", "priority", "started_at", "played_at", "cleaned_at", "created_at", "ready_at", "cancelled_at"];
    nowPlaying = safeOne(db, "radio_v3_requests", desired, { where: "status='playing'", orderBy: "id DESC" });
    queue = safeRows(db, "radio_v3_requests", desired, {
      where: "status IN ('pending','preparing','ready','loading')",
      orderBy: "id ASC",
      limit: "50",
    }).map((row, i) => ({ ...row, filename: row.temp_filename || "", artist: row.artist || "", pos: i + 1 }));
    recent = safeRows(db, "radio_v3_requests", desired, {
      where: "status IN ('played','cleaned','failed','cancelled')",
      orderBy: "id DESC",
      limit: "50",
    }).map((row) => ({ ...row, filename: row.temp_filename || "" }));
    try {
      counts = Object.fromEntries(
        db
          .prepare("SELECT status, COUNT(*) AS n FROM radio_v3_requests GROUP BY status")
          .all()
          .map((r) => [r.status, r.n]),
      );
      const dateExpr = "COALESCE(played_at, started_at, created_at)";
      playedToday = db.prepare(`SELECT COUNT(*) AS n FROM radio_v3_requests WHERE status IN ('played','cleaned') AND date(${dateExpr})=date('now')`).get().n ?? 0;
      failedToday = db.prepare(`SELECT COUNT(*) AS n FROM radio_v3_requests WHERE status IN ('failed','cancelled') AND date(${dateExpr})=date('now')`).get().n ?? 0;
      topRequester = db.prepare("SELECT username, COUNT(*) AS requests FROM radio_v3_requests WHERE COALESCE(username,'')!='' GROUP BY username ORDER BY requests DESC LIMIT 1").get() ?? null;
    } catch (err) {
      console.error(`[DASHBOARD_DB] radio_v3_counts_failed error=${err.message}`);
    }
  } else if (hasYtJobs) {
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
  } else if (tableExists(db, "radio_requests") && columnExists(db, "radio_requests", "status")) {
    const desired = ["id", "title", "artist", "username", "user_id", "source_type", "source_ref", "status", "error", "azura_file_id", "azura_song_id", "azura_path", "temp_filename", "disc_cost_charged", "priority", "created_at", "prepared_at", "submitted_at", "playing_at", "played_at", "cleaned_at", "failed_at"];
    nowPlaying = safeOne(db, "radio_requests", desired, { where: "status='playing'", orderBy: "id DESC" });
    queue = safeRows(db, "radio_requests", desired, {
      where: "status IN ('pending','preparing','ready','submitted')",
      orderBy: "id ASC",
      limit: "50",
    }).map((row, i) => ({ ...row, filename: row.temp_filename || "", pos: i + 1 }));
    recent = safeRows(db, "radio_requests", desired, {
      where: "status IN ('played','cleaned','failed','cancelled')",
      orderBy: "id DESC",
      limit: "50",
    }).map((row) => ({ ...row, filename: row.temp_filename || "" }));
    try {
      counts = Object.fromEntries(
        db.prepare("SELECT status, COUNT(*) AS n FROM radio_requests GROUP BY status").all().map((r) => [r.status, r.n]),
      );
      playedToday = db.prepare("SELECT COUNT(*) AS n FROM radio_requests WHERE status IN ('played','cleaned') AND date(COALESCE(played_at, created_at))=date('now')").get().n ?? 0;
      failedToday = db.prepare("SELECT COUNT(*) AS n FROM radio_requests WHERE status IN ('failed','cancelled') AND date(COALESCE(failed_at, cancelled_at, created_at))=date('now')").get().n ?? 0;
      topRequester = db.prepare("SELECT username, COUNT(*) AS requests FROM radio_requests WHERE COALESCE(username,'')!='' GROUP BY username ORDER BY requests DESC LIMIT 1").get() ?? null;
    } catch (err) {
      console.error(`[DASHBOARD_DB] radio_phase4_counts_failed error=${err.message}`);
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
    radio_system_version: radioVersion,
    live_queue_source: useV3 ? "radio_v3_requests" : hasYtJobs ? "yt_request_jobs" : "radio_requests",
    requests_enabled: dashboardGate && roomGate,
    dashboard_requests_enabled: dashboardGate,
    radio_requests_enabled: roomGate,
    max_active_queue: getSetting(db, "radio_max_active_queue", "20"),
    per_user_queue_limit: getSetting(db, "radio_per_user_queue_limit", "3"),
    request_cooldown: getSetting(db, "radio_request_cooldown", "300"),
    request_price_legacy: getSetting(db, "radio_request_price", "500"),
    request_payment_model: "song_plays",
    request_max_duration: process.env.REQUEST_MAX_DURATION || "600",
    voteskip_threshold: getSetting(db, "radio_voteskip_threshold", "3"),
    skip_on_leave: getSetting(db, "radio_skip_on_leave", "true"),
    refund_on_leave: getSetting(db, "radio_refund_on_leave", "true"),
    admin_ignore_leave: getSetting(db, "radio_admin_ignore_leave", "true"),
    music_shop_enabled: getRadioSetting(db, "music_shop_enabled", "true"),
    music_disc_display_name: getRadioSetting(db, "music_disc_display_name", "Song Request 💽"),
    music_disc_price_coins: getRadioSetting(db, "music_disc_price_coins", "500"),
    music_disc_price_luxe: getRadioSetting(db, "music_disc_price_luxe", "50"),
    music_disc_purchase_coins_enabled: getRadioSetting(db, "music_disc_purchase_coins_enabled", "true"),
    music_disc_purchase_luxe_enabled: getRadioSetting(db, "music_disc_purchase_luxe_enabled", "true"),
    music_disc_max_purchase_per_command: getRadioSetting(db, "music_disc_max_purchase_per_command", "10"),
    music_disc_daily_purchase_limit: getRadioSetting(db, "music_disc_daily_purchase_limit", "50"),
    request_disc_cost_normal: getRadioSetting(db, "request_disc_cost_normal", "1"),
    request_disc_cost_vip: getRadioSetting(db, "request_disc_cost_vip", "1"),
    request_disc_cost_staff: getRadioSetting(db, "request_disc_cost_staff", "0"),
    request_disc_cost_owner: getRadioSetting(db, "request_disc_cost_owner", "0"),
    radio_enabled: getRadioSetting(db, "radio_enabled", "true"),
    radio_poll_interval_secs: getRadioSetting(db, "radio_poll_interval_secs", "3"),
    radio_submit_ready_immediately: getRadioSetting(db, "radio_submit_ready_immediately", "true"),
    radio_request_prequeue_enabled: getRadioSetting(db, "radio_request_prequeue_enabled", "true"),
    radio_request_prequeue_count: getRadioSetting(db, "radio_request_prequeue_count", "1"),
    now_announce_song_changes: getRadioSetting(db, "now_announce_song_changes", "true"),
    now_announce_autodj: getRadioSetting(db, "now_announce_autodj", "true"),
    now_announce_requests: getRadioSetting(db, "now_announce_requests", "true"),
    now_command_response_mode: getRadioSetting(db, "now_command_response_mode", "whisper"),
    now_show_progress_bar: getRadioSetting(db, "now_show_progress_bar", "true"),
    now_show_likes_dislikes: getRadioSetting(db, "now_show_likes_dislikes", "true"),
    now_show_request_play_count: getRadioSetting(db, "now_show_request_play_count", "true"),
    now_footer_text: getRadioSetting(db, "now_footer_text", "🎶 !play to request a song"),
    max_song_duration_normal_secs: getRadioSetting(db, "max_song_duration_normal_secs", "300"),
    max_song_duration_vip_secs: getRadioSetting(db, "max_song_duration_vip_secs", "480"),
    max_song_duration_staff_secs: getRadioSetting(db, "max_song_duration_staff_secs", "600"),
    max_song_duration_owner_secs: getRadioSetting(db, "max_song_duration_owner_secs", "0"),
    youtube_direct_url_enabled: getRadioSetting(db, "youtube_direct_url_enabled", "true"),
    youtube_search_enabled: getRadioSetting(db, "youtube_search_enabled", "true"),
    youtube_search_result_count: getRadioSetting(db, "youtube_search_result_count", "5"),
    youtube_search_session_timeout_secs: getRadioSetting(db, "youtube_search_session_timeout_secs", "120"),
    radio_favorites_enabled: getRadioSetting(db, "radio_favorites_enabled", "true"),
    radio_favorites_max_per_user: getRadioSetting(db, "radio_favorites_max_per_user", "25"),
    youtube_reject_playlists: getRadioSetting(db, "youtube_reject_playlists", "true"),
    youtube_reject_mixes: getRadioSetting(db, "youtube_reject_mixes", "true"),
    youtube_reject_livestreams: getRadioSetting(db, "youtube_reject_livestreams", "true"),
    youtube_reject_shorts: getRadioSetting(db, "youtube_reject_shorts", "false"),
    block_requests_when_azura_unhealthy: getRadioSetting(db, "block_requests_when_azura_unhealthy", "true"),
  };
  const currentTrack = parseJsonState(getRadioRuntimeState(db, "current_track", ""), null);
  const lastPoll = parseJsonState(getRadioRuntimeState(db, "last_poll", ""), null);
  const skeletonHealth = {
    radio_enabled: settings.radio_enabled !== "false",
    azura_api_configured: Boolean((process.env.AZURA_BASE_URL || "").trim() && (process.env.AZURA_API_KEY || "").trim()),
    azura_sftp_configured: Boolean((process.env.AZURA_SFTP_HOST || "").trim() && (process.env.AZURA_SFTP_USER || "").trim() && (process.env.AZURA_SFTP_PASS || "").trim()),
    current_track: currentTrack,
    queue_size: activeQueueCountFromRadioRequests(db),
    last_poll: lastPoll,
    last_error: getRadioRuntimeState(db, "last_error", "none"),
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
    top_requesters: useV3
      ? rowsOrEmpty(db, "radio_v3_requests", "SELECT username, COUNT(*) AS requests FROM radio_v3_requests WHERE COALESCE(username,'')!='' GROUP BY username ORDER BY requests DESC LIMIT 20")
      : (hasYtJobs ? rowsOrEmpty(db, "yt_request_jobs", "SELECT username, COUNT(*) AS requests FROM yt_request_jobs WHERE COALESCE(username,'')!='' GROUP BY username ORDER BY requests DESC LIMIT 20") : []),
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
    skeleton_health: skeletonHealth,
    blocklist,
    stats,
    local_library: localLibrary,
    logs: {
      audit: safeRows(db, "audit_logs", ["id","actor","action_type","target_type","target_id","old_value","new_value","ip_address","created_at"], { where: "action_type LIKE 'radio_%' OR target_type IN ('yt_request_jobs','radio_v3_requests','bot_command_queue')", orderBy: columnExists(db, "audit_logs", "created_at") ? "created_at DESC" : "", limit: "100" }),
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
      live_queue_source: useV3 ? "radio_v3_requests" : "yt_request_jobs",
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

const AUTOMATION_TABLES = [
  "dashboard_scheduled_announcements",
  "rotating_announcements",
  "subscriber_announcements",
  "big_announcement_settings",
  "big_announcement_logs",
  "release_announcements",
  "first_find_announce_pending",
  "host_dm_queue",
  "bot_command_queue",
  "event_settings",
  "event_history",
  "event_definitions",
  "room_settings",
  "bot_settings",
  "audit_logs",
  "admin_action_logs",
];

function automationTableInfo(db, table, limit = "75") {
  const exists = tableExists(db, table);
  const cols = exists ? tableColumns(db, table) : [];
  const orderBy = cols.includes("next_run_at") ? "enabled DESC, datetime(next_run_at) ASC"
    : cols.includes("created_at") ? "created_at DESC"
    : cols.includes("set_at") ? "set_at DESC"
    : cols.includes("last_sent_at") ? "last_sent_at DESC"
    : cols.includes("id") ? "id DESC"
    : "";
  return { exists, columns: cols, rows: safeTableRows(db, table, { orderBy, limit }) };
}

function automationAnnouncementRows(db) {
  return safeRows(db, "dashboard_scheduled_announcements", [
    "id", "title", "message", "target_bot", "schedule_type", "interval_minutes",
    "next_run_at", "last_sent_at", "enabled", "archived", "created_by", "created_at", "updated_at",
  ], {
    where: "COALESCE(archived,0)=0",
    orderBy: "enabled DESC, datetime(COALESCE(next_run_at, '9999-12-31')) ASC, id DESC",
    limit: "500",
  });
}

function readAutomationDashboard(db) {
  const tables = Object.fromEntries(AUTOMATION_TABLES.map((table) => [table, automationTableInfo(db, table, table === "bot_command_queue" ? "150" : "100")]));
  const scheduled = automationAnnouncementRows(db);
  const rotating = tables.rotating_announcements?.rows || [];
  const queue = safeRows(db, "bot_command_queue", BOT_COMMAND_QUEUE_COLUMNS, {
    where: "action IN ('announce','event_reminder','promo_message','security_alert') OR payload LIKE '%automation%' OR payload LIKE '%promo%' OR payload LIKE '%reminder%'",
    orderBy: columnExists(db, "bot_command_queue", "created_at") ? "created_at DESC" : "",
    limit: "150",
  });
  const logs = {
    big_announcement_logs: tables.big_announcement_logs?.rows || [],
    admin_action_logs: tables.admin_action_logs?.rows || [],
    host_dm_queue: tables.host_dm_queue?.rows || [],
    audit_logs: safeRows(db, "audit_logs", ["id", "actor", "action_type", "target_type", "target_id", "old_value", "new_value", "ip_address", "created_at"], {
      where: "action_type LIKE 'automation_%' OR action_type LIKE 'room_announce%' OR action_type LIKE 'room_rotating_announcement%' OR target_type IN ('dashboard_scheduled_announcements','rotating_announcements','bot_command_queue')",
      orderBy: columnExists(db, "audit_logs", "created_at") ? "created_at DESC" : "",
      limit: "150",
    }),
    failed_commands: queue.filter((row) => String(row.status || "").toLowerCase() === "failed"),
  };
  const settings = readKeyValueMap(db, "room_settings");
  const nextAnnouncement = scheduled
    .filter((row) => Number(row.enabled) && row.next_run_at)
    .sort((a, b) => String(a.next_run_at).localeCompare(String(b.next_run_at)))[0] || null;
  const activeAutomations = scheduled.filter((row) => Number(row.enabled)).length
    + rotating.filter((row) => String(row.enabled ?? "1") !== "0").length;
  return {
    overview: {
      active_automations: activeAutomations,
      scheduled_announcements: scheduled.length,
      rotating_enabled: settings.announcements_enabled ?? "",
      next_announcement: nextAnnouncement,
      recent_deliveries: queue.filter((row) => String(row.status || "").toLowerCase() === "completed").length,
      failed_deliveries: queue.filter((row) => String(row.status || "").toLowerCase() === "failed").length,
      host_queue_pending: queue.filter((row) => ["pending", "queued", "claimed", "running"].includes(String(row.status || "").toLowerCase())).length,
      dj_promo_status: settings.radio_promo_enabled ?? "",
    },
    scheduled_announcements: scheduled,
    rotating_announcements: rotating,
    event_reminders: {
      event_settings: tables.event_settings?.rows || [],
      event_history: tables.event_history?.rows || [],
      event_definitions: tables.event_definitions?.rows || [],
    },
    promo_messages: [
      { category: "Radio promo", title: "Radio Requests", message: "Request music with !play [song name or artist]", target_bot: "host", status: "template" },
      { category: "Mining promo", title: "Mining", message: "Mine rare ores with !mine and climb !topminers.", target_bot: "host", status: "template" },
      { category: "Fishing promo", title: "Fishing", message: "Catch rare fish with !fish and climb !topfishers.", target_bot: "host", status: "template" },
      { category: "Casino promo", title: "Casino", message: "Play Blackjack with !bj [amount] or Poker with !join [amount].", target_bot: "host", status: "template" },
      { category: "VIP/rewards promo", title: "Rewards", message: "Claim !daily, check !profile, and watch for VIP rewards.", target_bot: "host", status: "template" },
      { category: "Event promo", title: "Events", message: "Watch announcements for events and check !events.", target_bot: "host", status: "template" },
    ],
    staff_alerts: {
      pending_reports: tableExists(db, "reports") && columnExists(db, "reports", "status")
        ? rowsOrEmpty(db, "reports", "SELECT COUNT(*) AS n FROM reports WHERE COALESCE(status,'open') NOT IN ('resolved','closed','dismissed')")[0]?.n ?? 0
        : null,
      failed_bot_commands: queue.filter((row) => String(row.status || "").toLowerCase() === "failed").length,
      radio_failures: tableExists(db, "yt_request_jobs") && columnExists(db, "yt_request_jobs", "status")
        ? rowsOrEmpty(db, "yt_request_jobs", "SELECT COUNT(*) AS n FROM yt_request_jobs WHERE status IN ('failed','failed_download','error','cancelled')")[0]?.n ?? 0
        : null,
      db_warnings: dbHealthSnapshot(db).warning_count,
    },
    delivery_queue: {
      pending: queue.filter((row) => ["pending", "queued", "claimed", "running"].includes(String(row.status || "").toLowerCase())),
      recent: queue,
      failed: queue.filter((row) => String(row.status || "").toLowerCase() === "failed"),
    },
    logs,
    tables,
    table_status: tableStatusMap(db, AUTOMATION_TABLES),
    columns: tableColumnsMap(db, AUTOMATION_TABLES),
    scheduler_note: "Dashboard stores scheduled announcements and can queue send-now commands. Recurring delivery requires a bot-side or external scheduler consumer.",
  };
}

function cleanAutomationMessage(message) {
  const text = String(message || "").trim();
  if (!text) throw new Error("message_required");
  if (text.length > 500) throw new Error("message_too_long");
  return text;
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
  "reviewed_at",
  "reviewed_by",
];

function moderationTableInfo(db, table, limit = "100") {
  const exists = tableExists(db, table);
  const cols = exists ? tableColumns(db, table) : [];
  const orderBy = cols.includes("created_at") ? "created_at DESC"
    : cols.includes("timestamp") ? "timestamp DESC"
    : cols.includes("muted_at") ? "muted_at DESC"
    : cols.includes("warned_at") ? "warned_at DESC"
    : cols.includes("reported_at") ? "reported_at DESC"
    : cols.includes("id") ? "id DESC"
    : "";
  return { exists, columns: cols, rows: safeTableRows(db, table, { orderBy, limit }) };
}

function activeSecurityBot(db) {
  const rows = safeRows(db, "bot_instances", ["bot_username","bot_mode","status","last_heartbeat_at","last_seen_at","last_error","current_room_id"], {
    where: "lower(bot_mode)='security' OR lower(bot_username)=lower('KeanuShield')",
    orderBy: columnExists(db, "bot_instances", "last_heartbeat_at") ? "last_heartbeat_at DESC" : "",
    limit: "5",
  });
  return rows[0] || null;
}

function readSecurityDashboard(db) {
  const reports = moderationTableInfo(db, "reports", "250");
  const warnings = moderationTableInfo(db, "warnings", "250");
  const roomWarnings = moderationTableInfo(db, "room_warnings", "250");
  const mutes = moderationTableInfo(db, "mutes", "250");
  const bans = moderationTableInfo(db, "room_bans", "250");
  const jail = moderationTableInfo(db, "jail_sentences", "250");
  const modLogs = moderationTableInfo(db, "moderation_logs", "250");
  const securityBot = activeSecurityBot(db);
  const securityCommands = safeRows(db, "bot_command_queue", BOT_COMMAND_QUEUE_COLUMNS, {
    where: "target_bot IN ('security','KeanuShield') OR action IN ('warn_user','mute_user','unmute_user','jail_user','unjail_user','security_alert')",
    orderBy: columnExists(db, "bot_command_queue", "created_at") ? "created_at DESC" : "",
    limit: "100",
  });
  const openReports = reports.rows.filter((r) => !["resolved","closed","done","dismissed"].includes(String(r.status || "").toLowerCase())).length;
  const activeMutes = mutes.rows.filter((r) => !r.expires_at || new Date(r.expires_at).getTime() > Date.now()).length;
  const recentWarnings = [...warnings.rows, ...roomWarnings.rows].slice(0, 25);
  const activeJail = jail.rows.filter((r) => !r.expires_at || new Date(r.expires_at).getTime() > Date.now()).length;
  const activeBans = bans.rows.filter((r) => !r.expires_at || new Date(r.expires_at).getTime() > Date.now()).length;
  return {
    overview: {
      open_reports: openReports,
      active_mutes: activeMutes,
      recent_warnings: recentWarnings.length,
      active_bans_or_jail: activeBans + activeJail,
      security_bot_status: securityBot?.status || "missing",
      failed_moderation_commands: securityCommands.filter((r) => r.status === "failed").length,
    },
    security_bot: securityBot,
    command_queue: {
      pending: securityCommands.filter((r) => ["pending","queued","claimed","running"].includes(String(r.status || ""))),
      recent: securityCommands,
    },
    tables: {
      reports,
      warnings,
      room_warnings: roomWarnings,
      mutes,
      room_bans: bans,
      jail_sentences: jail,
      moderation_logs: modLogs,
      audit_logs: {
        exists: tableExists(db, "audit_logs"),
        columns: tableColumns(db, "audit_logs"),
        rows: safeRows(db, "audit_logs", ["id","actor","action_type","target_type","target_id","old_value","new_value","ip_address","created_at"], {
          where: "action_type LIKE '%moderation%' OR action_type LIKE 'player_warn%' OR action_type LIKE 'player_mute%' OR action_type LIKE 'player_unmute%' OR action_type LIKE 'security_%'",
          orderBy: columnExists(db, "audit_logs", "created_at") ? "created_at DESC" : "",
          limit: "100",
        }),
      },
      admin_action_logs: moderationTableInfo(db, "admin_action_logs", "100"),
      command_error_logs: moderationTableInfo(db, "command_error_logs", "100"),
    },
    missing_endpoints: [
      { endpoint: "DELETE /api/security/warnings/:id", purpose: "Clear warning history", status: "OWNER ONLY / NOT IMPLEMENTED" },
      { endpoint: "POST /api/security/bans", purpose: "Ban/unban from room", status: bans.exists ? "UNVERIFIED" : "MISSING TABLE" },
      { endpoint: "POST /api/security/jail", purpose: "Jail/unjail user", status: jail.exists ? "UNVERIFIED" : "MISSING TABLE" },
      { endpoint: "moderation cleanup", purpose: "Delete old moderation records", status: "PREVIEW ONLY" },
    ],
  };
}

function insertWarningRow(db, player, actor, reason) {
  if (!tableExists(db, "warnings")) throw new Error("warnings_missing");
  const cols = tableColumns(db, "warnings");
  const pairs = [
    ["user_id", player.user_id],
    ["username", player.username],
    ["warned_by", actor],
    ["staff_name", actor],
    ["reason", reason],
    ["created_at", "CURRENT_TIMESTAMP", true],
    ["warned_at", "CURRENT_TIMESTAMP", true],
  ].filter(([col]) => cols.includes(col));
  if (!pairs.some(([col]) => col === "reason")) throw new Error("warnings_reason_missing");
  const names = pairs.map(([col]) => sqlIdent(col));
  const placeholders = pairs.map(([, , raw]) => raw ? "CURRENT_TIMESTAMP" : "?");
  const values = pairs.filter(([, , raw]) => !raw).map(([, value]) => value);
  return db.prepare(`INSERT INTO warnings (${names.join(", ")}) VALUES (${placeholders.join(", ")})`).run(...values);
}

function upsertMuteRow(db, player, actor, reason, minutes) {
  if (!tableExists(db, "mutes")) throw new Error("mutes_missing");
  const cols = tableColumns(db, "mutes");
  const expiresAt = new Date(Date.now() + minutes * 60_000).toISOString();
  const pairs = [
    ["user_id", player.user_id],
    ["username", player.username],
    ["muted_by", actor],
    ["staff_name", actor],
    ["reason", reason],
    ["duration_minutes", minutes],
    ["muted_at", "CURRENT_TIMESTAMP", true],
    ["created_at", "CURRENT_TIMESTAMP", true],
    ["expires_at", expiresAt],
  ].filter(([col]) => cols.includes(col));
  if (!pairs.some(([col]) => col === "user_id") && !pairs.some(([col]) => col === "username")) throw new Error("mutes_identity_column_missing");
  const names = pairs.map(([col]) => sqlIdent(col));
  const placeholders = pairs.map(([, , raw]) => raw ? "CURRENT_TIMESTAMP" : "?");
  const values = pairs.filter(([, , raw]) => !raw).map(([, value]) => value);
  return db.prepare(`INSERT OR REPLACE INTO mutes (${names.join(", ")}) VALUES (${placeholders.join(", ")})`).run(...values);
}

function buildLeaderboardsBase(db) {
  const missingTables = [];
  const missingColumns = [];
  const sources = {};
  const limit = 50;

  function source(name, table, columns, status = "connected", notes = "") {
    sources[name] = { table, columns, status, notes, row_count: rowCountSafe(db, table) ?? 0 };
  }
  function aliasSource(from, to, notes = "") {
    if (sources[from] && !sources[to]) sources[to] = { ...sources[from], notes: notes || sources[from].notes };
  }
  function markMissingTable(name, table) {
    missingTables.push(table);
    source(name, table, [], "missing_table");
    return [];
  }
  function markMissingColumns(name, table, cols) {
    for (const col of cols) missingColumns.push(`${table}.${col}`);
    source(name, table, tableExists(db, table) ? tableColumns(db, table) : [], "missing_column", cols.join(", "));
    return [];
  }
  function choose(cols, candidates) {
    return candidates.find((col) => cols.includes(col));
  }
  function addRank(rows) {
    return rows.map((row, index) => ({ rank: index + 1, ...row }));
  }
  function numericExpr(col) {
    return `COALESCE(CAST(${sqlIdent(col)} AS REAL),0)`;
  }
  function readTableLeaderboard(name, table, desired, { orderCandidates = [], where = "", map = (r) => r } = {}) {
    if (!tableExists(db, table)) return markMissingTable(name, table);
    const cols = tableColumns(db, table);
    const orderCol = choose(cols, orderCandidates);
    if (!orderCol) return markMissingColumns(name, table, orderCandidates.slice(0, 1));
    source(name, table, desired.filter((col) => cols.includes(col)), "connected");
    return addRank(safeRows(db, table, desired, {
      where,
      orderBy: `${numericExpr(orderCol)} DESC`,
      limit: String(limit),
    }).map(map));
  }

  const userCols = tableExists(db, "users") ? tableColumns(db, "users") : [];
  const usernameCol = userCols.includes("username") ? "username" : null;
  const balanceCol = choose(userCols, ["balance", "coins"]);
  const rich = tableExists(db, "users") && usernameCol && balanceCol
    ? addRank(safeRows(db, "users", ["user_id", "username", balanceCol], {
        where: `${numericExpr(balanceCol)} > 0`,
        orderBy: `${numericExpr(balanceCol)} DESC`,
        limit: String(limit),
      }).map((r) => ({ username: r.username, balance: r.balance ?? r.coins ?? 0 })))
    : (tableExists(db, "users") ? markMissingColumns("rich", "users", [usernameCol ? balanceCol || "balance" : "username"]) : markMissingTable("rich", "users"));
  if (rich.length) source("rich", "users", ["username", balanceCol], "connected", "Richest players from users.balance.");

  const xp = tableExists(db, "users") && usernameCol && userCols.includes("xp")
    ? addRank(safeRows(db, "users", ["user_id", "username", "level", "xp"], {
        where: "COALESCE(CAST(xp AS REAL),0) > 0",
        orderBy: `${userCols.includes("level") ? "COALESCE(CAST(level AS REAL),0) DESC, " : ""}COALESCE(CAST(xp AS REAL),0) DESC`,
        limit: String(limit),
      }))
    : (tableExists(db, "users") ? markMissingColumns("xp", "users", [usernameCol ? "xp" : "username"]) : markMissingTable("xp", "users"));
  if (xp.length) source("xp", "users", ["username", "level", "xp"].filter((c) => userCols.includes(c)), "connected", "Top XP and level from users.");

  const casino = tableExists(db, "users") && usernameCol && userCols.includes("total_games_won")
    ? addRank(safeRows(db, "users", ["user_id", "username", "total_games_won", "total_coins_earned"], {
        where: "COALESCE(CAST(total_games_won AS REAL),0) > 0",
        orderBy: "COALESCE(CAST(total_games_won AS REAL),0) DESC",
        limit: String(limit),
      }).map((r) => ({ username: r.username, wins: r.total_games_won, total_won: r.total_coins_earned ?? "" })))
    : [];
  source("casino", "users", ["username", "total_games_won", "total_coins_earned"].filter((c) => userCols.includes(c)), casino.length ? "connected" : "partial", "Overall casino uses users.total_games_won when present.");

  function gameStats(name, preferredTables) {
    for (const table of preferredTables) {
      if (!tableExists(db, table)) continue;
      const cols = tableColumns(db, table);
      const uname = choose(cols, ["username", "user_name", "player_name", "name", "user_id"]);
      if (!uname) return markMissingColumns(name, table, ["username"]);
      const wins = choose(cols, ["wins", "games_won", "hands_won", "total_wins"]);
      const totalWon = choose(cols, ["total_won", "coins_won", "winnings", "profit", "net_profit"]);
      const orderCol = totalWon || wins || choose(cols, ["blackjacks", "hands_played", "games_played"]);
      if (!orderCol) return markMissingColumns(name, table, ["wins"]);
      const desired = [uname, "wins", "games_won", "hands_won", "total_wins", "losses", "blackjacks", "total_won", "coins_won", "winnings", "profit", "net", "net_profit", "hands_played", "biggest_pot", "biggest_win", "games_played"].filter((c, i, a) => cols.includes(c) && a.indexOf(c) === i);
      source(name, table, desired, "connected");
      return addRank(safeRows(db, table, desired, {
        orderBy: `${numericExpr(orderCol)} DESC`,
        limit: String(limit),
      }).map((r) => ({
        username: r.username || r.user_name || r.player_name || r.name || r.user_id,
        wins: r.wins ?? r.games_won ?? r.hands_won ?? r.total_wins ?? "",
        losses: r.losses ?? "",
        blackjacks: r.blackjacks ?? "",
        total_won: r.total_won ?? r.coins_won ?? r.winnings ?? "",
        net: r.net ?? r.net_profit ?? r.profit ?? "",
        hands_played: r.hands_played ?? r.games_played ?? "",
        biggest_pot: r.biggest_pot ?? r.biggest_win ?? "",
      })));
    }
    return markMissingTable(name, preferredTables[0]);
  }

  const blackjack = gameStats("blackjack", ["rbj_stats", "bj_stats"]);
  const poker = gameStats("poker", ["poker_stats"]);

  const mining = (() => {
    for (const table of ["mining_players", "mining_profiles"]) {
      if (!tableExists(db, table)) continue;
      const cols = tableColumns(db, table);
      const uname = choose(cols, ["username", "user_name", "player_name", "user_id"]);
      const total = choose(cols, ["total_mined", "total_ores_mined", "ores_mined", "total_finds", "mines", "total_weight", "total_value", "xp", "mining_xp"]);
      if (!uname || !total) return markMissingColumns("mining", table, [uname ? total || "total_mined" : "username"]);
      const desired = [uname, "mining_level", "level", "mining_xp", "xp", "total_mined", "total_ores_mined", "ores_mined", "total_finds", "rare_finds", "total_value", "total_weight", "best_ore"].filter((c, i, a) => cols.includes(c) && a.indexOf(c) === i);
      source("mining", table, desired, "connected");
      return addRank(safeRows(db, table, desired, { orderBy: `${numericExpr(total)} DESC`, limit: String(limit) }).map((r) => ({
        username: r.username || r.user_name || r.player_name || r.user_id,
        level: r.mining_level ?? r.level ?? "",
        xp: r.mining_xp ?? r.xp ?? "",
        total_mined: r.total_mined ?? r.total_ores_mined ?? r.ores_mined ?? r.total_finds ?? "",
        rare_finds: r.rare_finds ?? "",
        total_value: r.total_value ?? "",
        total_weight: r.total_weight ?? "",
        best_ore: r.best_ore ?? "",
      })));
    }
    return markMissingTable("mining", "mining_players");
  })();

  const fishing = (() => {
    for (const table of ["fish_profiles", "fishing_profiles"]) {
      if (!tableExists(db, table)) continue;
      const cols = tableColumns(db, table);
      const uname = choose(cols, ["username", "user_name", "player_name", "user_id"]);
      const total = choose(cols, ["total_catches", "fish_caught", "catches", "total_fish", "biggest_catch", "biggest_weight", "xp", "fishing_xp"]);
      if (!uname || !total) return markMissingColumns("fishing", table, [uname ? total || "total_catches" : "username"]);
      const desired = [uname, "fishing_level", "level", "fishing_xp", "xp", "total_catches", "fish_caught", "catches", "biggest_catch", "biggest_weight", "total_value"].filter((c, i, a) => cols.includes(c) && a.indexOf(c) === i);
      source("fishing", table, desired, "connected");
      return addRank(safeRows(db, table, desired, { orderBy: `${numericExpr(total)} DESC`, limit: String(limit) }).map((r) => ({
        username: r.username || r.user_name || r.player_name || r.user_id,
        level: r.fishing_level ?? r.level ?? "",
        xp: r.fishing_xp ?? r.xp ?? "",
        total_catches: r.total_catches ?? r.fish_caught ?? r.catches ?? "",
        biggest_catch: r.biggest_catch ?? r.biggest_weight ?? "",
        total_value: r.total_value ?? "",
      })));
    }
    return markMissingTable("fishing", "fish_profiles");
  })();

  const events = (() => {
    if (!tableExists(db, "event_points")) return markMissingTable("events", "event_points");
    const cols = tableColumns(db, "event_points");
    const uname = choose(cols, ["username", "user_name", "player_name", "user_id"]);
    const points = choose(cols, ["points", "total_points", "score"]);
    if (!uname || !points) return markMissingColumns("events", "event_points", [uname ? "points" : "username"]);
    source("events", "event_points", [uname, points], "connected");
    return addRank(safeRows(db, "event_points", [uname, points], { orderBy: `${numericExpr(points)} DESC`, limit: String(limit) }).map((r) => ({
      username: r.username || r.user_name || r.player_name || r.user_id,
      points: r.points ?? r.total_points ?? r.score ?? 0,
    })));
  })();

  const radio = (() => {
    if (tableExists(db, "radio_user_stats")) {
      const cols = tableColumns(db, "radio_user_stats");
      const uname = choose(cols, ["username", "user_name", "requester", "user_id"]);
      const requests = choose(cols, ["requests", "request_count", "total_requests", "plays", "played_count"]);
      if (uname && requests) {
        source("radio", "radio_user_stats", [uname, requests], "connected");
        return addRank(safeRows(db, "radio_user_stats", [uname, requests], { orderBy: `${numericExpr(requests)} DESC`, limit: String(limit) }).map((r) => ({
          username: r.username || r.user_name || r.requester || r.user_id,
          requests: r.requests ?? r.request_count ?? r.total_requests ?? r.plays ?? r.played_count,
        })));
      }
    }
    if (!tableExists(db, "yt_request_jobs") || !columnExists(db, "yt_request_jobs", "username")) return markMissingTable("radio", "radio_user_stats");
    const rows = safeRows(db, "yt_request_jobs", ["username", "status"], { where: "username IS NOT NULL AND username <> ''", limit: "1000" });
    const counts = new Map();
    for (const row of rows) counts.set(row.username, (counts.get(row.username) || 0) + 1);
    source("radio", "yt_request_jobs", ["username", "status"], "connected", "Fallback aggregation from yt_request_jobs.");
    return addRank([...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, limit).map(([username, requests]) => ({ username, requests })));
  })();

  const radio_songs = tableExists(db, "radio_song_stats")
    ? readTableLeaderboard("radio_songs", "radio_song_stats", ["title", "artist", "plays", "requests", "likes", "dislikes"], { orderCandidates: ["plays", "requests", "like_count"] })
    : [];

  const reputation = (() => {
    if (!tableExists(db, "reputation")) return markMissingTable("reputation", "reputation");
    const cols = tableColumns(db, "reputation");
    const uname = choose(cols, ["username", "user_name", "user_id"]);
    const rep = choose(cols, ["rep_received", "received", "reputation", "points", "likes_received"]);
    if (!uname || !rep) return markMissingColumns("reputation", "reputation", [uname ? "rep_received" : "username"]);
    source("reputation", "reputation", [uname, rep, "rep_given", "given", "likes_given"].filter((c) => cols.includes(c)), "connected");
    return addRank(safeRows(db, "reputation", [uname, rep, "rep_given", "given", "likes_given"].filter((c) => cols.includes(c)), {
      orderBy: `${numericExpr(rep)} DESC`,
      limit: String(limit),
    }).map((r) => ({
      username: r.username || r.user_name || r.user_id,
      rep_received: r.rep_received ?? r.received ?? r.reputation ?? r.points ?? r.likes_received ?? 0,
      rep_given: r.rep_given ?? r.given ?? r.likes_given ?? "",
    })));
  })();

  const out = {
    rich,
    xp,
    casino,
    blackjack,
    poker,
    mining,
    fishing,
    events,
    radio,
    radio_songs,
    reputation,
    metadata: {
      generated_at: nowIso(),
      missing_tables: [...new Set(missingTables)],
      missing_columns: [...new Set(missingColumns.filter(Boolean))],
      sources,
    },
  };
  return {
    ...out,
    rich_list: rich,
    miners: mining,
    fishers: fishing,
    top_requesters: radio,
  };
}

function buildLeaderboards(db, options = {}) {
  let base;
  try {
    base = buildLeaderboardsBase(db);
  } catch (err) {
    base = {
      rich: [], xp: [], casino: [], blackjack: [], poker: [], mining: [], fishing: [], events: [], radio: [], radio_songs: [], reputation: [],
      metadata: { sources: { base: { table: "multiple", columns: [], status: "error", notes: err.message, row_count: 0 } }, missing_tables: [], missing_columns: [] },
    };
  }
  const sources = { ...(base.metadata?.sources || {}) };
  const missingTables = new Set(base.metadata?.missing_tables || []);
  const missingColumns = new Set(base.metadata?.missing_columns || []);
  const sourceErrors = Object.entries(sources)
    .filter(([, info]) => info?.status === "error")
    .map(([name, info]) => ({ name, table: info.table, error: info.notes || "source_error" }));
  const limit = 50;
  const hideStaff = !!options.hideStaff;
  const hideBots = !!options.hideBots;
  const menu = [
    { command: "!toprich", label: "Richest", category: "Economy", source: "users.balance" },
    { command: "!xpleaderboard", label: "XP / Level", category: "Economy", source: "users.xp" },
    { command: "!topminers", label: "Top Miners", category: "Mining", source: "mining_players" },
    { command: "!topweights", label: "Heaviest Ores", category: "Mining", source: "ore_weight_records" },
    { command: "!topfishers", label: "Top Fishers", category: "Fishing", source: "fish_profiles" },
    { command: "!topweightfish", label: "Heaviest Fish", category: "Fishing", source: "fish_catch_records / fish_profiles" },
    { command: "!topstreaks", label: "Daily Streaks", category: "Streaks", source: "daily_claims" },
    { command: "!topdonators", label: "Gold Supporters", category: "Gold / Tips", source: "gold_tip_events" },
    { command: "!toptippers", label: "P2P Senders", category: "Gold / Tips", source: "p2p_gold_tip_logs" },
    { command: "!toptipped", label: "P2P Receivers", category: "Gold / Tips", source: "p2p_gold_tip_logs" },
    { command: "!toprequesters", label: "Radio Requesters", category: "Radio", source: "radio_user_stats / yt_request_jobs" },
    { command: "!topsongs", label: "Radio Songs", category: "Radio", source: "radio_song_stats" },
    { command: "!toprep", label: "Reputation", category: "Social / Reputation", source: "reputation" },
    { command: "!profile", label: "Profile Stats", category: "Profiles", source: "users + profile tables" },
  ];

  function source(name, table, columns, status = "connected", notes = "") {
    sources[name] = { table, columns, status, notes, row_count: rowCountSafe(db, table) ?? 0 };
    if (status === "error") sourceErrors.push({ name, table, error: notes });
  }
  function aliasSource(from, to, notes = "") {
    if (sources[from] && !sources[to]) sources[to] = { ...sources[from], notes: notes || sources[from].notes };
  }
  function markMissingTable(name, table) {
    missingTables.add(table);
    source(name, table, [], "missing_table");
    return [];
  }
  function markMissingColumns(name, table, columns) {
    for (const col of columns) if (col) missingColumns.add(`${table}.${col}`);
    source(name, table, tableExists(db, table) ? tableColumns(db, table) : [], "missing_column", columns.filter(Boolean).join(", "));
    return [];
  }
  function choose(cols, candidates) {
    return candidates.find((col) => cols.includes(col));
  }
  function numericExpr(col, prefix = "") {
    return `COALESCE(CAST(${prefix}${sqlIdent(col)} AS REAL),0)`;
  }
  function textExpr(col) {
    return `LOWER(COALESCE(${sqlIdent(col)},''))`;
  }
  function timeExpr(cols, candidates) {
    const col = choose(cols, candidates);
    return col ? `, COALESCE(${sqlIdent(col)}, '') DESC` : "";
  }
  function addRank(rows) {
    return rows.map((row, index) => ({ rank: index + 1, ...row }));
  }
  function userNameMap() {
    if (!tableExists(db, "users") || !columnExists(db, "users", "user_id") || !columnExists(db, "users", "username")) return new Map();
    return new Map(safeRows(db, "users", ["user_id", "username"], { limit: "100000" }).map((row) => [String(row.user_id), row.username]));
  }
  function hiddenNameSet() {
    const names = new Set();
    if (hideBots) {
      for (const name of ["dj", "host", "banker", "blackjack", "poker", "miner", "fisher", "security", "main", "all", "shopkeeper", "eventhost", "arcadiaradio"]) names.add(name);
      for (const bot of CANONICAL_BOTS) names.add(String(bot.username).toLowerCase());
      names.add("arcadiaradio");
      try {
        for (const bot of readCanonicalBotAudit(db).bots || []) names.add(String(bot.bot_username || bot.username || "").toLowerCase());
      } catch {}
    }
    if (hideStaff) {
      if (tableExists(db, "dashboard_users") && columnExists(db, "dashboard_users", "username")) {
        for (const row of safeRows(db, "dashboard_users", ["username", "role", "disabled"], { limit: "10000" })) {
          if (["owner", "admin", "staff", "moderator"].includes(String(row.role || "").toLowerCase())) names.add(String(row.username || "").toLowerCase());
        }
      }
      if (tableExists(db, "admin_users") && columnExists(db, "admin_users", "username")) {
        for (const row of safeRows(db, "admin_users", ["username"], { limit: "10000" })) names.add(String(row.username || "").toLowerCase());
      }
    }
    names.delete("");
    return names;
  }
  function directRows(name, table, columns, sql, params = [], notes = "") {
    try {
      if (!tableExists(db, table)) return markMissingTable(name, table);
      const cols = tableColumns(db, table);
      const missing = columns.filter((col) => !cols.includes(col));
      if (missing.length) return markMissingColumns(name, table, missing);
      const rows = db.prepare(sql).all(...params);
      source(name, table, columns, rows.length ? "connected" : "empty", notes);
      return addRank(rows);
    } catch (err) {
      source(name, table, columns, "error", err.message);
      return [];
    }
  }
  function runSql(name, table, requiredColumns, sql, params = [], notes = "") {
    try {
      if (!tableExists(db, table)) return markMissingTable(name, table);
      const cols = tableColumns(db, table);
      const missing = requiredColumns.filter((col) => col && !cols.includes(col));
      if (missing.length) return markMissingColumns(name, table, missing);
      const rows = db.prepare(sql).all(...params);
      source(name, table, requiredColumns.filter((col) => cols.includes(col)), rows.length ? "connected" : "empty", notes);
      return addRank(rows);
    } catch (err) {
      source(name, table, requiredColumns, "error", err.message);
      return [];
    }
  }
  function sumByUser(name, table, userCol, valueCol, outValue, notes, where = "") {
    if (!tableExists(db, table)) return markMissingTable(name, table);
    const cols = tableColumns(db, table);
    const missing = [userCol, valueCol].filter((col) => !cols.includes(col));
    if (missing.length) return markMissingColumns(name, table, missing);
    const sql = `SELECT ${sqlIdent(userCol)} AS username, SUM(${numericExpr(valueCol)}) AS ${sqlIdent(outValue)}, COUNT(*) AS entries FROM ${sqlIdent(table)}${where ? ` WHERE ${where}` : ""} GROUP BY ${sqlIdent(userCol)} ORDER BY ${sqlIdent(outValue)} DESC LIMIT ?`;
    return runSql(name, table, [userCol, valueCol], sql, [limit], notes);
  }
  function countBy(name, table, groupCol, outValue, notes, where = "") {
    if (!tableExists(db, table)) return markMissingTable(name, table);
    const cols = tableColumns(db, table);
    if (!cols.includes(groupCol)) return markMissingColumns(name, table, [groupCol]);
    const sql = `SELECT ${sqlIdent(groupCol)} AS name, COUNT(*) AS ${sqlIdent(outValue)} FROM ${sqlIdent(table)}${where ? ` WHERE ${where}` : ""} GROUP BY ${sqlIdent(groupCol)} ORDER BY ${sqlIdent(outValue)} DESC LIMIT ?`;
    return runSql(name, table, [groupCol], sql, [limit], notes);
  }
  aliasSource("rich", "richest", "Exact in-room !toprich source from users.balance.");
  aliasSource("casino", "casino_overall", "Casino overall from users.total_games_won.");
  aliasSource("mining", "mining_top", "Exact in-room !topminers source from mining player stats.");
  aliasSource("fishing", "fishing_top", "Exact in-room !topfishers source from fish profile stats.");
  aliasSource("events", "event_points", "Event points leaderboard.");
  aliasSource("radio", "radio_requesters", "Radio requester leaderboard.");
  aliasSource("radio_songs", "radio_tracks", "Radio song stats leaderboard.");

  function activeBlackjackStats() {
    function statsFromTable(table, prefix, label) {
      if (!tableExists(db, table)) return null;
      const cols = tableColumns(db, table);
      if (!cols.includes("user_id")) return markMissingColumns("blackjack", table, ["user_id"]);
      const wins = choose(cols, [`${prefix}_wins`, "wins", "games_won", "total_wins"]);
      const losses = choose(cols, [`${prefix}_losses`, "losses"]);
      const blackjacks = choose(cols, [`${prefix}_blackjacks`, "blackjacks"]);
      const totalBet = choose(cols, [`${prefix}_total_bet`, "total_bet", "bet"]);
      const totalWon = choose(cols, [`${prefix}_total_won`, "total_won", "coins_won", "winnings"]);
      const totalLost = choose(cols, [`${prefix}_total_lost`, "total_lost", "lost"]);
      if (!wins && !totalWon && !totalBet) return markMissingColumns("blackjack", table, [`${prefix}_wins`, `${prefix}_total_won`]);
      const joinUsers = tableExists(db, "users") && columnExists(db, "users", "user_id") && columnExists(db, "users", "username");
      const usernameExpr = joinUsers ? "COALESCE(NULLIF(u.username,''), 'Unknown Player')" : "'Unknown Player'";
      const joinSql = joinUsers ? "LEFT JOIN users u ON u.user_id = s.user_id" : "";
      const netExpr = totalWon && totalBet ? `(${numericExpr(totalWon, "s.")} - ${numericExpr(totalBet, "s.")})` : (totalWon ? numericExpr(totalWon, "s.") : "0");
      const whereParts = [wins ? `${numericExpr(wins, "s.")}>0` : "", totalBet ? `${numericExpr(totalBet, "s.")}>0` : "", totalWon ? `${numericExpr(totalWon, "s.")}>0` : ""].filter(Boolean);
      const sql = `SELECT ${usernameExpr} AS username, s.user_id AS user_id, ${wins ? `s.${sqlIdent(wins)}` : "0"} AS wins, ${losses ? `s.${sqlIdent(losses)}` : "''"} AS losses, ${blackjacks ? `s.${sqlIdent(blackjacks)}` : "0"} AS blackjacks, ${totalWon ? `s.${sqlIdent(totalWon)}` : "0"} AS total_won, ${netExpr} AS net FROM ${sqlIdent(table)} s ${joinSql} ${whereParts.length ? `WHERE ${whereParts.join(" OR ")}` : ""} ORDER BY ${netExpr} DESC, ${wins ? numericExpr(wins, "s.") : "0"} DESC LIMIT ?`;
      const rows = runSql("blackjack", table, ["user_id", wins, losses, blackjacks, totalWon, totalBet].filter(Boolean), sql, [limit], `${label} active Blackjack/RBJ stats.`);
      if (rows.length) return rows;
      return [];
    }
    const rbj = statsFromTable("rbj_stats", "rbj", "Active RBJ");
    if (rbj?.length) return rbj;
    const roundRows = (() => {
      if (!tableExists(db, "casino_round_results")) return null;
      const cols = tableColumns(db, "casino_round_results");
      const required = ["mode", "username", "user_id", "result", "payout", "net"];
      const missing = required.filter((col) => !cols.includes(col));
      if (missing.length) return markMissingColumns("blackjack", "casino_round_results", missing);
      const joinUsers = tableExists(db, "users") && columnExists(db, "users", "user_id") && columnExists(db, "users", "username");
      const usernameExpr = joinUsers ? "COALESCE(NULLIF(cr.username,''), NULLIF(u.username,''), 'Unknown Player')" : "COALESCE(NULLIF(cr.username,''), 'Unknown Player')";
      const sql = `SELECT ${usernameExpr} AS username, cr.user_id AS user_id, SUM(CASE WHEN LOWER(cr.result) IN ('win','blackjack','natural','bj') THEN 1 ELSE 0 END) AS wins, SUM(CASE WHEN LOWER(cr.result) IN ('loss','lose','lost') THEN 1 ELSE 0 END) AS losses, SUM(CASE WHEN LOWER(cr.result) IN ('blackjack','natural','bj') THEN 1 ELSE 0 END) AS blackjacks, SUM(${numericExpr("payout", "cr.")}) AS total_won, SUM(${numericExpr("net", "cr.")}) AS net, MAX(${numericExpr("net", "cr.")}) AS biggest_win FROM casino_round_results cr ${joinUsers ? "LEFT JOIN users u ON u.user_id = cr.user_id" : ""} WHERE LOWER(cr.mode) IN ('rbj','realistic_blackjack','realistic blackjack','blackjack') GROUP BY cr.user_id, cr.username ORDER BY net DESC, wins DESC LIMIT ?`;
      return runSql("blackjack", "casino_round_results", required, sql, [limit], "Fallback active blackjack round results.");
    })();
    if (roundRows?.length) return roundRows;
    const legacy = statsFromTable("bj_stats", "bj", "Legacy BJ");
    if (legacy) return legacy;
    if (rbj) {
      source("blackjack", "rbj_stats", tableExists(db, "rbj_stats") ? tableColumns(db, "rbj_stats") : [], "empty", "Active RBJ stats table exists but has no public leaderboard rows.");
      return rbj;
    }
    return markMissingTable("blackjack", "rbj_stats");
  }

  const userCols = tableExists(db, "users") ? tableColumns(db, "users") : [];
  const balanceCol = choose(userCols, ["balance", "coins"]);
  const rich = balanceCol
    ? directRows("rich", "users", ["username", balanceCol], `SELECT username, ${sqlIdent(balanceCol)} AS balance FROM users ORDER BY COALESCE(CAST(${sqlIdent(balanceCol)} AS REAL),0) DESC LIMIT 10`, [], "Exact public richest query from users.balance.")
    : (tableExists(db, "users") ? markMissingColumns("rich", "users", ["balance"]) : markMissingTable("rich", "users"));
  source("richest", "users", ["username", balanceCol || "balance"], sources.rich?.status || "empty", "Alias for !toprich / rich.");
  const xp = directRows("xp", "users", ["username", "xp", "level"], "SELECT username, xp, level FROM users ORDER BY COALESCE(CAST(xp AS REAL),0) DESC LIMIT 10", [], "Exact public XP query from users.xp.");
  source("top_xp", "users", ["username", "xp", "level"], sources.xp?.status || "empty", "Alias for top XP.");
  const level = directRows("level", "users", ["username", "level", "xp"], "SELECT username, level, xp FROM users ORDER BY COALESCE(CAST(level AS REAL),0) DESC, COALESCE(CAST(xp AS REAL),0) DESC LIMIT 10", [], "Exact public level query from users.level/users.xp.");
  const mostGamesWon = directRows("most_games_won", "users", ["username", "total_games_won"], "SELECT username, total_games_won AS wins FROM users ORDER BY COALESCE(CAST(total_games_won AS REAL),0) DESC LIMIT 10", [], "Exact public games-won query from users.total_games_won.");

  const miningHeaviestOre = (() => {
    if (!tableExists(db, "ore_weight_records")) return markMissingTable("mining_heaviest_ore", "ore_weight_records");
    const cols = tableColumns(db, "ore_weight_records");
    const missing = ["username", "ore_name", "weight"].filter((col) => !cols.includes(col));
    if (missing.length) return markMissingColumns("mining_heaviest_ore", "ore_weight_records", missing);
    const desired = ["username", "ore_name", "rarity", "weight", "base_value", "final_value", "mxp", "mined_at"].filter((col) => cols.includes(col));
    source("mining_heaviest_ore", "ore_weight_records", desired, "connected", "In-room heaviest ore / ore weight leaderboard.");
    return addRank(safeRows(db, "ore_weight_records", desired, { orderBy: `${numericExpr("weight")} DESC`, limit: String(limit) }).map((row) => ({
      username: row.username,
      ore: row.ore_name,
      rarity: row.rarity ?? "",
      weight: row.weight,
      value: row.final_value ?? row.base_value ?? "",
      mined_at: row.mined_at ?? "",
    })));
  })();
  const miningMostValuable = tableExists(db, "ore_weight_records") && columnExists(db, "ore_weight_records", "final_value")
    ? addRank(safeRows(db, "ore_weight_records", ["username", "ore_name", "rarity", "weight", "final_value", "mined_at"], {
        orderBy: `${numericExpr("final_value")} DESC`,
        limit: String(limit),
      }).map((row) => ({ username: row.username, ore: row.ore_name, rarity: row.rarity ?? "", weight: row.weight ?? "", value: row.final_value ?? "", mined_at: row.mined_at ?? "" })))
    : (tableExists(db, "ore_weight_records") ? markMissingColumns("mining_most_valuable", "ore_weight_records", ["final_value"]) : markMissingTable("mining_most_valuable", "ore_weight_records"));
  if (miningMostValuable.length) source("mining_most_valuable", "ore_weight_records", ["username", "ore_name", "rarity", "weight", "final_value", "mined_at"].filter((col) => columnExists(db, "ore_weight_records", col)), "connected", "Most valuable ore finds.");
  const miningRarest = (() => {
    if (!tableExists(db, "ore_weight_records")) return markMissingTable("mining_rarest", "ore_weight_records");
    const cols = tableColumns(db, "ore_weight_records");
    const missing = ["username", "ore_name", "rarity"].filter((col) => !cols.includes(col));
    if (missing.length) return markMissingColumns("mining_rarest", "ore_weight_records", missing);
    const valueCol = choose(cols, ["final_value", "base_value"]);
    const sql = `SELECT ${sqlIdent("username")} AS username, ${sqlIdent("ore_name")} AS ore, ${sqlIdent("rarity")} AS rarity${cols.includes("weight") ? `, ${sqlIdent("weight")} AS weight` : ""}${valueCol ? `, ${sqlIdent(valueCol)} AS value` : ""}${cols.includes("mined_at") ? `, ${sqlIdent("mined_at")} AS mined_at` : ""} FROM ${sqlIdent("ore_weight_records")} WHERE ${sqlIdent("rarity")} IS NOT NULL AND ${sqlIdent("rarity")} <> '' ORDER BY CASE ${textExpr("rarity")} WHEN 'exotic' THEN 1 WHEN 'prismatic' THEN 2 WHEN 'mythic' THEN 3 WHEN 'legendary' THEN 4 WHEN 'epic' THEN 5 WHEN 'uncommon' THEN 6 WHEN 'common' THEN 7 ELSE 20 END ASC${cols.includes("weight") ? `, ${numericExpr("weight")} DESC` : ""}${valueCol ? `, ${numericExpr(valueCol)} DESC` : ""}${timeExpr(cols, ["mined_at", "created_at", "found_at"])} LIMIT ?`;
    return runSql("mining_rarest", "ore_weight_records", ["username", "ore_name", "rarity", "weight", valueCol, "mined_at"].filter(Boolean), sql, [limit], "Rarest ore finds by rarity rank.");
  })();
  const miningStreaks = tableExists(db, "mining_players") && columnExists(db, "mining_players", "streak_days")
    ? addRank(safeRows(db, "mining_players", ["username", "mining_level", "mining_xp", "streak_days"], {
        where: `${numericExpr("streak_days")} > 0`,
        orderBy: `${numericExpr("streak_days")} DESC`,
        limit: String(limit),
      }).map((row) => ({ username: row.username, level: row.mining_level ?? "", xp: row.mining_xp ?? "", streak: row.streak_days ?? 0 })))
    : (tableExists(db, "mining_players") ? markMissingColumns("mining_streaks", "mining_players", ["streak_days"]) : markMissingTable("mining_streaks", "mining_players"));
  if (miningStreaks.length) source("mining_streaks", "mining_players", ["username", "mining_level", "mining_xp", "streak_days"], "connected", "Mining streaks from mining_players.streak_days.");

  const fishingHeaviestFish = (() => {
    if (tableExists(db, "fish_catch_records")) {
      const cols = tableColumns(db, "fish_catch_records");
      const missing = ["username", "fish_name", "weight"].filter((col) => !cols.includes(col));
      if (missing.length) return markMissingColumns("fishing_heaviest_fish", "fish_catch_records", missing);
      const desired = ["username", "fish_name", "rarity", "weight", "final_value", "base_value", "caught_at"].filter((col) => cols.includes(col));
      source("fishing_heaviest_fish", "fish_catch_records", desired, "connected", "In-room biggest/heaviest fish leaderboard.");
      return addRank(safeRows(db, "fish_catch_records", desired, { orderBy: `${numericExpr("weight")} DESC`, limit: String(limit) }).map((row) => ({
        username: row.username,
        fish: row.fish_name,
        rarity: row.rarity ?? "",
        weight: row.weight,
        value: row.final_value ?? row.base_value ?? "",
        caught_at: row.caught_at ?? "",
      })));
    }
    if (tableExists(db, "fish_profiles") && columnExists(db, "fish_profiles", "best_fish_weight")) {
      source("fishing_heaviest_fish", "fish_profiles", ["username", "best_fish_name", "best_fish_weight", "best_fish_value"], "connected", "Fallback from fish_profiles best fish fields.");
      return addRank(safeRows(db, "fish_profiles", ["username", "best_fish_name", "best_fish_weight", "best_fish_value"], {
        orderBy: `${numericExpr("best_fish_weight")} DESC`,
        limit: String(limit),
      }).map((row) => ({ username: row.username, fish: row.best_fish_name ?? "", weight: row.best_fish_weight ?? 0, value: row.best_fish_value ?? "" })));
    }
    return markMissingTable("fishing_heaviest_fish", "fish_catch_records");
  })();
  const fishingMostValuable = tableExists(db, "fish_catch_records") && columnExists(db, "fish_catch_records", "final_value")
    ? addRank(safeRows(db, "fish_catch_records", ["username", "fish_name", "rarity", "weight", "final_value", "caught_at"], {
        orderBy: `${numericExpr("final_value")} DESC`,
        limit: String(limit),
      }).map((row) => ({ username: row.username, fish: row.fish_name, rarity: row.rarity ?? "", weight: row.weight ?? "", value: row.final_value ?? "", caught_at: row.caught_at ?? "" })))
    : (tableExists(db, "fish_catch_records") ? markMissingColumns("fishing_most_valuable", "fish_catch_records", ["final_value"]) : markMissingTable("fishing_most_valuable", "fish_catch_records"));
  if (fishingMostValuable.length) source("fishing_most_valuable", "fish_catch_records", ["username", "fish_name", "rarity", "weight", "final_value", "caught_at"].filter((col) => columnExists(db, "fish_catch_records", col)), "connected", "Most valuable fish catches.");
  const fishingRarest = (() => {
    if (!tableExists(db, "fish_catch_records")) return markMissingTable("fishing_rarest", "fish_catch_records");
    const cols = tableColumns(db, "fish_catch_records");
    const missing = ["username", "fish_name", "rarity"].filter((col) => !cols.includes(col));
    if (missing.length) return markMissingColumns("fishing_rarest", "fish_catch_records", missing);
    const valueCol = choose(cols, ["final_value", "base_value"]);
    const sql = `SELECT ${sqlIdent("username")} AS username, ${sqlIdent("fish_name")} AS fish, ${sqlIdent("rarity")} AS rarity${cols.includes("weight") ? `, ${sqlIdent("weight")} AS weight` : ""}${valueCol ? `, ${sqlIdent(valueCol)} AS value` : ""}${cols.includes("caught_at") ? `, ${sqlIdent("caught_at")} AS caught_at` : ""} FROM ${sqlIdent("fish_catch_records")} WHERE ${sqlIdent("rarity")} IS NOT NULL AND ${sqlIdent("rarity")} <> '' ORDER BY CASE ${textExpr("rarity")} WHEN 'mythic' THEN 1 WHEN 'legendary' THEN 2 WHEN 'exotic' THEN 3 WHEN 'epic' THEN 4 WHEN 'rare' THEN 5 WHEN 'uncommon' THEN 6 WHEN 'common' THEN 7 ELSE 20 END ASC${cols.includes("weight") ? `, ${numericExpr("weight")} DESC` : ""}${valueCol ? `, ${numericExpr(valueCol)} DESC` : ""}${timeExpr(cols, ["caught_at", "created_at"])} LIMIT ?`;
    return runSql("fishing_rarest", "fish_catch_records", ["username", "fish_name", "rarity", "weight", valueCol, "caught_at"].filter(Boolean), sql, [limit], "Rarest fish catches by rarity rank.");
  })();
  const fishProfileCols = tableExists(db, "fish_profiles") ? tableColumns(db, "fish_profiles") : [];
  const fishingStreakCol = choose(fishProfileCols, ["streak_days", "current_streak", "best_streak"]);
  const fishingStreaks = fishingStreakCol
    ? addRank(safeRows(db, "fish_profiles", ["username", "fishing_level", "fishing_xp", fishingStreakCol], {
        where: `${numericExpr(fishingStreakCol)} > 0`,
        orderBy: `${numericExpr(fishingStreakCol)} DESC`,
        limit: String(limit),
      }).map((row) => ({ username: row.username, level: row.fishing_level ?? "", xp: row.fishing_xp ?? "", streak: row[fishingStreakCol] ?? 0 })))
    : (tableExists(db, "fish_profiles") ? markMissingColumns("fishing_streaks", "fish_profiles", ["streak_days"]) : markMissingTable("fishing_streaks", "fish_profiles"));
  if (fishingStreaks.length) source("fishing_streaks", "fish_profiles", ["username", "fishing_level", "fishing_xp", fishingStreakCol], "connected", "Fishing streaks from fish_profiles.");

  const streaks = (() => {
    if (!tableExists(db, "daily_claims")) return markMissingTable("streaks", "daily_claims");
    const cols = tableColumns(db, "daily_claims");
    const streakCol = choose(cols, ["best_streak", "streak", "current_streak"]);
    if (!streakCol) return markMissingColumns("streaks", "daily_claims", ["best_streak"]);
    if (cols.includes("user_id") && tableExists(db, "users") && columnExists(db, "users", "user_id") && columnExists(db, "users", "username")) {
      const sql = `SELECT u.${sqlIdent("username")} AS username, dc.${sqlIdent(streakCol)} AS streak${cols.includes("total_claims") ? `, dc.${sqlIdent("total_claims")} AS total_claims` : ""} FROM ${sqlIdent("daily_claims")} dc JOIN ${sqlIdent("users")} u ON u.${sqlIdent("user_id")} = dc.${sqlIdent("user_id")} WHERE ${numericExpr(streakCol, "dc.")} > 0 ORDER BY ${numericExpr(streakCol, "dc.")} DESC LIMIT ?`;
      return runSql("streaks", "daily_claims", ["user_id", streakCol, "total_claims"].filter((col) => cols.includes(col)), sql, [limit], "Exact in-room !topstreaks source from daily_claims.");
    }
    const uname = choose(cols, ["username", "user_name", "player_name"]);
    if (!uname) return markMissingColumns("streaks", "daily_claims", ["username"]);
    source("streaks", "daily_claims", [uname, streakCol], "connected", "Daily streaks from daily_claims.");
    return addRank(safeRows(db, "daily_claims", [uname, streakCol, "total_claims"].filter((col) => cols.includes(col)), {
      where: `${numericExpr(streakCol)} > 0`,
      orderBy: `${numericExpr(streakCol)} DESC`,
      limit: String(limit),
    }).map((row) => ({ username: row.username || row.user_name || row.player_name, streak: row[streakCol] ?? 0, total_claims: row.total_claims ?? "" })));
  })();

  const topdonators = sumByUser("topdonators", "gold_tip_events", "from_username", "gold_amount", "total_gold", "Exact in-room !topdonators / gold supporters.");
  const toptippers = sumByUser("toptippers", "p2p_gold_tip_logs", "sender_username", "amount", "total_gold", "Exact in-room !toptippers P2P senders.");
  const toptipped = sumByUser("toptipped", "p2p_gold_tip_logs", "receiver_username", "amount", "total_gold", "Exact in-room !toptipped P2P receivers.");
  const tipTransactions = tableExists(db, "tip_transactions")
    ? sumByUser("tip_transactions", "tip_transactions", "username", "gold_amount", "total_gold", "Tip leaderboard from tip_transactions.")
    : markMissingTable("tip_transactions", "tip_transactions");

  const radioLiked = tableExists(db, "dj_ratings") && columnExists(db, "dj_ratings", "rating")
    ? countBy("radio_liked", "dj_ratings", "song_key", "likes", "Top liked radio tracks.", "LOWER(rating) IN ('like','liked','up','1')")
    : (tableExists(db, "dj_ratings") ? markMissingColumns("radio_liked", "dj_ratings", ["rating"]) : markMissingTable("radio_liked", "dj_ratings"));
  const radioDisliked = tableExists(db, "dj_ratings") && columnExists(db, "dj_ratings", "rating")
    ? countBy("radio_disliked", "dj_ratings", "song_key", "dislikes", "Top disliked radio tracks.", "LOWER(rating) IN ('dislike','disliked','down','-1')")
    : (tableExists(db, "dj_ratings") ? markMissingColumns("radio_disliked", "dj_ratings", ["rating"]) : markMissingTable("radio_disliked", "dj_ratings"));

  const profiles = tableExists(db, "users") && userCols.includes("username")
    ? addRank(safeRows(db, "users", ["username", "level", "xp", balanceCol, "total_games_won", "total_coins_earned"].filter(Boolean), {
        orderBy: [
          userCols.includes("level") ? `${numericExpr("level")} DESC` : "",
          userCols.includes("xp") ? `${numericExpr("xp")} DESC` : "",
          balanceCol ? `${numericExpr(balanceCol)} DESC` : "",
        ].filter(Boolean).join(", ") || "username ASC",
        limit: String(limit),
      }).map((row) => ({
        username: row.username,
        level: row.level ?? "",
        xp: row.xp ?? "",
        balance: row.balance ?? row.coins ?? "",
        games_won: row.total_games_won ?? "",
        coins_earned: row.total_coins_earned ?? "",
      })))
    : (tableExists(db, "users") ? markMissingColumns("profiles", "users", [userCols.includes("username") ? "xp" : "username"]) : markMissingTable("profiles", "users"));
  if (profiles.length) source("profiles", "users", ["username", "level", "xp", balanceCol, "total_games_won", "total_coins_earned"].filter(Boolean), "connected", "Public-safe !profile summary fields.");

  const eventPoints = (() => {
    if (!tableExists(db, "event_points")) return markMissingTable("event_points", "event_points");
    const cols = tableColumns(db, "event_points");
    const pointsCol = choose(cols, ["points", "total_points", "score"]);
    if (!pointsCol) return markMissingColumns("event_points", "event_points", ["points"]);
    if (cols.includes("username")) {
      return runSql(
        "event_points",
        "event_points",
        ["username", pointsCol],
        `SELECT NULLIF(username,'') AS username, ${sqlIdent(pointsCol)} AS points FROM event_points ORDER BY ${numericExpr(pointsCol)} DESC LIMIT ?`,
        [limit],
        "Event points with stored username.",
      ).map((row) => ({ ...row, username: row.username || "Unknown Player" }));
    }
    if (cols.includes("user_id") && tableExists(db, "users") && columnExists(db, "users", "user_id") && columnExists(db, "users", "username")) {
      return runSql(
        "event_points",
        "event_points",
        ["user_id", pointsCol],
        `SELECT COALESCE(NULLIF(u.username,''), 'Unknown Player') AS username, ep.user_id AS fallback_id, ep.${sqlIdent(pointsCol)} AS points FROM event_points ep LEFT JOIN users u ON u.user_id = ep.user_id ORDER BY ${numericExpr(pointsCol, "ep.")} DESC LIMIT ?`,
        [limit],
        "Event points joined to users for public display names.",
      );
    }
    return markMissingColumns("event_points", "event_points", ["username"]);
  })();

  const radioRequesters = (() => {
    if (tableExists(db, "radio_user_stats") && columnExists(db, "radio_user_stats", "username") && columnExists(db, "radio_user_stats", "requests_count")) {
      return runSql(
        "radio_requesters",
        "radio_user_stats",
        ["username", "requests_count"],
        "SELECT username, requests_count AS requests FROM radio_user_stats WHERE COALESCE(username,'')<>'' AND COALESCE(CAST(requests_count AS REAL),0)>0 ORDER BY COALESCE(CAST(requests_count AS REAL),0) DESC LIMIT ?",
        [limit],
        "Top requesters from radio_user_stats.requests_count.",
      );
    }
    return base.radio || [];
  })();

  const radioTracks = (() => {
    if (!tableExists(db, "radio_song_stats")) return markMissingTable("radio_tracks", "radio_song_stats");
    const cols = tableColumns(db, "radio_song_stats");
    const countCol = choose(cols, ["play_count", "played_count", "plays"]);
    if (!countCol) return markMissingColumns("radio_tracks", "radio_song_stats", ["play_count"]);
    return runSql(
      "radio_tracks",
      "radio_song_stats",
      ["song_key", "title", cols.includes("artist") ? "artist" : "", countCol].filter(Boolean),
      `SELECT COALESCE(NULLIF(title,''), NULLIF(song_key,''), 'Unknown Track') AS title${cols.includes("artist") ? ", artist" : ""}, ${sqlIdent(countCol)} AS plays FROM radio_song_stats WHERE COALESCE(NULLIF(title,''), NULLIF(song_key,''), '')<>'' AND ${numericExpr(countCol)}>0 ORDER BY ${numericExpr(countCol)} DESC LIMIT ?`,
      [limit],
      "Most played/requested songs from radio_song_stats.",
    );
  })();

  function enrichRadioSongRequesters(rows) {
    if (!tableExists(db, "yt_request_jobs") || !columnExists(db, "yt_request_jobs", "title") || !columnExists(db, "yt_request_jobs", "username")) return rows;
    const cols = ["title", "artist", "username"].filter((col) => columnExists(db, "yt_request_jobs", col));
    const requests = safeRows(db, "yt_request_jobs", cols, { where: "COALESCE(username,'')<>'' AND COALESCE(title,'')<>''", limit: "10000" });
    const byTitle = new Map();
    for (const req of requests) {
      const title = String(req.title || "").trim().toLowerCase().slice(0, 150);
      const key = `${title}|${String(req.artist || "").trim().toLowerCase()}`.slice(0, 150);
      if (title && !byTitle.has(title)) byTitle.set(title, req.username);
      if (key && !byTitle.has(key)) byTitle.set(key, req.username);
    }
    return rows.map((row) => {
      const title = String(row.title || row.name || "").trim().toLowerCase().slice(0, 150);
      const key = `${title}|${String(row.artist || "").trim().toLowerCase()}`.slice(0, 150);
      return { ...row, requester: byTitle.get(key) || byTitle.get(title) || "" };
    });
  }

  function radioRatingRows(name, rating, outCol, notes) {
    if (!tableExists(db, "dj_ratings")) {
      const statCol = rating === "like" ? "like_count" : "dislike_count";
      if (tableExists(db, "radio_song_stats") && columnExists(db, "radio_song_stats", statCol)) {
        return enrichRadioSongRequesters(runSql(
          name,
          "radio_song_stats",
          ["song_key", "title", statCol],
          `SELECT COALESCE(NULLIF(title,''), NULLIF(song_key,''), 'Unknown Track') AS title${columnExists(db, "radio_song_stats", "artist") ? ", artist" : ""}, ${sqlIdent(statCol)} AS ${sqlIdent(outCol)} FROM radio_song_stats WHERE COALESCE(NULLIF(title,''), NULLIF(song_key,''), '')<>'' AND ${numericExpr(statCol)}>0 ORDER BY ${numericExpr(statCol)} DESC LIMIT ?`,
          [limit],
          `${notes} Fallback source: radio_song_stats.${statCol}.`,
        ));
      }
      return markMissingTable(name, "dj_ratings");
    }
    const cols = tableColumns(db, "dj_ratings");
    const missing = ["song_key", "rating"].filter((col) => !cols.includes(col));
    if (missing.length) return markMissingColumns(name, "dj_ratings", missing);
    const hasSongStats = tableExists(db, "radio_song_stats") && columnExists(db, "radio_song_stats", "song_key");
    const hasSongArtist = hasSongStats && columnExists(db, "radio_song_stats", "artist");
    const sql = hasSongStats
      ? `SELECT COALESCE(NULLIF(rss.title,''), NULLIF(dr.song_key,''), 'Unknown Track') AS title${hasSongArtist ? ", COALESCE(NULLIF(rss.artist,''), '') AS artist" : ""}, COUNT(*) AS ${sqlIdent(outCol)} FROM dj_ratings dr LEFT JOIN radio_song_stats rss ON rss.song_key = dr.song_key WHERE LOWER(dr.rating)=? AND COALESCE(NULLIF(dr.song_key,''),'')<>'' GROUP BY dr.song_key ORDER BY ${sqlIdent(outCol)} DESC LIMIT ?`
      : `SELECT COALESCE(NULLIF(song_key,''), 'Unknown Track') AS title, COUNT(*) AS ${sqlIdent(outCol)} FROM dj_ratings WHERE LOWER(rating)=? AND COALESCE(NULLIF(song_key,''),'')<>'' GROUP BY song_key ORDER BY ${sqlIdent(outCol)} DESC LIMIT ?`;
    return enrichRadioSongRequesters(runSql(name, "dj_ratings", ["song_key", "rating"], sql, [rating, limit], notes)
      .filter((row) => row.title && row.title !== "Unknown Track"));
  }
  const radioLikedSongs = radioRatingRows("radio_liked", "like", "likes", "Top liked songs from dj_ratings joined to radio_song_stats titles.");
  const radioDislikedSongs = radioRatingRows("radio_disliked", "dislike", "dislikes", "Top disliked songs from dj_ratings joined to radio_song_stats titles.");
  const radioLikedRequesters = (() => {
    if (tableExists(db, "yt_request_jobs") && tableExists(db, "dj_ratings") && columnExists(db, "yt_request_jobs", "username") && columnExists(db, "yt_request_jobs", "title") && columnExists(db, "dj_ratings", "song_key") && columnExists(db, "dj_ratings", "rating")) {
      const hasArtist = columnExists(db, "yt_request_jobs", "artist");
      const joinExpr = hasArtist
        ? "(dr.song_key = LOWER(SUBSTR(rj.title,1,150)) OR dr.song_key = LOWER(SUBSTR(rj.title || '|' || COALESCE(rj.artist,''),1,150)))"
        : "dr.song_key = LOWER(SUBSTR(rj.title,1,150))";
      return runSql(
        "radio_liked_requesters",
        "yt_request_jobs",
        ["username", "title"],
        `SELECT rj.username AS username, COUNT(*) AS likes FROM yt_request_jobs rj JOIN dj_ratings dr ON ${joinExpr} WHERE LOWER(dr.rating)='like' AND COALESCE(rj.username,'')<>'' GROUP BY rj.username ORDER BY likes DESC LIMIT ?`,
        [limit],
        "Requesters whose requested songs accumulated likes.",
      );
    }
    if (tableExists(db, "radio_user_stats") && columnExists(db, "radio_user_stats", "likes_count")) {
      return runSql(
        "radio_liked_requesters",
        "radio_user_stats",
        ["username", "likes_count"],
        "SELECT username, likes_count AS likes FROM radio_user_stats WHERE COALESCE(username,'')<>'' AND COALESCE(CAST(likes_count AS REAL),0)>0 ORDER BY COALESCE(CAST(likes_count AS REAL),0) DESC LIMIT ?",
        [limit],
        "Fallback: radio_user_stats.likes_count.",
      );
    }
    return markMissingTable("radio_liked_requesters", "dj_ratings");
  })();
  const blackjackStats = activeBlackjackStats();

  const leaderboards = {
    richest: rich,
    xp,
    top_xp: xp,
    level,
    most_games_won: mostGamesWon,
    casino_overall: base.casino || [],
    blackjack: blackjackStats,
    poker: base.poker || [],
    mining_top: base.mining || [],
    mining_heaviest_ore: miningHeaviestOre,
    mining_most_valuable: miningMostValuable,
    mining_rarest: miningRarest,
    mining_streaks: miningStreaks,
    fishing_top: base.fishing || [],
    fishing_heaviest_fish: fishingHeaviestFish,
    fishing_most_valuable: fishingMostValuable,
    fishing_rarest: fishingRarest,
    fishing_streaks: fishingStreaks,
    event_points: eventPoints,
    radio_requesters: radioRequesters,
    radio_tracks: radioTracks,
    radio_liked: radioLikedSongs,
    radio_disliked: radioDislikedSongs,
    radio_liked_requesters: radioLikedRequesters,
    reputation: base.reputation || [],
    topdonators,
    toptippers,
    toptipped,
    tip_transactions: tipTransactions,
    streaks,
    profiles,
  };
  const userMap = userNameMap();
  const hiddenNames = hiddenNameSet();
  const looksLikeId = (value) => /^[a-z0-9_-]{18,}$/i.test(String(value || ""));
  const resolvedPlayerName = (row) => {
    const explicit = row.username || row.player || row.requester || row.requester_username || row.from_username || row.sender_username || row.receiver_username;
    if (explicit && !looksLikeId(explicit)) return explicit;
    const id = row.user_id || row.fallback_id || row.player_id || row.requester_id || explicit;
    const resolved = userMap.get(String(id || ""));
    if (resolved) return resolved;
    return explicit && !looksLikeId(explicit) ? explicit : "";
  };
  const normalizedLeaderboards = {};
  const leaderboardDiagnostics = [];
  for (const [key, rows] of Object.entries(leaderboards)) {
    const before = Array.isArray(rows) ? rows.length : 0;
    const normalizedRows = (Array.isArray(rows) ? rows : []).map((row) => {
      const username = resolvedPlayerName(row);
      const fallbackId = row.fallback_id || row.user_id || (looksLikeId(row.username) ? row.username : "");
      return {
        ...row,
        username: username || (fallbackId ? "Unknown Player" : row.username),
        fallback_id: fallbackId || row.fallback_id,
      };
    });
    const filteredRows = normalizedRows.filter((row) => {
      const name = String(resolvedPlayerName(row) || row.username || "").toLowerCase();
      return !hiddenNames.has(name);
    });
    normalizedLeaderboards[key] = addRank(filteredRows.map(({ rank, ...row }) => row));
    const sourceInfo = sources[key] || sources[key.replace(/^radio_tracks$/, "radio_songs")] || {};
    const hiddenCount = before - filteredRows.length;
    const rawStatus = sourceInfo.status ? String(sourceInfo.status).toUpperCase() : (before ? "CONNECTED" : "EMPTY");
    leaderboardDiagnostics.push({
      key,
      title: key.replaceAll("_", " "),
      source_table: sourceInfo.table || "",
      source_columns: sourceInfo.columns || [],
      row_count_before_filter: before,
      row_count_after_filter: normalizedLeaderboards[key].length,
      hidden_staff_bot_count: hiddenCount,
      status: before > 0 && filteredRows.length === 0 && hiddenCount > 0 ? "FILTERED_EMPTY" : rawStatus,
      notes: sourceInfo.notes || "",
    });
  }
  const sourceRows = Object.entries(sources).map(([name, info]) => ({ name, ...info }));
  const diagnostics = {
    generated_at: nowIso(),
    db_path: DB_PATH,
    hide_staff: hideStaff,
    hide_bots: hideBots,
    connected_sources: sourceRows.filter((row) => row.status === "connected"),
    source_errors: sourceErrors,
    row_counts: Object.fromEntries([...new Set(sourceRows.map((row) => row.table).filter((table) => table && table !== "multiple"))].map((table) => [table, rowCountSafe(db, table) ?? 0])),
    leaderboards: leaderboardDiagnostics,
    missing_tables: [...missingTables],
    missing_columns: [...missingColumns].filter(Boolean),
    empty_sources: sourceRows.filter((row) => row.status === "empty" || (row.status === "connected" && Array.isArray(normalizedLeaderboards[row.name]) && normalizedLeaderboards[row.name].length === 0)).map((row) => row.name),
    sources,
  };

  return {
    ...base,
    menu,
    leaderboards: normalizedLeaderboards,
    rich: normalizedLeaderboards.richest,
    rich_list: normalizedLeaderboards.richest,
    xp: normalizedLeaderboards.xp,
    top_xp: normalizedLeaderboards.top_xp,
    level: normalizedLeaderboards.level,
    most_games_won: normalizedLeaderboards.most_games_won,
    mining_heaviest_ore: normalizedLeaderboards.mining_heaviest_ore,
    mining_most_valuable: normalizedLeaderboards.mining_most_valuable,
    mining_rarest: normalizedLeaderboards.mining_rarest,
    mining_streaks: normalizedLeaderboards.mining_streaks,
    fishing_heaviest_fish: normalizedLeaderboards.fishing_heaviest_fish,
    fishing_most_valuable: normalizedLeaderboards.fishing_most_valuable,
    fishing_rarest: normalizedLeaderboards.fishing_rarest,
    fishing_streaks: normalizedLeaderboards.fishing_streaks,
    radio_liked: normalizedLeaderboards.radio_liked,
    radio_disliked: normalizedLeaderboards.radio_disliked,
    radio_liked_requesters: normalizedLeaderboards.radio_liked_requesters,
    topdonators: normalizedLeaderboards.topdonators,
    toptippers: normalizedLeaderboards.toptippers,
    toptipped: normalizedLeaderboards.toptipped,
    tip_transactions: normalizedLeaderboards.tip_transactions,
    streaks: normalizedLeaderboards.streaks,
    profiles: normalizedLeaderboards.profiles,
    diagnostics,
    miners: normalizedLeaderboards.mining_top,
    mining: normalizedLeaderboards.mining_top,
    fishers: normalizedLeaderboards.fishing_top,
    fishing: normalizedLeaderboards.fishing_top,
    events: normalizedLeaderboards.event_points,
    radio: normalizedLeaderboards.radio_requesters,
    top_requesters: normalizedLeaderboards.radio_requesters,
    metadata: {
      generated_at: diagnostics.generated_at,
      db_path: diagnostics.db_path,
      missing_tables: diagnostics.missing_tables,
      missing_columns: diagnostics.missing_columns,
      source_errors: diagnostics.source_errors,
      row_counts: diagnostics.row_counts,
      sources,
    },
  };
}

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
  { field: "mining_announce_min_rarity", label: "Announce Minimum Rarity", table: "mining_settings", key: "mining_announce_min_rarity", type: "enum", fallback: "legendary", values: ["common", "uncommon", "epic", "legendary", "mythic", "prismatic", "exotic"] },
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

function writeKeyValue(db, table, key, value) {
  if (!KEY_VALUE_SETTING_TABLES.has(table)) throw new Error("unsupported_setting_table");
  if (!tableExists(db, table)) throw new Error(`${table}_missing`);
  db.prepare(`INSERT OR REPLACE INTO ${sqlIdent(table)} (key, value) VALUES (?, ?)`).run(key, String(value));
}

const ECONOMY_SETTING_FIELDS = [
  { key: "daily_coins", label: "Daily Coins", type: "int", min: 0, max: 1000000, fallback: "50" },
  { key: "trivia_reward", label: "Trivia Reward", type: "int", min: 0, max: 1000000, fallback: "20" },
  { key: "scramble_reward", label: "Scramble Reward", type: "int", min: 0, max: 1000000, fallback: "20" },
  { key: "riddle_reward", label: "Riddle Reward", type: "int", min: 0, max: 1000000, fallback: "25" },
  { key: "max_balance", label: "Max Balance", type: "int", min: 0, max: 100000000000, fallback: "1000000" },
];

const BANK_SETTING_FIELDS = [
  { key: "min_send_amount", label: "Minimum Send", type: "int", min: 1, max: 1000000000, fallback: "10" },
  { key: "max_send_amount", label: "Maximum Send", type: "int", min: 1, max: 1000000000, fallback: "5000" },
  { key: "daily_send_limit", label: "Daily Send Limit", type: "int", min: 1, max: 1000000000, fallback: "20000" },
  { key: "send_tax_percent", label: "Transfer Tax", type: "float", min: 0, max: 100, fallback: "0" },
  { key: "new_account_days", label: "New Account Days", type: "int", min: 0, max: 3650, fallback: "0" },
  { key: "min_level_to_send", label: "Minimum Send Level", type: "int", min: 0, max: 1000000, fallback: "0" },
  { key: "min_total_earned_to_send", label: "Minimum Total Earned", type: "int", min: 0, max: 100000000000, fallback: "0" },
  { key: "min_daily_claim_days_to_send", label: "Minimum Daily Claims", type: "int", min: 0, max: 1000000, fallback: "0" },
  { key: "high_risk_blocks", label: "High Risk Blocks", type: "bool", fallback: "false" },
];

function normalizeTypedSetting(spec, value) {
  if (spec.type === "bool") {
    return (value === true || value === 1 || ["1", "true", "yes", "on", "enabled"].includes(String(value ?? "").toLowerCase()))
      ? "true"
      : "false";
  }
  if (spec.type === "int") {
    const n = Number(value);
    if (!Number.isFinite(n) || !Number.isInteger(n)) throw new Error(`${spec.key}_must_be_integer`);
    if (spec.min !== undefined && n < spec.min) throw new Error(`${spec.key}_too_low`);
    if (spec.max !== undefined && n > spec.max) throw new Error(`${spec.key}_too_high`);
    return String(n);
  }
  if (spec.type === "float") {
    const n = Number(value);
    if (!Number.isFinite(n)) throw new Error(`${spec.key}_must_be_number`);
    if (spec.min !== undefined && n < spec.min) throw new Error(`${spec.key}_too_low`);
    if (spec.max !== undefined && n > spec.max) throw new Error(`${spec.key}_too_high`);
    return String(n);
  }
  const out = String(value ?? "");
  if (out.length > 2000) throw new Error(`${spec.key}_too_long`);
  return out;
}

function readTypedSettings(db, table, fields) {
  const map = readKeyValueMap(db, table);
  return {
    source: table,
    table_exists: tableExists(db, table),
    settings: Object.fromEntries(fields.map((f) => [f.key, map[f.key] ?? f.fallback ?? ""])),
    fields,
  };
}

function normalizeSettingsBody(body, fields) {
  const specs = new Map(fields.map((f) => [f.key, f]));
  const out = {};
  for (const [key, value] of Object.entries(body || {})) {
    const spec = specs.get(key);
    if (!spec) continue;
    out[key] = normalizeTypedSetting(spec, value);
  }
  return out;
}

function validateBankSettings(next) {
  const num = (key) => Number(next[key]);
  if (next.min_send_amount !== undefined && next.max_send_amount !== undefined && num("min_send_amount") > num("max_send_amount")) {
    throw new Error("min_send_amount_cannot_exceed_max_send_amount");
  }
  if (next.max_send_amount !== undefined && next.daily_send_limit !== undefined && num("max_send_amount") > num("daily_send_limit")) {
    throw new Error("max_send_amount_cannot_exceed_daily_send_limit");
  }
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

const GAME_RARITY_ORDER = ["common", "uncommon", "epic", "legendary", "mythic", "prismatic", "exotic"];
const MINING_RARITY_ORDER = GAME_RARITY_ORDER;
const FISHING_RARITY_ORDER = GAME_RARITY_ORDER;
const RARITY_LABELS = {
  common: "Common",
  uncommon: "Uncommon",
  rare: "Rare",
  epic: "Epic",
  legendary: "Legendary",
  mythic: "Mythic",
  ultra_rare: "Mythic",
  exotic: "Exotic",
  prismatic: "Prismatic",
};

function normalizeRarity(value) {
  const raw = String(value || "common").trim().toLowerCase();
  return raw === "ultra_rare" ? "mythic" : raw;
}

function rarityRank(value, order = MINING_RARITY_ORDER) {
  const idx = order.indexOf(normalizeRarity(value));
  return idx === -1 ? order.length : idx;
}

function chanceTextFromPercent(value, zeroText = "Not currently dropping") {
  if (value === null || value === undefined || value === "") return "Unknown";
  const n = Number(value);
  if (!Number.isFinite(n)) return "Unknown";
  if (n <= 0) return zeroText;
  const oneIn = n > 0 ? Math.max(1, Math.round(100 / n)) : null;
  if (n < 0.0001) return oneIn ? `Very Rare · about 1 in ${oneIn.toLocaleString("en-US")}` : "Very Rare";
  const pct = `${Number(n.toFixed(4)).toString()}%`;
  return oneIn ? `${pct} · about 1 in ${oneIn.toLocaleString("en-US")}` : pct;
}

function groupByRarity(rows, order) {
  const grouped = Object.fromEntries(order.map((rarity) => [rarity, []]));
  for (const row of rows) {
    const rarity = normalizeRarity(row.rarity);
    if (!grouped[rarity]) grouped[rarity] = [];
    grouped[rarity].push(row);
  }
  return grouped;
}

function commandExists(command) {
  const names = [command, command.replace(/^!/, "")].map((x) => String(x || "").replace(/^!/, "").toLowerCase());
  const files = [
    path.join(BOT_ROOT, "modules", "command_registry.py"),
    path.join(BOT_ROOT, "modules", "help_cmds.py"),
    path.join(BOT_ROOT, "modules", "multi_bot.py"),
    path.join(BOT_ROOT, "modules", "cmd_audit.py"),
    path.join(BOT_ROOT, "modules", "mining.py"),
    path.join(BOT_ROOT, "modules", "fishing.py"),
  ];
  const source = files.map((filePath) => fs.existsSync(filePath) ? fs.readFileSync(filePath, "utf8").toLowerCase() : "").join("\n");
  return names.some((name) => new RegExp(`["'!]${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(source));
}

function verifiedCommands(specs) {
  return specs
    .filter((spec) => commandExists(spec.command))
    .map((spec) => ({
      command: spec.display || `!${spec.command}`,
      description: spec.description,
    }));
}

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
  if (!tableExists(db, "mining_items")) return [];
  const cols = ["item_id", "name", "emoji", "icon", "description", "rarity", "item_type", "sell_value", "chance_percent", "event_only", "drop_enabled", "enabled", "archived", "created_at", "updated_at"]
    .filter((col) => columnExists(db, "mining_items", col));
  const select = cols.map((col) => `mi.${sqlIdent(col)} AS ${sqlIdent(col)}`).join(", ");
  const where = includeDisabled ? "" : (columnExists(db, "mining_items", "drop_enabled") ? "WHERE mi.drop_enabled=1" : "");
  if (tableExists(db, "mining_item_weights")) {
    try {
      db.prepare(`
        INSERT OR IGNORE INTO mining_item_weights (item_id, drop_weight, updated_at)
        SELECT item_id, 1, datetime('now')
        FROM mining_items
        WHERE item_type='ore'
      `).run();
    } catch {}
    return db.prepare(`
      SELECT ${select}, COALESCE(miw.drop_weight, 1) AS drop_weight
      FROM mining_items mi
      LEFT JOIN mining_item_weights miw ON mi.item_id=miw.item_id
      ${where}
      ORDER BY mi.rarity, mi.sell_value, mi.name
      LIMIT 1000
    `).all();
  }
  return db.prepare(`
    SELECT ${select}, 1 AS drop_weight
    FROM mining_items mi
    ${where}
    ORDER BY mi.rarity, mi.sell_value, mi.name
    LIMIT 1000
  `).all();
}

function readRaritySettings(db, system) {
  if (!tableExists(db, "game_rarity_settings")) return {};
  try {
    const rows = db.prepare("SELECT * FROM game_rarity_settings WHERE system=?").all(system);
    return Object.fromEntries(rows.map((row) => [normalizeRarity(row.rarity), row]));
  } catch {
    return {};
  }
}

function defaultRarityWeight(system, rarity) {
  if (system === "mining") return Number(MINING_RARITY_PROBS[rarity] || 0);
  if (system === "fishing") {
    const { fish } = readFishingCodeCatalog();
    return fish
      .filter((row) => normalizeRarity(row.rarity) === rarity)
      .reduce((sum, row) => sum + Number(row.drop_weight || row.catch_weight || 0), 0);
  }
  return null;
}

function calculateMiningDropRows(db) {
  const items = miningItemRows(db, false);
  const byRarity = items.reduce((acc, item) => {
    const rarity = normalizeRarity(item.rarity);
    acc[rarity] = acc[rarity] || [];
    acc[rarity].push(item);
    return acc;
  }, {});
  const settings = readRaritySettings(db, "mining");
  const rarityWeights = { ...MINING_RARITY_PROBS };
  for (const [rarity, row] of Object.entries(settings)) {
    if (Number(row.enabled ?? 1) !== 1) {
      rarityWeights[rarity] = 0;
      continue;
    }
    const raw = row.base_weight ?? row.base_chance;
    if (raw !== undefined && raw !== null && raw !== "" && Number.isFinite(Number(raw))) rarityWeights[rarity] = Math.max(0, Number(raw));
  }
  const totalRarityWeight = Object.values(rarityWeights).reduce((sum, value) => sum + Math.max(0, Number(value || 0)), 0);
  return items.map((item) => {
    const rarity = normalizeRarity(item.rarity);
    const rarityChance = totalRarityWeight > 0 ? (Math.max(0, Number(rarityWeights[rarity] || 0)) / totalRarityWeight) * 100 : 0;
    const explicitChance = item.chance_percent !== undefined && item.chance_percent !== null && item.chance_percent !== "" ? Number(item.chance_percent) : null;
    const customWeight = item.drop_weight !== undefined && item.drop_weight !== null && item.drop_weight !== "" ? Number(item.drop_weight) : null;
    const peerWeightTotal = byRarity[rarity]?.reduce((sum, row) => sum + (row.drop_weight !== undefined && row.drop_weight !== null && row.drop_weight !== "" ? Number(row.drop_weight || 0) : 1), 0) || 0;
    const chance = explicitChance !== null && Number.isFinite(explicitChance)
      ? explicitChance
      : (customWeight !== null && peerWeightTotal > 0 ? rarityChance * (customWeight / peerWeightTotal) : 0);
    return {
      item_id: item.item_id,
      ore: item.name,
      rarity,
      weight: customWeight,
      chance_percent: chance,
      drop_weight: customWeight,
      rarity_chance_percent: rarityChance,
      enabled: item.drop_enabled,
      event_only: 0,
      source: "game_rarity_settings + mining_items + mining_item_weights",
      writable: tableExists(db, "mining_item_weights"),
    };
  });
}

function calculateFishDropRows() {
  const { fish } = readFishingCodeCatalog();
  const total = fish.reduce((sum, row) => sum + Number(row.drop_weight || 0), 0);
  return fish.map((row) => ({
    fish_id: row.fish_id,
    fish: row.name,
    rarity: normalizeRarity(row.rarity),
    weight: row.drop_weight,
    chance_percent: total ? (Number(row.drop_weight || 0) / total) * 100 : 0,
    enabled: 1,
    event_only: 0,
    source: "modules/fishing.py FISH_CATALOG drop_weight",
    writable: false,
  }));
}

function fishCatalogRows(db, includeDisabled = true) {
  if (!db || !tableExists(db, "fish_catalog")) return [];
  const cols = ["fish_id", "name", "emoji", "rarity", "base_value", "min_weight", "max_weight", "catch_weight", "catch_enabled", "event_only", "created_at", "updated_at"]
    .filter((col) => columnExists(db, "fish_catalog", col));
  return safeRows(db, "fish_catalog", cols, {
    where: includeDisabled ? "" : (columnExists(db, "fish_catalog", "catch_enabled") ? "catch_enabled=1" : ""),
    orderBy: "rarity, base_value, name",
    limit: "1000",
  });
}

function calculateFishDropRowsFromDb(db) {
  const items = fishCatalogRows(db, false);
  if (!items.length) return calculateFishDropRows();
  const byRarity = items.reduce((acc, item) => {
    const rarity = normalizeRarity(item.rarity);
    acc[rarity] = acc[rarity] || [];
    acc[rarity].push(item);
    return acc;
  }, {});
  const rarityWeights = {};
  for (const item of items) {
    const rarity = normalizeRarity(item.rarity);
    rarityWeights[rarity] = (rarityWeights[rarity] || 0) + Math.max(0, Number(item.catch_weight || 0));
  }
  for (const [rarity, row] of Object.entries(readRaritySettings(db, "fishing"))) {
    if (Number(row.enabled ?? 1) !== 1) {
      rarityWeights[rarity] = 0;
      continue;
    }
    const raw = row.base_weight ?? row.base_chance;
    if (raw !== undefined && raw !== null && raw !== "" && Number.isFinite(Number(raw))) rarityWeights[rarity] = Math.max(0, Number(raw));
  }
  const totalRarityWeight = Object.values(rarityWeights).reduce((sum, value) => sum + Math.max(0, Number(value || 0)), 0);
  return items.map((item) => {
    const rarity = normalizeRarity(item.rarity);
    const rarityChance = totalRarityWeight > 0 ? (Math.max(0, Number(rarityWeights[rarity] || 0)) / totalRarityWeight) * 100 : 0;
    const weight = Number(item.catch_weight || 0);
    const peerWeightTotal = byRarity[rarity]?.reduce((sum, row) => sum + Math.max(0, Number(row.catch_weight || 0)), 0) || 0;
    const chance = peerWeightTotal > 0 ? rarityChance * (Math.max(0, weight) / peerWeightTotal) : 0;
    return {
      fish_id: item.fish_id,
      fish: item.name,
      rarity,
      weight,
      catch_weight: weight,
      chance_percent: chance,
      rarity_chance_percent: rarityChance,
      enabled: item.catch_enabled,
      event_only: item.event_only ?? 0,
      source: "game_rarity_settings + fish_catalog.catch_weight",
      writable: true,
    };
  });
}

function enrichedMiningOreRows(db, includeDisabled = true) {
  const odds = calculateMiningDropRows(db);
  return miningItemRows(db, includeDisabled).map((row) => {
    const odd = odds.find((o) => o.item_id === row.item_id || o.ore === row.name);
    const chance = odd?.chance_percent ?? null;
    return {
      ...row,
      rarity: normalizeRarity(row.rarity),
      rarity_label: RARITY_LABELS[normalizeRarity(row.rarity)] || row.rarity || "Common",
      weight: odd?.weight ?? null,
      drop_weight: row.drop_weight ?? odd?.drop_weight ?? null,
      chance_percent: chance,
      chance_label: chanceTextFromPercent(chance, "Not currently dropping"),
      event_only: row.event_only ?? odd?.event_only ?? 0,
      enabled: row.drop_enabled ?? row.enabled ?? 1,
      source: "game_rarity_settings + mining_items + mining_item_weights",
    };
  }).sort((a, b) => rarityRank(a.rarity, MINING_RARITY_ORDER) - rarityRank(b.rarity, MINING_RARITY_ORDER)
    || Number(b.chance_percent ?? -1) - Number(a.chance_percent ?? -1)
    || Number(b.sell_value || 0) - Number(a.sell_value || 0)
    || String(a.name || "").localeCompare(String(b.name || "")));
}

function enrichedFishingRows(db = null) {
  const dbRows = db ? fishCatalogRows(db, true) : [];
  const { fish, source, error } = readFishingCodeCatalog();
  const codeById = new Map(fish.map((row) => [row.fish_id, row]));
  const sourceRows = dbRows.length ? dbRows.map((row) => ({ ...codeById.get(row.fish_id), ...row, drop_weight: row.catch_weight })) : fish;
  const odds = dbRows.length ? calculateFishDropRowsFromDb(db) : calculateFishDropRows();
  const rows = sourceRows.map((row) => {
    const odd = odds.find((o) => o.fish_id === row.fish_id || o.fish === row.name);
    const chance = odd?.chance_percent ?? null;
    return {
      ...row,
      rarity: normalizeRarity(row.rarity),
      rarity_label: RARITY_LABELS[normalizeRarity(row.rarity)] || row.rarity || "Common",
      weight: odd?.weight ?? row.catch_weight ?? row.drop_weight ?? null,
      catch_weight: row.catch_weight ?? row.drop_weight ?? odd?.weight ?? null,
      drop_weight: row.catch_weight ?? row.drop_weight ?? odd?.weight ?? null,
      chance_percent: chance,
      chance_label: chanceTextFromPercent(chance, "Not currently catching"),
      enabled: row.catch_enabled ?? 1,
      event_only: row.event_only ?? 0,
      source: dbRows.length ? "fish_catalog + game_rarity_settings" : "modules/fishing.py FISH_CATALOG",
      writable: !!dbRows.length,
    };
  }).sort((a, b) => rarityRank(a.rarity, FISHING_RARITY_ORDER) - rarityRank(b.rarity, FISHING_RARITY_ORDER)
    || Number(b.chance_percent ?? -1) - Number(a.chance_percent ?? -1)
    || Number(b.base_value || 0) - Number(a.base_value || 0)
    || Number(b.max_weight || 0) - Number(a.max_weight || 0)
    || String(a.name || "").localeCompare(String(b.name || "")));
  return { rows, source: dbRows.length ? "fish_catalog" : source, error, runtime_connected: !!dbRows.length };
}

function raritySummaryRows(items, order, { valueKey = "value", weightKey = "weight", zeroText = "Not currently dropping", source = "runtime_code", itemLabel = "items", system = "" } = {}, db = null) {
  const enabled = items.filter((row) => row.enabled !== 0 && row.enabled !== false && row.drop_enabled !== 0 && row.catch_enabled !== 0);
  const settings = db && system ? readRaritySettings(db, system) : {};
  const runtimeConnected = !!db && tableExists(db, "game_rarity_settings")
    && ((system === "mining" && tableExists(db, "mining_item_weights")) || (system === "fishing" && tableExists(db, "fish_catalog")));
  const rarityWeights = {};
  for (const rarity of order) {
    const setting = settings[rarity] || {};
    const raw = setting.base_weight ?? setting.base_chance ?? defaultRarityWeight(system, rarity);
    rarityWeights[rarity] = Number(setting.enabled ?? 1) === 1 && Number.isFinite(Number(raw)) ? Math.max(0, Number(raw)) : 0;
  }
  const totalRarityWeight = Object.values(rarityWeights).reduce((sum, value) => sum + Number(value || 0), 0);
  const totalWeight = enabled.reduce((sum, row) => sum + Number(row[weightKey] ?? row.chance_percent ?? 0), 0);
  return order.map((rarity) => {
    const setting = settings[rarity] || {};
    const rows = items.filter((row) => normalizeRarity(row.rarity) === rarity);
    const enabledRows = rows.filter((row) => row.enabled !== 0 && row.enabled !== false && row.drop_enabled !== 0 && row.catch_enabled !== 0);
    const rarityWeight = enabledRows.reduce((sum, row) => sum + Number(row[weightKey] ?? row.chance_percent ?? 0), 0);
    const defaultWeight = defaultRarityWeight(system, rarity);
    const baseWeight = setting.base_weight ?? defaultWeight;
    const chance = runtimeConnected && totalRarityWeight > 0
      ? (Number(rarityWeights[rarity] || 0) / totalRarityWeight) * 100
      : (totalWeight ? (rarityWeight / totalWeight) * 100 : 0);
    const maxValue = rows.reduce((max, row) => Math.max(max, Number(row[valueKey] || row.sell_value || row.base_value || 0)), 0);
    return {
      rarity,
      label: RARITY_LABELS[rarity] || rarity,
      total_items: rows.length,
      enabled_items: enabledRows.length,
      total_weight: rarityWeight,
      base_weight: baseWeight,
      base_chance: setting.base_chance ?? baseWeight,
      planning_enabled: setting.enabled ?? 1,
      chance_percent: chance,
      chance_label: chanceTextFromPercent(chance, zeroText),
      max_value: maxValue,
      writable: !!db && tableExists(db, "game_rarity_settings"),
      schema_verified: !!db && tableExists(db, "game_rarity_settings"),
      runtime_connected: runtimeConnected,
      source,
      notes: runtimeConnected
        ? `Connected to !${system === "fishing" ? "fish" : "mine"}. Rarity base weight is read from game_rarity_settings; item odds are calculated from ${itemLabel}.`
        : `Base rarity values are stored for dashboard planning only; active runtime still uses ${source}. Item odds are calculated from ${itemLabel}.`,
    };
  });
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
    table_status: Object.fromEntries(["mining_settings", "mining_weight_settings", "game_rarity_settings", "mining_item_weights", "mining_players", "mining_inventory", "mining_items", "mining_logs", "mining_events", "mining_payout_logs", "forced_mining_drops", "ore_weight_records", "gold_settings", "gold_rain_settings", "gold_tip_events"].map((t) => [t, tableExists(db, t)])),
    raw: {
      mining_settings: Object.entries(readKeyValueMap(db, "mining_settings")).map(([key, value]) => ({ key, value })),
      mining_weight_settings: Object.entries(readKeyValueMap(db, "mining_weight_settings")).map(([key, value]) => ({ key, value })),
      game_rarity_settings: safeTableRows(db, "game_rarity_settings", { orderBy: "system, rarity", limit: "200" }).filter((row) => row.system === "mining"),
      mining_item_weights: safeTableRows(db, "mining_item_weights", { orderBy: "item_id", limit: "500" }),
      auto_activity_settings: Object.entries(readKeyValueMap(db, "auto_activity_settings")).filter(([key]) => key.startsWith("mine") || key.startsWith("automine")).map(([key, value]) => ({ key, value })),
    },
  };
}

function fishingOverview(db) {
  const codeCatalog = readFishingCodeCatalog();
  const fishRows = enrichedFishingRows(db);
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
    catalog: { fish_count: fishRows.rows.length || codeCatalog.fish.length, rod_count: codeCatalog.rods.length, source: fishRows.source || codeCatalog.source, runtime_connected: fishRows.runtime_connected },
    table_status: Object.fromEntries(["auto_activity_settings", "game_rarity_settings", "fish_catalog", "fish_profiles", "fish_catch_records", "fish_inventory", "fish_auto_sell_settings", "forced_fishing_drops", "player_rods", "owned_items"].map((t) => [t, tableExists(db, t)])),
    raw: {
      fish_catalog: safeTableRows(db, "fish_catalog", { orderBy: "rarity, base_value, name", limit: "500" }),
      game_rarity_settings: safeTableRows(db, "game_rarity_settings", { orderBy: "system, rarity", limit: "200" }).filter((row) => row.system === "fishing"),
      fish_profiles: safeTableRows(db, "fish_profiles", { orderBy: "total_catches DESC", limit: "100" }),
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

function tableStatusMap(db, tables) {
  return Object.fromEntries(tables.map((table) => [table, tableExists(db, table)]));
}

function tableColumnsMap(db, tables) {
  return Object.fromEntries(tables.map((table) => [table, tableExists(db, table) ? tableColumns(db, table) : []]));
}

function vipRows(db, limit = 500) {
  if (!tableExists(db, "owned_items")) return [];
  const cols = tableColumns(db, "owned_items");
  if (!cols.includes("user_id") || !cols.includes("item_id")) return [];
  const hasType = cols.includes("item_type");
  const joinUsers = tableExists(db, "users") && columnExists(db, "users", "user_id") && columnExists(db, "users", "username");
  const createdCol = ["created_at", "acquired_at", "added_at"].find((col) => cols.includes(col));
  const sql = `SELECT oi.user_id AS user_id, ${joinUsers ? "COALESCE(NULLIF(u.username,''), oi.user_id)" : "oi.user_id"} AS username, oi.item_id AS item_id${hasType ? ", oi.item_type AS item_type" : ""}${createdCol ? `, oi.${sqlIdent(createdCol)} AS acquired_at` : ""} FROM owned_items oi ${joinUsers ? "LEFT JOIN users u ON u.user_id=oi.user_id" : ""} WHERE lower(oi.item_id)='vip' ORDER BY ${joinUsers ? "username" : "oi.user_id"} LIMIT ?`;
  return rowsOrEmpty(db, "owned_items", sql, limit);
}

function usernameResolutionMap(db) {
  const map = new Map();
  const addRows = (table) => {
    if (!tableExists(db, table) || !columnExists(db, table, "user_id") || !columnExists(db, table, "username")) return;
    for (const row of safeRows(db, table, ["user_id", "username"], { limit: "100000" })) {
      if (row.user_id && row.username && !map.has(String(row.user_id))) map.set(String(row.user_id), row.username);
    }
  };
  for (const table of ["users", "fish_profiles", "mining_players", "premium_balances"]) addRows(table);
  return map;
}

function resolveUsername(db, row = {}, users = null) {
  const direct = row.resolved_username || row.username || row.display_name || row.player_name || row.user_name;
  if (direct && !/^[a-z0-9_-]{18,}$/i.test(String(direct))) return direct;
  const lookup = users || usernameResolutionMap(db);
  for (const key of ["user_id", "uid", "highrise_user_id", "buyer_id", "seller_id", "target_user_id", "requester_id", "owner_id"]) {
    const id = row[key];
    if (id && lookup.has(String(id))) return lookup.get(String(id));
  }
  const name = row.name;
  if (name && !row.user_id && !/^[a-z0-9_-]{18,}$/i.test(String(name)) && !String(name).includes("_id")) return name;
  return "Unknown Player";
}

function enrichResolvedUsers(db, rows = []) {
  const users = usernameResolutionMap(db);
  return (rows || []).map((row) => {
    const resolved = resolveUsername(db, row, users);
    const out = { ...row, resolved_username: resolved };
    if (!out.username && resolved !== "Unknown Player") out.username = resolved;
    if (resolved === "Unknown Player" && (row.user_id || row.uid || row.requester_id || row.target_user_id)) out.lookup_needed = 1;
    return out;
  });
}

function readRewardsDashboard(db) {
  const tables = [
    "owned_items", "user_badges", "user_titles", "player_titles", "title_catalog", "title_loadouts",
    "badge_claims", "badge_market_listings", "badge_market_logs", "badge_trades", "badge_wishlist",
    "onboarding_rewards_log", "pending_coin_rewards", "weekly_rewards", "weekly_leaderboard_snapshots",
    "quest_progress", "player_missions", "player_mission_sets", "shop_view_sessions", "purchase_history",
    "premium_balances", "premium_transactions", "premium_settings", "luxe_ticket_logs", "luxe_conversion_logs",
    "subscriber_users", "subscriber_announcements", "emoji_badges",
  ];
  const ownedItems = enrichResolvedUsers(db, safeTableRows(db, "owned_items", { limit: "500" }));
  const vipPlayers = vipRows(db);
  return {
    overview: {
      vip_players: vipPlayers.length,
      owned_items: rowCountSafe(db, "owned_items") ?? 0,
      title_catalog: rowCountSafe(db, "title_catalog") ?? 0,
      user_titles: rowCountSafe(db, "user_titles") ?? 0,
      player_titles: rowCountSafe(db, "player_titles") ?? 0,
      user_badges: rowCountSafe(db, "user_badges") ?? 0,
      pending_rewards: rowCountSafe(db, "pending_coin_rewards") ?? 0,
      purchases: rowCountSafe(db, "purchase_history") ?? 0,
      quests: (rowCountSafe(db, "quest_progress") ?? 0) + (rowCountSafe(db, "player_missions") ?? 0),
    },
    vip_players: vipPlayers,
    owned_items: ownedItems,
    titles: {
      catalog: safeTableRows(db, "title_catalog", { orderBy: columnExists(db, "title_catalog", "tier") ? "tier, title_id" : "", limit: "500" }),
      assigned: enrichResolvedUsers(db, safeTableRows(db, "user_titles", { orderBy: columnExists(db, "user_titles", "unlocked_at") ? "unlocked_at DESC" : "", limit: "500" })),
      dashboard_titles: enrichResolvedUsers(db, safeTableRows(db, "player_titles", { orderBy: "id DESC", limit: "500" })),
      loadouts: safeTableRows(db, "title_loadouts", { limit: "200" }),
    },
    badges: {
      owned: enrichResolvedUsers(db, safeTableRows(db, "user_badges", { orderBy: columnExists(db, "user_badges", "acquired_at") ? "acquired_at DESC" : "", limit: "500" })),
      claims: enrichResolvedUsers(db, safeTableRows(db, "badge_claims", { limit: "500" })),
      market_listings: safeTableRows(db, "badge_market_listings", { limit: "200" }),
      market_logs: safeTableRows(db, "badge_market_logs", { limit: "200" }),
      trades: safeTableRows(db, "badge_trades", { limit: "200" }),
      wishlist: safeTableRows(db, "badge_wishlist", { limit: "200" }),
    },
    rewards: {
      onboarding: safeTableRows(db, "onboarding_rewards_log", { limit: "200" }),
      pending_coin_rewards: enrichResolvedUsers(db, safeTableRows(db, "pending_coin_rewards", { limit: "200" })),
      weekly_rewards: enrichResolvedUsers(db, safeTableRows(db, "weekly_rewards", { limit: "200" })),
      weekly_snapshots: enrichResolvedUsers(db, safeTableRows(db, "weekly_leaderboard_snapshots", { limit: "200" })),
      subscriber_users: safeTableRows(db, "subscriber_users", { limit: "200" }),
      subscriber_announcements: safeTableRows(db, "subscriber_announcements", { limit: "100" }),
    },
    shop: {
      sessions: safeTableRows(db, "shop_view_sessions", { limit: "200" }),
      purchases: enrichResolvedUsers(db, safeTableRows(db, "purchase_history", { orderBy: columnExists(db, "purchase_history", "created_at") ? "created_at DESC" : "", limit: "500" })),
      premium_balances: enrichResolvedUsers(db, safeTableRows(db, "premium_balances", { limit: "200" })),
      premium_transactions: enrichResolvedUsers(db, safeTableRows(db, "premium_transactions", { orderBy: columnExists(db, "premium_transactions", "created_at") ? "created_at DESC" : "", limit: "500" })),
    },
    quests: {
      quest_progress: enrichResolvedUsers(db, safeTableRows(db, "quest_progress", { limit: "500" })),
      player_missions: enrichResolvedUsers(db, safeTableRows(db, "player_missions", { limit: "500" })),
      player_mission_sets: safeTableRows(db, "player_mission_sets", { limit: "200" }),
    },
    logs: {
      audit_logs: safeRows(db, "audit_logs", ["id", "actor", "action_type", "target_type", "target_id", "old_value", "new_value", "ip_address", "created_at"], {
        where: "action_type LIKE '%vip%' OR action_type LIKE '%title%' OR action_type LIKE '%badge%' OR action_type LIKE '%reward%' OR action_type LIKE '%item%' OR target_type IN ('owned_items','user_titles','player_titles','user_badges','title_catalog')",
        orderBy: columnExists(db, "audit_logs", "created_at") ? "created_at DESC" : "",
        limit: "200",
      }),
      premium_transactions: safeTableRows(db, "premium_transactions", { limit: "100" }),
      purchase_history: safeTableRows(db, "purchase_history", { limit: "100" }),
    },
    table_status: tableStatusMap(db, tables),
    columns: tableColumnsMap(db, tables),
  };
}

function readPlayerRewardsInventory(db, idOrQuery) {
  const player = readPlayerProfile(db, idOrQuery);
  if (!player) return null;
  const userId = player.user_id || "";
  const username = player.username || "";
  return {
    player,
    owned_items: safeRows(db, "owned_items", ["user_id", "item_id", "item_type"], {
      where: "user_id=?",
      params: [userId],
      orderBy: columnExists(db, "owned_items", "item_type") ? "item_type, item_id" : "",
      limit: "500",
    }),
    titles: safeRows(db, "user_titles", ["user_id", "username", "title_id", "source", "unlocked_at", "expires_at"], {
      where: "user_id=?",
      params: [userId],
      orderBy: columnExists(db, "user_titles", "unlocked_at") ? "unlocked_at DESC" : "",
      limit: "500",
    }),
    badges: safeRows(db, "user_badges", ["id", "username", "badge_id", "acquired_at", "source", "equipped", "locked"], {
      where: "lower(username)=lower(?)",
      params: [username],
      orderBy: columnExists(db, "user_badges", "acquired_at") ? "acquired_at DESC" : "",
      limit: "500",
    }),
    vip: vipRows(db).filter((row) => row.user_id === userId || String(row.username || "").toLowerCase() === String(username).toLowerCase()),
    luxe_balance: safeOne(db, "premium_balances", ["user_id", "username", "luxe_tickets", "updated_at"], { where: "user_id=? OR lower(username)=lower(?)", params: [userId, username] }),
    purchases: safeRows(db, "purchase_history", ["id", "user_id", "username", "item_id", "price", "created_at"], {
      where: "user_id=? OR lower(username)=lower(?)",
      params: [userId, username],
      orderBy: columnExists(db, "purchase_history", "created_at") ? "created_at DESC" : "",
      limit: "200",
    }),
    premium_transactions: safeRows(db, "premium_transactions", ["id", "user_id", "username", "type", "amount", "currency", "details", "created_at"], {
      where: "user_id=? OR lower(username)=lower(?)",
      params: [userId, username],
      orderBy: columnExists(db, "premium_transactions", "created_at") ? "created_at DESC" : "",
      limit: "200",
    }),
    badge_market_listings: safeRows(db, "badge_market_listings", ["id", "seller_username", "badge_id", "emoji", "price", "listed_at", "status", "buyer_username", "sold_at"], {
      where: "lower(seller_username)=lower(?) OR lower(buyer_username)=lower(?)",
      params: [username, username],
      orderBy: columnExists(db, "badge_market_listings", "listed_at") ? "listed_at DESC" : "id DESC",
      limit: "200",
    }),
  };
}

const LUXE_SHOP_ITEMS = [
  { item_key: "vip", number: 1, name: "VIP Pass", category: "vip", default_price: 500, default_duration_seconds: 2592000 },
  { item_key: "automine1h", number: 2, name: "Auto-Mine 1h", category: "mining", default_price: 100, default_duration_seconds: 3600 },
  { item_key: "automine3h", number: 3, name: "Auto-Mine 3h", category: "mining", default_price: 250, default_duration_seconds: 10800 },
  { item_key: "automine5h", number: 4, name: "Auto-Mine 5h", category: "mining", default_price: 400, default_duration_seconds: 18000 },
  { item_key: "autofish1h", number: 5, name: "Auto-Fish 1h", category: "fishing", default_price: 100, default_duration_seconds: 3600 },
  { item_key: "autofish3h", number: 6, name: "Auto-Fish 3h", category: "fishing", default_price: 250, default_duration_seconds: 10800 },
  { item_key: "autofish5h", number: 7, name: "Auto-Fish 5h", category: "fishing", default_price: 400, default_duration_seconds: 18000 },
  { item_key: "luckyhour", number: 8, name: "Lucky Hour Boost", category: "boosts", default_price: 150, default_duration_seconds: 3600 },
  { item_key: "treasurehour", number: 9, name: "Treasure Hour Boost", category: "boosts", default_price: 200, default_duration_seconds: 3600 },
  { item_key: "smallcoins", number: 10, name: "Small ChillCoins", category: "coins", default_price: 50, default_duration_seconds: 0 },
  { item_key: "mediumcoins", number: 11, name: "Medium ChillCoins", category: "coins", default_price: 100, default_duration_seconds: 0 },
  { item_key: "largecoins", number: 12, name: "Large ChillCoins", category: "coins", default_price: 250, default_duration_seconds: 0 },
];

function commerceSourceMap(db) {
  const hasEmojiBadges = tableExists(db, "emoji_badges");
  const hasTitleCatalog = tableExists(db, "title_catalog");
  return [
    { system: "Titles", command: "!shop titles / !buy title / !equip title", module: "modules/shop.py", source: hasTitleCatalog ? "title_catalog + owned_items/user_titles" : "modules/shop.py TITLES + owned_items", dashboard_page: "Titles & Badges / Titles", status: hasTitleCatalog ? "CONNECTED" : "RUNTIME_CONSTANT", notes: hasTitleCatalog ? "DB catalog exists; classic shop constants may still be fallback." : "Runtime title catalog is a Python constant; grants/equips remain DB-backed." },
    { system: "Badges", command: "!badgeshop / !buy badge / !equip badge", module: "modules/badge_market.py + modules/shop.py", source: hasEmojiBadges ? "emoji_badges + user_badges" : "modules/shop.py BADGES + user_badges", dashboard_page: "Titles & Badges / Badge Shop", status: hasEmojiBadges ? "CONNECTED" : "RUNTIME_CONSTANT", notes: hasEmojiBadges ? "Badge market catalog table supports price and availability edits." : "Classic badge catalog is a Python constant; ownership remains DB-backed." },
    { system: "Badge Market", command: "!badgemarket / !badgelist / !badgebuy / !badgecancel", module: "modules/badge_market.py", source: "badge_market_listings, badge_market_logs, badge_trades, badge_wishlist, bot_settings.badge_market_fee_percent", dashboard_page: "Titles & Badges / Badge Market", status: tableExists(db, "badge_market_listings") ? "CONNECTED" : "UNVERIFIED_SCHEMA", notes: "Listings are cancelled/archived, not deleted." },
    { system: "Luxe Shop", command: "!luxeshop / !buyluxe / !luxeadmin set price|duration", module: "modules/luxe.py", source: "modules/luxe.py _SHOP_ITEMS + premium_settings price_* and duration_*", dashboard_page: "VIP & Luxe / Luxe Shop", status: tableExists(db, "premium_settings") ? "CONNECTED" : "UNVERIFIED_SCHEMA", notes: "Catalog identity is runtime constant; price and duration are DB-backed." },
    { system: "Luxe Tickets", command: "!luxe / !addtickets / !removetickets / !settickets", module: "modules/luxe.py + modules/luxe_admin.py", source: "premium_balances, premium_transactions, luxe_ticket_logs", dashboard_page: "VIP & Luxe / Luxe Tickets", status: tableExists(db, "premium_balances") ? "CONNECTED" : "UNVERIFIED_SCHEMA", notes: "Owner grants write premium_balances and premium_transactions." },
    { system: "VIP", command: "!vip / Luxe VIP Pass", module: "modules/luxe.py + modules/shop.py", source: "owned_items.item_id='vip'", dashboard_page: "VIP & Luxe / VIP Members", status: tableExists(db, "owned_items") ? "CONNECTED" : "UNVERIFIED_SCHEMA", notes: "Dashboard avoids duplicate VIP ownership rows." },
    { system: "Owned Items", command: "!myitems / shop ownership", module: "modules/shop.py", source: "owned_items, purchase_history", dashboard_page: "Titles & Badges / Player Ownership", status: tableExists(db, "owned_items") ? "CONNECTED" : "UNVERIFIED_SCHEMA", notes: "Catalog edits depend on the relevant catalog source." },
    { system: "Achievements", command: "achievement / badge claim systems", module: "modules/achievements.py", source: "badge_claims, onboarding_rewards_log, weekly_rewards", dashboard_page: "Quests & Rewards / Achievements", status: tableExists(db, "badge_claims") || tableExists(db, "weekly_rewards") ? "READ_ONLY" : "UNVERIFIED_SCHEMA", notes: "Visible as diagnostics unless grant/revoke schema is verified." },
  ];
}

function readLuxeSettings(db) {
  return readKeyValueMap(db, "premium_settings");
}

function readLuxeShop(db) {
  const settings = readLuxeSettings(db);
  const rows = LUXE_SHOP_ITEMS.map((item) => ({
    ...item,
    price: Number(settings[`price_${item.item_key}`] ?? item.default_price),
    duration_seconds: Number(settings[`duration_${item.item_key}`] ?? item.default_duration_seconds),
    status: tableExists(db, "premium_settings") ? "CONNECTED" : "RUNTIME_CONSTANT",
    source: "modules/luxe.py _SHOP_ITEMS + premium_settings overrides",
  }));
  return { rows, settings, writable: tableExists(db, "premium_settings"), source: "premium_settings" };
}

function readCommerceDashboard(db) {
  const rewards = readRewardsDashboard(db);
  const badgeCatalog = safeTableRows(db, "emoji_badges", { orderBy: columnExists(db, "emoji_badges", "rarity") ? "rarity, badge_id" : "", limit: "1000" });
  const marketListings = safeTableRows(db, "badge_market_listings", { orderBy: columnExists(db, "badge_market_listings", "listed_at") ? "listed_at DESC" : "id DESC", limit: "500" });
  const marketLogs = safeTableRows(db, "badge_market_logs", { orderBy: columnExists(db, "badge_market_logs", "timestamp") ? "timestamp DESC" : "id DESC", limit: "500" });
  const luxe = readLuxeShop(db);
  return {
    overview: {
      title_catalog: rowCountSafe(db, "title_catalog") ?? 0,
      badge_catalog: rowCountSafe(db, "emoji_badges") ?? 0,
      active_badge_listings: marketListings.filter((r) => String(r.status || "").toLowerCase() === "active").length,
      luxe_balances: rowCountSafe(db, "premium_balances") ?? 0,
      owned_items: rowCountSafe(db, "owned_items") ?? 0,
      purchases: rowCountSafe(db, "purchase_history") ?? 0,
      premium_transactions: rowCountSafe(db, "premium_transactions") ?? 0,
      vip_players: rewards.overview.vip_players,
    },
    source_map: commerceSourceMap(db),
    titles: rewards.titles,
    badge_shop: { catalog: badgeCatalog, writable: tableExists(db, "emoji_badges"), source: tableExists(db, "emoji_badges") ? "emoji_badges" : "modules/shop.py BADGES runtime constant" },
    badge_market: {
      listings: marketListings,
      logs: marketLogs,
      trades: safeTableRows(db, "badge_trades", { orderBy: columnExists(db, "badge_trades", "created_at") ? "created_at DESC" : "", limit: "300" }),
      wishlist: safeTableRows(db, "badge_wishlist", { orderBy: columnExists(db, "badge_wishlist", "created_at") ? "created_at DESC" : "", limit: "300" }),
      fee_percent: readKeyValueMap(db, "bot_settings").badge_market_fee_percent ?? "5",
      source: "bot_settings.badge_market_fee_percent",
    },
    luxe: {
      shop: luxe.rows,
      settings: luxe.settings,
      balances: safeTableRows(db, "premium_balances", { orderBy: columnExists(db, "premium_balances", "luxe_tickets") ? "luxe_tickets DESC" : "", limit: "500" }),
      transactions: safeTableRows(db, "premium_transactions", { orderBy: columnExists(db, "premium_transactions", "created_at") ? "created_at DESC" : "id DESC", limit: "500" }),
      ticket_logs: safeTableRows(db, "luxe_ticket_logs", { orderBy: columnExists(db, "luxe_ticket_logs", "created_at") ? "created_at DESC" : "id DESC", limit: "300" }),
      conversion_logs: safeTableRows(db, "luxe_conversion_logs", { orderBy: columnExists(db, "luxe_conversion_logs", "created_at") ? "created_at DESC" : "id DESC", limit: "300" }),
    },
    vip: { rows: vipRows(db), source: "owned_items.item_id='vip'" },
    owned_items: rewards.owned_items,
    purchase_history: rewards.shop.purchases,
    premium_transactions: rewards.shop.premium_transactions,
    achievements: {
      badge_claims: rewards.badges.claims,
      onboarding: rewards.rewards.onboarding,
      weekly_rewards: rewards.rewards.weekly_rewards,
      weekly_snapshots: rewards.rewards.weekly_snapshots,
    },
    table_status: { ...rewards.table_status, emoji_badges: tableExists(db, "emoji_badges"), luxe_ticket_logs: tableExists(db, "luxe_ticket_logs"), luxe_conversion_logs: tableExists(db, "luxe_conversion_logs") },
    columns: { ...rewards.columns, emoji_badges: tableExists(db, "emoji_badges") ? tableColumns(db, "emoji_badges") : [], luxe_ticket_logs: tableExists(db, "luxe_ticket_logs") ? tableColumns(db, "luxe_ticket_logs") : [] },
  };
}

const QUEST_TABLES = [
  "quest_progress",
  "player_missions",
  "player_mission_sets",
  "pending_coin_rewards",
  "weekly_rewards",
  "weekly_leaderboard_snapshots",
  "event_points",
  "event_settings",
  "ledger",
  "audit_logs",
];

function chooseColumn(cols, candidates) {
  return candidates.find((col) => cols.includes(col)) || "";
}

function userLookupMap(db) {
  return usernameResolutionMap(db);
}

function resolveQuestUsername(row, users) {
  const direct = row.resolved_username || row.username || row.user_name || row.player || row.player_name || row.display_name;
  if (direct) return direct;
  const id = row.user_id || row.player_id || row.uid || row.user || "";
  if (id && users.has(String(id))) return users.get(String(id));
  return id ? "Unknown Player" : "";
}

function shortQuestId(row) {
  const id = row.user_id || row.player_id || row.uid || row.user || "";
  return id ? String(id).slice(0, 8) : "";
}

function normalizeQuestRows(rows, users) {
  return (rows || []).map((row) => {
    const current = Number(row.progress ?? row.current_amount ?? row.current_value ?? row.amount ?? row.count ?? 0);
    const target = Number(row.target_amount ?? row.required_amount ?? row.goal_amount ?? row.target ?? row.required ?? 0);
    const completion = Number.isFinite(current) && Number.isFinite(target) && target > 0
      ? Math.max(0, Math.min(100, Math.round((current / target) * 100)))
      : null;
    return {
      ...row,
      username: resolveQuestUsername(row, users),
      fallback_id: shortQuestId(row),
      current_progress: Number.isFinite(current) ? current : null,
      target_progress: Number.isFinite(target) && target > 0 ? target : null,
      completion_percent: completion,
      claimed: row.claimed ?? row.reward_claimed ?? row.claimed_at ?? row.completed_at ?? "",
      period_key: row.period_key ?? row.day_key ?? row.week_key ?? row.period ?? "",
    };
  });
}

function questTableRows(db, table, limit = "500") {
  if (!tableExists(db, table)) return [];
  const info = tableInfo(db, table, limit);
  return enrichResolvedUsers(db, info.rows || []);
}

function questCatalogSpec(db) {
  const candidateTables = ["quest_catalog", "quest_definitions", "mission_definitions", "missions_catalog", "daily_quests", "weekly_quests"];
  for (const table of candidateTables) {
    if (!tableExists(db, table)) continue;
    const cols = tableColumns(db, table);
    const idCol = chooseColumn(cols, ["quest_id", "mission_id", "id", "key"]);
    const nameCol = chooseColumn(cols, ["name", "title", "display_name"]);
    if (!idCol || !nameCol) continue;
    const enabledCol = chooseColumn(cols, ["enabled", "active", "is_active", "disabled"]);
    return {
      table,
      exists: true,
      columns: cols,
      id_col: idCol,
      name_col: nameCol,
      enabled_col: enabledCol,
      archive_col: chooseColumn(cols, ["archived", "is_archived", "deleted", "disabled"]),
      period_col: chooseColumn(cols, ["period", "quest_period", "frequency", "type", "category"]),
      category_col: chooseColumn(cols, ["category", "quest_type", "target_type", "module"]),
      target_col: chooseColumn(cols, ["target_amount", "required_amount", "goal_amount", "target", "amount"]),
      writable: Boolean(enabledCol),
    };
  }
  const fallbackTable = tableExists(db, "player_mission_sets") ? "player_mission_sets" : tableExists(db, "quest_progress") ? "quest_progress" : "";
  return {
    table: fallbackTable,
    exists: Boolean(fallbackTable),
    columns: fallbackTable ? tableColumns(db, fallbackTable) : [],
    writable: false,
    message: fallbackTable
      ? `${fallbackTable} exists, but no verified editable quest catalog table was found. Showing read-only mission/progress rows.`
      : "No quest catalog/progress table found.",
  };
}

function classifyQuestRows(rows, period) {
  const needle = String(period || "").toLowerCase();
  return (rows || []).filter((row) => {
    const text = [
      row.period,
      row.quest_period,
      row.frequency,
      row.type,
      row.category,
      row.quest_type,
      row.period_key,
      row.name,
      row.title,
      row.quest_id,
    ].filter(Boolean).join(" ").toLowerCase();
    return text.includes(needle);
  });
}

function readQuestsDashboard(db, query = {}) {
  const users = userLookupMap(db);
  const table_status = tableStatusMap(db, QUEST_TABLES);
  const columns = tableColumnsMap(db, QUEST_TABLES);
  const catalogSpec = questCatalogSpec(db);
  const rawProgress = questTableRows(db, "quest_progress", "1000");
  const rawMissions = questTableRows(db, "player_missions", "1000");
  const rawSets = questTableRows(db, "player_mission_sets", "500");
  const catalogRows = catalogSpec.table
    ? safeTableRows(db, catalogSpec.table, {
        orderBy: catalogSpec.columns?.includes("updated_at") ? "updated_at DESC"
          : catalogSpec.columns?.includes("created_at") ? "created_at DESC"
          : catalogSpec.id_col ? sqlIdent(catalogSpec.id_col)
          : "",
        limit: "500",
      })
    : [];
  const progress = normalizeQuestRows(rawProgress, users);
  const missions = normalizeQuestRows(rawMissions, users);
  const sets = normalizeQuestRows(rawSets, users);
  const catalog = normalizeQuestRows(catalogRows, users);
  const playerQuery = String(query.q || query.player || "").trim().toLowerCase();
  const playerRows = playerQuery
    ? [...progress, ...missions].filter((row) => [
        row.username,
        row.user_id,
        row.player_id,
        row.quest_id,
        row.mission_id,
      ].filter(Boolean).some((value) => String(value).toLowerCase().includes(playerQuery)))
    : [];
  const completedToday = [...progress, ...missions].filter((row) => {
    const doneAt = String(row.completed_at || row.updated_at || row.claimed_at || "");
    return doneAt.startsWith(nowIso().slice(0, 10)) && (row.completed || row.completed_at || Number(row.completion_percent) >= 100);
  }).length;
  const claimedToday = [...progress, ...missions].filter((row) => String(row.claimed_at || "").startsWith(nowIso().slice(0, 10))).length;
  const activeQuestRows = catalog.filter((row) => {
    const enabled = row.enabled ?? row.active ?? row.is_active;
    const disabled = row.disabled ?? row.archived ?? row.is_archived;
    return String(enabled ?? "1").toLowerCase() !== "0" && String(enabled ?? "true").toLowerCase() !== "false" && String(disabled ?? "0") !== "1";
  });
  const daily = classifyQuestRows([...catalog, ...sets, ...missions], "daily");
  const weekly = classifyQuestRows([...catalog, ...sets, ...missions], "weekly");
  const event = classifyQuestRows([...catalog, ...sets, ...missions], "event");
  const pendingRewards = safeTableRows(db, "pending_coin_rewards", { limit: "500" });
  const weeklyRewards = safeTableRows(db, "weekly_rewards", { limit: "300" });
  const snapshots = safeTableRows(db, "weekly_leaderboard_snapshots", { limit: "200" });
  const eventPoints = normalizeQuestRows(safeTableRows(db, "event_points", { limit: "300" }), users);
  return {
    overview: {
      active_quests: activeQuestRows.length,
      daily_quests: daily.length,
      weekly_quests: weekly.length,
      event_quests: event.length,
      players_with_progress: new Set([...progress, ...missions].map((row) => row.user_id || row.player_id || row.username).filter(Boolean)).size,
      pending_rewards: pendingRewards.length,
      completed_today: completedToday,
      claimed_rewards_today: claimedToday,
    },
    catalog: {
      ...catalogSpec,
      rows: catalog,
      schema_verified: Boolean(catalogSpec.writable),
      writable: Boolean(catalogSpec.writable),
    },
    daily_quests: daily,
    weekly_quests: weekly,
    event_quests: event,
    progress: {
      rows: progress,
      player_missions: missions,
      player_rows: playerRows,
      query: playerQuery,
    },
    rewards: {
      pending_coin_rewards: normalizeQuestRows(pendingRewards, users),
      weekly_rewards: normalizeQuestRows(weeklyRewards, users),
      weekly_snapshots: normalizeQuestRows(snapshots, users),
      event_points: eventPoints,
    },
    logs: {
      ledger: safeRows(db, "ledger", ["id", "user_id", "username", "change_amount", "amount", "reason", "source", "created_at", "timestamp"], {
        where: tableExists(db, "ledger") && tableColumns(db, "ledger").includes("reason") ? "lower(reason) LIKE '%quest%' OR lower(reason) LIKE '%mission%' OR lower(reason) LIKE '%daily%'" : "",
        orderBy: columnExists(db, "ledger", "created_at") ? "created_at DESC" : columnExists(db, "ledger", "timestamp") ? "timestamp DESC" : "",
        limit: "200",
      }),
      audit_logs: safeRows(db, "audit_logs", ["id", "actor", "action_type", "target_type", "target_id", "old_value", "new_value", "ip_address", "created_at"], {
        where: "action_type LIKE '%quest%' OR action_type LIKE '%mission%' OR action_type LIKE '%reward%' OR target_type IN ('quest_catalog','quest_progress','player_missions','pending_coin_rewards')",
        orderBy: columnExists(db, "audit_logs", "created_at") ? "created_at DESC" : "",
        limit: "200",
      }),
    },
    tables: Object.fromEntries(QUEST_TABLES.map((table) => [table, tableInfo(db, table, table === "audit_logs" ? "100" : "250")])),
    table_status,
    columns,
    future_controls: [
      {
        endpoint: "POST/PUT /api/quests/catalog",
        purpose: "Create or edit quest definitions",
        status: catalogSpec.writable ? "Connected" : "Unverified schema",
      },
      {
        endpoint: "quest reward grants/resets",
        purpose: "Grant rewards, mark complete, or reset player quest progress",
        status: "Unverified schema",
      },
    ],
  };
}

function questCatalogWriteSpec(db) {
  const spec = questCatalogSpec(db);
  if (!spec.writable || !spec.table || !spec.id_col || !spec.name_col) return null;
  return spec;
}

function playerByInput(db, body) {
  const query = String(body?.user_id || body?.username || body?.query || "").trim();
  return query ? readPlayerProfile(db, query) : null;
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
  "premium_settings",
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
    ["!setbadgemarketfee", "Badge Market Fee", "bot_settings", "badge_market_fee_percent", "PUT /api/badge-market/settings", "Badge Market"],
    ["!luxeadmin set rate", "Luxe Ticket Rate", "premium_settings", "luxe_rate", "PUT /api/luxe/settings", "Luxe Tickets"],
    ["!luxeadmin set price vip", "VIP Luxe Price", "premium_settings", "price_vip", "PUT /api/luxe/shop/vip", "Luxe Shop"],
    ["!vipadmin set duration", "VIP Duration Days", "premium_settings", "vip_duration_days", "PUT /api/luxe/settings", "Luxe Shop"],
    ["!setcoinpack small", "Small Coin Pack Tickets", "premium_settings", "coinpack_small_tickets", "PUT /api/luxe/settings", "Luxe Shop"],
    ["!setcoinpack medium", "Medium Coin Pack Tickets", "premium_settings", "coinpack_medium_tickets", "PUT /api/luxe/settings", "Luxe Shop"],
    ["!setcoinpack large", "Large Coin Pack Tickets", "premium_settings", "coinpack_large_tickets", "PUT /api/luxe/settings", "Luxe Shop"],
  ].map(([command, displayName, dbTable, key, writeEndpoint, section]) => auditRow({
    module: "rewards_commerce",
    command,
    display_name: displayName,
    dashboard_page: "VIP & Luxe",
    dashboard_section: section,
    db_table: dbTable,
    db_key_or_column: key,
    writeEndpoint,
    dashboardConnected: true,
    status: "CONNECTED",
    notes: "Rewards Commerce Control Center writes the same key-value source read by the in-room commerce command.",
  })),
  ...[
    ["!shop titles / !buy title", "Classic Title Catalog", "modules/shop.py TITLES"],
    ["!shop badges / !buy badge", "Classic Badge Catalog", "modules/shop.py BADGES"],
    ["!luxeshop", "Luxe Catalog Identity", "modules/luxe.py _SHOP_ITEMS"],
  ].map(([command, displayName, source]) => auditRow({
    module: "rewards_commerce",
    command,
    display_name: displayName,
    dashboard_page: /Badge|Title/.test(displayName) ? "Titles & Badges" : "VIP & Luxe",
    dashboard_section: "Diagnostics",
    db_table: "runtime_constants",
    db_key_or_column: source,
    dashboardConnected: true,
    status: "RUNTIME_CONSTANT",
    notes: "Dashboard shows this source honestly and only edits DB-backed ownership/settings around it.",
    readSource: source,
  })),
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
    dashboard_page: "Economy",
    dashboard_section: table === "bank_settings" ? "Bank / P2P Settings" : "Economy Settings",
    db_table: table,
    db_key_or_column: key,
    writeEndpoint: table === "bank_settings" ? "PUT /api/bank/settings" : "PUT /api/economy/settings",
    dashboardConnected: true,
    status: "CONNECTED",
    notes: table === "bank_settings"
      ? "Active bank command source. Dashboard writes the same bank_settings key read by BankingBot."
      : "Active economy command source. Dashboard writes the same economy_settings key read by runtime economy helpers.",
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
    dashboard_page: "Mining",
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
    db_key_or_column: "item_id,name,rarity,sell_value,drop_enabled + mining_item_weights.drop_weight",
    readSource: "database.get_all_mining_items() joins mining_items to mining_item_weights",
    writeEndpoint: "POST/PUT/DELETE /api/mining/ores",
    dashboardConnected: true,
    status: "CONNECTED",
    notes: "Active !mine reads mining_items and mining_item_weights.drop_weight through database.get_all_mining_items(). Dashboard writes both tables and soft-disables with drop_enabled=0.",
  }),
  auditRow({
    module: "mining",
    command: "!minechances / !orechances",
    display_name: "Ore Drop Chances",
    dashboard_page: "Mining",
    dashboard_section: "Rarity Chances",
    db_table: "game_rarity_settings + mining_item_weights",
    db_key_or_column: "game_rarity_settings.base_weight + mining_item_weights.drop_weight",
    readSource: "modules/mining.py _runtime_rarity_probs + database.get_all_mining_items",
    writeEndpoint: "PUT /api/mining/rarities/:rarity + PUT /api/mining/ores/:id",
    dashboardConnected: true,
    status: "CONNECTED",
    notes: "Active !mine chooses rarity from game_rarity_settings where system='mining', then chooses ore by mining_item_weights.drop_weight.",
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
    dashboard_page: "Fishing",
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
    dashboard_page: "Fishing",
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
    db_table: "fish_catalog",
    db_key_or_column: "fish_id,name,rarity,base_value,min_weight,max_weight,catch_weight,catch_enabled,event_only,emoji",
    readSource: "modules/fishing.py _runtime_fish_catalog reads database.get_fish_catalog()",
    writeEndpoint: "POST/PUT/DELETE /api/fishing/fish",
    dashboardConnected: true,
    status: "CONNECTED",
    notes: "Active !fish reads fish_catalog when present and falls back to FISH_CATALOG constants if the table is missing. fish_profiles remains player profile data only.",
  }),
  auditRow({
    module: "fishing",
    command: "!fishchances",
    display_name: "Fish Catch Chances",
    dashboard_page: "Fishing",
    dashboard_section: "Rarity Chances",
    db_table: "game_rarity_settings + fish_catalog",
    db_key_or_column: "game_rarity_settings.base_weight + fish_catalog.catch_weight",
    readSource: "modules/fishing.py _runtime_fishing_rarity_weights + database.get_fish_catalog",
    writeEndpoint: "PUT /api/fishing/rarities/:rarity + PUT /api/fishing/fish/:id",
    dashboardConnected: true,
    status: "CONNECTED",
    notes: "Active !fish chooses rarity from game_rarity_settings where system='fishing', then chooses fish by fish_catalog.catch_weight.",
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
    ["!quests / !missions", "Player Quest Progress", "quest_progress", "user_id,quest_id,progress,claimed"],
    ["!missions", "Player Missions", "player_missions", "user_id,mission_id,progress,completed"],
    ["!missions", "Mission Sets", "player_mission_sets", "set_id,name,period,reward"],
    ["!daily / reward claim", "Pending Coin Rewards", "pending_coin_rewards", "user_id,amount,reason,status"],
    ["weekly reward job", "Weekly Rewards", "weekly_rewards", "user_id,amount,period_key"],
  ].map(([command, displayName, table, key]) => auditRow({
    module: "quests",
    command,
    display_name: displayName,
    dashboard_page: "Quests & Rewards",
    dashboard_section: table === "quest_progress" ? "Player Progress" : table === "player_mission_sets" ? "Quest Catalog" : "Rewards",
    db_table: table,
    db_key_or_column: key,
    readSource: `SELECT * FROM ${table} LIMIT 500`,
    status: "READ ONLY",
    notes: "Quest dashboard reads this live table. Writes stay hidden unless an editable quest catalog schema is verified.",
  })),
  ...[
    ["!setroomsetting", "Room Setting", "room_settings", "<dynamic key>"],
    ["!setwelcome", "Welcome Message", "room_settings", "welcome_message"],
    ["!setemoteloopinterval", "Emote Loop Interval", "room_settings", "emote_loop_interval_seconds"],
    ["!setemote <alias> time", "Emote Timing Override", "room_settings", "emote_timing_overrides"],
  ].map(([command, displayName, table, key]) => auditRow({
    module: "room_utils",
    command,
    display_name: displayName,
    dashboard_page: command.includes("emote") ? "Emotes" : "Room & Content",
    dashboard_section: command === "!setwelcome" ? "Welcome" : command.includes("emote") ? "Timing Overrides" : "Advanced / Dynamic Room Setting",
    db_table: table,
    db_key_or_column: key,
    writeEndpoint: key.includes("<") ? "" : command === "!setwelcome" ? "PUT /api/room/welcome" : "PUT /api/emotes/timing",
    dashboardConnected: !key.includes("<"),
    status: key.includes("<") ? "UNKNOWN" : "CONNECTED",
    notes: key.includes("<")
      ? "Dynamic admin command; audit confirms table but this is not a single dashboard field."
      : "Verified room_settings source connected to a dashboard endpoint that writes the exact runtime key.",
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
    if (/bot-config|db\/inspect|settings-audit|permissions\/audit|qa\/audit|maintenance/.test(route.path) && !route.owner_only) {
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

function buildQaAudit() {
  const appJsPath = path.join(PUBLIC_DIR, "app.js");
  const appSource = fs.existsSync(appJsPath) ? fs.readFileSync(appJsPath, "utf8") : "";
  const routePatterns = ROUTE_SECURITY.map((route) => ({ ...route, regex: routeToRegex(route.path) }));
  const hasEndpoint = (apiPath, method = "GET") => {
    const cleanPath = String(apiPath || "").split("?")[0].replace(/\/$/, "") || "/";
    return routePatterns.some((route) => route.method === method && route.regex.test(cleanPath));
  };
  function routeToRegex(routePath) {
    const escaped = String(routePath || "")
      .replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
      .replace(/:([A-Za-z0-9_]+)/g, "[^/]+")
      .replace(/\\\*/g, ".*");
    return new RegExp(`^${escaped.replace(/\\\/$/, "")}\\/?$`);
  }
  const publicRoutes = [
    ["Home", "/api/public/home"],
    ["Radio", "/api/public/radio"],
    ["How to Play", "/api/public/how-to-play"],
    ["Casino", "/api/public/casino"],
    ["Mining", "/api/public/mining"],
    ["Fishing", "/api/public/fishing"],
    ["Quests", "/api/public/quests"],
    ["Events", "/api/public/events"],
    ["Rankings", "/api/public/rankings"],
    ["Room Info", "/api/public/room-info"],
  ].map(([page, api]) => ({ page, api, status: hasEndpoint(api) ? "ok" : "missing" }));
  const ownerPages = [
    ["Command Center", "/api/overview"],
    ["Operations Center", "/api/operations"],
    ["Bots", "/api/bot-control"],
    ["Players", null],
    ["Radio", "/api/radio"],
    ["Casino", "/api/casino"],
    ["Mining", "/api/mining"],
    ["Fishing", "/api/fishing"],
    ["Automation Center", "/api/automation"],
    ["Economy", "/api/economy/overview"],
    ["VIP & Luxe", "/api/commerce/overview"],
    ["Titles & Badges", "/api/commerce/overview"],
    ["Quests & Rewards", "/api/quests"],
    ["Economy & Rewards", "/api/commerce/overview"],
    ["Room & Content", "/api/room-control"],
    ["Emotes", "/api/emotes/overview"],
    ["Events", "/api/events"],
    ["Security", "/api/security"],
    ["Staff", "/api/staff"],
    ["System", "/api/healthz"],
    ["Release Control", "/api/release/status"],
    ["Maintenance Center", "/api/maintenance/overview"],
    ["Leaderboards", "/api/leaderboards"],
    ["Settings Audit", "/api/settings-audit"],
    ["Permissions Audit", "/api/permissions/audit"],
    ["QA Audit", "/api/qa/audit"],
    ["E2E Audit", "/api/e2e/audit"],
  ].map(([page, api]) => ({ page, api, status: !api || hasEndpoint(api) ? "ok" : "missing" }));
  const staffRoutes = [
    ["Staff Home", "/api/overview"],
    ["Radio Queue", "/api/radio"],
    ["Players", null],
    ["Events", "/api/events"],
    ["Room Tools", "/api/room-control"],
    ["Moderation", "/api/security"],
    ["Logs", "/api/logs"],
  ].map(([page, api]) => ({ page, api, status: !api || hasEndpoint(api) ? "ok" : "missing" }));
  const expectedRenderers = [
    "renderPublicHome", "renderPublicRadio", "renderPublicHowToPlay", "renderPublicCasino", "renderPublicMining", "renderPublicFishing", "renderPublicQuests", "renderPublicEvents", "renderPublicRankings", "renderPublicRoomInfo",
    "renderCommandCenter", "renderOperationsCenter", "renderBotsPage", "renderOwnerPlayersPage", "renderRadioOwnerPage", "renderCasinoOwnerPage", "renderMiningOwnerPage", "renderFishingOwnerPage", "renderQuestsMissionsPage", "renderAutomationCenterPage", "renderEconomyRewards", "renderRoomContent", "renderEmotesOwnerPage", "renderEventsOwnerPage", "renderSecurityPage", "renderStaffPage_shared", "renderSystemPage", "renderMaintenanceCenter", "renderLeaderboardsPage", "renderSettingsAudit", "renderPermissionsAudit", "renderQaAudit", "renderE2eAudit",
    "renderStaffHome", "renderStaffRadioQueue", "renderStaffPlayers", "renderStaffEvents", "renderStaffRoomTools", "renderStaffLogs",
    "renderReleaseControl",
  ];
  const missingRenderers = expectedRenderers.filter((name) => !new RegExp(`function\\s+${name}\\s*\\(`).test(appSource));
  const apiRefs = new Set();
  for (const match of appSource.matchAll(/["'`]((?:\/api\/)[^"'`$?)]*)/g)) {
    const value = match[1].replace(/\\`$/, "").split("?")[0];
    if (!value.includes("${") && !value.includes(":key") && !value.includes(":module")) apiRefs.add(value);
  }
  const missingEndpoints = [...apiRefs]
    .filter((apiPath) => !apiPath.endsWith("/"))
    .filter((apiPath) => !hasEndpoint(apiPath, "GET") && !hasEndpoint(apiPath, "POST") && !hasEndpoint(apiPath, "PUT") && !hasEndpoint(apiPath, "DELETE"))
    .map((pathValue) => ({ endpoint: pathValue, reason: "No matching server route pattern found" }));
  const dataAttrs = [...new Set([...appSource.matchAll(/data-([a-z0-9-]+)=/gi)].map((m) => m[1]))];
  const handledAttrs = new Set([
    "pub-page", "manual-tab", "manual-rarity", "manual-section", "manual-game-section", "manual-game-tab", "public-game-section", "public-game-tab", "public-rarity", "owner-rarity", "rarity-section", "rarity", "rarity-chance-form", "ranking-tab", "admin-page", "admin-tab", "nav-id", "page-tab", "maint-tab", "action",
    "bot-command", "bot-runtime-restart", "bot-runtime-anchor", "bot-runtime-wake", "target-bot", "dancefloor-command", "sync-command", "sync-persist",
    "command-id",
    "player-jump", "remove-item", "quick-item", "quick-type", "remove-title", "remove-badge",
    "report-review", "report-resolve", "security-unmute", "security-bot-action",
    "remove-request", "unblock-requester", "unblock-track", "mining-ore-form", "mining-ore-disable",
    "open-player-game-inventory", "inventory-mode", "game-inventory-search", "game-inventory-filter",
    "fish-inventory-edit", "fish-inventory-sold", "sold", "fish-inventory-remove",
    "mining-inventory-edit", "mining-inventory-remove", "fishing-fish-form", "fishing-fish-disable",
    "disable-announcement", "toggle-module", "emergency", "maint-action", "maint-cleanup",
    "release-tab", "release-action", "ops-queue-tab",
    "settings-group", "sg-api", "sg-opts", "sf-toggle", "raw-edit-key", "raw-edit-val", "raw-edit-src",
    "table-search", "rarity-filter", "enabled-filter", "room-toggle", "room-edit",
    "staff-id", "remove-staff", "enabled", "room-val", "key", "vip-remove", "vip-user",
    "badge-shop-edit", "badge-market-cancel", "luxe-shop-edit", "copy-text", "user-lookup",
    "quest-disable",
    "automation-send", "automation-archive", "automation-rotating-send", "automation-rotating-disable",
    "automation-promo", "automation-source",
  ]);
  const buttonsWithoutHandlers = dataAttrs
    .filter((attr) => !handledAttrs.has(attr))
    .map((attr) => ({ selector: `data-${attr}`, reason: "No known delegated handler registered in QA whitelist" }));
  const endpointNeededControls = [...appSource.matchAll(/endpoint[-\s]?needed|Endpoint Needed/gi)]
    .map((match) => ({ text: match[0], reason: "Endpoint-needed copy should stay inside Advanced/Future sections" }));
  const publicSafetyWarnings = [];
  const publicSection = appSource.slice(appSource.indexOf("PUBLIC PORTAL"), appSource.indexOf("ADMIN SHELL"));
  if (/bot[_-]?token|AZURA_API_KEY|\.env|db_path|resolved_db_path/i.test(publicSection)) {
    publicSafetyWarnings.push({ warning: "public_sensitive_text_match", message: "Public render section contains sensitive-looking implementation text." });
  }
  const publicRawUserIdMatches = [...publicSection.matchAll(/\b(?:[a-z0-9_-]{24,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b/gi)]
    .slice(0, 5)
    .map((m) => `${m[0].slice(0, 6)}…${m[0].slice(-4)}`);
  if (publicRawUserIdMatches.length) {
    publicSafetyWarnings.push({ warning: "public_raw_user_id_exposure", message: "Public renderer contains long raw ID-like literals.", matches: publicRawUserIdMatches });
  }
  const permissionWarnings = buildPermissionsAudit().warnings;
  const issues = [];
  for (const item of [...publicRoutes, ...ownerPages, ...staffRoutes]) {
    if (item.status === "missing") issues.push({ severity: "CRITICAL", area: "route", item: item.page, message: `Missing endpoint ${item.api}` });
  }
  for (const endpoint of missingEndpoints) issues.push({ severity: "CRITICAL", area: "api", item: endpoint.endpoint, message: endpoint.reason });
  for (const renderer of missingRenderers) issues.push({ severity: "CRITICAL", area: "frontend", item: renderer, message: "Expected render function is missing." });
  for (const button of buttonsWithoutHandlers) issues.push({ severity: "WARNING", area: "button", item: button.selector, message: button.reason });
  for (const control of endpointNeededControls) issues.push({ severity: "INFO", area: "future-controls", item: control.text, message: control.reason });
  for (const warning of publicSafetyWarnings) issues.push({ severity: "CRITICAL", area: "public-safety", item: warning.warning, message: warning.message });
  for (const warning of permissionWarnings) issues.push({ severity: "WARNING", area: "permissions", item: `${warning.method} ${warning.path}`, message: warning.warning });
  return {
    public_routes: publicRoutes,
    admin_routes: ownerPages,
    staff_routes: staffRoutes,
    frontend_pages: [
      ...publicRoutes.map((row) => ({ type: "public", ...row })),
      ...ownerPages.map((row) => ({ type: "owner", ...row })),
      ...staffRoutes.map((row) => ({ type: "staff", ...row })),
    ],
    api_endpoints: ROUTE_SECURITY,
    missing_renderers: missingRenderers,
    missing_endpoints: missingEndpoints,
    buttons_without_handlers: buttonsWithoutHandlers,
    endpoint_needed_controls: endpointNeededControls,
    public_safety_warnings: publicSafetyWarnings,
    permission_warnings: permissionWarnings,
    broken_routes_count: [...publicRoutes, ...ownerPages, ...staffRoutes].filter((row) => row.status === "missing").length + missingRenderers.length,
    issues,
    last_checked_at: nowIso(),
  };
}

function routeToRegex(routePath) {
  const escaped = String(routePath || "")
    .replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    .replace(/:([A-Za-z0-9_]+)/g, "[^/]+")
    .replace(/\\\*/g, ".*");
  return new RegExp(`^${escaped.replace(/\\\/$/, "")}\\/?$`);
}

function routeExists(apiPath, method = "GET") {
  const cleanPath = String(apiPath || "").split("?")[0].replace(/\/$/, "") || "/";
  return ROUTE_SECURITY.some((route) => route.method === method && routeToRegex(route.path).test(cleanPath));
}

function findPublicSensitiveMatches(source) {
  const matches = [];
  const patterns = [
    { kind: "assigned_secret", regex: /\b(?:token|api[_ -]?key|secret|azura_api_key|highrise[_ -]?[a-z0-9_ -]*key)\b\s*[:=]\s*["'`][^"'`\n]{6,}["'`]/ig },
    { kind: "env_path", regex: /(?:\/[A-Za-z0-9._-]+)+\/\.env\b|\.env\s*[:=]\s*["'`][^"'`\n]+["'`]/ig },
    { kind: "internal_db_path", regex: /(?:\/[A-Za-z0-9._-]+)+\/[^"'`\s]+\.db\b/ig },
  ];
  for (const { kind, regex } of patterns) {
    let match;
    while ((match = regex.exec(source))) {
      const start = Math.max(0, match.index - 60);
      const end = Math.min(source.length, match.index + match[0].length + 60);
      matches.push({
        kind,
        match: match[0].replace(/(["'`])[^"'`]{8,}\1/g, "$1[redacted]$1"),
        snippet: source.slice(start, end).replace(/\s+/g, " ").trim(),
      });
    }
  }
  return matches;
}

function buildE2eAudit(db) {
  const appJsPath = path.join(PUBLIC_DIR, "app.js");
  const appSource = fs.existsSync(appJsPath) ? fs.readFileSync(appJsPath, "utf8") : "";
  const serverSource = fs.existsSync(fileURLToPath(import.meta.url)) ? fs.readFileSync(fileURLToPath(import.meta.url), "utf8") : "";
  const relayPath = path.join(BOT_ROOT, "modules", "bot_command_relay.py");
  const relaySource = fs.existsSync(relayPath) ? fs.readFileSync(relayPath, "utf8") : "";
  const qa = buildQaAudit();
  const permissionAudit = buildPermissionsAudit();
  const dbHealth = dbHealthSnapshot(db);
  const queueHealth = readOperationsQueue(db);
  const publicPages = [
    ["Home", "/api/public/home"],
    ["Radio", "/api/public/radio"],
    ["How to Play", "/api/public/how-to-play"],
    ["Casino", "/api/public/casino"],
    ["Mining", "/api/public/mining"],
    ["Fishing", "/api/public/fishing"],
    ["Events", "/api/public/events"],
    ["Rankings", "/api/public/rankings"],
    ["Room Info", "/api/public/room-info"],
    ["Owner Login", null],
  ].map(([page, api]) => ({ page, api, status: !api || routeExists(api) ? "PASS" : "MISSING_ENDPOINT" }));
  const ownerPages = [
    ["Command Center", "/api/overview"],
    ["Operations Center", "/api/operations"],
    ["Bots", "/api/bot-control"],
    ["Room & Content", "/api/room-control"],
    ["Radio", "/api/radio"],
    ["Emotes", "/api/emotes/overview"],
    ["Automation Center", "/api/automation"],
    ["Casino", "/api/casino"],
    ["Mining", "/api/mining"],
    ["Fishing", "/api/fishing"],
    ["Events", "/api/events"],
    ["Players", null],
    ["Economy", "/api/economy/overview"],
    ["VIP & Luxe", "/api/commerce/overview"],
    ["Titles & Badges", "/api/commerce/overview"],
    ["Quests & Rewards", "/api/quests"],
    ["Economy & Rewards", "/api/commerce/overview"],
    ["Leaderboards", "/api/leaderboards"],
    ["Security", "/api/security"],
    ["Staff", "/api/staff"],
    ["Permissions Audit", "/api/permissions/audit"],
    ["System Overview", "/api/healthz"],
    ["Release Control", "/api/release/status"],
    ["Maintenance Center", "/api/maintenance/overview"],
    ["Settings Audit", "/api/settings-audit"],
    ["QA Audit", "/api/qa/audit"],
    ["E2E Audit", "/api/e2e/audit"],
    ["Logs / Errors", "/api/logs"],
  ].map(([page, api]) => ({ page, api, status: !api || routeExists(api) ? "PASS" : "MISSING_ENDPOINT" }));
  const staffPages = [
    ["Staff Home", "/api/overview"],
    ["Radio Queue", "/api/radio"],
    ["Players", null],
    ["Events", "/api/events"],
    ["Room Tools", "/api/room-control"],
    ["Moderation", "/api/security"],
    ["Logs", "/api/logs"],
  ].map(([page, api]) => ({ page, api, status: !api || routeExists(api) ? "PASS" : "MISSING_ENDPOINT" }));
  const visibleQueueActions = [...new Set([...appSource.matchAll(/data-bot-command="([^"]+)"/g)].map((m) => m[1]))];
  const relaySupported = new Set([...relaySource.matchAll(/"([a-z0-9_]+)":\s*_do_/g)].map((m) => m[1]));
  const allowedActions = [...serverSource.matchAll(/"([a-z0-9_]+)",/g)]
    .map((m) => m[1])
    .filter((value) => /^(return_home|stop_emote|restart_requested|announce|trigger_emote|radio_|event_|dancefloor_|sync_|botemote_|warn_user|mute_user|unmute_user|jail_user|unjail_user|security_alert|promo_message)/.test(value));
  const queueSmokeTests = [
    { action: "queue announcement", endpoint: "/api/room/announce", method: "POST", consumer_action: "announce" },
    { action: "queue stop emote", endpoint: "/api/bot-command", method: "POST", consumer_action: "stop_emote" },
    { action: "queue home", endpoint: "/api/bot-command", method: "POST", consumer_action: "return_home" },
    { action: "queue radio skip", endpoint: "/api/radio/skip", method: "POST", consumer_action: "radio_skip" },
    { action: "queue room announce", endpoint: "/api/room/announce", method: "POST", consumer_action: "announce" },
  ].map((row) => ({
    ...row,
    endpoint_status: routeExists(row.endpoint, row.method) ? "PASS" : "MISSING_ENDPOINT",
    consumer_status: relaySupported.has(row.consumer_action) ? "PASS" : "QUEUED_ONLY",
  }));
  const publicSection = appSource.slice(appSource.indexOf("PUBLIC PORTAL"), appSource.indexOf("ADMIN SHELL"));
  const sensitiveMatches = findPublicSensitiveMatches(publicSection);
  const tokenExposureCheck = {
    public_render_sensitive_match: sensitiveMatches.length > 0,
    public_render_matches: sensitiveMatches,
    public_api_route_sensitive_names: ROUTE_SECURITY.filter((route) => route.public && /(?:token|secret|api[_-]?key|\.env|db_path)/i.test(route.path)),
    status: sensitiveMatches.length ? "REVIEW" : "PASS",
  };
  const publicSafetyIssues = [...qa.public_safety_warnings];
  if (sensitiveMatches.length) {
    publicSafetyIssues.push({ warning: "public_sensitive_literal", message: "Public renderer contains sensitive-looking literal or path.", matches: sensitiveMatches });
  }
  const rawIdMatches = [...publicSection.matchAll(/\b(?:[a-z0-9_-]{24,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b/gi)]
    .slice(0, 5)
    .map((m) => `${m[0].slice(0, 6)}…${m[0].slice(-4)}`);
  if (rawIdMatches.length) {
    publicSafetyIssues.push({ warning: "public_raw_user_id_exposure", message: "Public renderer contains long raw ID-like literals.", matches: rawIdMatches });
  }
  const commandConsumerGaps = visibleQueueActions
    .filter((action) => !relaySupported.has(action))
    .map((action) => ({ action, status: "QUEUED_ONLY", message: "Visible queued action is not explicitly consumed by bot_command_relay.py." }));
  const unresolvedUserRows = (() => {
    const users = usernameResolutionMap(db);
    const tables = ["quest_progress", "player_missions", "event_points", "pending_coin_rewards", "weekly_rewards", "fish_inventory", "fish_catch_records", "mining_inventory", "mining_logs", "premium_balances", "premium_transactions", "owned_items", "purchase_history"];
    const affected = [];
    let total = 0;
    for (const table of tables) {
      if (!tableExists(db, table) || !columnExists(db, table, "user_id")) continue;
      let count = 0;
      for (const row of safeRows(db, table, ["user_id", "username"], { limit: "10000" })) {
        if (!row.user_id) continue;
        if (row.username || users.has(String(row.user_id))) continue;
        count += 1;
      }
      if (count) {
        affected.push({ table, unresolved_rows: count });
        total += count;
      }
    }
    return { count: total, tables: affected };
  })();
  const criticalIssues = [
    ...publicPages.filter((row) => row.status !== "PASS").map((row) => ({ severity: "CRITICAL", area: "public", item: row.page, message: row.status })),
    ...ownerPages.filter((row) => row.status !== "PASS").map((row) => ({ severity: "CRITICAL", area: "owner", item: row.page, message: row.status })),
    ...staffPages.filter((row) => row.status !== "PASS").map((row) => ({ severity: "CRITICAL", area: "staff", item: row.page, message: row.status })),
    ...qa.missing_endpoints.map((row) => ({ severity: "CRITICAL", area: "api", item: row.endpoint, message: row.reason })),
    ...qa.missing_renderers.map((name) => ({ severity: "CRITICAL", area: "frontend", item: name, message: "Missing renderer." })),
    ...publicSafetyIssues.map((row) => ({ severity: "CRITICAL", area: "public-safety", item: row.warning, message: row.message })),
  ];
  const warnings = [
    ...qa.buttons_without_handlers.map((row) => ({ severity: "WARNING", area: "button", item: row.selector, message: row.reason })),
    ...permissionAudit.warnings.map((row) => ({ severity: "WARNING", area: "permission", item: `${row.method} ${row.path}`, message: row.warning })),
    ...commandConsumerGaps.map((row) => ({ severity: "WARNING", area: "command-queue", item: row.action, message: row.message })),
    ...(unresolvedUserRows.count ? [{ severity: "WARNING", area: "identity", item: "unresolved_user_rows", message: `${unresolvedUserRows.count} row(s) could not resolve to usernames.` }] : []),
  ];
  const passedChecks = [
    { check: "Public portal route registry", status: publicPages.every((row) => row.status === "PASS") ? "PASS" : "FAIL" },
    { check: "Owner dashboard route registry", status: ownerPages.every((row) => row.status === "PASS") ? "PASS" : "FAIL" },
    { check: "Staff console route registry", status: staffPages.every((row) => row.status === "PASS") ? "PASS" : "FAIL" },
    { check: "No missing render functions", status: qa.missing_renderers.length ? "FAIL" : "PASS" },
    { check: "No missing visible API endpoints", status: qa.missing_endpoints.length ? "FAIL" : "PASS" },
    { check: "Public token exposure static check", status: tokenExposureCheck.status },
    { check: "Bot command queue smoke endpoint coverage", status: queueSmokeTests.every((row) => row.endpoint_status === "PASS") ? "PASS" : "FAIL" },
    { check: "SQLite integrity", status: dbHealth.integrity_check === "ok" ? "PASS" : "WARN" },
  ];
  const issues = [...criticalIssues, ...warnings];
  return {
    generated_at: nowIso(),
    public_pages: publicPages,
    owner_pages: ownerPages,
    staff_pages: staffPages,
    frontend_routes: qa.frontend_pages,
    api_routes: ROUTE_SECURITY,
    missing_endpoints: qa.missing_endpoints,
    broken_buttons: qa.buttons_without_handlers,
    permission_issues: permissionAudit.warnings,
    public_safety_issues: publicSafetyIssues,
    token_exposure_check: tokenExposureCheck,
    unresolved_user_rows: unresolvedUserRows,
    command_queue_health: {
      counts: queueHealth.counts,
      visible_queue_actions: visibleQueueActions,
      allowed_actions: [...new Set(allowedActions)].sort(),
      relay_supported_actions: [...relaySupported].sort(),
      consumer_gaps: commandConsumerGaps,
      smoke_tests: queueSmokeTests,
    },
    db_health: dbHealth,
    critical_issues: criticalIssues,
    warnings,
    passed_checks: passedChecks,
    recommendations: issues.length
      ? issues.map((issue) => ({ area: issue.area, recommendation: `Review ${issue.item}: ${issue.message}` })).slice(0, 30)
      : [{ area: "dashboard", recommendation: "No static E2E issues detected. Continue with logged-in browser smoke testing after deploy." }],
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

app.get("/api/qa/audit", requireAuth, requireOwner, (_req, res) => {
  json(res, buildQaAudit());
}, closeDb);

app.get("/api/e2e/audit", requireAuth, requireOwner, (req, res) => {
  json(res, buildE2eAudit(req.db));
}, closeDb);

app.get("/api/release/status", requireAuth, requireOwner, async (req, res) => {
  json(res, await readReleaseStatus(req.db));
}, closeDb);

app.post("/api/release/checklist/run", requireAuth, requireOwner, async (req, res) => {
  const result = await runReleaseChecklist(req.db);
  audit(req.db, req.user.username, "release_checklist_run", "release", "dashboard-redesign", "", result, req.ip);
  json(res, result);
}, closeDb);

app.post("/api/release/backup", requireAuth, requireOwner, async (req, res) => {
  const result = await createDashboardDbBackup(req.db, req.user.username, req.ip, "release_db_backup");
  if (!result.ok) return json(res, { error: result.error, message: result.message }, result.status || 500);
  json(res, { ...result, release_status: await readReleaseStatus(req.db) });
}, closeDb);

app.get("/api/release/logs", requireAuth, requireOwner, async (req, res) => {
  const git = await gitReleaseSnapshot();
  const pm2 = await pm2Snapshot();
  json(res, {
    generated_at: nowIso(),
    recent_commits: git.recent_commits,
    pm2_restarts: pm2.processes.map((p) => ({ name: p.name, status: p.status, restart_time: p.restart_time, uptime: p.uptime })),
    dashboard_errors: safeTableRows(req.db, "admin_action_logs", { orderBy: columnExists(req.db, "admin_action_logs", "created_at") ? "created_at DESC" : "id DESC", limit: "100" }).filter((row) => JSON.stringify(row).toLowerCase().includes("error") || JSON.stringify(row).toLowerCase().includes("fail")),
    bot_errors: safeTableRows(req.db, "command_error_logs", { orderBy: columnExists(req.db, "command_error_logs", "created_at") ? "created_at DESC" : "id DESC", limit: "100" }),
    audit_events: safeRows(req.db, "audit_logs", ["id","actor","action_type","target_type","target_id","old_value","new_value","ip_address","created_at"], { orderBy: columnExists(req.db, "audit_logs", "created_at") ? "created_at DESC" : "id DESC", limit: "100" }),
    command_queue_failures: safeRows(req.db, "bot_command_queue", BOT_COMMAND_QUEUE_COLUMNS, { where: activeFailedCommandWhere(req.db), orderBy: columnExists(req.db, "bot_command_queue", "created_at") ? "created_at DESC" : "id DESC", limit: "100" }),
  });
}, closeDb);

app.get("/api/public-settings", requireAuth, requireOwner, (req, res) => {
  const key = "public_rankings_hide_staff_bots";
  json(res, {
    settings: {
      [key]: publicRankingsHideStaffBots(req.db),
    },
    sources: {
      [key]: { table: "room_settings", key, default: true },
    },
  });
}, closeDb);

app.put("/api/public-settings", requireAuth, requireOwner, (req, res) => {
  const key = "public_rankings_hide_staff_bots";
  const oldValue = getSetting(req.db, key, "true");
  const raw = req.body?.[key];
  const nextValue = raw === false || raw === "false" || raw === 0 || raw === "0" ? "false" : "true";
  setRoomSetting(req.db, key, nextValue);
  audit(req.db, req.user.username, "public_settings_update", "room_settings", key, oldValue, nextValue, req.ip);
  json(res, { ok: true, settings: { [key]: nextValue === "true" }, source: "room_settings" });
}, closeDb);

app.get("/api/maintenance/overview", requireAuth, requireOwner, async (req, res) => {
  json(res, await maintenanceOverview(req.db));
}, closeDb);

app.get("/api/maintenance/backups", requireAuth, requireOwner, (req, res) => {
  json(res, {
    backups: backupFileList(),
    env_metadata: envMetadata(),
    approved_backup_folders: APPROVED_BACKUP_DIRS,
    db_path: DB_PATH,
    db_file: fileMeta(DB_PATH),
  });
}, closeDb);

app.post("/api/maintenance/backup/db", requireAuth, requireOwner, async (req, res) => {
  const result = await createDashboardDbBackup(req.db, req.user.username, req.ip, "maintenance_db_backup");
  if (!result.ok) return json(res, { error: result.error, message: result.message }, result.status || 500);
  json(res, result);
}, closeDb);

app.post("/api/maintenance/backup/dashboard", requireAuth, requireOwner, (req, res) => {
  const stamp = formatStamp();
  ensureDir(DASHBOARD_BACKUP_DIR);
  const files = ["server.mjs", "public/app.js", "public/styles.css", "public/index.html"];
  const copied = [];
  try {
    for (const rel of files) {
      const src = path.join(__dirname, rel);
      if (!fs.existsSync(src)) continue;
      const safeName = rel.replace(/[\\/]/g, "__");
      const dest = path.join(DASHBOARD_BACKUP_DIR, `${safeName}.${stamp}.bak`);
      fs.copyFileSync(src, dest);
      copied.push(fileMeta(dest));
    }
    audit(req.db, req.user.username, "maintenance_dashboard_backup", "backup_folder", DASHBOARD_BACKUP_DIR, "", copied.map((f) => f.name || f.path), req.ip);
    json(res, { ok: true, copied, folder: DASHBOARD_BACKUP_DIR });
  } catch (err) {
    audit(req.db, req.user.username, "maintenance_dashboard_backup_failed", "backup_folder", DASHBOARD_BACKUP_DIR, "", err.message, req.ip);
    json(res, { error: "dashboard_backup_failed", message: err.message }, 500);
  }
}, closeDb);

app.post("/api/maintenance/backup/env-metadata", requireAuth, requireOwner, (req, res) => {
  const confirmation = String(req.body?.confirmation || "");
  const meta = envMetadata();
  if (confirmation !== "BACKUP ENV") return json(res, { error: "confirmation_required", required: "BACKUP ENV", env_metadata: meta }, 400);
  if (!meta.file_exists) return json(res, { error: "env_missing", env_metadata: meta }, 404);
  ensureDir(DASHBOARD_BACKUP_DIR, 0o700);
  const dest = path.join(DASHBOARD_BACKUP_DIR, `.env.dashboard_backup_${formatStamp()}`);
  try {
    fs.copyFileSync(VPS_ENV_PATH, dest);
    try { fs.chmodSync(dest, 0o600); } catch {}
    audit(req.db, req.user.username, "maintenance_env_backup", "backup_file", dest, "", { env_path: VPS_ENV_PATH, token_keys: meta.token_keys }, req.ip);
    json(res, { ok: true, env_metadata: meta, backup: { path: dest, exists: true, mode: fileMeta(dest).mode }, message: "Environment file backed up server-side; contents are not returned." });
  } catch (err) {
    audit(req.db, req.user.username, "maintenance_env_backup_failed", "backup_file", dest, "", err.message, req.ip);
    json(res, { error: "env_backup_failed", message: err.message, env_metadata: meta }, 500);
  }
}, closeDb);

app.get("/api/maintenance/restore-preview", requireAuth, requireOwner, (req, res) => {
  const backup = String(req.query.backup || "").trim();
  const resolved = path.resolve(backup);
  if (!backup || !isApprovedBackupPath(resolved, [DB_BACKUP_DIR])) {
    return json(res, { error: "backup_not_approved", approved_backup_folder: DB_BACKUP_DIR }, 400);
  }
  const meta = fileMeta(resolved);
  if (!meta.exists || !resolved.endsWith(".db")) return json(res, { error: "backup_not_found_or_not_db", backup: meta }, 404);
  json(res, {
    backup: meta,
    current_db: fileMeta(DB_PATH),
    will_create_pre_restore_backup: true,
    required_confirmation: "RESTORE DATABASE",
    warnings: [
      "Restoring replaces the live SQLite DB file.",
      "Restart ChillTopia-8Bots and ChillTopia-Dashboard after restore.",
      "Dashboard file restore is intentionally not automated.",
    ],
  });
}, closeDb);

app.post("/api/maintenance/restore-db", requireAuth, requireOwner, async (req, res) => {
  const backup = path.resolve(String(req.body?.backup || "").trim());
  const confirmation = String(req.body?.confirmation || "");
  if (confirmation !== "RESTORE DATABASE") return json(res, { error: "confirmation_required", required: "RESTORE DATABASE" }, 400);
  if (!isApprovedBackupPath(backup, [DB_BACKUP_DIR]) || !backup.endsWith(".db")) return json(res, { error: "backup_not_approved", approved_backup_folder: DB_BACKUP_DIR }, 400);
  if (!fs.existsSync(backup)) return json(res, { error: "backup_not_found" }, 404);
  ensureDir(DB_BACKUP_DIR);
  const preRestore = path.join(DB_BACKUP_DIR, `highrise_hangout.pre_restore_${formatStamp()}.db`);
  try {
    try { req.db.prepare("PRAGMA wal_checkpoint(PASSIVE)").get(); } catch {}
    await req.db.backup(preRestore);
    audit(req.db, req.user.username, "maintenance_db_restore", "sqlite_db", DB_PATH, { pre_restore_backup: preRestore }, { restored_from: backup }, req.ip);
    fs.copyFileSync(backup, DB_PATH);
    json(res, {
      ok: true,
      restored_from: backup,
      pre_restore_backup: preRestore,
      next_steps: ["restart ChillTopia-8Bots", "restart ChillTopia-Dashboard"],
      warning: "Live DB file was replaced. Restart bots and dashboard so every process reopens SQLite cleanly.",
    });
  } catch (err) {
    audit(req.db, req.user.username, "maintenance_db_restore_failed", "sqlite_db", DB_PATH, backup, err.message, req.ip);
    json(res, { error: "restore_failed", message: err.message }, 500);
  }
}, closeDb);

app.get("/api/maintenance/db-health", requireAuth, requireOwner, (req, res) => {
  json(res, dbHealthSnapshot(req.db));
}, closeDb);

app.get("/api/maintenance/cleanup-preview", requireAuth, requireOwner, (req, res) => {
  json(res, cleanupPreview(req.db, req.query.retention_days || 30));
}, closeDb);

app.post("/api/maintenance/cleanup", requireAuth, requireOwner, (req, res) => {
  const cleanupType = String(req.body?.cleanup_type || "").trim();
  const confirmation = String(req.body?.confirmation || "");
  const dryRun = req.body?.dry_run !== false;
  const preview = cleanupPreview(req.db, req.body?.retention_days || 30);
  const candidate = preview.candidates.find((row) => row.cleanup_type === cleanupType);
  if (!candidate) return json(res, { error: "cleanup_type_not_allowed", preview }, 400);
  if (confirmation !== "CLEANUP") return json(res, { error: "confirmation_required", required: "CLEANUP", preview }, 400);
  if (!dryRun) return json(res, { error: "actual_cleanup_disabled", message: "This build only returns cleanup previews for DB rows and files.", preview }, 409);
  audit(req.db, req.user.username, "maintenance_cleanup_preview", "cleanup", cleanupType, "", candidate, req.ip);
  json(res, { ok: true, dry_run: true, candidate, preview });
}, closeDb);

app.get("/api/maintenance/runtime-health", requireAuth, requireOwner, async (req, res) => {
  const pm2 = await pm2Snapshot();
  const bots = tableExists(req.db, "bot_instances")
    ? safeRows(req.db, "bot_instances", ["bot_username","bot_mode","status","last_heartbeat_at","last_error"], { orderBy: "bot_mode, bot_username", limit: "250" })
    : [];
  const queueCounts = tableExists(req.db, "bot_command_queue") && columnExists(req.db, "bot_command_queue", "status")
    ? rowsOrEmpty(req.db, "bot_command_queue", "SELECT status, COUNT(*) AS n FROM bot_command_queue GROUP BY status")
    : [];
  json(res, {
    pm2,
    bot_heartbeats: bots,
    heartbeat_count: bots.length,
    command_queue_counts: queueCounts,
    command_queue_failures: safeRows(req.db, "bot_command_queue", ["id","target_bot","action","status","requester_id","created_at","claimed_by","completed_at","error_text"], { where: activeFailedCommandWhere(req.db), orderBy: "id DESC", limit: "50" }),
  });
}, closeDb);

app.get("/api/maintenance/logs", requireAuth, requireOwner, (req, res) => {
  json(res, {
    audit_logs: safeRows(req.db, "audit_logs", ["id","actor","action_type","target_type","target_id","old_value","new_value","ip_address","created_at"], { orderBy: "id DESC", limit: "100" }),
    admin_action_logs: safeTableRows(req.db, "admin_action_logs", { orderBy: "id DESC", limit: "100" }),
    command_queue_failures: safeRows(req.db, "bot_command_queue", ["id","target_bot","action","payload","status","requester_id","created_at","claimed_by","completed_at","error_text"], { where: activeFailedCommandWhere(req.db), orderBy: "id DESC", limit: "100" }),
    radio_failures: tableExists(req.db, "yt_request_jobs") ? safeRows(req.db, "yt_request_jobs", ["id","title","username","status","error","created_at","finished_at"], { where: "status IN ('failed','failed_download','error','cancelled')", orderBy: "id DESC", limit: "100" }) : [],
  });
}, closeDb);

app.get("/api/maintenance/advanced", requireAuth, requireOwner, (req, res) => {
  json(res, {
    db_path: DB_PATH,
    approved_backup_folders: APPROVED_BACKUP_DIRS,
    retention_settings: { default_days: 30, max_days: 365 },
    raw_health: dbHealthSnapshot(req.db),
    restore_warnings: [
      "Only DB files inside the approved DB backup folder can be restored.",
      "A pre-restore backup is created before overwrite.",
      "Restart bots and dashboard after restore.",
    ],
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
    const settings = readKeyValueMap(db, "room_settings");
    const eventSettings = readKeyValueMap(db, "event_settings");
    const activeEventName = eventSettings.event_active === "1" ? eventSettings.event_name : (settings.active_event || settings["event.active_event"] || "");
    const hide = publicRankingsHideStaffBots(db);
    const rankings = buildLeaderboards(db, { hideStaff: hide, hideBots: hide }).leaderboards || {};
    const first = (rows) => Array.isArray(rows) && rows.length ? rows[0] : null;
    const highlights = {
      top_song: (() => {
        const row = first(rankings.radio_liked) || first(rankings.radio_tracks);
        return row ? { title: row.title || row.name || "Top track", detail: row.likes ? `${row.likes} likes` : row.plays ? `${row.plays} plays` : "" } : null;
      })(),
      big_fish: (() => {
        const row = first(rankings.fishing_heaviest_fish);
        return row ? { title: row.fish || "Big fish", detail: `${row.weight ?? "—"} lbs · ${row.username || "Unknown Player"}` } : null;
      })(),
      big_ore: (() => {
        const row = first(rankings.mining_heaviest_ore);
        return row ? { title: row.ore || "Big ore", detail: `${row.weight ?? "—"} lbs · ${row.username || "Unknown Player"}` } : null;
      })(),
      casino_winner: (() => {
        const row = first(rankings.blackjack) || first(rankings.poker) || first(rankings.casino_overall);
        return row ? { title: row.username || "Casino leader", detail: row.net ? `${row.net} coins net` : row.wins ? `${row.wins} wins` : "" } : null;
      })(),
      event_winner: (() => {
        const row = first(rankings.event_points);
        return row ? { title: row.username || "Event leader", detail: `${row.points ?? 0} pts` } : null;
      })(),
    };
    json(res, {
      online_bots: onlineBots,
      total_bots: bots.length,
      room_users: roomUsers,
      queue_count: queueCount,
      now_playing: nowPlaying ? { title: nowPlaying.title, artist: nowPlaying.artist } : null,
      vibe,
      current_event: activeEventName ? { active: true, name: activeEventName } : { active: false, name: "" },
      join_url: settings.highrise_join_url || settings.room_url || settings.join_url || "",
      highlights,
    });
  } catch (err) {
    json(res, { online_bots: 0, total_bots: 0, room_users: 0, queue_count: 0, now_playing: null, vibe: "Chill vibes", current_event: { active: false, name: "" }, highlights: {} });
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
    const hide = publicRankingsHideStaffBots(db);
    const rankings = buildLeaderboards(db, { hideStaff: hide, hideBots: hide }).leaderboards || {};
    json(res, {
      now_playing: radio.now_playing ? { title: radio.now_playing.title, artist: radio.now_playing.artist, username: radio.now_playing.username } : null,
      queue: safeQueue,
      recently_played: safeRecent,
      queue_open: queueOpen,
      stream_url: AZURACAST_STREAM_URL || null,
      playlist_urls_disabled: true,
      leaderboards: {
        top_requesters: rankings.radio_requesters || [],
        top_liked_songs: rankings.radio_liked || [],
        top_disliked_songs: rankings.radio_disliked || [],
      },
    });
  } catch (err) {
    json(res, { now_playing: null, queue: [], recently_played: [], queue_open: true, stream_url: null, playlist_urls_disabled: true, leaderboards: { top_requesters: [], top_liked_songs: [], top_disliked_songs: [] } });
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
    const definitions = safeRows(db, "event_definitions", ["id","name","description","reward","enabled"], { limit: "50" });
    const hide = publicRankingsHideStaffBots(db);
    const rankings = buildLeaderboards(db, { hideStaff: hide, hideBots: hide }).leaderboards || {};
    json(res, { current_settings: [...roomCurrent, ...eventCurrent], scheduled, definitions, leaderboards: { event_points: rankings.event_points || [] } });
  } catch {
    json(res, { current_settings: [], scheduled: [], definitions: [], leaderboards: { event_points: [] } });
  } finally {
    if (db) try { db.close(); } catch {}
  }
});

function publicHowToPlayPayload(db) {
  const missingTables = new Set();
  const missingColumns = new Set();
  const missingTable = (table) => { if (!tableExists(db, table)) missingTables.add(table); };
  const missingColumn = (table, column) => {
    if (tableExists(db, table) && !columnExists(db, table, column)) missingColumns.add(`${table}.${column}`);
  };
  const radio = (() => {
    const status = readLocalRadioStatus(db);
    let requestsEnabled = true;
    try { requestsEnabled = db.prepare("SELECT value FROM bot_settings WHERE key='requests_enabled' LIMIT 1").get()?.value !== "false"; } catch {}
    return {
      queue_open: requestsEnabled,
      now_playing: status.now_playing ? { title: status.now_playing.title, artist: status.now_playing.artist, username: status.now_playing.username } : null,
      queue_size: status.queue?.length || 0,
      recently_played: (status.recently_played || []).slice(0, 5).map((row) => ({ title: row.title, artist: row.artist, username: row.username })),
      playlist_urls_disabled: true,
      local_and_youtube: "Requests may use local replay or YouTube pipeline depending on availability and DJ DUDU's current radio mode.",
    };
  })();
  const blackjack = readActiveBlackjackSettings(db);
  const poker = readActivePokerSettings(db);
  const miningSettings = readActiveMiningSettings(db);
  const fishingSettings = readActiveFishingSettings(db);
  const miningOdds = calculateMiningDropRows(db);
  if (!tableExists(db, "mining_items")) missingTable("mining_items");
  for (const col of ["item_id", "name", "rarity", "sell_value", "drop_enabled"]) missingColumn("mining_items", col);
  const ores = enrichedMiningOreRows(db, true).map((row) => ({ ...row, value: row.sell_value })).slice(0, 200);
  const fishCode = readFishingCodeCatalog();
  const fishingOdds = calculateFishDropRowsFromDb(db);
  const fish = enrichedFishingRows(db).rows.slice(0, 200);
  const miningCommands = verifiedCommands([
    { command: "mine", display: "!mine", description: "Mine for ores, coins, and mining XP." },
    { command: "topminers", display: "!topminers", description: "Open the mining leaderboard." },
    { command: "orebook", display: "!orebook", description: "View your ore discovery book." },
    { command: "ores", display: "!ores / !mineinv", description: "View your mining inventory." },
    { command: "sellores", display: "!sellores", description: "Sell ores from your mining inventory." },
    { command: "tool", display: "!tool / !pickaxe", description: "Check your pickaxe/tool status." },
    { command: "upgradetool", display: "!upgradetool", description: "Upgrade your mining tool when eligible." },
    { command: "mineprofile", display: "!mineprofile", description: "View mining profile and rank." },
    { command: "minelb", display: "!minelb", description: "Show mining leaderboard details." },
    { command: "mineshop", display: "!mineshop", description: "View mining shop/tool options." },
    { command: "minedaily", display: "!minedaily", description: "Claim mining daily reward if active." },
    { command: "orelist", display: "!orelist [rarity]", description: "Browse ores by rarity." },
    { command: "rarelog", display: "!rarelog", description: "View recent rare ore discoveries." },
    { command: "mineluck", display: "!mineluck", description: "Check mining luck stack/boosts." },
    { command: "contracts", display: "!contracts", description: "View mining jobs/contracts when active." },
  ]);
  const fishingCommands = verifiedCommands([
    { command: "fish", display: "!fish", description: "Cast your line and catch fish." },
    { command: "topfishers", display: "!topfishers", description: "Open the fishing leaderboard." },
    { command: "fishbook", display: "!fishbook", description: "View your fish discovery book." },
    { command: "myfish", display: "!myfish / !fishinv / !fishbag", description: "View your fish inventory." },
    { command: "sellfish", display: "!sellfish", description: "Sell fish from your inventory." },
    { command: "sellallfish", display: "!sellallfish", description: "Sell all eligible fish." },
    { command: "fishlist", display: "!fishlist", description: "Browse fish by rarity." },
    { command: "fishprices", display: "!fishprices", description: "View fish values." },
    { command: "fishinfo", display: "!fishinfo [fish]", description: "View details for one fish." },
    { command: "fishautosell", display: "!fishautosell", description: "Manage fish auto-sell if enabled." },
    { command: "fishautosellrare", display: "!fishautosellrare", description: "Protect rare fish from auto-sell." },
    { command: "fishluck", display: "!fishluck", description: "Check fishing luck stack/boosts." },
    { command: "fishhelp", display: "!fishhelp / !fishinghelp", description: "Show fishing help." },
    { command: "topfish", display: "!topfish / !fishlb", description: "Show fishing leaderboard details." },
    { command: "topweightfish", display: "!topweightfish", description: "Show heaviest fish rankings." },
  ]);
  const rareMiningPreview = ores
    .filter((row) => Number(row.chance_percent || 0) > 0 && ["exotic", "prismatic", "mythic", "legendary", "epic"].includes(row.rarity))
    .sort((a, b) => rarityRank(b.rarity, MINING_RARITY_ORDER) - rarityRank(a.rarity, MINING_RARITY_ORDER)
      || Number(a.chance_percent || 0) - Number(b.chance_percent || 0)
      || Number(b.value || 0) - Number(a.value || 0))
    .slice(0, 6);
  const rareFishingPreview = fish
    .filter((row) => Number(row.chance_percent || 0) > 0 && ["exotic", "prismatic", "mythic", "legendary", "epic"].includes(row.rarity))
    .sort((a, b) => rarityRank(b.rarity, FISHING_RARITY_ORDER) - rarityRank(a.rarity, FISHING_RARITY_ORDER)
      || Number(a.chance_percent || 0) - Number(b.chance_percent || 0)
      || Number(b.base_value || 0) - Number(a.base_value || 0))
    .slice(0, 6);
  const rankings = buildLeaderboards(db);
  const eventCurrent = safeRows(db, "event_settings", ["key", "value"], { where: "key IN ('event_active','event_name','event_expires_at')", limit: "20" });
  const scheduled = safeRows(db, "scheduled_events", ["id", "name", "description", "starts_at", "ends_at", "points", "reward"], { orderBy: "starts_at ASC", limit: "10" });
  if (!tableExists(db, "scheduled_events")) missingTable("scheduled_events");
  return {
    radio,
    casino: {
      blackjack_settings: {
        enabled: blackjack.rbj_enabled,
        min_bet: blackjack.min_bet,
        max_bet: blackjack.max_bet,
        max_players: blackjack.max_players,
        turn_timer: blackjack.rbj_action_timer,
        decks: blackjack.decks,
        shuffle_used_percent: blackjack.shuffle_used_percent,
        win_payout: blackjack.win_payout,
        blackjack_payout: blackjack.blackjack_payout,
        daily_win_limit: blackjack.rbj_daily_win_limit,
        source: blackjack.source,
      },
      poker_settings: {
        enabled: poker.enabled,
        min_buyin: poker.min_buyin,
        max_buyin: poker.max_buyin,
        max_players: poker.max_players,
        turn_timer: poker.turn_timer,
        small_blind: poker.small_blind,
        big_blind: poker.big_blind,
        source: poker.source,
      },
      leaderboards: {
        casino_overall: rankings.leaderboards?.casino_overall || rankings.leaderboards?.most_games_won || [],
        blackjack: rankings.leaderboards?.blackjack || [],
        poker: rankings.leaderboards?.poker || [],
      },
    },
    mining: {
      basics: [
        "Use !mine to search for ores.",
        "Mining can reward coins, mining XP, rare ores, and leaderboard progress.",
        "Rare ores can trigger announcements and bonus rewards.",
        "Better tools or pickaxes may improve progression when that system is active.",
      ],
      player_settings: {
        cooldown: miningSettings.base_cooldown_seconds ? `Mining has a short cooldown between attempts.` : "",
        announcements: miningSettings.mining_announce_enabled === "1" ? "Rare finds may be announced in-room." : "",
        tools: "Pickaxes are represented by mining tool levels when active.",
      },
      commands: miningCommands,
      rarity_order: MINING_RARITY_ORDER,
      rarity_chips: MINING_RARITY_ORDER.map((rarity) => ({ rarity, label: RARITY_LABELS[rarity] || rarity })),
      ores,
      ores_by_rarity: groupByRarity(ores, MINING_RARITY_ORDER),
      odds: miningOdds.map((row) => ({ ...row, rarity: normalizeRarity(row.rarity), chance_label: chanceTextFromPercent(row.chance_percent, "Not currently dropping") })).slice(0, 100),
      rare_preview: rareMiningPreview,
      rarest: rareMiningPreview,
      odds_notes: [
        "Drop chances are calculated from active rarity weights and enabled catalog rows when available.",
        "Very tiny active chances are shown as <0.0001% instead of rounded to zero.",
        "Events, boosts, and tools may affect live results when the bot enables them.",
      ],
      tools: PICKAXE_CATALOG,
      leaderboards: {
        top_miners: rankings.leaderboards?.mining_top || [],
        heaviest_ores: rankings.leaderboards?.mining_heaviest_ore || [],
        most_valuable_ores: rankings.leaderboards?.mining_most_valuable || [],
        rarest_finds: rankings.leaderboards?.mining_rarest || [],
      },
    },
    fishing: {
      basics: [
        "Use !fish to cast your line.",
        "Fishing can reward coins, fishing XP, big catches, rare fish, and leaderboard progress.",
        "Rare and heavy catches can appear on leaderboards.",
        "Rods or tools may improve catches when that system is active.",
      ],
      player_settings: {
        cooldown: fishingSettings.fish_base_interval ? "Fishing has a short cooldown between casts." : "",
        auto: fishingSettings.autofish_enabled === "1" ? "AutoFish may be available from in-room commands when enabled." : "",
        tools: "Rods and boosts are shown when the active fishing module exposes public data.",
      },
      commands: fishingCommands,
      rarity_order: FISHING_RARITY_ORDER,
      rarity_chips: FISHING_RARITY_ORDER.map((rarity) => ({ rarity, label: RARITY_LABELS[rarity] || rarity })),
      fish,
      fish_by_rarity: groupByRarity(fish, FISHING_RARITY_ORDER),
      odds: fishingOdds.map((row) => ({ ...row, rarity: normalizeRarity(row.rarity), chance_label: chanceTextFromPercent(row.chance_percent, "Not currently catching") })).slice(0, 100),
      rare_preview: rareFishingPreview,
      rarest: rareFishingPreview,
      odds_notes: [
        "Catch chances are calculated from active fish weights when available.",
        "Very tiny active chances are shown as <0.0001% instead of rounded to zero.",
        "Rods, boosts, events, and VIP bonuses may affect live catches when enabled.",
      ],
      rods: fishCode.rods,
      leaderboards: {
        top_fishers: rankings.leaderboards?.fishing_top || [],
        heaviest_fish: rankings.leaderboards?.fishing_heaviest_fish || [],
        most_valuable_fish: rankings.leaderboards?.fishing_most_valuable || [],
        rarest_catches: rankings.leaderboards?.fishing_rarest || [],
      },
    },
    events: {
      current_settings: eventCurrent,
      scheduled,
      leaderboards: { event_points: rankings.leaderboards?.event_points || [] },
    },
    commands: [
      { category: "Radio", command: "!play [song]", description: "Request a song by name, artist, or URL." },
      { category: "Radio", command: "!q / !queue", description: "View the current request queue." },
      { category: "Radio", command: "!now / !np", description: "Show the current song if supported." },
      { category: "Radio", command: "!like / !dislike", description: "Rate the current song if ratings are enabled.", availability: "if available" },
      { category: "Radio", command: "!skip", description: "Skip current song.", availability: "staff/owner only" },
      { category: "Economy", command: "!balance", description: "Check your coins." },
      { category: "Economy", command: "!daily", description: "Claim your daily reward." },
      { category: "Economy", command: "!profile", description: "View your public stats." },
      { category: "Economy", command: "!top / !leaderboard", description: "Show the leaderboard menu." },
      { category: "Economy", command: "!toprich", description: "Richest players." },
      { category: "Economy", command: "!topstreaks", description: "Daily claim streaks." },
      { category: "Gold / Tips", command: "!topdonators", description: "Top gold supporters." },
      { category: "Gold / Tips", command: "!toptippers", description: "Top P2P senders." },
      { category: "Gold / Tips", command: "!toptipped", description: "Top P2P receivers." },
      { category: "Badges", command: "!badgeshop", description: "Browse purchasable badges." },
      { category: "Badges", command: "!allbadges", description: "Browse all known badges if enabled." },
      { category: "Badges", command: "!badgeinfo [id]", description: "Show badge details." },
      { category: "Badges", command: "!buy badge [id]", description: "Buy a badge from the badge shop." },
      { category: "Badges", command: "!equip badge [id]", description: "Equip an owned badge." },
      { category: "Badges", command: "!mybadges", description: "View your badges." },
      { category: "Badge Market", command: "!badgemarket", description: "Browse player badge listings." },
      { category: "Badge Market", command: "!badgelist [id] [price]", description: "List a tradeable badge for sale." },
      { category: "Badge Market", command: "!badgebuy [listing_id]", description: "Buy a badge market listing." },
      { category: "Badge Market", command: "!badgecancel [listing_id]", description: "Cancel your own active listing." },
      { category: "Titles", command: "!shop titles / !titleshop", description: "Browse title shop pages." },
      { category: "Titles", command: "!buy title [id]", description: "Buy a title." },
      { category: "Titles", command: "!equip title [id]", description: "Equip an owned title." },
      { category: "Luxe", command: "!luxe / !tickets", description: "Check Luxe Ticket info and balance." },
      { category: "Luxe", command: "!luxeshop", description: "Browse the Luxe Ticket shop." },
      { category: "Luxe", command: "!buyluxe [number]", description: "Buy an item from the Luxe shop." },
      { category: "Luxe", command: "!packs / !buycoins", description: "View or buy ChillCoins with Luxe Tickets." },
      { category: "Blackjack", command: "!bj [amount]", description: "Join Realistic Blackjack." },
      { category: "Blackjack", command: "!hit", description: "Take another card." },
      { category: "Blackjack", command: "!stand", description: "Hold your hand." },
      { category: "Blackjack", command: "!double", description: "Double your bet and take one card." },
      { category: "Blackjack", command: "!split", description: "Split matching cards if available." },
      { category: "Poker", command: "!join [amount]", description: "Join the poker table with a buy-in." },
      { category: "Poker", command: "!poker / !poker table", description: "Show poker table/status.", availability: "if available" },
      { category: "Poker", command: "!leave", description: "Leave the poker table when allowed." },
      { category: "Poker", command: "!check / !call / !raise [amount] / !fold / !allin", description: "Poker actions on your turn." },
      ...miningCommands.map((cmd) => ({ category: "Mining", ...cmd })),
      ...fishingCommands.map((cmd) => ({ category: "Fishing", ...cmd })),
      { category: "Emotes", command: "!emote [name]", description: "Trigger a known emote.", availability: "if available" },
      { category: "Emotes", command: "!sync / !syncstop / !syncstatus", description: "Join, stop, or check sync.", availability: "if available" },
      { category: "Events", command: "!events / !event", description: "Check current events.", availability: "if available" },
    ],
    diagnostics: {
      missing_tables: [...new Set([...missingTables, ...(rankings.diagnostics?.missing_tables || [])])],
      missing_columns: [...new Set([...missingColumns, ...(rankings.diagnostics?.missing_columns || [])])],
      generated_at: nowIso(),
    },
  };
}

app.get("/api/public/how-to-play", async (_req, res) => {
  let db = null;
  try {
    db = await openDb({ readonly: true });
    json(res, publicHowToPlayPayload(db));
  } catch (err) {
    json(res, {
      radio: {},
      casino: { blackjack_settings: {}, poker_settings: {} },
      mining: { settings: {}, ores: [], odds: [], tools: [], leaderboards: {} },
      fishing: { settings: {}, fish: [], odds: [], rods: [], leaderboards: {} },
      events: { current_settings: [], scheduled: [] },
      commands: [],
      diagnostics: { missing_tables: [], missing_columns: [], generated_at: nowIso(), error: err.message },
    });
  } finally {
    if (db) try { db.close(); } catch {}
  }
});

app.get("/api/public/casino", async (_req, res) => {
  let db = null;
  try {
    db = await openDb({ readonly: true });
    const payload = publicHowToPlayPayload(db);
    json(res, {
      casino: payload.casino,
      commands: payload.commands.filter((cmd) => ["Blackjack", "Poker"].includes(cmd.category)),
      diagnostics: { generated_at: payload.diagnostics.generated_at },
    });
  } catch (err) {
    json(res, { casino: { blackjack_settings: {}, poker_settings: {}, leaderboards: {} }, commands: [], diagnostics: { generated_at: nowIso(), error: err.message } });
  } finally {
    if (db) try { db.close(); } catch {}
  }
});

app.get("/api/public/mining", async (_req, res) => {
  let db = null;
  try {
    db = await openDb({ readonly: true });
    const payload = publicHowToPlayPayload(db);
    json(res, {
      mining: payload.mining,
      commands: payload.commands.filter((cmd) => ["Mining", "Economy"].includes(cmd.category)),
      diagnostics: { generated_at: payload.diagnostics.generated_at },
    });
  } catch (err) {
    json(res, { mining: { settings: {}, ores: [], odds: [], tools: [], leaderboards: {} }, commands: [], diagnostics: { generated_at: nowIso(), error: err.message } });
  } finally {
    if (db) try { db.close(); } catch {}
  }
});

app.get("/api/public/fishing", async (_req, res) => {
  let db = null;
  try {
    db = await openDb({ readonly: true });
    const payload = publicHowToPlayPayload(db);
    json(res, {
      fishing: payload.fishing,
      commands: payload.commands.filter((cmd) => ["Fishing", "Economy"].includes(cmd.category)),
      diagnostics: { generated_at: payload.diagnostics.generated_at },
    });
  } catch (err) {
    json(res, { fishing: { settings: {}, fish: [], odds: [], rods: [], leaderboards: {} }, commands: [], diagnostics: { generated_at: nowIso(), error: err.message } });
  } finally {
    if (db) try { db.close(); } catch {}
  }
});

app.get("/api/public/quests", async (_req, res) => {
  let db = null;
  try {
    db = await openDb({ readonly: true });
    const quests = readQuestsDashboard(db);
    const active = quests.catalog.rows.filter((row) => {
      const enabled = row.enabled ?? row.active ?? row.is_active;
      const disabled = row.disabled ?? row.archived ?? row.is_archived;
      return String(enabled ?? "1").toLowerCase() !== "0" && String(enabled ?? "true").toLowerCase() !== "false" && String(disabled ?? "0") !== "1";
    });
    json(res, {
      overview: quests.overview,
      daily_quests: quests.daily_quests.slice(0, 25),
      weekly_quests: quests.weekly_quests.slice(0, 25),
      event_quests: quests.event_quests.slice(0, 25),
      active_quests: active.slice(0, 50),
      rewards: {
        pending_count: quests.overview.pending_rewards,
        weekly_rewards: quests.rewards.weekly_rewards.slice(0, 25),
      },
      commands: [
        { command: "!quests", description: "View active quests or missions if enabled." },
        { command: "!missions", description: "Check your personal mission list if supported." },
        { command: "!daily", description: "Claim daily rewards and streak progress." },
        { command: "!profile", description: "View your profile, level, and progress." },
      ],
      source: quests.catalog.table || "quest_progress",
      diagnostics: { generated_at: nowIso() },
    });
  } catch (err) {
    json(res, { overview: {}, daily_quests: [], weekly_quests: [], event_quests: [], active_quests: [], rewards: {}, commands: [], diagnostics: { generated_at: nowIso(), error: err.message } });
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
    const titleCols = tableExists(db, "title_catalog") ? tableColumns(db, "title_catalog") : [];
    const titles = titleCols.includes("title_id")
      ? safeRows(db, "title_catalog", ["title_id", "display", "display_name", "name", "tier", "rarity", "description"], { limit: "50" })
          .map((row) => ({ title_id: row.title_id, name: row.display || row.display_name || row.name || row.title_id, tier: row.tier || row.rarity || "", description: row.description || "" }))
      : [];
    const badges = tableExists(db, "badge_claims")
      ? safeTableRows(db, "badge_claims", { limit: "50" }).map((row) => ({ badge_id: row.badge_id || row.id || row.badge || "", name: row.name || row.badge_name || row.badge_id || row.id || "", description: row.description || row.reason || "" })).filter((row) => row.badge_id || row.name)
      : [];
    json(res, { info, announcements, rewards: { titles, badges, vip_source: "owned_items.item_id=vip" }, join_url: settings.highrise_join_url || settings.room_url || settings.join_url || "" });
  } catch {
    json(res, { info: {}, announcements: [], rewards: { titles: [], badges: [] }, join_url: "" });
  } finally {
    if (db) try { db.close(); } catch {}
  }
});

app.get("/api/public/rankings", async (req, res) => {
  let db = null;
  const scrubUserIds = (value) => {
    if (Array.isArray(value)) return value.map(scrubUserIds);
    if (!value || typeof value !== "object") return value;
    const out = {};
    for (const [key, item] of Object.entries(value)) {
      if (key === "user_id") continue;
      out[key] = scrubUserIds(item);
    }
    return out;
  };
  const minimalRankings = (errorMessage = "") => {
    const addRank = (rows) => rows.map((row, index) => ({ rank: index + 1, ...row }));
    const query = (name, table, sql) => {
      try {
        if (!tableExists(db, table)) return [];
        return addRank(db.prepare(sql).all());
      } catch {
        return [];
      }
    };
    const rich = query("rich", "users", "SELECT username, balance FROM users ORDER BY COALESCE(CAST(balance AS REAL),0) DESC LIMIT 10");
    const xp = query("xp", "users", "SELECT username, xp, level FROM users ORDER BY COALESCE(CAST(xp AS REAL),0) DESC LIMIT 10");
    const level = query("level", "users", "SELECT username, level, xp FROM users ORDER BY COALESCE(CAST(level AS REAL),0) DESC, COALESCE(CAST(xp AS REAL),0) DESC LIMIT 10");
    const games = query("most_games_won", "users", "SELECT username, total_games_won AS wins FROM users ORDER BY COALESCE(CAST(total_games_won AS REAL),0) DESC LIMIT 10");
    const mining = query("mining", "mining_players", "SELECT username, mining_xp AS xp, mining_level AS level, total_ores AS total_mined FROM mining_players ORDER BY COALESCE(CAST(mining_xp AS REAL),0) DESC LIMIT 10");
    const fishing = query("fishing", "fish_profiles", "SELECT username, total_catches, fishing_level AS level FROM fish_profiles ORDER BY COALESCE(CAST(total_catches AS REAL),0) DESC LIMIT 10");
    const radio = query("radio", "radio_user_stats", "SELECT username, requests_count AS requests FROM radio_user_stats ORDER BY COALESCE(CAST(requests_count AS REAL),0) DESC LIMIT 10");
    const leaderboards = { richest: rich, xp, top_xp: xp, level, most_games_won: games, mining_top: mining, fishing_top: fishing, radio_requesters: radio };
    return {
      menu: [],
      leaderboards,
      rich,
      rich_list: rich,
      xp,
      top_xp: xp,
      level,
      most_games_won: games,
      mining,
      miners: mining,
      fishing,
      fishers: fishing,
      radio,
      top_requesters: radio,
      casino: games,
      blackjack: [],
      poker: [],
      events: [],
      reputation: [],
      diagnostics: {
        generated_at: nowIso(),
        db_path: DB_PATH,
        connected_sources: [],
        source_errors: errorMessage ? [{ name: "public_rankings", table: "multiple", error: errorMessage }] : [],
        row_counts: Object.fromEntries(["users", "mining_players", "fish_profiles", "radio_user_stats"].map((table) => [table, rowCountSafe(db, table) ?? 0])),
        missing_tables: [],
        missing_columns: [],
        empty_sources: [],
        sources: {},
      },
      metadata: { generated_at: nowIso(), db_path: DB_PATH, missing_tables: [], missing_columns: [], source_errors: errorMessage ? [{ name: "public_rankings", table: "multiple", error: errorMessage }] : [], row_counts: {} },
    };
  };
  try {
    db = await openDb({ readonly: true });
    const defaultHide = publicRankingsHideStaffBots(db);
    const hideStaff = req.query.hide_staff === undefined ? defaultHide : req.query.hide_staff !== "0";
    const hideBots = req.query.hide_bots === undefined ? defaultHide : req.query.hide_bots !== "0";
    json(res, scrubUserIds(buildLeaderboards(db, { hideStaff, hideBots })));
  } catch (err) {
    json(res, db ? scrubUserIds(minimalRankings(err.message)) : {
      rich: [], xp: [], casino: [], blackjack: [], poker: [], mining: [], fishing: [], events: [], radio: [], reputation: [],
      rich_list: [], miners: [], fishers: [], top_requesters: [],
      metadata: { generated_at: nowIso(), db_path: DB_PATH, missing_tables: [], missing_columns: [], source_errors: [{ name: "public_rankings", table: "multiple", error: err.message }] },
    });
  } finally {
    if (db) try { db.close(); } catch {}
  }
});

app.get("/api/leaderboards", requireAuth, (req, res) => {
  json(res, buildLeaderboards(req.db));
}, closeDb);

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
    voteskip_threshold: { key: "radio_voteskip_threshold", min: 2, max: 25 },
  };
  const radioSchema = {
    music_disc_price_coins: { min: 0, max: 1000000 },
    music_disc_price_luxe: { min: 0, max: 1000000 },
    music_disc_max_purchase_per_command: { min: 1, max: 1000 },
    music_disc_daily_purchase_limit: { min: 0, max: 10000 },
    request_disc_cost_normal: { min: 0, max: 1000 },
    request_disc_cost_vip: { min: 0, max: 1000 },
    request_disc_cost_staff: { min: 0, max: 1000 },
    request_disc_cost_owner: { min: 0, max: 1000 },
    radio_poll_interval_secs: { min: 3, max: 30 },
    radio_request_prequeue_count: { min: 0, max: 3 },
    youtube_search_result_count: { min: 1, max: 5 },
    youtube_search_session_timeout_secs: { min: 30, max: 300 },
    radio_favorites_max_per_user: { min: 1, max: 100 },
    max_song_duration_normal_secs: { min: 0, max: 86400 },
    max_song_duration_vip_secs: { min: 0, max: 86400 },
    max_song_duration_staff_secs: { min: 0, max: 86400 },
    max_song_duration_owner_secs: { min: 0, max: 86400 },
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
  for (const [field, cfg] of Object.entries(radioSchema)) {
    if (!Object.prototype.hasOwnProperty.call(req.body || {}, field)) continue;
    const value = Number(req.body[field]);
    if (!Number.isFinite(value)) return json(res, { error: "invalid_number", field }, 400);
    const clamped = Math.min(cfg.max, Math.max(cfg.min, Math.trunc(value)));
    setRadioSetting(req.db, field, String(clamped), "int", req.user.username);
    updates[field] = String(clamped);
  }
  if (Object.prototype.hasOwnProperty.call(req.body || {}, "music_disc_display_name")) {
    const value = String(req.body.music_disc_display_name || "Song Request 💽").trim().slice(0, 80) || "Song Request 💽";
    setRadioSetting(req.db, "music_disc_display_name", value, "str", req.user.username);
    updates.music_disc_display_name = value;
  }
  if (Object.prototype.hasOwnProperty.call(req.body || {}, "now_footer_text")) {
    const value = String(req.body.now_footer_text || "🎶 !play to request a song").trim().slice(0, 120) || "🎶 !play to request a song";
    setRadioSetting(req.db, "now_footer_text", value, "str", req.user.username);
    updates.now_footer_text = value;
  }
  if (Object.prototype.hasOwnProperty.call(req.body || {}, "now_command_response_mode")) {
    const requested = String(req.body.now_command_response_mode || "whisper").trim().toLowerCase();
    const value = ["whisper", "chat"].includes(requested) ? requested : "whisper";
    setRadioSetting(req.db, "now_command_response_mode", value, "str", req.user.username);
    updates.now_command_response_mode = value;
  }
  for (const field of ["skip_on_leave", "refund_on_leave", "admin_ignore_leave"]) {
    if (!Object.prototype.hasOwnProperty.call(req.body || {}, field)) continue;
    const key = `radio_${field}`;
    const value = req.body[field] ? "true" : "false";
    setRoomSetting(req.db, key, value);
    updates[key] = value;
  }
  for (const field of [
    "music_shop_enabled",
    "music_disc_purchase_coins_enabled",
    "music_disc_purchase_luxe_enabled",
    "radio_enabled",
    "radio_submit_ready_immediately",
    "radio_request_prequeue_enabled",
    "now_announce_song_changes",
    "now_announce_autodj",
    "now_announce_requests",
    "now_show_progress_bar",
    "now_show_likes_dislikes",
    "now_show_request_play_count",
    "youtube_direct_url_enabled",
    "youtube_search_enabled",
    "radio_favorites_enabled",
    "youtube_reject_playlists",
    "youtube_reject_mixes",
    "youtube_reject_livestreams",
    "youtube_reject_shorts",
    "block_requests_when_azura_unhealthy",
  ]) {
    if (!Object.prototype.hasOwnProperty.call(req.body || {}, field)) continue;
    const value = req.body[field] ? "true" : "false";
    setRadioSetting(req.db, field, value, "bool", req.user.username);
    updates[field] = value;
  }
  audit(req.db, req.user.username, "radio_settings_update", "room_settings", "radio", "", updates, req.ip);
  json(res, { ok: true, updates, radio: readLocalRadioStatus(req.db) });
}, closeDb);

app.get("/api/radio/music-discs/lookup", requireAuth, requirePermission("manage_radio"), (req, res) => {
  const result = readMusicDiscIdentity(req.db, req.query.q);
  if (!result) return json(res, { ok: false, error: "Player not found." }, 404);
  json(res, { ok: true, player: result });
}, closeDb);

app.post("/api/radio/music-discs/adjust", requireAuth, requirePermission("manage_radio"), (req, res) => {
  ensureMusicDiscSchema(req.db);
  const user = resolveDashboardUser(req.db, req.body?.username || req.body?.user_id || req.body?.q);
  if (!user) return json(res, { ok: false, error: "Player not found." }, 404);
  const rawAmount = Number(req.body?.amount);
  if (!Number.isFinite(rawAmount) || rawAmount <= 0) return json(res, { ok: false, error: "Amount must be positive." }, 400);
  const amount = Math.min(100000, Math.trunc(rawAmount));
  const mode = String(req.body?.action || "grant").toLowerCase() === "remove" ? "remove" : "grant";
  const reason = String(req.body?.reason || "Admin Music Disc Adjustment 💽").trim().slice(0, 200) || "Admin Music Disc Adjustment 💽";
  db.prepare(`INSERT OR IGNORE INTO music_disc_balances
    (user_id, username, disc_balance, created_at, updated_at)
    VALUES (?, ?, 0, datetime('now'), datetime('now'))`).run(user.user_id, user.username);
  const current = Number(db.prepare("SELECT disc_balance FROM music_disc_balances WHERE user_id=?").get(user.user_id)?.disc_balance || 0);
  const delta = mode === "remove" ? -Math.min(current, amount) : amount;
  const balanceAfter = Math.max(0, current + delta);
  db.prepare(`UPDATE music_disc_balances
    SET username=?, disc_balance=?, updated_at=datetime('now')
    WHERE user_id=?`).run(user.username, balanceAfter, user.user_id);
  db.prepare(`INSERT INTO music_disc_transactions
    (user_id, username, amount, action, reason, balance_after, actor, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))`).run(
      user.user_id,
      user.username,
      delta,
      mode === "remove" ? "admin_remove" : "admin_grant",
      reason,
      balanceAfter,
      req.user.username,
    );
  audit(req.db, req.user.username, "music_disc_adjust", "music_disc_balances", user.username, current, balanceAfter, req.ip);
  json(res, { ok: true, player: readMusicDiscIdentity(req.db, user.username) });
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

app.post("/api/radio/maintenance/cleanup", requireAuth, requireOwner, (req, res) => {
  if (req.body?.confirmation !== "CLEANUP RADIO") {
    return json(res, { error: "confirmation_required", message: "Type CLEANUP RADIO to queue radio cleanup." }, 400);
  }
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
  const rarity = req.query.rarity ? normalizeRarity(req.query.rarity) : "";
  const rows = enrichedMiningOreRows(req.db, true).filter((row) => !rarity || normalizeRarity(row.rarity) === rarity);
  json(res, {
    rows,
    schema_verified: tableExists(req.db, "mining_items"),
    writable: tableExists(req.db, "mining_items"),
    weight_writable: tableExists(req.db, "mining_item_weights"),
    runtime_connected: tableExists(req.db, "mining_item_weights") && tableExists(req.db, "game_rarity_settings"),
    table: "mining_items",
    weight_table: "mining_item_weights",
    columns: [...tableColumns(req.db, "mining_items"), ...(tableExists(req.db, "mining_item_weights") ? ["drop_weight"] : [])],
    rarity_order: MINING_RARITY_ORDER,
  });
}, closeDb);

app.get("/api/mining/rarities", requireAuth, requirePermission("manage_mining"), (req, res) => {
  const rows = raritySummaryRows(enrichedMiningOreRows(req.db, true), MINING_RARITY_ORDER, {
    valueKey: "sell_value",
    weightKey: "drop_weight",
    zeroText: "Not currently dropping",
    source: "game_rarity_settings + mining_items + mining_item_weights",
    system: "mining",
    itemLabel: "enabled mining_items rows with mining_item_weights.drop_weight",
  }, req.db);
  json(res, {
    rows,
    writable: true,
    schema_verified: tableExists(req.db, "game_rarity_settings"),
    rarity_order: MINING_RARITY_ORDER,
    source: "game_rarity_settings + mining_items + mining_item_weights",
    runtime_connected: tableExists(req.db, "game_rarity_settings") && tableExists(req.db, "mining_item_weights"),
    message: "Base rarity weights are connected to !mine. Individual ore weights are edited in the Ores tab.",
  });
}, closeDb);

app.put("/api/mining/rarities/:rarity", requireAuth, requirePermission("manage_mining"), (req, res) => {
  const rarity = normalizeRarity(req.params.rarity);
  if (!MINING_RARITY_ORDER.includes(rarity)) return json(res, { error: "invalid_rarity" }, 400);
  if (!tableExists(req.db, "game_rarity_settings")) return unverifiedSchema(res, "game_rarity_settings table is missing.");
  const before = req.db.prepare("SELECT * FROM game_rarity_settings WHERE system='mining' AND rarity=?").get(rarity) || null;
  const baseWeight = req.body?.base_weight === "" || req.body?.base_weight === undefined ? null : Number(req.body.base_weight);
  const baseChance = req.body?.base_chance === "" || req.body?.base_chance === undefined ? baseWeight : Number(req.body.base_chance);
  if (baseWeight !== null && !Number.isFinite(baseWeight)) return json(res, { error: "base_weight_must_be_number" }, 400);
  if (baseChance !== null && !Number.isFinite(baseChance)) return json(res, { error: "base_chance_must_be_number" }, 400);
  const enabled = req.body?.enabled === false || req.body?.enabled === "0" ? 0 : 1;
  req.db.prepare(`
    INSERT INTO game_rarity_settings (system, rarity, base_weight, base_chance, enabled, updated_at)
    VALUES ('mining', ?, ?, ?, ?, datetime('now'))
    ON CONFLICT(system, rarity) DO UPDATE SET base_weight=excluded.base_weight, base_chance=excluded.base_chance, enabled=excluded.enabled, updated_at=datetime('now')
  `).run(rarity, baseWeight, baseChance, enabled);
  const after = req.db.prepare("SELECT * FROM game_rarity_settings WHERE system='mining' AND rarity=?").get(rarity);
  audit(req.db, req.user.username, "mining_rarity_runtime_update", "game_rarity_settings", `mining:${rarity}`, before, after, req.ip);
  json(res, { ok: true, row: after, runtime_connected: true, message: "Saved. Active !mine reads this rarity weight." });
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
    icon: String(req.body?.icon || req.body?.emoji || "").trim(),
    description: String(req.body?.description || "").trim(),
    rarity: String(req.body?.rarity || "common").trim().toLowerCase(),
    item_type: "ore",
    sell_value: Math.max(0, Math.trunc(Number(req.body?.sell_value || 0))),
    drop_weight: req.body?.drop_weight === undefined || req.body?.drop_weight === "" ? undefined : Math.max(0, Number(req.body.drop_weight)),
    chance_percent: req.body?.chance_percent === undefined || req.body?.chance_percent === "" ? undefined : Math.max(0, Number(req.body.chance_percent)),
    event_only: req.body?.event_only === true || req.body?.event_only === "1" || req.body?.event_only === "on" ? 1 : 0,
    drop_enabled: req.body?.drop_enabled === false || req.body?.drop_enabled === "0" ? 0 : 1,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
  if (row.drop_weight !== undefined && !Number.isFinite(row.drop_weight)) return json(res, { error: "drop_weight_must_be_number" }, 400);
  if (row.chance_percent !== undefined && !Number.isFinite(row.chance_percent)) return json(res, { error: "chance_percent_must_be_number" }, 400);
  const insertCols = Object.keys(row).filter((c) => cols.includes(c));
  try {
    req.db.prepare(`INSERT INTO mining_items (${insertCols.map(sqlIdent).join(", ")}) VALUES (${insertCols.map(() => "?").join(", ")})`).run(...insertCols.map((c) => row[c]));
    if (tableExists(req.db, "mining_item_weights")) {
      req.db.prepare(`
        INSERT INTO mining_item_weights (item_id, drop_weight, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(item_id) DO UPDATE SET drop_weight=excluded.drop_weight, updated_at=datetime('now')
      `).run(itemId, row.drop_weight ?? 1);
    }
    const after = enrichedMiningOreRows(req.db, true).find((ore) => ore.item_id === itemId) || req.db.prepare("SELECT * FROM mining_items WHERE item_id=?").get(itemId);
    audit(req.db, req.user.username, "mining_ore_create", "mining_items", itemId, before, after, req.ip);
    json(res, { ok: true, row: after, runtime_connected: tableExists(req.db, "mining_item_weights") });
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
  const beforeWeight = tableExists(req.db, "mining_item_weights") ? req.db.prepare("SELECT * FROM mining_item_weights WHERE item_id=?").get(id) : null;
  const cols = tableColumns(req.db, "mining_items");
  const allowed = {
    name: req.body?.name,
    emoji: req.body?.emoji,
    icon: req.body?.icon,
    description: req.body?.description,
    rarity: req.body?.rarity,
    sell_value: req.body?.sell_value === undefined ? undefined : Math.max(0, Math.trunc(Number(req.body.sell_value || 0))),
    drop_weight: req.body?.drop_weight === undefined || req.body?.drop_weight === "" ? undefined : Math.max(0, Number(req.body.drop_weight)),
    chance_percent: req.body?.chance_percent === undefined || req.body?.chance_percent === "" ? undefined : Math.max(0, Number(req.body.chance_percent)),
    event_only: req.body?.event_only === undefined ? undefined : (req.body.event_only === true || req.body.event_only === "1" || req.body.event_only === 1 ? 1 : 0),
    drop_enabled: req.body?.drop_enabled === undefined ? undefined : (req.body.drop_enabled === true || req.body.drop_enabled === "1" || req.body.drop_enabled === 1 ? 1 : 0),
    updated_at: new Date().toISOString(),
  };
  if (allowed.drop_weight !== undefined && !Number.isFinite(allowed.drop_weight)) return json(res, { error: "drop_weight_must_be_number" }, 400);
  if (allowed.chance_percent !== undefined && !Number.isFinite(allowed.chance_percent)) return json(res, { error: "chance_percent_must_be_number" }, 400);
  const updates = Object.entries(allowed).filter(([key, value]) => value !== undefined && key !== "drop_weight" && cols.includes(key));
  if (!updates.length && allowed.drop_weight === undefined) return json(res, { error: "no_verified_columns" }, 400);
  if (updates.length) {
    req.db.prepare(`UPDATE mining_items SET ${updates.map(([key]) => `${sqlIdent(key)}=?`).join(", ")} WHERE item_id=?`).run(...updates.map(([, value]) => String(value).trim()), id);
  }
  if (allowed.drop_weight !== undefined) {
    if (!tableExists(req.db, "mining_item_weights")) return unverifiedSchema(res, "mining_item_weights table is missing; cannot update runtime drop weight.");
    req.db.prepare(`
      INSERT INTO mining_item_weights (item_id, drop_weight, updated_at)
      VALUES (?, ?, datetime('now'))
      ON CONFLICT(item_id) DO UPDATE SET drop_weight=excluded.drop_weight, updated_at=datetime('now')
    `).run(id, allowed.drop_weight);
  }
  const after = enrichedMiningOreRows(req.db, true).find((ore) => ore.item_id === id) || req.db.prepare("SELECT * FROM mining_items WHERE item_id=?").get(id);
  const oldValue = { ...before, drop_weight: beforeWeight?.drop_weight ?? null };
  audit(req.db, req.user.username, "mining_ore_update", "mining_items", id, oldValue, after, req.ip);
  json(res, { ok: true, row: after, runtime_connected: tableExists(req.db, "mining_item_weights") });
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
  json(res, {
    rows: calculateMiningDropRows(req.db),
    writable: tableExists(req.db, "mining_item_weights"),
    schema_verified: tableExists(req.db, "mining_item_weights") && tableExists(req.db, "game_rarity_settings"),
    source: "game_rarity_settings + mining_item_weights",
    message: "Active !mine chances are calculated from rarity base weights and per-ore drop weights.",
  });
}, closeDb);
app.put("/api/mining/drop-weights", requireAuth, requirePermission("manage_mining"), (_req, res) => unverifiedSchema(res, "Active mining drop weights are not stored in a verified DB weight table."));

app.get("/api/mining/players", requireAuth, requirePermission("manage_mining"), (req, res) => {
  json(res, { rows: safeTableRows(req.db, "mining_players", { orderBy: "total_mines DESC", limit: "250" }), table: "mining_players" });
}, closeDb);
app.get("/api/mining/inventory", requireAuth, requirePermission("manage_mining"), (req, res) => {
  const clauses = [];
  const params = [];
  if (req.query.username) {
    clauses.push("lower(mi.username)=lower(?)");
    params.push(String(req.query.username).slice(0, 80));
  }
  if (req.query.rarity) {
    clauses.push("lower(it.rarity)=lower(?)");
    params.push(String(req.query.rarity).slice(0, 40));
  }
  if (req.query.q) {
    clauses.push("(lower(mi.item_id) LIKE lower(?) OR lower(COALESCE(it.name,'')) LIKE lower(?))");
    const q = `%${String(req.query.q).slice(0, 80)}%`;
    params.push(q, q);
  }
  const soldTracking = columnExists(req.db, "mining_inventory", "sold");
  if ((req.query.sold === "0" || req.query.sold === "1") && soldTracking) {
    clauses.push("COALESCE(mi.sold,0)=?");
    params.push(Number(req.query.sold));
  }
  const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
  const rows = rowsOrEmpty(req.db, "mining_inventory", `
    SELECT mi.id, ${columnExists(req.db, "mining_inventory", "user_id") ? "mi.user_id," : "NULL AS user_id,"}
      mi.username, mi.item_id, mi.quantity, it.name AS ore_name, it.rarity, it.sell_value AS value,
      (COALESCE(mi.quantity,0) * COALESCE(it.sell_value,0)) AS total_value
      ${soldTracking ? ", mi.sold" : ""}
    FROM mining_inventory mi
    LEFT JOIN mining_items it ON mi.item_id=it.item_id
    ${where}
    ORDER BY mi.username, total_value DESC
    LIMIT 500
  `, ...params);
  json(res, { rows, table: "mining_inventory", sold_tracking: soldTracking });
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
    table_status: {
      fish_catalog: tableExists(req.db, "fish_catalog"),
      game_rarity_settings: tableExists(req.db, "game_rarity_settings"),
      fish_profiles: tableExists(req.db, "fish_profiles"),
      fish_inventory: tableExists(req.db, "fish_inventory"),
      fish_catch_records: tableExists(req.db, "fish_catch_records"),
    },
    raw: {
      fish_catalog: safeTableRows(req.db, "fish_catalog", { orderBy: columnExists(req.db, "fish_catalog", "rarity") ? "rarity, name" : "", limit: "300" }),
      game_rarity_settings: tableExists(req.db, "game_rarity_settings") ? rowsOrEmpty(req.db, "game_rarity_settings", "SELECT * FROM game_rarity_settings WHERE system='fishing' ORDER BY rarity") : [],
      fish_profiles: safeTableRows(req.db, "fish_profiles", { orderBy: columnExists(req.db, "fish_profiles", "total_catches") ? "total_catches DESC" : "", limit: "100" }),
      fish_inventory: safeTableRows(req.db, "fish_inventory", { orderBy: columnExists(req.db, "fish_inventory", "id") ? "id DESC" : "", limit: "100" }),
      fish_catch_records: safeTableRows(req.db, "fish_catch_records", { orderBy: columnExists(req.db, "fish_catch_records", "id") ? "id DESC" : "", limit: "100" }),
    },
  });
}, closeDb);

app.get("/api/fishing/fish", requireAuth, requirePermission("manage_fishing"), (req, res) => {
  const rarity = req.query.rarity ? normalizeRarity(req.query.rarity) : "";
  const { rows, source, error, runtime_connected } = enrichedFishingRows(req.db);
  json(res, {
    rows: rows.filter((row) => !rarity || normalizeRarity(row.rarity) === rarity),
    writable: tableExists(req.db, "fish_catalog"),
    schema_verified: tableExists(req.db, "fish_catalog"),
    runtime_connected,
    source,
    error,
    table: "fish_catalog",
    columns: tableColumns(req.db, "fish_catalog"),
    rarity_order: FISHING_RARITY_ORDER,
    message: runtime_connected ? "Fish catalog is connected to !fish." : "Fish catalog DB table missing; runtime falls back to modules/fishing.py FISH_CATALOG.",
  });
}, closeDb);
app.post("/api/fishing/fish", requireAuth, requirePermission("manage_fishing"), (req, res) => {
  if (!tableExists(req.db, "fish_catalog")) return unverifiedSchema(res, "fish_catalog table is missing.");
  const fishId = String(req.body?.fish_id || "").trim();
  if (!validCatalogId(fishId)) return json(res, { error: "invalid_fish_id" }, 400);
  const row = {
    fish_id: fishId,
    name: String(req.body?.name || fishId).trim(),
    emoji: String(req.body?.emoji || "").trim(),
    rarity: normalizeRarity(req.body?.rarity || "common"),
    base_value: Math.max(0, Math.trunc(Number(req.body?.base_value || 0))),
    min_weight: req.body?.min_weight === "" || req.body?.min_weight === undefined ? null : Number(req.body.min_weight),
    max_weight: req.body?.max_weight === "" || req.body?.max_weight === undefined ? null : Number(req.body.max_weight),
    catch_weight: req.body?.catch_weight === "" || req.body?.catch_weight === undefined ? 1 : Math.max(0, Number(req.body.catch_weight)),
    catch_enabled: req.body?.catch_enabled === false || req.body?.catch_enabled === "0" ? 0 : 1,
    event_only: req.body?.event_only === true || req.body?.event_only === "1" || req.body?.event_only === "on" ? 1 : 0,
  };
  if (![row.min_weight, row.max_weight, row.catch_weight].every((v) => v === null || Number.isFinite(v))) return json(res, { error: "weights_must_be_numbers" }, 400);
  req.db.prepare(`
    INSERT INTO fish_catalog (fish_id, name, emoji, rarity, base_value, min_weight, max_weight, catch_weight, catch_enabled, event_only, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
  `).run(row.fish_id, row.name, row.emoji, row.rarity, row.base_value, row.min_weight, row.max_weight, row.catch_weight, row.catch_enabled, row.event_only);
  const after = enrichedFishingRows(req.db).rows.find((fish) => fish.fish_id === fishId);
  audit(req.db, req.user.username, "fishing_fish_create", "fish_catalog", fishId, null, after, req.ip);
  json(res, { ok: true, row: after, runtime_connected: true });
}, closeDb);
app.put("/api/fishing/fish/:id", requireAuth, requirePermission("manage_fishing"), (req, res) => {
  if (!tableExists(req.db, "fish_catalog")) return unverifiedSchema(res, "fish_catalog table is missing.");
  const id = String(req.params.id || "").trim();
  if (!validCatalogId(id)) return json(res, { error: "invalid_fish_id" }, 400);
  const before = req.db.prepare("SELECT * FROM fish_catalog WHERE fish_id=?").get(id);
  if (!before) return json(res, { error: "fish_not_found" }, 404);
  const allowed = {
    name: req.body?.name,
    emoji: req.body?.emoji,
    rarity: req.body?.rarity === undefined ? undefined : normalizeRarity(req.body.rarity),
    base_value: req.body?.base_value === undefined ? undefined : Math.max(0, Math.trunc(Number(req.body.base_value || 0))),
    min_weight: req.body?.min_weight === undefined || req.body?.min_weight === "" ? undefined : Number(req.body.min_weight),
    max_weight: req.body?.max_weight === undefined || req.body?.max_weight === "" ? undefined : Number(req.body.max_weight),
    catch_weight: req.body?.catch_weight === undefined || req.body?.catch_weight === "" ? undefined : Math.max(0, Number(req.body.catch_weight)),
    catch_enabled: req.body?.catch_enabled === undefined ? undefined : (req.body.catch_enabled === true || req.body.catch_enabled === "1" || req.body.catch_enabled === 1 ? 1 : 0),
    event_only: req.body?.event_only === undefined ? undefined : (req.body.event_only === true || req.body.event_only === "1" || req.body.event_only === 1 ? 1 : 0),
    updated_at: new Date().toISOString(),
  };
  if ([allowed.min_weight, allowed.max_weight, allowed.catch_weight].some((v) => v !== undefined && !Number.isFinite(v))) return json(res, { error: "weights_must_be_numbers" }, 400);
  const updates = Object.entries(allowed).filter(([, value]) => value !== undefined);
  if (!updates.length) return json(res, { error: "no_verified_columns" }, 400);
  req.db.prepare(`UPDATE fish_catalog SET ${updates.map(([key]) => `${sqlIdent(key)}=?`).join(", ")} WHERE fish_id=?`).run(...updates.map(([, value]) => String(value).trim()), id);
  const after = enrichedFishingRows(req.db).rows.find((fish) => fish.fish_id === id);
  audit(req.db, req.user.username, "fishing_fish_update", "fish_catalog", id, before, after, req.ip);
  json(res, { ok: true, row: after, runtime_connected: true });
}, closeDb);
app.delete("/api/fishing/fish/:id", requireAuth, requirePermission("manage_fishing"), (req, res) => {
  if (!tableExists(req.db, "fish_catalog")) return unverifiedSchema(res, "fish_catalog table is missing.");
  const id = String(req.params.id || "").trim();
  const before = req.db.prepare("SELECT * FROM fish_catalog WHERE fish_id=?").get(id);
  if (!before) return json(res, { error: "fish_not_found" }, 404);
  const hard = req.body?.hard === true || req.query.hard === "1";
  if (!hard) {
    req.db.prepare("UPDATE fish_catalog SET catch_enabled=0, updated_at=datetime('now') WHERE fish_id=?").run(id);
    audit(req.db, req.user.username, "fishing_fish_disable", "fish_catalog", id, before, { ...before, catch_enabled: 0 }, req.ip);
    return json(res, { ok: true, mode: "soft_disable" });
  }
  if (req.user.role !== "owner") return json(res, { error: "owner_required" }, 403);
  if (String(req.body?.confirmation || "") !== "DELETE FISH") return json(res, { error: "typed_confirmation_required" }, 400);
  req.db.prepare("DELETE FROM fish_catalog WHERE fish_id=?").run(id);
  audit(req.db, req.user.username, "fishing_fish_hard_delete", "fish_catalog", id, before, { hard_delete: true }, req.ip);
  json(res, { ok: true, mode: "hard_delete" });
}, closeDb);

app.get("/api/fishing/rarities", requireAuth, requirePermission("manage_fishing"), (_req, res) => {
  const rows = raritySummaryRows(enrichedFishingRows(_req.db).rows, FISHING_RARITY_ORDER, {
    valueKey: "base_value",
    weightKey: "catch_weight",
    zeroText: "Not currently catching",
    source: "fish_catalog + game_rarity_settings",
    system: "fishing",
    itemLabel: "enabled fish_catalog rows with catch_weight",
  }, _req.db);
  json(res, {
    rows,
    writable: true,
    schema_verified: tableExists(_req.db, "game_rarity_settings") && tableExists(_req.db, "fish_catalog"),
    rarity_order: FISHING_RARITY_ORDER,
    source: "game_rarity_settings + fish_catalog",
    runtime_connected: tableExists(_req.db, "game_rarity_settings") && tableExists(_req.db, "fish_catalog"),
    message: "Base rarity weights are connected to !fish. Individual fish weights are edited in Fish Catalog.",
  });
}, closeDb);

app.put("/api/fishing/rarities/:rarity", requireAuth, requirePermission("manage_fishing"), (req, res) => {
  const rarity = normalizeRarity(req.params.rarity);
  if (!FISHING_RARITY_ORDER.includes(rarity)) return json(res, { error: "invalid_rarity" }, 400);
  if (!tableExists(req.db, "game_rarity_settings")) return unverifiedSchema(res, "game_rarity_settings table is missing.");
  const before = req.db.prepare("SELECT * FROM game_rarity_settings WHERE system='fishing' AND rarity=?").get(rarity) || null;
  const baseWeight = req.body?.base_weight === "" || req.body?.base_weight === undefined ? null : Number(req.body.base_weight);
  const baseChance = req.body?.base_chance === "" || req.body?.base_chance === undefined ? baseWeight : Number(req.body.base_chance);
  if (baseWeight !== null && !Number.isFinite(baseWeight)) return json(res, { error: "base_weight_must_be_number" }, 400);
  if (baseChance !== null && !Number.isFinite(baseChance)) return json(res, { error: "base_chance_must_be_number" }, 400);
  const enabled = req.body?.enabled === false || req.body?.enabled === "0" ? 0 : 1;
  req.db.prepare(`
    INSERT INTO game_rarity_settings (system, rarity, base_weight, base_chance, enabled, updated_at)
    VALUES ('fishing', ?, ?, ?, ?, datetime('now'))
    ON CONFLICT(system, rarity) DO UPDATE SET base_weight=excluded.base_weight, base_chance=excluded.base_chance, enabled=excluded.enabled, updated_at=datetime('now')
  `).run(rarity, baseWeight, baseChance, enabled);
  const after = req.db.prepare("SELECT * FROM game_rarity_settings WHERE system='fishing' AND rarity=?").get(rarity);
  audit(req.db, req.user.username, "fishing_rarity_runtime_update", "game_rarity_settings", `fishing:${rarity}`, before, after, req.ip);
  json(res, { ok: true, row: after, runtime_connected: true, message: "Saved. Active !fish reads this rarity weight." });
}, closeDb);

app.get("/api/fishing/rods", requireAuth, requirePermission("manage_fishing"), (_req, res) => {
  const { rods, source, error } = readFishingCodeCatalog();
  json(res, { rows: rods, writable: false, schema_verified: false, source, error, message: "Active rod catalog is defined in modules/fishing.py FISHING_RODS; dashboard keeps it read-only." });
});
app.post("/api/fishing/rods", requireAuth, requirePermission("manage_fishing"), (_req, res) => unverifiedSchema(res, "Rod catalog is a runtime code catalog, not a verified DB table."));
app.put("/api/fishing/rods/:id", requireAuth, requirePermission("manage_fishing"), (_req, res) => unverifiedSchema(res, "Rod catalog is a runtime code catalog, not a verified DB table."));
app.delete("/api/fishing/rods/:id", requireAuth, requirePermission("manage_fishing"), (_req, res) => unverifiedSchema(res, "Rod catalog is a runtime code catalog, not a verified DB table."));

app.get("/api/fishing/drop-weights", requireAuth, requirePermission("manage_fishing"), (req, res) => {
  json(res, { rows: calculateFishDropRowsFromDb(req.db), writable: tableExists(req.db, "fish_catalog"), schema_verified: tableExists(req.db, "fish_catalog") && tableExists(req.db, "game_rarity_settings"), source: "game_rarity_settings + fish_catalog.catch_weight", message: "Active !fish chances are calculated from rarity base weights and per-fish catch weights." });
}, closeDb);
app.put("/api/fishing/drop-weights", requireAuth, requirePermission("manage_fishing"), (_req, res) => unverifiedSchema(res, "Use PUT /api/fishing/fish/:id to update fish_catalog.catch_weight."));
app.get("/api/fishing/players", requireAuth, requirePermission("manage_fishing"), (req, res) => {
  const rows = tableExists(req.db, "fish_auto_sell_settings")
    ? rowsOrEmpty(req.db, "fish_profiles", `SELECT fp.*, fas.auto_sell_enabled, fas.auto_sell_rare_enabled FROM fish_profiles fp LEFT JOIN fish_auto_sell_settings fas ON fp.user_id=fas.user_id ORDER BY fp.total_catches DESC LIMIT 250`)
    : safeTableRows(req.db, "fish_profiles", { orderBy: "total_catches DESC", limit: "250" });
  json(res, { rows, table: "fish_profiles" });
}, closeDb);
app.get("/api/fishing/inventory", requireAuth, requirePermission("manage_fishing"), (req, res) => {
  if (!tableExists(req.db, "fish_inventory")) return json(res, { rows: [], table: "fish_inventory", missing_table: true });
  const clauses = [];
  const params = [];
  if (req.query.username && columnExists(req.db, "fish_inventory", "username")) {
    clauses.push("lower(username)=lower(?)");
    params.push(String(req.query.username).slice(0, 80));
  }
  if ((req.query.sold === "0" || req.query.sold === "1") && columnExists(req.db, "fish_inventory", "sold")) {
    clauses.push("COALESCE(sold,0)=?");
    params.push(Number(req.query.sold));
  }
  if (req.query.rarity && columnExists(req.db, "fish_inventory", "rarity")) {
    clauses.push("lower(rarity)=lower(?)");
    params.push(String(req.query.rarity).slice(0, 40));
  }
  if (req.query.q && columnExists(req.db, "fish_inventory", "fish_name")) {
    clauses.push("lower(fish_name) LIKE lower(?)");
    params.push(`%${String(req.query.q).slice(0, 80)}%`);
  }
  const rows = safeRows(req.db, "fish_inventory", ["id","user_id","username","fish_id","fish_name","rarity","weight","value","sold","sold_at","caught_at","created_at"], {
    where: clauses.join(" AND "),
    params,
    orderBy: columnExists(req.db, "fish_inventory", "id") ? "id DESC" : "",
    limit: "500",
  });
  json(res, { rows, table: "fish_inventory", sold_tracking: columnExists(req.db, "fish_inventory", "sold") });
}, closeDb);
app.get("/api/fishing/logs", requireAuth, requireAnyPermission("manage_fishing", "view_logs"), (req, res) => {
  json(res, {
    fish_catch_records: safeTableRows(req.db, "fish_catch_records", { orderBy: "id DESC", limit: "200" }),
    forced_fishing_drops: safeTableRows(req.db, "forced_fishing_drops", { orderBy: "id DESC", limit: "100" }),
    fish_auto_sell_settings: safeTableRows(req.db, "fish_auto_sell_settings", { orderBy: "updated_at DESC", limit: "100" }),
  });
}, closeDb);

app.get("/api/rewards", requireAuth, requireAnyPermission("manage_rewards", "manage_inventory", "manage_economy", "manage_players", "view_logs"), (req, res) => {
  json(res, readRewardsDashboard(req.db));
}, closeDb);

app.get("/api/commerce/overview", requireAuth, requireAnyPermission("manage_rewards", "manage_inventory", "manage_economy", "manage_players", "view_logs"), (req, res) => {
  json(res, readCommerceDashboard(req.db));
}, closeDb);

app.get("/api/commerce/source-map", requireAuth, requireAnyPermission("manage_rewards", "manage_inventory", "manage_economy", "view_logs"), (req, res) => {
  json(res, { rows: commerceSourceMap(req.db), generated_at: new Date().toISOString() });
}, closeDb);

app.get("/api/badge-shop", requireAuth, requireAnyPermission("manage_rewards", "manage_inventory", "manage_players"), (req, res) => {
  const data = readCommerceDashboard(req.db);
  json(res, {
    ...data.badge_shop,
    owned: data.badges?.owned || readRewardsDashboard(req.db).badges.owned,
    claims: data.achievements.badge_claims,
    source_map: data.source_map.filter((row) => row.system.includes("Badge")),
  });
}, closeDb);

app.put("/api/badge-shop/:badge_id", requireAuth, requireAnyPermission("manage_rewards", "manage_inventory"), (req, res) => {
  if (req.user?.role !== "owner" && !req.permissions?.manage_rewards) return json(res, { error: "forbidden", permission: "manage_rewards" }, 403);
  if (!tableExists(req.db, "emoji_badges")) return unverifiedSchema(res, "emoji_badges table is required before badge shop catalog writes are safe.");
  const cols = tableColumns(req.db, "emoji_badges");
  if (!cols.includes("badge_id")) return unverifiedSchema(res, "emoji_badges.badge_id is required before badge shop writes are safe.");
  const badgeId = String(req.params.badge_id || "").trim();
  const reason = String(req.body?.reason || "").trim();
  if (!validCatalogId(badgeId)) return json(res, { error: "bad_badge_id" }, 400);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const allowed = ["emoji", "name", "rarity", "price", "purchasable", "tradeable", "sellable", "source"];
  const updateCols = allowed.filter((col) => cols.includes(col) && req.body?.[col] !== undefined);
  if (!updateCols.length) return json(res, { error: "no_verified_columns" }, 400);
  const before = safeOne(req.db, "emoji_badges", cols, { where: "badge_id=?", params: [badgeId] });
  if (!before) {
    const insertCols = ["badge_id", ...updateCols];
    req.db.prepare(`INSERT INTO emoji_badges (${insertCols.map(sqlIdent).join(",")}) VALUES (${insertCols.map(() => "?").join(",")})`).run(badgeId, ...updateCols.map((col) => String(req.body[col] ?? "")));
  } else {
    req.db.prepare(`UPDATE emoji_badges SET ${updateCols.map((col) => `${sqlIdent(col)}=?`).join(", ")} WHERE badge_id=?`).run(...updateCols.map((col) => String(req.body[col] ?? "")), badgeId);
  }
  const after = safeOne(req.db, "emoji_badges", cols, { where: "badge_id=?", params: [badgeId] });
  audit(req.db, req.user.username, before ? "badge_shop_update" : "badge_shop_create", "emoji_badges", badgeId, before, { row: after, reason }, req.ip);
  json(res, { ok: true, row: after });
}, closeDb);

app.get("/api/badge-market", requireAuth, requireAnyPermission("manage_rewards", "manage_inventory", "view_logs"), (req, res) => {
  json(res, readCommerceDashboard(req.db).badge_market);
}, closeDb);

app.put("/api/badge-market/settings", requireAuth, requireAnyPermission("manage_rewards", "manage_inventory"), (req, res) => {
  if (req.user?.role !== "owner" && !req.permissions?.manage_rewards) return json(res, { error: "forbidden", permission: "manage_rewards" }, 403);
  const reason = String(req.body?.reason || "").trim();
  const fee = Number(req.body?.badge_market_fee_percent);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  if (!Number.isFinite(fee) || fee < 0 || fee > 50) return json(res, { error: "bad_fee_percent" }, 400);
  const before = readKeyValueMap(req.db, "bot_settings").badge_market_fee_percent ?? "5";
  writeKeyValue(req.db, "bot_settings", "badge_market_fee_percent", String(fee));
  audit(req.db, req.user.username, "badge_market_fee_update", "bot_settings", "badge_market_fee_percent", before, { value: fee, reason }, req.ip);
  json(res, { ok: true, badge_market_fee_percent: String(fee) });
}, closeDb);

app.post("/api/badge-market/listings/:id/cancel", requireAuth, requireAnyPermission("manage_rewards", "manage_inventory"), (req, res) => {
  if (req.user?.role !== "owner" && !req.permissions?.manage_rewards) return json(res, { error: "forbidden", permission: "manage_rewards" }, 403);
  if (!tableExists(req.db, "badge_market_listings")) return json(res, { error: "badge_market_listings_missing" }, 404);
  const cols = tableColumns(req.db, "badge_market_listings");
  if (!cols.includes("id") || !cols.includes("status")) return unverifiedSchema(res, "badge_market_listings requires id and status for safe cancellation.");
  const id = Number(req.params.id);
  const reason = String(req.body?.reason || "").trim();
  if (!Number.isInteger(id) || id <= 0) return json(res, { error: "bad_listing_id" }, 400);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const before = safeOne(req.db, "badge_market_listings", cols, { where: "id=?", params: [id] });
  const info = req.db.prepare("UPDATE badge_market_listings SET status='cancelled' WHERE id=? AND lower(COALESCE(status,''))='active'").run(id);
  const after = safeOne(req.db, "badge_market_listings", cols, { where: "id=?", params: [id] });
  audit(req.db, req.user.username, "badge_market_listing_cancel", "badge_market_listings", id, before, { row: after, changed: info.changes, reason }, req.ip);
  json(res, { ok: true, changed: info.changes, row: after });
}, closeDb);

app.get("/api/badge-market/logs", requireAuth, requireAnyPermission("manage_rewards", "view_logs"), (req, res) => {
  const d = readCommerceDashboard(req.db).badge_market;
  json(res, { logs: d.logs, trades: d.trades, wishlist: d.wishlist });
}, closeDb);

app.get("/api/luxe", requireAuth, requireAnyPermission("manage_rewards", "manage_inventory", "manage_economy", "manage_players"), (req, res) => {
  json(res, readCommerceDashboard(req.db).luxe);
}, closeDb);

app.get("/api/luxe/shop", requireAuth, requireAnyPermission("manage_rewards", "manage_inventory", "manage_economy"), (req, res) => {
  json(res, readLuxeShop(req.db));
}, closeDb);

app.put("/api/luxe/shop/:item_key", requireAuth, requireAnyPermission("manage_rewards", "manage_economy"), (req, res) => {
  if (req.user?.role !== "owner" && !req.permissions?.manage_rewards) return json(res, { error: "forbidden", permission: "manage_rewards" }, 403);
  if (!tableExists(req.db, "premium_settings")) return unverifiedSchema(res, "premium_settings is required for Luxe shop price/duration writes.");
  const item = LUXE_SHOP_ITEMS.find((row) => row.item_key === String(req.params.item_key || ""));
  const reason = String(req.body?.reason || "").trim();
  if (!item) return json(res, { error: "unknown_luxe_item" }, 404);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const before = readLuxeShop(req.db).rows.find((row) => row.item_key === item.item_key);
  const price = req.body?.price === undefined || req.body.price === "" ? null : Number(req.body.price);
  const duration = req.body?.duration_seconds === undefined || req.body.duration_seconds === "" ? null : Number(req.body.duration_seconds);
  if (price != null && (!Number.isFinite(price) || price < 0)) return json(res, { error: "bad_price" }, 400);
  if (duration != null && (!Number.isFinite(duration) || duration < 0)) return json(res, { error: "bad_duration_seconds" }, 400);
  if (price != null) writeKeyValue(req.db, "premium_settings", `price_${item.item_key}`, String(Math.round(price)));
  if (duration != null) writeKeyValue(req.db, "premium_settings", `duration_${item.item_key}`, String(Math.round(duration)));
  const after = readLuxeShop(req.db).rows.find((row) => row.item_key === item.item_key);
  audit(req.db, req.user.username, "luxe_shop_update", "premium_settings", item.item_key, before, { row: after, reason }, req.ip);
  json(res, { ok: true, row: after });
}, closeDb);

app.get("/api/luxe/balances", requireAuth, requireAnyPermission("manage_rewards", "manage_inventory", "manage_economy", "manage_players"), (req, res) => {
  json(res, { rows: safeTableRows(req.db, "premium_balances", { orderBy: columnExists(req.db, "premium_balances", "luxe_tickets") ? "luxe_tickets DESC" : "", limit: "1000" }) });
}, closeDb);

app.post("/api/player/:id/luxe", requireAuth, requireAnyPermission("manage_rewards", "manage_inventory", "manage_economy"), (req, res) => {
  if (req.user?.role !== "owner" && !req.permissions?.manage_rewards) return json(res, { error: "forbidden", permission: "manage_rewards" }, 403);
  const player = readPlayerProfile(req.db, req.params.id);
  const amount = Number(req.body?.amount);
  const reason = String(req.body?.reason || "").trim();
  if (!player) return json(res, { error: "not_found" }, 404);
  if (!tableExists(req.db, "premium_balances") || !tableExists(req.db, "premium_transactions")) return unverifiedSchema(res, "premium_balances and premium_transactions are required for Luxe ticket grants.");
  if (!Number.isFinite(amount) || amount === 0) return json(res, { error: "bad_amount" }, 400);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const before = safeOne(req.db, "premium_balances", ["user_id", "username", "luxe_tickets", "updated_at"], { where: "user_id=?", params: [player.user_id] });
  const current = Number(before?.luxe_tickets || 0);
  const next = Math.max(0, current + Math.trunc(amount));
  req.db.prepare("INSERT INTO premium_balances (user_id, username, luxe_tickets, updated_at) VALUES (?, ?, ?, datetime('now')) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, luxe_tickets=excluded.luxe_tickets, updated_at=datetime('now')").run(player.user_id, player.username, next);
  req.db.prepare("INSERT INTO premium_transactions (user_id, username, type, amount, currency, details) VALUES (?, ?, ?, ?, 'luxe', ?)").run(player.user_id, player.username, amount > 0 ? "dashboard_grant" : "dashboard_remove", Math.abs(Math.trunc(amount)), reason);
  const after = safeOne(req.db, "premium_balances", ["user_id", "username", "luxe_tickets", "updated_at"], { where: "user_id=?", params: [player.user_id] });
  audit(req.db, req.user.username, amount > 0 ? "luxe_ticket_grant" : "luxe_ticket_remove", "premium_balances", player.user_id, before, { row: after, amount, reason }, req.ip);
  json(res, { ok: true, row: after, player: readPlayerProfile(req.db, player.user_id) });
}, closeDb);

app.get("/api/luxe/transactions", requireAuth, requireAnyPermission("manage_rewards", "manage_economy", "view_logs"), (req, res) => {
  const d = readCommerceDashboard(req.db).luxe;
  json(res, { transactions: d.transactions, ticket_logs: d.ticket_logs, conversion_logs: d.conversion_logs });
}, closeDb);

app.put("/api/luxe/settings", requireAuth, requireAnyPermission("manage_rewards", "manage_economy"), (req, res) => {
  if (req.user?.role !== "owner" && !req.permissions?.manage_rewards) return json(res, { error: "forbidden", permission: "manage_rewards" }, 403);
  if (!tableExists(req.db, "premium_settings")) return unverifiedSchema(res, "premium_settings is required for Luxe settings writes.");
  const reason = String(req.body?.reason || "").trim();
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const allowed = ["luxe_rate", "vip_duration_days", "coinpack_small_tickets", "coinpack_small_coins", "coinpack_medium_tickets", "coinpack_medium_coins", "coinpack_large_tickets", "coinpack_large_coins"];
  const before = readLuxeSettings(req.db);
  const updates = {};
  for (const key of allowed) {
    if (req.body?.[key] === undefined || req.body[key] === "") continue;
    updates[key] = String(req.body[key]);
    writeKeyValue(req.db, "premium_settings", key, updates[key]);
  }
  if (!Object.keys(updates).length) return json(res, { error: "no_supported_settings" }, 400);
  audit(req.db, req.user.username, "luxe_settings_update", "premium_settings", "luxe", before, { updates, reason }, req.ip);
  json(res, { ok: true, settings: readLuxeSettings(req.db) });
}, closeDb);

app.get("/api/vip", requireAuth, requireAnyPermission("manage_rewards", "manage_inventory", "manage_economy", "manage_players"), (req, res) => {
  json(res, { vip_players: vipRows(req.db), source: "owned_items.item_id=vip", table_status: { owned_items: tableExists(req.db, "owned_items") } });
}, closeDb);

app.post("/api/vip/add", requireAuth, requireAnyPermission("manage_rewards", "manage_inventory"), (req, res) => {
  if (req.user?.role !== "owner" && !req.permissions?.manage_inventory) return json(res, { error: "forbidden", permission: "manage_inventory" }, 403);
  if (!tableExists(req.db, "owned_items")) return json(res, { error: "owned_items_missing" }, 404);
  if (!["user_id", "item_id", "item_type"].every((col) => columnExists(req.db, "owned_items", col))) return unverifiedSchema(res, "owned_items requires user_id, item_id, and item_type for VIP writes.");
  const player = playerByInput(req.db, req.body);
  const reason = String(req.body?.reason || "").trim();
  if (!player) return json(res, { error: "player_not_found" }, 404);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const before = safeOne(req.db, "owned_items", ["user_id","item_id","item_type"], { where: "user_id=? AND lower(item_id)='vip'", params: [player.user_id] });
  req.db.prepare("INSERT OR IGNORE INTO owned_items (user_id, item_id, item_type) VALUES (?, 'vip', 'vip')").run(player.user_id);
  audit(req.db, req.user.username, "vip_add", "owned_items", player.user_id, before, { username: player.username, reason }, req.ip);
  json(res, { ok: true, player: readPlayerProfile(req.db, player.user_id), vip_players: vipRows(req.db) });
}, closeDb);

app.post("/api/vip/remove", requireAuth, requireAnyPermission("manage_rewards", "manage_inventory"), (req, res) => {
  if (req.user?.role !== "owner" && !req.permissions?.manage_inventory) return json(res, { error: "forbidden", permission: "manage_inventory" }, 403);
  if (!tableExists(req.db, "owned_items")) return json(res, { error: "owned_items_missing" }, 404);
  if (!["user_id", "item_id"].every((col) => columnExists(req.db, "owned_items", col))) return unverifiedSchema(res, "owned_items requires user_id and item_id for VIP writes.");
  const player = playerByInput(req.db, req.body);
  const reason = String(req.body?.reason || "").trim();
  if (!player) return json(res, { error: "player_not_found" }, 404);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const before = safeOne(req.db, "owned_items", ["user_id","item_id","item_type"], { where: "user_id=? AND lower(item_id)='vip'", params: [player.user_id] });
  const info = req.db.prepare("DELETE FROM owned_items WHERE user_id=? AND lower(item_id)='vip'").run(player.user_id);
  audit(req.db, req.user.username, "vip_remove", "owned_items", player.user_id, before, { username: player.username, removed: info.changes, reason }, req.ip);
  json(res, { ok: true, removed: info.changes, player: readPlayerProfile(req.db, player.user_id), vip_players: vipRows(req.db) });
}, closeDb);

app.get("/api/titles", requireAuth, requireAnyPermission("manage_players", "manage_inventory", "manage_rewards"), (req, res) => {
  const rewards = readRewardsDashboard(req.db);
  json(res, { ...rewards.titles, table_status: rewards.table_status, columns: rewards.columns });
}, closeDb);

app.get("/api/titles/catalog", requireAuth, requireAnyPermission("manage_rewards", "manage_inventory", "manage_players"), (req, res) => {
  json(res, { rows: safeTableRows(req.db, "title_catalog", { orderBy: columnExists(req.db, "title_catalog", "tier") ? "tier, title_id" : "", limit: "500" }), table: "title_catalog", columns: tableExists(req.db, "title_catalog") ? tableColumns(req.db, "title_catalog") : [] });
}, closeDb);

app.post("/api/titles/catalog", requireAuth, requireOwner, (req, res) => {
  if (!tableExists(req.db, "title_catalog")) return json(res, { error: "title_catalog_missing" }, 404);
  const cols = tableColumns(req.db, "title_catalog");
  if (!cols.includes("title_id")) return unverifiedSchema(res, "title_catalog.title_id is required before dashboard writes are safe.");
  const titleId = String(req.body?.title_id || "").trim();
  const reason = String(req.body?.reason || "").trim();
  if (!validCatalogId(titleId)) return json(res, { error: "bad_title_id" }, 400);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const allowed = ["title_id", "display", "display_name", "name", "description", "tier", "rarity", "color", "price", "buyable", "active", "enabled", "archived", "category", "perks_json"];
  const insertCols = allowed.filter((col) => cols.includes(col) && (col === "title_id" || req.body?.[col] !== undefined));
  const values = insertCols.map((col) => col === "title_id" ? titleId : String(req.body[col] ?? ""));
  req.db.prepare(`INSERT OR IGNORE INTO title_catalog (${insertCols.map(sqlIdent).join(",")}) VALUES (${insertCols.map(() => "?").join(",")})`).run(...values);
  audit(req.db, req.user.username, "title_catalog_create", "title_catalog", titleId, "", { title_id: titleId, reason }, req.ip);
  json(res, { ok: true, rows: safeTableRows(req.db, "title_catalog", { limit: "500" }) });
}, closeDb);

app.put("/api/titles/catalog/:id", requireAuth, requireOwner, (req, res) => {
  if (!tableExists(req.db, "title_catalog")) return json(res, { error: "title_catalog_missing" }, 404);
  const cols = tableColumns(req.db, "title_catalog");
  if (!cols.includes("title_id")) return unverifiedSchema(res, "title_catalog.title_id is required before dashboard writes are safe.");
  const titleId = String(req.params.id || "").trim();
  const reason = String(req.body?.reason || "").trim();
  if (!validCatalogId(titleId)) return json(res, { error: "bad_title_id" }, 400);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const before = safeOne(req.db, "title_catalog", cols, { where: "title_id=?", params: [titleId] });
  const allowed = ["display", "display_name", "name", "description", "tier", "rarity", "color", "price", "buyable", "active", "enabled", "archived", "category", "perks_json"];
  const updateCols = allowed.filter((col) => cols.includes(col) && req.body?.[col] !== undefined);
  if (!updateCols.length) return json(res, { error: "no_verified_columns" }, 400);
  req.db.prepare(`UPDATE title_catalog SET ${updateCols.map((col) => `${sqlIdent(col)}=?`).join(", ")} WHERE title_id=?`).run(...updateCols.map((col) => String(req.body[col] ?? "")), titleId);
  audit(req.db, req.user.username, "title_catalog_update", "title_catalog", titleId, before, { updates: Object.fromEntries(updateCols.map((col) => [col, req.body[col]])), reason }, req.ip);
  json(res, { ok: true, row: safeOne(req.db, "title_catalog", cols, { where: "title_id=?", params: [titleId] }) });
}, closeDb);

app.get("/api/badges/catalog", requireAuth, requireAnyPermission("manage_rewards", "manage_inventory", "manage_players"), (req, res) => {
  const rewards = readRewardsDashboard(req.db);
  const catalog = safeTableRows(req.db, "emoji_badges", { orderBy: columnExists(req.db, "emoji_badges", "rarity") ? "rarity, badge_id" : "", limit: "1000" });
  json(res, {
    ...rewards.badges,
    catalog,
    table_status: { ...rewards.table_status, emoji_badges: tableExists(req.db, "emoji_badges") },
    columns: { ...rewards.columns, emoji_badges: tableExists(req.db, "emoji_badges") ? tableColumns(req.db, "emoji_badges") : [] },
    writable: tableExists(req.db, "emoji_badges"),
    source: tableExists(req.db, "emoji_badges") ? "emoji_badges" : "modules/shop.py BADGES runtime constant",
    message: tableExists(req.db, "emoji_badges") ? "Badge catalog is DB-backed." : "No verified badge catalog table found; ownership and market tables remain visible.",
  });
}, closeDb);

app.post("/api/badges/catalog", requireAuth, requireOwner, (req, res) => {
  if (!tableExists(req.db, "emoji_badges")) return unverifiedSchema(res, "emoji_badges table is required before badge catalog writes are safe.");
  const cols = tableColumns(req.db, "emoji_badges");
  const badgeId = String(req.body?.badge_id || "").trim();
  const reason = String(req.body?.reason || "").trim();
  if (!validCatalogId(badgeId)) return json(res, { error: "bad_badge_id" }, 400);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const allowed = ["badge_id", "emoji", "name", "rarity", "price", "purchasable", "tradeable", "sellable", "source"];
  const insertCols = allowed.filter((col) => cols.includes(col) && (col === "badge_id" || req.body?.[col] !== undefined));
  if (!insertCols.includes("badge_id")) return unverifiedSchema(res, "emoji_badges.badge_id is required before badge catalog writes are safe.");
  req.db.prepare(`INSERT OR IGNORE INTO emoji_badges (${insertCols.map(sqlIdent).join(",")}) VALUES (${insertCols.map(() => "?").join(",")})`).run(...insertCols.map((col) => col === "badge_id" ? badgeId : String(req.body[col] ?? "")));
  audit(req.db, req.user.username, "badge_catalog_create", "emoji_badges", badgeId, "", { badge_id: badgeId, reason }, req.ip);
  json(res, { ok: true, rows: safeTableRows(req.db, "emoji_badges", { limit: "1000" }) });
}, closeDb);
app.put("/api/badges/catalog/:id", requireAuth, requireOwner, (req, res) => {
  if (!tableExists(req.db, "emoji_badges")) return unverifiedSchema(res, "emoji_badges table is required before badge catalog writes are safe.");
  const cols = tableColumns(req.db, "emoji_badges");
  if (!cols.includes("badge_id")) return unverifiedSchema(res, "emoji_badges.badge_id is required before badge catalog writes are safe.");
  const badgeId = String(req.params.id || "").trim();
  const reason = String(req.body?.reason || "").trim();
  if (!validCatalogId(badgeId)) return json(res, { error: "bad_badge_id" }, 400);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const allowed = ["emoji", "name", "rarity", "price", "purchasable", "tradeable", "sellable", "source"];
  const updateCols = allowed.filter((col) => cols.includes(col) && req.body?.[col] !== undefined);
  if (!updateCols.length) return json(res, { error: "no_verified_columns" }, 400);
  const before = safeOne(req.db, "emoji_badges", cols, { where: "badge_id=?", params: [badgeId] });
  req.db.prepare(`UPDATE emoji_badges SET ${updateCols.map((col) => `${sqlIdent(col)}=?`).join(", ")} WHERE badge_id=?`).run(...updateCols.map((col) => String(req.body[col] ?? "")), badgeId);
  const after = safeOne(req.db, "emoji_badges", cols, { where: "badge_id=?", params: [badgeId] });
  audit(req.db, req.user.username, "badge_catalog_update", "emoji_badges", badgeId, before, { row: after, reason }, req.ip);
  json(res, { ok: true, row: after });
}, closeDb);

app.get("/api/shop", requireAuth, requireAnyPermission("manage_rewards", "manage_inventory", "manage_economy"), (req, res) => {
  const rewards = readRewardsDashboard(req.db);
  json(res, { ...rewards.shop, table_status: rewards.table_status, columns: rewards.columns, writable: false });
}, closeDb);

app.get("/api/shop/purchases", requireAuth, requireAnyPermission("manage_rewards", "manage_inventory", "manage_economy", "view_logs"), (req, res) => {
  json(res, { purchases: safeTableRows(req.db, "purchase_history", { limit: "500" }), premium_transactions: safeTableRows(req.db, "premium_transactions", { limit: "500" }) });
}, closeDb);

app.post("/api/shop/items", requireAuth, requireOwner, (_req, res) => unverifiedSchema(res, "No verified shop item catalog table exists in the live schema."));
app.put("/api/shop/items/:id", requireAuth, requireOwner, (_req, res) => unverifiedSchema(res, "No verified shop item catalog table exists in the live schema."));

app.get("/api/quests", requireAuth, requireAnyPermission("manage_rewards", "manage_economy", "manage_players"), (req, res) => {
  json(res, readQuestsDashboard(req.db, req.query || {}));
}, closeDb);

app.post("/api/quests/catalog", requireAuth, requireOwner, (req, res) => {
  const spec = questCatalogWriteSpec(req.db);
  if (!spec) return unverifiedSchema(res, "No verified editable quest catalog table exists in the live schema.");
  const reason = String(req.body?.reason || "").trim();
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const id = String(req.body?.quest_id || req.body?.mission_id || req.body?.id || "").trim();
  const name = String(req.body?.name || req.body?.title || "").trim();
  if (!id || !name || !validCatalogId(id)) return json(res, { error: "valid_quest_id_and_name_required" }, 400);
  const cols = spec.columns;
  const allowed = [
    spec.id_col,
    spec.name_col,
    "description",
    "category",
    "target_type",
    "target_amount",
    "reward_coins",
    "reward_xp",
    "reward_item",
    "period",
    "enabled",
    "created_at",
    "updated_at",
  ].filter(Boolean);
  const values = {
    [spec.id_col]: id,
    [spec.name_col]: name,
    description: req.body?.description || "",
    category: req.body?.category || "",
    target_type: req.body?.target_type || "",
    target_amount: req.body?.target_amount || "",
    reward_coins: req.body?.reward_coins || "",
    reward_xp: req.body?.reward_xp || "",
    reward_item: req.body?.reward_item || "",
    period: req.body?.period || "",
    enabled: req.body?.enabled === false ? 0 : 1,
    created_at: nowIso(),
    updated_at: nowIso(),
  };
  const insertCols = [...new Set(allowed.filter((col) => cols.includes(col) && values[col] !== undefined))];
  if (!insertCols.includes(spec.id_col) || !insertCols.includes(spec.name_col)) return unverifiedSchema(res, "Quest catalog table does not expose writable id/name columns.");
  const placeholders = insertCols.map(() => "?").join(", ");
  req.db.prepare(`INSERT INTO ${sqlIdent(spec.table)} (${insertCols.map(sqlIdent).join(", ")}) VALUES (${placeholders})`).run(...insertCols.map((col) => values[col]));
  audit(req.db, req.user.username, "quest_catalog_create", spec.table, id, "", { values: Object.fromEntries(insertCols.map((col) => [col, values[col]])), reason }, req.ip);
  json(res, { ok: true, catalog: readQuestsDashboard(req.db).catalog });
}, closeDb);

app.put("/api/quests/catalog/:id", requireAuth, requireOwner, (req, res) => {
  const spec = questCatalogWriteSpec(req.db);
  if (!spec) return unverifiedSchema(res, "No verified editable quest catalog table exists in the live schema.");
  const reason = String(req.body?.reason || "").trim();
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const questId = String(req.params.id || "").trim();
  if (!validCatalogId(questId)) return json(res, { error: "invalid_quest_id" }, 400);
  const before = safeOne(req.db, spec.table, spec.columns, { where: `${sqlIdent(spec.id_col)}=?`, params: [questId] });
  if (!before) return json(res, { error: "not_found" }, 404);
  const allowed = [
    spec.name_col,
    "description",
    "category",
    "target_type",
    "target_amount",
    "reward_coins",
    "reward_xp",
    "reward_item",
    "period",
    "enabled",
    "updated_at",
  ].filter(Boolean);
  const values = {
    [spec.name_col]: req.body?.name ?? req.body?.title,
    description: req.body?.description,
    category: req.body?.category,
    target_type: req.body?.target_type,
    target_amount: req.body?.target_amount,
    reward_coins: req.body?.reward_coins,
    reward_xp: req.body?.reward_xp,
    reward_item: req.body?.reward_item,
    period: req.body?.period,
    enabled: req.body?.enabled,
    updated_at: nowIso(),
  };
  const updateCols = allowed.filter((col) => spec.columns.includes(col) && values[col] !== undefined);
  if (!updateCols.length) return json(res, { error: "no_verified_columns" }, 400);
  req.db.prepare(`UPDATE ${sqlIdent(spec.table)} SET ${updateCols.map((col) => `${sqlIdent(col)}=?`).join(", ")} WHERE ${sqlIdent(spec.id_col)}=?`).run(...updateCols.map((col) => values[col]), questId);
  const after = safeOne(req.db, spec.table, spec.columns, { where: `${sqlIdent(spec.id_col)}=?`, params: [questId] });
  audit(req.db, req.user.username, "quest_catalog_update", spec.table, questId, before, { after, reason }, req.ip);
  json(res, { ok: true, row: after });
}, closeDb);

app.delete("/api/quests/catalog/:id", requireAuth, requireOwner, (req, res) => {
  const spec = questCatalogWriteSpec(req.db);
  if (!spec) return unverifiedSchema(res, "No verified editable quest catalog table exists in the live schema.");
  const questId = String(req.params.id || "").trim();
  const reason = String(req.body?.reason || "").trim();
  if (!validCatalogId(questId)) return json(res, { error: "invalid_quest_id" }, 400);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const before = safeOne(req.db, spec.table, spec.columns, { where: `${sqlIdent(spec.id_col)}=?`, params: [questId] });
  if (!before) return json(res, { error: "not_found" }, 404);
  const col = spec.enabled_col || spec.archive_col;
  if (!col) return unverifiedSchema(res, "Quest catalog table has no verified enabled/archive column for soft-disable.");
  const value = col === "disabled" || col === "archived" || col === "is_archived" || col === "deleted" ? 1 : 0;
  req.db.prepare(`UPDATE ${sqlIdent(spec.table)} SET ${sqlIdent(col)}=? WHERE ${sqlIdent(spec.id_col)}=?`).run(value, questId);
  audit(req.db, req.user.username, "quest_catalog_disable", spec.table, questId, before, { [col]: value, reason }, req.ip);
  json(res, { ok: true, disabled: true });
}, closeDb);

app.get("/api/rewards/logs", requireAuth, requireAnyPermission("manage_rewards", "manage_economy", "view_logs"), (req, res) => {
  json(res, readRewardsDashboard(req.db).logs);
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
const BOT_CONTROL_VALID_MODES = ["dj", "host", "banker", "blackjack", "poker", "miner", "fisher", "security"];
const BOT_CONTROL_VALID_MESSAGE = `Unknown bot. Valid bots: ${BOT_CONTROL_VALID_MODES.join(", ")}`;
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

function normalizeBotControlMode(value) {
  const key = botKey(value);
  if (CANONICAL_BY_MODE.has(key)) return key;
  return CANONICAL_BY_USERNAME.get(key)?.mode || null;
}

function ensureBotControlRequestsTable(db) {
  db.prepare(`
    CREATE TABLE IF NOT EXISTS bot_control_requests (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      action TEXT NOT NULL,
      target_mode TEXT NOT NULL,
      requested_by TEXT,
      requested_at TEXT DEFAULT CURRENT_TIMESTAMP,
      handled_at TEXT,
      status TEXT DEFAULT 'pending'
    )
  `).run();
}

function queueBotControlRequest(db, { action, targetMode, requestedBy }) {
  ensureBotControlRequestsTable(db);
  return db.prepare(`
    INSERT INTO bot_control_requests (action, target_mode, requested_by, status)
    VALUES (?, ?, ?, 'pending')
  `).run(action, targetMode, requestedBy || "dashboard").lastInsertRowid;
}

function botControlMessage(action, targetMode) {
  if (action === "wake_bots") return "Wake request queued for all bots.";
  const username = CANONICAL_BY_MODE.get(targetMode)?.username || targetMode;
  const label = action === "anchor_bot" ? "Anchor request" : "Restart request";
  return `${label} queued for ${username}.`;
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
  if (CANONICAL_BY_MODE.has(mode) && (!username || username === mode)) {
    return { debugOnly: true, reason: `${mode}/${username || "blank"} is a stale generic alias row` };
  }
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
      raw_duplicate_count: Math.max(0, rows.length - 1),
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

function countWhereSafe(db, table, where = "", params = []) {
  try {
    if (!tableExists(db, table)) return 0;
    const sql = `SELECT COUNT(*) AS n FROM ${sqlIdent(table)}${where ? ` WHERE ${where}` : ""}`;
    return db.prepare(sql).get(...params)?.n ?? 0;
  } catch (err) {
    console.error(`[DASHBOARD_DB] countWhereSafe table=${table} error=${err.message}`);
    return 0;
  }
}

function statusCounts(db, table, statusColumn = "status") {
  if (!tableExists(db, table) || !columnExists(db, table, statusColumn)) return [];
  return rowsOrEmpty(db, table, `SELECT ${sqlIdent(statusColumn)} AS status, COUNT(*) AS count FROM ${sqlIdent(table)} GROUP BY ${sqlIdent(statusColumn)} ORDER BY count DESC`);
}

function activeFailedCommandWhere(db) {
  const base = "status IN ('failed','error','unknown_action')";
  return columnExists(db, "bot_command_queue", "reviewed_at")
    ? `${base} AND COALESCE(reviewed_at,'')=''`
    : base;
}

function reviewedCommandWhere(db) {
  const parts = ["status='reviewed'"];
  if (columnExists(db, "bot_command_queue", "reviewed_at")) parts.push("COALESCE(reviewed_at,'')!=''");
  return `(${parts.join(" OR ")})`;
}

function newestTimestamp(values) {
  return values.filter(Boolean).sort((a, b) => String(b).localeCompare(String(a)))[0] || null;
}

function readLiveStatusMap(db) {
  if (!tableExists(db, "live_status") || !columnExists(db, "live_status", "key") || !columnExists(db, "live_status", "value")) return {};
  return readKeyValueMap(db, "live_status");
}

function readOperationsQueue(db) {
  const pendingStatuses = ["pending", "queued"];
  const claimedStatuses = ["claimed", "running"];
  const completedStatuses = ["completed"];
  const quoted = (items) => items.map(() => "?").join(",");
  const hasQueue = tableExists(db, "bot_command_queue");
  const hasStatus = hasQueue && columnExists(db, "bot_command_queue", "status");
  const createdOrder = columnExists(db, "bot_command_queue", "created_at") ? "created_at DESC" : "";
  const failedWhere = activeFailedCommandWhere(db);
  const reviewedWhere = reviewedCommandWhere(db);
  const summarizePayload = (row) => {
    if (!row || !row.payload) return "";
    try {
      const parsed = typeof row.payload === "string" ? JSON.parse(row.payload) : row.payload;
      if (parsed?.message) return `message: ${String(parsed.message).slice(0, 80)}`;
      if (parsed?.emote) return `emote: ${parsed.emote}${parsed.target ? ` → ${parsed.target}` : ""}`;
      if (parsed?.username) return `user: ${parsed.username}`;
      return Object.entries(parsed || {})
        .slice(0, 4)
        .map(([key, value]) => `${key}: ${typeof value === "object" ? JSON.stringify(value).slice(0, 40) : String(value).slice(0, 40)}`)
        .join(", ");
    } catch {
      return String(row.payload).slice(0, 100);
    }
  };
  const formatRows = (rows) => rows.map((row) => ({
    ...row,
    payload_summary: summarizePayload(row),
    failure: row.error_text || row.result_text || "",
  }));
  const pending = hasStatus ? safeRows(db, "bot_command_queue", BOT_COMMAND_QUEUE_COLUMNS, { where: `status IN (${quoted([...pendingStatuses, ...claimedStatuses])})`, params: [...pendingStatuses, ...claimedStatuses], orderBy: createdOrder, limit: "75" }) : [];
  const failed = hasStatus ? safeRows(db, "bot_command_queue", BOT_COMMAND_QUEUE_COLUMNS, { where: failedWhere, orderBy: createdOrder, limit: "75" }) : [];
  const reviewed = hasStatus ? safeRows(db, "bot_command_queue", BOT_COMMAND_QUEUE_COLUMNS, { where: reviewedWhere, orderBy: columnExists(db, "bot_command_queue", "reviewed_at") ? "reviewed_at DESC" : createdOrder, limit: "75" }) : [];
  const completed = hasStatus ? safeRows(db, "bot_command_queue", BOT_COMMAND_QUEUE_COLUMNS, { where: `status IN (${quoted(completedStatuses)})`, params: completedStatuses, orderBy: createdOrder, limit: "75" }) : [];
  const all = hasQueue ? safeRows(db, "bot_command_queue", BOT_COMMAND_QUEUE_COLUMNS, { orderBy: createdOrder, limit: "150" }) : [];
  const recent = all.slice(0, 100);
  return {
    counts: {
      pending: hasStatus ? countWhereSafe(db, "bot_command_queue", `status IN (${quoted(pendingStatuses)})`, pendingStatuses) : 0,
      claimed: hasStatus ? countWhereSafe(db, "bot_command_queue", `status IN (${quoted(claimedStatuses)})`, claimedStatuses) : 0,
      completed: hasStatus ? countWhereSafe(db, "bot_command_queue", `status IN (${quoted(completedStatuses)})`, completedStatuses) : 0,
      failed: hasStatus ? countWhereSafe(db, "bot_command_queue", failedWhere) : 0,
      reviewed: hasStatus ? countWhereSafe(db, "bot_command_queue", reviewedWhere) : 0,
      paused: hasStatus ? countWhereSafe(db, "bot_command_queue", "status='paused'") : 0,
    },
    by_status: statusCounts(db, "bot_command_queue"),
    pending: formatRows(pending),
    failed: formatRows(failed),
    reviewed: formatRows(reviewed),
    completed: formatRows(completed),
    all: formatRows(all),
    recent: formatRows(recent),
  };
}

function readOperationsRoom(db, bots) {
  const live = readLiveStatusMap(db);
  const settings = readKeyValueMap(db, "room_settings");
  const roomId = live.room_id || live.current_room_id || settings.room_id || settings.highrise_room_id || null;
  const roomUsers = Number(live.room_user_count || live.current_room_users || live.users || 0) || 0;
  const presentBots = bots.filter((b) => String(b.status || "").toLowerCase() === "online" || b.current_room_id).map((b) => b.bot_username);
  const missingBots = bots.filter((b) => !presentBots.includes(b.bot_username)).map((b) => b.bot_username);
  const spawnRows = safeRows(db, "bot_spawns", ["bot_username", "spawn_name", "x", "y", "z", "facing", "set_by", "set_at"], { orderBy: columnExists(db, "bot_spawns", "bot_username") ? "bot_username, spawn_name" : "", limit: "250" });
  const spawnStatus = bots.map((bot) => {
    const spawn = spawnRows.find((row) => botKey(row.bot_username) === botKey(bot.bot_username) || botKey(row.bot_username) === botKey(bot.bot_mode));
    return { bot_username: bot.bot_username, mode: bot.bot_mode, spawn_saved: !!spawn, spawn_name: spawn?.spawn_name || null, last_heartbeat_at: bot.last_heartbeat_at || null, current_room_id: bot.current_room_id || null };
  });
  const notInRoomErrors = [
    ...safeRows(db, "bot_command_queue", BOT_COMMAND_QUEUE_COLUMNS, { where: "COALESCE(error_text,'') LIKE '%Not in room%' OR COALESCE(result_text,'') LIKE '%Not in room%'", orderBy: columnExists(db, "bot_command_queue", "created_at") ? "created_at DESC" : "", limit: "50" }),
    ...safeTableRows(db, "command_error_logs", { orderBy: columnExists(db, "command_error_logs", "created_at") ? "created_at DESC" : "", limit: "50" }).filter((row) => JSON.stringify(row).toLowerCase().includes("not in room")),
  ].slice(0, 50);
  return { room_id: roomId, room_users_count: roomUsers, live_status: live, bots_present: presentBots, missing_bots: missingBots, spawn_status: spawnStatus, spawn_rows: spawnRows, recent_not_in_room_errors: notInRoomErrors };
}

function readOperationsRadio(db, bots) {
  const radio = readLocalRadioStatus(db);
  const dj = bots.find((bot) => bot.bot_mode === "dj" || bot.bot_username === "DJ_DUDU") || null;
  const failedJobs = tableExists(db, "yt_request_jobs") && columnExists(db, "yt_request_jobs", "status")
    ? safeRows(db, "yt_request_jobs", ["id", "title", "artist", "username", "status", "error", "created_at", "finished_at"], { where: "status IN ('failed','failed_download','error','cancelled')", orderBy: columnExists(db, "yt_request_jobs", "created_at") ? "created_at DESC" : "", limit: "50" })
    : [];
  return {
    now_playing: radio.now_playing || null,
    queue_size: radio.queue?.length || 0,
    queue: radio.queue || [],
    request_gate_open: radio.queue_open ?? null,
    failed_jobs: failedJobs,
    failed_jobs_count: failedJobs.length,
    azuracast: radio.health?.azuracast || radio.stream || {},
    dj_heartbeat: dj,
    last_radio_error: failedJobs[0]?.error || dj?.last_error || null,
    health: radio.health || {},
  };
}

function readOperationsErrors(db) {
  const commandErrors = safeTableRows(db, "command_error_logs", { orderBy: columnExists(db, "command_error_logs", "created_at") ? "created_at DESC" : columnExists(db, "command_error_logs", "id") ? "id DESC" : "", limit: "100" });
  const failedCommands = safeRows(db, "bot_command_queue", BOT_COMMAND_QUEUE_COLUMNS, { where: activeFailedCommandWhere(db), orderBy: columnExists(db, "bot_command_queue", "created_at") ? "created_at DESC" : "", limit: "100" });
  const failedAdmin = safeTableRows(db, "admin_action_logs", { orderBy: columnExists(db, "admin_action_logs", "created_at") ? "created_at DESC" : columnExists(db, "admin_action_logs", "id") ? "id DESC" : "", limit: "100" }).filter((row) => JSON.stringify(row).toLowerCase().includes("fail") || JSON.stringify(row).toLowerCase().includes("error"));
  const failedRadio = tableExists(db, "yt_request_jobs") && columnExists(db, "yt_request_jobs", "status")
    ? safeRows(db, "yt_request_jobs", ["id", "title", "username", "status", "error", "created_at", "finished_at"], { where: "status IN ('failed','failed_download','error','cancelled')", orderBy: columnExists(db, "yt_request_jobs", "created_at") ? "created_at DESC" : "", limit: "100" })
    : [];
  return { command_error_logs: commandErrors, failed_commands: failedCommands, failed_admin_actions: failedAdmin, radio_failures: failedRadio, recent_errors_count: commandErrors.length + failedCommands.length + failedAdmin.length + failedRadio.length };
}

function buildOperationsAlerts({ bots, room, radio, queue, database }) {
  const alerts = [];
  const add = (severity, key, message, detail = "") => alerts.push({ severity, key, message, detail });
  const offline = bots.filter((b) => String(b.status || "").toLowerCase() !== "online");
  if (offline.length) add("CRITICAL", "bot_offline", `${offline.length} canonical bot${offline.length === 1 ? "" : "s"} offline or missing.`, offline.map((b) => b.bot_username).join(", "));
  const stale = bots.filter((b) => !b.last_heartbeat_at || Date.now() - new Date(b.last_heartbeat_at).getTime() > 10 * 60 * 1000);
  const allCanonicalOnline = bots.length === CANONICAL_BOTS.length && offline.length === 0;
  if (stale.length && allCanonicalOnline) {
    add("INFO", "bot_heartbeat_source_stale", "Bot heartbeat source stale / runtime health uncertain.", stale.map((b) => b.bot_username).join(", "));
  } else if (stale.length) {
    add("WARNING", "bot_heartbeat_stale", `${stale.length} bot heartbeat${stale.length === 1 ? "" : "s"} stale.`, stale.map((b) => b.bot_username).join(", "));
  }
  if (database.integrity_check && database.integrity_check !== "ok") add("CRITICAL", "db_integrity_failed", `SQLite integrity_check: ${database.integrity_check}`);
  if ((queue.counts?.failed || 0) > 0) add("WARNING", "command_queue_failed", `${queue.counts.failed} failed command queue row${queue.counts.failed === 1 ? "" : "s"}.`);
  if ((radio.failed_jobs_count || 0) > 0) add("WARNING", "radio_failed_jobs", `${radio.failed_jobs_count} failed radio job${radio.failed_jobs_count === 1 ? "" : "s"}.`);
  const lastBackup = backupFileList().find((b) => b.type === "sqlite_db");
  if (!lastBackup) add("WARNING", "backup_missing", "No DB backup found in approved backup folders.");
  else if (Date.now() - new Date(lastBackup.modified_at).getTime() > 24 * 60 * 60 * 1000) add("WARNING", "backup_old", "Latest DB backup is older than 24 hours.", lastBackup.modified_at);
  if ((room.missing_bots || []).length) add("WARNING", "room_bot_missing", `${room.missing_bots.length} bot${room.missing_bots.length === 1 ? "" : "s"} not confirmed present.`, room.missing_bots.join(", "));
  if ((room.recent_not_in_room_errors || []).length) add("WARNING", "not_in_room_errors", `${room.recent_not_in_room_errors.length} recent Not in room error row${room.recent_not_in_room_errors.length === 1 ? "" : "s"}.`);
  return alerts;
}

async function readOperationsSnapshot(db) {
  const audit = readCanonicalBotAudit(db);
  const bots = audit.bots;
  const pm2 = await pm2Snapshot();
  const queue = readOperationsQueue(db);
  const database = dbHealthSnapshot(db);
  database.last_backup = backupFileList().find((b) => b.type === "sqlite_db") || null;
  const room = readOperationsRoom(db, bots);
  const radio = readOperationsRadio(db, bots);
  const errors = readOperationsErrors(db);
  const alerts = buildOperationsAlerts({ bots, room, radio, queue, database });
  const activeAlerts = alerts.filter((alert) => ["WARNING", "CRITICAL"].includes(String(alert.severity || "").toUpperCase()));
  const infoNotices = alerts.filter((alert) => String(alert.severity || "").toUpperCase() === "INFO");
  const onlineBots = bots.filter((b) => String(b.status || "").toLowerCase() === "online").length;
  const dashboardProc = pm2.processes.find((p) => /dashboard/i.test(p.name || "")) || null;
  const systemStatus = activeAlerts.some((a) => a.severity === "CRITICAL") ? "CRITICAL" : activeAlerts.some((a) => a.severity === "WARNING") ? "WARNING" : "HEALTHY";
  const lastRestart = newestTimestamp([dashboardProc?.uptime, ...pm2.processes.map((p) => p.uptime)].filter(Boolean).map((t) => new Date(t).toISOString()));
  const logs = {
    audit_logs: safeRows(db, "audit_logs", ["id", "actor", "action_type", "target_type", "target_id", "old_value", "new_value", "ip_address", "created_at"], { orderBy: columnExists(db, "audit_logs", "created_at") ? "created_at DESC" : "id DESC", limit: "100" }),
    admin_action_logs: safeTableRows(db, "admin_action_logs", { orderBy: columnExists(db, "admin_action_logs", "created_at") ? "created_at DESC" : columnExists(db, "admin_action_logs", "id") ? "id DESC" : "", limit: "100" }),
    command_queue_failures: errors.failed_commands,
    radio_failures: errors.radio_failures,
  };
  return {
    updated_at: nowIso(),
    system_status: systemStatus,
    overview: {
      system_status: systemStatus,
      bots_online: onlineBots,
      bots_total: bots.length,
      dashboard_status: dashboardProc?.status || (pm2.available ? "not_found" : "pm2_unavailable"),
      radio_status: radio.health?.radio_online ? "Online" : (radio.dj_heartbeat && String(radio.dj_heartbeat.status).toLowerCase() === "online" ? "Online" : "Offline"),
      db_status: database.integrity_check === "ok" ? "OK" : "Warning",
      command_queue_pending: queue.counts.pending,
      command_queue_failed: queue.counts.failed,
      room_users: room.room_users_count,
      last_restart: lastRestart,
      active_alerts: activeAlerts.length,
      info_notices: infoNotices.length,
    },
    bots,
    raw_bot_instances: audit.raw_rows,
    room,
    radio,
    queue,
    database,
    errors,
    alerts,
    active_alerts: activeAlerts,
    info_notices: infoNotices,
    logs,
    pm2,
    advanced: {
      raw_health: { pm2, database, bot_audit: audit.summary, generated_at: nowIso() },
      cleanup_links: ["/api/maintenance/cleanup-preview", "/api/maintenance/backups"],
      maintenance_links: ["/api/maintenance/overview", "/api/maintenance/db-health", "/api/maintenance/runtime-health"],
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

app.post("/api/owner/bots/wake", requireAuth, requireOwner, (req, res) => {
  const action = "wake_bots";
  const targetMode = "all";
  const id = queueBotControlRequest(req.db, { action, targetMode, requestedBy: req.user?.username });
  audit(req.db, req.user.username, "bot_control_wake_requested", "bot_control_requests", id, "", { action, target_mode: targetMode }, req.ip);
  json(res, { ok: true, action, target_mode: targetMode, message: botControlMessage(action, targetMode) });
}, closeDb);

app.post("/api/owner/bots/:mode/restart", requireAuth, requireOwner, (req, res) => {
  const targetMode = normalizeBotControlMode(req.params.mode);
  if (!targetMode) return json(res, { ok: false, error: BOT_CONTROL_VALID_MESSAGE }, 400);
  const action = "restart_bot";
  const id = queueBotControlRequest(req.db, { action, targetMode, requestedBy: req.user?.username });
  audit(req.db, req.user.username, "bot_control_restart_requested", "bot_control_requests", id, "", { action, target_mode: targetMode }, req.ip);
  json(res, { ok: true, action, target_mode: targetMode, message: botControlMessage(action, targetMode) });
}, closeDb);

app.post("/api/owner/bots/:mode/anchor", requireAuth, requireOwner, (req, res) => {
  const targetMode = normalizeBotControlMode(req.params.mode);
  if (!targetMode) return json(res, { ok: false, error: BOT_CONTROL_VALID_MESSAGE }, 400);
  const action = "anchor_bot";
  const id = queueBotControlRequest(req.db, { action, targetMode, requestedBy: req.user?.username });
  audit(req.db, req.user.username, "bot_control_anchor_requested", "bot_control_requests", id, "", { action, target_mode: targetMode }, req.ip);
  json(res, { ok: true, action, target_mode: targetMode, message: botControlMessage(action, targetMode) });
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

async function sendOperationsSection(req, res, section = "all") {
  const data = await readOperationsSnapshot(req.db);
  const sections = {
    all: data,
    bots: { updated_at: data.updated_at, bots: data.bots, raw_bot_instances: data.raw_bot_instances, alerts: data.alerts.filter((a) => /bot|heartbeat|room/.test(a.key)) },
    room: { updated_at: data.updated_at, ...data.room, alerts: data.alerts.filter((a) => /room|spawn|bot_missing|not_in_room/.test(a.key)) },
    radio: { updated_at: data.updated_at, ...data.radio, alerts: data.alerts.filter((a) => /radio|dj/.test(a.key)) },
    queue: { updated_at: data.updated_at, ...data.queue, alerts: data.alerts.filter((a) => /queue|command/.test(a.key)) },
    database: { updated_at: data.updated_at, ...data.database, alerts: data.alerts.filter((a) => /db|backup/.test(a.key)) },
    errors: { updated_at: data.updated_at, ...data.errors },
    alerts: { updated_at: data.updated_at, alerts: data.alerts, active_alerts: data.active_alerts, info_notices: data.info_notices, system_status: data.system_status },
    logs: { updated_at: data.updated_at, ...data.logs },
  };
  json(res, sections[section] || data);
}

app.get("/api/operations", requireAuth, requireAnyPermission("view_logs", "manage_bots", "emergency_controls"), async (req, res) => {
  await sendOperationsSection(req, res, "all");
}, closeDb);

for (const [pathName, section] of [
  ["bots", "bots"],
  ["room", "room"],
  ["radio", "radio"],
  ["queue", "queue"],
  ["database", "database"],
  ["errors", "errors"],
  ["alerts", "alerts"],
  ["logs", "logs"],
]) {
  app.get(`/api/operations/${pathName}`, requireAuth, requireAnyPermission("view_logs", "manage_bots", "emergency_controls"), async (req, res) => {
    await sendOperationsSection(req, res, section);
  }, closeDb);
}

/* ── Economy Overview (read-only) ───────────────────── */
app.get("/api/economy/settings", requireAuth, requireAnyPermission("manage_economy", "manage_rewards", "emergency_controls"), (req, res) => {
  json(res, readTypedSettings(req.db, "economy_settings", ECONOMY_SETTING_FIELDS));
}, closeDb);

app.put("/api/economy/settings", requireAuth, requireAnyPermission("manage_economy", "emergency_controls"), (req, res) => {
  if (!tableExists(req.db, "economy_settings")) return json(res, { error: "economy_settings_missing" }, 404);
  let updates;
  try {
    updates = normalizeSettingsBody(req.body || {}, ECONOMY_SETTING_FIELDS);
  } catch (err) {
    return json(res, { error: err.message || "invalid_economy_setting" }, 400);
  }
  if (!Object.keys(updates).length) return json(res, { error: "no_supported_settings" }, 400);
  const before = readTypedSettings(req.db, "economy_settings", ECONOMY_SETTING_FIELDS).settings;
  try {
    for (const [key, value] of Object.entries(updates)) writeKeyValue(req.db, "economy_settings", key, value);
    const after = readTypedSettings(req.db, "economy_settings", ECONOMY_SETTING_FIELDS).settings;
    audit(req.db, req.user.username, "economy_settings_update", "economy_settings", Object.keys(updates).join(","), before, updates, req.ip);
    json(res, { ok: true, settings: after, source: "economy_settings" });
  } catch (err) {
    json(res, { error: err.message || "economy_settings_update_failed" }, 500);
  }
}, closeDb);

app.get("/api/bank/settings", requireAuth, requireAnyPermission("manage_economy", "manage_rewards", "view_logs", "emergency_controls"), (req, res) => {
  json(res, readTypedSettings(req.db, "bank_settings", BANK_SETTING_FIELDS));
}, closeDb);

app.put("/api/bank/settings", requireAuth, requireAnyPermission("manage_economy", "emergency_controls"), (req, res) => {
  if (!tableExists(req.db, "bank_settings")) return json(res, { error: "bank_settings_missing" }, 404);
  let updates;
  try {
    updates = normalizeSettingsBody(req.body || {}, BANK_SETTING_FIELDS);
    const current = readTypedSettings(req.db, "bank_settings", BANK_SETTING_FIELDS).settings;
    validateBankSettings({ ...current, ...updates });
  } catch (err) {
    return json(res, { error: err.message || "invalid_bank_setting" }, 400);
  }
  if (!Object.keys(updates).length) return json(res, { error: "no_supported_settings" }, 400);
  const before = readTypedSettings(req.db, "bank_settings", BANK_SETTING_FIELDS).settings;
  try {
    for (const [key, value] of Object.entries(updates)) writeKeyValue(req.db, "bank_settings", key, value);
    const after = readTypedSettings(req.db, "bank_settings", BANK_SETTING_FIELDS).settings;
    audit(req.db, req.user.username, "bank_settings_update", "bank_settings", Object.keys(updates).join(","), before, updates, req.ip);
    json(res, { ok: true, settings: after, source: "bank_settings" });
  } catch (err) {
    json(res, { error: err.message || "bank_settings_update_failed" }, 500);
  }
}, closeDb);

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

/* ── Security / Moderation ──────────────────────────── */
app.get("/api/security", requireAuth, requireAnyPermission("manage_moderation", "view_logs", "emergency_controls"), (req, res) => {
  json(res, readSecurityDashboard(req.db));
}, closeDb);

app.get("/api/security/reports", requireAuth, requireAnyPermission("manage_moderation", "view_logs", "emergency_controls"), (req, res) => {
  const d = readSecurityDashboard(req.db);
  json(res, { reports: d.tables.reports, overview: d.overview });
}, closeDb);

app.put("/api/security/reports/:id", requireAuth, requireAnyPermission("manage_moderation", "emergency_controls"), (req, res) => {
  if (!tableExists(req.db, "reports")) return json(res, { error: "reports_missing" }, 404);
  const cols = tableColumns(req.db, "reports");
  if (!cols.includes("id")) return json(res, { error: "reports_id_missing" }, 400);
  const id = String(req.params.id);
  const before = safeOne(req.db, "reports", cols, { where: "id=?", params: [id] });
  if (!before) return json(res, { error: "not_found" }, 404);
  const updates = [];
  const values = [];
  const status = String(req.body?.status || "").trim().toLowerCase();
  if (status) {
    if (!["open", "reviewing", "resolved", "closed", "dismissed"].includes(status)) return json(res, { error: "invalid_status" }, 400);
    if (cols.includes("status")) { updates.push("status=?"); values.push(status); }
  }
  const resolution = String(req.body?.resolution || req.body?.resolution_note || "").trim();
  for (const col of ["resolution", "resolution_note", "handled_reason"]) {
    if (resolution && cols.includes(col)) { updates.push(`${sqlIdent(col)}=?`); values.push(resolution); break; }
  }
  for (const col of ["handled_by", "assigned_to", "reviewed_by"]) {
    if (cols.includes(col)) { updates.push(`${sqlIdent(col)}=?`); values.push(req.user.username); break; }
  }
  for (const col of ["handled_at", "updated_at", "resolved_at"]) {
    if (cols.includes(col)) { updates.push(`${sqlIdent(col)}=CURRENT_TIMESTAMP`); break; }
  }
  if (!updates.length) return json(res, { error: "unverified_schema", message: "reports table has no supported writable moderation columns." }, 400);
  req.db.prepare(`UPDATE reports SET ${updates.join(", ")} WHERE id=?`).run(...values, id);
  const after = safeOne(req.db, "reports", cols, { where: "id=?", params: [id] });
  audit(req.db, req.user.username, "security_report_update", "reports", id, before, after, req.ip);
  json(res, { ok: true, report: after });
}, closeDb);

app.get("/api/security/warnings", requireAuth, requireAnyPermission("manage_moderation", "view_logs", "emergency_controls"), (req, res) => {
  const d = readSecurityDashboard(req.db);
  json(res, { warnings: d.tables.warnings, room_warnings: d.tables.room_warnings, overview: d.overview });
}, closeDb);

app.post("/api/security/warnings", requireAuth, requireAnyPermission("manage_moderation", "emergency_controls"), (req, res) => {
  const query = String(req.body?.username || req.body?.user_id || req.body?.query || "").trim();
  const reason = String(req.body?.reason || "").trim();
  if (!query) return json(res, { error: "player_required" }, 400);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const player = readPlayerProfile(req.db, query);
  if (!player) return json(res, { error: "player_not_found" }, 404);
  try {
    const info = insertWarningRow(req.db, player, req.user.username, reason);
    writeModerationLog(req.db, req.user.username, player, "warn", reason, 0);
    audit(req.db, req.user.username, "security_warn", "warnings", player.user_id, "", { reason, rowid: info.lastInsertRowid }, req.ip);
    json(res, { ok: true, player: readPlayerProfile(req.db, player.user_id), rowid: info.lastInsertRowid });
  } catch (err) {
    try {
      const queued = enqueueBotCommand(req.db, { targetBot: "security", actionName: "warn_user", payload: { username: player.username, user_id: player.user_id, reason }, requesterId: req.user.username });
      audit(req.db, req.user.username, "security_warn_enqueue", "bot_command_queue", queued.id, "", { player: player.username, reason }, req.ip);
      json(res, { ok: true, queued: true, command: queued, message: "Warning queued for security bot. Bot must consume bot_command_queue." });
    } catch (queueErr) {
      json(res, { error: err.message || "warning_failed", queue_error: queueErr.message }, 400);
    }
  }
}, closeDb);

app.get("/api/security/mutes", requireAuth, requireAnyPermission("manage_moderation", "view_logs", "emergency_controls"), (req, res) => {
  const d = readSecurityDashboard(req.db);
  json(res, { mutes: d.tables.mutes, overview: d.overview });
}, closeDb);

app.post("/api/security/mutes", requireAuth, requireAnyPermission("manage_moderation", "emergency_controls"), (req, res) => {
  const query = String(req.body?.username || req.body?.user_id || req.body?.query || "").trim();
  const reason = String(req.body?.reason || "").trim();
  const minutes = Math.max(1, Math.min(10080, Math.trunc(Number(req.body?.minutes || 60))));
  if (!query) return json(res, { error: "player_required" }, 400);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const player = readPlayerProfile(req.db, query);
  if (!player) return json(res, { error: "player_not_found" }, 404);
  try {
    const before = tableExists(req.db, "mutes") ? safeOne(req.db, "mutes", tableColumns(req.db, "mutes"), { where: "user_id=? OR lower(username)=lower(?)", params: [player.user_id, player.username] }) : null;
    upsertMuteRow(req.db, player, req.user.username, reason, minutes);
    writeModerationLog(req.db, req.user.username, player, "mute", reason, minutes);
    audit(req.db, req.user.username, "security_mute", "mutes", player.user_id, before, { reason, minutes }, req.ip);
    json(res, { ok: true, player: readPlayerProfile(req.db, player.user_id) });
  } catch (err) {
    try {
      const queued = enqueueBotCommand(req.db, { targetBot: "security", actionName: "mute_user", payload: { username: player.username, user_id: player.user_id, reason, minutes }, requesterId: req.user.username });
      audit(req.db, req.user.username, "security_mute_enqueue", "bot_command_queue", queued.id, "", { player: player.username, reason, minutes }, req.ip);
      json(res, { ok: true, queued: true, command: queued, message: "Mute queued for security bot. Bot must consume bot_command_queue." });
    } catch (queueErr) {
      json(res, { error: err.message || "mute_failed", queue_error: queueErr.message }, 400);
    }
  }
}, closeDb);

app.delete("/api/security/mutes/:user_id", requireAuth, requireAnyPermission("manage_moderation", "emergency_controls"), (req, res) => {
  const player = readPlayerProfile(req.db, req.params.user_id);
  const target = player?.user_id || String(req.params.user_id);
  if (!tableExists(req.db, "mutes")) return json(res, { error: "mutes_missing" }, 404);
  const before = safeOne(req.db, "mutes", tableColumns(req.db, "mutes"), { where: "user_id=? OR lower(username)=lower(?)", params: [target, player?.username || target] });
  const cols = tableColumns(req.db, "mutes");
  const where = cols.includes("user_id") && cols.includes("username") ? "user_id=? OR lower(username)=lower(?)" : cols.includes("user_id") ? "user_id=?" : "lower(username)=lower(?)";
  const params = where.includes("OR") ? [target, player?.username || target] : [cols.includes("user_id") ? target : player?.username || target];
  const info = req.db.prepare(`DELETE FROM mutes WHERE ${where}`).run(...params);
  if (player) writeModerationLog(req.db, req.user.username, player, "unmute", String(req.body?.reason || ""), 0);
  audit(req.db, req.user.username, "security_unmute", "mutes", target, before, { removed: info.changes }, req.ip);
  json(res, { ok: true, removed: info.changes, player: player ? readPlayerProfile(req.db, player.user_id) : null });
}, closeDb);

app.get("/api/security/player/:query", requireAuth, requireAnyPermission("manage_moderation", "view_logs", "emergency_controls"), (req, res) => {
  const player = readPlayerProfile(req.db, req.params.query);
  if (!player) return json(res, { error: "not_found" }, 404);
  const lowerId = String(player.user_id || "").toLowerCase();
  const lowerName = String(player.username || "").toLowerCase();
  const matchPlayer = (r) => [r.user_id, r.username, r.target_id, r.target_username].map((v) => String(v || "").toLowerCase()).includes(lowerId)
    || [r.username, r.target_username, r.target_name].map((v) => String(v || "").toLowerCase()).includes(lowerName);
  const bans = tableExists(req.db, "room_bans") ? safeTableRows(req.db, "room_bans", { orderBy: columnExists(req.db, "room_bans", "created_at") ? "created_at DESC" : "", limit: "250" }).filter(matchPlayer) : [];
  const jail = tableExists(req.db, "jail_sentences") ? safeTableRows(req.db, "jail_sentences", { orderBy: columnExists(req.db, "jail_sentences", "created_at") ? "created_at DESC" : "", limit: "250" }).filter(matchPlayer) : [];
  json(res, { player, bans, jail });
}, closeDb);

app.get("/api/security/logs", requireAuth, requireAnyPermission("manage_moderation", "view_logs", "emergency_controls"), (req, res) => {
  const d = readSecurityDashboard(req.db);
  json(res, {
    moderation_logs: d.tables.moderation_logs,
    audit_logs: d.tables.audit_logs,
    admin_action_logs: d.tables.admin_action_logs,
    command_error_logs: d.tables.command_error_logs,
    command_queue: d.command_queue,
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
  const miningLogs = safeRows(db, "mining_logs", ["id","user_id","username","ore","ore_name","item_id","rarity","weight","value","created_at","mined_at","timestamp"], {
    where: columnExists(db, "mining_logs", "user_id") ? "user_id=? OR lower(username)=lower(?)" : "lower(username)=lower(?)",
    params: columnExists(db, "mining_logs", "user_id") ? [userId, username] : [username],
    orderBy: columnExists(db, "mining_logs", "created_at") ? "created_at DESC" : (columnExists(db, "mining_logs", "id") ? "id DESC" : ""),
    limit: "50",
  });
  const fishingLogs = safeRows(db, "fish_catch_records", ["id","user_id","username","fish_name","rarity","weight","value","caught_at","created_at"], {
    where: columnExists(db, "fish_catch_records", "user_id") ? "user_id=? OR lower(username)=lower(?)" : "lower(username)=lower(?)",
    params: columnExists(db, "fish_catch_records", "user_id") ? [userId, username] : [username],
    orderBy: columnExists(db, "fish_catch_records", "caught_at") ? "caught_at DESC" : (columnExists(db, "fish_catch_records", "id") ? "id DESC" : ""),
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
    game_logs: { mining: miningLogs, fishing: fishingLogs },
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

function playerInventoryWriteAllowed(req) {
  return req.user?.role === "owner" || !!req.permissions?.manage_inventory;
}

function playerInventoryReason(req) {
  return String(req.body?.reason || "").trim().slice(0, 300);
}

function fishInventoryWhere(db, player, filters = {}) {
  const clauses = [];
  const params = [];
  if (columnExists(db, "fish_inventory", "user_id")) {
    clauses.push("(user_id=? OR lower(username)=lower(?))");
    params.push(player.user_id, player.username);
  } else {
    clauses.push("lower(username)=lower(?)");
    params.push(player.username);
  }
  if (filters.sold === "0" || filters.sold === "1") {
    if (columnExists(db, "fish_inventory", "sold")) {
      clauses.push("COALESCE(sold,0)=?");
      params.push(Number(filters.sold));
    }
  }
  if (filters.rarity && columnExists(db, "fish_inventory", "rarity")) {
    clauses.push("lower(rarity)=lower(?)");
    params.push(String(filters.rarity));
  }
  if (filters.q && columnExists(db, "fish_inventory", "fish_name")) {
    clauses.push("lower(fish_name) LIKE lower(?)");
    params.push(`%${String(filters.q).slice(0, 80)}%`);
  }
  return { where: clauses.join(" AND "), params };
}

function readPlayerFishingInventory(db, idOrQuery, filters = {}) {
  const player = readPlayerProfile(db, idOrQuery);
  if (!player) return null;
  const inventoryCols = ["id","user_id","username","fish_id","fish_name","rarity","weight","value","sold","sold_at","caught_at","created_at"];
  const { where, params } = fishInventoryWhere(db, player, filters);
  const order = columnExists(db, "fish_inventory", "caught_at") ? "caught_at DESC" : (columnExists(db, "fish_inventory", "id") ? "id DESC" : "");
  const rows = safeRows(db, "fish_inventory", inventoryCols, { where, params, orderBy: order, limit: "500" })
    .map((row) => ({ ...row, source_table: "fish_inventory" }));
  const allRows = safeRows(db, "fish_inventory", inventoryCols, fishInventoryWhere(db, player, {}));
  const profile = safeOne(db, "fish_profiles", ["user_id","username","fishing_level","fishing_xp","total_catches","equipped_rod","best_fish_name","best_fish_weight","best_fish_value","last_fish_at","created_at","updated_at"], {
    where: columnExists(db, "fish_profiles", "user_id") ? "user_id=? OR lower(username)=lower(?)" : "lower(username)=lower(?)",
    params: columnExists(db, "fish_profiles", "user_id") ? [player.user_id, player.username] : [player.username],
  });
  const catchRecords = safeRows(db, "fish_catch_records", ["id","user_id","username","fish_name","rarity","weight","value","caught_at","created_at"], {
    where: columnExists(db, "fish_catch_records", "user_id") ? "user_id=? OR lower(username)=lower(?)" : "lower(username)=lower(?)",
    params: columnExists(db, "fish_catch_records", "user_id") ? [player.user_id, player.username] : [player.username],
    orderBy: columnExists(db, "fish_catch_records", "caught_at") ? "caught_at DESC" : (columnExists(db, "fish_catch_records", "id") ? "id DESC" : ""),
    limit: "150",
  }).map((row) => ({ ...row, source_table: "fish_catch_records" }));
  const catalog = safeRows(db, "fish_catalog", ["fish_id","name","rarity","base_value","min_weight","max_weight","catch_enabled","event_only","emoji"], {
    orderBy: columnExists(db, "fish_catalog", "rarity") ? "rarity, name" : "",
    limit: "500",
  });
  const unsoldRows = allRows.filter((row) => Number(row.sold || 0) === 0);
  const soldRows = allRows.filter((row) => Number(row.sold || 0) === 1);
  const biggest = [...allRows].sort((a, b) => Number(b.weight || 0) - Number(a.weight || 0))[0] || null;
  const best = [...allRows].sort((a, b) => Number(b.value || 0) - Number(a.value || 0))[0] || null;
  return {
    player,
    profile,
    rows,
    catch_records: catchRecords,
    catalog,
    filters,
    table_status: {
      fish_inventory: tableExists(db, "fish_inventory"),
      fish_catch_records: tableExists(db, "fish_catch_records"),
      fish_profiles: tableExists(db, "fish_profiles"),
      fish_catalog: tableExists(db, "fish_catalog"),
      sold_tracking: columnExists(db, "fish_inventory", "sold"),
    },
    summary: {
      username: player.username,
      total_fish: allRows.length,
      unsold_fish: unsoldRows.length,
      sold_fish: soldRows.length,
      total_unsold_value: unsoldRows.reduce((sum, row) => sum + Number(row.value || 0), 0),
      best_fish: profile?.best_fish_name || best?.fish_name || null,
      best_fish_value: profile?.best_fish_value ?? best?.value ?? null,
      biggest_fish: profile?.best_fish_name || biggest?.fish_name || null,
      biggest_fish_weight: profile?.best_fish_weight ?? biggest?.weight ?? null,
    },
  };
}

function miningInventoryWhere(db, player, filters = {}) {
  const clauses = [];
  const params = [];
  if (columnExists(db, "mining_inventory", "user_id")) {
    clauses.push("(mi.user_id=? OR lower(mi.username)=lower(?))");
    params.push(player.user_id, player.username);
  } else {
    clauses.push("lower(mi.username)=lower(?)");
    params.push(player.username);
  }
  if (filters.rarity && tableExists(db, "mining_items") && columnExists(db, "mining_items", "rarity")) {
    clauses.push("lower(it.rarity)=lower(?)");
    params.push(String(filters.rarity));
  }
  if (filters.q) {
    clauses.push("(lower(mi.item_id) LIKE lower(?) OR lower(COALESCE(it.name,'')) LIKE lower(?))");
    const q = `%${String(filters.q).slice(0, 80)}%`;
    params.push(q, q);
  }
  return { where: clauses.join(" AND "), params };
}

function readPlayerMiningInventory(db, idOrQuery, filters = {}) {
  const player = readPlayerProfile(db, idOrQuery);
  if (!player) return null;
  const profile = safeOne(db, "mining_players", ["user_id","username","mining_level","mining_xp","level","xp","total_mines","total_mined","current_pickaxe","equipped_pickaxe","best_ore","best_ore_name","created_at","updated_at"], {
    where: columnExists(db, "mining_players", "user_id") ? "user_id=? OR lower(username)=lower(?)" : "lower(username)=lower(?)",
    params: columnExists(db, "mining_players", "user_id") ? [player.user_id, player.username] : [player.username],
  });
  const { where, params } = miningInventoryWhere(db, player, filters);
  const rows = rowsOrEmpty(db, "mining_inventory", `
    SELECT mi.id, ${columnExists(db, "mining_inventory", "user_id") ? "mi.user_id," : "NULL AS user_id,"}
      mi.username, mi.item_id, mi.quantity,
      it.name AS ore_name, it.emoji, it.rarity, it.sell_value AS value,
      (COALESCE(mi.quantity,0) * COALESCE(it.sell_value,0)) AS total_value,
      'mining_inventory' AS source_table
    FROM mining_inventory mi
    LEFT JOIN mining_items it ON mi.item_id=it.item_id
    WHERE ${where}
    ORDER BY total_value DESC, mi.item_id
    LIMIT 500
  `, ...params);
  const allParams = miningInventoryWhere(db, player, {});
  const allRows = rowsOrEmpty(db, "mining_inventory", `
    SELECT mi.id, mi.username, mi.item_id, mi.quantity, it.name AS ore_name, it.rarity, it.sell_value AS value,
      (COALESCE(mi.quantity,0) * COALESCE(it.sell_value,0)) AS total_value
    FROM mining_inventory mi
    LEFT JOIN mining_items it ON mi.item_id=it.item_id
    WHERE ${allParams.where}
    LIMIT 1000
  `, ...allParams.params);
  const logs = safeRows(db, "mining_logs", ["id","user_id","username","ore","ore_name","item_id","rarity","weight","value","created_at","mined_at","timestamp"], {
    where: columnExists(db, "mining_logs", "user_id") ? "user_id=? OR lower(username)=lower(?)" : "lower(username)=lower(?)",
    params: columnExists(db, "mining_logs", "user_id") ? [player.user_id, player.username] : [player.username],
    orderBy: columnExists(db, "mining_logs", "created_at") ? "created_at DESC" : (columnExists(db, "mining_logs", "id") ? "id DESC" : ""),
    limit: "150",
  }).map((row) => ({ ...row, source_table: "mining_logs" }));
  const catalog = safeRows(db, "mining_items", ["item_id","name","emoji","rarity","item_type","sell_value","drop_enabled"], {
    where: columnExists(db, "mining_items", "item_type") ? "item_type='ore'" : "",
    orderBy: columnExists(db, "mining_items", "rarity") ? "rarity, name" : "",
    limit: "500",
  });
  const best = [...allRows].sort((a, b) => Number(b.total_value || 0) - Number(a.total_value || 0))[0] || null;
  return {
    player,
    profile,
    rows,
    logs,
    catalog,
    filters,
    table_status: {
      mining_inventory: tableExists(db, "mining_inventory"),
      mining_logs: tableExists(db, "mining_logs"),
      mining_players: tableExists(db, "mining_players"),
      mining_items: tableExists(db, "mining_items"),
      sold_tracking: columnExists(db, "mining_inventory", "sold"),
    },
    summary: {
      username: player.username,
      total_ores: allRows.reduce((sum, row) => sum + Number(row.quantity || 0), 0),
      unsold_ores: allRows.reduce((sum, row) => sum + Number(row.quantity || 0), 0),
      sold_ores: null,
      total_unsold_value: allRows.reduce((sum, row) => sum + Number(row.total_value || 0), 0),
      best_ore: profile?.best_ore_name || profile?.best_ore || best?.ore_name || best?.item_id || null,
      best_ore_value: best?.total_value ?? null,
      sold_tracking_available: columnExists(db, "mining_inventory", "sold"),
    },
  };
}

function shortUserIdValue(value) {
  const s = String(value || "").trim();
  if (!s) return "";
  return s.length > 12 ? `${s.slice(0, 6)}…${s.slice(-4)}` : s;
}

function userIdentityLookup(db, query) {
  const q = String(query || "").trim().slice(0, 120);
  if (!q) return { query: q, matches: [] };
  const identities = new Map();
  const addIdentity = (row = {}, table = "", identity = "", notes = "") => {
    const username = row.username || row.seller_username || row.buyer_username || row.target_username || row.target_name || row.name || "";
    const userId = row.user_id || row.uid || row.highrise_user_id || row.target_user_id || row.requester_id || "";
    const key = userId ? `id:${userId}` : `name:${String(username).toLowerCase()}`;
    if (!userId && !username) return;
    const existing = identities.get(key) || {
      username: username || "Unknown Player",
      user_id: userId || "",
      short_user_id: shortUserIdValue(userId),
      aliases: new Set(),
      sources: [],
      stats: {},
      warnings: [],
    };
    if (username && existing.username === "Unknown Player") existing.username = username;
    if (userId && !existing.user_id) {
      existing.user_id = userId;
      existing.short_user_id = shortUserIdValue(userId);
    }
    if (username) existing.aliases.add(username);
    const found = existing.sources.find((source) => source.table === table && source.identity === identity);
    if (found) found.matches += 1;
    else existing.sources.push({ table, matches: 1, identity, notes });
    identities.set(key, existing);
  };

  const tableSpecs = [
    ["users", ["user_id", "username", "balance", "level", "xp", "last_seen_at", "last_seen"]],
    ["fish_profiles", ["user_id", "username", "fishing_level", "total_catches", "last_fish_at"]],
    ["mining_players", ["user_id", "username", "mining_level", "total_ores", "last_mine_at"]],
    ["fish_inventory", ["user_id", "username", "fish_name", "caught_at"]],
    ["mining_inventory", ["user_id", "username", "item_id"]],
    ["premium_balances", ["user_id", "username", "luxe_tickets", "updated_at"]],
    ["owned_items", ["user_id", "item_id", "item_type"]],
    ["user_titles", ["user_id", "username", "title_id"]],
    ["user_badges", ["username", "badge_id"]],
    ["badge_market_listings", ["seller_username", "buyer_username", "badge_id", "status"]],
    ["premium_transactions", ["user_id", "username", "type", "amount", "created_at"]],
    ["bot_command_queue", ["requester_id", "action", "status", "created_at"]],
  ];

  for (const [table, desired] of tableSpecs) {
    if (!tableExists(db, table)) continue;
    const cols = tableColumns(db, table);
    const clauses = [];
    const params = [];
    for (const col of ["user_id", "uid", "highrise_user_id", "requester_id", "target_user_id"]) {
      if (cols.includes(col)) {
        clauses.push(`${sqlIdent(col)}=?`);
        params.push(q);
      }
    }
    for (const col of ["username", "seller_username", "buyer_username", "target_username", "target_name"]) {
      if (cols.includes(col)) {
        clauses.push(`lower(${sqlIdent(col)})=lower(?)`);
        params.push(q);
      }
    }
    if (!clauses.length) continue;
    for (const row of safeRows(db, table, desired, { where: clauses.join(" OR "), params, limit: "100" })) {
      const identity = row.user_id || row.requester_id || row.seller_username || row.buyer_username || row.username || "";
      addIdentity(row, table, identity, cols.includes("user_id") ? "user_id/username match" : "username-only source");
    }
  }

  const usersById = new Map();
  if (tableExists(db, "users") && columnExists(db, "users", "user_id")) {
    for (const row of safeRows(db, "users", ["user_id", "username", "balance", "level", "xp", "last_seen_at", "last_seen"], { limit: "10000" })) {
      if (row.user_id) usersById.set(String(row.user_id), row);
    }
  }
  const matches = [...identities.values()].map((item) => {
    const userRow = item.user_id ? usersById.get(String(item.user_id)) : null;
    const profile = userRow || (item.username ? readPlayerProfile(db, item.username) : null);
    if (profile?.username && item.username === "Unknown Player") item.username = profile.username;
    const vip = item.user_id
      ? !!safeOne(db, "owned_items", ["user_id", "item_id"], { where: "user_id=? AND lower(item_id)='vip'", params: [item.user_id] })
      : false;
    return {
      ...item,
      aliases: [...item.aliases].filter(Boolean),
      stats: {
        balance: profile?.balance ?? userRow?.balance ?? null,
        level: profile?.level ?? userRow?.level ?? null,
        xp: profile?.xp ?? userRow?.xp ?? null,
        vip,
        last_seen: profile?.last_seen_at || profile?.last_seen || userRow?.last_seen_at || userRow?.last_seen || "",
      },
      warnings: item.user_id ? [] : ["No full user_id was found in the matched source tables."],
    };
  });
  return { query: q, matches };
}

app.get("/api/player/search", requireAuth, (req, res) => {
  const q = String(req.query.q || "").trim().slice(0, 80);
  if (!q) return json(res, { player: null, error: "query_required" }, 400);
  if (!tableExists(req.db, "users")) return json(res, { player: null, error: "users_table_missing" }, 404);
  const player = readPlayerProfile(req.db, q);
  if (!player) return json(res, { player: null });
  json(res, { player });
}, closeDb);

app.get("/api/users/lookup", requireAuth, requireAnyPermission("manage_players", "db_admin"), (req, res) => {
  if (req.user?.role !== "owner" && !req.permissions?.db_admin && !req.permissions?.manage_players) {
    return json(res, { error: "forbidden", permission: "manage_players" }, 403);
  }
  const q = String(req.query.q || "").trim().slice(0, 120);
  if (!q) return json(res, { query: "", matches: [], error: "query_required" }, 400);
  json(res, userIdentityLookup(req.db, q));
}, closeDb);

app.get("/api/player/:id/fishing-inventory", requireAuth, (req, res) => {
  const data = readPlayerFishingInventory(req.db, req.params.id, {
    sold: req.query.sold,
    rarity: req.query.rarity,
    q: req.query.q,
  });
  if (!data) return json(res, { error: "player_not_found" }, 404);
  json(res, data);
}, closeDb);

app.post("/api/player/:id/fishing-inventory", requireAuth, requireAnyPermission("manage_inventory"), (req, res) => {
  if (!playerInventoryWriteAllowed(req)) return json(res, { error: "forbidden", permission: "manage_inventory" }, 403);
  if (!tableExists(req.db, "fish_inventory")) return json(res, { error: "fish_inventory_missing" }, 404);
  const cols = tableColumns(req.db, "fish_inventory");
  if (!["fish_name", "username"].every((col) => cols.includes(col))) return unverifiedSchema(res, "fish_inventory requires username and fish_name for player inventory writes.");
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "player_not_found" }, 404);
  const reason = playerInventoryReason(req);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const fishId = String(req.body?.fish_id || "").trim();
  const catalog = fishId && tableExists(req.db, "fish_catalog") ? req.db.prepare("SELECT * FROM fish_catalog WHERE fish_id=?").get(fishId) : null;
  const payload = {
    user_id: player.user_id,
    username: player.username,
    fish_id: fishId || null,
    fish_name: String(req.body?.fish_name || catalog?.name || fishId || "").trim(),
    rarity: normalizeRarity(req.body?.rarity || catalog?.rarity || "common"),
    weight: req.body?.weight === "" || req.body?.weight == null ? null : Number(req.body.weight),
    value: req.body?.value === "" || req.body?.value == null ? Number(catalog?.base_value || 0) : Math.max(0, Math.trunc(Number(req.body.value))),
    sold: req.body?.sold === true || req.body?.sold === "1" ? 1 : 0,
    caught_at: new Date().toISOString(),
  };
  if (!payload.fish_name) return json(res, { error: "fish_name_required" }, 400);
  if ((payload.weight !== null && !Number.isFinite(payload.weight)) || !Number.isFinite(payload.value)) return json(res, { error: "invalid_number" }, 400);
  const insertCols = ["user_id","username","fish_id","fish_name","rarity","weight","value","sold","caught_at","created_at"].filter((col) => cols.includes(col));
  const values = insertCols.map((col) => col === "created_at" ? payload.caught_at : payload[col]);
  const info = req.db.prepare(`INSERT INTO fish_inventory (${insertCols.map(sqlIdent).join(", ")}) VALUES (${insertCols.map(() => "?").join(", ")})`).run(...values);
  const after = readPlayerFishingInventory(req.db, player.user_id, {});
  audit(req.db, req.user.username, "player_fishing_inventory_add", "fish_inventory", `${player.user_id}:${info.lastInsertRowid}`, null, { ...payload, reason }, req.ip);
  json(res, { ok: true, row_id: info.lastInsertRowid, ...after });
}, closeDb);

app.put("/api/player/:id/fishing-inventory/:row_id", requireAuth, requireAnyPermission("manage_inventory"), (req, res) => {
  if (!playerInventoryWriteAllowed(req)) return json(res, { error: "forbidden", permission: "manage_inventory" }, 403);
  if (!tableExists(req.db, "fish_inventory") || !columnExists(req.db, "fish_inventory", "id")) return unverifiedSchema(res, "fish_inventory.id is required for row edits.");
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "player_not_found" }, 404);
  const reason = playerInventoryReason(req);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const id = String(req.params.row_id || "");
  const before = req.db.prepare("SELECT * FROM fish_inventory WHERE id=?").get(id);
  if (!before) return json(res, { error: "fish_row_not_found" }, 404);
  const owns = String(before.user_id || "") === String(player.user_id || "") || String(before.username || "").toLowerCase() === String(player.username || "").toLowerCase();
  if (!owns) return json(res, { error: "row_not_for_player" }, 403);
  const cols = tableColumns(req.db, "fish_inventory");
  const allowed = {
    fish_name: req.body?.fish_name,
    rarity: req.body?.rarity === undefined ? undefined : normalizeRarity(req.body.rarity),
    weight: req.body?.weight === undefined || req.body?.weight === "" ? undefined : Number(req.body.weight),
    value: req.body?.value === undefined || req.body?.value === "" ? undefined : Math.max(0, Math.trunc(Number(req.body.value))),
    sold: req.body?.sold === undefined ? undefined : (req.body.sold === true || req.body.sold === "1" || req.body.sold === 1 ? 1 : 0),
    sold_at: req.body?.sold === undefined || !cols.includes("sold_at") ? undefined : ((req.body.sold === true || req.body.sold === "1" || req.body.sold === 1) ? new Date().toISOString() : null),
  };
  if ([allowed.weight, allowed.value].some((v) => v !== undefined && !Number.isFinite(v))) return json(res, { error: "invalid_number" }, 400);
  const updates = Object.entries(allowed).filter(([key, value]) => cols.includes(key) && value !== undefined);
  if (!updates.length) return json(res, { error: "no_verified_columns" }, 400);
  req.db.prepare(`UPDATE fish_inventory SET ${updates.map(([key]) => `${sqlIdent(key)}=?`).join(", ")} WHERE id=?`).run(...updates.map(([, value]) => value), id);
  const afterRow = req.db.prepare("SELECT * FROM fish_inventory WHERE id=?").get(id);
  audit(req.db, req.user.username, "player_fishing_inventory_update", "fish_inventory", `${player.user_id}:${id}`, before, { ...afterRow, reason }, req.ip);
  json(res, { ok: true, ...readPlayerFishingInventory(req.db, player.user_id, {}) });
}, closeDb);

app.delete("/api/player/:id/fishing-inventory/:row_id", requireAuth, requireAnyPermission("manage_inventory"), (req, res) => {
  if (!playerInventoryWriteAllowed(req)) return json(res, { error: "forbidden", permission: "manage_inventory" }, 403);
  if (!tableExists(req.db, "fish_inventory") || !columnExists(req.db, "fish_inventory", "id")) return unverifiedSchema(res, "fish_inventory.id is required for row removal.");
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "player_not_found" }, 404);
  const reason = playerInventoryReason(req);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  if (String(req.body?.confirmation || "") !== "REMOVE FISH") return json(res, { error: "typed_confirmation_required", confirmation: "REMOVE FISH" }, 400);
  const id = String(req.params.row_id || "");
  const before = req.db.prepare("SELECT * FROM fish_inventory WHERE id=?").get(id);
  if (!before) return json(res, { error: "fish_row_not_found" }, 404);
  const owns = String(before.user_id || "") === String(player.user_id || "") || String(before.username || "").toLowerCase() === String(player.username || "").toLowerCase();
  if (!owns) return json(res, { error: "row_not_for_player" }, 403);
  const info = req.db.prepare("DELETE FROM fish_inventory WHERE id=?").run(id);
  audit(req.db, req.user.username, "player_fishing_inventory_remove", "fish_inventory", `${player.user_id}:${id}`, before, { removed: info.changes, reason }, req.ip);
  json(res, { ok: true, removed: info.changes, ...readPlayerFishingInventory(req.db, player.user_id, {}) });
}, closeDb);

app.get("/api/player/:id/mining-inventory", requireAuth, (req, res) => {
  const data = readPlayerMiningInventory(req.db, req.params.id, {
    rarity: req.query.rarity,
    q: req.query.q,
  });
  if (!data) return json(res, { error: "player_not_found" }, 404);
  json(res, data);
}, closeDb);

app.post("/api/player/:id/mining-inventory", requireAuth, requireAnyPermission("manage_inventory"), (req, res) => {
  if (!playerInventoryWriteAllowed(req)) return json(res, { error: "forbidden", permission: "manage_inventory" }, 403);
  if (!tableExists(req.db, "mining_inventory")) return json(res, { error: "mining_inventory_missing" }, 404);
  const cols = tableColumns(req.db, "mining_inventory");
  if (!["username","item_id","quantity"].every((col) => cols.includes(col))) return unverifiedSchema(res, "mining_inventory requires username, item_id, and quantity for player inventory writes.");
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "player_not_found" }, 404);
  const reason = playerInventoryReason(req);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const itemId = String(req.body?.item_id || req.body?.ore_id || "").trim();
  const quantity = Math.max(1, Math.trunc(Number(req.body?.quantity || 1)));
  if (!itemId) return json(res, { error: "item_id_required" }, 400);
  if (!Number.isFinite(quantity)) return json(res, { error: "quantity_required" }, 400);
  const before = req.db.prepare("SELECT * FROM mining_inventory WHERE lower(username)=lower(?) AND item_id=?").get(player.username, itemId) || null;
  if (before) {
    req.db.prepare("UPDATE mining_inventory SET quantity=COALESCE(quantity,0)+? WHERE id=?").run(quantity, before.id);
  } else {
    const insertCols = ["user_id","username","item_id","quantity"].filter((col) => cols.includes(col));
    const values = insertCols.map((col) => col === "user_id" ? player.user_id : col === "username" ? player.username : col === "item_id" ? itemId : quantity);
    req.db.prepare(`INSERT INTO mining_inventory (${insertCols.map(sqlIdent).join(", ")}) VALUES (${insertCols.map(() => "?").join(", ")})`).run(...values);
  }
  const after = req.db.prepare("SELECT * FROM mining_inventory WHERE lower(username)=lower(?) AND item_id=?").get(player.username, itemId) || null;
  audit(req.db, req.user.username, "player_mining_inventory_add", "mining_inventory", `${player.username}:${itemId}`, before, { ...after, added_quantity: quantity, reason }, req.ip);
  json(res, { ok: true, ...readPlayerMiningInventory(req.db, player.user_id, {}) });
}, closeDb);

app.put("/api/player/:id/mining-inventory/:row_id", requireAuth, requireAnyPermission("manage_inventory"), (req, res) => {
  if (!playerInventoryWriteAllowed(req)) return json(res, { error: "forbidden", permission: "manage_inventory" }, 403);
  if (!tableExists(req.db, "mining_inventory") || !columnExists(req.db, "mining_inventory", "id")) return unverifiedSchema(res, "mining_inventory.id is required for row edits.");
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "player_not_found" }, 404);
  const reason = playerInventoryReason(req);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const id = String(req.params.row_id || "");
  const before = req.db.prepare("SELECT * FROM mining_inventory WHERE id=?").get(id);
  if (!before) return json(res, { error: "ore_row_not_found" }, 404);
  if (String(before.username || "").toLowerCase() !== String(player.username || "").toLowerCase()) return json(res, { error: "row_not_for_player" }, 403);
  const quantity = Math.max(0, Math.trunc(Number(req.body?.quantity)));
  if (!Number.isFinite(quantity)) return json(res, { error: "quantity_required" }, 400);
  req.db.prepare("UPDATE mining_inventory SET quantity=? WHERE id=?").run(quantity, id);
  const after = req.db.prepare("SELECT * FROM mining_inventory WHERE id=?").get(id);
  audit(req.db, req.user.username, "player_mining_inventory_update", "mining_inventory", `${player.username}:${id}`, before, { ...after, reason }, req.ip);
  json(res, { ok: true, ...readPlayerMiningInventory(req.db, player.user_id, {}) });
}, closeDb);

app.delete("/api/player/:id/mining-inventory/:row_id", requireAuth, requireAnyPermission("manage_inventory"), (req, res) => {
  if (!playerInventoryWriteAllowed(req)) return json(res, { error: "forbidden", permission: "manage_inventory" }, 403);
  if (!tableExists(req.db, "mining_inventory") || !columnExists(req.db, "mining_inventory", "id")) return unverifiedSchema(res, "mining_inventory.id is required for row removal.");
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "player_not_found" }, 404);
  const reason = playerInventoryReason(req);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const id = String(req.params.row_id || "");
  const before = req.db.prepare("SELECT * FROM mining_inventory WHERE id=?").get(id);
  if (!before) return json(res, { error: "ore_row_not_found" }, 404);
  if (String(before.username || "").toLowerCase() !== String(player.username || "").toLowerCase()) return json(res, { error: "row_not_for_player" }, 403);
  const removeQty = req.body?.quantity === undefined || req.body?.quantity === "" ? Number(before.quantity || 0) : Math.max(0, Math.trunc(Number(req.body.quantity)));
  if (!Number.isFinite(removeQty)) return json(res, { error: "quantity_required" }, 400);
  const nextQty = Math.max(0, Number(before.quantity || 0) - removeQty);
  req.db.prepare("UPDATE mining_inventory SET quantity=? WHERE id=?").run(nextQty, id);
  const after = req.db.prepare("SELECT * FROM mining_inventory WHERE id=?").get(id);
  audit(req.db, req.user.username, "player_mining_inventory_remove", "mining_inventory", `${player.username}:${id}`, before, { ...after, removed_quantity: removeQty, reason }, req.ip);
  json(res, { ok: true, ...readPlayerMiningInventory(req.db, player.user_id, {}) });
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
  const reason = String(req.body?.reason || "").trim();
  if (!itemId || !itemType) return json(res, { error: "item_id_and_type_required" }, 400);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const before = safeOne(req.db, "owned_items", ["user_id","item_id","item_type"], { where: "user_id=? AND item_id=?", params: [player.user_id, itemId] });
  req.db.prepare("INSERT OR IGNORE INTO owned_items (user_id, item_id, item_type) VALUES (?, ?, ?)").run(player.user_id, itemId, itemType);
  audit(req.db, req.user.username, "player_item_add", "owned_items", `${player.user_id}:${itemId}`, before, { item_id: itemId, item_type: itemType, reason }, req.ip);
  json(res, { ok: true, player: readPlayerProfile(req.db, player.user_id) });
}, closeDb);

app.get("/api/player/:id/rewards-inventory", requireAuth, requireAnyPermission("manage_rewards", "manage_inventory", "manage_players"), (req, res) => {
  const inventory = readPlayerRewardsInventory(req.db, req.params.id);
  if (!inventory) return json(res, { error: "not_found" }, 404);
  json(res, inventory);
}, closeDb);

app.post("/api/player/:id/owned-items", requireAuth, requireOwner, (req, res) => {
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "not_found" }, 404);
  if (!tableExists(req.db, "owned_items")) return json(res, { error: "owned_items_missing" }, 404);
  const itemId = String(req.body?.item_id || "").trim().slice(0, 120);
  const itemType = String(req.body?.item_type || "").trim().slice(0, 80);
  const reason = String(req.body?.reason || "").trim();
  if (!itemId || !itemType) return json(res, { error: "item_id_and_type_required" }, 400);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const before = safeOne(req.db, "owned_items", ["user_id","item_id","item_type"], { where: "user_id=? AND item_id=?", params: [player.user_id, itemId] });
  req.db.prepare("INSERT OR IGNORE INTO owned_items (user_id, item_id, item_type) VALUES (?, ?, ?)").run(player.user_id, itemId, itemType);
  audit(req.db, req.user.username, "player_owned_item_add", "owned_items", `${player.user_id}:${itemId}`, before, { item_id: itemId, item_type: itemType, reason }, req.ip);
  json(res, { ok: true, inventory: readPlayerRewardsInventory(req.db, player.user_id) });
}, closeDb);

app.delete("/api/player/:id/items/:item_id", requireAuth, requireOwner, (req, res) => {
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "not_found" }, 404);
  if (!tableExists(req.db, "owned_items")) return json(res, { error: "owned_items_missing" }, 404);
  const itemId = String(req.params.item_id || "").trim();
  const reason = String(req.body?.reason || "").trim();
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const before = safeOne(req.db, "owned_items", ["user_id","item_id","item_type"], { where: "user_id=? AND item_id=?", params: [player.user_id, itemId] });
  const info = req.db.prepare("DELETE FROM owned_items WHERE user_id=? AND item_id=?").run(player.user_id, itemId);
  audit(req.db, req.user.username, "player_item_remove", "owned_items", `${player.user_id}:${itemId}`, before, { removed: info.changes, reason }, req.ip);
  json(res, { ok: true, removed: info.changes, player: readPlayerProfile(req.db, player.user_id) });
}, closeDb);

app.delete("/api/player/:id/owned-items/:item_id", requireAuth, requireOwner, (req, res) => {
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "not_found" }, 404);
  if (!tableExists(req.db, "owned_items")) return json(res, { error: "owned_items_missing" }, 404);
  const itemId = String(req.params.item_id || "").trim();
  const reason = String(req.body?.reason || "").trim();
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const before = safeOne(req.db, "owned_items", ["user_id","item_id","item_type"], { where: "user_id=? AND item_id=?", params: [player.user_id, itemId] });
  const info = req.db.prepare("DELETE FROM owned_items WHERE user_id=? AND item_id=?").run(player.user_id, itemId);
  audit(req.db, req.user.username, "player_owned_item_remove", "owned_items", `${player.user_id}:${itemId}`, before, { removed: info.changes, reason }, req.ip);
  json(res, { ok: true, removed: info.changes, inventory: readPlayerRewardsInventory(req.db, player.user_id) });
}, closeDb);

app.post("/api/player/:id/titles", requireAuth, requireOwner, (req, res) => {
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "not_found" }, 404);
  if (!tableExists(req.db, "user_titles")) return json(res, { error: "user_titles_missing" }, 404);
  const titleId = String(req.body?.title_id || "").trim().slice(0, 120);
  const reason = String(req.body?.reason || "").trim();
  if (!titleId) return json(res, { error: "title_id_required" }, 400);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  req.db.prepare("INSERT OR IGNORE INTO user_titles (user_id, username, title_id, source) VALUES (?, ?, ?, 'dashboard')").run(player.user_id, player.username, titleId);
  audit(req.db, req.user.username, "player_title_add", "user_titles", `${player.user_id}:${titleId}`, "", { title_id: titleId, reason }, req.ip);
  json(res, { ok: true, player: readPlayerProfile(req.db, player.user_id) });
}, closeDb);

app.delete("/api/player/:id/titles/:title_id", requireAuth, requireOwner, (req, res) => {
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "not_found" }, 404);
  const titleId = String(req.params.title_id || "").trim();
  const reason = String(req.body?.reason || "").trim();
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const before = safeOne(req.db, "user_titles", ["user_id","username","title_id","source","unlocked_at"], { where: "user_id=? AND title_id=?", params: [player.user_id, titleId] });
  const info = tableExists(req.db, "user_titles") ? req.db.prepare("DELETE FROM user_titles WHERE user_id=? AND title_id=?").run(player.user_id, titleId) : { changes: 0 };
  audit(req.db, req.user.username, "player_title_remove", "user_titles", `${player.user_id}:${titleId}`, before, { removed: info.changes, reason }, req.ip);
  json(res, { ok: true, removed: info.changes, player: readPlayerProfile(req.db, player.user_id) });
}, closeDb);

app.post("/api/player/:id/equip-title", requireAuth, requireOwner, (req, res) => {
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "not_found" }, 404);
  const titleId = String(req.body?.title_id || "").trim().slice(0, 120);
  const reason = String(req.body?.reason || "").trim();
  if (!titleId) return json(res, { error: "title_id_required" }, 400);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const cols = tableExists(req.db, "users") ? tableColumns(req.db, "users") : [];
  const targetCol = cols.includes("equipped_title_id") ? "equipped_title_id" : (cols.includes("equipped_title") ? "equipped_title" : "");
  if (!targetCol) return unverifiedSchema(res, "users equipped title column was not found.");
  const before = readPlayerProfile(req.db, player.user_id);
  req.db.prepare(`UPDATE users SET ${sqlIdent(targetCol)}=? WHERE user_id=?`).run(titleId, player.user_id);
  audit(req.db, req.user.username, "player_title_equip", "users", player.user_id, before, { title_id: titleId, reason }, req.ip);
  json(res, { ok: true, player: readPlayerProfile(req.db, player.user_id) });
}, closeDb);

app.post("/api/player/:id/badges", requireAuth, requireOwner, (req, res) => {
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "not_found" }, 404);
  if (!tableExists(req.db, "user_badges")) return json(res, { error: "user_badges_missing" }, 404);
  const badgeId = String(req.body?.badge_id || "").trim().slice(0, 120);
  const reason = String(req.body?.reason || "").trim();
  if (!badgeId) return json(res, { error: "badge_id_required" }, 400);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  req.db.prepare("INSERT OR IGNORE INTO user_badges (username, badge_id, acquired_at, source) VALUES (?, ?, CURRENT_TIMESTAMP, 'dashboard')").run(player.username, badgeId);
  audit(req.db, req.user.username, "player_badge_add", "user_badges", `${player.username}:${badgeId}`, "", { badge_id: badgeId, reason }, req.ip);
  json(res, { ok: true, player: readPlayerProfile(req.db, player.user_id) });
}, closeDb);

app.delete("/api/player/:id/badges/:badge_id", requireAuth, requireOwner, (req, res) => {
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "not_found" }, 404);
  const badgeId = String(req.params.badge_id || "").trim();
  const reason = String(req.body?.reason || "").trim();
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const before = safeOne(req.db, "user_badges", ["id","username","badge_id","acquired_at","source","equipped","locked"], { where: "lower(username)=lower(?) AND badge_id=?", params: [player.username, badgeId] });
  const info = tableExists(req.db, "user_badges") ? req.db.prepare("DELETE FROM user_badges WHERE lower(username)=lower(?) AND badge_id=?").run(player.username, badgeId) : { changes: 0 };
  audit(req.db, req.user.username, "player_badge_remove", "user_badges", `${player.username}:${badgeId}`, before, { removed: info.changes, reason }, req.ip);
  json(res, { ok: true, removed: info.changes, player: readPlayerProfile(req.db, player.user_id) });
}, closeDb);

app.post("/api/player/:id/equip-badge", requireAuth, requireOwner, (req, res) => {
  const player = readPlayerProfile(req.db, req.params.id);
  if (!player) return json(res, { error: "not_found" }, 404);
  const badgeId = String(req.body?.badge_id || "").trim().slice(0, 120);
  const reason = String(req.body?.reason || "").trim();
  if (!badgeId) return json(res, { error: "badge_id_required" }, 400);
  if (!reason) return json(res, { error: "reason_required" }, 400);
  const before = {
    player: readPlayerProfile(req.db, player.user_id),
    badges: safeRows(req.db, "user_badges", ["id", "username", "badge_id", "equipped"], { where: "lower(username)=lower(?)", params: [player.username], limit: "200" }),
  };
  if (tableExists(req.db, "user_badges") && columnExists(req.db, "user_badges", "equipped")) {
    req.db.prepare("UPDATE user_badges SET equipped=0 WHERE lower(username)=lower(?)").run(player.username);
    req.db.prepare("UPDATE user_badges SET equipped=1 WHERE lower(username)=lower(?) AND badge_id=?").run(player.username, badgeId);
  }
  const cols = tableExists(req.db, "users") ? tableColumns(req.db, "users") : [];
  const targetCol = cols.includes("equipped_badge_id") ? "equipped_badge_id" : (cols.includes("equipped_badge") ? "equipped_badge" : "");
  if (targetCol) req.db.prepare(`UPDATE users SET ${sqlIdent(targetCol)}=? WHERE user_id=?`).run(badgeId, player.user_id);
  if (!targetCol && !(tableExists(req.db, "user_badges") && columnExists(req.db, "user_badges", "equipped"))) return unverifiedSchema(res, "No verified equipped badge column was found.");
  audit(req.db, req.user.username, "player_badge_equip", "users", player.user_id, before, { badge_id: badgeId, reason }, req.ip);
  json(res, { ok: true, player: readPlayerProfile(req.db, player.user_id) });
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
  "event_reminder",
  "promo_message",
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
  "warn_user",
  "mute_user",
  "unmute_user",
  "jail_user",
  "unjail_user",
  "security_alert",
]);

app.post("/api/bot-command", requireAuth, requireAnyPermission("emergency_controls","manage_radio","manage_games","manage_room","manage_events","manage_automation","manage_emotes","manage_bots","manage_moderation"), (req, res) => {
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

app.get("/api/bot-command-queue", requireAuth, requireAnyPermission("manage_bots","manage_radio","manage_room","manage_events","manage_automation","manage_emotes","view_logs"), (req, res) => {
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

app.post("/api/bot-command-queue/:id/review", requireAuth, requireOwner, (req, res) => {
  const id = Number(req.params.id);
  if (!Number.isFinite(id) || id <= 0) return json(res, { error: "invalid_command_id" }, 400);
  if (!tableExists(req.db, "bot_command_queue")) return json(res, { error: "bot_command_queue_missing" }, 404);
  const existing = req.db.prepare("SELECT * FROM bot_command_queue WHERE id=?").get(id);
  if (!existing) return json(res, { error: "command_not_found" }, 404);
  if (!["failed", "error", "unknown_action"].includes(String(existing.status || "").toLowerCase())) {
    return json(res, { error: "not_failed_command", message: "Only failed command rows can be marked reviewed." }, 400);
  }
  const assignments = ["status='reviewed'"];
  const params = [];
  if (columnExists(req.db, "bot_command_queue", "reviewed_at")) assignments.push("reviewed_at=CURRENT_TIMESTAMP");
  if (columnExists(req.db, "bot_command_queue", "reviewed_by")) {
    assignments.push("reviewed_by=?");
    params.push(req.user.username);
  }
  params.push(id);
  req.db.prepare(`UPDATE bot_command_queue SET ${assignments.join(", ")} WHERE id=?`).run(...params);
  const updated = req.db.prepare("SELECT * FROM bot_command_queue WHERE id=?").get(id);
  audit(req.db, req.user.username, "bot_command_reviewed", "bot_command_queue", id, existing, updated, req.ip);
  json(res, { ok: true, command: updated });
}, closeDb);

app.get("/api/automation", requireAuth, requireAnyPermission("manage_automation", "manage_room", "manage_events", "view_logs"), (req, res) => {
  json(res, readAutomationDashboard(req.db));
}, closeDb);

app.get("/api/automation/announcements", requireAuth, requireAnyPermission("manage_automation", "manage_room", "manage_events", "view_logs"), (req, res) => {
  const data = readAutomationDashboard(req.db);
  json(res, { announcements: data.scheduled_announcements, overview: data.overview, table_status: data.table_status, scheduler_note: data.scheduler_note });
}, closeDb);

app.post("/api/automation/announcements", requireAuth, requireOwner, (req, res) => {
  try {
    const title = String(req.body?.title || "").trim().slice(0, 160);
    const message = cleanAutomationMessage(req.body?.message);
    const targetBot = String(req.body?.target_bot || "host").trim().slice(0, 80) || "host";
    const scheduleType = String(req.body?.schedule_type || "manual").trim().slice(0, 40) || "manual";
    const intervalMinutes = req.body?.interval_minutes === "" || req.body?.interval_minutes == null ? null : Math.max(1, Math.min(43200, Math.trunc(Number(req.body.interval_minutes))));
    const nextRunAt = String(req.body?.next_run_at || "").trim();
    const enabled = req.body?.enabled === false || req.body?.enabled === "false" || req.body?.enabled === "0" ? 0 : 1;
    const info = req.db.prepare(`
      INSERT INTO dashboard_scheduled_announcements
        (title, message, target_bot, schedule_type, interval_minutes, next_run_at, enabled, created_by, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    `).run(title, message, targetBot, scheduleType, intervalMinutes, nextRunAt, enabled, req.user.username);
    audit(req.db, req.user.username, "automation_announcement_create", "dashboard_scheduled_announcements", info.lastInsertRowid, "", { title, targetBot, scheduleType, intervalMinutes, nextRunAt, enabled }, req.ip);
    json(res, { ok: true, id: info.lastInsertRowid });
  } catch (err) {
    json(res, { error: err.message || "create_failed" }, 400);
  }
}, closeDb);

app.put("/api/automation/announcements/:id", requireAuth, requireOwner, (req, res) => {
  const id = String(req.params.id || "").trim();
  const old = safeOne(req.db, "dashboard_scheduled_announcements", tableColumns(req.db, "dashboard_scheduled_announcements"), { where: "id=?", params: [id] });
  if (!old) return json(res, { error: "not_found" }, 404);
  try {
    const updates = [];
    const values = [];
    const add = (col, value) => { updates.push(`${sqlIdent(col)}=?`); values.push(value); };
    if (req.body?.title !== undefined) add("title", String(req.body.title || "").trim().slice(0, 160));
    if (req.body?.message !== undefined) add("message", cleanAutomationMessage(req.body.message));
    if (req.body?.target_bot !== undefined) add("target_bot", String(req.body.target_bot || "host").trim().slice(0, 80) || "host");
    if (req.body?.schedule_type !== undefined) add("schedule_type", String(req.body.schedule_type || "manual").trim().slice(0, 40) || "manual");
    if (req.body?.interval_minutes !== undefined) add("interval_minutes", req.body.interval_minutes === "" || req.body.interval_minutes == null ? null : Math.max(1, Math.min(43200, Math.trunc(Number(req.body.interval_minutes)))));
    if (req.body?.next_run_at !== undefined) add("next_run_at", String(req.body.next_run_at || "").trim());
    if (req.body?.enabled !== undefined) add("enabled", req.body.enabled === true || req.body.enabled === "true" || req.body.enabled === "1" ? 1 : 0);
    if (req.body?.archived !== undefined) add("archived", req.body.archived === true || req.body.archived === "true" || req.body.archived === "1" ? 1 : 0);
    updates.push("updated_at=CURRENT_TIMESTAMP");
    if (!updates.length) return json(res, { error: "no_updates" }, 400);
    values.push(id);
    req.db.prepare(`UPDATE dashboard_scheduled_announcements SET ${updates.join(", ")} WHERE id=?`).run(...values);
    const row = safeOne(req.db, "dashboard_scheduled_announcements", tableColumns(req.db, "dashboard_scheduled_announcements"), { where: "id=?", params: [id] });
    audit(req.db, req.user.username, "automation_announcement_update", "dashboard_scheduled_announcements", id, old, row, req.ip);
    json(res, { ok: true, row });
  } catch (err) {
    json(res, { error: err.message || "update_failed" }, 400);
  }
}, closeDb);

app.delete("/api/automation/announcements/:id", requireAuth, requireOwner, (req, res) => {
  const id = String(req.params.id || "").trim();
  const old = safeOne(req.db, "dashboard_scheduled_announcements", tableColumns(req.db, "dashboard_scheduled_announcements"), { where: "id=?", params: [id] });
  if (!old) return json(res, { error: "not_found" }, 404);
  const hard = req.body?.hard === true || req.body?.hard === "true";
  if (hard) {
    if (String(req.body?.confirmation || "").trim() !== "DELETE ANNOUNCEMENT") return json(res, { error: "confirmation_required", required: "DELETE ANNOUNCEMENT" }, 400);
    req.db.prepare("DELETE FROM dashboard_scheduled_announcements WHERE id=?").run(id);
    audit(req.db, req.user.username, "automation_announcement_hard_delete", "dashboard_scheduled_announcements", id, old, { hard: true }, req.ip);
    return json(res, { ok: true, deleted: true });
  }
  req.db.prepare("UPDATE dashboard_scheduled_announcements SET archived=1, enabled=0, updated_at=CURRENT_TIMESTAMP WHERE id=?").run(id);
  audit(req.db, req.user.username, "automation_announcement_archive", "dashboard_scheduled_announcements", id, old, { archived: true }, req.ip);
  json(res, { ok: true, archived: true });
}, closeDb);

app.post("/api/automation/announcements/:id/send-now", requireAuth, requireAnyPermission("manage_automation", "manage_room", "manage_events", "emergency_controls"), (req, res) => {
  const id = String(req.params.id || "").trim();
  const row = safeOne(req.db, "dashboard_scheduled_announcements", tableColumns(req.db, "dashboard_scheduled_announcements"), { where: "id=?", params: [id] });
  if (!row) return json(res, { error: "not_found" }, 404);
  try {
    const message = cleanAutomationMessage(row.message);
    const targetBot = String(row.target_bot || "host").trim() || "host";
    const queued = enqueueBotCommand(req.db, { targetBot, actionName: "announce", payload: { message, source: "automation", announcement_id: row.id, title: row.title || "" }, requesterId: req.user.username });
    req.db.prepare("UPDATE dashboard_scheduled_announcements SET last_sent_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?").run(id);
    audit(req.db, req.user.username, "automation_announcement_send_now", "bot_command_queue", queued.id, "", { announcement_id: id, targetBot, message }, req.ip);
    json(res, { ok: true, command: queued, message: "Command queued. Bot must consume bot_command_queue." });
  } catch (err) {
    json(res, { error: err.message || "send_failed" }, 400);
  }
}, closeDb);

app.get("/api/automation/rotating", requireAuth, requireAnyPermission("manage_automation", "manage_room", "manage_events", "view_logs"), (req, res) => {
  const data = readAutomationDashboard(req.db);
  json(res, { rows: data.rotating_announcements, table: data.tables.rotating_announcements, overview: data.overview });
}, closeDb);

app.post("/api/automation/rotating", requireAuth, requireOwner, (req, res) => {
  if (!tableExists(req.db, "rotating_announcements")) return json(res, { error: "missing_table", message: "rotating_announcements is not present." }, 400);
  const cols = tableColumns(req.db, "rotating_announcements");
  const messageCol = ["message", "text", "body", "content"].find((col) => cols.includes(col));
  if (!messageCol) return json(res, { error: "unverified_schema", message: "No message/text/body/content column found." }, 400);
  try {
    const message = cleanAutomationMessage(req.body?.message);
    const insertCols = [messageCol];
    const values = [message];
    if (cols.includes("enabled")) { insertCols.push("enabled"); values.push("1"); }
    if (cols.includes("created_by")) { insertCols.push("created_by"); values.push(req.user.username); }
    if (cols.includes("set_by")) { insertCols.push("set_by"); values.push(req.user.username); }
    if (cols.includes("created_at")) insertCols.push("created_at");
    const placeholders = insertCols.map((col) => col === "created_at" ? "CURRENT_TIMESTAMP" : "?").join(", ");
    const info = req.db.prepare(`INSERT INTO rotating_announcements (${insertCols.map(sqlIdent).join(", ")}) VALUES (${placeholders})`).run(...values);
    audit(req.db, req.user.username, "automation_rotating_create", "rotating_announcements", info.lastInsertRowid, "", { message }, req.ip);
    json(res, { ok: true, id: info.lastInsertRowid });
  } catch (err) {
    json(res, { error: err.message || "create_failed" }, 400);
  }
}, closeDb);

app.put("/api/automation/rotating/:id", requireAuth, requireOwner, (req, res) => {
  if (!tableExists(req.db, "rotating_announcements")) return json(res, { error: "missing_table" }, 400);
  const cols = tableColumns(req.db, "rotating_announcements");
  if (!cols.includes("id")) return json(res, { error: "unverified_schema", message: "rotating_announcements.id is required." }, 400);
  const id = String(req.params.id || "").trim();
  const old = req.db.prepare("SELECT * FROM rotating_announcements WHERE id=? LIMIT 1").get(id);
  if (!old) return json(res, { error: "not_found" }, 404);
  const updates = [];
  const values = [];
  try {
    const messageCol = ["message", "text", "body", "content"].find((col) => cols.includes(col));
    if (messageCol && req.body?.message !== undefined) {
      updates.push(`${sqlIdent(messageCol)}=?`);
      values.push(cleanAutomationMessage(req.body.message));
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
    audit(req.db, req.user.username, "automation_rotating_update", "rotating_announcements", id, old, next, req.ip);
    json(res, { ok: true, row: next });
  } catch (err) {
    json(res, { error: err.message || "update_failed" }, 400);
  }
}, closeDb);

app.post("/api/automation/send", requireAuth, requireAnyPermission("manage_automation", "manage_room", "manage_events", "emergency_controls"), (req, res) => {
  try {
    const message = cleanAutomationMessage(req.body?.message);
    const targetBot = String(req.body?.target_bot || "host").trim().slice(0, 80) || "host";
    const source = String(req.body?.source || "automation").trim().slice(0, 80) || "automation";
    const queued = enqueueBotCommand(req.db, { targetBot, actionName: "announce", payload: { message, source }, requesterId: req.user.username });
    audit(req.db, req.user.username, "automation_send_now", "bot_command_queue", queued.id, "", { targetBot, source, message }, req.ip);
    json(res, { ok: true, command: queued, message: "Command queued. Bot must consume bot_command_queue." });
  } catch (err) {
    json(res, { error: err.message || "send_failed" }, 400);
  }
}, closeDb);

app.get("/api/room/welcome", requireAuth, requireAnyPermission("manage_room", "view_logs", "emergency_controls"), (req, res) => {
  const settings = readKeyValueMap(req.db, "room_settings");
  const seenCols = tableExists(req.db, "room_welcome_seen") ? tableColumns(req.db, "room_welcome_seen") : [];
  const seenOrder = seenCols.includes("seen_at") ? "seen_at DESC" : seenCols.includes("created_at") ? "created_at DESC" : "";
  json(res, {
    source: "room_settings",
    table_exists: tableExists(req.db, "room_settings"),
    settings: {
      welcome_enabled: settings.welcome_enabled ?? "true",
      welcome_message: settings.welcome_message ?? "",
      first_time_welcome: settings.first_time_welcome ?? "",
      returning_welcome: settings.returning_welcome ?? "",
    },
    seen: safeTableRows(req.db, "room_welcome_seen", { orderBy: seenOrder, limit: "50" }),
  });
}, closeDb);

app.put("/api/room/welcome", requireAuth, requireAnyPermission("manage_room", "emergency_controls"), (req, res) => {
  if (!tableExists(req.db, "room_settings")) return json(res, { error: "room_settings_missing" }, 404);
  const updates = {};
  if (Object.prototype.hasOwnProperty.call(req.body || {}, "welcome_message")) {
    const value = String(req.body.welcome_message ?? "").trim().slice(0, 200);
    if (!value) return json(res, { error: "welcome_message_required" }, 400);
    updates.welcome_message = value;
  }
  if (Object.prototype.hasOwnProperty.call(req.body || {}, "welcome_enabled")) {
    updates.welcome_enabled = boolFromSetting(req.body.welcome_enabled, false) ? "true" : "false";
  }
  if (!Object.keys(updates).length) return json(res, { error: "no_supported_settings" }, 400);
  const before = readKeyValueMap(req.db, "room_settings");
  for (const [key, value] of Object.entries(updates)) writeKeyValue(req.db, "room_settings", key, value);
  const after = readKeyValueMap(req.db, "room_settings");
  audit(req.db, req.user.username, "room_welcome_update", "room_settings", Object.keys(updates).join(","), before, updates, req.ip);
  json(res, { ok: true, source: "room_settings", settings: { welcome_enabled: after.welcome_enabled ?? "true", welcome_message: after.welcome_message ?? "" } });
}, closeDb);

app.get("/api/emotes/timing", requireAuth, requireAnyPermission("emergency_controls", "manage_emotes", "view_logs"), (req, res) => {
  const settings = readKeyValueMap(req.db, "room_settings");
  let overrides = {};
  try {
    overrides = JSON.parse(settings.emote_timing_overrides || "{}") || {};
  } catch {
    overrides = {};
  }
  json(res, {
    source: "room_settings",
    table_exists: tableExists(req.db, "room_settings"),
    loop_interval_seconds: settings.emote_loop_interval_seconds ?? "30",
    overrides,
    rows: Object.entries(overrides).map(([alias, seconds]) => ({ alias, seconds })),
  });
}, closeDb);

app.put("/api/emotes/timing", requireAuth, requireAnyPermission("emergency_controls", "manage_emotes"), (req, res) => {
  if (!tableExists(req.db, "room_settings")) return json(res, { error: "room_settings_missing" }, 404);
  const before = readKeyValueMap(req.db, "room_settings");
  const updates = {};
  if (Object.prototype.hasOwnProperty.call(req.body || {}, "loop_interval_seconds")) {
    const sec = Number(req.body.loop_interval_seconds);
    if (!Number.isFinite(sec) || !Number.isInteger(sec) || sec < 3 || sec > 3600) return json(res, { error: "loop_interval_seconds_invalid" }, 400);
    updates.emote_loop_interval_seconds = String(sec);
  }
  const alias = String(req.body?.alias || "").trim().toLowerCase();
  if (alias) {
    if (!/^[a-z0-9_.:-]{1,80}$/.test(alias)) return json(res, { error: "bad_alias" }, 400);
    const seconds = Number(req.body?.seconds);
    if (!Number.isFinite(seconds) || seconds <= 0 || seconds > 3600) return json(res, { error: "seconds_invalid" }, 400);
    let overrides = {};
    try {
      overrides = JSON.parse(before.emote_timing_overrides || "{}") || {};
    } catch {
      overrides = {};
    }
    overrides[alias] = seconds;
    updates.emote_timing_overrides = JSON.stringify(overrides);
  }
  if (!Object.keys(updates).length) return json(res, { error: "no_supported_settings" }, 400);
  for (const [key, value] of Object.entries(updates)) writeKeyValue(req.db, "room_settings", key, value);
  const after = readKeyValueMap(req.db, "room_settings");
  let overrides = {};
  try { overrides = JSON.parse(after.emote_timing_overrides || "{}") || {}; } catch {}
  audit(req.db, req.user.username, "emote_timing_update", "room_settings", Object.keys(updates).join(","), before, updates, req.ip);
  json(res, { ok: true, source: "room_settings", loop_interval_seconds: after.emote_loop_interval_seconds ?? "30", overrides });
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

app.post("/api/room/announce", requireAuth, requireAnyPermission("emergency_controls","manage_room","manage_events","manage_automation"), (req, res) => {
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
