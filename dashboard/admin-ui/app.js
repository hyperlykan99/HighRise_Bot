const NAV = [
  "Overview",
  "Radio",
  "Casino",
  "Games",
  "Titles",
  "Staff",
  "Settings",
  "Live Tracker",
  "Logs",
  "Emergency",
];

const API_FOR_PAGE = {
  Overview: "/api/overview",
  Radio: "/api/radio",
  Casino: "/api/casino",
  Games: "/api/games",
  Titles: "/api/titles",
  Staff: "/api/staff",
  Settings: "/api/settings",
  "Live Tracker": "/api/live",
  Logs: "/api/logs",
  Emergency: "/api/settings",
};

const state = {
  user: null,
  csrf: "",
  page: location.hash ? decodeURIComponent(location.hash.slice(1)) : "Overview",
  data: null,
  error: "",
  notice: "",
  logs: { action_type: "", user: "", module: "", offset: 0 },
  modal: null,
};

const app = document.getElementById("app");

function esc(value) {
  return String(value ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
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

function pill(value, goodValues = ["online", "enabled", "ready", "queued", "submitted"]) {
  const text = String(value ?? "");
  const lower = text.toLowerCase();
  const cls = goodValues.includes(lower) || value === 1 || value === true ? "ok" : lower.includes("off") || lower.includes("disabled") || value === 0 ? "bad" : "warn";
  return `<span class="pill ${cls}">${esc(text || "unknown")}</span>`;
}

function table(rows, columns, actions) {
  if (!rows || rows.length === 0) return `<div class="notice">No data available.</div>`;
  const cols = columns || Object.keys(rows[0]).slice(0, 8).map((key) => ({ key, label: key }));
  return `<div class="table-wrap"><table><thead><tr>${cols.map((c) => `<th>${esc(c.label)}</th>`).join("")}${actions ? "<th>Actions</th>" : ""}</tr></thead><tbody>${
    rows.map((row) => `<tr>${cols.map((c) => `<td>${c.render ? c.render(row) : esc(row[c.key])}</td>`).join("")}${actions ? `<td>${actions(row)}</td>` : ""}</tr>`).join("")
  }</tbody></table></div>`;
}

function metric(label, value, hint = "") {
  return `<div class="card stat"><span>${esc(label)}</span><b>${esc(value ?? "-")}</b>${hint ? `<span>${esc(hint)}</span>` : ""}</div>`;
}

async function load() {
  const page = NAV.includes(state.page) ? state.page : "Overview";
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
    if (state.user && ["Overview", "Radio", "Live Tracker", "Logs"].includes(state.page)) load();
  }, 5000);
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
    await load();
  } catch (err) {
    state.error = err.message;
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
  app.innerHTML = `<div class="login-shell"><form class="login-card" id="loginForm">
    <h1>ChillTopia Admin</h1>
    <p class="muted">Owner and staff control panel</p>
    ${state.error ? `<p class="error">${esc(state.error)}</p>` : ""}
    <label class="field">Username<input name="username" autocomplete="username" required /></label>
    <label class="field">Password<input name="password" type="password" autocomplete="current-password" required /></label>
    <button class="btn primary" type="submit">Log in</button>
  </form></div>`;
  document.getElementById("loginForm").addEventListener("submit", login);
}

function renderShell() {
  app.innerHTML = `<div class="shell">
    <aside class="sidebar">
      <div class="brand"><div class="mark">CT</div><div><strong>ChillTopia</strong><span>Admin Panel</span></div></div>
      <nav class="nav">${NAV.map((page) => `<button class="${page === state.page ? "active" : ""}" data-page="${esc(page)}">${esc(page)}</button>`).join("")}</nav>
    </aside>
    <main class="content">
      <div class="topbar">
        <div><h1>${esc(state.page)}</h1><div class="muted">${esc(state.user.username)} · ${esc(state.user.role)}</div></div>
        <div class="topbar-actions"><button class="btn" id="refreshBtn">Refresh</button><button class="btn ghost" id="logoutBtn">Log out</button></div>
      </div>
      ${state.error ? `<div class="notice error">${esc(state.error)}</div>` : ""}
      ${state.notice ? `<div class="notice success">${esc(state.notice)}</div>` : ""}
      <section class="page">${renderPage()}</section>
    </main>
    ${state.modal ? renderModal() : ""}
  </div>`;
  document.querySelectorAll(".nav button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.page = btn.dataset.page;
      state.notice = "";
      load();
    });
  });
  document.getElementById("refreshBtn").addEventListener("click", load);
  document.getElementById("logoutBtn").addEventListener("click", logout);
  bindPageEvents();
}

