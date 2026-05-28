/* ═══════════════════════════════════════════════════════
   ChillTopia Web Portal — app.js
   3-layer: Public Portal → Staff Console → Owner Control Center
══════════════════════════════════════════════════════ */

/* ── Public Nav ──────────────────────────────────────── */
const PUBLIC_NAV = [
  { id: "home",      icon: "🏠", label: "Home" },
  { id: "radio",     icon: "📻", label: "Radio" },
  { id: "howtoplay", icon: "📖", label: "How to Play" },
  { id: "events",    icon: "🎉", label: "Events" },
  { id: "rankings",  icon: "🏆", label: "Rankings" },
  { id: "roominfo",  icon: "ℹ️",  label: "Room Info" },
];

/* ── Owner Nav ───────────────────────────────────────── */
const OWNER_NAV = [
  { id: "Command Center",    icon: "⚡", label: "Command Center",    group: "Monitor" },
  { id: "Bots",              icon: "🤖", label: "Bots",              group: "Control" },
  { id: "Players",           icon: "👤", label: "Players",           group: "Control" },
  { id: "Room & Content",    icon: "🏠", label: "Room & Content",    group: "Control" },
  { id: "Economy & Rewards", icon: "💰", label: "Economy & Rewards", group: "Control" },
  { id: "Staff",             icon: "👥", label: "Staff",             group: "Admin" },
  { id: "System",            icon: "⚙️", label: "System",            group: "Admin" },
];

/* ── Staff Nav ───────────────────────────────────────── */
const STAFF_NAV = [
  { id: "Staff Home",  icon: "📊", label: "Staff Home",  group: "Dashboard" },
  { id: "Radio Queue", icon: "🎵", label: "Radio Queue",  group: "Tools" },
  { id: "Players",     icon: "👤", label: "Players",      group: "Tools" },
  { id: "Events",      icon: "🎉", label: "Events",       group: "Tools" },
  { id: "Room Tools",  icon: "🔧", label: "Room Tools",   group: "Tools" },
  { id: "Logs",        icon: "📋", label: "Logs",         group: "Admin" },
];

/* ── Page Tabs ───────────────────────────────────────── */
const PAGE_TABS = {
  "Bots":              ["Bot Status", "Bot Config", "Bot Settings", "Bot Spawns", "Advanced Debug"],
  "Players":           ["Search", "Titles & Badges", "Moderation"],
  "Room & Content":    ["Room Settings", "Radio", "Events", "Announcements", "Welcome", "Emotes"],
  "Economy & Rewards": ["Coins & Tickets", "Casino", "Games", "VIP", "Titles", "Badges", "Rewards"],
  "System":            ["Health", "Logs", "Settings Audit", "Emergency", "Database"],
};

/* ── Page Descriptions ───────────────────────────────── */
const PAGE_DESC = {
  "Command Center":    "All systems at a glance — quick actions and health overview",
  "Bots":              "Bot status, configuration and control",
  "Players":           "Player search, titles, moderation",
  "Room & Content":    "Room settings, radio, events and announcements",
  "Economy & Rewards": "Coins, tickets, VIP, titles and rewards",
  "Staff":             "Dashboard users, permissions and bot roles",
  "System":            "Health monitoring, logs and emergency controls",
  "Staff Home":        "Room health and pending attention items",
  "Radio Queue":       "DJ queue management and radio controls",
  "Events":            "Current and upcoming room events",
  "Room Tools":        "Announcements, welcome messages and room flags",
  "Logs":              "Audit trail and command error logs",
};

/* ── Settings Schema ─────────────────────────────────── */
const SETTINGS_SCHEMA = {
  casino: [
    {
      title: "Blackjack Settings — AceSinatra",
      description: "Primary shoe-based blackjack rules.",
      api: "/api/casino/blackjack-settings",
      keys: [
        { key: "rbj_enabled",          label: "Enabled",              type: "toggle" },
        { key: "min_bet",              label: "Min Bet",              type: "number", suffix: "coins" },
        { key: "max_bet",              label: "Max Bet",              type: "number", suffix: "coins" },
        { key: "max_players",          label: "Max Players",          type: "number" },
        { key: "rbj_action_timer",     label: "Action Timer",         type: "number", suffix: "sec" },
        { key: "decks",                label: "Number of Decks",      type: "number", placeholder: "6" },
        { key: "shuffle_used_percent", label: "Shuffle Used Percent", type: "number", suffix: "%" },
        { key: "win_payout",           label: "Win Payout",           type: "number", suffix: "x" },
        { key: "blackjack_payout",     label: "Blackjack Payout",     type: "number", suffix: "x" },
        { key: "dealer_hits_soft_17",  label: "Dealer Hits Soft 17",  type: "toggle" },
        { key: "lobby_countdown",      label: "Lobby Countdown",      type: "number", suffix: "sec" },
        { key: "rbj_daily_win_limit",  label: "Daily Win Limit",      type: "number", suffix: "coins" },
      ],
    },
    {
      title: "Poker Settings",
      description: "Texas Hold'em poker table settings",
      api: "/api/casino/:key",
      keys: [
        { key: "poker_enabled",      label: "Enabled",    type: "toggle" },
        { key: "poker_min_buyin",    label: "Min Buy-In", type: "number", suffix: "coins" },
        { key: "poker_max_buyin",    label: "Max Buy-In", type: "number", suffix: "coins" },
        { key: "poker_small_blind",  label: "Small Blind",type: "number", suffix: "coins" },
        { key: "poker_big_blind",    label: "Big Blind",  type: "number", suffix: "coins" },
        { key: "poker_max_players",  label: "Max Players",type: "number", placeholder: "8" },
        { key: "poker_turn_timer",   label: "Turn Timer", type: "number", suffix: "sec" },
        { key: "poker_lobby_timer",  label: "Lobby Timer",type: "number", suffix: "sec" },
      ],
    },
    {
      title: "Casino Global Settings",
      description: "Settings that apply across all casino games",
      api: "/api/casino/:key",
      keys: [
        { key: "casino_enabled",      label: "Casino Enabled",    type: "toggle" },
        { key: "daily_coin_limit",    label: "Daily Coin Limit",  type: "number", suffix: "coins" },
        { key: "daily_reset_hour",    label: "Daily Reset Hour",  type: "number", suffix: "h UTC", placeholder: "0" },
      ],
    },
  ],
  games: [
    {
      title: "Trivia Settings",
      description: "In-room trivia game configuration",
      api: "/api/games/:key",
      keys: [
        { key: "trivia.enabled",       label: "Enabled",              type: "toggle" },
        { key: "trivia.reward_coins",  label: "Reward per Correct",   type: "number", suffix: "coins" },
        { key: "trivia.timer",         label: "Answer Timer",         type: "number", suffix: "sec" },
        { key: "trivia.cooldown",      label: "Cooldown",             type: "number", suffix: "sec" },
        { key: "trivia.max_rounds",    label: "Max Rounds",           type: "number" },
      ],
    },
    {
      title: "Scramble Settings",
      description: "Word scramble game configuration",
      api: "/api/games/:key",
      keys: [
        { key: "scramble.enabled",      label: "Enabled",          type: "toggle" },
        { key: "scramble.reward_coins", label: "Reward per Win",   type: "number", suffix: "coins" },
        { key: "scramble.timer",        label: "Answer Timer",     type: "number", suffix: "sec" },
        { key: "scramble.cooldown",     label: "Cooldown",         type: "number", suffix: "sec" },
      ],
    },
    {
      title: "Riddle Settings",
      description: "Riddle game configuration",
      api: "/api/games/:key",
      keys: [
        { key: "riddle.enabled",      label: "Enabled",           type: "toggle" },
        { key: "riddle.reward_coins", label: "Reward per Correct",type: "number", suffix: "coins" },
        { key: "riddle.timer",        label: "Answer Timer",      type: "number", suffix: "sec" },
        { key: "riddle.cooldown",     label: "Cooldown",          type: "number", suffix: "sec" },
      ],
    },
    {
      title: "Auto Games",
      description: "Automatic game scheduling",
      api: "/api/games/:key",
      keys: [
        { key: "games.auto_enabled",  label: "Auto Games Enabled",type: "toggle" },
        { key: "games.auto_interval", label: "Auto Interval",     type: "number", suffix: "min" },
        { key: "games.auto_types",    label: "Game Types",        type: "text", placeholder: "trivia,scramble,riddle" },
      ],
    },
    {
      title: "Games Rewards",
      description: "XP and ticket rewards for mini-games",
      api: "/api/games/:key",
      keys: [
        { key: "games.xp_per_win",    label: "XP per Win",        type: "number" },
        { key: "games.ticket_per_win",label: "Tickets per Win",   type: "number" },
        { key: "games.bonus_streak",  label: "Streak Bonus Round",type: "number", placeholder: "5" },
      ],
    },
  ],
  radio: [
    {
      title: "Request Settings",
      description: "Controls for the song request system",
      api: "/api/settings/:key",
      apiOpts: { source: "room_settings" },
      keys: [
        { key: "radio_requests_enabled",   label: "Requests Enabled",     type: "toggle" },
        { key: "radio_max_queue_size",     label: "Max Queue Size",       type: "number", placeholder: "50" },
        { key: "radio_max_per_user",       label: "Max Per User",         type: "number", placeholder: "3" },
        { key: "radio_cooldown_min",       label: "Request Cooldown",     type: "number", suffix: "min" },
        { key: "radio_vip_queue_priority", label: "VIP Priority Queue",   type: "toggle" },
        { key: "radio_vip_max_per_user",   label: "VIP Max Per User",     type: "number" },
      ],
    },
    {
      title: "Queue Settings",
      description: "Now-playing and skip behaviour",
      api: "/api/settings/:key",
      apiOpts: { source: "room_settings" },
      keys: [
        { key: "radio_skip_votes_required",label: "Skip Votes Required",  type: "number", placeholder: "3" },
        { key: "radio_announce_np",        label: "Announce Now Playing", type: "toggle" },
        { key: "radio_announce_up_next",   label: "Announce Up Next",     type: "toggle" },
        { key: "radio_dj_prefix",          label: "DJ Announce Prefix",   type: "text",   placeholder: "🎵" },
      ],
    },
    {
      title: "AutoDJ Settings",
      description: "Fallback automatic DJ when queue is empty",
      api: "/api/settings/:key",
      apiOpts: { source: "room_settings" },
      keys: [
        { key: "autodj_enabled",  label: "AutoDJ Enabled",  type: "toggle" },
        { key: "autodj_playlist", label: "Playlist",        type: "text", placeholder: "playlist ID or name" },
        { key: "autodj_shuffle",  label: "Shuffle Playlist",type: "toggle" },
      ],
    },
  ],
  room: [
    {
      title: "Welcome Settings",
      description: "Welcome message sent to players when they join",
      api: "/api/settings/:key",
      apiOpts: { source: "room_settings" },
      keys: [
        { key: "welcome_enabled",    label: "Welcome Enabled",   type: "toggle" },
        { key: "welcome_message",    label: "Welcome Message",   type: "textarea", placeholder: "Welcome to ChillTopia! 🎉" },
        { key: "welcome_delay_sec",  label: "Send Delay",        type: "number", suffix: "sec", placeholder: "2" },
        { key: "welcome_vip_extra",  label: "VIP Extra Message", type: "textarea" },
      ],
    },
    {
      title: "Announcement Settings",
      description: "Room-wide announcement configuration",
      api: "/api/settings/:key",
      apiOpts: { source: "room_settings" },
      keys: [
        { key: "announcements_enabled",         label: "Announcements Enabled",    type: "toggle" },
        { key: "announcement_interval_min",     label: "Auto Interval",            type: "number", suffix: "min" },
        { key: "announce_big_find_threshold",   label: "Big Find Threshold",       type: "number", suffix: "lbs" },
        { key: "announce_big_catch_threshold",  label: "Big Catch Threshold",      type: "number", suffix: "lbs" },
      ],
    },
    {
      title: "Social Settings",
      description: "Social interactions and public room features",
      api: "/api/settings/:key",
      apiOpts: { source: "room_settings" },
      keys: [
        { key: "social_enabled",          label: "Social Enabled",    type: "toggle" },
        { key: "public_emotes_enabled",   label: "Public Emotes",     type: "toggle" },
        { key: "dancefloor_enabled",      label: "Dancefloor",        type: "toggle" },
        { key: "self_teleport_enabled",   label: "Self Teleport",     type: "toggle" },
        { key: "daily_enabled",           label: "Daily Rewards",     type: "toggle" },
        { key: "mining_enabled",          label: "Mining",            type: "toggle" },
        { key: "fishing_enabled",         label: "Fishing",           type: "toggle" },
      ],
    },
    {
      title: "Moderation Settings",
      description: "Auto-mod thresholds and kick behaviour",
      api: "/api/settings/:key",
      apiOpts: { source: "room_settings" },
      keys: [
        { key: "maintenance_mode",   label: "Maintenance Mode",    type: "toggle" },
        { key: "automod_enabled",    label: "Auto-Mod Enabled",    type: "toggle" },
        { key: "spam_threshold",     label: "Spam Threshold",      type: "number", suffix: "msgs/30s" },
        { key: "warn_before_kick",   label: "Warn Before Kick",    type: "toggle" },
      ],
    },
  ],
  bots: [
    {
      title: "Bot Runtime Settings",
      description: "How bots behave at runtime — read on next heartbeat",
      api: "/api/settings/:key",
      apiOpts: { source: "room_settings" },
      keys: [
        { key: "bot_heartbeat_interval", label: "Heartbeat Interval",      type: "number", suffix: "sec", placeholder: "30" },
        { key: "bot_rejoin_delay",       label: "Rejoin Delay",            type: "number", suffix: "sec", placeholder: "10" },
        { key: "bot_max_retries",        label: "Max Reconnect Retries",   type: "number", placeholder: "5" },
        { key: "bot_auto_start",         label: "Auto-start on boot",      type: "toggle" },
        { key: "bots_enabled",           label: "Enabled Bots",            type: "text",   placeholder: "all  or  main,dj,banker" },
      ],
    },
    {
      title: "Bot Welcome Settings",
      description: "Per-bot configurable whispers sent when a player joins",
      api: "/api/settings/:key",
      apiOpts: { source: "room_settings" },
      keys: [
        { key: "botwelcomes_enabled",   label: "Bot Welcomes Enabled",  type: "toggle" },
        { key: "botwelcome_dj",         label: "DJ Bot Welcome",        type: "textarea", placeholder: "Welcome to ChillTopia! DJ_DUDU here 🎵" },
        { key: "botwelcome_host",       label: "Host Bot Welcome",      type: "textarea", placeholder: "Hey! I'm ChillTopiaMC 🎉" },
        { key: "botwelcome_security",   label: "Security Bot Welcome",  type: "textarea" },
        { key: "botwelcome_miner",      label: "Miner Bot Welcome",     type: "textarea" },
        { key: "botwelcome_banker",     label: "Banker Bot Welcome",    type: "textarea" },
      ],
    },
  ],
  emotes: [
    {
      title: "Player Emote Settings",
      description: "Player-triggered emotes and cooldowns",
      api: "/api/settings/:key",
      apiOpts: { source: "room_settings" },
      keys: [
        { key: "public_emotes_enabled", label: "Player Emotes Enabled", type: "toggle" },
        { key: "emote_cooldown_sec",    label: "Emote Cooldown",        type: "number", suffix: "sec" },
        { key: "emote_allowed_roles",   label: "Allowed Roles",         type: "text",   placeholder: "all  or  vip,admin" },
      ],
    },
    {
      title: "Bot Emote Settings",
      description: "Bot reaction emotes for big finds, greetings, and dancefloor",
      api: "/api/settings/:key",
      apiOpts: { source: "room_settings" },
      keys: [
        { key: "bot_react_enabled",    label: "Bot Reactions Enabled",    type: "toggle" },
        { key: "bot_react_threshold",  label: "Big Find React Threshold", type: "number", suffix: "lbs" },
        { key: "bot_greet_emote",      label: "Greeting Emote",           type: "text",   placeholder: "wave" },
      ],
    },
    {
      title: "Dancefloor Settings",
      description: "Automated dancefloor and sync configuration",
      api: "/api/settings/:key",
      apiOpts: { source: "room_settings" },
      keys: [
        { key: "dancefloor_enabled",       label: "Dancefloor Enabled",   type: "toggle" },
        { key: "dancefloor_sync_interval", label: "Sync Interval",        type: "number", suffix: "sec" },
        { key: "dancefloor_auto_emote",    label: "Auto Emote on Floor",  type: "toggle" },
      ],
    },
  ],
};

