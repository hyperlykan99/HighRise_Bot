const NAV = [
  { id: "Overview",     icon: "📊", label: "Overview",     group: "main" },
  { id: "Radio",        icon: "🎵", label: "Radio",        group: "main" },
  { id: "Live Tracker", icon: "📡", label: "Live Tracker", group: "main" },
  { id: "Casino",       icon: "🎲", label: "Casino",       group: "games" },
  { id: "Games",        icon: "🎮", label: "Games",        group: "games" },
  { id: "Titles",       icon: "🏅", label: "Titles",       group: "games" },
  { id: "Staff",        icon: "👥", label: "Staff",        group: "admin" },
  { id: "Bot Config",   icon: "⚙️", label: "Bot Config",   group: "admin" },
  { id: "Settings",     icon: "🔧", label: "Settings",     group: "admin" },
  { id: "Logs",         icon: "📋", label: "Logs",         group: "admin" },
  { id: "Emergency",    icon: "🚨", label: "Emergency",    group: "admin" },
];

const NAV_IDS = NAV.map((n) => n.id);

const API_FOR_PAGE = {
  Overview:      "/api/overview",
  Radio:         "/api/radio",
  Casino:        "/api/casino",
  Games:         "/api/games",
  Titles:        "/api/titles",
  Staff:         "/api/staff",
  "Bot Config":  "/api/bot-config",
  Settings:      "/api/settings",
  "Live Tracker":"/api/live",
  Logs:          "/api/logs",
  Emergency:     "/api/settings",
};

const PAGE_DESC = {
  Overview:      "Room status, bot health and quick metrics",
  Radio:         "Manage the DJ queue and radio stream",
  "Live Tracker":"Real-time bot heartbeats and room status",
  Casino:        "Blackjack, Poker and casino settings",
  Games:         "Trivia, Scramble and game settings",
  Titles:        "Assign and manage player titles",
  Staff:         "Dashboard users and bot role management",
  "Bot Config":  "Bot tokens, ROOM_ID and restart controls",
  Settings:      "Global bot and room settings",
  Logs:          "Audit trail and command error logs",
  Emergency:     "Quick disable switches for critical systems",
};

const state = {
  user: null,
  csrf: "",
  page: (() => {
    const h = location.hash ? decodeURIComponent(location.hash.slice(1)) : "";
    return NAV_IDS.includes(h) ? h : "Overview";
  })(),
  data: null,
  error: "",
  notice: "",
  logs: { action_type: "", user: "", module: "", offset: 0 },
  modal: null,
  sidebarOpen: false,
};

const app = document.getElementById("app");