function renderModal() {
  return `<div class="modal-backdrop"><div class="modal">
    <h2>${esc(state.modal.title)}</h2>
    <p class="muted">${esc(state.modal.body)}</p>
    <div class="toolbar"><button class="btn danger" id="modalConfirm">Confirm</button><button class="btn" id="modalCancel">Cancel</button></div>
  </div></div>`;
}

function renderPage() {
  if (!state.data) return `<div class="notice">Loading or unavailable.</div>`;
  return {
    Overview: renderOverview,
    Radio: renderRadio,
    Casino: renderCasino,
    Games: renderGames,
    Titles: renderTitles,
    Staff: renderStaff,
    Settings: renderSettings,
    "Live Tracker": renderLive,
    Logs: renderLogs,
    Emergency: renderEmergency,
  }[state.page]();
}

function renderOverview() {
  const d = state.data;
  const m = d.metrics || {};
  return `<div class="grid">
    ${metric("Online bots", `${m.online_bots ?? 0}/${m.total_bots ?? 0}`, "from bot_instances")}
    ${metric("Room users", m.current_room_users ?? "not reported", "from live_status when available")}
    ${metric("Current song", d.radio?.now_playing?.title || "Auto DJ / unknown")}
    ${metric("Queue count", m.queue_count ?? 0)}
    ${metric("Active games", m.active_games ?? 0, "casino/game modules enabled")}
    ${metric("Staff online", m.staff_online ?? "not reported")}
    <div class="card"><h2>Recent Actions</h2>${table(d.command_errors, null)}</div>
    <div class="card"><h2>Module Flags</h2>${moduleFlagTable(d.module_flags)}</div>
  </div>`;
}

function renderLive() {
  const d = state.data;
  return `<div class="grid">
    <div class="card"><h2>Bot Heartbeats</h2>${table(d.bots, [
      { key: "bot_mode", label: "Mode" },
      { key: "bot_username", label: "Bot" },
      { key: "status", label: "Status", render: (r) => pill(r.status) },
      { key: "current_room_id", label: "Room" },
      { key: "last_heartbeat_at", label: "Heartbeat" },
      { key: "last_error", label: "Last Error" },
    ])}</div>
    <div class="card"><h2>Live Status Rows</h2>${table(d.live_status)}</div>
    <div class="card"><h2>Last Commands / Errors</h2>${table(d.recent_commands_or_errors)}</div>
  </div>`;
}

function renderRadio() {
  const d = state.data;
  return `<div class="grid">
    <div class="card"><h2>Current Song</h2>${d.now_playing ? table([d.now_playing], [
      { key: "title", label: "Title" },
      { key: "artist", label: "Artist" },
      { key: "username", label: "Requester" },
      { key: "status", label: "Status", render: (r) => pill(r.status) },
    ]) : `<div class="notice">No request currently marked playing.</div>`}
      <div class="toolbar"><button class="btn primary" data-action="radio-skip">Skip</button></div>
    </div>
    <div class="card"><h2>Request Controls</h2>
      <label class="switch"><input type="checkbox" id="requestsEnabled" ${d.queue_open ? "checked" : ""} /> Requests enabled</label>
      <div class="toolbar"><button class="btn danger" data-action="radio-clear">Clear Queue</button></div>
    </div>
    <div class="card" style="grid-column:1/-1"><h2>Queue</h2>${table(d.queue, [
      { key: "pos", label: "#" },
      { key: "title", label: "Title" },
      { key: "artist", label: "Artist" },
      { key: "username", label: "Requester" },
      { key: "status", label: "Status", render: (r) => pill(r.status) },
      { key: "azura_song_id", label: "Azura Song" },
    ], (r) => `<button class="btn danger" data-remove-request="${r.id}">Remove</button>`)}</div>
  </div>`;
}

function renderCasino() {
  const d = state.data;
  return `<div class="grid">
    <div class="card"><h2>Casino Module</h2>${moduleFlagEditor(d.module_flag || { module: "casino", enabled: 1 })}</div>
    <div class="card"><h2>Limits and Cooldowns</h2>${settingsEditor(d.settings || [], "casino")}</div>
  </div>`;
}