/* ── Settings Helpers ────────────────────────────────── */
function settingsMapFrom(rawArr) {
  const m = {};
  if (!rawArr) return m;
  for (const s of rawArr) if (s?.key !== undefined) m[String(s.key)] = String(s.value ?? "");
  return m;
}

function renderSettingsGroup(group, valuesMap, idx) {
  const formId = `sg_${idx}_${group.title.replace(/\W+/g, "_").toLowerCase()}`;
  const apiPath = group.api || "";
  return `<div class="card settings-group">
    <div class="card-header" style="margin-bottom:10px">
      <div>
        <h2>${esc(group.title)}</h2>
        ${group.description ? `<div class="muted text-sm" style="margin-top:2px">${esc(group.description)}</div>` : ""}
      </div>
    </div>
    <form id="${esc(formId)}" data-settings-group="${esc(formId)}"
          data-sg-api="${esc(apiPath)}"
          data-sg-opts='${JSON.stringify(group.apiOpts || {})}'>
      <div class="settings-fields">
        ${group.keys.map((f) => renderSettingsField(f, valuesMap[f.key])).join("")}
      </div>
      <div style="margin-top:14px">
        <button class="btn primary sm" type="submit">💾 Save ${esc(group.title)}</button>
      </div>
    </form>
  </div>`;
}

function renderSettingsField(field, currentVal) {
  const val = currentVal ?? field.default ?? "";
  const id = `sf_${field.key.replace(/\W+/g, "_")}`;

  if (field.type === "toggle") {
    const checked = val === "true" || val === "1" || val === true;
    return `<div class="settings-field settings-field-toggle">
      <div>
        <div class="field-label">${esc(field.label)}</div>
        ${field.hint ? `<div class="muted text-sm">${esc(field.hint)}</div>` : ""}
      </div>
      <label class="switch" style="margin:0">
        <input type="checkbox" id="${esc(id)}" name="${esc(field.key)}" data-sf-toggle="1" ${checked ? "checked" : ""} />
        <span></span>
      </label>
    </div>`;
  }

  if (field.type === "textarea") {
    return `<div class="settings-field">
      <label class="field-label" for="${esc(id)}">${esc(field.label)}</label>
      ${field.hint ? `<div class="muted text-sm" style="margin-bottom:4px">${esc(field.hint)}</div>` : ""}
      <textarea id="${esc(id)}" name="${esc(field.key)}" rows="3"
        placeholder="${esc(field.placeholder || "")}"
        style="resize:vertical">${esc(val)}</textarea>
    </div>`;
  }

  if (field.type === "select") {
    return `<div class="settings-field">
      <label class="field-label" for="${esc(id)}">${esc(field.label)}</label>
      <select id="${esc(id)}" name="${esc(field.key)}">
        ${(field.options || []).map(([v, l]) => `<option value="${esc(v)}" ${val === v ? "selected" : ""}>${esc(l)}</option>`).join("")}
      </select>
    </div>`;
  }

  // number or text
  return `<div class="settings-field">
    <div class="settings-field-row">
      <label class="field-label" for="${esc(id)}">${esc(field.label)}</label>
      ${field.suffix ? `<span class="settings-suffix">${esc(field.suffix)}</span>` : ""}
    </div>
    ${field.hint ? `<div class="muted text-sm" style="margin-bottom:4px">${esc(field.hint)}</div>` : ""}
    <input type="${field.type === "number" ? "number" : "text"}"
      id="${esc(id)}" name="${esc(field.key)}"
      value="${esc(val)}" placeholder="${esc(field.placeholder || "")}" />
  </div>`;
}

function renderAdvancedCollapse(rawSettings) {
  const raw = rawSettings || [];
  return `<details class="advanced-collapse">
    <summary class="advanced-summary">
      <span class="pill warn">⚙️</span> Advanced — Raw Settings Editor
      <span class="muted text-sm">(${raw.length} keys)</span>
    </summary>
    <div class="advanced-content">
      <form class="rawSettingGroupForm toolbar" style="flex-wrap:wrap;margin-bottom:14px">
        <input name="key" placeholder="setting_key" required style="flex:2;min-width:120px" />
        <input name="value" placeholder="value" required style="flex:3;min-width:120px" />
        <button class="btn sm" type="submit">Save</button>
      </form>
      ${raw.length ? table(raw,
          [{ key: "key", label: "Key" }, { key: "value", label: "Value" }, { key: "source", label: "Source" }],
          (r) => `<button class="btn sm" data-raw-edit-key="${esc(r.key)}" data-raw-edit-val="${esc(r.value)}" data-raw-edit-src="${esc(r.source||"room_settings")}">Edit</button>`)
        : `<div class="notice">No raw settings found.</div>`}
    </div>
  </details>`;
}

function renderSchemaGroups(schemaKey, valuesMap) {
  const groups = SETTINGS_SCHEMA[schemaKey] || [];
  return groups.map((g, i) => renderSettingsGroup(g, valuesMap, `${schemaKey}${i}`)).join("");
}

/* ── API Map ─────────────────────────────────────────── */
function pageApi(page, tab) {
  const key = tab ? `${page}/${tab}` : page;
  return ({
    "Command Center":                     "/api/overview",
    "Bots/Bot Status":                    "/api/bot-control",
    "Bots/Bot Config":                    "/api/bot-config",
    "Bots/Bot Settings":                  "/api/settings",
    "Bots/Bot Spawns":                    "/api/bot-spawns",
    "Bots/Advanced Debug":                "/api/bot-audit",
    "Players/Titles & Badges":            "/api/titles",
    "Room & Content/Room Settings":       "/api/room-control",
    "Room & Content/Radio":               "/api/radio",
    "Room & Content/Events":              "/api/events",
    "Room & Content/Announcements":       "/api/room-control",
    "Room & Content/Welcome":             "/api/room-control",
    "Room & Content/Emotes":              "/api/room-control",
    "Economy & Rewards/Coins & Tickets":  "/api/economy/overview",
    "Economy & Rewards/Casino":           "/api/casino",
    "Economy & Rewards/Games":            "/api/games",
    "Economy & Rewards/Titles":           "/api/titles",
    "Staff":                              "/api/staff",
    "System/Health":                      "/api/healthz",
    "System/Logs":                        null,
    "System/Settings Audit":              "/api/settings-audit",
    "System/Emergency":                   "/api/settings",
    "System/Database":                    "/api/db/inspect",
    "Staff Home":                         "/api/overview",
    "Radio Queue":                        "/api/radio",
    "Events":                             "/api/events",
    "Room Tools":                         "/api/room-control",
    "Logs":                               null,
  })[key] ?? null;
}

/* ── State ───────────────────────────────────────────── */
const state = {
  user: null,
  csrf: "",
  adminPage: (() => {
    const h = location.hash ? decodeURIComponent(location.hash.slice(1)) : "";
    const all = [...OWNER_NAV, ...STAFF_NAV].map((n) => n.id);
    return all.includes(h) ? h : "Command Center";
  })(),
  adminTab: {},
  publicPage: "home",
  data: null,
  error: "",
  notice: "",
  logs: { action_type: "", user: "", module: "", offset: 0 },
  settingsAudit: { status: "all", module: "", page: "" },
  modal: null,
  sidebarOpen: false,
  showLoginOverlay: false,
  playerResult: null,
};

const app = document.getElementById("app");

/* ── Helpers ─────────────────────────────────────────── */
function esc(v) {
  return String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}
function can(p) { return state.user?.role === "owner" || !!state.user?.permissions?.[p]; }