function esc(v) {
  return String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

function can(permission) {
  return state.user?.role === "owner" || !!state.user?.permissions?.[permission];
}

async function api(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (!["GET", "HEAD"].includes(String(options.method || "GET").toUpperCase()) && state.csrf) {
    headers["X-CSRF-Token"] = state.csrf;
  }
  const res = await fetch(path, { credentials: "include", ...options, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function pill(value, goodValues = ["online", "enabled", "ready", "queued", "submitted", "set"]) {
  const text = String(value ?? "");
  const lower = text.toLowerCase();
  let cls = "def";
  if (goodValues.includes(lower) || value === 1 || value === true) cls = "ok";
  else if (lower === "empty" || lower === "off" || lower.includes("disab") || value === 0 || value === false) cls = "bad";
  else if (lower === "warn" || lower === "playing" || lower === "pending") cls = "warn";
  else if (lower === "unknown") cls = "def";
  return `<span class="pill ${cls}">${esc(text || "unknown")}</span>`;
}

function table(rows, columns, actions) {
  if (!rows || rows.length === 0) return `<div class="notice">No data available.</div>`;
  const cols = columns || Object.keys(rows[0]).slice(0, 8).map((key) => ({ key, label: key }));
  return `<div class="table-wrap"><table>
    <thead><tr>${cols.map((c) => `<th>${esc(c.label)}</th>`).join("")}${actions ? "<th></th>" : ""}</tr></thead>
    <tbody>${rows.map((row) => `<tr>${cols.map((c) => `<td>${c.render ? c.render(row) : esc(row[c.key])}</td>`).join("")}${actions ? `<td class="inline-actions">${actions(row)}</td>` : ""}</tr>`).join("")}</tbody>
  </table></div>`;
}

function metricCard(label, value, hint = "", accentClass = "", icon = "") {
  return `<div class="metric-card ${accentClass}">
    ${icon ? `<div class="metric-icon">${icon}</div>` : ""}
    <div class="metric-value">${esc(String(value ?? "—"))}</div>
    <div class="metric-label">${esc(label)}</div>
    ${hint ? `<div class="metric-hint">${esc(hint)}</div>` : ""}
  </div>`;
}

async function load() {
  const page = NAV_IDS.includes(state.page) ? state.page : "Overview";
  state.page = page;
  location.hash = encodeURIComponent(page);
  try {
    const url = page === "Logs" ? logsUrl() : API_FOR_PAGE[page];
    state.data = await api(url);
    state.error = "";
  } catch (err) {
    state.data = null;
    state.error = err.message;
  }
  render();
}

function logsUrl() {
  const p = new URLSearchParams();
  for (const key of ["action_type", "user", "module"]) {
    if (state.logs[key]) p.set(key, state.logs[key]);
  }
  p.set("offset", String(state.logs.offset || 0));
  p.set("limit", "50");
  return `/api/logs?${p.toString()}`;
}

async function init() {
  try {
    const me = await api("/api/auth/me");
    state.user = me.user;
    state.csrf = me.csrf_token || "";
    await load();
  } catch {
    render();
  }
  setInterval(() => {
    if (state.user && ["Overview", "Radio", "Live Tracker"].includes(state.page)) load();
  }, 8000);
}

async function login(event) {
  event.preventDefault();
  const body = Object.fromEntries(new FormData(event.currentTarget));
  try {
    const result = await api("/api/auth/login", { method: "POST", body: JSON.stringify(body) });
    state.user = result.user;
    state.csrf = result.csrf_token || "";
    state.notice = "Signed in.";
    await load();
  } catch (err) {
    state.error = err.message;
    render();
  }
}

async function logout() {
  await api("/api/auth/logout", { method: "POST" }).catch(() => {});
  state.user = null;
  state.csrf = "";
  state.data = null;
  render();
}

async function action(label, fn) {
  try {
    await fn();
    state.notice = label;
    state.error = "";
    await load();
  } catch (err) {
    state.error = err.message;
    state.notice = "";
    render();
  }
}

function confirmAction(title, body, fn) {
  state.modal = { title, body, fn };
  render();
}

async function runModalAction() {
  const fn = state.modal?.fn;
  state.modal = null;
  if (fn) await fn();
}

function renderLogin() {
  app.innerHTML = `<div class="login-shell">
    <form class="login-card" id="loginForm">
      <div class="login-logo">
        <div class="login-mark">CT</div>
        <div class="login-logo-text">
          <strong>ChillTopia</strong>
          <span>Admin Control Panel</span>
        </div>
      </div>
      <h1>Welcome back</h1>
      <p class="subtitle">Sign in to manage your room and bots</p>
      ${state.error ? `<div class="notice error" style="margin-bottom:14px">${esc(state.error)}</div>` : ""}
      <div class="field">
        <label class="field-label">Username</label>
        <input name="username" autocomplete="username" placeholder="Enter username" required />
      </div>
      <div class="field" style="margin-bottom:20px">
        <label class="field-label">Password</label>
        <input name="password" type="password" autocomplete="current-password" placeholder="••••••••" required />
      </div>
      <button class="btn primary" type="submit" style="width:100%;justify-content:center;padding:12px">Log in</button>
    </form>
  </div>`;
  document.getElementById("loginForm").addEventListener("submit", login);
}

function buildNav() {
  const groups = { main: "Monitor", games: "Content", admin: "Admin" };
  let html = "";
  let lastGroup = null;
  for (const item of NAV) {
    if (item.group !== lastGroup) {
      html += `<div class="nav-label" style="margin-top:${lastGroup ? '14px' : '0'}">${groups[item.group]}</div>`;
      lastGroup = item.group;
    }
    html += `<button class="${item.id === state.page ? "active" : ""}" data-page="${esc(item.id)}">
      <span class="nav-icon">${item.icon}</span>${esc(item.label)}
    </button>`;
  }
  return html;
}

function renderShell() {
  const initials = (state.user?.username || "U").slice(0, 2).toUpperCase();
  app.innerHTML = `
    <button class="hamburger" id="hamburgerBtn">☰</button>
    <div class="sidebar-overlay" id="sidebarOverlay"></div>
    <div class="shell">
      <aside class="sidebar ${state.sidebarOpen ? "open" : ""}" id="sidebar">
        <div class="brand">
          <div class="mark">CT</div>
          <div>
            <strong>ChillTopia</strong>
            <span>Control Panel</span>
          </div>
        </div>
        <nav class="nav">${buildNav()}</nav>
        <div class="sidebar-footer">
          <div class="sidebar-user">
            <div class="sidebar-user-avatar">${esc(initials)}</div>
            <div>
              <div class="sidebar-user-name">${esc(state.user.username)}</div>
              <div class="sidebar-user-role">${esc(state.user.role)}</div>
            </div>
          </div>
          <button class="btn-logout" id="logoutBtn">Sign out</button>
        </div>
      </aside>
      <main class="content">
        <div class="topbar">
          <div class="topbar-left">
            <h1>${esc(state.page)}</h1>
            <div class="page-desc">${esc(PAGE_DESC[state.page] || "")}</div>
          </div>
          <div class="topbar-actions">
            <button class="btn ghost" id="refreshBtn">↻ Refresh</button>
          </div>
        </div>
        ${state.error ? `<div class="notice error" style="margin-bottom:16px">${esc(state.error)}</div>` : ""}
        ${state.notice ? `<div class="notice success" style="margin-bottom:16px">${esc(state.notice)}</div>` : ""}
        <section class="page">${renderPage()}</section>
      </main>
    </div>
    ${state.modal ? renderModal() : ""}
  `;
  document.querySelectorAll(".nav button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.page = btn.dataset.page;
      state.notice = "";
      state.sidebarOpen = false;
      load();
    });
  });
  document.getElementById("refreshBtn").addEventListener("click", load);
  document.getElementById("logoutBtn").addEventListener("click", logout);
  document.getElementById("hamburgerBtn")?.addEventListener("click", () => {
    state.sidebarOpen = !state.sidebarOpen;
    document.getElementById("sidebar")?.classList.toggle("open", state.sidebarOpen);
    document.getElementById("sidebarOverlay")?.classList.toggle("open", state.sidebarOpen);
  });
  document.getElementById("sidebarOverlay")?.addEventListener("click", () => {
    state.sidebarOpen = false;
    document.getElementById("sidebar")?.classList.remove("open");
    document.getElementById("sidebarOverlay")?.classList.remove("open");
  });
  bindPageEvents();
}

function renderModal() {
  return `<div class="modal-backdrop">
    <div class="modal">
      <h2>${esc(state.modal.title)}</h2>
      <p>${esc(state.modal.body)}</p>
      <div class="toolbar">
        <button class="btn danger" id="modalConfirm">Confirm</button>
        <button class="btn ghost" id="modalCancel">Cancel</button>
      </div>
    </div>
  </div>`;
}

function renderPage() {
  if (!state.data) return `<div class="notice">Loading or unavailable.</div>`;
  return {
    Overview:      renderOverview,
    Radio:         renderRadio,
    Casino:        renderCasino,
    Games:         renderGames,
    Titles:        renderTitles,
    Staff:         renderStaff,
    "Bot Config":  renderBotConfig,
    Settings:      renderSettings,
    "Live Tracker":renderLive,
    Logs:          renderLogs,
    Emergency:     renderEmergency,
  }[state.page]?.() ?? `<div class="notice">Unknown page.</div>`;
}

function renderOverview() {
  const d = state.data;
  const m = d.metrics || {};
  const r = d.radio || {};
  const nowPlaying = r.now_playing?.title || "Auto DJ";

  return `
    <div class="grid grid-3" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr))">
      ${metricCard("Online Bots", `${m.online_bots ?? 0}/${m.total_bots ?? 0}`, "active instances", "accent-green", "🤖")}
      ${metricCard("Room Users", m.current_room_users ?? "—", "live occupancy", "accent-cyan", "👥")}
      ${metricCard("Queue", m.queue_count ?? 0, "songs pending", "", "🎵")}
      ${metricCard("Active Games", m.active_games ?? 0, "casino / games", "", "🎲")}
      ${metricCard("Staff Online", m.staff_online ?? "—", "dashboard staff", "", "👑")}
    </div>
    <div class="card" style="padding:16px 20px">
      <div class="card-header">
        <h2>🎵 Now Playing</h2>
      </div>
      <div style="font-size:18px;font-weight:700;color:#fff;margin-bottom:4px">${esc(nowPlaying)}</div>
      ${r.now_playing?.artist ? `<div class="muted text-sm">${esc(r.now_playing.artist)}</div>` : ""}
    </div>
    <div class="grid">
      <div class="card">
        <h2>Module Status</h2>
        ${moduleFlagTable(d.module_flags)}
      </div>
      <div class="card">
        <h2>Recent Command Errors</h2>
        ${table(d.command_errors, [
          { key: "command", label: "Command" },
          { key: "error", label: "Error" },
          { key: "username", label: "User" },
          { key: "created_at", label: "Time" },
        ])}
      </div>
    </div>
  `;
}

function renderLive() {
  const d = state.data;
  return `
    <div class="grid">
      <div class="card full-width">
        <h2>Bot Heartbeats</h2>
        ${table(d.bots, [
          { key: "bot_mode", label: "Mode" },
          { key: "bot_username", label: "Username" },
          { key: "status", label: "Status", render: (r) => pill(r.status) },
          { key: "current_room_id", label: "Room" },
          { key: "last_heartbeat_at", label: "Last Heartbeat" },
          { key: "last_error", label: "Last Error" },
        ])}
      </div>
      <div class="card">
        <h2>Live Status</h2>
        ${table(d.live_status)}
      </div>
      <div class="card">
        <h2>Recent Events</h2>
        ${table(d.recent_commands_or_errors)}
      </div>
    </div>
  `;
}

function renderRadio() {
  const d = state.data;
  return `
    <div class="grid">
      <div class="card">
        <h2>Now Playing</h2>
        ${d.now_playing ? `
          <div style="margin-bottom:14px">
            <div style="font-size:17px;font-weight:700;color:#fff;margin-bottom:4px">${esc(d.now_playing.title || "—")}</div>
            ${d.now_playing.artist ? `<div class="muted text-sm">${esc(d.now_playing.artist)}</div>` : ""}
            ${d.now_playing.username ? `<div class="muted text-sm" style="margin-top:4px">Requested by ${esc(d.now_playing.username)}</div>` : ""}
          </div>
          <div class="inline-actions">
            <button class="btn danger sm" data-action="radio-skip">⏭ Skip Song</button>
          </div>
        ` : `<div class="notice">Nothing currently playing.</div>`}
      </div>
      <div class="card">
        <h2>Request Controls</h2>
        <label class="switch" style="margin-bottom:16px">
          <input type="checkbox" id="requestsEnabled" ${d.queue_open ? "checked" : ""} />
          <span>Requests enabled</span>
        </label>
        <hr class="divider" />
        <button class="btn danger" data-action="radio-clear">🗑 Clear Queue</button>
      </div>
    </div>
    <div class="card">
      <h2>Song Queue</h2>
      ${table(d.queue, [
        { key: "pos", label: "#" },
        { key: "title", label: "Title" },
        { key: "artist", label: "Artist" },
        { key: "username", label: "Requester" },
        { key: "status", label: "Status", render: (r) => pill(r.status) },
      ], (r) => `<button class="btn danger sm" data-remove-request="${r.id}">Remove</button>`)}
    </div>
  `;
}

function renderCasino() {
  const d = state.data;
  return `
    <div class="grid">
      <div class="card">
        <h2>Casino Module</h2>
        ${moduleFlagEditor(d.module_flag || { module: "casino", enabled: 1 })}
      </div>
      <div class="card">
        <h2>Casino Settings</h2>
        ${settingsEditor(d.settings || [], "casino")}
      </div>
    </div>
  `;
}

function renderGames() {
  const flag = state.data.module_flag || { module: "games", enabled: 1 };
  return `
    <div class="grid">
      <div class="card">
        <h2>Games Toggle</h2>
        ${moduleFlagEditor(flag)}
      </div>
      <div class="card">
        <h2>Maintenance Mode</h2>
        ${settingsForm("games.maintenance_mode", "false", "games")}
      </div>
    </div>
    <div class="card">
      <h2>Game Settings</h2>
      ${settingsEditor(state.data.settings || [], "games")}
    </div>
  `;
}

function renderTitles() {
  const d = state.data;
  return `
    <div class="grid">
      <div class="card">
        <h2>Assign Title</h2>
        <form id="assignTitleForm">
          <div class="field"><label class="field-label">User ID</label><input name="user_id" placeholder="Optional if username known" /></div>
          <div class="field"><label class="field-label">Username</label><input name="username" /></div>
          <div class="field"><label class="field-label">Title ID</label><input name="title_id" required /></div>
          <div class="field"><label class="field-label">Display Text</label><input name="display" /></div>
          <div class="field" style="margin-bottom:16px"><label class="field-label">Color</label><input name="color" placeholder="#9b5eff" /></div>
          <button class="btn primary">Assign Title</button>
        </form>
      </div>
      <div class="card">
        <h2>Title Catalog</h2>
        ${table(d.catalog)}
      </div>
    </div>
    <div class="card">
      <h2>Assigned Titles</h2>
      ${table(d.assigned)}
    </div>
  `;
}

function renderStaff() {
  const d = state.data;
  return `
    <div class="grid">
      <div class="card">
        <h2>Create Staff Account</h2>
        <form id="staffCreateForm">
          <div class="field"><label class="field-label">Username</label><input name="username" required /></div>
          <div class="field"><label class="field-label">Password</label><input name="password" type="password" required /></div>
          <div class="field" style="margin-bottom:14px"><label class="field-label">Role</label>
            <select name="role"><option value="staff">Staff</option><option value="owner">Owner</option></select>
          </div>
          <div style="margin-bottom:14px">
            <div class="field-label" style="margin-bottom:8px">Permissions</div>
            ${permissionChecks({})}
          </div>
          <button class="btn primary">Create Account</button>
        </form>
      </div>
      <div class="card">
        <h2>Bot Role Management</h2>
        <form id="botRoleForm">
          <div class="field"><label class="field-label">Username</label><input name="username" required placeholder="Highrise username" /></div>
          <div class="field" style="margin-bottom:14px"><label class="field-label">Role</label>
            <select name="role">
              <option value="admin">Admin</option>
              <option value="manager">Manager</option>
              <option value="mod">Mod</option>
              <option value="dj">DJ</option>
            </select>
          </div>
          <div class="inline-actions">
            <button class="btn primary" type="submit" name="action" value="add">Add Role</button>
            <button class="btn danger" type="submit" name="action" value="remove">Remove Role</button>
          </div>
        </form>
      </div>
    </div>
    <div class="card">
      <h2>Dashboard Users</h2>
      ${table(d.dashboard_users, [
        { key: "username", label: "User" },
        { key: "role", label: "Role", render: (r) => `<span class="pill ${r.role === "owner" ? "ok" : "def"}">${esc(r.role)}</span>` },
        { key: "disabled", label: "State", render: (r) => r.disabled ? pill("disabled") : pill("enabled") },
        { key: "last_login_at", label: "Last Login" },
      ], (r) => `
        <button class="btn sm" data-edit-staff="${r.id}" data-role="${esc(r.role)}" data-disabled="${r.disabled ? "1" : "0"}" data-perms="${encodeURIComponent(JSON.stringify(r.permissions || {}))}">Edit</button>
        <button class="btn danger sm" data-remove-staff="${r.id}">Remove</button>
      `)}
    </div>
  `;
}

function renderBotConfig() {
  const d = state.data;
  const tokens = d.tokens || {};
  const tokenRows = Object.entries(tokens).map(([key, status]) => `
    <div class="token-row">
      <span class="token-name">${esc(key)}</span>
      ${pill(status)}
      <div class="token-input-wrap">
        <input type="password" placeholder="Paste new token (leave blank to keep)" data-token-key="${esc(key)}" autocomplete="off" />
      </div>
    </div>
  `).join("");

  return `
    <div class="grid">
      <div class="card">
        <h2>Connection Settings</h2>
        <form id="botConfigForm">
          <div class="field">
            <label class="field-label">Room ID</label>
            <input name="room_id" value="${esc(d.room_id || "")}" placeholder="Enter Room ID" />
          </div>
          <div class="field" style="margin-bottom:18px">
            <label class="field-label">Bots Enabled</label>
            <input name="bots_enabled" value="${esc(d.bots_enabled || "")}" placeholder="e.g. all, main,dj,poker" />
          </div>
          <div class="inline-actions">
            <button class="btn primary" type="submit">💾 Save Config</button>
            <button class="btn danger" type="button" id="restartBotsBtn">🔄 Restart Bots</button>
          </div>
        </form>
      </div>
      <div class="card">
        <h2>Token Status</h2>
        <p class="muted text-sm" style="margin-bottom:14px">Tokens are stored in Replit Secrets and never displayed. Status shows SET or EMPTY only.</p>
        ${tokenRows}
        <div style="margin-top:14px">
          <p class="muted text-sm">⚠️ Token fields above are for future update support — changes here are noted in audit log only. Set actual tokens in your environment secrets.</p>
        </div>
      </div>
    </div>
  `;
}

function renderSettings() {
  const d = state.data;
  return `
    <div class="card">
      <h2>Module Flags</h2>
      ${moduleFlagTable(d.module_flags)}
    </div>
    <div class="grid">
      <div class="card">
        <h2>Edit Setting</h2>
        ${settingsForm("", "", "global")}
      </div>
      <div class="card">
        <h2>Bot Settings</h2>
        ${settingsEditor(d.bot_settings || [], "global")}
      </div>
    </div>
    <div class="card">
      <h2>Room Settings</h2>
      ${table(d.room_settings)}
    </div>
  `;
}

function renderLogs() {
  const d = state.data;
  return `
    <div class="card">
      <h2>Audit Log</h2>
      <div class="toolbar" style="margin-bottom:14px">
        <input id="logAction" placeholder="Action type" value="${esc(state.logs.action_type)}" style="flex:1" />
        <input id="logUser" placeholder="User" value="${esc(state.logs.user)}" style="flex:1" />
        <input id="logModule" placeholder="Module / target" value="${esc(state.logs.module)}" style="flex:1" />
        <button class="btn" data-action="logs-filter">Filter</button>
        <button class="btn ghost" data-action="logs-prev">← Prev</button>
        <button class="btn ghost" data-action="logs-next">Next →</button>
      </div>
      ${table(d.audit_logs)}
    </div>
    <div class="card">
      <h2>Command Errors</h2>
      ${table(d.command_error_logs)}
    </div>
  `;
}

function renderEmergency() {
  return `
    <div class="notice warn" style="margin-bottom:4px">
      ⚠️ Emergency controls write DB flags only. Bots consume these flags safely — no processes are killed.
    </div>
    <div class="card">
      <h2>Quick Disable</h2>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin-top:4px">
        <button class="btn danger" data-emergency="disable_radio_requests">🎵 Disable Radio Requests</button>
        <button class="btn danger" data-emergency="disable_casino">🎲 Disable Casino</button>
        <button class="btn danger" data-emergency="disable_games">🎮 Disable Games</button>
        <button class="btn danger" data-emergency="clear_queue">🗑 Clear Queue</button>
        <button class="btn danger" data-emergency="lock_room_systems">🔒 Lock All Systems</button>
      </div>
    </div>
    <div class="card">
      <h2>Current Module Flags</h2>
      ${moduleFlagTable(state.data.module_flags || [])}
    </div>
  `;
}

function moduleFlagTable(flags) {
  return table(flags || [], [
    { key: "module", label: "Module" },
    { key: "enabled", label: "State", render: (r) => Number(r.enabled) === 1 ? pill("enabled") : pill("disabled") },
    { key: "reason", label: "Reason" },
    { key: "updated_by", label: "By" },
    { key: "updated_at", label: "Updated" },
  ], (r) => `<button class="btn sm ${Number(r.enabled) === 1 ? "danger" : "cyan"}" data-toggle-module="${esc(r.module)}" data-enabled="${Number(r.enabled) === 1 ? "0" : "1"}">${Number(r.enabled) === 1 ? "Disable" : "Enable"}</button>`);
}

function moduleFlagEditor(flag) {
  return `<label class="switch">
    <input type="checkbox" data-module-checkbox="${esc(flag.module)}" ${Number(flag.enabled) === 1 ? "checked" : ""} />
    <span>${esc(flag.module)} enabled</span>
  </label>
  ${flag.reason ? `<p class="muted text-sm" style="margin-top:8px">Reason: ${esc(flag.reason)}</p>` : ""}`;
}

function settingsEditor(rows, moduleName) {
  if (!rows.length) return `${settingsForm("", "", moduleName)}<div class="notice" style="margin-top:10px">No settings recorded yet.</div>`;
  return `${settingsForm("", "", moduleName)}
    <div style="margin-top:14px">${table(rows, [
      { key: "key", label: "Key" },
      { key: "value", label: "Value" },
      { key: "module", label: "Module" },
      { key: "updated_by", label: "By" },
    ], (r) => `<button class="btn sm" data-edit-setting="${esc(r.key)}" data-module="${esc(r.module || moduleName)}" data-value="${esc(r.value)}">Edit</button>`)}</div>`;
}

function settingsForm(key, value, moduleName) {
  return `<form class="toolbar settingForm" data-module="${esc(moduleName)}" style="flex-wrap:wrap">
    <input name="key" placeholder="setting.key" value="${esc(key)}" required style="flex:2;min-width:120px" />
    <input name="value" placeholder="value" value="${esc(value)}" required style="flex:2;min-width:100px" />
    <input name="module" placeholder="module" value="${esc(moduleName)}" style="flex:1;min-width:80px" />
    <button class="btn primary sm">Save</button>
  </form>`;
}

function permissionChecks(perms) {
  const all = ["manage_radio", "manage_casino", "manage_games", "manage_titles", "manage_staff", "view_logs", "emergency_controls"];
  return `<div style="display:grid;gap:8px">${all.map((p) => `<label class="switch">
    <input type="checkbox" name="${p}" ${perms[p] ? "checked" : ""} />
    <span class="text-sm">${p.replaceAll("_", " ")}</span>
  </label>`).join("")}</div>`;
}

function bindPageEvents() {
  document.getElementById("modalConfirm")?.addEventListener("click", runModalAction);
  document.getElementById("modalCancel")?.addEventListener("click", () => { state.modal = null; render(); });

  document.querySelectorAll("[data-toggle-module]").forEach((btn) => {
    btn.addEventListener("click", () => action("Module flag updated.", () => api(`/api/modules/${btn.dataset.toggleModule}`, {
      method: "PUT",
      body: JSON.stringify({ enabled: btn.dataset.enabled === "1", reason: "dashboard update" }),
    })));
  });

  document.querySelectorAll("[data-module-checkbox]").forEach((box) => {
    box.addEventListener("change", () => action("Module flag updated.", () => api(`/api/modules/${box.dataset.moduleCheckbox}`, {
      method: "PUT",
      body: JSON.stringify({ enabled: box.checked, reason: "dashboard update" }),
    })));
  });

  document.querySelectorAll(".settingForm").forEach((form) => {
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const data = Object.fromEntries(new FormData(form));
      const moduleName = data.module || form.dataset.module;
      const endpoint = moduleName === "casino" ? `/api/casino/${encodeURIComponent(data.key)}`
        : moduleName === "games" ? `/api/games/${encodeURIComponent(data.key)}`
        : `/api/settings/${encodeURIComponent(data.key)}`;
      action("Setting saved.", () => api(endpoint, {
        method: "PUT",
        body: JSON.stringify({ value: data.value, module: moduleName, source: "bot_settings" }),
      }));
    });
  });

  document.querySelectorAll("[data-edit-setting]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const value = prompt(`New value for ${btn.dataset.editSetting}`, btn.dataset.value || "");
      if (value === null) return;
      const moduleName = btn.dataset.module || "global";
      const endpoint = moduleName === "casino" ? `/api/casino/${encodeURIComponent(btn.dataset.editSetting)}`
        : moduleName === "games" ? `/api/games/${encodeURIComponent(btn.dataset.editSetting)}`
        : `/api/settings/${encodeURIComponent(btn.dataset.editSetting)}`;
      action("Setting saved.", () => api(endpoint, {
        method: "PUT",
        body: JSON.stringify({ value, module: moduleName, source: "bot_settings" }),
      }));
    });
  });

  document.querySelector("[data-action='radio-skip']")?.addEventListener("click", () => {
    confirmAction("Skip current song?", "This stores a DB skip request for the radio bot to consume.", () => action("Skip requested.", () => api("/api/radio/skip", { method: "POST" })));
  });
  document.querySelector("[data-action='radio-clear']")?.addEventListener("click", () => {
    confirmAction("Clear queue?", "Upcoming requests will be marked cancelled in the database.", () => action("Queue cleared.", () => api("/api/radio/clear", { method: "POST" })));
  });
  document.getElementById("requestsEnabled")?.addEventListener("change", (ev) => {
    action("Request setting updated.", () => api("/api/radio/requests-enabled", {
      method: "PUT",
      body: JSON.stringify({ enabled: ev.target.checked }),
    }));
  });
  document.querySelectorAll("[data-remove-request]").forEach((btn) => {
    btn.addEventListener("click", () => confirmAction("Remove queue item?", `Request #${btn.dataset.removeRequest} will be cancelled.`,
      () => action("Request removed.", () => api(`/api/radio/requests/${btn.dataset.removeRequest}/remove`, { method: "POST" }))));
  });

  document.getElementById("assignTitleForm")?.addEventListener("submit", (ev) => {
    ev.preventDefault();
    action("Title assigned.", () => api("/api/titles/assign", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(ev.currentTarget))) }));
  });

  document.getElementById("staffCreateForm")?.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const raw = Object.fromEntries(new FormData(ev.currentTarget));
    const permissions = {};
    for (const key of Object.keys(raw)) if (key.startsWith("manage_") || key === "view_logs" || key === "emergency_controls") permissions[key] = true;
    action("Staff account created.", () => api("/api/staff", { method: "POST", body: JSON.stringify({ username: raw.username, password: raw.password, role: raw.role, permissions }) }));
  });

  document.getElementById("botRoleForm")?.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const raw = Object.fromEntries(new FormData(ev.currentTarget));
    const actionType = ev.submitter?.value || "add";
    action(`Bot role ${actionType}ed.`, () => api("/api/staff/bot-role", {
      method: "POST",
      body: JSON.stringify({ username: raw.username, role: raw.role, action: actionType }),
    }));
  });

  document.querySelectorAll("[data-remove-staff]").forEach((btn) => {
    btn.addEventListener("click", () => confirmAction("Remove staff account?", "The account will be disabled and active sessions revoked.",
      () => action("Staff removed.", () => api(`/api/staff/${btn.dataset.removeStaff}`, { method: "DELETE" }))));
  });

  document.querySelectorAll("[data-edit-staff]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const current = Object.entries(JSON.parse(decodeURIComponent(btn.dataset.perms || "%7B%7D"))).filter(([, v]) => v).map(([k]) => k).join(", ");
      const next = prompt("Enabled permissions, comma-separated", current);
      if (next === null) return;
      const permissions = {};
      next.split(",").map((s) => s.trim()).filter(Boolean).forEach((p) => { permissions[p] = true; });
      action("Staff permissions updated.", () => api(`/api/staff/${btn.dataset.editStaff}`, {
        method: "PUT",
        body: JSON.stringify({ role: btn.dataset.role || "staff", disabled: btn.dataset.disabled === "1", permissions }),
      }));
    });
  });

  document.getElementById("botConfigForm")?.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const data = Object.fromEntries(new FormData(ev.currentTarget));
    action("Bot config saved.", () => api("/api/bot-config", {
      method: "POST",
      body: JSON.stringify({ room_id: data.room_id, bots_enabled: data.bots_enabled }),
    }));
  });

  document.getElementById("restartBotsBtn")?.addEventListener("click", () => {
    confirmAction("Restart bots?", "A restart flag will be written to the database. Bots will restart on their next heartbeat cycle.", () => action("Restart requested.", () => api("/api/bot-config/restart", { method: "POST" })));
  });

  document.querySelector("[data-action='logs-filter']")?.addEventListener("click", () => {
    state.logs.action_type = document.getElementById("logAction").value;
    state.logs.user = document.getElementById("logUser").value;
    state.logs.module = document.getElementById("logModule").value;
    state.logs.offset = 0;
    load();
  });
  document.querySelector("[data-action='logs-prev']")?.addEventListener("click", () => { state.logs.offset = Math.max(0, state.logs.offset - 50); load(); });
  document.querySelector("[data-action='logs-next']")?.addEventListener("click", () => { state.logs.offset += 50; load(); });

  document.querySelectorAll("[data-emergency]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const flag = btn.dataset.emergency;
      const flags = flag === "lock_room_systems"
        ? { disable_radio_requests: true, disable_casino: true, disable_games: true }
        : { [flag]: true };
      confirmAction("Confirm emergency action", `"${flag.replaceAll("_", " ")}" will be applied through database flags only.`,
        () => action("Emergency action applied.", () => api("/api/emergency", { method: "POST", body: JSON.stringify({ flags }) })));
    });
  });
}

function render() {
  if (!state.user) return renderLogin();
  renderShell();
}

window.addEventListener("hashchange", () => {
  const next = decodeURIComponent(location.hash.slice(1));
  if (NAV_IDS.includes(next) && next !== state.page) {
    state.page = next;
    load();
  }
});

init();