function renderGames() {
  const flag = state.data.module_flag || { module: "games", enabled: 1 };
  return `<div class="grid">
    <div class="card"><h2>Games Toggle</h2>${moduleFlagEditor(flag)}</div>
    <div class="card"><h2>Maintenance Mode</h2>${settingsForm("games.maintenance_mode", "false", "games")}</div>
    <div class="card"><h2>Game Settings</h2>${settingsEditor(state.data.settings || [], "games")}</div>
  </div>`;
}

function renderTitles() {
  const d = state.data;
  return `<div class="grid">
    <div class="card"><h2>Assign Title</h2>
      <form id="assignTitleForm">
        <label class="field">User ID<input name="user_id" placeholder="Optional if username is known" /></label>
        <label class="field">Username<input name="username" /></label>
        <label class="field">Title ID<input name="title_id" required /></label>
        <label class="field">Display Text<input name="display" /></label>
        <label class="field">Color<input name="color" placeholder="#55d6a5" /></label>
        <button class="btn primary">Assign</button>
      </form>
    </div>
    <div class="card"><h2>Catalog</h2>${table(d.catalog)}</div>
    <div class="card"><h2>Assigned</h2>${table(d.assigned)}</div>
  </div>`;
}

function renderStaff() {
  const d = state.data;
  return `<div class="grid">
    <div class="card"><h2>Create Staff Account</h2>
      <form id="staffCreateForm">
        <label class="field">Username<input name="username" required /></label>
        <label class="field">Password<input name="password" type="password" required /></label>
        <label class="field">Role<select name="role"><option value="staff">Staff</option><option value="owner">Owner</option></select></label>
        ${permissionChecks({})}
        <button class="btn primary">Create</button>
      </form>
    </div>
    <div class="card" style="grid-column:1/-1"><h2>Dashboard Users</h2>${table(d.dashboard_users, [
      { key: "username", label: "User" },
      { key: "role", label: "Role", render: (r) => `<span class="pill ${r.role === "owner" ? "ok" : ""}">${esc(r.role)}</span>` },
      { key: "disabled", label: "State", render: (r) => r.disabled ? pill("disabled") : pill("enabled") },
      { key: "last_login_at", label: "Last Login" },
    ], (r) => `<div class="toolbar"><button class="btn" data-edit-staff="${r.id}" data-role="${esc(r.role)}" data-disabled="${r.disabled ? "1" : "0"}" data-perms="${encodeURIComponent(JSON.stringify(r.permissions || {}))}">Edit permissions</button><button class="btn danger" data-remove-staff="${r.id}">Remove</button></div>`)}</div>
    <div class="card"><h2>Bot Roles</h2><pre class="mono">${esc(JSON.stringify(d.bot_roles, null, 2))}</pre></div>
  </div>`;
}

function renderSettings() {
  const d = state.data;
  return `<div class="grid">
    <div class="card"><h2>Module Flags</h2>${moduleFlagTable(d.module_flags)}</div>
    <div class="card"><h2>Add / Edit Setting</h2>${settingsForm("", "", "global")}</div>
    <div class="card"><h2>Bot Settings</h2>${settingsEditor(d.bot_settings || [], "global")}</div>
    <div class="card"><h2>Room Settings</h2>${table(d.room_settings)}</div>
  </div>`;
}

function renderLogs() {
  const d = state.data;
  return `<div class="card">
    <h2>Audit Logs</h2>
    <div class="toolbar">
      <input id="logAction" placeholder="Action type" value="${esc(state.logs.action_type)}" />
      <input id="logUser" placeholder="User" value="${esc(state.logs.user)}" />
      <input id="logModule" placeholder="Module / target" value="${esc(state.logs.module)}" />
      <button class="btn" data-action="logs-filter">Filter</button>
      <button class="btn" data-action="logs-prev">Prev</button>
      <button class="btn" data-action="logs-next">Next</button>
    </div>
    ${table(d.audit_logs)}
    <h2>Command Errors</h2>${table(d.command_error_logs)}
  </div>`;
}

function renderEmergency() {
  return `<div class="grid">
    <div class="card"><h2>Emergency Controls</h2><p class="muted">These write DB flags only. Bots consume the flags safely.</p>
      <div class="toolbar">
        <button class="btn danger" data-emergency="disable_radio_requests">Disable all requests</button>
        <button class="btn danger" data-emergency="disable_casino">Disable casino</button>
        <button class="btn danger" data-emergency="disable_games">Disable games</button>
        <button class="btn danger" data-emergency="clear_queue">Clear queue</button>
        <button class="btn danger" data-emergency="lock_room_systems">Lock room systems</button>
      </div>
    </div>
    <div class="card"><h2>Current Flags</h2>${moduleFlagTable(state.data.module_flags || [])}</div>
  </div>`;
}

