/* ── Public Nav ──────────────────────────────────────── */
const PUBLIC_NAV = [
  { id: "home",      icon: "🏠", label: "Home" },
  { id: "radio",     icon: "📻", label: "Radio" },
  { id: "tutorials", icon: "📖", label: "Tutorials" },
  { id: "events",    icon: "🎉", label: "Events" },
  { id: "rankings",  icon: "🏆", label: "Rankings" },
  { id: "roominfo",  icon: "ℹ️",  label: "Room Info" },
];

/* ── Admin Nav ───────────────────────────────────────── */
const ADMIN_NAV = [
  { id: "Overview",       icon: "📊", label: "Overview",       group: "Monitor" },
  { id: "Live Tracker",   icon: "📡", label: "Live Tracker",   group: "Monitor" },
  { id: "Bot Control",    icon: "🤖", label: "Bot Control",    group: "Bots" },
  { id: "Bot Config",     icon: "⚙️", label: "Bot Config",     group: "Bots" },
  { id: "Radio",          icon: "🎵", label: "Radio",          group: "Content" },
  { id: "Casino",         icon: "🎲", label: "Casino",         group: "Content" },
  { id: "Games",          icon: "🎮", label: "Games",          group: "Content" },
  { id: "Titles",         icon: "🏅", label: "Titles",         group: "Content" },
  { id: "Player Control", icon: "👤", label: "Player Control", group: "Players" },
  { id: "Economy",        icon: "💰", label: "Economy",        group: "Players" },
  { id: "Room Control",   icon: "🏠", label: "Room Control",   group: "Room" },
  { id: "Emotes",         icon: "💃", label: "Emotes",         group: "Room" },
  { id: "Staff",          icon: "👥", label: "Staff",          group: "Admin" },
  { id: "Logs",           icon: "📋", label: "Logs",           group: "Admin" },
  { id: "Emergency",      icon: "🚨", label: "Emergency",      group: "Admin" },
];

const ADMIN_NAV_IDS = ADMIN_NAV.map((n) => n.id);

const ADMIN_API = {
  Overview:        "/api/overview",
  "Live Tracker":  "/api/live",
  "Bot Control":   "/api/bot-control",
  "Bot Config":    "/api/bot-config",
  Radio:           "/api/radio",
  Casino:          "/api/casino",
  Games:           "/api/games",
  Titles:          "/api/titles",
  "Player Control":null,
  Economy:         "/api/economy/overview",
  "Room Control":  "/api/room-control",
  Emotes:          null,
  Staff:           "/api/staff",
  Logs:            "/api/logs",
  Emergency:       "/api/settings",
};

const PAGE_DESC = {
  Overview:        "Room status, bot health and quick metrics",
  "Live Tracker":  "Real-time bot heartbeats and room status",
  "Bot Control":   "Per-bot status, enable/disable and restart",
  "Bot Config":    "Bot tokens, ROOM_ID and restart controls",
  Radio:           "Manage the DJ queue and radio stream",
  Casino:          "Blackjack, Poker and casino settings",
  Games:           "Trivia, Scramble and game settings",
  Titles:          "Assign and manage player titles",
  "Player Control":"Search and edit player profiles",
  Economy:         "Coins, tickets, inventory and transactions",
  "Room Control":  "Announcements, welcome messages and room flags",
  Emotes:          "Bot emotes, dancefloor and sync controls",
  Staff:           "Dashboard users and bot role management",
  Logs:            "Audit trail and command error logs",
  Emergency:       "Quick disable switches for critical systems",
};

/* ── State ───────────────────────────────────────────── */
const state = {
  user: null,
  csrf: "",
  adminPage: (() => {
    const h = location.hash ? decodeURIComponent(location.hash.slice(1)) : "";
    return ADMIN_NAV_IDS.includes(h) ? h : "Overview";
  })(),
  publicPage: "home",
  data: null,
  error: "",
  notice: "",
  logs: { action_type: "", user: "", module: "", offset: 0 },
  modal: null,
  sidebarOpen: false,
  showLoginOverlay: false,
};

const app = document.getElementById("app");

/* ── Helpers ─────────────────────────────────────────── */
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