async function api(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (!["GET", "HEAD"].includes(String(options.method || "GET").toUpperCase()) && state.csrf)
    headers["X-CSRF-Token"] = state.csrf;
  const res = await fetch(path, { credentials: "include", ...options, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
async function publicApi(path) {
  const res = await fetch(path, { credentials: "omit" });
  return res.json().catch(() => ({}));
}

function pill(value, good = ["online","enabled","ready","queued","submitted","set","ok","found","playing"]) {
  const text = String(value ?? "");
  const lo = text.toLowerCase();
  let cls = "def";
  if (good.includes(lo) || value === 1 || value === true) cls = "ok";
  else if (lo === "empty" || lo === "off" || lo.includes("disab") || value === 0 || value === false) cls = "bad";
  else if (["warn","pending"].includes(lo)) cls = "warn";
  return `<span class="pill ${cls}">${esc(text || "unknown")}</span>`;
}

function table(rows, columns, actions) {
  if (!rows || rows.length === 0) return `<div class="empty-state"><div class="empty-state-icon">📭</div><span>No records found.</span></div>`;
  const cols = columns || Object.keys(rows[0]).slice(0, 8).map((k) => ({ key: k, label: k }));
  return `<div class="table-wrap"><table>
    <thead><tr>${cols.map((c) => `<th>${esc(c.label)}</th>`).join("")}${actions ? "<th></th>" : ""}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${cols.map((c) => `<td>${c.render ? c.render(r) : esc(r[c.key])}</td>`).join("")}${actions ? `<td class="inline-actions">${actions(r)}</td>` : ""}</tr>`).join("")}</tbody>
  </table></div>`;
}

function metricCard(label, value, hint = "", accent = "", icon = "") {
  return `<div class="metric-card ${accent}">
    ${icon ? `<div class="metric-icon">${icon}</div>` : ""}
    <div class="metric-value">${esc(String(value ?? "—"))}</div>
    <div class="metric-label">${esc(label)}</div>
    ${hint ? `<div class="metric-hint">${esc(hint)}</div>` : ""}
  </div>`;
}

function auditStat(label, value, hint = "") {
  return `<div class="audit-stat">
    <div class="audit-stat-value">${esc(String(value ?? "—"))}</div>
    <div class="audit-stat-label">${esc(label)}</div>
    ${hint ? `<div class="metric-hint">${esc(hint)}</div>` : ""}
  </div>`;
}

function futureControls(rows, title = "Advanced / Future Controls") {
  const data = (rows || []).map((row) => ({
    endpoint: row.endpoint || row[0] || "",
    purpose: row.purpose || row[1] || "",
    status: row.status || row[2] || "Not wired",
  }));
  return `<details class="advanced-collapse future-controls">
    <summary class="advanced-summary">
      <span class="pill def">Future</span> ${esc(title)}
      <span class="muted text-sm">(${data.length} item${data.length === 1 ? "" : "s"})</span>
    </summary>
    <div class="advanced-content">
      ${data.length ? table(data, [
        { key: "endpoint", label: "Endpoint" },
        { key: "purpose", label: "Purpose" },
        { key: "status", label: "Status", render: (r) => `<span class="pill warn">${esc(r.status)}</span>` },
      ]) : `<div class="notice">No future controls listed.</div>`}
    </div>
  </details>`;
}

function queueHelp() {
  return `<p class="muted text-sm control-note">Queued commands require bot-side command queue consumer.</p>`;
}

function tabNav(page) {
  const tabs = PAGE_TABS[page];
  if (!tabs) return "";
  const cur = state.adminTab[page] || tabs[0];
  return `<div class="tab-nav">
    ${tabs.map((t) => `<button class="tab-btn ${t === cur ? "active" : ""}" data-page-tab="${esc(t)}">${esc(t)}</button>`).join("")}
  </div>`;
}
function activeTab(page) {
  const tabs = PAGE_TABS[page];
  return tabs ? (state.adminTab[page] || tabs[0]) : null;
}

/* ── Loading ─────────────────────────────────────────── */
async function loadPublic() {
  const apiMap = {
    home: "/api/public/home", radio: "/api/public/radio",
    events: "/api/public/events", rankings: "/api/public/rankings",
    howtoplay: null, roominfo: null,
  };
  try {
    const url = apiMap[state.publicPage];
    state.data = url ? await publicApi(url) : {};
    state.error = "";
  } catch (err) { state.data = {}; state.error = err.message; }
  render();
}

async function loadAdmin() {
  const role = state.user?.role;
  const nav = role === "owner" ? OWNER_NAV : STAFF_NAV;
  const navIds = nav.map((n) => n.id);
  if (!navIds.includes(state.adminPage)) state.adminPage = navIds[0];

  const page = state.adminPage;
  const tabs = PAGE_TABS[page];
  if (tabs && (!state.adminTab[page] || !tabs.includes(state.adminTab[page])))
    state.adminTab[page] = tabs[0];

  const tab = state.adminTab[page] || null;
  const url = page === "Logs" || (page === "System" && tab === "Logs")
    ? logsUrl() : pageApi(page, tab);
  location.hash = encodeURIComponent(page);

  if (!url) { state.data = {}; render(); return; }
  try { state.data = await api(url); state.error = ""; }
  catch (err) { state.data = null; state.error = err.message; }
  render();
}

async function switchTab(page, tab) {
  state.adminTab[page] = tab;
  const url = (page === "System" && tab === "Logs") || page === "Logs"
    ? logsUrl() : pageApi(page, tab);
  if (!url) { state.data = {}; render(); return; }
  try { state.data = await api(url); state.error = ""; }
  catch (err) { state.data = null; state.error = err.message; }
  render();
}

function logsUrl() {
  const p = new URLSearchParams();
  for (const k of ["action_type", "user", "module"]) if (state.logs[k]) p.set(k, state.logs[k]);
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
    if (state.user.role === "owner" && !OWNER_NAV.map((n) => n.id).includes(state.adminPage))
      state.adminPage = "Command Center";
    else if (state.user.role !== "owner" && !STAFF_NAV.map((n) => n.id).includes(state.adminPage))
      state.adminPage = "Staff Home";
    await loadAdmin();
  } catch { await loadPublic(); }
  setInterval(() => {
    if (state.user) {
      const auto = state.user.role === "owner" ? ["Command Center"] : ["Staff Home", "Radio Queue"];
      if (auto.includes(state.adminPage)) loadAdmin();
    } else if (["home", "radio"].includes(state.publicPage)) loadPublic();
  }, 12000);
}

async function login(event) {
  event.preventDefault();
  try {
    const result = await api("/api/auth/login", {
      method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget))),
    });
    state.user = result.user;
    state.csrf = result.csrf_token || "";
    state.notice = "Signed in successfully.";
    state.showLoginOverlay = false;
    state.adminPage = state.user.role === "owner" ? "Command Center" : "Staff Home";
    await loadAdmin();
  } catch (err) { state.error = err.message; render(); }
}

async function logout() {
  await api("/api/auth/logout", { method: "POST" }).catch(() => {});
  state.user = null; state.csrf = ""; state.data = null; state.playerResult = null;
  state.adminPage = "Command Center";
  await loadPublic();
}

async function action(label, fn) {
  try {
    await fn();
    state.notice = label; state.error = "";
    if (state.user) await loadAdmin(); else await loadPublic();
  } catch (err) { state.error = err.message; state.notice = ""; render(); }
}

function confirmAction(title, body, fn) { state.modal = { title, body, fn }; render(); }
async function runModalAction() { const fn = state.modal?.fn; state.modal = null; if (fn) await fn(); }
function render() { if (state.user) renderAdmin(); else renderPublicPortal(); }

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
        <span>ChillTopia &copy; 2025</span><span>·</span>
        <span>Powered by DJ DUDU Radio</span><span>·</span>
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
      <div><strong>ChillTopia</strong><span>DJ DUDU Radio</span></div>
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
    case "howtoplay": return renderPublicHowToPlay();
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
          ${np.artist ? `<span class="pub-now-artist">${esc(np.artist)}</span>` : ""}`
          : `<span class="pub-now-label">Auto DJ is in the house 🎧</span>`}
        </div>
        <div class="pub-quick-links">
          <button class="btn primary" data-pub-page="radio">📻 Radio</button>
          <button class="btn cyan" data-pub-page="rankings">🏆 Rankings</button>
          <button class="btn ghost" data-pub-page="howtoplay">📖 How to Play</button>
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
    <div class="pub-section-title"><h2>📻 DJ DUDU Radio</h2>
      <p>Live music in ChillTopia — request your favourite songs in the room!</p></div>
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
          <p>Type <strong>!request [song name]</strong> in the Highrise room to add your song to the queue.</p>
        </div>
      </div>
      <div style="display:grid;gap:16px;min-width:0">
        <div class="card">
          <h3>🎶 Up Next (${queue.length})</h3>
          ${queue.length ? queue.slice(0, 10).map((s, i) => `
            <div class="pub-queue-item">
              <span class="pub-queue-pos">${i + 1}</span>
              <div class="pub-queue-info">
                <div class="pub-queue-title">${esc(s.title || "—")}</div>
                ${s.artist ? `<div class="pub-queue-artist">${esc(s.artist)}</div>` : ""}
              </div>
              <span class="muted text-sm">${esc(s.username || "")}</span>
            </div>`).join("") : `<div class="notice">Queue is empty — be the first to request!</div>`}
        </div>
        ${recent.length ? `<div class="card"><h3>🕐 Recently Played</h3>
          ${recent.slice(0, 5).map((s) => `<div class="pub-queue-item">
            <span class="pub-queue-pos" style="opacity:0.4">✓</span>
            <div class="pub-queue-info">
              <div class="pub-queue-title">${esc(s.title || "—")}</div>
              ${s.artist ? `<div class="pub-queue-artist">${esc(s.artist)}</div>` : ""}
            </div>
          </div>`).join("")}</div>` : ""}
      </div>
    </div>
  `;
}

function renderPublicHowToPlay() {
  const sections = [
    { icon: "🎵", title: "Request Songs", content: `Type <strong>!request [song name or artist]</strong> in the room chat. Example: <code>!request lofi hip hop</code>` },
    { icon: "🃏", title: "Casino Games", content: `<strong>!bj [amount]</strong> for Blackjack. <strong>!poker</strong> for Poker. Use <strong>!hit</strong>, <strong>!stand</strong>, <strong>!double</strong> to play.` },
    { icon: "⛏️", title: "Mining", content: `Type <strong>!mine</strong> to start mining ores. 7+ rarities including Prismatic and Exotic. Rare finds earn bonus coins!` },
    { icon: "🎣", title: "Fishing", content: `Type <strong>!fish</strong> to start fishing. Rare catches earn announcements and bonus rewards!` },
    { icon: "💰", title: "Coins & Daily Rewards", content: `Earn coins via <strong>!daily</strong>, casino wins, mining, fishing, quests, events, and room time. Check with <strong>!balance</strong>.` },
    { icon: "💃", title: "Emotes & Dancefloor", content: `Jump on the dancefloor and the bot may react! Use <strong>!emote [name]</strong> for bot emotes.` },
    { icon: "⭐", title: "VIP", content: `VIP gives priority queue slots, exclusive badge, and bonus daily coins. Ask staff about VIP access.` },
  ];
  return `
    <div class="pub-section-title"><h2>📖 How to Play</h2>
      <p>Everything you need to know to enjoy ChillTopia</p></div>
    <div class="pub-tutorial-grid">
      ${sections.map((s) => `<div class="card pub-tutorial-card">
        <div class="pub-tutorial-icon">${s.icon}</div>
        <h3>${s.title}</h3><p>${s.content}</p>
      </div>`).join("")}
    </div>
  `;
}

function renderPublicEvents(d) {
  const scheduled = d.scheduled || [];
  return `
    <div class="pub-section-title"><h2>🎉 Events</h2>
      <p>Current and upcoming ChillTopia events</p></div>
    <div class="pub-grid2">
      <div class="card">
        <h3>🏆 Event Points & Rewards</h3>
        <p class="muted" style="margin-bottom:12px">Earn points participating in room events.</p>
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
      <p class="muted" style="margin-top:6px">Contact a staff member in the room to arrange a special event!</p>
    </div>
  `;
}

function renderPublicRankings(d) {
  function leaderboard(title, icon, rows, cols) {
    return `<div class="card"><h3>${icon} ${title}</h3>
      ${rows && rows.length ? `<div class="pub-leaderboard">
        ${rows.map((r, i) => `<div class="pub-lb-row">
          <span class="pub-lb-rank ${i < 3 ? "top" + i : ""}">${["🥇","🥈","🥉"][i] || (i + 1)}</span>
          <span class="pub-lb-name">${esc(r[cols[0]] || "—")}</span>
          ${r[cols[1]] !== undefined ? `<span class="pub-lb-val">${esc(String(r[cols[1]]))}</span>` : ""}
        </div>`).join("")}
      </div>` : `<div class="notice">No data yet — be the first on the leaderboard!</div>`}
    </div>`;
  }
  return `
    <div class="pub-section-title"><h2>🏆 Rankings</h2>
      <p>Top players across all ChillTopia activities</p></div>
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
    <div class="pub-section-title"><h2>ℹ️ Room Info</h2>
      <p>Everything you need to know about ChillTopia</p></div>
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
        <h3>🤖 Key Commands</h3>
        <div class="pub-cmd-list">
          ${[["!balance","Check your coins"],["!daily","Claim daily reward"],["!bj [bet]","Play Blackjack"],
             ["!poker","Join Poker"],["!mine","Start mining"],["!fish","Start fishing"],
             ["!request [song]","Request a song"],["!queue","View song queue"],
             ["!profile","View your profile"],["!leaderboard","Top players"]
          ].map(([cmd, desc]) => `<div class="pub-cmd-item"><code>${esc(cmd)}</code><span>${esc(desc)}</span></div>`).join("")}
        </div>
      </div>
      <div class="card">
        <h3>👥 Staff Roles</h3>
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
        <div class="login-logo-text"><strong>ChillTopia</strong><span>Owner / Staff Login</span></div>
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
  document.querySelectorAll("[data-pub-page]").forEach((btn) => btn.addEventListener("click", () => {
    state.publicPage = btn.dataset.pubPage; state.error = ""; loadPublic();
  }));
  document.getElementById("pubLoginBtn")?.addEventListener("click", () => {
    state.showLoginOverlay = true; state.error = ""; render();
  });
  document.getElementById("loginCloseBtn")?.addEventListener("click", () => {
    state.showLoginOverlay = false; state.error = ""; render();
  });
  document.getElementById("loginOverlay")?.addEventListener("click", (e) => {
    if (e.target.id === "loginOverlay") { state.showLoginOverlay = false; state.error = ""; render(); }
  });
  document.getElementById("loginForm")?.addEventListener("submit", login);
}