function moduleFlagTable(flags) {
  return table(flags || [], [
    { key: "module", label: "Module" },
    { key: "enabled", label: "State", render: (r) => Number(r.enabled) === 1 ? pill("enabled") : pill("disabled") },
    { key: "reason", label: "Reason" },
    { key: "updated_by", label: "Updated By" },
    { key: "updated_at", label: "Updated" },
  ], (r) => `<button class="btn" data-toggle-module="${esc(r.module)}" data-enabled="${Number(r.enabled) === 1 ? "0" : "1"}">${Number(r.enabled) === 1 ? "Disable" : "Enable"}</button>`);
}

function moduleFlagEditor(flag) {
  return `<label class="switch"><input type="checkbox" data-module-checkbox="${esc(flag.module)}" ${Number(flag.enabled) === 1 ? "checked" : ""} /> ${esc(flag.module)} enabled</label>
    <p class="muted">Reason: ${esc(flag.reason || "")}</p>`;
}

function settingsEditor(rows, moduleName) {
  if (!rows.length) return `${settingsForm("", "", moduleName)}<div class="notice">No settings recorded yet.</div>`;
  return `${settingsForm("", "", moduleName)}${table(rows, [
    { key: "key", label: "Key" },
    { key: "value", label: "Value" },
    { key: "module", label: "Module" },
    { key: "updated_by", label: "Updated By" },
  ], (r) => `<button class="btn" data-edit-setting="${esc(r.key)}" data-module="${esc(r.module || moduleName)}" data-value="${esc(r.value)}">Edit</button>`)}`;
}

function settingsForm(key, value, moduleName) {
  return `<form class="toolbar settingForm" data-module="${esc(moduleName)}">
    <input name="key" placeholder="setting.key" value="${esc(key)}" required />
    <input name="value" placeholder="value" value="${esc(value)}" required />
    <input name="module" placeholder="module" value="${esc(moduleName)}" />
    <button class="btn primary">Save</button>
  </form>`;
}

function permissionChecks(perms) {
  const all = ["manage_radio", "manage_casino", "manage_games", "manage_titles", "manage_staff", "view_logs", "emergency_controls"];
  return `<div class="grid">${all.map((p) => `<label class="switch"><input type="checkbox" name="${p}" ${perms[p] ? "checked" : ""} /> ${p}</label>`).join("")}</div>`;
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
    btn.addEventListener("click", () => confirmAction("Remove queue item?", `Request #${btn.dataset.removeRequest} will be cancelled.`, () => action("Request removed.", () => api(`/api/radio/requests/${btn.dataset.removeRequest}/remove`, { method: "POST" }))));
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
  document.querySelectorAll("[data-remove-staff]").forEach((btn) => {
    btn.addEventListener("click", () => confirmAction("Remove staff account?", "The account will be disabled and active sessions revoked.", () => action("Staff removed.", () => api(`/api/staff/${btn.dataset.removeStaff}`, { method: "DELETE" }))));
  });
  document.querySelectorAll("[data-edit-staff]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const current = Object.entries(JSON.parse(decodeURIComponent(btn.dataset.perms || "%7B%7D"))).filter(([, v]) => v).map(([k]) => k).join(", ");
      const next = prompt("Enabled permissions, comma separated", current);
      if (next === null) return;
      const permissions = {};
      next.split(",").map((s) => s.trim()).filter(Boolean).forEach((p) => { permissions[p] = true; });
      action("Staff permissions updated.", () => api(`/api/staff/${btn.dataset.editStaff}`, {
        method: "PUT",
        body: JSON.stringify({ role: btn.dataset.role || "staff", disabled: btn.dataset.disabled === "1", permissions }),
      }));
    });
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
      confirmAction("Confirm emergency action", `${flag.replaceAll("_", " ")} will be applied through database flags only.`, () => action("Emergency action applied.", () => api("/api/emergency", { method: "POST", body: JSON.stringify({ flags }) })));
    });
  });
}

function render() {
  if (!state.user) return renderLogin();
  renderShell();
}

window.addEventListener("hashchange", () => {
  const next = decodeURIComponent(location.hash.slice(1));
  if (NAV.includes(next) && next !== state.page) {
    state.page = next;
    load();
  }
});

init();