async function publicApi(path) {
  const res = await fetch(path, { credentials: "omit" });
  return res.json().catch(() => ({}));
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

function endpointNeeded(label) {
  return `<div class="endpoint-needed"><span class="pill warn">Backend endpoint needed</span><p>${esc(label)}</p></div>`;
}

/* ── Data Loading ────────────────────────────────────── */
async function loadPublic() {
  const apiMap = {
    home:      "/api/public/home",
    radio:     "/api/public/radio",
    events:    "/api/public/events",
    rankings:  "/api/public/rankings",
    tutorials: null,
    roominfo:  null,
  };
  const url = apiMap[state.publicPage];
  try {
    state.data = url ? await publicApi(url) : {};
    state.error = "";
  } catch (err) {
    state.data = {};
    state.error = err.message;
  }
  render();
}

async function loadAdmin() {
  const page = ADMIN_NAV_IDS.includes(state.adminPage) ? state.adminPage : "Overview";
  state.adminPage = page;
  location.hash = encodeURIComponent(page);
  const url = page === "Logs" ? logsUrl() : ADMIN_API[page];
  if (!url) { state.data = {}; render(); return; }
  try {
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

/* ── Auth ────────────────────────────────────────────── */
async function init() {
  try {
    const me = await api("/api/auth/me");
    state.user = me.user;
    state.csrf = me.csrf_token || "";
    await loadAdmin();
  } catch {
    await loadPublic();
  }
  setInterval(() => {
    if (state.user) {
      if (["Overview", "Radio", "Live Tracker"].includes(state.adminPage)) loadAdmin();
    } else {
      if (["home", "radio"].includes(state.publicPage)) loadPublic();
    }
  }, 10000);
}

async function login(event) {
  event.preventDefault();
  const body = Object.fromEntries(new FormData(event.currentTarget));
  try {
    const result = await api("/api/auth/login", { method: "POST", body: JSON.stringify(body) });
    state.user = result.user;
    state.csrf = result.csrf_token || "";
    state.notice = "Signed in successfully.";
    state.showLoginOverlay = false;
    state.adminPage = "Overview";
    await loadAdmin();
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
  state.adminPage = "Overview";
  await loadPublic();
}

async function action(label, fn) {
  try {
    await fn();
    state.notice = label;
    state.error = "";
    if (state.user) await loadAdmin(); else await loadPublic();
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

function render() {
  if (state.user) renderAdmin();
  else renderPublicPortal();
}

/* ═══════════════════════════════════════════════════════
   PUBLIC PORTAL
══════════════════════════════════════════════════════ */
function renderPublicPortal() {
  app.innerHTML = `
    <div class="pub-shell">
      ${renderPublicNav()}
      <main class="pub-main">
        ${state.error ? `<div class="notice error pub-notice">${esc(state.error)}</div>` : ""}
        <section class="pub-page">${renderPublicPage()}</section>
      </main>
      <footer class="pub-footer">
        <span>ChillTopia &copy; 2025</span>
        <span>·</span>
        <span>Powered by DJ DUDU Radio</span>
        <span>·</span>
        <span>Join us on Highrise</span>
      </footer>
    </div>
    ${state.showLoginOverlay ? renderLoginOverlay() : ""}
    ${state.modal ? renderModal() : ""}
  `;
  bindPublicEvents();
}

function renderPublicNav() {
  const links = PUBLIC_NAV.map((n) => `
    <button class="pub-nav-link ${n.id === state.publicPage ? "active" : ""}" data-pub-page="${n.id}">
      <span>${n.icon}</span> ${n.label}
    </button>`).join("");
  return `<header class="pub-header">
    <div class="pub-brand">
      <div class="mark">CT</div>
      <div>
        <strong>ChillTopia</strong>
        <span>DJ DUDU Radio</span>
      </div>
    </div>
    <nav class="pub-nav">${links}</nav>
    <button class="btn primary pub-login-btn" id="pubLoginBtn">Owner Login</button>
  </header>`;
}

function renderPublicPage() {
  const d = state.data || {};
  switch (state.publicPage) {
    case "home":      return renderPublicHome(d);
    case "radio":     return renderPublicRadio(d);
    case "tutorials": return renderPublicTutorials();
    case "events":    return renderPublicEvents(d);
    case "rankings":  return renderPublicRankings(d);
    case "roominfo":  return renderPublicRoomInfo();
    default:          return renderPublicHome(d);
  }
}

function renderPublicHome(d) {
  const np = d.now_playing;
  return `
    <div class="pub-hero">
      <div class="pub-hero-glow"></div>
      <div class="pub-hero-content">
        <div class="pub-hero-badge">🎵 Live Now</div>
        <h1 class="pub-hero-title">Welcome to<br><span class="gradient-text">ChillTopia</span></h1>
        <p class="pub-hero-sub">Your favourite DJ DUDU Radio hangout on Highrise</p>
        <div class="pub-hero-now">
          ${np ? `<span class="pub-now-label">Now Playing</span>
          <span class="pub-now-song">${esc(np.title || "Auto DJ")}</span>
          ${np.artist ? `<span class="pub-now-artist">${esc(np.artist)}</span>` : ""}` : `<span class="pub-now-label">Auto DJ is in the house 🎧</span>`}
        </div>
        <div class="pub-quick-links">
          <button class="btn primary" data-pub-page="radio">📻 View Radio</button>
          <button class="btn cyan" data-pub-page="rankings">🏆 Rankings</button>
          <button class="btn ghost" data-pub-page="tutorials">📖 How to Play</button>
        </div>
      </div>
    </div>
    <div class="pub-stats-row">
      ${pubStatCard("🤖", "Bots Online", `${d.online_bots ?? 0}/${d.total_bots ?? 0}`, "Active bots running")}
      ${pubStatCard("👥", "In Room", d.room_users ?? 0, "Players hanging out")}
      ${pubStatCard("🎵", "Queue", d.queue_count ?? 0, "Songs up next")}
      ${pubStatCard("✨", "Vibe", d.vibe || "Chill", "Current room energy")}
    </div>
  `;
}

function pubStatCard(icon, label, value, hint) {
  return `<div class="pub-stat-card">
    <div class="pub-stat-icon">${icon}</div>
    <div class="pub-stat-value">${esc(String(value))}</div>
    <div class="pub-stat-label">${esc(label)}</div>
    <div class="pub-stat-hint">${esc(hint)}</div>
  </div>`;
}

function renderPublicRadio(d) {
  const np = d.now_playing;
  const queue = d.queue || [];
  const recent = d.recently_played || [];
  return `
    <div class="pub-section-title">
      <h2>📻 DJ DUDU Radio</h2>
      <p>Live music in ChillTopia — request your favourite songs in the room!</p>
    </div>
    <div class="pub-radio-layout">
      <div class="card pub-radio-main">
        <div class="pub-radio-disc ${np ? "spinning" : ""}">🎵</div>
        <h3 style="font-size:20px;font-weight:800;margin-bottom:4px">${esc(np?.title || "Auto DJ")}</h3>
        ${np?.artist ? `<p class="muted">${esc(np.artist)}</p>` : ""}
        ${np?.username ? `<p class="muted text-sm" style="margin-top:4px">Requested by <strong>${esc(np.username)}</strong></p>` : ""}
        <div style="margin-top:16px">
          <span class="pill ${d.queue_open ? "ok" : "bad"}">${d.queue_open ? "Requests Open" : "Requests Closed"}</span>
        </div>
        ${d.stream_url ? `<div style="margin-top:16px"><a href="${esc(d.stream_url)}" target="_blank" class="btn cyan">🔊 Listen Live</a></div>` : ""}
        <div class="pub-request-tip">
          <span class="pill info">💡 How to Request</span>
          <p>Type <strong>!request [song name]</strong> in the Highrise room chat to add your song to the queue.</p>
        </div>
      </div>
      <div style="display:grid;gap:16px;min-width:0">
        <div class="card">
          <h3>🎶 Up Next (${queue.length})</h3>
          ${queue.length ? queue.slice(0,10).map((s,i) => `
            <div class="pub-queue-item">
              <span class="pub-queue-pos">${i+1}</span>
              <div class="pub-queue-info">
                <div class="pub-queue-title">${esc(s.title || "—")}</div>
                ${s.artist ? `<div class="pub-queue-artist">${esc(s.artist)}</div>` : ""}
              </div>
              <span class="muted text-sm">${esc(s.username || "")}</span>
            </div>`).join("") : `<div class="notice">Queue is empty. Be the first to request!</div>`}
        </div>
        ${recent.length ? `<div class="card">
          <h3>🕐 Recently Played</h3>
          ${recent.slice(0,5).map((s) => `<div class="pub-queue-item">
            <span class="pub-queue-pos" style="opacity:0.4">✓</span>
            <div class="pub-queue-info">
              <div class="pub-queue-title">${esc(s.title || "—")}</div>
              ${s.artist ? `<div class="pub-queue-artist">${esc(s.artist)}</div>` : ""}
            </div>
          </div>`).join("")}
        </div>` : ""}
      </div>
    </div>
  `;
}

function renderPublicTutorials() {
  const sections = [
    { icon: "🎵", title: "How to Request Songs", content: `Type <strong>!request [song name or artist]</strong> in the room chat. The DJ bot will search YouTube and add it to the queue. Example: <code>!request lofi hip hop</code>` },
    { icon: "🃏", title: "How to Play Casino Games", content: `Type <strong>!bj [amount]</strong> to start Blackjack, or <strong>!poker</strong> to join Poker. Use <strong>!hit</strong>, <strong>!stand</strong>, <strong>!double</strong> to play Blackjack hands. Min/max bets are set by staff.` },
    { icon: "⛏️", title: "How to Mine & Fish", content: `Type <strong>!mine</strong> to start mining ores. There are 7+ rarities including Prismatic and Exotic. Type <strong>!fish</strong> to go fishing. Rare catches earn bonus coins and room announcements!` },
    { icon: "💰", title: "How to Earn Coins", content: `Coins are earned through daily rewards (<strong>!daily</strong>), casino wins, mining/fishing, completing quests, attending events, and time spent in the room. Use <strong>!balance</strong> to check your coins.` },
    { icon: "💃", title: "Emotes & Dancefloor", content: `Jump on the dancefloor and the bot may react or start a chain! Use <strong>!emote [name]</strong> for bot emotes. Special dancefloor events run during DJ sets.` },
    { icon: "⭐", title: "How VIP Works", content: `VIP gives you priority queue slots, a special badge in the room, and bonus coins on daily rewards. Ask staff about VIP access.` },
  ];
  return `
    <div class="pub-section-title">
      <h2>📖 Tutorials & Guides</h2>
      <p>Everything you need to know to enjoy ChillTopia</p>
    </div>
    <div class="pub-tutorial-grid">
      ${sections.map((s) => `<div class="card pub-tutorial-card">
        <div class="pub-tutorial-icon">${s.icon}</div>
        <h3>${s.title}</h3>
        <p>${s.content}</p>
      </div>`).join("")}
    </div>
  `;
}

function renderPublicEvents(d) {
  const scheduled = d.scheduled || [];
  return `
    <div class="pub-section-title">
      <h2>🎉 Events</h2>
      <p>Current and upcoming ChillTopia events</p>
    </div>
    <div class="pub-grid2">
      <div class="card">
        <h3>🏆 Event Points & Rewards</h3>
        <p class="muted" style="margin-bottom:12px">Earn points by participating in room events. Points unlock exclusive rewards.</p>
        <div class="pub-reward-list">
          <div class="pub-reward-item"><span class="pill info">Bronze</span><span>50 pts — Special title</span></div>
          <div class="pub-reward-item"><span class="pill def">Silver</span><span>150 pts — Badge + bonus coins</span></div>
          <div class="pub-reward-item"><span class="pill warn">Gold</span><span>300 pts — VIP access + exclusive badge</span></div>
          <div class="pub-reward-item"><span class="pill ok">Diamond</span><span>500 pts — Premium rewards package</span></div>
        </div>
      </div>
      <div class="card">
        <h3>📅 Upcoming Events</h3>
        ${scheduled.length ? scheduled.map((e) => `<div class="pub-event-item">
          <div class="pub-event-name">${esc(e.name || "Event")}</div>
          ${e.description ? `<div class="pub-event-desc muted">${esc(e.description)}</div>` : ""}
          ${e.starts_at ? `<div class="muted text-sm">📅 ${esc(e.starts_at)}</div>` : ""}
          ${e.points ? `<div class="muted text-sm">⭐ ${esc(String(e.points))} points</div>` : ""}
        </div>`).join("") : `<div class="notice">No events scheduled yet — stay tuned!</div>`}
      </div>
    </div>
    <div class="card" style="text-align:center;padding:28px">
      <div style="font-size:40px;margin-bottom:12px">🎊</div>
      <h3>Want to host an event?</h3>
      <p class="muted" style="margin-top:6px">Contact a staff member in the room to arrange a special event in ChillTopia!</p>
    </div>
  `;
}

function renderPublicRankings(d) {
  function leaderboard(title, icon, rows, cols) {
    return `<div class="card">
      <h3>${icon} ${title}</h3>
      ${rows && rows.length ? `<div class="pub-leaderboard">
        ${rows.map((r, i) => `<div class="pub-lb-row">
          <span class="pub-lb-rank ${i < 3 ? "top"+i : ""}">${["🥇","🥈","🥉"][i] || (i+1)}</span>
          <span class="pub-lb-name">${esc(r[cols[0]] || "—")}</span>
          ${r[cols[1]] !== undefined ? `<span class="pub-lb-val">${esc(String(r[cols[1]]))}</span>` : ""}
        </div>`).join("")}
      </div>` : `<div class="notice">No data yet — be the first on the leaderboard!</div>`}
    </div>`;
  }
  return `
    <div class="pub-section-title">
      <h2>🏆 Rankings</h2>
      <p>Top players across all ChillTopia activities</p>
    </div>
    <div class="pub-rankings-grid">
      ${leaderboard("Rich List", "💰", d.rich_list, ["username","coins"])}
      ${leaderboard("Top Miners", "⛏️", d.miners, ["username","total_weight"])}
      ${leaderboard("Top Fishers", "🎣", d.fishers, ["username","total_weight"])}
      ${leaderboard("Casino Kings", "🎲", d.casino, ["username","casino_winnings"])}
      ${leaderboard("Top Requesters", "🎵", d.top_requesters, ["username","count"])}
    </div>
  `;
}

function renderPublicRoomInfo() {
  return `
    <div class="pub-section-title">
      <h2>ℹ️ Room Info</h2>
      <p>Everything you need to know about ChillTopia</p>
    </div>
    <div class="pub-grid2">
      <div class="card">
        <h3>📋 Room Rules</h3>
        <ol class="pub-rules-list">
          <li>Be respectful — no harassment or hate speech.</li>
          <li>No spamming commands or chat.</li>
          <li>Keep requests appropriate for all ages.</li>
          <li>Follow staff instructions at all times.</li>
          <li>No advertising other rooms.</li>
          <li>Have fun and spread good vibes!</li>
        </ol>
      </div>
      <div class="card">
        <h3>⭐ VIP Perks</h3>
        <div class="pub-vip-list">
          <div class="pub-vip-item">🎵 Priority song queue slots</div>
          <div class="pub-vip-item">💰 Bonus daily coin rewards</div>
          <div class="pub-vip-item">🏅 Exclusive VIP badge</div>
          <div class="pub-vip-item">🎨 Special room access</div>
          <div class="pub-vip-item">🤖 Personal bot shoutout</div>
        </div>
      </div>
      <div class="card">
        <h3>🤖 Bot Commands</h3>
        <div class="pub-cmd-list">
          ${[
            ["!balance", "Check your coin balance"],
            ["!daily", "Claim daily coins"],
            ["!bj [bet]", "Play Blackjack"],
            ["!poker", "Join Poker game"],
            ["!mine", "Start mining"],
            ["!fish", "Start fishing"],
            ["!request [song]", "Request a song"],
            ["!queue", "View the song queue"],
            ["!profile", "View your profile"],
            ["!leaderboard", "View top players"],
          ].map(([cmd, desc]) => `<div class="pub-cmd-item">
            <code>${esc(cmd)}</code><span>${esc(desc)}</span>
          </div>`).join("")}
        </div>
      </div>
      <div class="card">
        <h3>👥 Staff</h3>
        <p class="muted" style="margin-bottom:12px">ChillTopia is managed by a dedicated team of staff members.</p>
        <div class="pub-staff-roles">
          <div class="pub-staff-role"><span class="pill ok">Owner</span><span>Full room authority</span></div>
          <div class="pub-staff-role"><span class="pill info">Admin</span><span>Rule enforcement</span></div>
          <div class="pub-staff-role"><span class="pill def">Manager</span><span>Event & bot control</span></div>
          <div class="pub-staff-role"><span class="pill warn">Mod</span><span>Chat moderation</span></div>
          <div class="pub-staff-role"><span class="pill info">DJ</span><span>Radio management</span></div>
        </div>
      </div>
    </div>
  `;
}

function renderLoginOverlay() {
  return `<div class="modal-backdrop" id="loginOverlay">
    <div class="login-card" style="position:relative">
      <button class="login-close" id="loginCloseBtn">✕</button>
      <div class="login-logo">
        <div class="login-mark">CT</div>
        <div class="login-logo-text">
          <strong>ChillTopia</strong>
          <span>Owner / Staff Login</span>
        </div>
      </div>
      <h1>Welcome back</h1>
      <p class="subtitle">Sign in to access the control panel</p>
      ${state.error ? `<div class="notice error" style="margin-bottom:14px">${esc(state.error)}</div>` : ""}
      <form id="loginForm">
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
    </div>
  </div>`;
}

function bindPublicEvents() {
  document.querySelectorAll("[data-pub-page]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.publicPage = btn.dataset.pubPage;
      state.error = "";
      loadPublic();
    });
  });
  document.getElementById("pubLoginBtn")?.addEventListener("click", () => {
    state.showLoginOverlay = true;
    state.error = "";
    render();
  });
  document.getElementById("loginCloseBtn")?.addEventListener("click", () => {
    state.showLoginOverlay = false;
    state.error = "";
    render();
  });
  document.getElementById("loginOverlay")?.addEventListener("click", (e) => {
    if (e.target.id === "loginOverlay") {
      state.showLoginOverlay = false;
      state.error = "";
      render();
    }
  });
  document.getElementById("loginForm")?.addEventListener("submit", login);
}

/* ═══════════════════════════════════════════════════════
   ADMIN PORTAL
══════════════════════════════════════════════════════ */
function renderAdmin() {
  const initials = (state.user?.username || "U").slice(0, 2).toUpperCase();
  let lastGroup = null;
  const navHtml = ADMIN_NAV.map((item) => {
    let groupHdr = "";
    if (item.group !== lastGroup) {
      groupHdr = `<div class="nav-label" style="margin-top:${lastGroup ? '14px' : '0'}">${item.group}</div>`;
      lastGroup = item.group;
    }
    return `${groupHdr}<button class="${item.id === state.adminPage ? "active" : ""}" data-admin-page="${esc(item.id)}">
      <span class="nav-icon">${item.icon}</span>${esc(item.label)}
    </button>`;
  }).join("");

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
        <nav class="nav">${navHtml}</nav>
        <div class="sidebar-footer">
          <button class="btn ghost pub-portal-btn" id="pubPortalBtn">← Public Portal</button>
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
            <h1>${esc(state.adminPage)}</h1>
            <div class="page-desc">${esc(PAGE_DESC[state.adminPage] || "")}</div>
          </div>
          <div class="topbar-actions">
            <button class="btn ghost" id="refreshBtn">↻ Refresh</button>
          </div>
        </div>
        ${state.error ? `<div class="notice error" style="margin-bottom:16px">${esc(state.error)}</div>` : ""}
        ${state.notice ? `<div class="notice success" style="margin-bottom:16px">${esc(state.notice)}</div>` : ""}
        <section class="page">${renderAdminPage()}</section>
      </main>
    </div>
    ${state.modal ? renderModal() : ""}
  `;

  document.querySelectorAll("[data-admin-page]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.adminPage = btn.dataset.adminPage;
      state.notice = "";
      state.error = "";
      state.sidebarOpen = false;
      loadAdmin();
    });
  });
  document.getElementById("refreshBtn").addEventListener("click", loadAdmin);
  document.getElementById("logoutBtn").addEventListener("click", logout);
  document.getElementById("pubPortalBtn")?.addEventListener("click", () => {
    state.user = null;
    state.csrf = "";
    state.data = null;
    loadPublic();
  });
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
  if (state.modal) {
    document.getElementById("modalConfirm")?.addEventListener("click", runModalAction);
    document.getElementById("modalCancel")?.addEventListener("click", () => { state.modal = null; render(); });
  }
  bindAdminPageEvents();
}

function renderAdminPage() {
  if (!state.data && !["Player Control","Emotes"].includes(state.adminPage)) {
    return `<div class="notice">Loading or unavailable.</div>`;
  }
  const map = {
    Overview:        renderOverview,
    "Live Tracker":  renderLive,
    "Bot Control":   renderBotControl,
    "Bot Config":    renderBotConfig,
    Radio:           renderRadio,
    Casino:          renderCasino,
    Games:           renderGames,
    Titles:          renderTitles,
    "Player Control":renderPlayerControl,
    Economy:         renderEconomy,
    "Room Control":  renderRoomControl,
    Emotes:          renderEmotes,
    Staff:           renderStaff,
    Logs:            renderLogs,
    Emergency:       renderEmergency,
  };
  return map[state.adminPage]?.() ?? `<div class="notice">Unknown page.</div>`;
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

/* ── Admin Pages ─────────────────────────────────────── */
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
      <div class="card-header"><h2>🎵 Now Playing</h2></div>
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

function renderBotControl() {
  const bots = state.data?.bots || [];
  const rawCount = state.data?.raw_count ?? bots.length;
  if (!bots.length) {
    return `<div class="card">
      <h2>🤖 Bot Control</h2>
      <div class="notice warn" style="margin-bottom:16px">No bot heartbeat data yet. Bots write to <code>bot_instances</code> on startup — ensure bots are running and have connected at least once.</div>
      <p class="muted text-sm">Spawn/stop/emote controls: <span class="pill warn">Backend endpoint needed</span> /api/bot-control/spawn · /stop · /emote</p>
    </div>`;
  }
  const dupeNote = rawCount > bots.length
    ? `<div class="notice" style="margin-bottom:16px">ℹ️ ${rawCount} raw rows deduped to ${bots.length} bots (latest heartbeat per mode kept).</div>`
    : "";
  return `
    ${dupeNote}
    <div class="grid">
      ${bots.map((b) => {
        const isOnline = String(b.status || "").toLowerCase() === "online";
        return `<div class="card">
          <div class="card-header">
            <h2>${esc(b.display_name || b.bot_mode || b.bot_username || "Bot")}</h2>
            ${pill(b.status || "unknown")}
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:10px">
            <span class="muted text-sm">@${esc(b.bot_username || "—")}</span>
            <span class="pill def">${esc(b.bot_mode || "—")}</span>
            ${b.has_duplicate_raw_rows ? `<span class="pill warn" title="Multiple rows in bot_instances for this mode">dupes</span>` : ""}
            ${b.enabled === 0 ? `<span class="pill bad">disabled</span>` : ""}
          </div>
          <div style="display:grid;gap:4px;font-size:13px;margin-bottom:14px">
            <div class="muted">Room: ${esc(b.current_room_id || "—")}</div>
            <div class="muted">Heartbeat: ${esc(b.last_heartbeat_at || "—")}</div>
            ${b.last_error ? `<div class="muted text-sm" style="color:#ffaaa5">⚠ ${esc(String(b.last_error).slice(0, 120))}</div>` : ""}
          </div>
          <div class="inline-actions">
            <button class="btn sm" disabled title="Backend endpoint needed: /api/bot-control/spawn">⬆ Spawn</button>
            <button class="btn danger sm" disabled title="Backend endpoint needed: /api/bot-control/stop">⏹ Stop</button>
            <button class="btn cyan sm" disabled title="Backend endpoint needed: /api/bot-control/emote">💃 Emote</button>
          </div>
          <div class="muted text-sm" style="margin-top:8px">Per-bot spawn/stop/emote: <span class="pill warn">endpoint needed</span></div>
        </div>`;
      }).join("")}
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
        <p class="muted text-sm" style="margin-bottom:14px">⚠️ Tokens are stored in Replit Secrets and <strong>never displayed</strong>. Status shows SET or EMPTY only.</p>
        ${tokenRows}
        <div style="margin-top:14px">
          <p class="muted text-sm">Token fields are for future update support — set actual tokens in Replit environment secrets.</p>
        </div>
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
          </div>` : `<div class="notice">Nothing currently playing.</div>`}
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

function renderPlayerControl() {
  return `
    <div class="grid">
      <div class="card">
        <h2>Search Player</h2>
        <form id="playerSearchForm">
          <div class="field"><label class="field-label">Username or User ID</label><input name="query" placeholder="Enter Highrise username or ID" required /></div>
          <button class="btn primary">Search</button>
        </form>
        <div id="playerSearchResult" style="margin-top:14px"></div>
      </div>
      <div class="card">
        <h2>Edit Player Economy</h2>
        ${endpointNeeded("Coin/ticket/XP editing requires a /api/player/:id/economy endpoint.")}
      </div>
    </div>
    <div class="grid">
      <div class="card">
        <h2>Badges & Titles</h2>
        ${endpointNeeded("Give/remove badge and title actions require a /api/player/:id/badges endpoint.")}
      </div>
      <div class="card">
        <h2>Inventory</h2>
        ${endpointNeeded("Player inventory editing requires a /api/player/:id/inventory endpoint.")}
      </div>
    </div>
  `;
}

function renderEconomy() {
  const d = state.data || {};
  const s = d.stats;
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr))">
      ${metricCard("Players", s?.player_count ?? "—", "in database", "", "👤")}
      ${metricCard("Total Coins", s?.total_coins != null ? Number(s.total_coins).toLocaleString() : "—", "in circulation", "accent-green", "💰")}
      ${metricCard("Total Tickets", s?.total_tickets != null ? Number(s.total_tickets).toLocaleString() : "—", "in circulation", "", "🎟")}
      ${metricCard("Avg Coins", s?.avg_coins != null ? Math.round(Number(s.avg_coins)).toLocaleString() : "—", "per player", "", "📊")}
      ${metricCard("Richest Balance", s?.richest != null ? Number(s.richest).toLocaleString() : "—", "single player", "", "🏆")}
    </div>
    <div class="grid">
      <div class="card">
        <h2>💰 Rich List</h2>
        ${table(d.top_rich || [], [
          { key: "username", label: "Player" },
          { key: "coins", label: "Coins", render: (r) => Number(r.coins ?? 0).toLocaleString() },
          { key: "level", label: "Level" },
          { key: "xp", label: "XP", render: (r) => Number(r.xp ?? 0).toLocaleString() },
        ])}
      </div>
      <div class="card">
        <h2>📈 Top XP</h2>
        ${table(d.top_xp || [], [
          { key: "username", label: "Player" },
          { key: "xp", label: "XP", render: (r) => Number(r.xp ?? 0).toLocaleString() },
          { key: "level", label: "Level" },
        ])}
      </div>
    </div>
    <div class="card">
      <h2>🔧 Economy Write Actions</h2>
      <div style="display:grid;gap:8px">
        ${endpointNeeded("Coin/ticket/XP editing: POST /api/player/:id/economy")}
        ${endpointNeeded("Bulk coin adjustments: POST /api/economy/adjust")}
        ${endpointNeeded("Transaction history: GET /api/economy/transactions")}
      </div>
    </div>
  `;
}

function renderRoomControl() {
  const d = state.data || {};
  const known = d.known_settings || {};

  function boolRow(key, label) {
    const val = known[key];
    const isSet = val !== undefined;
    const checked = val === "true" || val === "1";
    return `<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid #1e2822">
      <span>${esc(label)}</span>
      <div style="display:flex;align-items:center;gap:8px">
        ${isSet ? pill(checked ? "enabled" : "disabled") : `<span class="muted text-sm">not set</span>`}
        <label class="switch" style="margin:0">
          <input type="checkbox" data-room-toggle="${esc(key)}" ${checked ? "checked" : ""} ${!isSet ? "" : ""} />
          <span></span>
        </label>
      </div>
    </div>`;
  }

  const extra = d.extra_settings || [];
  return `
    <div class="grid">
      <div class="card">
        <h2>🔧 Room Toggles</h2>
        <p class="muted text-sm" style="margin-bottom:12px">Writes to <code>room_settings</code> table — bots consume these flags.</p>
        ${boolRow("welcome_enabled", "Welcome messages")}
        ${boolRow("maintenance_mode", "Maintenance mode")}
        ${boolRow("public_emotes_enabled", "Public emotes")}
        ${boolRow("social_enabled", "Social features")}
        ${boolRow("self_teleport_enabled", "Self teleport")}
        ${boolRow("announcements_enabled", "Announcements")}
        ${boolRow("daily_enabled", "Daily rewards")}
        ${boolRow("mining_enabled", "Mining")}
        ${boolRow("fishing_enabled", "Fishing")}
      </div>
      <div class="card">
        <h2>👋 Welcome Message</h2>
        <form class="roomSettingForm" data-key="welcome_message" style="display:grid;gap:10px">
          <textarea name="value" rows="4" placeholder="Enter welcome message for new players" style="width:100%;box-sizing:border-box;background:#0e120f;color:#fff;border:1px solid #334037;border-radius:6px;padding:10px;font:inherit;resize:vertical">${esc(known.welcome_message ?? "")}</textarea>
          <button class="btn primary sm">💾 Save Welcome Message</button>
        </form>
        <div style="margin-top:16px">
          ${boolRow("welcome_enabled", "Enable welcome messages")}
        </div>
      </div>
    </div>
    <div class="card">
      <h2>⚙️ Generic Room Setting Editor</h2>
      <p class="muted text-sm" style="margin-bottom:12px">Edit any <code>room_settings</code> key. Writes are audit-logged.</p>
      <form id="roomSettingRawForm" class="toolbar" style="flex-wrap:wrap;margin-bottom:16px">
        <input name="key" placeholder="setting_key" required style="flex:2;min-width:120px" />
        <input name="value" placeholder="value" required style="flex:3;min-width:120px" />
        <button class="btn primary sm">Save</button>
      </form>
      ${extra.length ? `<div class="muted text-sm" style="margin-bottom:8px">Other room settings (${extra.length}):</div>
        ${table(extra, [{ key: "key", label: "Key" }, { key: "value", label: "Value" }],
          (r) => `<button class="btn sm" data-room-edit="${esc(r.key)}" data-room-val="${esc(r.value)}">Edit</button>`)}` :
        `<div class="notice">No other room settings found.</div>`}
    </div>
    <div class="grid">
      <div class="card">
        <h2>📢 Send Announcement</h2>
        ${endpointNeeded("Room announcements: POST /api/room/announce consumed by the host bot.")}
      </div>
      <div class="card">
        <h2>🚩 Quick Emergency</h2>
        <p class="muted text-sm" style="margin-bottom:12px">Use the Emergency page for module kill-switches.</p>
        <button class="btn" data-admin-page="Emergency">→ Go to Emergency</button>
      </div>
    </div>
  `;
}

function renderEmotes() {
  return `
    <div class="grid">
      <div class="card">
        <h2>🤖 Bot Emotes</h2>
        ${endpointNeeded("Bot emote triggers require a /api/room/emote endpoint consumed by the active bot.")}
      </div>
      <div class="card">
        <h2>💃 Dancefloor</h2>
        ${endpointNeeded("Dancefloor sync controls require a /api/room/dancefloor endpoint.")}
      </div>
      <div class="card">
        <h2>🎭 Custom Packs</h2>
        ${endpointNeeded("Custom emote packs require a /api/room/emote-packs endpoint.")}
      </div>
      <div class="card">
        <h2>🔄 Sync All</h2>
        ${endpointNeeded("Sync emotes across all bots requires a /api/room/emote-sync endpoint.")}
      </div>
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
    <div class="card">
      <h2>Admin Action Logs</h2>
      ${table(d.admin_action_logs)}
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
      </div>
    </div>
    <div class="card">
      <h2>Current Module Flags</h2>
      ${moduleFlagTable(state.data?.module_flags || [])}
    </div>
  `;
}

/* ── Shared Admin Helpers ────────────────────────────── */
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
    <input type="checkbox" name="perm_${p}" ${perms[p] ? "checked" : ""} />
    <span>${p.replace(/_/g," ")}</span>
  </label>`).join("")}</div>`;
}

/* ── Admin Event Binding ─────────────────────────────── */
function bindAdminPageEvents() {
  document.querySelector("#botConfigForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.currentTarget));
    await action("Config saved.", () => api("/api/bot-config", { method: "POST", body: JSON.stringify(body) }));
  });

  document.getElementById("restartBotsBtn")?.addEventListener("click", () => {
    confirmAction("Restart Bots", "Write a restart flag to the DB. Bots will restart on their next heartbeat check.", async () => {
      await action("Restart flag written.", () => api("/api/bot-config/restart", { method: "POST", body: JSON.stringify({}) }));
    });
  });

  document.querySelector('[data-action="radio-skip"]')?.addEventListener("click", () => {
    confirmAction("Skip Song", "Request a skip of the current song.", async () => {
      await action("Skip requested.", () => api("/api/radio/skip", { method: "POST", body: JSON.stringify({}) }));
    });
  });

  document.querySelector('[data-action="radio-clear"]')?.addEventListener("click", () => {
    confirmAction("Clear Queue", "Cancel all pending song requests.", async () => {
      await action("Queue cleared.", () => api("/api/radio/clear", { method: "POST", body: JSON.stringify({}) }));
    });
  });

  document.getElementById("requestsEnabled")?.addEventListener("change", async (e) => {
    await action(e.target.checked ? "Requests enabled." : "Requests disabled.", () =>
      api("/api/radio/requests-enabled", { method: "PUT", body: JSON.stringify({ enabled: e.target.checked }) }));
  });

  document.querySelectorAll("[data-remove-request]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.removeRequest;
      confirmAction("Remove Request", `Remove song request #${id} from the queue?`, async () => {
        await action("Request removed.", () => api(`/api/radio/requests/${id}/remove`, { method: "POST", body: JSON.stringify({}) }));
      });
    });
  });

  document.querySelectorAll("[data-emergency]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const flag = btn.dataset.emergency;
      confirmAction("Confirm Emergency Action", `Apply emergency flag: ${flag}? This disables the system immediately.`, async () => {
        await action(`Emergency: ${flag} applied.`, () =>
          api("/api/emergency", { method: "POST", body: JSON.stringify({ flags: { [flag]: true } }) }));
      });
    });
  });

  document.querySelectorAll("[data-toggle-module]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const mod = btn.dataset.toggleModule;
      const enable = btn.dataset.enabled === "1";
      confirmAction(`${enable ? "Enable" : "Disable"} Module`, `${enable ? "Enable" : "Disable"} the ${mod} module?`, async () => {
        await action(`Module ${mod} ${enable ? "enabled" : "disabled"}.`, () =>
          api("/api/settings", { method: "PUT", body: JSON.stringify({ key: `module.${mod}.enabled`, value: enable ? "true" : "false", module: mod }) }));
      });
    });
  });

  document.querySelectorAll("[data-module-checkbox]").forEach((cb) => {
    cb.addEventListener("change", async (e) => {
      const mod = cb.dataset.moduleCheckbox;
      const enabled = e.target.checked;
      await action(`Module ${mod} ${enabled ? "enabled" : "disabled"}.`, () =>
        api("/api/settings", { method: "PUT", body: JSON.stringify({ key: `module.${mod}.enabled`, value: enabled ? "true" : "false", module: mod }) }));
    });
  });

  document.querySelectorAll(".settingForm").forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const data = Object.fromEntries(new FormData(form));
      const mod = form.dataset.module || data.module || "global";
      const route = { casino: "/api/casino", games: "/api/games" }[mod] || "/api/settings";
      await action("Setting saved.", () =>
        api(`${route}/${encodeURIComponent(data.key)}`, { method: "PUT", body: JSON.stringify({ value: data.value }) }));
    });
  });

  document.querySelectorAll("[data-edit-setting]").forEach((btn) => {
    const key = btn.dataset.editSetting;
    const mod = btn.dataset.module || "global";
    const value = btn.dataset.value || "";
    btn.addEventListener("click", () => {
      const route = { casino: "/api/casino", games: "/api/games" }[mod] || "/api/settings";
      const newVal = prompt(`Edit "${key}" (current: ${value})`, value);
      if (newVal !== null) {
        action("Setting saved.", () =>
          api(`${route}/${encodeURIComponent(key)}`, { method: "PUT", body: JSON.stringify({ value: newVal }) }));
      }
    });
  });

  document.getElementById("assignTitleForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.currentTarget));
    await action("Title assigned.", () => api("/api/titles/assign", { method: "POST", body: JSON.stringify(body) }));
  });

  document.getElementById("staffCreateForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    const perms = {};
    for (const [key, val] of Object.entries(data)) {
      if (key.startsWith("perm_")) perms[key.slice(5)] = val === "on";
    }
    const body = { username: data.username, password: data.password, role: data.role, permissions: perms };
    await action("Staff account created.", () => api("/api/staff", { method: "POST", body: JSON.stringify(body) }));
  });

  document.getElementById("botRoleForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const submitter = e.submitter;
    const data = Object.fromEntries(new FormData(e.currentTarget));
    const body = { username: data.username, role: data.role, action: submitter?.value || "add" };
    await action(`Bot role ${body.action === "add" ? "added" : "removed"}.`, () =>
      api("/api/staff/bot-role", { method: "POST", body: JSON.stringify(body) }));
  });

  document.querySelectorAll("[data-remove-staff]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.removeStaff;
      confirmAction("Remove Staff", `Disable dashboard account #${id}?`, async () => {
        await action("Staff account disabled.", () =>
          api(`/api/staff/${id}`, { method: "DELETE", body: JSON.stringify({}) }));
      });
    });
  });

  document.querySelectorAll("[data-edit-staff]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.editStaff;
      const role = prompt("Role (owner/staff):", btn.dataset.role);
      if (!role) return;
      const body = { role, disabled: btn.dataset.disabled === "1", permissions: {} };
      action("Staff updated.", () => api(`/api/staff/${id}`, { method: "PUT", body: JSON.stringify(body) }));
    });
  });

  document.getElementById("playerSearchForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const query = e.currentTarget.querySelector('[name="query"]').value.trim();
    const result = document.getElementById("playerSearchResult");
    if (!result) return;
    result.innerHTML = `<div class="notice">Searching…</div>`;
    try {
      const data = await api(`/api/player/search?q=${encodeURIComponent(query)}`);
      if (!data.player) {
        result.innerHTML = `<div class="notice">No player found for <strong>${esc(query)}</strong>.</div>`;
      } else {
        const p = data.player;
        result.innerHTML = `<div class="card" style="margin-top:0">
          <div class="card-header">
            <h3>🔍 ${esc(p.username || "Unknown")}</h3>
            <span class="muted text-sm">ID: ${esc(p.user_id || "—")}</span>
          </div>
          <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin:12px 0">
            ${metricCard("Coins", Number(p.coins ?? 0).toLocaleString(), "", "accent-green", "💰")}
            ${metricCard("Tickets", p.tickets != null ? Number(p.tickets).toLocaleString() : "—", "", "", "🎟")}
            ${metricCard("Level", p.level ?? "—", "", "", "⭐")}
            ${metricCard("XP", p.xp != null ? Number(p.xp).toLocaleString() : "—", "", "", "📈")}
            ${metricCard("Casino", p.casino_winnings != null ? Number(p.casino_winnings).toLocaleString() : "—", "winnings", "", "🎲")}
            ${metricCard("Items", p.owned_item_count ?? 0, "owned", "", "🎒")}
          </div>
          ${p.titles?.length ? `<div class="muted text-sm">Titles: ${p.titles.map((t) => esc(t.title_id)).join(", ")}</div>` : ""}
          ${p.last_seen_at ? `<div class="muted text-sm" style="margin-top:6px">Last seen: ${esc(p.last_seen_at)}</div>` : ""}
        </div>
        <div class="card" style="margin-top:12px">
          <h3>🔧 Edit Actions</h3>
          ${endpointNeeded("Coin/ticket/XP editing: POST /api/player/:id/economy")}
          ${endpointNeeded("Badge/title give-remove: POST /api/player/:id/badges")}
          ${endpointNeeded("Inventory edit: POST /api/player/:id/inventory")}
        </div>`;
      }
    } catch (err) {
      result.innerHTML = `<div class="notice error">${esc(err.message)}</div>`;
    }
  });

  document.querySelectorAll("[data-room-toggle]").forEach((cb) => {
    cb.addEventListener("change", async (e) => {
      const key = cb.dataset.roomToggle;
      const value = e.target.checked ? "true" : "false";
      await action(`${key} set to ${value}.`, () =>
        api(`/api/settings/${encodeURIComponent(key)}`, { method: "PUT", body: JSON.stringify({ value, source: "room_settings" }) }));
    });
  });

  document.querySelectorAll(".roomSettingForm").forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const key = form.dataset.key;
      const value = form.querySelector("[name='value']")?.value ?? "";
      await action(`${key} saved.`, () =>
        api(`/api/settings/${encodeURIComponent(key)}`, { method: "PUT", body: JSON.stringify({ value, source: "room_settings" }) }));
    });
  });

  document.getElementById("roomSettingRawForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    if (!data.key) return;
    await action(`${data.key} saved.`, () =>
      api(`/api/settings/${encodeURIComponent(data.key)}`, { method: "PUT", body: JSON.stringify({ value: data.value, source: "room_settings" }) }));
  });

  document.querySelectorAll("[data-room-edit]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.roomEdit;
      const cur = btn.dataset.roomVal || "";
      const newVal = prompt(`Edit room setting "${key}" (current: ${cur})`, cur);
      if (newVal !== null) {
        action(`${key} saved.`, () =>
          api(`/api/settings/${encodeURIComponent(key)}`, { method: "PUT", body: JSON.stringify({ value: newVal, source: "room_settings" }) }));
      }
    });
  });

  document.querySelector('[data-action="logs-filter"]')?.addEventListener("click", () => {
    state.logs.action_type = document.getElementById("logAction")?.value || "";
    state.logs.user = document.getElementById("logUser")?.value || "";
    state.logs.module = document.getElementById("logModule")?.value || "";
    state.logs.offset = 0;
    loadAdmin();
  });
  document.querySelector('[data-action="logs-prev"]')?.addEventListener("click", () => {
    state.logs.offset = Math.max(0, (state.logs.offset || 0) - 50);
    loadAdmin();
  });
  document.querySelector('[data-action="logs-next"]')?.addEventListener("click", () => {
    state.logs.offset = (state.logs.offset || 0) + 50;
    loadAdmin();
  });

  document.querySelectorAll("[data-admin-page]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.adminPage = btn.dataset.adminPage;
      state.notice = "";
      state.error = "";
      loadAdmin();
    });
  });

  if (state.modal) {
    document.getElementById("modalConfirm")?.addEventListener("click", runModalAction);
    document.getElementById("modalCancel")?.addEventListener("click", () => { state.modal = null; render(); });
  }
}

/* ── Boot ────────────────────────────────────────────── */
init();