/* ═══════════════════════════════════════════════════════
   ADMIN SHELL (shared — roles branch here)
══════════════════════════════════════════════════════ */
function renderAdmin() {
  const role = state.user?.role;
  const nav = role === "owner" ? OWNER_NAV : STAFF_NAV;
  const initials = (state.user?.username || "U").slice(0, 2).toUpperCase();
  const roleLabel = role === "owner" ? "Owner Control Center" : "Staff Console";

  let lastGroup = null;
  const navHtml = nav.map((item) => {
    let hdr = "";
    if (item.group !== lastGroup) {
      hdr = `<div class="nav-label" style="margin-top:${lastGroup ? "14px" : "0"}">${item.group}</div>`;
      lastGroup = item.group;
    }
    return `${hdr}<button class="${item.id === state.adminPage ? "active" : ""}" data-admin-page="${esc(item.id)}">
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
          <div><strong>ChillTopia</strong><span>${roleLabel}</span></div>
        </div>
        <nav class="nav">${navHtml}</nav>
        <div class="sidebar-footer">
          <button class="btn ghost pub-portal-btn" id="pubPortalBtn">← Public Portal</button>
          <div class="sidebar-user">
            <div class="sidebar-user-avatar">${esc(initials)}</div>
            <div>
              <div class="sidebar-user-name">${esc(state.user.username)}</div>
              <div class="sidebar-user-role">${esc(role === "owner" ? "Owner" : "Staff")}</div>
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

  document.querySelectorAll("[data-admin-page]").forEach((btn) => btn.addEventListener("click", () => {
    state.adminPage = btn.dataset.adminPage; state.notice = ""; state.error = "";
    state.sidebarOpen = false; loadAdmin();
  }));
  document.querySelectorAll("[data-page-tab]").forEach((btn) => btn.addEventListener("click", () => {
    state.notice = ""; state.error = ""; switchTab(state.adminPage, btn.dataset.pageTab);
  }));
  document.getElementById("refreshBtn").addEventListener("click", loadAdmin);
  document.getElementById("logoutBtn").addEventListener("click", logout);
  document.getElementById("pubPortalBtn")?.addEventListener("click", () => {
    state.user = null; state.csrf = ""; state.data = null; loadPublic();
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
  const d = state.data;
  const page = state.adminPage;
  const role = state.user?.role;
  const nullDataOk = ["Players", "Bots", "Room & Content", "Economy & Rewards",
    "System", "Staff Home", "Players", "Events", "Room Tools", "Logs"];
  if (!d && state.error && !nullDataOk.includes(page)) return `<div class="card"><div class="empty-state"><div class="empty-state-icon">⚠️</div><strong style="color:var(--red);margin-bottom:4px">Failed to load</strong><span>${esc(state.error)}</span></div></div>`;
  if (!d && !nullDataOk.includes(page)) return `<div class="card"><div class="loading-state"><div class="loading-spinner"></div><span class="muted text-sm">Loading…</span></div></div>`;
  return role === "owner" ? renderOwnerPage(page) : renderStaffPage(page);
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

/* ═══════════════════════════════════════════════════════
   OWNER CONTROL CENTER
══════════════════════════════════════════════════════ */
function renderOwnerPage(page) {
  switch (page) {
    case "Command Center":    return renderCommandCenter();
    case "Bots":              return renderBotsPage(activeTab("Bots"));
    case "Players":           return renderOwnerPlayersPage(activeTab("Players"));
    case "Room & Content":    return renderRoomContent(activeTab("Room & Content"));
    case "Economy & Rewards": return renderEconomyRewards(activeTab("Economy & Rewards"));
    case "Staff":             return renderStaffPage_shared();
    case "System":            return renderSystemPage(activeTab("System"));
    default:                  return renderCommandCenter();
  }
}

/* ── Command Center ──────────────────────────────────── */
function renderCommandCenter() {
  const d = state.data || {};
  const m = d.metrics || {};
  const r = d.radio || {};
  const bots = d.bots || [];
  const onlineBots = bots.filter((b) => String(b.status || "").toLowerCase() === "online").length;
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr))">
      ${metricCard("Online Bots", `${m.online_bots ?? onlineBots ?? 0}/${m.total_bots ?? bots.length ?? 0}`, "bot instances", "accent-green", "🤖")}
      ${metricCard("Room Users", m.current_room_users ?? "—", "live occupancy", "accent-cyan", "👥")}
      ${metricCard("Radio Queue", m.queue_count ?? 0, "songs pending", "", "🎵")}
      ${metricCard("Active Games", m.active_games ?? 0, "casino / games", "", "🎲")}
      ${metricCard("Staff Online", m.staff_online ?? "—", "dashboard staff", "", "👑")}
    </div>
    <div class="card" style="padding:16px 20px">
      <div class="card-header"><h2>🎵 Now Playing</h2></div>
      <div style="font-size:18px;font-weight:700;margin-bottom:4px">${esc(r.now_playing?.title || "Auto DJ")}</div>
      ${r.now_playing?.artist ? `<div class="muted text-sm">${esc(r.now_playing.artist)}</div>` : ""}
      ${r.now_playing?.username ? `<div class="muted text-sm">Requested by ${esc(r.now_playing.username)}</div>` : ""}
    </div>
    <div class="card">
      <div class="card-header"><h2>⚡ Quick Actions</h2></div>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-top:4px">
        <button class="btn primary" id="qaRestartBots">🔄 Restart Bots</button>
        <button class="btn cyan" id="qaToggleRequests">${r.queue_open ? "🚫 Close Requests" : "✅ Open Requests"}</button>
        <button class="btn" data-admin-page="Room & Content">📻 Radio Controls</button>
      </div>
    </div>
    <div class="card danger-zone">
      <div class="card-header" style="margin-bottom:10px">
        <h2>🚨 Danger Zone</h2>
        <span class="muted text-sm">Writes DB flags — bots respond on next heartbeat</span>
      </div>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px">
        <button class="btn danger" id="qaDisableCasino">🎲 Disable Casino</button>
        <button class="btn danger" id="qaDisableGames">🎮 Disable Games</button>
        <button class="btn danger" id="qaClearQueue">🗑 Clear Queue</button>
      </div>
    </div>
    <div class="grid">
      <div class="card">
        <h2>📊 Module Status</h2>
        ${moduleFlagTable(d.module_flags || [])}
      </div>
      <div class="card">
        <h2>⚠️ Recent Errors</h2>
        ${table(d.command_errors || [], [
          { key: "command", label: "Command" },
          { key: "error", label: "Error" },
          { key: "username", label: "User" },
          { key: "created_at", label: "Time" },
        ])}
      </div>
    </div>
  `;
}

/* ── Bots Page ───────────────────────────────────────── */
function renderBotsPage(tab) {
  return `
    ${tabNav("Bots")}
    ${tab === "Bot Status"   ? renderBotStatus() : ""}
    ${tab === "Bot Config"   ? renderBotConfig() : ""}
    ${tab === "Bot Settings" ? renderBotSettingsTab() : ""}
    ${tab === "Bot Spawns"   ? renderBotSpawns() : ""}
    ${tab === "Advanced Debug" ? renderBotAdvanced() : ""}
  `;
}

function renderBotSettingsTab() {
  const d = state.data || {};
  const rawAll = [...(d.room_settings || []), ...(d.bot_settings || [])];
  const valMap = settingsMapFrom(rawAll);
  return `
    ${renderSchemaGroups("bots", valMap)}
    ${renderAdvancedCollapse(rawAll.filter((s) =>
      s.key?.startsWith("bot") || s.key?.startsWith("botwelcome") || s.key?.startsWith("bots_")))}
  `;
}

function renderBotStatus() {
  const bots = state.data?.bots || [];
  const rawCount = state.data?.raw_count ?? bots.length;
  const rawDebugRows = state.data?.raw_duplicate_rows || [];
  const auditSummary = state.data?.audit_summary || {};
  const queue = state.data?.command_queue || {};

  if (!bots.length) return `<div class="card">
    <h2>🤖 Bot Status</h2>
    <div class="notice warn">No canonical bot data returned. Bots write heartbeat rows to <code>bot_instances</code> on startup.</div>
  </div>`;

  const mergedCount = auditSummary.merged_count ?? rawDebugRows.length;
  const dupeNote = rawCount !== bots.length || mergedCount > 0
    ? `<div class="notice" style="margin-bottom:12px">${rawCount} raw DB rows audited as ${bots.length} canonical bot accounts. ${mergedCount} duplicate or alias row${mergedCount !== 1 ? "s" : ""} merged; raw rows remain untouched in Advanced.</div>`
    : `<div class="notice" style="margin-bottom:12px">${bots.length} canonical bot accounts are displayed. Raw DB rows remain read-only in Advanced.</div>`;

  return `
    ${dupeNote}
    <div class="grid">
      ${bots.map((b) => {
        const rawRowCount = b.raw_row_count ?? b.raw_duplicate_count ?? 0;
        return `<div class="card bot-card">
          <div class="card-header" style="margin-bottom:8px">
            <div>
              <h2>${esc(b.card_title || b.display_name || b.bot_mode || "Bot")}</h2>
              <div class="muted text-sm" style="margin-top:2px">
                @${esc(b.bot_username || b.display_name || "—")}
                <span style="margin:0 4px">·</span>
                <code style="font-size:11px">${esc(b.bot_mode || "—")}</code>
              </div>
            </div>
            <div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px">
              ${pill(b.status || "unknown")}
              ${rawRowCount > 1 ? `<span class="pill warn" style="font-size:10px">${rawRowCount} raw rows audited</span>` : ""}
            </div>
          </div>
          ${b.modules && b.modules.length ? `<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px">
            ${b.modules.map((m) => `<span class="pill def" style="font-size:11px">${esc(m)}</span>`).join("")}
          </div>` : ""}
          <div class="muted text-sm" style="display:grid;gap:3px;margin-bottom:12px">
            <span>Room: ${esc(b.current_room_id || "—")}</span>
            <span>Heartbeat: ${esc(b.last_heartbeat_at || "—")}</span>
            <span>Raw source: ${esc(b.source_bot_mode || "—")} / ${esc(b.source_bot_username || "—")}</span>
            ${b.enabled === 0 ? `<span style="color:var(--warn)">⚠ Bot disabled</span>` : ""}
            ${b.last_error ? `<span style="color:var(--red)">⚠ ${esc(String(b.last_error).slice(0, 120))}</span>` : ""}
          </div>
          <div class="inline-actions">
            <button class="btn sm" data-bot-command="return_home" data-target-bot="${esc(b.bot_username)}">🏠 Queue Home</button>
            <button class="btn sm" data-bot-command="stop_emote" data-target-bot="${esc(b.bot_username)}">⏹ Queue Stop</button>
            <button class="btn danger sm" data-bot-command="restart_requested" data-target-bot="${esc(b.bot_username)}">🔄 Queue Restart</button>
          </div>
        </div>`;
      }).join("")}
    </div>
    ${queueHelp()}
    <div class="card">
      <h2>Queued Bot Commands</h2>
      ${renderQueuedBotCommands(queue)}
    </div>
  `;
}

function renderBotConfig() {
  const d = state.data || {};
  const tokens = d.tokens || {};
  return `
    <div class="grid">
      <div class="card">
        <h2>⚙️ Connection Settings</h2>
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
            <button class="btn" type="button" id="saveAndRestartBtn">💾 Save + Restart</button>
            <button class="btn danger" type="button" id="restartBotsBtn">🔄 Restart Bots</button>
          </div>
          <p class="muted text-sm" style="margin-top:12px">ℹ️ Restart writes a flag to the DB. Each bot picks it up on its next heartbeat (~15–30 s) — the process is not killed immediately.</p>
        </form>
      </div>
      <div class="card">
        <h2>🔑 Token Status</h2>
        <p class="muted text-sm" style="margin-bottom:14px">⚠️ Tokens are stored in environment secrets — never exposed here. SET = token exists.</p>
        ${Object.entries(tokens).map(([key, status]) => `
          <div class="token-row">
            <span class="token-name">${esc(key)}</span>
            ${pill(status)}
          </div>`).join("")}
        ${futureControls([
          { endpoint: "PUT /api/bot-config/token/:key", purpose: "Token rotation; use environment secrets instead", status: "Hidden" },
        ])}
      </div>
    </div>
  `;
}

function renderBotSpawns() {
  const d = state.data || {};
  const spawns = d.spawns || [];
  return `<div class="card">
    <div class="card-header" style="margin-bottom:10px">
      <h2>🚀 Saved Bot Spawns</h2>
      <span class="muted text-sm">Read-only from <code>bot_spawns</code></span>
    </div>
    ${spawns.length ? table(spawns, [
      { key: "bot_username", label: "Bot" },
      { key: "spawn_name", label: "Spawn" },
      { key: "x", label: "X" },
      { key: "y", label: "Y" },
      { key: "z", label: "Z" },
      { key: "facing", label: "Facing" },
      { key: "set_by", label: "Set By" },
      { key: "set_at", label: "Set At" },
    ]) : `<div class="notice">No saved bot spawns found.</div>`}
    ${futureControls([
      { endpoint: "POST /api/bot-spawns", purpose: "Set spawn point", status: "Not wired" },
      { endpoint: "DELETE /api/bot-spawns/:bot/:name", purpose: "Remove spawn point", status: "Not wired" },
    ])}
  </div>`;
}

function renderBotAdvanced() {
  const d = state.data || {};
  const rawRows = d.raw_bot_instances || [];
  const cleanupPreview = d.cleanup_preview || [];
  const canonical = d.canonical_bots || d.bots || [];
  const summary = d.summary || {};
  const queue = d.command_queue || {};
  return `
    <div class="card">
      <div class="card-header" style="margin-bottom:10px">
        <h2>🔧 Advanced Debug</h2>
        <span class="muted text-sm">Owner-only, read-only bot audit</span>
      </div>
      <div class="audit-summary-grid">
        ${auditStat("Canonical Bots", summary.canonical_count ?? canonical.length ?? 0, "expected accounts")}
        ${auditStat("Raw Rows", summary.raw_count ?? rawRows.length ?? 0, "bot_instances")}
        ${auditStat("Merged Rows", summary.merged_count ?? 0, "aliases / duplicates")}
        ${auditStat("Debug Only", summary.debug_only_count ?? 0, "all / main / unknown")}
      </div>
      <div class="notice warn" style="margin-bottom:14px">Cleanup is preview-only. The dashboard does not expose a delete endpoint for <code>bot_instances</code>.</div>
      ${table(canonical, [
        { key: "display_name", label: "Canonical Bot" },
        { key: "bot_mode", label: "Mode" },
        { key: "status", label: "Status", render: (r) => pill(r.status || "unknown") },
        { key: "raw_row_count", label: "Raw Rows" },
        { key: "source_bot_mode", label: "Source Mode" },
        { key: "source_bot_username", label: "Source Username" },
      ])}
    </div>
    <div class="card">
      <h2>Queued Bot Commands</h2>
      ${renderQueuedBotCommands(queue)}
    </div>
    <div class="card">
      <h2>Cleanup Preview</h2>
      ${cleanupPreview.length ? table(cleanupPreview, [
        { key: "action", label: "Preview Action" },
        { key: "canonical_username", label: "Canonical Bot" },
        { key: "canonical_mode", label: "Mode" },
        { key: "bot_mode", label: "Raw Mode" },
        { key: "bot_username", label: "Raw Username" },
        { key: "reason", label: "Reason" },
      ]) : `<div class="notice">No alias, duplicate, or debug-only rows detected.</div>`}
    </div>
    <div class="card">
      <h2>Raw bot_instances Rows</h2>
      ${rawRows.length ? table(rawRows, [
        { key: "bot_id", label: "Bot ID" },
        { key: "bot_mode", label: "Bot Mode" },
        { key: "bot_username", label: "Username" },
        { key: "status", label: "Status", render: (r) => pill(r.status || "unknown") },
        { key: "enabled", label: "Enabled" },
        { key: "last_heartbeat_at", label: "Last Heartbeat" },
        { key: "current_room_id", label: "Room" },
        { key: "last_error", label: "Last Error", render: (r) => r.last_error ? `<span style="color:var(--red);font-size:11px">${esc(String(r.last_error).slice(0, 100))}</span>` : "—" },
      ]) : `<div class="notice">No raw <code>bot_instances</code> rows found.</div>`}
    </div>
  `;
}

function renderQueuedBotCommands(queue) {
  const pending = queue?.pending || [];
  const recent = queue?.recent || [];
  return `
    <div style="display:grid;gap:14px">
      <div>
        <div class="field-label" style="margin-bottom:8px">Pending</div>
        ${pending.length ? table(pending, [
          { key: "id", label: "ID" },
          { key: "target_bot", label: "Target" },
          { key: "action", label: "Action" },
          { key: "status", label: "Status", render: (r) => pill(r.status || "pending") },
          { key: "requester_id", label: "Requester" },
          { key: "created_at", label: "Created" },
        ]) : `<div class="notice">No pending bot commands.</div>`}
      </div>
      <div>
        <div class="field-label" style="margin-bottom:8px">Recent</div>
        ${recent.length ? table(recent, [
          { key: "id", label: "ID" },
          { key: "target_bot", label: "Target" },
          { key: "action", label: "Action" },
          { key: "status", label: "Status", render: (r) => pill(r.status || "unknown") },
          { key: "requester_id", label: "Requester" },
          { key: "created_at", label: "Created" },
          { key: "completed_at", label: "Completed" },
        ]) : `<div class="notice">No recent bot commands.</div>`}
      </div>
    </div>
  `;
}

/* ── Players Page ────────────────────────────────────── */
function renderOwnerPlayersPage(tab) {
  return `
    ${tabNav("Players")}
    ${tab === "Search" ? renderPlayerSearch() : ""}
    ${tab === "Titles & Badges" ? renderTitlesTab() : ""}
    ${tab === "Moderation" ? renderModerationTab() : ""}
  `;
}

function renderPlayerSearch() {
  return `
    <div class="card">
      <h2>🔍 Player Search</h2>
      <form id="playerSearchForm" class="toolbar" style="flex-wrap:wrap">
        <input name="query" placeholder="Username or User ID" required style="flex:1;min-width:200px" />
        <button class="btn primary">Search</button>
      </form>
      <div id="playerSearchResult" style="margin-top:16px"></div>
    </div>
    ${futureControls([
      { endpoint: "POST /api/player/:id/economy", purpose: "Adjust balance, tickets, or XP", status: "Future" },
      { endpoint: "POST /api/player/:id/badges", purpose: "Give or remove badges", status: "Future" },
      { endpoint: "POST /api/player/:id/inventory", purpose: "Edit owned items", status: "Future" },
    ])}
  `;
}

function renderPlayerCard(p) {
  const items = p.owned_items || [];
  return `<div class="card" style="margin-top:0">
    <div class="card-header">
      <h3>🔍 ${esc(p.username || "Unknown")}</h3>
      <span class="muted text-sm">ID: ${esc(p.user_id || "—")}</span>
    </div>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin:12px 0">
      ${metricCard("Balance", p.balance != null ? Number(p.balance).toLocaleString() : "—", "", "accent-green", "💰")}
      ${metricCard("Level", p.level ?? "—", "", "", "⭐")}
      ${metricCard("XP", p.xp != null ? Number(p.xp).toLocaleString() : "—", "", "", "📈")}
      ${metricCard("Games Won", p.total_games_won != null ? Number(p.total_games_won).toLocaleString() : "—", "", "", "🎲")}
      ${metricCard("Coins Earned", p.total_coins_earned != null ? Number(p.total_coins_earned).toLocaleString() : "—", "", "", "🏦")}
      ${metricCard("Tip Earned", p.tip_coins_earned != null ? Number(p.tip_coins_earned).toLocaleString() : "—", "", "", "🎁")}
      ${metricCard("Items", p.owned_items_count ?? p.owned_item_count ?? 0, "owned", "", "🎒")}
    </div>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px">
      <div class="notice">
        <div class="field-label">Equipped Title</div>
        <div>${esc(p.equipped_title || p.equipped_title_id || "—")}</div>
      </div>
      <div class="notice">
        <div class="field-label">Equipped Badge</div>
        <div>${esc(p.equipped_badge || p.equipped_badge_id || "—")}</div>
      </div>
    </div>
    <div style="margin-top:14px">
      <div class="field-label" style="margin-bottom:8px">Inventory Preview</div>
      ${items.length ? table(items, [
        { key: "item_type", label: "Type" },
        { key: "item_id", label: "Item ID" },
      ]) : `<div class="notice">No owned items found.</div>`}
    </div>
    <div class="inline-actions" style="margin-top:14px">
      <button class="btn sm" disabled title="Write endpoint not implemented">Edit Economy</button>
      <button class="btn sm" disabled title="Write endpoint not implemented">Edit Inventory</button>
      <button class="btn sm" disabled title="Write endpoint not implemented">Edit Badge / Title</button>
    </div>
  </div>`;
}

function renderTitlesTab() {
  const d = state.data || {};
  return `
    <div class="card">
      <h2>📚 Title Catalog</h2>
      ${table(d.catalog || [])}
    </div>
    <div class="card">
      <h2>📋 Assigned Titles</h2>
      ${table(d.assigned || [])}
    </div>
    ${futureControls([
      { endpoint: "POST /api/titles/assign", purpose: "Assign titles from the dashboard", status: "Hidden" },
      { endpoint: "POST /api/player/:id/badges", purpose: "Give or remove badges", status: "Future" },
    ])}
  `;
}

function renderModerationTab() {
  return `<div class="card">
    <h2>🛡 Moderation Actions</h2>
    <p class="muted text-sm">Moderation writes are not exposed on the normal dashboard surface yet.</p>
  </div>
  ${futureControls([
    { endpoint: "POST /api/player/:id/warn", purpose: "Issue warning", status: "Future" },
    { endpoint: "POST /api/player/:id/mute", purpose: "Mute player", status: "Future" },
    { endpoint: "POST /api/player/:id/ban", purpose: "Ban player", status: "Future" },
    { endpoint: "POST /api/player/:id/kick", purpose: "Kick from room", status: "Future" },
    { endpoint: "GET /api/player/:id/history", purpose: "Moderation history", status: "Future" },
  ])}`;
}

/* ── Room & Content ──────────────────────────────────── */
function renderRoomContent(tab) {
  return `
    ${tabNav("Room & Content")}
    ${tab === "Room Settings" ? renderRoomSettings() : ""}
    ${tab === "Radio" ? renderRadioTab() : ""}
    ${tab === "Events" ? renderEventsTab() : ""}
    ${tab === "Announcements" ? renderAnnouncementsTab() : ""}
    ${tab === "Welcome" ? renderWelcomeTab() : ""}
    ${tab === "Emotes" ? renderEmotesTab() : ""}
  `;
}

function renderRoomSettings() {
  const d = state.data || {};
  const known = d.known_settings || {};
  const extra = d.extra_settings || [];
  const allRaw = Object.entries(known).map(([key, value]) => ({ key, value, source: "room_settings" })).concat(extra);
  const valMap = settingsMapFrom(allRaw);
  return `
    ${renderSchemaGroups("room", valMap)}
    ${renderAdvancedCollapse(allRaw)}
  `;
}

function renderRadioTab() {
  const d = state.data || {};
  return `
    <div class="grid">
      <div class="card">
        <h2>🎵 Now Playing</h2>
        ${d.now_playing ? `
          <div style="font-size:17px;font-weight:700;margin-bottom:4px">${esc(d.now_playing.title || "—")}</div>
          ${d.now_playing.artist ? `<div class="muted text-sm">${esc(d.now_playing.artist)}</div>` : ""}
          ${d.now_playing.username ? `<div class="muted text-sm">Requested by ${esc(d.now_playing.username)}</div>` : ""}
          <div class="inline-actions" style="margin-top:12px">
            <button class="btn danger sm" data-action="radio-skip">⏭ Skip</button>
          </div>` : `<div class="notice">Nothing currently playing.</div>`}
      </div>
      <div class="card">
        <h2>🎛 Request Controls</h2>
        <label class="switch" style="margin-bottom:16px">
          <input type="checkbox" id="requestsEnabled" ${d.queue_open ? "checked" : ""} />
          <span>Requests enabled</span>
        </label>
        <hr class="divider" />
        <button class="btn danger" data-action="radio-clear">🗑 Clear Queue</button>
      </div>
    </div>
    <div class="card">
      <h2>📋 Song Queue (${(d.queue || []).length})</h2>
      ${table(d.queue || [], [
        { key: "pos", label: "#" },
        { key: "title", label: "Title" },
        { key: "artist", label: "Artist" },
        { key: "username", label: "Requester" },
        { key: "status", label: "Status", render: (r) => pill(r.status) },
      ], (r) => `<button class="btn danger sm" data-remove-request="${r.id}">Remove</button>`)}
    </div>
    ${renderSchemaGroups("radio", {})}
  `;
}

function renderEventsTab() {
  const d = state.data || {};
  const tables = d.tables || {};
  const hasRows = Object.values(tables).some((info) => info.exists && (info.rows || []).length);
  return `
    <div class="grid">
      <div class="card">
        <h2>📅 Scheduled Events</h2>
        ${renderEventRows(tables.scheduled_events?.rows || d.scheduled || [])}
      </div>
    </div>
    ${hasRows ? `
      ${renderEventTableCard("Event Definitions", tables.event_definitions)}
      ${renderEventTableCard("Event History", tables.event_history)}
      ${renderEventTableCard("Event Points", tables.event_points)}
      ${renderEventTableCard("Event Settings", tables.event_settings)}
      ${renderEventTableCard("Event Votes", tables.event_votes)}
    ` : `<div class="card"><div class="notice">No event rows found in the live DB tables.</div></div>`}
    ${futureControls([
      { endpoint: "POST /api/events/start", purpose: "Start event", status: "Future" },
      { endpoint: "POST /api/events/stop", purpose: "Stop current event", status: "Future" },
      { endpoint: "POST /api/events/schedule", purpose: "Schedule event", status: "Future" },
    ])}
  `;
}

function renderEventRows(rows) {
  return rows.length ? rows.map((e) => `<div class="pub-event-item">
    <div class="pub-event-name">${esc(e.name || e.event_name || e.title || e.id || "Event")}</div>
    ${e.description ? `<div class="muted text-sm">${esc(e.description)}</div>` : ""}
    ${e.starts_at ? `<div class="muted text-sm">📅 ${esc(e.starts_at)}</div>` : ""}
  </div>`).join("") : `<div class="notice">No scheduled events.</div>`;
}

function renderEventTableCard(title, info) {
  if (!info?.exists) return "";
  const rows = info.rows || [];
  return `<div class="card">
    <h2>${esc(title)}</h2>
    ${rows.length ? table(rows) : `<div class="notice">Table exists with no rows.</div>`}
  </div>`;
}

function renderAnnouncementsTab() {
  const d = state.data || {};
  const known = d.known_settings || {};
  const extra = d.extra_settings || [];
  const allRaw = Object.entries(known).map(([key, value]) => ({ key, value, source: "room_settings" })).concat(extra);
  const valMap = settingsMapFrom(allRaw);
  const annoGroup = SETTINGS_SCHEMA.room.find((g) => g.title === "Announcement Settings");
  return `
    ${annoGroup ? renderSettingsGroup(annoGroup, valMap, "announcements") : ""}
    <div class="card">
      <h2>📢 Send Announcement</h2>
      <p class="muted text-sm" style="margin-bottom:12px">Queues a room-wide message for the host bot.</p>
      <form id="announcementForm" style="display:grid;gap:10px">
        <textarea name="message" rows="4" required maxlength="500" placeholder="Announcement message"></textarea>
        <button class="btn primary">Queue Announcement</button>
      </form>
      ${queueHelp()}
    </div>
  `;
}

function renderWelcomeTab() {
  const d = state.data || {};
  const known = d.known_settings || {};
  const extra = d.extra_settings || [];
  const allRaw = Object.entries(known).map(([key, value]) => ({ key, value, source: "room_settings" })).concat(extra);
  const valMap = settingsMapFrom(allRaw);
  const welGroup = SETTINGS_SCHEMA.room.find((g) => g.title === "Welcome Settings");
  return `
    ${welGroup ? renderSettingsGroup(welGroup, valMap, "welcome") : ""}
    ${renderAdvancedCollapse(allRaw.filter((s) => s.key?.startsWith("welcome")))}
  `;
}

function renderEmotesTab() {
  const d = state.data || {};
  const known = d.known_settings || {};
  const extra = d.extra_settings || [];
  const allRaw = Object.entries(known).map(([key, value]) => ({ key, value, source: "room_settings" })).concat(extra);
  const valMap = settingsMapFrom(allRaw);
  return `
    ${renderSchemaGroups("emotes", valMap)}
    <div class="card">
      <h2>Trigger Bot Emote</h2>
      <form id="emoteCommandForm" class="toolbar" style="flex-wrap:wrap">
        <input name="target_bot" placeholder="Bot username or mode" required style="flex:1;min-width:160px" />
        <input name="emote" placeholder="Emote name or ID" required style="flex:1;min-width:160px" />
        <button class="btn primary">Queue Emote</button>
      </form>
      ${queueHelp()}
    </div>
    ${futureControls([
      { endpoint: "GET /api/room/emote-packs", purpose: "List and manage emote packs", status: "Future" },
    ])}
    ${renderAdvancedCollapse(allRaw.filter((s) =>
      s.key?.includes("emote") || s.key?.includes("dance") || s.key?.includes("react")))}
  `;
}

/* ── Economy & Rewards ───────────────────────────────── */
function renderEconomyRewards(tab) {
  return `
    ${tabNav("Economy & Rewards")}
    ${tab === "Coins & Tickets" ? renderCoinsTab() : ""}
    ${tab === "Casino"          ? renderCasinoTab() : ""}
    ${tab === "Games"           ? renderGamesTab() : ""}
    ${tab === "VIP"             ? renderVipTab() : ""}
    ${tab === "Titles"          ? renderTitlesTab() : ""}
    ${tab === "Badges"          ? renderBadgesTab() : ""}
    ${tab === "Rewards"         ? renderRewardsTab() : ""}
  `;
}

function renderCasinoTab() {
  const d = state.data || {};
  const settings = d.settings || [];
  const valMap = settingsMapFrom(settings);
  const flag = d.module_flag;
  const unverifiedCasinoGroups = (SETTINGS_SCHEMA.casino || []).filter((g) => g.title !== "Blackjack Settings — AceSinatra");
  return `
    <div class="card">
      <div class="card-header">
        <h2>🎲 Casino Module</h2>
        ${flag ? pill(flag.enabled ? "enabled" : "disabled") : `<span class="muted text-sm">no flag set</span>`}
      </div>
      <div class="inline-actions">
        <button class="btn cyan" data-toggle-module="casino" data-enabled="${flag?.enabled ? "0" : "1"}">${flag?.enabled ? "Disable Casino" : "Enable Casino"}</button>
      </div>
    </div>
    ${renderBlackjackSettingsCard(d.blackjack_settings || {})}
    ${renderPokerSettingsCard(d.active_poker_settings || {})}
    ${renderPokerRawSettings(d.poker_settings || [])}
    <details class="advanced-collapse">
      <summary class="advanced-summary">
        <span class="pill def">Legacy</span> Legacy Blackjack Settings
        <span class="muted text-sm">Old bj_settings row</span>
      </summary>
      <div class="advanced-content">
        ${d.legacy_blackjack_settings ? table(Object.entries(d.legacy_blackjack_settings).map(([key, value]) => ({ key, value })), [
          { key: "key", label: "Column" },
          { key: "value", label: "Value" },
        ]) : `<div class="notice">No legacy <code>bj_settings</code> row found.</div>`}
      </div>
    </details>
    ${renderUnverifiedCasinoSettings(unverifiedCasinoGroups, valMap)}
    ${renderAdvancedCollapse(settings)}
  `;
}

function renderBlackjackSettingsCard(settings) {
  const group = (SETTINGS_SCHEMA.casino || []).find((g) => g.title === "Blackjack Settings — AceSinatra");
  if (!group) return "";
  const values = { ...settings };
  return `<div class="card">
    <div class="card-header">
      <div>
        <h2>🃏 Blackjack Settings — AceSinatra</h2>
        <div class="muted text-sm">Source: <code>${esc(settings.source || "rbj_settings")}</code></div>
      </div>
      <span class="pill info">Realistic BJ</span>
    </div>
    <form id="blackjackSettingsForm">
      <div class="settings-fields">
        ${group.keys.map((f) => renderSettingsField(f, values[f.key])).join("")}
      </div>
      <div style="margin-top:14px">
        <button class="btn primary sm" type="submit">💾 Save Blackjack Settings</button>
      </div>
    </form>
  </div>`;
}

function renderPokerSettingsCard(settings) {
  const fields = [
    { key: "enabled",     label: "Enabled",      type: "toggle" },
    { key: "min_buyin",   label: "Min Buy-In",   type: "number", suffix: "coins" },
    { key: "max_buyin",   label: "Max Buy-In",   type: "number", suffix: "coins" },
    { key: "max_players", label: "Max Players",  type: "number" },
    { key: "turn_timer",  label: "Turn Timer",   type: "number", suffix: "sec" },
    { key: "small_blind", label: "Small Blind",  type: "number", suffix: "coins" },
    { key: "big_blind",   label: "Big Blind",    type: "number", suffix: "coins" },
  ];
  return `<div class="card">
    <div class="card-header">
      <div>
        <h2>♠️ Poker Settings — ChipSoprano</h2>
        <div class="muted text-sm">Source: <code>${esc(settings.source || "poker_settings")}</code>. Enabled maps to <code>v2_paused</code>.</div>
      </div>
      <span class="pill info">Poker V2</span>
    </div>
    <form id="pokerSettingsForm">
      <div class="settings-fields">
        ${fields.map((f) => renderSettingsField(f, settings[f.key])).join("")}
      </div>
      <div class="notice" style="margin-top:12px">Only fields loaded by the active Poker V2 module are writable here. Legacy keys stay in raw settings.</div>
      <div style="margin-top:14px">
        <button class="btn primary sm" type="submit">💾 Save Poker Settings</button>
      </div>
    </form>
  </div>`;
}

function renderPokerRawSettings(rows) {
  const verified = new Set(["v2_paused", "v2_min_buyin", "v2_max_buyin", "v2_max_players", "v2_turn_seconds", "v2_small_blind", "v2_big_blind"]);
  const raw = (rows || [])
    .filter((r) => !verified.has(r.key))
    .map((r) => ({
      key: r.key,
      value: r.value,
      status: String(r.key || "").startsWith("v2_") ? "Advanced V2" : "Legacy / unverified for active V2",
    }));
  return `<details class="advanced-collapse">
    <summary class="advanced-summary">
      <span class="pill warn">Raw</span> Advanced / Poker Raw Settings
      <span class="muted text-sm">Read-only poker_settings keys not in the normal V2 card</span>
    </summary>
    <div class="advanced-content">
      ${raw.length ? table(raw, [
        { key: "key", label: "Key" },
        { key: "value", label: "Value" },
        { key: "status", label: "Status", render: (r) => `<span class="pill warn">${esc(r.status)}</span>` },
      ]) : `<div class="notice">No extra poker_settings keys found.</div>`}
    </div>
  </details>`;
}

function renderUnverifiedCasinoSettings(groups, valuesMap) {
  const rows = (groups || [])
    .filter((g) => g.title !== "Poker Settings")
    .flatMap((g) => (g.keys || []).map((f) => ({
      field: f.label,
      key: f.key,
      section: g.title,
      current_value: valuesMap[f.key] ?? "—",
      status: "Unverified source",
    })));
  return `<details class="advanced-collapse">
    <summary class="advanced-summary">
      <span class="pill warn">Unverified</span> Advanced / Unverified Casino Settings
      <span class="muted text-sm">Hidden until sources match in-room commands</span>
    </summary>
    <div class="advanced-content">
      ${rows.length ? table(rows, [
        { key: "section", label: "Section" },
        { key: "field", label: "Field" },
        { key: "key", label: "Dashboard Key" },
        { key: "current_value", label: "Current Value" },
        { key: "status", label: "Status", render: (r) => `<span class="pill warn">${esc(r.status)}</span>` },
      ]) : `<div class="notice">No unverified casino settings listed.</div>`}
    </div>
  </details>`;
}

function renderGamesTab() {
  const d = state.data || {};
  const settings = d.settings || [];
  const valMap = settingsMapFrom(settings);
  const flag = d.module_flag;
  return `
    <div class="card">
      <div class="card-header">
        <h2>🎮 Games Module</h2>
        ${flag ? pill(flag.enabled ? "enabled" : "disabled") : `<span class="muted text-sm">no flag set</span>`}
      </div>
      <div class="inline-actions">
        <button class="btn cyan" data-toggle-module="games" data-enabled="${flag?.enabled ? "0" : "1"}">${flag?.enabled ? "Disable Games" : "Enable Games"}</button>
      </div>
    </div>
    <details class="advanced-collapse">
      <summary class="advanced-summary">
        <span class="pill warn">Unverified</span> Advanced / Unverified Game Settings
        <span class="muted text-sm">Use Settings Audit before enabling writes</span>
      </summary>
      <div class="advanced-content">
        <div class="notice warn">These controls are hidden from the normal page until their in-room command sources are wired to exact DB keys.</div>
        ${renderSchemaGroups("games", valMap)}
      </div>
    </details>
    ${renderAdvancedCollapse(settings)}
  `;
}

function renderCoinsTab() {
  const d = state.data || {};
  const s = d.stats;
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr))">
      ${metricCard("Players", s?.player_count ?? "—", "in database", "", "👤")}
      ${metricCard("Total Balance", s?.total_balance != null ? Number(s.total_balance).toLocaleString() : "—", "in circulation", "accent-green", "💰")}
      ${s?.total_tickets != null ? metricCard("Total Tickets", Number(s.total_tickets).toLocaleString(), "in circulation", "", "🎟") : ""}
      ${metricCard("Avg Balance", s?.avg_balance != null ? Math.round(Number(s.avg_balance)).toLocaleString() : "—", "per player", "", "📊")}
      ${metricCard("Richest Balance", s?.richest_balance != null ? Number(s.richest_balance).toLocaleString() : "—", "single player", "", "🏆")}
    </div>
    <div class="grid">
      <div class="card">
        <h2>💰 Rich List</h2>
        ${table(d.top_rich || [], [
          { key: "username", label: "Player" },
          { key: "balance", label: "Balance", render: (r) => Number(r.balance ?? 0).toLocaleString() },
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
    ${futureControls([
      { endpoint: "POST /api/player/:id/economy", purpose: "Adjust balance, tickets, or XP", status: "Future" },
      { endpoint: "POST /api/economy/adjust", purpose: "Bulk economy adjustment", status: "Future" },
      { endpoint: "GET /api/economy/transactions", purpose: "Transaction history", status: "Future" },
    ])}
  `;
}

function renderVipTab() {
  return `<div class="card">
    <h2>⭐ VIP Summary</h2>
    <p class="muted text-sm">VIP status is stored in <code>owned_items</code> with <code>item_id='vip'</code>. Use Player Search to inspect individual inventories.</p>
  </div>
  ${futureControls([
    { endpoint: "GET /api/vip/list", purpose: "List all VIP players", status: "Future" },
    { endpoint: "POST /api/vip/add", purpose: "Grant VIP", status: "Future" },
    { endpoint: "POST /api/vip/remove", purpose: "Remove VIP", status: "Future" },
  ])}`;
}

function renderBadgesTab() {
  return `<div class="card">
    <h2>🏅 Badges</h2>
    <p class="muted text-sm">Equipped badge data is available in Player Search. Badge write actions are hidden until the dashboard has a dedicated workflow.</p>
  </div>
  ${futureControls([
    { endpoint: "GET /api/badges", purpose: "List available badge types", status: "Future" },
    { endpoint: "POST /api/player/:id/badges", purpose: "Give badge to player", status: "Future" },
    { endpoint: "DELETE /api/player/:id/badges/:badge", purpose: "Remove badge", status: "Future" },
  ])}`;
}

function renderRewardsTab() {
  return `<div class="card">
    <h2>🎁 Rewards</h2>
    <p class="muted text-sm">Reward editing is kept out of the normal dashboard surface until the backing endpoints are wired.</p>
  </div>
  ${futureControls([
    { endpoint: "GET /api/rewards", purpose: "List reward configurations", status: "Future" },
    { endpoint: "PUT /api/rewards/:id", purpose: "Update daily/event rewards", status: "Future" },
    { endpoint: "GET /api/quests", purpose: "List quest configs", status: "Future" },
  ])}`;
}

/* ── Staff Page (shared for owner Staff page) ────────── */
function renderStaffPage_shared() {
  const d = state.data || {};
  return `
    <div class="grid">
      <div class="card">
        <h2>➕ Create Staff Account</h2>
        <form id="staffCreateForm">
          <div class="field"><label class="field-label">Username</label><input name="username" required /></div>
          <div class="field"><label class="field-label">Password</label><input name="password" type="password" required /></div>
          <div class="field" style="margin-bottom:14px">
            <label class="field-label">Role</label>
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
        <h2>🤖 Bot Role Management</h2>
        <p class="muted text-sm" style="margin-bottom:12px">Manages roles in the bot's own tables (owner_users, admin_users, managers, moderators, dj_users).</p>
        <form id="botRoleForm">
          <div class="field"><label class="field-label">Username</label><input name="username" required placeholder="Highrise username" /></div>
          <div class="field" style="margin-bottom:14px">
            <label class="field-label">Role</label>
            <select name="role">
              <option value="admin">Admin</option><option value="manager">Manager</option>
              <option value="mod">Mod</option><option value="dj">DJ</option>
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
      <h2>👥 Dashboard Users</h2>
      ${table(d.dashboard_users || [], [
        { key: "username", label: "User" },
        { key: "role", label: "Role", render: (r) => `<span class="pill ${r.role === "owner" ? "ok" : "def"}">${esc(r.role)}</span>` },
        { key: "disabled", label: "State", render: (r) => r.disabled ? pill("disabled") : pill("enabled") },
        { key: "last_login_at", label: "Last Login" },
      ], (r) => `
        <button class="btn sm" data-edit-staff="${r.id}" data-role="${esc(r.role)}" data-disabled="${r.disabled ? "1" : "0"}" data-perms="${encodeURIComponent(JSON.stringify(r.permissions || {}))}">Edit</button>
        <button class="btn danger sm" data-remove-staff="${r.id}">Remove</button>
      `)}
    </div>
    <div class="card">
      <h2>🤖 Bot Roles</h2>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr))">
        ${Object.entries(d.bot_roles || {}).map(([role, rows]) => `
          <div>
            <div class="field-label" style="margin-bottom:6px;text-transform:capitalize">${esc(role)}</div>
            ${rows.length ? rows.map((r) => `<div class="muted text-sm">@${esc(r.username || r.user_id || "?")}</div>`).join("") : `<div class="muted text-sm">None</div>`}
          </div>`).join("")}
      </div>
    </div>
  `;
}

/* ── System Page ─────────────────────────────────────── */
function renderSystemPage(tab) {
  return `
    ${tabNav("System")}
    ${tab === "Health" ? renderSystemHealth() : ""}
    ${tab === "Logs" ? renderSystemLogs() : ""}
    ${tab === "Settings Audit" ? renderSettingsAudit() : ""}
    ${tab === "Emergency" ? renderEmergency() : ""}
    ${tab === "Database" ? renderSystemDatabase() : ""}
  `;
}

function renderSystemHealth() {
  const d = state.data || {};
  return `
    <div class="grid">
      <div class="card">
        <h2>🟢 Dashboard Health</h2>
        <div style="display:grid;gap:8px">
          <div class="inline-actions"><span>Status</span>${pill(d.status || "unknown")}</div>
          <div class="inline-actions"><span>DB Exists</span>${pill(d.db_exists ? "ok" : "error")}</div>
          <div class="inline-actions"><span>Mode</span><span class="pill def">${esc(d.app_mode || "—")}</span></div>
          <div class="inline-actions"><span>Port</span><span class="muted text-sm">${esc(String(d.port || "—"))}</span></div>
          <div class="muted text-sm" style="margin-top:4px">DB: <code>${esc(d.resolved_db_path || "—")}</code></div>
        </div>
      </div>
      <div class="card">
        <h2>📊 Known Tables</h2>
        <div style="display:grid;gap:4px;font-size:13px">
          ${Object.entries(d.known_tables || {}).map(([t, exists]) =>
            `<div class="inline-actions"><span class="muted">${esc(t)}</span>${pill(exists ? "ok" : "missing")}</div>`
          ).join("")}
        </div>
      </div>
    </div>
  `;
}

function renderSystemLogs() {
  const d = state.data || {};
  return `
    <div class="card">
      <h2>📋 Audit Log</h2>
      <div class="toolbar" style="margin-bottom:14px;flex-wrap:wrap">
        <input id="logAction" placeholder="Action type" value="${esc(state.logs.action_type)}" style="flex:1;min-width:100px" />
        <input id="logUser" placeholder="User" value="${esc(state.logs.user)}" style="flex:1;min-width:100px" />
        <input id="logModule" placeholder="Module / target" value="${esc(state.logs.module)}" style="flex:1;min-width:100px" />
        <button class="btn" data-action="logs-filter">Filter</button>
        <button class="btn ghost" data-action="logs-prev">← Prev</button>
        <button class="btn ghost" data-action="logs-next">Next →</button>
      </div>
      ${table(d.audit_logs || [])}
    </div>
    <div class="card"><h2>⚠️ Command Errors</h2>${table(d.command_error_logs || [])}</div>
    <div class="card"><h2>📝 Admin Action Logs</h2>${table(d.admin_action_logs || [])}</div>
  `;
}

function auditStatusChip(status, connected) {
  const s = String(status || (connected ? "CONNECTED" : "UNKNOWN")).toUpperCase();
  const cls = s === "CONNECTED" ? "ok" : s === "LEGACY" ? "def" : s === "UNKNOWN" ? "warn" : "bad";
  return `<span class="pill ${cls}">${esc(s)}</span>`;
}

function renderSettingsAudit() {
  const d = state.data || {};
  const rows = d.rows || [];
  const modules = [...new Set(rows.map((r) => r.module).filter(Boolean))].sort();
  const pages = [...new Set(rows.map((r) => r.dashboard_page).filter(Boolean))].sort();
  const f = state.settingsAudit;
  const filtered = rows.filter((r) => {
    const status = String(r.status || "").toUpperCase();
    if (f.status === "broken" && status !== "BROKEN") return false;
    if (f.status === "connected" && status !== "CONNECTED") return false;
    if (f.status === "legacy" && status !== "LEGACY") return false;
    if (f.status === "unknown" && status !== "UNKNOWN") return false;
    if (f.module && r.module !== f.module) return false;
    if (f.page && r.dashboard_page !== f.page) return false;
    return true;
  });
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr));margin-bottom:14px">
      ${metricCard("Connected", d.connected_count ?? 0, "dashboard fields verified", "accent-green", "✓")}
      ${metricCard("Broken / Missing", d.broken_count ?? 0, "verified source but not wired", "accent-red", "!")}
      ${metricCard("Legacy", (d.duplicate_or_legacy_settings || []).length, "kept out of normal UI", "", "↺")}
      ${metricCard("Unknown Commands", (d.unknown_commands || []).length, "needs source review", "", "?")}
    </div>
    <div class="card">
      <div class="card-header">
        <div>
          <h2>Settings Command Audit</h2>
          <div class="muted text-sm">Generated: ${esc(d.generated_at || "—")}</div>
        </div>
        <span class="pill info">${filtered.length} rows</span>
      </div>
      <div class="toolbar" style="margin-bottom:14px;flex-wrap:wrap">
        <select id="settingsAuditStatus">
          ${[
            ["all", "All"],
            ["broken", "Broken only"],
            ["connected", "Connected"],
            ["legacy", "Legacy"],
            ["unknown", "Unknown"],
          ].map(([value, label]) => `<option value="${value}" ${f.status === value ? "selected" : ""}>${label}</option>`).join("")}
        </select>
        <select id="settingsAuditModule">
          <option value="">All modules</option>
          ${modules.map((m) => `<option value="${esc(m)}" ${f.module === m ? "selected" : ""}>${esc(m)}</option>`).join("")}
        </select>
        <select id="settingsAuditPage">
          <option value="">All pages</option>
          ${pages.map((p) => `<option value="${esc(p)}" ${f.page === p ? "selected" : ""}>${esc(p)}</option>`).join("")}
        </select>
      </div>
      ${table(filtered, [
        { key: "module", label: "Module" },
        { key: "command", label: "Command" },
        { key: "display_name", label: "Dashboard Field" },
        { key: "db_source", label: "DB Source", render: (r) => `<code>${esc(`${r.db_table}.${r.db_key_or_column}`)}</code>` },
        { key: "current_value", label: "Current Value", render: (r) => `<code>${esc(r.current_value ?? "—")}</code>` },
        { key: "status", label: "Status", render: (r) => auditStatusChip(r.status, r.dashboard_connected) },
        { key: "notes", label: "Notes" },
      ])}
    </div>
    ${futureControls((d.unknown_commands || []).map((command) => ({
      endpoint: command,
      purpose: "Command/source mapping not fully verified yet",
      status: "UNKNOWN",
    })), "Unknown Settings Commands")}
  `;
}

function renderEmergency() {
  const d = state.data || {};
  return `
    <div class="notice warn" style="margin-bottom:0">
      ⚠️ Emergency controls write DB flags only. Bots consume these on next heartbeat — no processes are killed.
    </div>
    <div class="card danger-zone">
      <h2>🚨 Quick Disable</h2>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin-top:4px">
        <button class="btn danger" data-emergency="disable_radio_requests">🎵 Disable Radio Requests</button>
        <button class="btn danger" data-emergency="disable_casino">🎲 Disable Casino</button>
        <button class="btn danger" data-emergency="disable_games">🎮 Disable Games</button>
        <button class="btn danger" data-emergency="clear_queue">🗑 Clear Queue</button>
      </div>
    </div>
    <div class="card">
      <h2>📊 Module Flags</h2>
      ${moduleFlagTable(d.module_flags || [])}
    </div>
  `;
}

function renderSystemDatabase() {
  const d = state.data || {};
  const tables = d.tables || {};
  return `
    <div class="card">
      <h2>🗄 Database Inspect</h2>
      <div class="muted text-sm" style="margin-bottom:12px">DB: <code>${esc(d.resolved_db_path || "—")}</code></div>
      ${Object.keys(tables).length ? table(
        Object.entries(tables).map(([name, info]) => ({ name, row_count: info.row_count, columns: (info.columns || []).length, important: info.important ? "✓" : "" })),
        [
          { key: "name", label: "Table" },
          { key: "row_count", label: "Rows" },
          { key: "columns", label: "Columns" },
          { key: "important", label: "Core" },
        ]
      ) : `<div class="notice">No table data — DB may not be connected.</div>`}
    </div>
  `;
}

/* ═══════════════════════════════════════════════════════
   STAFF CONSOLE
══════════════════════════════════════════════════════ */
function renderStaffPage(page) {
  switch (page) {
    case "Staff Home":  return renderStaffHome();
    case "Radio Queue": return renderStaffRadioQueue();
    case "Players":     return renderStaffPlayers();
    case "Events":      return renderStaffEvents();
    case "Room Tools":  return renderStaffRoomTools();
    case "Logs":        return renderStaffLogs();
    default:            return renderStaffHome();
  }
}

function renderStaffHome() {
  const d = state.data || {};
  const m = d.metrics || {};
  const r = d.radio || {};
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr))">
      ${metricCard("Bots Online", `${m.online_bots ?? 0}/${m.total_bots ?? 0}`, "active bots", "accent-green", "🤖")}
      ${metricCard("Room Users", m.current_room_users ?? "—", "live", "accent-cyan", "👥")}
      ${metricCard("Queue", m.queue_count ?? 0, "songs pending", "", "🎵")}
      ${metricCard("Games", m.active_games ?? 0, "active", "", "🎲")}
    </div>
    <div class="card">
      <h2>🎵 Now Playing</h2>
      <div style="font-size:17px;font-weight:700;margin-bottom:4px">${esc(r.now_playing?.title || "Auto DJ")}</div>
      ${r.now_playing?.artist ? `<div class="muted text-sm">${esc(r.now_playing.artist)}</div>` : ""}
    </div>
    <div class="grid">
      <div class="card">
        <h2>📊 Module Status</h2>
        ${moduleFlagTable(d.module_flags || [])}
      </div>
      <div class="card">
        <h2>⚠️ Recent Errors</h2>
        ${table(d.command_errors || [], [
          { key: "command", label: "Command" },
          { key: "error", label: "Error" },
          { key: "created_at", label: "Time" },
        ])}
      </div>
    </div>
  `;
}

function renderStaffRadioQueue() {
  const d = state.data || {};
  return `
    <div class="grid">
      <div class="card">
        <h2>🎵 Now Playing</h2>
        ${d.now_playing ? `
          <div style="font-size:17px;font-weight:700;margin-bottom:4px">${esc(d.now_playing.title || "—")}</div>
          ${d.now_playing.artist ? `<div class="muted text-sm">${esc(d.now_playing.artist)}</div>` : ""}
          ${d.now_playing.username ? `<div class="muted text-sm">Requested by ${esc(d.now_playing.username)}</div>` : ""}
          ${can("manage_radio") ? `<div class="inline-actions" style="margin-top:12px">
            <button class="btn danger sm" data-action="radio-skip">⏭ Skip</button>
          </div>` : ""}` : `<div class="notice">Nothing playing.</div>`}
      </div>
      <div class="card">
        <h2>🎛 Controls</h2>
        ${can("manage_radio") ? `
          <label class="switch" style="margin-bottom:16px">
            <input type="checkbox" id="requestsEnabled" ${d.queue_open ? "checked" : ""} />
            <span>Requests enabled</span>
          </label>
          <hr class="divider" />
          <button class="btn danger" data-action="radio-clear">🗑 Clear Queue</button>
        ` : `<div class="notice">You need <code>manage_radio</code> permission to control the queue.</div>`}
      </div>
    </div>
    <div class="card">
      <h2>📋 Queue (${(d.queue || []).length})</h2>
      ${table(d.queue || [], [
        { key: "pos", label: "#" },
        { key: "title", label: "Title" },
        { key: "artist", label: "Artist" },
        { key: "username", label: "Requester" },
        { key: "status", label: "Status", render: (r) => pill(r.status) },
      ], can("manage_radio") ? (r) => `<button class="btn danger sm" data-remove-request="${r.id}">Remove</button>` : null)}
    </div>
  `;
}

function renderStaffPlayers() {
  return `
    <div class="card">
      <h2>🔍 Player Search</h2>
      <form id="playerSearchForm" class="toolbar" style="flex-wrap:wrap">
        <input name="query" placeholder="Username or User ID" required style="flex:1;min-width:200px" />
        <button class="btn primary">Search</button>
      </form>
      <div id="playerSearchResult" style="margin-top:16px"></div>
    </div>
  `;
}

function renderStaffEvents() {
  const d = state.data || {};
  const tables = d.tables || {};
  const scheduled = tables.scheduled_events?.rows || d.scheduled || [];
  return `
    <div class="grid">
      <div class="card">
        <h2>📅 Scheduled Events</h2>
        ${renderEventRows(scheduled)}
      </div>
    </div>
    ${renderEventTableCard("Event Definitions", tables.event_definitions)}
    ${renderEventTableCard("Event History", tables.event_history)}
    ${renderEventTableCard("Event Points", tables.event_points)}
    ${futureControls([
      { endpoint: "POST /api/events/start", purpose: "Start event", status: "Owner future" },
      { endpoint: "POST /api/events/stop", purpose: "Stop current event", status: "Owner future" },
    ])}
  `;
}

function renderStaffRoomTools() {
  const d = state.data || {};
  const known = d.known_settings || {};
  const hasPerms = can("emergency_controls");

  function boolRow(key, label) {
    const val = known[key];
    const checked = val === "true" || val === "1";
    return `<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid #1e2822">
      <span>${esc(label)}</span>
      <label class="switch" style="margin:0">
        <input type="checkbox" data-room-toggle="${esc(key)}" ${checked ? "checked" : ""} />
        <span></span>
      </label>
    </div>`;
  }

  return `
    <div class="grid">
      <div class="card">
        <h2>🔧 Room Flags</h2>
        ${hasPerms ? `
          ${boolRow("welcome_enabled", "Welcome messages")}
          ${boolRow("public_emotes_enabled", "Public emotes")}
          ${boolRow("social_enabled", "Social features")}
          ${boolRow("announcements_enabled", "Announcements")}
        ` : `<div class="notice">Requires <code>emergency_controls</code> permission — contact the owner to enable this access.</div>`}
      </div>
      <div class="card">
        <h2>👋 Welcome Message</h2>
        ${hasPerms ? `
          <form class="roomSettingForm" data-key="welcome_message" style="display:grid;gap:10px">
            <textarea name="value" rows="4" placeholder="Enter welcome message"
              style="width:100%;box-sizing:border-box;background:#0e120f;color:#fff;border:1px solid #334037;border-radius:8px;padding:10px;font:inherit;resize:vertical"
            >${esc(known.welcome_message ?? "")}</textarea>
            <button class="btn primary sm">💾 Save</button>
          </form>
        ` : `<div class="notice">Requires <code>emergency_controls</code> permission.</div>`}
      </div>
    </div>
    <div class="card">
      <h2>📢 Announcements</h2>
      ${hasPerms ? `
        <form id="announcementForm" style="display:grid;gap:10px">
          <textarea name="message" rows="4" required maxlength="500" placeholder="Announcement message"></textarea>
          <button class="btn primary">Queue Announcement</button>
        </form>
        ${queueHelp()}
      ` : `<div class="notice">Requires <code>emergency_controls</code> permission to queue announcements.</div>`}
    </div>
  `;
}

function renderStaffLogs() {
  const d = state.data || {};
  return `
    <div class="card">
      <h2>📋 Audit Log</h2>
      <div class="toolbar" style="margin-bottom:14px;flex-wrap:wrap">
        <input id="logAction" placeholder="Action type" value="${esc(state.logs.action_type)}" style="flex:1;min-width:100px" />
        <input id="logUser" placeholder="User" value="${esc(state.logs.user)}" style="flex:1;min-width:100px" />
        <input id="logModule" placeholder="Module" value="${esc(state.logs.module)}" style="flex:1;min-width:100px" />
        <button class="btn" data-action="logs-filter">Filter</button>
        <button class="btn ghost" data-action="logs-prev">← Prev</button>
        <button class="btn ghost" data-action="logs-next">Next →</button>
      </div>
      ${table(d.audit_logs || [])}
    </div>
    <div class="card"><h2>⚠️ Command Errors</h2>${table(d.command_error_logs || [])}</div>
  `;
}

/* ═══════════════════════════════════════════════════════
   SHARED ADMIN COMPONENTS
══════════════════════════════════════════════════════ */
function moduleFlagTable(flags) {
  return table(flags, [
    { key: "module", label: "Module" },
    { key: "enabled", label: "State", render: (r) => Number(r.enabled) === 1 ? pill("enabled") : pill("disabled") },
    { key: "reason", label: "Reason" },
    { key: "updated_by", label: "By" },
    { key: "updated_at", label: "Updated" },
  ], (r) => `<button class="btn sm ${Number(r.enabled) === 1 ? "danger" : "cyan"}" data-toggle-module="${esc(r.module)}" data-enabled="${Number(r.enabled) === 1 ? "0" : "1"}">${Number(r.enabled) === 1 ? "Disable" : "Enable"}</button>`);
}

function permissionChecks(perms) {
  const all = ["manage_radio","manage_casino","manage_games","manage_titles","manage_staff","view_logs","emergency_controls"];
  return `<div style="display:grid;gap:8px">${all.map((p) => `<label class="switch">
    <input type="checkbox" name="perm_${p}" ${perms[p] ? "checked" : ""} />
    <span>${p.replace(/_/g, " ")}</span>
  </label>`).join("")}</div>`;
}

/* ═══════════════════════════════════════════════════════
   EVENT BINDING
══════════════════════════════════════════════════════ */
function bindAdminPageEvents() {
  ["settingsAuditStatus", "settingsAuditModule", "settingsAuditPage"].forEach((id) => {
    document.getElementById(id)?.addEventListener("change", (e) => {
      const key = id.replace("settingsAudit", "").toLowerCase();
      state.settingsAudit[key] = e.target.value;
      render();
    });
  });

  /* Bot Config save */
  document.getElementById("botConfigForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.currentTarget));
    await action("Config saved.", () => api("/api/bot-config", { method: "POST", body: JSON.stringify(body) }));
  });

  /* Save + Restart */
  document.getElementById("saveAndRestartBtn")?.addEventListener("click", async () => {
    const form = document.getElementById("botConfigForm");
    if (!form) return;
    const body = Object.fromEntries(new FormData(form));
    await action("Config saved and restart flag written.", async () => {
      await api("/api/bot-config", { method: "POST", body: JSON.stringify(body) });
      await api("/api/bot-config/restart", { method: "POST", body: JSON.stringify({}) });
    });
  });

  /* Restart Bots */
  document.getElementById("restartBotsBtn")?.addEventListener("click", () => {
    confirmAction("Restart Bots", "Write a restart flag to the DB. Bots will restart on next heartbeat.", async () => {
      await action("Restart flag written.", () => api("/api/bot-config/restart", { method: "POST", body: JSON.stringify({}) }));
    });
  });

  /* Bot command queue actions */
  document.querySelectorAll("[data-bot-command]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const queuedAction = btn.dataset.botCommand;
      const targetBot = btn.dataset.targetBot;
      const run = () => action("Command queued. Bot must consume bot_command_queue.", () =>
        api("/api/bot-command", {
          method: "POST",
          body: JSON.stringify({ target_bot: targetBot, action: queuedAction, payload: {} }),
        }));
      if (queuedAction === "restart_requested") {
        confirmAction("Restart Bot", `Queue restart request for ${targetBot}?`, run);
      } else {
        run();
      }
    });
  });

  /* Command Center quick actions */
  document.getElementById("qaRestartBots")?.addEventListener("click", () => {
    confirmAction("Restart Bots", "Write a restart flag. Bots will restart on next heartbeat check.", async () => {
      await action("Restart flag written.", () => api("/api/bot-config/restart", { method: "POST", body: JSON.stringify({}) }));
    });
  });
  document.getElementById("qaToggleRequests")?.addEventListener("click", async () => {
    const r = state.data?.radio || {};
    const enable = !r.queue_open;
    await action(enable ? "Requests opened." : "Requests closed.", () =>
      api("/api/radio/requests-enabled", { method: "PUT", body: JSON.stringify({ enabled: enable }) }));
  });
  document.getElementById("qaDisableCasino")?.addEventListener("click", () => {
    confirmAction("Disable Casino", "Disable the casino module immediately?", async () => {
      await action("Casino disabled.", () =>
        api("/api/emergency", { method: "POST", body: JSON.stringify({ flags: { disable_casino: true } }) }));
    });
  });
  document.getElementById("qaDisableGames")?.addEventListener("click", () => {
    confirmAction("Disable Games", "Disable the games module immediately?", async () => {
      await action("Games disabled.", () =>
        api("/api/emergency", { method: "POST", body: JSON.stringify({ flags: { disable_games: true } }) }));
    });
  });
  document.getElementById("qaClearQueue")?.addEventListener("click", () => {
    confirmAction("Clear Queue", "Cancel all pending song requests?", async () => {
      await action("Queue cleared.", () =>
        api("/api/radio/clear", { method: "POST", body: JSON.stringify({}) }));
    });
  });

  /* Radio controls */
  document.querySelector('[data-action="radio-skip"]')?.addEventListener("click", () => {
    confirmAction("Skip Song", "Request a skip of the current song.", async () => {
      await action("Skip requested.", () => api("/api/radio/skip", { method: "POST", body: JSON.stringify({}) }));
    });
  });
  document.querySelector('[data-action="radio-clear"]')?.addEventListener("click", () => {
    confirmAction("Clear Queue", "Cancel all pending song requests?", async () => {
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
      confirmAction("Remove Request", `Remove request #${id} from the queue?`, async () => {
        await action("Request removed.", () => api(`/api/radio/requests/${id}/remove`, { method: "POST", body: JSON.stringify({}) }));
      });
    });
  });

  document.getElementById("blackjackSettingsForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const body = {};
    Array.from(form.elements).forEach((el) => {
      if (!el.name) return;
      body[el.name] = el.type === "checkbox" ? el.checked : el.value;
    });
    await action("Blackjack settings saved.", () =>
      api("/api/casino/blackjack-settings", { method: "PUT", body: JSON.stringify(body) }));
  });

  document.getElementById("pokerSettingsForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const body = {};
    Array.from(form.elements).forEach((el) => {
      if (!el.name) return;
      body[el.name] = el.type === "checkbox" ? el.checked : el.value;
    });
    await action("Poker settings saved.", () =>
      api("/api/casino/poker-settings", { method: "PUT", body: JSON.stringify(body) }));
  });

  document.getElementById("announcementForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = e.currentTarget.querySelector("[name='message']")?.value.trim() || "";
    if (!message) return;
    await action("Command queued. Bot must consume bot_command_queue.", () =>
      api("/api/room/announce", { method: "POST", body: JSON.stringify({ message }) }));
  });

  document.getElementById("emoteCommandForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    await action("Command queued. Bot must consume bot_command_queue.", () =>
      api("/api/bot-command", {
        method: "POST",
        body: JSON.stringify({
          target_bot: String(data.target_bot || "").trim(),
          action: "trigger_emote",
          payload: { emote: String(data.emote || "").trim() },
        }),
      }));
  });

  /* Settings group forms — data-settings-group */
  document.querySelectorAll("[data-settings-group]").forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const apiTemplate = form.dataset.sgApi || "";
      let opts = {};
      try { opts = JSON.parse(form.dataset.sgOpts || "{}"); } catch (_) {}

      const fields = Array.from(form.elements).filter((el) => el.name);
      const saves = fields.map((el) => {
        const key = el.name;
        let value;
        if (el.dataset.sfToggle === "1" || el.type === "checkbox") {
          value = el.checked ? "true" : "false";
        } else {
          value = el.value;
        }
        const url = apiTemplate.replace(/:key$/, encodeURIComponent(key));
        const body = { value, ...opts };
        return api(url, { method: "PUT", body: JSON.stringify(body) });
      });

      await action(`Saved ${fields.length} setting${fields.length !== 1 ? "s" : ""}.`, () => Promise.all(saves));
    });
  });

  /* Raw settings advanced editor — edit buttons */
  document.querySelectorAll("[data-raw-edit-key]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.rawEditKey;
      const val = btn.dataset.rawEditVal;
      const src = btn.dataset.rawEditSrc || "room_settings";
      const form = btn.closest(".advanced-content")?.querySelector(".rawSettingGroupForm");
      if (form) {
        form.querySelector("[name='key']").value = key;
        form.querySelector("[name='value']").value = val;
        form.dataset.editSrc = src;
        form.querySelector("[name='key']").focus();
      }
    });
  });

  /* Raw settings advanced editor — form submit */
  document.querySelectorAll(".rawSettingGroupForm").forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const key = form.querySelector("[name='key']").value.trim();
      const value = form.querySelector("[name='value']").value;
      const source = form.dataset.editSrc || "room_settings";
      if (!key) return;
      await action(`Setting "${key}" saved.`, () =>
        api(`/api/settings/${encodeURIComponent(key)}`, { method: "PUT", body: JSON.stringify({ value, source }) }));
    });
  });

  /* Module toggles */
  document.querySelectorAll("[data-toggle-module]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const mod = btn.dataset.toggleModule;
      const enable = btn.dataset.enabled === "1";
      confirmAction(`${enable ? "Enable" : "Disable"} Module`, `${enable ? "Enable" : "Disable"} the ${mod} module?`, async () => {
        await action(`Module ${mod} ${enable ? "enabled" : "disabled"}.`, () =>
          api(`/api/modules/${encodeURIComponent(mod)}`, { method: "PUT", body: JSON.stringify({ enabled: enable }) }));
      });
    });
  });

  /* Emergency controls */
  document.querySelectorAll("[data-emergency]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const flag = btn.dataset.emergency;
      confirmAction("Emergency Action", `Apply emergency flag: ${flag}? This disables the system immediately.`, async () => {
        await action(`Emergency: ${flag} applied.`, () =>
          api("/api/emergency", { method: "POST", body: JSON.stringify({ flags: { [flag]: true } }) }));
      });
    });
  });

  /* Player search */
  document.getElementById("playerSearchForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const query = e.currentTarget.querySelector("[name='query']").value.trim();
    const result = document.getElementById("playerSearchResult");
    if (!result) return;
    result.innerHTML = `<div class="notice">Searching…</div>`;
    try {
      const data = await api(`/api/player/search?q=${encodeURIComponent(query)}`);
      if (!data.player) {
        result.innerHTML = `<div class="notice">No player found for <strong>${esc(query)}</strong>.</div>`;
      } else {
        result.innerHTML = renderPlayerCard(data.player);
      }
    } catch (err) {
      result.innerHTML = `<div class="notice error">${esc(err.message)}</div>`;
    }
  });

  /* Titles assign */
  document.getElementById("assignTitleForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.currentTarget));
    await action("Title assigned.", () => api("/api/titles/assign", { method: "POST", body: JSON.stringify(body) }));
  });

  /* Staff management */
  document.getElementById("staffCreateForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    const perms = {};
    for (const [k, v] of Object.entries(data)) if (k.startsWith("perm_")) perms[k.slice(5)] = v === "on";
    await action("Staff account created.", () =>
      api("/api/staff", { method: "POST", body: JSON.stringify({ username: data.username, password: data.password, role: data.role, permissions: perms }) }));
  });
  document.getElementById("botRoleForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    await action(`Bot role ${e.submitter?.value === "remove" ? "removed" : "added"}.`, () =>
      api("/api/staff/bot-role", { method: "POST", body: JSON.stringify({ username: data.username, role: data.role, action: e.submitter?.value || "add" }) }));
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
      action("Staff updated.", () =>
        api(`/api/staff/${id}`, { method: "PUT", body: JSON.stringify({ role, disabled: btn.dataset.disabled === "1", permissions: {} }) }));
    });
  });

  /* Room setting toggles */
  document.querySelectorAll("[data-room-toggle]").forEach((cb) => {
    cb.addEventListener("change", async (e) => {
      const key = cb.dataset.roomToggle;
      const value = e.target.checked ? "true" : "false";
      await action(`${key} set to ${value}.`, () =>
        api(`/api/settings/${encodeURIComponent(key)}`, { method: "PUT", body: JSON.stringify({ value, source: "room_settings" }) }));
    });
  });

  /* Room setting forms (welcome message etc) */
  document.querySelectorAll(".roomSettingForm").forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const key = form.dataset.key;
      const value = form.querySelector("[name='value']")?.value ?? "";
      await action(`${key} saved.`, () =>
        api(`/api/settings/${encodeURIComponent(key)}`, { method: "PUT", body: JSON.stringify({ value, source: "room_settings" }) }));
    });
  });

  /* Raw room setting editor */
  document.getElementById("roomSettingRawForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    await action(`${data.key} saved.`, () =>
      api(`/api/settings/${encodeURIComponent(data.key)}`, { method: "PUT", body: JSON.stringify({ value: data.value, source: "room_settings" }) }));
  });

  /* Room setting inline edit buttons */
  document.querySelectorAll("[data-room-edit]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.roomEdit;
      const cur = btn.dataset.roomVal || "";
      const val = prompt(`Edit "${key}" (current: ${cur})`, cur);
      if (val !== null) action(`${key} saved.`, () =>
        api(`/api/settings/${encodeURIComponent(key)}`, { method: "PUT", body: JSON.stringify({ value: val, source: "room_settings" }) }));
    });
  });

  /* Logs filters */
  document.querySelector('[data-action="logs-filter"]')?.addEventListener("click", () => {
    state.logs.action_type = document.getElementById("logAction")?.value || "";
    state.logs.user = document.getElementById("logUser")?.value || "";
    state.logs.module = document.getElementById("logModule")?.value || "";
    state.logs.offset = 0; loadAdmin();
  });
  document.querySelector('[data-action="logs-prev"]')?.addEventListener("click", () => {
    state.logs.offset = Math.max(0, (state.logs.offset || 0) - 50); loadAdmin();
  });
  document.querySelector('[data-action="logs-next"]')?.addEventListener("click", () => {
    state.logs.offset = (state.logs.offset || 0) + 50; loadAdmin();
  });

  /* Admin page navigation from within pages */
  document.querySelectorAll("[data-admin-page]").forEach((btn) => {
    if (!btn.dataset.boundNav) {
      btn.dataset.boundNav = "1";
      btn.addEventListener("click", () => {
        state.adminPage = btn.dataset.adminPage; state.notice = ""; state.error = ""; loadAdmin();
      });
    }
  });
}

/* ── Boot ────────────────────────────────────────────── */
init();
