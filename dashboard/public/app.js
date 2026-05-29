/* ═══════════════════════════════════════════════════════
   ChillTopia Web Portal — app.js
   3-layer: Public Portal → Staff Console → Owner Control Center
══════════════════════════════════════════════════════ */

/* ── Public Nav ──────────────────────────────────────── */
const PUBLIC_NAV = [
  { id: "home",      icon: "🏠", label: "Home" },
  { id: "radio",     icon: "📻", label: "Radio" },
  { id: "howtoplay", icon: "📖", label: "How to Play" },
  { id: "casino",    icon: "🎲", label: "Casino" },
  { id: "mining",    icon: "⛏️", label: "Mining" },
  { id: "fishing",   icon: "🎣", label: "Fishing" },
  { id: "events",    icon: "🎉", label: "Events" },
  { id: "rankings",  icon: "🏆", label: "Rankings" },
  { id: "roominfo",  icon: "ℹ️",  label: "Room Info" },
];

/* ── Owner Nav ───────────────────────────────────────── */
const OWNER_NAV_GROUPS = [
  { group: "Home", items: [
    { id: "Command Center", icon: "⚡", label: "Command Center", keywords: "home overview quick actions" },
    { id: "Operations Center", icon: "🩺", label: "Operations Center", permission: "view_logs", keywords: "health alerts pm2 bots database" },
  ]},
  { group: "Live Room", items: [
    { id: "Bots", icon: "🤖", label: "Bots", permission: "manage_bots", keywords: "bot control spawns queue" },
    { id: "Room & Content", icon: "🏠", label: "Room & Content", permission: "manage_room", keywords: "room welcome announcement content" },
    { id: "Radio", icon: "📻", label: "Radio", permission: "manage_radio", keywords: "dj dudu songs requests queue" },
    { id: "Emotes", icon: "💃", label: "Emotes", permission: "manage_emotes", keywords: "dancefloor sync botemote social" },
    { id: "Automation Center", icon: "⏱️", label: "Automation Center", permission: "manage_automation", keywords: "scheduled announcements reminders promos" },
  ]},
  { group: "Games", items: [
    { id: "Casino", icon: "🎲", label: "Casino", permission: "manage_casino", keywords: "blackjack poker rbj ace chip" },
    { id: "Mining", icon: "⛏️", label: "Mining", permission: "manage_mining", keywords: "ores pickaxes drops" },
    { id: "Fishing", icon: "🎣", label: "Fishing", permission: "manage_fishing", keywords: "fish rods catches" },
    { id: "Quests & Missions", icon: "📜", label: "Quests & Missions", permission: "manage_rewards", keywords: "daily weekly challenges" },
    { id: "Events", icon: "🎉", label: "Events", permission: "manage_events", keywords: "event points schedule votes" },
  ]},
  { group: "Players", items: [
    { id: "Players", icon: "👤", label: "Players", permission: "manage_players", keywords: "player search profile inventory moderation" },
    { id: "Economy & Rewards", icon: "💰", label: "Economy & Rewards", permission: "manage_economy", keywords: "vip titles badges rewards shop coins" },
    { id: "Leaderboards", icon: "🏆", label: "Leaderboards", permission: "view_logs", keywords: "rankings top public diagnostics" },
  ]},
  { group: "Safety", items: [
    { id: "Security", icon: "🛡️", label: "Security", permission: "manage_moderation", keywords: "moderation warnings mutes reports" },
    { id: "Staff", icon: "👥", label: "Staff", permission: "manage_staff", ownerOnly: true, keywords: "users permissions roles" },
    { id: "Permissions Audit", page: "System", tab: "Permissions Audit", icon: "🔐", label: "Permissions Audit", ownerOnly: true, keywords: "permission route audit" },
  ]},
  { group: "System", items: [
    { id: "System Overview", page: "System", tab: "Health", icon: "⚙️", label: "System Overview", ownerOnly: true, keywords: "health system overview" },
    { id: "Maintenance Center", page: "System", tab: "Maintenance Center", icon: "🧰", label: "Maintenance Center", ownerOnly: true, keywords: "backup restore database cleanup" },
    { id: "Settings Audit", page: "System", tab: "Settings Audit", icon: "🧭", label: "Settings Audit", ownerOnly: true, keywords: "settings command audit mapping" },
    { id: "QA Audit", page: "System", tab: "QA Audit", icon: "✅", label: "QA Audit", ownerOnly: true, keywords: "qa broken routes buttons" },
    { id: "E2E Audit", page: "System", tab: "E2E Audit", icon: "🧪", label: "E2E Audit", ownerOnly: true, keywords: "end to end stabilization audit smoke" },
    { id: "Logs / Errors", page: "System", tab: "Logs", icon: "📋", label: "Logs / Errors", permission: "view_logs", keywords: "logs audit command errors" },
  ]},
];
const OWNER_NAV = OWNER_NAV_GROUPS.flatMap((section) => section.items.map((item) => ({ ...item, group: section.group })));

/* ── Staff Nav ───────────────────────────────────────── */
const STAFF_NAV_GROUPS = [
  { group: "Dashboard", items: [
    { id: "Staff Home", icon: "📊", label: "Staff Home", permission: "view_dashboard" },
  ]},
  { group: "Tools", items: [
    { id: "Radio Queue", icon: "🎵", label: "Radio Queue", permission: "manage_radio" },
    { id: "Players", icon: "👤", label: "Players", permission: "manage_players" },
    { id: "Events", icon: "🎉", label: "Events", permission: "manage_events" },
    { id: "Room Tools", icon: "🔧", label: "Room Tools", permission: "manage_room" },
    { id: "Moderation", icon: "🛡️", label: "Moderation", permission: "manage_moderation" },
  ]},
  { group: "Admin", items: [
    { id: "Logs", icon: "📋", label: "Logs", permission: "view_logs" },
  ]},
];
const STAFF_NAV = STAFF_NAV_GROUPS.flatMap((section) => section.items.map((item) => ({ ...item, group: section.group })));

/* ── Page Tabs ───────────────────────────────────────── */
const BOT_TABS = [
  { id: "Bot Status", api: "/api/bot-control" },
  { id: "Bot Config", api: "/api/bot-config" },
  { id: "Bot Settings", api: "/api/settings" },
  { id: "Bot Spawns", api: "/api/bot-spawns" },
  { id: "Bot Audit", api: "/api/bot-audit" },
  { id: "Queued Commands", api: "/api/bot-command-queue" },
  { id: "Advanced Debug", api: "/api/bot-audit" },
];
const PLAYER_TABS = [
  { id: "Search", api: null },
  { id: "Economy", api: null },
  { id: "Inventory", api: null },
  { id: "Mining", api: null },
  { id: "Fishing", api: null },
  { id: "Titles & Badges", api: "/api/titles" },
  { id: "Moderation", api: null },
  { id: "Logs", api: null },
];
const SECURITY_TABS = [
  { id: "Overview", api: "/api/security" },
  { id: "Reports", api: "/api/security/reports" },
  { id: "Warnings", api: "/api/security/warnings" },
  { id: "Mutes", api: "/api/security/mutes" },
  { id: "Bans / Jail", api: "/api/security" },
  { id: "Player Lookup", api: null },
  { id: "Security Bot", api: "/api/security" },
  { id: "Logs", api: "/api/security/logs" },
  { id: "Advanced", api: "/api/security" },
];
const LEADERBOARD_TABS = [
  { id: "Overview", api: "/api/leaderboards" },
  { id: "Richest", api: "/api/leaderboards" },
  { id: "XP / Level", api: "/api/leaderboards" },
  { id: "Mining", api: "/api/leaderboards" },
  { id: "Fishing", api: "/api/leaderboards" },
  { id: "Casino", api: "/api/leaderboards" },
  { id: "Poker", api: "/api/leaderboards" },
  { id: "Blackjack", api: "/api/leaderboards" },
  { id: "Radio", api: "/api/leaderboards" },
  { id: "Events", api: "/api/leaderboards" },
  { id: "Social / Reputation", api: "/api/leaderboards" },
  { id: "Gold / Tips", api: "/api/leaderboards" },
  { id: "Streaks", api: "/api/leaderboards" },
  { id: "Profiles", api: "/api/leaderboards" },
  { id: "Diagnostics", api: "/api/leaderboards" },
];
const ROOM_TABS = [
  { id: "Overview", api: "/api/room-control" },
  { id: "Room Settings", api: "/api/room-control" },
  { id: "Welcome", api: "/api/room-control" },
  { id: "Announcements", api: "/api/room-control" },
  { id: "Events", api: "/api/events" },
  { id: "Event Rewards", api: "/api/events" },
  { id: "Rules / Info", api: "/api/room-control" },
  { id: "Logs", api: "/api/room-control" },
  { id: "Advanced", api: "/api/room-control" },
];
const RADIO_TABS = [
  { id: "Overview", api: "/api/radio/overview" },
  { id: "Queue", api: "/api/radio/queue" },
  { id: "Requests", api: "/api/radio" },
  { id: "Now Playing", api: "/api/radio" },
  { id: "Recently Played", api: "/api/radio/recent" },
  { id: "Blocklist", api: "/api/radio/blocklist" },
  { id: "Rewards / Stats", api: "/api/radio/stats" },
  { id: "Local Library", api: "/api/radio" },
  { id: "AzuraCast / Stream", api: "/api/radio" },
  { id: "Logs", api: "/api/radio/logs" },
  { id: "Advanced", api: "/api/radio" },
];
const ECONOMY_TABS = [
  { id: "Overview", api: "/api/economy/overview" },
  { id: "VIP", api: "/api/vip" },
  { id: "Titles", api: "/api/titles" },
  { id: "Badges", api: "/api/badges/catalog" },
  { id: "Rewards", api: "/api/rewards" },
  { id: "Shop", api: "/api/shop" },
  { id: "Quests", api: "/api/quests" },
  { id: "Player Grants", api: "/api/rewards" },
  { id: "Logs", api: "/api/rewards/logs" },
  { id: "Advanced", api: "/api/rewards" },
];
const MINING_TABS = [
  { id: "Overview", api: "/api/mining" },
  { id: "Settings", api: "/api/mining-settings" },
  { id: "Pickaxes", api: "/api/mining/pickaxes" },
  { id: "Ores", api: "/api/mining/ores" },
  { id: "Drop Chances", api: "/api/mining/drop-weights" },
  { id: "Player Mining", api: "/api/mining/players" },
  { id: "Inventory", api: "/api/mining/inventory" },
  { id: "Logs", api: "/api/mining/logs" },
  { id: "Advanced", api: "/api/mining" },
];
const FISHING_TABS = [
  { id: "Overview", api: "/api/fishing" },
  { id: "Settings", api: "/api/fishing-settings" },
  { id: "Rods", api: "/api/fishing/rods" },
  { id: "Fish Catalog", api: "/api/fishing/fish" },
  { id: "Catch Chances", api: "/api/fishing/drop-weights" },
  { id: "Player Fishing", api: "/api/fishing/players" },
  { id: "Inventory", api: "/api/fishing/inventory" },
  { id: "Logs", api: "/api/fishing/logs" },
  { id: "Advanced", api: "/api/fishing" },
];
const QUEST_TABS = [
  { id: "Overview", api: "/api/quests" },
  { id: "Quest Catalog", api: "/api/quests" },
  { id: "Daily Quests", api: "/api/quests" },
  { id: "Weekly Quests", api: "/api/quests" },
  { id: "Event Quests", api: "/api/quests" },
  { id: "Player Progress", api: "/api/quests" },
  { id: "Rewards", api: "/api/quests" },
  { id: "Logs", api: "/api/quests" },
  { id: "Advanced", api: "/api/quests" },
];
const AUTOMATION_TABS = [
  { id: "Overview", api: "/api/automation" },
  { id: "Scheduled Announcements", api: "/api/automation/announcements" },
  { id: "Rotating Announcements", api: "/api/automation/rotating" },
  { id: "Event Reminders", api: "/api/automation" },
  { id: "Promo Messages", api: "/api/automation" },
  { id: "Staff Alerts", api: "/api/automation" },
  { id: "Delivery Queue", api: "/api/automation" },
  { id: "Logs", api: "/api/automation" },
  { id: "Advanced", api: "/api/automation" },
];
const OPERATIONS_TABS = [
  { id: "Overview", api: "/api/operations" },
  { id: "Bots", api: "/api/operations/bots" },
  { id: "Room", api: "/api/operations/room" },
  { id: "Radio", api: "/api/operations/radio" },
  { id: "Command Queue", api: "/api/operations/queue" },
  { id: "Database", api: "/api/operations/database" },
  { id: "Errors", api: "/api/operations/errors" },
  { id: "Alerts", api: "/api/operations/alerts" },
  { id: "Logs", api: "/api/operations/logs" },
  { id: "Advanced", api: "/api/operations" },
];
const EMOTE_TABS = [
  { id: "Overview", api: "/api/emotes/overview" },
  { id: "Emote Registry", api: "/api/emotes/registry" },
  { id: "Bot Emotes", api: "/api/emotes/bot-status" },
  { id: "Custom Packs", api: "/api/emotes/custom-packs" },
  { id: "Dancefloor", api: "/api/emotes/dancefloor" },
  { id: "Sync", api: "/api/emotes/sync" },
  { id: "Social / Hearts", api: "/api/emotes/social" },
  { id: "Logs", api: "/api/emotes/logs" },
  { id: "Advanced", api: "/api/emotes/overview" },
];
const SYSTEM_TABS = [
  { id: "Health", api: "/api/healthz" },
  { id: "Logs", api: null },
  { id: "Public Settings", api: "/api/public-settings" },
  { id: "Settings Audit", api: "/api/settings-audit" },
  { id: "Permissions Audit", api: "/api/permissions/audit" },
  { id: "QA Audit", api: "/api/qa/audit" },
  { id: "E2E Audit", api: "/api/e2e/audit" },
  { id: "Maintenance Center", api: "/api/maintenance/overview" },
  { id: "Database", api: "/api/db/inspect" },
  { id: "Emergency", api: "/api/settings" },
  { id: "Missing/Future Controls", api: null },
];
const MAINTENANCE_TABS = [
  "Overview",
  "Backups",
  "Restore",
  "Database Health",
  "Cleanup Preview",
  "Runtime Health",
  "Logs",
  "Advanced",
];
const MAINTENANCE_API = {
  "Overview": "/api/maintenance/overview",
  "Backups": "/api/maintenance/backups",
  "Restore": "/api/maintenance/backups",
  "Database Health": "/api/maintenance/db-health",
  "Cleanup Preview": "/api/maintenance/cleanup-preview",
  "Runtime Health": "/api/maintenance/runtime-health",
  "Logs": "/api/maintenance/logs",
  "Advanced": "/api/maintenance/advanced",
};
const TAB_REGISTRY = {
  "Operations Center": OPERATIONS_TABS,
  "Bots": BOT_TABS,
  "Radio": RADIO_TABS,
  "Players": PLAYER_TABS,
  "Security": SECURITY_TABS,
  "Moderation": SECURITY_TABS,
  "Leaderboards": LEADERBOARD_TABS,
  "Room & Content": ROOM_TABS,
  "Emotes": EMOTE_TABS,
  "Economy & Rewards": ECONOMY_TABS,
  "Mining": MINING_TABS,
  "Fishing": FISHING_TABS,
  "Quests & Missions": QUEST_TABS,
  "Automation Center": AUTOMATION_TABS,
  "System": SYSTEM_TABS,
};
const PAGE_TABS = Object.fromEntries(
  Object.entries(TAB_REGISTRY).map(([page, tabs]) => [page, tabs.map((tab) => tab.id)]),
);

/* ── Page Descriptions ───────────────────────────────── */
const PAGE_DESC = {
  "Command Center":    "All systems at a glance — quick actions and health overview",
  "Operations Center": "Live bot, room, radio, database, queue and alert health",
  "Radio":             "DJ DUDU queue, request gate, stream status and radio maintenance",
  "Bots":              "Bot status, configuration and control",
  "Players":           "Player search, titles, moderation",
  "Casino":            "Blackjack, Poker and verified casino settings",
  "Security":          "Moderation, reports, mutes and KeanuShield controls",
  "Leaderboards":      "Public rankings, source diagnostics and leaderboard health",
  "Room & Content":    "Room settings, radio, events and announcements",
  "Emotes":            "DJ DUDU emote registry, bot loops, dancefloor, sync and social controls",
  "Mining":            "Mining catalog, settings, drop chances and logs",
  "Fishing":           "Fishing catalog, settings, catch chances and logs",
  "Quests & Missions": "Daily, weekly, event and player mission progress",
  "Automation Center": "Scheduled announcements, promos, reminders and delivery queues",
  "Economy & Rewards": "Coins, VIP, shop, titles, badges, quests and grants",
  "Staff":             "Dashboard users, permissions and bot roles",
  "System":            "Health monitoring, logs and emergency controls",
  "System Overview":   "Core dashboard service health and known DB tables",
  "Maintenance Center":"Backups, restore previews, DB health and cleanup previews",
  "Settings Audit":    "Verified command-to-dashboard settings source mapping",
  "Permissions Audit": "Route protection, owner-only checks and permission coverage",
  "QA Audit":          "Static dashboard route, renderer and button audit",
  "E2E Audit":         "End-to-end route, button, permission and safety stabilization audit",
  "Logs / Errors":     "Audit logs, command errors and admin action history",
  "Staff Home":        "Room health and pending attention items",
  "Radio Queue":       "DJ queue management and radio controls",
  "Moderation":        "Warnings, mutes, reports and player moderation lookup",
  "Events":            "Event definitions, schedules, rewards and queued event controls",
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

function settingInput(name, label, value) {
  return `<div class="field">
    <label class="field-label">${esc(label)}</label>
    <input type="number" name="${esc(name)}" value="${esc(value ?? "")}" />
  </div>`;
}

/* ── API Map ─────────────────────────────────────────── */
function pageApi(page, tab) {
  if (tab && TAB_REGISTRY[page]) {
    const tabDef = TAB_REGISTRY[page].find((item) => item.id === tab);
    if (tabDef) return tabDef.api;
  }
  return ({
    "Command Center":                     "/api/overview",
    "Operations Center":                  "/api/operations",
    "Casino":                             "/api/casino",
    "Quests & Missions":                  "/api/quests",
    "Automation Center":                  "/api/automation",
    "Staff":                              "/api/staff",
    "Staff Home":                         "/api/overview",
    "Radio Queue":                        "/api/radio",
    "Moderation":                         "/api/security",
    "Leaderboards":                       "/api/leaderboards",
    "Events":                             "/api/events",
    "Room Tools":                         "/api/room-control",
    "Emotes":                             "/api/emotes/overview",
    "Logs":                               null,
  })[page] ?? null;
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
  logs: { action_type: "", user: "", module: "", status: "", date: "", target: "", offset: 0 },
  settingsAudit: { status: "all", module: "", page: "" },
  maintenanceTab: "Overview",
  navSearch: "",
  howToPlayTab: "Quick Start",
  manualRarity: { Mining: "common", Fishing: "common" },
  publicRankingTab: "Overview",
  questSearch: "",
  securityPlayer: null,
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

function navKey(item) {
  return item.id;
}

function navTarget(item) {
  return { page: item.page || item.id, tab: item.tab || null };
}

function visibleNavItems(nav) {
  return nav.filter((item) => {
    if (item.hidden) return false;
    if (item.ownerOnly && state.user?.role !== "owner") return false;
    if (state.user?.role === "owner") return true;
    return !item.permission || can(item.permission);
  });
}

function activeNavItem() {
  const nav = visibleNavItems(state.user?.role === "owner" ? OWNER_NAV : STAFF_NAV);
  return nav.find((item) => {
    const target = navTarget(item);
    if (target.page !== state.adminPage) return false;
    return target.tab ? state.adminTab[target.page] === target.tab : true;
  }) || nav.find((item) => navTarget(item).page === state.adminPage) || nav[0] || null;
}

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

function notConnectedCard(title, rows = []) {
  return `<div class="card">
    <div class="card-header">
      <h2>${esc(title)}</h2>
      <span class="pill warn">Not connected yet</span>
    </div>
    <div class="notice">This tab is registered in navigation, but normal working controls are hidden until exact endpoints and DB sources are verified.</div>
  </div>
  ${futureControls(rows)}`;
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
    casino: "/api/public/casino", mining: "/api/public/mining",
    fishing: "/api/public/fishing",
    quests: "/api/public/quests",
    events: "/api/public/events", rankings: "/api/public/rankings",
    howtoplay: "/api/public/how-to-play", roominfo: "/api/public/room-info",
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
  const nav = visibleNavItems(role === "owner" ? OWNER_NAV : STAFF_NAV);
  const pageIds = new Set(nav.map((n) => navTarget(n).page));
  if (!pageIds.has(state.adminPage)) {
    const matchedVirtual = nav.find((n) => n.id === state.adminPage);
    const target = navTarget(matchedVirtual || nav[0] || { id: role === "owner" ? "Command Center" : "Staff Home" });
    state.adminPage = target.page;
    if (target.tab) state.adminTab[target.page] = target.tab;
  }

  const page = state.adminPage;
  const tabs = PAGE_TABS[page];
  if (tabs && (!state.adminTab[page] || !tabs.includes(state.adminTab[page])))
    state.adminTab[page] = tabs[0];

  const tab = state.adminTab[page] || null;
  const url = page === "Logs" || (page === "System" && tab === "Logs")
    ? logsUrl() : pageApi(page, tab);
  location.hash = encodeURIComponent(activeNavItem()?.id || page);

  if (!url) { state.data = {}; render(); return; }
  try { state.data = await api(url); state.error = ""; }
  catch (err) { state.data = null; state.error = err.message; }
  render();
}

async function switchTab(page, tab) {
  state.adminTab[page] = tab;
  if (page === "System" && tab === "Maintenance Center") state.maintenanceTab = "Overview";
  const url = (page === "System" && tab === "Logs") || page === "Logs"
    ? logsUrl() : pageApi(page, tab);
  if (!url) { state.data = {}; render(); return; }
  try { state.data = await api(url); state.error = ""; }
  catch (err) { state.data = null; state.error = err.message; }
  render();
}

async function loadMaintenanceTab(tab = state.maintenanceTab || "Overview") {
  state.maintenanceTab = tab;
  const url = MAINTENANCE_API[tab] || MAINTENANCE_API.Overview;
  try { state.data = await api(url); state.error = ""; }
  catch (err) { state.data = null; state.error = err.message; }
  render();
}

function logsUrl() {
  const p = new URLSearchParams();
  for (const k of ["action_type", "user", "module", "status", "date", "target"]) if (state.logs[k]) p.set(k, state.logs[k]);
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
    const nav = visibleNavItems(state.user.role === "owner" ? OWNER_NAV : STAFF_NAV);
    const pageIds = new Set(nav.map((n) => navTarget(n).page));
    if (!pageIds.has(state.adminPage)) {
      const matchedVirtual = nav.find((n) => n.id === state.adminPage);
      const target = navTarget(matchedVirtual || nav[0] || { id: state.user.role === "owner" ? "Command Center" : "Staff Home" });
      state.adminPage = target.page;
      if (target.tab) state.adminTab[target.page] = target.tab;
    }
    await loadAdmin();
  } catch { await loadPublic(); }
  setInterval(() => {
    if (state.user) {
      const auto = state.user.role === "owner" ? ["Command Center", "Operations Center"] : ["Staff Home", "Radio Queue"];
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
    case "casino":    return renderPublicCasino(d);
    case "mining":    return renderPublicMining(d);
    case "fishing":   return renderPublicFishing(d);
    case "quests":    return renderPublicQuests(d);
    case "events":    return renderPublicEvents(d);
    case "rankings":  return renderPublicRankings(d);
    case "roominfo":  return renderPublicRoomInfo(d);
    default:          return renderPublicHome(d);
  }
}

function publicFmt(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString() : String(v);
}
function publicCoins(v) { return v === null || v === undefined || v === "" ? "—" : `${publicFmt(v)} coins`; }
function publicLbs(v) { return v === null || v === undefined || v === "" ? "—" : `${publicFmt(v)} lbs`; }
function publicPercent(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  return Number.isFinite(n) ? `${n.toFixed(n < 1 ? 4 : 2)}%` : String(v);
}
function publicCommandChips(commands) {
  return `<div class="chip-row pub-command-chips">${commands.map((cmd) => `<code class="manual-command">${esc(cmd)}</code>`).join("")}</div>`;
}
function publicSmallTable(rows, cols, empty = "No data yet.") {
  const list = Array.isArray(rows) ? rows : [];
  if (!list.length) return `<div class="pub-empty compact">${esc(empty)}</div>`;
  return `<div class="table-scroll"><table><thead><tr>${cols.map((c) => `<th>${esc(c.label)}</th>`).join("")}</tr></thead><tbody>
    ${list.map((r) => `<tr>${cols.map((c) => `<td>${c.html ? c.html(r) : esc(c.render ? c.render(r) : r[c.key] ?? "—")}</td>`).join("")}</tr>`).join("")}
  </tbody></table></div>`;
}
function publicMetricGrid(rows) {
  return `<div class="manual-info-grid">${rows.map(([label, value]) => `<div><span>${esc(label)}</span><strong>${esc(publicFmt(value))}</strong></div>`).join("")}</div>`;
}
function publicMiniLeaders(rows, { title = "Leaders", name = (r) => r.username || r.name || "Unknown Player", value = (r) => r.value ?? r.points ?? r.balance ?? "", detail = null, empty = "No data yet." } = {}) {
  const list = (rows || []).slice(0, 5);
  return `<div class="pub-mini-leaders">
    ${list.length ? list.map((r, i) => `<div class="pub-lb-row rich">
      <span class="pub-lb-rank ${i < 3 ? "top" + i : ""}">${["🥇","🥈","🥉"][i] || i + 1}</span>
      <span class="pub-lb-main">
        <span class="pub-lb-name leaderboard-title-marquee" title="${esc(name(r))}"><span>${esc(name(r))}</span></span>
        ${detail ? `<span class="pub-lb-detail">${esc(detail(r) || "")}</span>` : ""}
      </span>
      <span class="pub-lb-val">${esc(String(value(r) ?? ""))}</span>
    </div>`).join("") : `<div class="pub-empty compact">${esc(empty)}</div>`}
  </div>`;
}

function renderPublicHome(d) {
  const np = d.now_playing;
  const event = d.current_event;
  const highlights = d.highlights || {};
  return `
    <div class="pub-hero">
      <div class="pub-hero-glow"></div>
      <div class="pub-hero-content">
        <div class="pub-hero-badge">🎵 Live Now · Neon Lounge</div>
        <h1 class="pub-hero-title">ChillTopia</h1>
        <p class="pub-hero-sub">A relaxed VIP game room for music requests, casino nights, mining, fishing, events, and rewards.</p>
        <div class="pub-hero-status">
          <span class="pill ok">Radio Live</span>
          <span class="pill info">Games Open</span>
          <span class="pill def">Public Read Only</span>
        </div>
        <div class="pub-hero-now">
          ${np ? `<span class="pub-now-label">Now Playing</span>
          <span class="pub-now-song">${esc(np.title || "Auto DJ")}</span>
          ${np.artist ? `<span class="pub-now-artist">${esc(np.artist)}</span>` : ""}`
          : `<span class="pub-now-label">Auto DJ is in the house 🎧</span>`}
        </div>
        <div class="pub-quick-links">
          <button class="btn primary large" data-pub-page="radio">📻 Listen to Radio</button>
          <button class="btn ghost large" data-pub-page="howtoplay">📖 How to Play</button>
          <button class="btn cyan large" data-pub-page="rankings">🏆 View Rankings</button>
          <a class="btn ghost large" href="${esc(d.join_url || "#")}" ${d.join_url ? `target="_blank" rel="noopener"` : ""}>🚪 Join on Highrise</a>
        </div>
      </div>
    </div>
    <div class="pub-section-title"><h2>Live Room Snapshot</h2><p>Public room activity at a glance.</p></div>
    <div class="pub-stats-row">
      ${pubStatCard("🤖", "Bots Online", `${d.online_bots ?? 0}/${d.total_bots ?? 0}`, "Active bots running")}
      ${pubStatCard("👥", "In Room", d.room_users ?? 0, "Players hanging out")}
      ${pubStatCard("🎵", "Queue", d.queue_count ?? 0, "Songs up next")}
      ${pubStatCard("✨", "Vibe", d.vibe || "Chill", "Current room energy")}
      ${pubStatCard("🎉", "Current Event", event?.name || "None", event?.active ? "Event is active" : "No active event")}
    </div>
    <div class="pub-section-title"><h2>Featured Systems</h2><p>Pick your lane: music, games, progression, or rewards.</p></div>
    <div class="pub-system-grid">
      ${[
        ["📻", "DJ DUDU Radio", "Request music with !play and climb the requester boards.", "radio"],
        ["🎲", "Casino", "Play Blackjack with AceSinatra or Poker with ChipSoprano.", "casino"],
        ["⛏️", "Mining", "Mine ores, chase rare finds, and earn rewards.", "mining"],
        ["🎣", "Fishing", "Catch fish, hunt big weights, and build your profile.", "fishing"],
        ["📜", "Quests", "Complete daily and weekly missions for extra rewards.", "quests"],
        ["🎉", "Events", "Join room events for points, wins, and prizes.", "events"],
        ["⭐", "Rewards / VIP", "Earn coins, titles, badges, and VIP perks.", "roominfo"],
      ].map(([icon, title, text, page]) => `<button class="card pub-system-card" data-pub-page="${page}"><span>${icon}</span><strong>${esc(title)}</strong><em>${esc(text)}</em></button>`).join("")}
    </div>
    <div class="pub-section-title"><h2>Recent Highlights</h2><p>Fresh public-safe moments from the room.</p></div>
    <div class="pub-highlight-grid">
      ${[
        ["🎵", "Top Song", highlights.top_song?.title, highlights.top_song?.detail],
        ["🎣", "Big Fish", highlights.big_fish?.title, highlights.big_fish?.detail],
        ["⛏️", "Big Ore", highlights.big_ore?.title, highlights.big_ore?.detail],
        ["🎲", "Casino Winner", highlights.casino_winner?.title, highlights.casino_winner?.detail],
        ["🎉", "Event Leader", highlights.event_winner?.title, highlights.event_winner?.detail],
      ].filter(([, , title]) => title).map(([icon, label, title, detail]) => `<div class="card pub-highlight-card"><span>${icon}</span><small>${esc(label)}</small><strong>${esc(title)}</strong>${detail ? `<em>${esc(detail)}</em>` : ""}</div>`).join("") || `<div class="pub-empty compact">Highlights will appear as players create them.</div>`}
    </div>
    <div class="card">
      <h3>Quick Commands</h3>
      ${publicCommandChips(["!play", "!bj", "!join", "!mine", "!fish", "!daily", "!profile", "!top"])}
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
  const boards = d.leaderboards || {};
  const cleanSong = (v) => String(v || "Unknown Track")
    .replace(/\s*[\[(](official\s+(music\s+)?video|official\s+audio|lyrics?|lyric\s+video)[\])]\s*/ig, " ")
    .replace(/\s*\b(official\s+(music\s+)?video|official\s+audio|lyric\s+video|lyrics?|4k|hd)\b\s*/ig, " ")
    .replace(/\s+/g, " ")
    .trim() || "Unknown Track";
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
          <p>Type <strong>!play [song name or artist]</strong> in the Highrise room to add your song to the queue. Request one song at a time; queue limits and cooldowns may apply.</p>
          ${d.playlist_urls_disabled ? `<p>Playlist URLs are disabled for normal public requests.</p>` : ""}
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
    <div class="pub-section-title"><h2>Radio Leaderboards</h2><p>Requester and song stats from the live radio database.</p></div>
    <div class="pub-rankings-grid">
      <div class="card"><h3>Top Requesters</h3>${publicMiniLeaders(boards.top_requesters || [], { value: (r) => `${publicFmt(r.requests)} requests` })}</div>
      <div class="card"><h3>Top Liked Songs</h3>${publicMiniLeaders(boards.top_liked_songs || [], {
        name: (r) => cleanSong(r.title || r.name),
        value: (r) => `${publicFmt(r.likes)} likes`,
        detail: (r) => r.requester ? `requested by ${r.requester}` : "",
      })}</div>
      <div class="card"><h3>Top Disliked Songs</h3>${publicMiniLeaders(boards.top_disliked_songs || [], {
        name: (r) => cleanSong(r.title || r.name),
        value: (r) => `${publicFmt(r.dislikes)} dislikes`,
        detail: (r) => r.requester ? `requested by ${r.requester}` : "",
      })}</div>
    </div>
  `;
}

function renderPublicHowToPlay() {
  const d = state.data || {};
  const tabs = ["Quick Start", "Radio", "Casino", "Mining", "Fishing", "Economy", "Emotes & Dancefloor", "Events", "VIP", "Commands A-Z", "FAQ"];
  if (!tabs.includes(state.howToPlayTab)) state.howToPlayTab = tabs[0];
  const tab = state.howToPlayTab;
  const commandChip = (cmd) => `<code class="manual-command">${esc(cmd)}</code>`;
  const commandList = (items) => `<div class="manual-command-grid">${items.map((item) => `<div class="manual-command-row"><code>${esc(item.command || item[0])}</code><span>${esc(item.description || item[1])}${item.availability ? ` <em>${esc(item.availability)}</em>` : ""}</span></div>`).join("")}</div>`;
  const infoGrid = (rows) => `<div class="manual-info-grid">${rows.map(([label, value]) => `<div><span>${esc(label)}</span><strong>${esc(value ?? "—")}</strong></div>`).join("")}</div>`;
  const smallTable = (rows, cols, empty = "No live data connected yet.") => rows?.length ? `<div class="table-scroll"><table><thead><tr>${cols.map((c) => `<th>${esc(c.label)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${cols.map((c) => `<td>${esc(c.render ? c.render(r) : r[c.key] ?? "—")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : `<div class="notice">${esc(empty)}</div>`;
  const bj = d.casino?.blackjack_settings || {};
  const poker = d.casino?.poker_settings || {};
  const mining = d.mining || {};
  const fishing = d.fishing || {};
  const eventSettings = Object.fromEntries((d.events?.current_settings || []).map((row) => [row.key, row.value]));
  const activeEvent = eventSettings.event_active === "1" ? eventSettings.event_name : (eventSettings.active_event || "");
  const commandsByCategory = (category) => (d.commands || []).filter((cmd) => cmd.category === category);
  const rarityLabel = (rarity) => String(rarity || "common").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  const rarityChip = (rarity, label = rarityLabel(rarity)) => `<span class="rarity-chip rarity-${esc(String(rarity || "common").toLowerCase())}">${esc(label)}</span>`;
  const rarityTabs = (section, order = []) => {
    const current = (state.manualRarity?.[section] || order[0] || "common").toLowerCase();
    return `<div class="rarity-tabs">${order.map((rarity) => `<button class="rarity-tab ${rarity === current ? "active" : ""}" data-manual-rarity="${esc(rarity)}" data-manual-section="${esc(section)}">${rarityChip(rarity)}</button>`).join("")}</div>`;
  };
  const manualCommandCard = (title, commands) => `<div class="card manual-feature-card"><h3>${esc(title)}</h3>${commandList(commands || [])}</div>`;
  const rarePreview = (rows, kind) => rows?.length ? `<div class="manual-preview-list">${rows.map((row) => `<div class="manual-preview-row">
    <div><strong>${esc(row.name || row.ore || row.fish || "Unknown")}</strong><span>${rarityChip(row.rarity, row.rarity_label || rarityLabel(row.rarity))}</span></div>
    <div class="manual-preview-meta">
      <span>${esc(row.chance_label || "Unknown odds")}</span>
      ${kind === "fish" ? `<span>${esc(publicLbs(row.max_weight))} max</span>` : ""}
      <span>${esc(publicCoins(row.value ?? row.base_value))}</span>
    </div>
  </div>`).join("")}</div>` : `<div class="pub-empty compact">Rare preview will appear when catalog odds are available.</div>`;
  const catalogCards = (rows, kind) => rows?.length ? `<div class="manual-catalog-grid">${rows.map((row) => `<div class="manual-catalog-card">
    <div class="manual-catalog-head"><strong>${esc(row.name || "Unknown")}</strong>${rarityChip(row.rarity, row.rarity_label || rarityLabel(row.rarity))}</div>
    <div class="manual-catalog-meta">
      <span><b>${kind === "fish" ? "Value" : "Value"}</b>${esc(publicCoins(row.value ?? row.base_value))}</span>
      ${kind === "fish" ? `<span><b>Weight</b>${esc(`${publicLbs(row.min_weight)}–${publicLbs(row.max_weight)}`)}</span>` : ""}
      ${kind === "ore" && row.weight ? `<span><b>Weight</b>${esc(publicLbs(row.weight))}</span>` : ""}
      <span><b>Chance</b>${esc(row.chance_label || "Unknown")}</span>
      <span><b>Event Only</b>${row.event_only ? "Yes" : "No"}</span>
    </div>
  </div>`).join("")}</div>` : `<div class="pub-empty compact">No catalog entries for this rarity yet.</div>`;
  const miningManual = () => {
    const order = mining.rarity_order || ["common", "uncommon", "rare", "epic", "legendary", "mythic", "exotic", "prismatic"];
    const current = state.manualRarity.Mining || order[0];
    const rows = mining.ores_by_rarity?.[current] || [];
    return `
      <div class="manual-game-hero card">
        <div class="manual-game-icon">⛏️</div>
        <div><h3>Mining</h3><p>Mine ores, discover rare finds, earn coins, gain XP, and climb the mining leaderboards.</p></div>
      </div>
      <div class="pub-grid2">
        <div class="card manual-feature-card"><h3>Basics</h3><ul class="manual-list">${(mining.basics || []).map((line) => `<li>${esc(line)}</li>`).join("")}</ul></div>
        ${manualCommandCard("Mining Commands", mining.commands || commandsByCategory("Mining"))}
      </div>
      <div class="card manual-feature-card"><h3>Rarity Guide</h3><div class="rarity-guide">${order.map((rarity) => rarityChip(rarity)).join("")}</div><p class="manual-note">Rarer ores usually have lower drop weight and higher value. Special events may change live outcomes.</p></div>
      <div class="card manual-feature-card"><h3>Ore Catalog by Rarity</h3>${rarityTabs("Mining", order)}${catalogCards(rows, "ore")}</div>
      <div class="pub-grid2">
        <div class="card manual-feature-card"><h3>Rare Drop Preview</h3>${rarePreview(mining.rare_preview || mining.rarest || [], "ore")}</div>
        <div class="card manual-feature-card"><h3>What Affects Odds?</h3><ul class="manual-list">${(mining.odds_notes || []).map((line) => `<li>${esc(line)}</li>`).join("")}</ul></div>
      </div>
      <div class="pub-grid2">
        <div class="card manual-feature-card"><h3>Progression / Tools</h3><p class="manual-copy">Pickaxes and tool levels affect mining progression when the active bot supports upgrades. Use in-room tool commands to check your current setup.</p>${smallTable(mining.tools || [], [{ key: "name", label: "Pickaxe" }, { key: "required_level", label: "Required Level" }, { key: "cooldown_seconds", label: "Cooldown" }], "Tool catalog is not public yet.")}</div>
        <div class="card manual-feature-card"><h3>Leaderboards & Help</h3><p class="manual-copy">Check Rankings for top miners, heaviest ores, valuable finds, and rare discoveries.</p>${publicCommandChips(["!topminers", "!orebook", "!mineprofile", "!minelb"])}</div>
      </div>`;
  };
  const fishingManual = () => {
    const order = fishing.rarity_order || ["common", "uncommon", "rare", "epic", "legendary", "mythic", "exotic"];
    const current = state.manualRarity.Fishing || order[0];
    const rows = fishing.fish_by_rarity?.[current] || [];
    return `
      <div class="manual-game-hero card">
        <div class="manual-game-icon">🎣</div>
        <div><h3>Fishing</h3><p>Catch fish, find rare species, earn coins, gain XP, and climb the fishing leaderboards.</p></div>
      </div>
      <div class="pub-grid2">
        <div class="card manual-feature-card"><h3>Basics</h3><ul class="manual-list">${(fishing.basics || []).map((line) => `<li>${esc(line)}</li>`).join("")}</ul></div>
        ${manualCommandCard("Fishing Commands", fishing.commands || commandsByCategory("Fishing"))}
      </div>
      <div class="card manual-feature-card"><h3>Rarity Guide</h3><div class="rarity-guide">${order.map((rarity) => rarityChip(rarity)).join("")}</div><p class="manual-note">Rarer fish usually have lower catch weight and stronger leaderboard value. Special events may change live outcomes.</p></div>
      <div class="card manual-feature-card"><h3>Fish Catalog by Rarity</h3>${rarityTabs("Fishing", order)}${catalogCards(rows, "fish")}</div>
      <div class="pub-grid2">
        <div class="card manual-feature-card"><h3>Rare Catch Preview</h3>${rarePreview(fishing.rare_preview || fishing.rarest || [], "fish")}</div>
        <div class="card manual-feature-card"><h3>What Affects Odds?</h3><ul class="manual-list">${(fishing.odds_notes || []).map((line) => `<li>${esc(line)}</li>`).join("")}</ul></div>
      </div>
      <div class="pub-grid2">
        <div class="card manual-feature-card"><h3>Progression / Rods</h3><p class="manual-copy">Rods, boosts, VIP bonuses, and events may improve fishing when the active bot enables them. Use in-room commands to check your current fishing setup.</p>${smallTable(fishing.rods || [], [{ key: "name", label: "Rod" }, { key: "required_level", label: "Required Level" }, { key: "luck_bonus", label: "Luck" }, { key: "speed_bonus", label: "Speed" }], "Rod catalog is not public yet.")}</div>
        <div class="card manual-feature-card"><h3>Leaderboards & Help</h3><p class="manual-copy">Check Rankings for top fishers, heaviest fish, valuable catches, and rare catches.</p>${publicCommandChips(["!topfishers", "!fishbook", "!fishhelp", "!topweightfish"])}</div>
      </div>`;
  };
  const panel = {
    "Quick Start": `
      <div class="manual-hero card">
        <h3>Welcome to ChillTopia</h3>
        <p>ChillTopia is a social room with live radio, games, mining, fishing, events, leaderboards, emotes, and coin progression. Most things start with a chat command in-room.</p>
        <div class="chip-row">
          ${["!play [song]", "!q", "!now", "!balance", "!daily", "!bj [amount]", "!poker", "!mine", "!fish", "!top", "!profile"].map(commandChip).join("")}
        </div>
      </div>
      <div class="pub-tutorial-grid">
        ${[
          ["🎵", "Request music", "Use !play with a song name or artist. DJ DUDU adds it to the queue if requests are open."],
          ["💰", "Earn coins", "Claim !daily, mine ores, catch fish, win games, join events, and appear on leaderboards."],
          ["🃏", "Play casino", "Use !bj [amount] for Blackjack or !join [amount] for Poker once a table is active."],
          ["⛏️", "Progress", "Mining and fishing give XP, rare finds, profile stats, and leaderboard progress."],
          ["🎉", "Join events", "Watch announcements and use !events or !event if available for current activity."],
          ["👤", "Check stats", "Use !profile, !top, and category leaderboards to track your progress."],
        ].map(([icon, title, text]) => `<div class="card pub-tutorial-card"><div class="pub-tutorial-icon">${icon}</div><h3>${esc(title)}</h3><p>${esc(text)}</p></div>`).join("")}
      </div>`,
    "Radio": `
      <div class="pub-grid2">
        <div class="card"><h3>How Music Requests Work</h3>
          <p class="manual-copy">Use ${commandChip("!play [song name or artist]")} in room chat. If requests are open, DJ DUDU searches the radio pipeline, prepares the track, and places it in queue.</p>
          ${infoGrid([["Requests", d.radio?.queue_open ? "Open" : "Closed"], ["Queue Size", d.radio?.queue_size ?? 0], ["Playlist URLs", d.radio?.playlist_urls_disabled ? "Disabled for normal requests" : "May be allowed"], ["Pipeline", "Local replay + YouTube when available"]])}
        </div>
        <div class="card"><h3>Radio Commands</h3>${commandList(commandsByCategory("Radio"))}<p class="manual-note">Normal players request with !play. !skip is staff/owner only.</p></div>
      </div>
      <div class="card"><h3>Recently Played</h3>${smallTable(d.radio?.recently_played || [], [{ key: "title", label: "Title" }, { key: "artist", label: "Artist" }, { key: "username", label: "Requester" }])}</div>`,
    "Casino": `
      <div class="pub-grid2">
        <div class="card"><h3>Blackjack — AceSinatra</h3>
          <p class="manual-copy">Objective: beat the dealer without busting over 21. Use ${commandChip("!bj [amount]")} to join, then ${commandChip("!hit")}, ${commandChip("!stand")}, ${commandChip("!double")}, or ${commandChip("!split")} when available.</p>
          ${infoGrid([["Min Bet", bj.min_bet], ["Max Bet", bj.max_bet], ["Players", bj.max_players], ["Turn Timer", `${bj.turn_timer ?? "—"} sec`], ["Decks", bj.decks], ["Shuffle", `${bj.shuffle_used_percent ?? "—"}% used`], ["Win Payout", bj.win_payout], ["Blackjack Payout", bj.blackjack_payout], ["Daily Win Limit", bj.daily_win_limit ?? "—"]])}
          <p class="manual-note">A push means a tie and usually returns your bet. Double doubles your bet for one final card. Split separates matching cards when the active table supports it.</p>
        </div>
        <div class="card"><h3>Poker — ChipSoprano</h3>
          <p class="manual-copy">Objective: Texas Hold'em. Use ${commandChip("!join [amount]")} to buy in, then act on your turn with ${commandChip("!check")}, ${commandChip("!call")}, ${commandChip("!raise [amount]")}, ${commandChip("!fold")}, or ${commandChip("!allin")}.</p>
          ${infoGrid([["Min Buy-In", poker.min_buyin], ["Max Buy-In", poker.max_buyin], ["Max Players", poker.max_players], ["Turn Timer", `${poker.turn_timer ?? "—"} sec`], ["Small Blind", poker.small_blind], ["Big Blind", poker.big_blind]])}
          <p class="manual-note">Buy-ins become your table stack. Blinds create the pot. Win chips by making the best hand or getting everyone else to fold.</p>
        </div>
      </div>`,
    "Mining": miningManual(),
    "Fishing": fishingManual(),
    "Economy": `
      <div class="pub-grid2">
        <div class="card"><h3>Coins, XP, and Levels</h3><p class="manual-copy">Coins power bets, progression, and room rewards. XP and levels track your activity across ChillTopia systems.</p>${commandList(commandsByCategory("Economy").concat(commandsByCategory("Gold / Tips")))}</div>
        <div class="card"><h3>Ways to Earn</h3><ul class="manual-list"><li>Claim ${commandChip("!daily")} streak rewards.</li><li>Mine ores with ${commandChip("!mine")}.</li><li>Catch fish with ${commandChip("!fish")}.</li><li>Win casino games.</li><li>Join events and earn event points.</li><li>Use gold/tip systems when available.</li></ul></div>
      </div>`,
    "Emotes & Dancefloor": `
      <div class="pub-grid2">
        <div class="card"><h3>Emotes</h3><p class="manual-copy">Use public emote commands when enabled. VIP and social emotes may unlock extra effects.</p>${commandList(commandsByCategory("Emotes"))}</div>
        <div class="card"><h3>Dancefloor & Sync</h3><p class="manual-copy">Dancefloor sequences and sync let players coordinate movement or emotes when DJ DUDU has those systems active. Staff-only controls are not listed here as normal player commands.</p><p class="manual-note">Try ${commandChip("!sync")}, ${commandChip("!syncstop")}, and ${commandChip("!syncstatus")} if sync is enabled.</p></div>
      </div>`,
    "Events": `
      <div class="pub-grid2">
        <div class="card"><h3>Current Event</h3>${activeEvent ? `<div class="pub-event-item"><div class="pub-event-name">${esc(activeEvent)}</div>${eventSettings.event_expires_at ? `<div class="muted text-sm">Ends ${esc(eventSettings.event_expires_at)}</div>` : ""}</div>` : `<div class="notice">No event is active right now.</div>`}<p class="manual-note">Use ${commandChip("!events")} or ${commandChip("!event")} if available.</p></div>
        <div class="card"><h3>Upcoming Events</h3>${smallTable(d.events?.scheduled || [], [{ key: "name", label: "Event" }, { key: "starts_at", label: "Starts" }, { key: "points", label: "Points" }])}</div>
      </div>`,
    "VIP": `
      <div class="pub-grid2">
        <div class="card"><h3>VIP Perks</h3><ul class="manual-list"><li>Priority radio or playlist features when enabled.</li><li>Possible bonus rewards or VIP daily perks.</li><li>Special badges/titles or social emote access.</li><li>Extra luck/duration bonuses in systems that support VIP.</li></ul></div>
        <div class="card"><h3>How to Get VIP</h3><p class="manual-copy">VIP access is handled by the room owner/staff. Ask a staff member in ChillTopia for the current requirements and perks.</p></div>
      </div>`,
    "Commands A-Z": `
      <div class="pub-grid2">
        ${["Radio", "Economy", "Gold / Tips", "Blackjack", "Poker", "Mining", "Fishing", "Emotes", "Events"].map((cat) => `<div class="card"><h3>${esc(cat)}</h3>${commandList(commandsByCategory(cat))}</div>`).join("")}
      </div>`,
    "FAQ": `
      <div class="pub-grid2">
        ${[
          ["How do I earn coins?", "Use !daily, mine, fish, join events, play casino games, and participate in supported reward systems."],
          ["How do I request music?", "Use !play [song name or artist]. Check !q or !queue to see what is coming next."],
          ["Why was my song skipped or blocked?", "It may have been too long, unavailable, inappropriate, duplicated, blocked, or skipped by staff."],
          ["How do I play Blackjack?", "Use !bj [amount], then !hit, !stand, !double, or !split when available. Beat the dealer without going over 21."],
          ["How do I play Poker?", "Use !join [amount] to buy in, then check, call, raise, fold, or all-in on your turn."],
          ["How do I get rare ores?", "Mine consistently. Rare odds are low; special events or tools may improve outcomes when active."],
          ["How do I catch rare fish?", "Fish often and watch for rod/event bonuses. Rare fish use lower catch weights."],
          ["How do leaderboards work?", "Leaderboards read live stats such as balance, XP, mining/fishing totals, radio activity, event points, tips, and streaks."],
          ["What does VIP do?", "VIP perks depend on the current room setup; staff can explain the active benefits."],
          ["Who do I ask for help?", "Ask room staff or use the public help commands in-room."],
        ].map(([q, a]) => `<div class="card"><h3>${esc(q)}</h3><p class="manual-copy">${esc(a)}</p></div>`).join("")}
      </div>`,
  }[tab] || "";
  return `
    <div class="pub-section-title"><h2>📖 How to Play</h2>
      <p>A detailed player manual for music, games, rewards, progression, and commands.</p></div>
    <div class="tab-nav manual-tabs">
      ${tabs.map((name) => `<button class="tab-btn ${name === tab ? "active" : ""}" data-manual-tab="${esc(name)}">${esc(name)}</button>`).join("")}
    </div>
    <div class="manual-panel">${panel}</div>
    ${(d.diagnostics?.missing_tables || []).length ? `<div class="notice" style="margin-top:16px">Some live manual sections are using fallback text because these tables are missing: ${esc(d.diagnostics.missing_tables.slice(0, 8).join(", "))}</div>` : ""}
  `;
}

function renderPublicCasino(d) {
  const casino = d.casino || d;
  const bj = casino.blackjack_settings || {};
  const poker = casino.poker_settings || {};
  const boards = casino.leaderboards || d.leaderboards || {};
  return `
    <div class="pub-section-title"><h2>🎲 Casino</h2>
      <p>Public guide and live settings for AceSinatra Blackjack and ChipSoprano Poker.</p></div>
    <div class="pub-grid2">
      <div class="card">
        <h3>Blackjack / RBJ Guide</h3>
        <p class="manual-copy">Beat the dealer without busting over 21. Start with <code>!bj [amount]</code>, then choose hit, stand, double, or split when the table allows it.</p>
        ${publicMetricGrid([
          ["Min Bet", publicCoins(bj.min_bet)],
          ["Max Bet", publicCoins(bj.max_bet)],
          ["Players", bj.max_players],
          ["Timer", bj.turn_timer ? `${bj.turn_timer} sec` : "—"],
          ["Decks", bj.decks],
          ["Shuffle", bj.shuffle_used_percent ? `${bj.shuffle_used_percent}% used` : "—"],
        ])}
        ${publicCommandChips(["!bj [amount]", "!hit", "!stand", "!double", "!split"])}
      </div>
      <div class="card">
        <h3>Poker Guide</h3>
        <p class="manual-copy">Texas Hold'em with public table actions. Buy in with <code>!join [amount]</code>, then check, call, raise, fold, or go all-in on your turn.</p>
        ${publicMetricGrid([
          ["Min Buy-In", publicCoins(poker.min_buyin)],
          ["Max Buy-In", publicCoins(poker.max_buyin)],
          ["Players", poker.max_players],
          ["Timer", poker.turn_timer ? `${poker.turn_timer} sec` : "—"],
          ["Small Blind", publicCoins(poker.small_blind)],
          ["Big Blind", publicCoins(poker.big_blind)],
        ])}
        ${publicCommandChips(["!join [amount]", "!check", "!call", "!raise [amount]", "!fold", "!allin", "!leave"])}
      </div>
    </div>
    <div class="pub-section-title"><h2>Casino Leaderboards</h2><p>Live public ranking summaries where game data exists.</p></div>
    <div class="pub-rankings-grid">
      <div class="card"><h3>Most Games Won</h3>${publicMiniLeaders(boards.casino_overall || [], { value: (r) => `${publicFmt(r.wins)} wins`, detail: (r) => r.total_won ? publicCoins(r.total_won) : "" })}</div>
      <div class="card"><h3>Blackjack / RBJ</h3>${publicMiniLeaders(boards.blackjack || [], { value: (r) => `${publicFmt(r.wins)} wins`, detail: (r) => r.blackjacks ? `${publicFmt(r.blackjacks)} natural blackjacks` : "" })}</div>
      <div class="card"><h3>Poker</h3>${publicMiniLeaders(boards.poker || [], { value: (r) => `${publicFmt(r.wins)} wins`, detail: (r) => r.net ? `${publicCoins(r.net)} net` : "" })}</div>
    </div>
  `;
}

function renderPublicMining(d) {
  const mining = d.mining || d;
  const settings = mining.settings || {};
  const boards = mining.leaderboards || {};
  return `
    <div class="pub-section-title"><h2>⛏️ Mining</h2>
      <p>Mine ores, chase rare drops, earn coins and XP, and climb the mining boards.</p></div>
    <div class="pub-grid2">
      <div class="card">
        <h3>How Mining Works</h3>
        <p class="manual-copy">Use <code>!mine</code> in-room to search for ores. Ores can have different rarities, values, weights, event availability, and drop chances.</p>
        ${publicMetricGrid([
          ["Mining Enabled", settings.mining_enabled ?? "—"],
          ["Cooldown", settings.base_cooldown_seconds ? `${settings.base_cooldown_seconds} sec` : "—"],
          ["Auto Mining", settings.automine_enabled ?? "—"],
          ["Announcements", settings.mining_announce_enabled ?? "—"],
        ])}
        ${publicCommandChips(["!mine", "!topminers", "!profile"])}
      </div>
      <div class="card">
        <h3>Drop Odds / Chance</h3>
        ${publicSmallTable((mining.odds || []).slice(0, 12), [
          { key: "ore", label: "Ore" },
          { key: "rarity", label: "Rarity", render: (r) => r.rarity || "—" },
          { key: "chance_percent", label: "Chance", render: (r) => publicPercent(r.chance_percent) },
          { key: "event_only", label: "Event", render: (r) => r.event_only ? "Event only" : "Normal" },
        ])}
      </div>
    </div>
    <div class="card">
      <h3>Ore Catalog</h3>
      ${publicSmallTable(mining.ores || [], [
        { key: "name", label: "Ore" },
        { key: "rarity", label: "Rarity", html: (r) => `<span class="rarity-chip rarity-${esc(String(r.rarity || "common").toLowerCase())}">${esc(r.rarity || "—")}</span>` },
        { key: "value", label: "Value", render: (r) => publicCoins(r.value) },
        { key: "chance_percent", label: "Chance", render: (r) => publicPercent(r.chance_percent) },
        { key: "event_only", label: "Event Only", render: (r) => r.event_only ? "Yes" : "No" },
      ])}
    </div>
    <div class="pub-rankings-grid">
      <div class="card"><h3>Top Miners</h3>${publicMiniLeaders(boards.top_miners || [], { value: (r) => r.total_mined ? `${publicFmt(r.total_mined)} mined` : `${publicFmt(r.xp)} XP`, detail: (r) => r.level ? `Level ${r.level}` : "" })}</div>
      <div class="card"><h3>Heaviest Ores</h3>${publicMiniLeaders(boards.heaviest_ores || [], { name: (r) => r.ore || "Ore", value: (r) => publicLbs(r.weight), detail: (r) => `Found by ${r.username || "Unknown Player"}` })}</div>
      <div class="card"><h3>Most Valuable Ores</h3>${publicMiniLeaders(boards.most_valuable_ores || [], { name: (r) => r.ore || "Ore", value: (r) => publicCoins(r.value), detail: (r) => `${r.username || "Unknown Player"} · ${r.rarity || "ore"}` })}</div>
      <div class="card"><h3>Best Rare Finds</h3>${publicMiniLeaders(boards.rarest_finds || [], { name: (r) => r.ore || "Ore", value: (r) => r.rarity || "rare", detail: (r) => [r.weight ? publicLbs(r.weight) : "", r.value ? publicCoins(r.value) : "", `found by ${r.username || "Unknown Player"}`].filter(Boolean).join(" · ") })}</div>
    </div>
    <div class="card">
      <h3>Pickaxes / Tools</h3>
      <p class="manual-copy">Tool upgrades appear here when the active catalog exposes public-safe data.</p>
      ${publicSmallTable(mining.tools || [], [
        { key: "name", label: "Pickaxe" },
        { key: "required_level", label: "Required Level" },
        { key: "luck_bonus", label: "Luck" },
        { key: "speed_bonus", label: "Speed" },
      ])}
    </div>
  `;
}

function renderPublicFishing(d) {
  const fishing = d.fishing || d;
  const settings = fishing.settings || {};
  const boards = fishing.leaderboards || {};
  return `
    <div class="pub-section-title"><h2>🎣 Fishing</h2>
      <p>Catch fish, chase rare weights, earn coins and XP, and climb the fishing boards.</p></div>
    <div class="pub-grid2">
      <div class="card">
        <h3>How Fishing Works</h3>
        <p class="manual-copy">Use <code>!fish</code> in-room to cast. Fish can vary by rarity, value, weight range, and catch chance.</p>
        ${publicMetricGrid([
          ["AutoFish", settings.autofish_enabled ?? "—"],
          ["Base Duration", settings.fish_base_duration ? `${settings.fish_base_duration} min` : "—"],
          ["Cast Interval", settings.fish_base_interval ? `${settings.fish_base_interval} sec` : "—"],
          ["Base Luck", settings.fish_base_luck ?? "—"],
        ])}
        ${publicCommandChips(["!fish", "!topfishers", "!profile"])}
      </div>
      <div class="card">
        <h3>Catch Odds / Chance</h3>
        ${publicSmallTable((fishing.odds || []).slice(0, 12), [
          { key: "fish", label: "Fish" },
          { key: "rarity", label: "Rarity", render: (r) => r.rarity || "—" },
          { key: "chance_percent", label: "Chance", render: (r) => publicPercent(r.chance_percent) },
        ])}
      </div>
    </div>
    <div class="card">
      <h3>Fish Catalog</h3>
      ${publicSmallTable(fishing.fish || [], [
        { key: "name", label: "Fish" },
        { key: "rarity", label: "Rarity", html: (r) => `<span class="rarity-chip rarity-${esc(String(r.rarity || "common").toLowerCase())}">${esc(r.rarity || "—")}</span>` },
        { key: "base_value", label: "Value", render: (r) => publicCoins(r.base_value) },
        { key: "min_weight", label: "Min Weight", render: (r) => publicLbs(r.min_weight) },
        { key: "max_weight", label: "Max Weight", render: (r) => publicLbs(r.max_weight) },
        { key: "chance_percent", label: "Chance", render: (r) => publicPercent(r.chance_percent) },
      ])}
    </div>
    <div class="pub-rankings-grid">
      <div class="card"><h3>Top Fishers</h3>${publicMiniLeaders(boards.top_fishers || [], { value: (r) => `${publicFmt(r.total_catches)} catches`, detail: (r) => r.level ? `Level ${r.level}` : "" })}</div>
      <div class="card"><h3>Heaviest Fish</h3>${publicMiniLeaders(boards.heaviest_fish || [], { name: (r) => r.fish || "Fish", value: (r) => publicLbs(r.weight), detail: (r) => `Caught by ${r.username || "Unknown Player"}` })}</div>
      <div class="card"><h3>Most Valuable Fish</h3>${publicMiniLeaders(boards.most_valuable_fish || [], { name: (r) => r.fish || "Fish", value: (r) => publicCoins(r.value), detail: (r) => `${r.username || "Unknown Player"} · ${r.rarity || "fish"}` })}</div>
      <div class="card"><h3>Best Rare Catches</h3>${publicMiniLeaders(boards.rarest_catches || [], { name: (r) => r.fish || "Fish", value: (r) => r.rarity || "rare", detail: (r) => [r.weight ? publicLbs(r.weight) : "", r.value ? publicCoins(r.value) : "", `caught by ${r.username || "Unknown Player"}`].filter(Boolean).join(" · ") })}</div>
    </div>
    <div class="card">
      <h3>Rods / Tools</h3>
      <p class="manual-copy">Rod upgrades appear here when the active catalog exposes public-safe data.</p>
      ${publicSmallTable(fishing.rods || [], [
        { key: "name", label: "Rod" },
        { key: "required_level", label: "Required Level" },
        { key: "luck_bonus", label: "Luck" },
        { key: "speed_bonus", label: "Speed" },
      ])}
    </div>
  `;
}

function renderPublicQuests(d) {
  const overview = d.overview || {};
  const active = d.active_quests || [];
  const daily = d.daily_quests || [];
  const weekly = d.weekly_quests || [];
  const event = d.event_quests || [];
  const commands = d.commands || [];
  const questName = (r) => r.name || r.title || r.quest_id || r.mission_id || "Quest";
  const questDetail = (r) => [
    r.description,
    r.category,
    r.target_amount ? `Target ${publicFmt(r.target_amount)}` : "",
    r.reward_coins ? `${publicCoins(r.reward_coins)} reward` : "",
    r.reward_xp ? `${publicFmt(r.reward_xp)} XP` : "",
  ].filter(Boolean).join(" · ");
  const questList = (rows, empty = "No quests listed yet.") => rows.length
    ? rows.slice(0, 10).map((r) => `<div class="pub-event-item">
        <div class="pub-event-name">${esc(questName(r))}</div>
        ${questDetail(r) ? `<div class="muted text-sm">${esc(questDetail(r))}</div>` : ""}
      </div>`).join("")
    : `<div class="notice">${esc(empty)}</div>`;
  return `
    <div class="pub-section-title"><h2>📜 Quests & Missions</h2>
      <p>Daily, weekly, event, and activity challenges from the live quest tables.</p></div>
    <div class="pub-grid2">
      <div class="card">
        <h3>How Quests Work</h3>
        <p class="manual-copy">Complete room activities like mining, fishing, events, radio requests, casino play, and daily claims to earn quest progress and rewards when the mission system is active.</p>
        ${publicCommandChips(commands.map((cmd) => cmd.command))}
      </div>
      <div class="card">
        <h3>Quest Snapshot</h3>
        ${publicMetricGrid([
          ["Active Quests", overview.active_quests ?? 0],
          ["Daily", overview.daily_quests ?? 0],
          ["Weekly", overview.weekly_quests ?? 0],
          ["Event", overview.event_quests ?? 0],
          ["Players Progressing", overview.players_with_progress ?? 0],
          ["Pending Rewards", overview.pending_rewards ?? 0],
        ])}
      </div>
    </div>
    <div class="pub-rankings-grid">
      <div class="card"><h3>Active Quests</h3>${questList(active)}</div>
      <div class="card"><h3>Daily Quests</h3>${questList(daily, "No daily quests listed yet.")}</div>
      <div class="card"><h3>Weekly Quests</h3>${questList(weekly, "No weekly quests listed yet.")}</div>
      <div class="card"><h3>Event Quests</h3>${questList(event, "No event quests listed yet.")}</div>
    </div>
    <div class="card">
      <h3>Player Commands</h3>
      <p class="manual-copy">Use ${commandChip("!quests")} or ${commandChip("!missions")} if enabled, ${commandChip("!daily")} for daily rewards, and ${commandChip("!profile")} to check progress.</p>
    </div>
  `;
}

function renderPublicEvents(d) {
  const scheduled = d.scheduled || [];
  const definitions = d.definitions || [];
  const eventPoints = d.leaderboards?.event_points || [];
  const current = d.current_settings || [];
  const currentMap = Object.fromEntries(current.map((row) => [row.key, row.value]));
  const active = currentMap.event_active === "1" ? currentMap.event_name : (currentMap.active_event || "");
  return `
    <div class="pub-section-title"><h2>🎉 Events</h2>
      <p>Current and upcoming ChillTopia events</p></div>
    <div class="card" style="margin-bottom:16px">
      <h3>Current Event</h3>
      ${active ? `<div class="pub-event-item"><div class="pub-event-name">${esc(active)}</div>${currentMap.event_expires_at ? `<div class="muted text-sm">Ends ${esc(currentMap.event_expires_at)}</div>` : ""}</div>` : `<div class="notice">No event is active right now.</div>`}
    </div>
    <div class="pub-grid2">
      <div class="card">
        <h3>🏆 Event Points</h3>
        <p class="muted" style="margin-bottom:12px">Earn points by participating in room events.</p>
        ${publicMiniLeaders(eventPoints, { value: (r) => `${publicFmt(r.points)} pts`, detail: (r) => r.fallback_id ? `id ${String(r.fallback_id).slice(0, 8)}...` : "" })}
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
    <div class="pub-grid2">
      <div class="card">
        <h3>Event Definitions</h3>
        ${publicSmallTable(definitions.slice(0, 12), [
          { key: "name", label: "Event" },
          { key: "description", label: "Description" },
          { key: "reward", label: "Reward" },
        ], "No public event definitions yet.")}
      </div>
      <div class="card">
        <h3>How to Participate</h3>
        <p class="manual-copy">Watch room announcements, follow host instructions, and use public event commands if they are enabled for the current activity.</p>
        ${publicCommandChips(["!events", "!event", "!top"])}
        <p class="manual-note">Owner start/stop controls are not available on public pages.</p>
      </div>
    </div>
  `;
}

function renderPublicRankings(d) {
  const lb = d.leaderboards || d || {};
  const metadata = d.diagnostics || d.metadata || {};
  const tabs = ["Overview", "Economy", "Mining", "Fishing", "Casino", "Radio", "Events", "Social"];
  if (!tabs.includes(state.publicRankingTab)) state.publicRankingTab = "Overview";
  const current = state.publicRankingTab;
  const looksLikeId = (v) => /^[a-z0-9_-]{18,}$/i.test(String(v || ""));
  const fmtNum = (v) => {
    const n = Number(v);
    return Number.isFinite(n) ? n.toLocaleString() : (v ?? "—");
  };
  const coins = (v) => `${fmtNum(v)} coins`;
  const lbs = (v) => `${fmtNum(v)} lbs`;
  const cleanSongTitle = (v) => String(v || "Unknown Track")
    .replace(/\s*[\[(](official\s+(music\s+)?video|official\s+audio|lyrics?|lyric\s+video)[\])]\s*/ig, " ")
    .replace(/\s*\b(official\s+(music\s+)?video|official\s+audio|lyric\s+video|lyrics?|4k|hd)\b\s*/ig, " ")
    .replace(/\s+/g, " ")
    .trim() || "Unknown Track";
  const playerName = (r) => {
    const name = r.username || r.player || r.requester || r.name;
    if (!name || looksLikeId(name)) return "Unknown Player";
    return name;
  };
  const valueText = (r, keys) => keys.map((k) => r[k]).find((v) => v !== undefined && v !== null && v !== "") ?? "";
  const songDetail = (r) => [r.artist, r.requester ? `requested by ${r.requester}` : ""].filter(Boolean).join(" · ");
  const rareDetail = (r, foundLabel) => [
    r.rarity || "rare",
    r.weight !== undefined && r.weight !== "" ? lbs(r.weight) : "",
    r.value !== undefined && r.value !== "" ? coins(r.value) : "",
    `${foundLabel} ${playerName(r)}`,
  ].filter(Boolean).join(" · ");
  const cleanRows = (rows, titleKeys = ["username", "title", "ore", "fish", "name"]) => (rows || [])
    .filter((row) => titleKeys.some((key) => row[key] !== undefined && row[key] !== null && row[key] !== ""))
    .slice(0, 10);
  function leaderboard(title, icon, rows, { name = (r) => playerName(r), value = (r) => valueText(r, ["value", "points", "balance", "xp"]), detail = null, empty = "No data yet.", rowClass = "" } = {}) {
    const list = cleanRows(rows);
    return `<div class="card pub-rank-card">
      <div class="card-header"><h3>${icon} ${esc(title)}</h3><span class="pill info">Top 10</span></div>
      ${list.length ? `<div class="pub-leaderboard">
        ${list.map((r, i) => `<div class="pub-lb-row rich ${esc(rowClass)}">
          <span class="pub-lb-rank ${i < 3 ? "top" + i : ""}">${["🥇","🥈","🥉"][i] || (i + 1)}</span>
          <span class="pub-lb-main">
            <span class="pub-lb-name leaderboard-title-marquee" title="${esc(name(r) || "—")}"><span>${esc(name(r) || "—")}</span></span>
            ${detail ? `<span class="pub-lb-detail">${esc(detail(r) || "")}</span>` : ""}
          </span>
          <span class="pub-lb-val">${esc(String(value(r) ?? ""))}</span>
        </div>`).join("")}
        ${(rows || []).length > 10 ? `<div class="muted text-sm" style="margin-top:10px;text-align:center">Showing top 10 of ${rows.length}</div>` : ""}
      </div>` : `<div class="pub-empty">${esc(empty)}</div>`}
    </div>`;
  }
  const maybeLeaderboard = (title, icon, rows, opts = {}) => has(rows) ? leaderboard(title, icon, rows, opts) : "";
  const section = (title, cards) => {
    const visible = cards.filter(Boolean);
    return `<div class="pub-ranking-section"><h3>${esc(title)}</h3>${visible.length ? `<div class="pub-rankings-grid">${visible.join("")}</div>` : `<div class="pub-empty compact">No data yet.</div>`}</div>`;
  };
  const has = (rows) => Array.isArray(rows) && rows.length > 0;
  const top = (rows) => (rows || [])[0] || {};
  function featured(title, icon, row, valueKeys, subtitleFn = playerName) {
    const hasRow = row && Object.keys(row).length;
    return `<div class="card pub-feature-card">
      <div class="pub-feature-icon">${icon}</div>
      <div><span>${esc(title)}</span><strong>${esc(hasRow ? subtitleFn(row) : "No data yet")}</strong><em>${esc(String(hasRow ? valueText(row, valueKeys) : ""))}</em></div>
    </div>`;
  }
  const panels = {
    Overview: `
      <div class="pub-feature-grid">
        ${featured("Richest Player", "💰", top(lb.richest || d.rich), ["balance"])}
        ${featured("Highest Level", "⬆️", top(lb.level), ["level", "xp"])}
        ${featured("Top Miner", "⛏️", top(lb.mining_top || d.mining), ["total_mined", "xp"])}
        ${featured("Top Fisher", "🎣", top(lb.fishing_top || d.fishing), ["total_catches", "biggest_catch"])}
        ${featured("Top Requester", "🎵", top(lb.radio_requesters || d.radio), ["requests"])}
        ${featured("Casino Leader", "🎲", top(lb.casino_overall || d.casino || lb.most_games_won), ["wins", "total_won"])}
      </div>`,
    Economy: section("Economy Leaders", [
      maybeLeaderboard("Richest Players", "💰", lb.richest || d.rich, { value: (r) => coins(r.balance) }),
      maybeLeaderboard("Top XP / Level", "⭐", lb.xp, { value: (r) => `${fmtNum(r.xp ?? 0)} XP`, detail: (r) => `Level ${fmtNum(r.level ?? "—")}` }),
      maybeLeaderboard("Daily Streaks", "🔥", lb.streaks, { value: (r) => `${r.streak ?? 0} days`, detail: (r) => r.total_claims ? `${r.total_claims} claims` : "" }),
      maybeLeaderboard("Gold Supporters", "🥇", lb.topdonators, { value: (r) => `${r.total_gold ?? 0} gold`, detail: (r) => `${r.entries ?? 0} entries` }),
      maybeLeaderboard("Top P2P Senders", "💸", lb.toptippers, { value: (r) => `${r.total_gold ?? 0} gold`, detail: (r) => `${r.entries ?? 0} tips` }),
      maybeLeaderboard("Top P2P Receivers", "🤝", lb.toptipped, { value: (r) => `${r.total_gold ?? 0} gold`, detail: (r) => `${r.entries ?? 0} tips` }),
    ]),
    Mining: section("Mining Leaders", [
      maybeLeaderboard("Top Miners", "⛏️", lb.mining_top || d.mining, { value: (r) => r.total_mined ?? r.xp ?? "", detail: (r) => `Level ${r.level ?? "—"} · ${r.rare_finds ?? 0} rare` }),
      maybeLeaderboard("Heaviest Ores", "🪨", lb.mining_heaviest_ore, { name: (r) => r.ore, value: (r) => lbs(r.weight), detail: (r) => `Found by ${playerName(r)} · ${r.rarity || "ore"}` }),
      maybeLeaderboard("Most Valuable Ores", "💎", lb.mining_most_valuable, { name: (r) => r.ore, value: (r) => coins(r.value), detail: (r) => `${playerName(r)} · ${r.rarity || "ore"}` }),
      maybeLeaderboard("Rarest Mining Finds", "✨", lb.mining_rarest, { name: (r) => r.ore, value: (r) => r.rarity || "rare", detail: (r) => rareDetail(r, "found by") }),
    ]),
    Fishing: section("Fishing Leaders", [
      maybeLeaderboard("Top Fishers", "🎣", lb.fishing_top || d.fishing, { value: (r) => `${r.total_catches ?? 0} catches`, detail: (r) => `Level ${r.level ?? "—"}` }),
      maybeLeaderboard("Heaviest Fish", "🐟", lb.fishing_heaviest_fish, { name: (r) => r.fish, value: (r) => lbs(r.weight), detail: (r) => `Caught by ${playerName(r)} · ${r.rarity || "fish"}` }),
      maybeLeaderboard("Most Valuable Fish", "💧", lb.fishing_most_valuable, { name: (r) => r.fish, value: (r) => coins(r.value), detail: (r) => `${playerName(r)} · ${r.rarity || "fish"}` }),
      maybeLeaderboard("Rarest Fishing Catches", "✨", lb.fishing_rarest, { name: (r) => r.fish, value: (r) => r.rarity || "rare", detail: (r) => rareDetail(r, "caught by") }),
    ]),
    Casino: section("Casino Leaders", [
      maybeLeaderboard("Most Games Won", "🎲", lb.casino_overall || lb.most_games_won || d.casino, { value: (r) => `${fmtNum(r.wins ?? 0)} wins`, detail: (r) => r.total_won ? coins(r.total_won) : "" }),
      has(lb.poker) ? leaderboard("Poker Wins", "♠️", lb.poker, { value: (r) => `${fmtNum(r.wins ?? 0)} wins`, detail: (r) => `${fmtNum(r.hands_played || 0)} hands` }) : "",
      has(lb.poker) ? leaderboard("Poker Net Profit/Loss", "📈", lb.poker.filter((r) => r.net !== ""), { value: (r) => coins(r.net), detail: (r) => playerName(r) }) : "",
      has(lb.poker) ? leaderboard("Biggest Poker Pots", "🏦", lb.poker.filter((r) => r.biggest_pot).sort((a, b) => Number(b.biggest_pot || 0) - Number(a.biggest_pot || 0)), { value: (r) => coins(r.biggest_pot), detail: (r) => playerName(r) }) : "",
      has(lb.blackjack) ? leaderboard("Blackjack Wins", "🃏", lb.blackjack, { value: (r) => `${fmtNum(r.wins ?? 0)} wins`, detail: (r) => r.blackjacks ? `${fmtNum(r.blackjacks)} natural blackjacks` : "" }) : leaderboard("Blackjack / RBJ", "🃏", [], { empty: "No blackjack rounds recorded yet." }),
      has(lb.blackjack) ? leaderboard("Natural Blackjacks", "✨", lb.blackjack.filter((r) => Number(r.blackjacks || 0) > 0), { value: (r) => `${fmtNum(r.blackjacks)} naturals`, detail: (r) => playerName(r) }) : "",
      has(lb.blackjack) ? leaderboard("Blackjack Net Winnings", "💵", lb.blackjack.filter((r) => r.net !== ""), { value: (r) => coins(r.net), detail: (r) => playerName(r) }) : "",
      has(lb.blackjack) ? leaderboard("Biggest Blackjack Wins", "🏆", lb.blackjack.filter((r) => r.biggest_win).sort((a, b) => Number(b.biggest_win || 0) - Number(a.biggest_win || 0)), { value: (r) => coins(r.biggest_win), detail: (r) => playerName(r) }) : "",
    ]),
    Radio: section("Radio Leaders", [
      maybeLeaderboard("Top Requesters", "🎵", lb.radio_requesters || d.radio, { value: (r) => `${fmtNum(r.requests ?? 0)} requests` }),
      maybeLeaderboard("Top Liked Songs", "👍", lb.radio_liked, { name: (r) => cleanSongTitle(r.title || r.name), value: (r) => `${fmtNum(r.likes ?? 0)} likes`, detail: songDetail, rowClass: "song-row" }),
      maybeLeaderboard("Top Disliked Songs", "👎", lb.radio_disliked, { name: (r) => cleanSongTitle(r.title || r.name), value: (r) => `${fmtNum(r.dislikes ?? 0)} dislikes`, detail: songDetail, rowClass: "song-row" }),
      maybeLeaderboard("Most Liked Requesters", "💜", lb.radio_liked_requesters, { value: (r) => `${fmtNum(r.likes ?? 0)} likes received` }),
      has(lb.radio_tracks) ? leaderboard("Most Played Songs", "📻", lb.radio_tracks, { name: (r) => cleanSongTitle(r.title || r.name), value: (r) => `${fmtNum(r.plays ?? r.requests ?? 0)} plays`, detail: (r) => r.artist || "", rowClass: "song-row" }) : "",
    ]),
    Events: section("Event Leaders", [
      maybeLeaderboard("Event Points", "🎉", lb.event_points || d.events, { value: (r) => `${r.points ?? 0} pts`, detail: (r) => r.fallback_id ? `id ${String(r.fallback_id).slice(0, 8)}…` : "" }),
    ]),
    Social: section("Social Leaders", [
      maybeLeaderboard("Reputation", "💜", lb.reputation, { value: (r) => `${r.rep_received ?? 0} received`, detail: (r) => r.rep_given ? `${r.rep_given} given` : "" }),
    ]),
  };
  return `
    <div class="pub-section-title"><h2>🏆 Rankings</h2>
      <p>Clean leaderboard categories backed by live ChillTopia data.</p></div>
    <div class="tab-nav pub-ranking-tabs">
      ${tabs.map((name) => `<button class="tab-btn ${name === current ? "active" : ""}" data-ranking-tab="${esc(name)}">${esc(name)}</button>`).join("")}
    </div>
    <div class="pub-ranking-hub">${panels[current] || panels.Overview}</div>
    ${(metadata.source_errors || []).length ? `<div class="notice" style="margin-top:16px">Some leaderboard sources had errors, but the rest of the page is still live.</div>` : ""}
  `;
}

function renderPublicRoomInfo(d = state.data || {}) {
  const info = d.info || {};
  const lines = (text, fallback) => String(text || fallback || "")
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean);
  const rules = lines(info.room_rules, "Be respectful.\nNo spamming commands or chat.\nKeep requests appropriate for all ages.\nFollow staff instructions.\nNo advertising other rooms.\nHave fun and spread good vibes.");
  const vip = lines(info.vip_info, "Priority song queue slots\nBonus daily coin rewards\nExclusive VIP badge\nSpecial room access\nPersonal bot shoutout");
  const staff = lines(info.staff_list, "");
  const announcements = d.announcements || [];
  const rewardInfo = d.rewards || {};
  return `
    <div class="pub-section-title"><h2>ℹ️ Room Info</h2>
      <p>Everything you need to know about ChillTopia</p></div>
    <div class="card">
      <h3>About ChillTopia</h3>
      <p class="manual-copy">ChillTopia is a relaxed Highrise room built around DJ DUDU Radio, casino games, mining, fishing, events, public rankings, and player rewards. Use the public tabs to learn commands and track live room activity.</p>
      ${d.join_url ? `<a class="btn cyan" href="${esc(d.join_url)}" target="_blank" rel="noopener">Join on Highrise</a>` : ""}
    </div>
    <div class="pub-grid2">
      <div class="card">
        <h3>📋 Room Rules</h3>
        <ol class="pub-rules-list">
          ${rules.map((rule) => `<li>${esc(rule)}</li>`).join("")}
        </ol>
      </div>
      <div class="card">
        <h3>⭐ VIP Perks</h3>
        <div class="pub-vip-list">
          ${vip.map((item) => `<div class="pub-vip-item">${esc(item)}</div>`).join("")}
        </div>
        <p class="muted text-sm" style="margin-top:10px">VIP status is tracked as a player reward and may unlock bonus perks when enabled by the room.</p>
      </div>
      <div class="card">
        <h3>🤖 Key Commands</h3>
        <div class="pub-cmd-list">
          ${[["!balance","Check your coins"],["!daily","Claim daily reward"],["!bj [bet]","Play Blackjack"],
             ["!poker","Join Poker"],["!mine","Start mining"],["!fish","Start fishing"],
             ["!play [song]","Request a song"],["!queue","View song queue"],
             ["!profile","View your profile"],["!top","Top players"]
          ].map(([cmd, desc]) => `<div class="pub-cmd-item"><code>${esc(cmd)}</code><span>${esc(desc)}</span></div>`).join("")}
        </div>
      </div>
      <div class="card">
        <h3>👥 Staff Roles</h3>
        ${staff.length ? `<div class="pub-vip-list">${staff.map((item) => `<div class="pub-vip-item">${esc(item)}</div>`).join("")}</div>` : `<div class="pub-staff-roles">
          <div class="pub-staff-role"><span class="pill ok">Owner</span><span>Full room authority</span></div>
          <div class="pub-staff-role"><span class="pill info">Admin</span><span>Rule enforcement</span></div>
          <div class="pub-staff-role"><span class="pill def">Manager</span><span>Event & bot control</span></div>
          <div class="pub-staff-role"><span class="pill warn">Mod</span><span>Chat moderation</span></div>
          <div class="pub-staff-role"><span class="pill info">DJ</span><span>Radio management</span></div>
        </div>`}
      </div>
      <div class="card">
        <h3>📢 Room Updates</h3>
        ${announcements.length ? announcements.map((a) => `<div class="pub-event-item">${esc(a.message)}</div>`).join("") : `<div class="notice">No public announcements posted.</div>`}
      </div>
      <div class="card">
        <h3>🤖 Bot Roles</h3>
        <div class="pub-cmd-list">
          ${[
            ["DJ_DUDU", "radio, emotes, dancefloor, sync"],
            ["ChillTopiaMC", "host, announcements, events"],
            ["BankingBot", "economy, bank, rewards, shop"],
            ["AceSinatra", "Blackjack / Realistic Blackjack"],
            ["ChipSoprano", "Poker"],
            ["GreatestProspector", "mining"],
            ["MasterAngler", "fishing"],
            ["KeanuShield", "security and moderation"],
          ].map(([bot, role]) => `<div class="pub-cmd-item"><code>${esc(bot)}</code><span>${esc(role)}</span></div>`).join("")}
        </div>
      </div>
    </div>
    <div class="pub-grid2">
      <div class="card">
        <h3>📚 Public Titles</h3>
        ${(rewardInfo.titles || []).length ? publicSmallTable((rewardInfo.titles || []).slice(0, 10), [
          { key: "name", label: "Title" },
          { key: "tier", label: "Tier" },
          { key: "description", label: "How to Earn" },
        ]) : `<div class="notice">Titles are earned through events, rewards, and staff grants when available.</div>`}
      </div>
      <div class="card">
        <h3>🏅 Public Badges</h3>
        ${(rewardInfo.badges || []).length ? publicSmallTable((rewardInfo.badges || []).slice(0, 10), [
          { key: "name", label: "Badge" },
          { key: "description", label: "Info" },
        ]) : `<div class="notice">Badges are earned from achievements, events, and special room rewards.</div>`}
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
  document.querySelectorAll("[data-manual-tab]").forEach((btn) => btn.addEventListener("click", () => {
    state.howToPlayTab = btn.dataset.manualTab; state.error = ""; render();
  }));
  document.querySelectorAll("[data-manual-rarity]").forEach((btn) => btn.addEventListener("click", () => {
    const section = btn.dataset.manualSection;
    if (section) state.manualRarity[section] = btn.dataset.manualRarity;
    state.error = "";
    render();
  }));
  document.querySelectorAll("[data-ranking-tab]").forEach((btn) => btn.addEventListener("click", () => {
    state.publicRankingTab = btn.dataset.rankingTab; state.error = ""; render();
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
  const nav = visibleNavItems(role === "owner" ? OWNER_NAV : STAFF_NAV);
  const initials = (state.user?.username || "U").slice(0, 2).toUpperCase();
  const roleLabel = role === "owner" ? "Owner Control Center" : "Staff Console";
  const search = state.navSearch.trim().toLowerCase();
  const activeItem = activeNavItem();
  const navGroups = [];
  for (const item of nav) {
    const haystack = `${item.label} ${item.id} ${item.group || ""} ${item.keywords || ""}`.toLowerCase();
    if (search && !haystack.includes(search)) continue;
    let group = navGroups.find((g) => g.group === item.group);
    if (!group) {
      group = { group: item.group || "Main", items: [] };
      navGroups.push(group);
    }
    group.items.push(item);
  }

  const navHtml = navGroups.length ? navGroups.map((group) => `
    <details class="nav-group" open>
      <summary>${esc(group.group)}</summary>
      <div class="nav-group-items">
        ${group.items.map((item) => {
          const target = navTarget(item);
          const isActive = activeItem && navKey(activeItem) === navKey(item);
          return `<button class="${isActive ? "active" : ""}" data-admin-page="${esc(target.page)}" data-admin-tab="${esc(target.tab || "")}" data-nav-id="${esc(navKey(item))}">
            <span class="nav-icon">${item.icon}</span><span>${esc(item.label)}</span>
          </button>`;
        }).join("")}
      </div>
    </details>
  `).join("") : `<div class="empty-state compact"><strong>No matches</strong><span>Try “radio”, “player”, “backup”, or “alerts”.</span></div>`;

  app.innerHTML = `
    <button class="hamburger" id="hamburgerBtn">☰</button>
    <div class="sidebar-overlay" id="sidebarOverlay"></div>
    <div class="shell">
      <aside class="sidebar ${state.sidebarOpen ? "open" : ""}" id="sidebar">
        <div class="brand">
          <div class="mark">CT</div>
          <div><strong>ChillTopia</strong><span>${roleLabel}</span></div>
        </div>
        <div class="nav-search">
          <input id="navSearchInput" placeholder="Search dashboard..." value="${esc(state.navSearch)}" />
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
            <div class="page-kicker">${esc(activeItem?.group || "")}</div>
            <h1><span class="page-icon">${esc(activeItem?.icon || "")}</span>${esc(activeItem?.label || state.adminPage)}</h1>
            <div class="page-desc">${esc(PAGE_DESC[activeItem?.id] || PAGE_DESC[state.adminPage] || "")}</div>
          </div>
          <div class="topbar-actions">
            ${state.adminPage === "Operations Center" ? `<span class="pill info">Live Health</span>` : ""}
            ${state.adminPage === "System" ? `<span class="pill def">${esc(state.adminTab.System || "Health")}</span>` : ""}
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
    state.adminPage = btn.dataset.adminPage;
    if (btn.dataset.adminTab) state.adminTab[state.adminPage] = btn.dataset.adminTab;
    state.notice = ""; state.error = "";
    state.sidebarOpen = false; loadAdmin();
  }));
  document.getElementById("navSearchInput")?.addEventListener("input", (e) => {
    state.navSearch = e.currentTarget.value;
    render();
  });
  document.querySelectorAll("[data-page-tab]").forEach((btn) => btn.addEventListener("click", () => {
    state.notice = ""; state.error = ""; switchTab(state.adminPage, btn.dataset.pageTab);
  }));
  document.querySelectorAll("[data-maint-tab]").forEach((btn) => btn.addEventListener("click", () => {
    state.notice = ""; state.error = ""; loadMaintenanceTab(btn.dataset.maintTab);
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
  const nullDataOk = ["Players", "Bots", "Casino", "Room & Content", "Emotes", "Economy & Rewards",
    "Radio", "Security", "Leaderboards", "Operations Center", "System", "Staff Home", "Players", "Moderation", "Events", "Room Tools", "Logs"];
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
    case "Operations Center": return renderOperationsCenter(activeTab("Operations Center"));
    case "Bots":              return renderBotsPage(activeTab("Bots"));
    case "Radio":             return renderRadioOwnerPage(activeTab("Radio"));
    case "Players":           return renderOwnerPlayersPage(activeTab("Players"));
    case "Casino":            return renderCasinoOwnerPage();
    case "Security":          return renderSecurityPage(activeTab("Security"));
    case "Leaderboards":      return renderLeaderboardsPage(activeTab("Leaderboards"));
    case "Room & Content":    return renderRoomContent(activeTab("Room & Content"));
    case "Emotes":            return renderEmotesOwnerPage(activeTab("Emotes"));
    case "Mining":            return renderMiningOwnerPage(activeTab("Mining"));
    case "Fishing":           return renderFishingOwnerPage(activeTab("Fishing"));
    case "Quests & Missions": return renderQuestsMissionsPage(activeTab("Quests & Missions"));
    case "Automation Center": return renderAutomationCenterPage(activeTab("Automation Center"));
    case "Events":            return renderEventsOwnerPage();
    case "Economy & Rewards": return renderEconomyRewards(activeTab("Economy & Rewards"));
    case "Staff":             return renderStaffPage_shared();
    case "System":            return renderSystemPage(activeTab("System"));
    default:                  return renderCommandCenter();
  }
}

/* ── Operations Center ───────────────────────────────── */
function operationsStatusChip(value) {
  const s = String(value || "unknown").toUpperCase();
  const cls = /HEALTHY|OK|ONLINE|COMPLETED|READY/.test(s) ? "ok"
    : /CRITICAL|FAILED|OFFLINE|ERROR/.test(s) ? "bad"
    : /WARNING|WARN|PENDING|STALE|MISSING/.test(s) ? "warn"
    : "def";
  return `<span class="pill ${cls}">${esc(s)}</span>`;
}

function operationsUpdatedAt(d) {
  return `<div class="muted text-sm" style="margin-bottom:12px">Updated: ${esc(d.updated_at || "—")} · auto-refreshes while this page is open</div>`;
}

function renderOperationsCenter(tab) {
  const d = state.data || {};
  return `
    ${tabNav("Operations Center")}
    ${operationsUpdatedAt(d)}
    ${tab === "Overview" ? renderOperationsOverview(d) : ""}
    ${tab === "Bots" ? renderOperationsBots(d) : ""}
    ${tab === "Room" ? renderOperationsRoom(d) : ""}
    ${tab === "Radio" ? renderOperationsRadio(d) : ""}
    ${tab === "Command Queue" ? renderOperationsQueue(d) : ""}
    ${tab === "Database" ? renderOperationsDatabase(d) : ""}
    ${tab === "Errors" ? renderOperationsErrors(d) : ""}
    ${tab === "Alerts" ? renderOperationsAlerts(d) : ""}
    ${tab === "Logs" ? renderOperationsLogs(d) : ""}
    ${tab === "Advanced" ? renderOperationsAdvanced(d) : ""}
  `;
}

function renderOperationsOverview(d) {
  const o = d.overview || {};
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(170px,1fr));margin-bottom:14px">
      ${metricCard("System Status", o.system_status || d.system_status || "unknown", "overall health", /CRITICAL/i.test(o.system_status) ? "accent-red" : /WARNING/i.test(o.system_status) ? "" : "accent-green", "SYS")}
      ${metricCard("Bots Online", `${o.bots_online ?? 0}/${o.bots_total ?? 8}`, "canonical accounts", Number(o.bots_online) === Number(o.bots_total) ? "accent-green" : "accent-red", "🤖")}
      ${metricCard("Dashboard", o.dashboard_status || "unknown", "PM2/dashboard process", /online/i.test(o.dashboard_status) ? "accent-green" : "", "WEB")}
      ${metricCard("Radio", o.radio_status || "unknown", "DJ/radio health", /online/i.test(o.radio_status) ? "accent-green" : "accent-red", "📻")}
      ${metricCard("DB", o.db_status || "unknown", "SQLite integrity", /ok/i.test(o.db_status) ? "accent-green" : "accent-red", "DB")}
      ${metricCard("Queue", `${o.command_queue_pending ?? 0}/${o.command_queue_failed ?? 0}`, "pending / failed", Number(o.command_queue_failed) ? "accent-red" : "", "Q")}
      ${metricCard("Room Users", o.room_users ?? 0, "live_status", "accent-cyan", "👥")}
      ${metricCard("Alerts", o.active_alerts ?? 0, "active warnings", Number(o.active_alerts) ? "accent-red" : "accent-green", "!")}
    </div>
    <div class="grid">
      <div class="card">
        <div class="card-header"><h2>Active Alerts</h2>${operationsStatusChip(d.system_status)}</div>
        ${(d.alerts || []).length ? table(d.alerts, [
          { key: "severity", label: "Severity", render: (r) => operationsStatusChip(r.severity) },
          { key: "message", label: "Alert" },
          { key: "detail", label: "Detail" },
        ]) : `<div class="notice success">No active operational alerts.</div>`}
      </div>
      <div class="card">
        <h2>Quick Snapshot</h2>
        <div style="display:grid;gap:8px">
          <div class="inline-actions"><span>Last Restart</span><span class="muted">${esc(o.last_restart || "—")}</span></div>
          <div class="inline-actions"><span>Room ID</span><code>${esc(d.room?.room_id || "—")}</code></div>
          <div class="inline-actions"><span>Radio Queue</span><span>${esc(d.radio?.queue_size ?? 0)}</span></div>
          <div class="inline-actions"><span>Failed Commands</span>${operationsStatusChip(d.queue?.counts?.failed ? `${d.queue.counts.failed} failed` : "ok")}</div>
        </div>
      </div>
    </div>
  `;
}

function renderOperationsBots(d) {
  const bots = d.bots || [];
  return `
    <div class="card">
      <div class="card-header"><h2>Canonical Bots</h2><span class="pill info">${bots.length}/8</span></div>
      ${table(bots, [
        { key: "status", label: "Status", render: (r) => operationsStatusChip(r.status) },
        { key: "bot_username", label: "Username", render: (r) => `<strong>@${esc(r.bot_username)}</strong>` },
        { key: "bot_mode", label: "Mode" },
        { key: "last_heartbeat_at", label: "Heartbeat" },
        { key: "current_room_id", label: "Room" },
        { key: "last_error", label: "Recent Error", render: (r) => `<span class="muted">${esc(r.last_error || "—")}</span>` },
      ], (r) => `
        <button class="btn sm" data-bot-command="return_home" data-target-bot="${esc(r.bot_mode)}">Queue Home</button>
        <button class="btn sm" data-bot-command="stop_emote" data-target-bot="${esc(r.bot_mode)}">Queue Stop Emote</button>
        <button class="btn danger sm" data-bot-command="restart_requested" data-target-bot="${esc(r.bot_mode)}">Queue Restart</button>
      `)}
      ${queueHelp()}
    </div>
    <div class="card"><h2>Raw Heartbeat Rows</h2>${table(d.raw_bot_instances || [])}</div>
  `;
}

function renderOperationsRoom(d) {
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(190px,1fr));margin-bottom:14px">
      ${metricCard("Room Users", d.room_users_count ?? 0, "live_status", "accent-cyan", "👥")}
      ${metricCard("Bots Present", (d.bots_present || []).length, "online/current room", "accent-green", "🤖")}
      ${metricCard("Missing Bots", (d.missing_bots || []).length, "not confirmed", (d.missing_bots || []).length ? "accent-red" : "accent-green", "!")}
    </div>
    <div class="grid">
      <div class="card">
        <h2>Room Presence</h2>
        <div class="inline-actions"><span>Room ID</span><code>${esc(d.room_id || "—")}</code></div>
        <div class="inline-actions"><span>Bots Present</span><span>${esc((d.bots_present || []).join(", ") || "—")}</span></div>
        <div class="inline-actions"><span>Missing Bots</span><span>${esc((d.missing_bots || []).join(", ") || "None")}</span></div>
      </div>
      <div class="card"><h2>Spawn Restore Status</h2>${table(d.spawn_status || [])}</div>
    </div>
    <div class="card"><h2>Recent Not In Room Errors</h2>${table(d.recent_not_in_room_errors || [])}</div>
  `;
}

function renderOperationsRadio(d) {
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(190px,1fr));margin-bottom:14px">
      ${metricCard("Queue Size", d.queue_size ?? 0, "active/upcoming", "", "🎵")}
      ${metricCard("Request Gate", d.request_gate_open ? "Open" : "Closed", "public requests", d.request_gate_open ? "accent-green" : "", "REQ")}
      ${metricCard("Failed Jobs", d.failed_jobs_count ?? 0, "radio pipeline", Number(d.failed_jobs_count) ? "accent-red" : "accent-green", "!")}
      ${metricCard("DJ Heartbeat", d.dj_heartbeat?.status || "missing", "DJ_DUDU", /online/i.test(d.dj_heartbeat?.status || "") ? "accent-green" : "accent-red", "DJ")}
    </div>
    <div class="grid">
      <div class="card">
        <h2>Now Playing</h2>
        <div style="font-size:18px;font-weight:800">${esc(d.now_playing?.title || "Auto DJ / Unknown")}</div>
        <div class="muted text-sm">${esc(d.now_playing?.artist || d.now_playing?.requester || "—")}</div>
      </div>
      <div class="card"><h2>AzuraCast / Stream</h2>${table([d.azuracast || {}])}</div>
    </div>
    <div class="card"><h2>Queue</h2>${table(d.queue || [])}</div>
    <div class="card"><h2>Failed Radio Jobs</h2>${table(d.failed_jobs || [])}</div>
  `;
}

function renderOperationsQueue(d) {
  const c = d.counts || {};
  const failedColumns = [
    { key: "id", label: "ID" },
    { key: "target_bot", label: "Target" },
    { key: "action", label: "Action" },
    { key: "payload_summary", label: "Payload" },
    { key: "failure", label: "Error / Result" },
    { key: "created_at", label: "Created" },
    { key: "reviewed_at", label: "Reviewed" },
    { key: "actions", label: "", render: (row) => row.reviewed_at
      ? `<span class="pill ok">reviewed</span>`
      : `<button class="btn small ghost" data-action="queue-review" data-command-id="${esc(row.id)}">Mark Reviewed</button>` },
  ];
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr));margin-bottom:14px">
      ${metricCard("Pending", c.pending ?? 0, "queued", "", "P")}
      ${metricCard("Claimed", c.claimed ?? 0, "in progress", "", "C")}
      ${metricCard("Completed", c.completed ?? 0, "historical", "accent-green", "✓")}
      ${metricCard("Failed", c.failed ?? 0, "needs review", Number(c.failed) ? "accent-red" : "accent-green", "!")}
      ${metricCard("Paused", c.paused ?? 0, "paused", "", "Ⅱ")}
    </div>
    <div class="card"><h2>Queue Counts</h2>${table(d.by_status || [])}</div>
    <div class="card"><h2>Pending / Claimed</h2>${table(d.pending || [])}</div>
    <div class="card">
      <h2>Failed Commands</h2>
      <p class="muted text-sm">Failed rows stay in the queue for audit. Review marks the row as seen without deleting it.</p>
      ${table(d.failed || [], failedColumns)}
    </div>
    <div class="card"><h2>Recent Commands</h2>${table(d.recent || [])}</div>
  `;
}

function renderOperationsDatabase(d) {
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr));margin-bottom:14px">
      ${metricCard("Integrity", d.integrity_check || "unknown", "PRAGMA integrity_check", d.integrity_check === "ok" ? "accent-green" : "accent-red", "DB")}
      ${metricCard("DB Size", bytes(d.db_file?.size), d.db_path || "SQLite", "accent-cyan", "SZ")}
      ${metricCard("WAL Size", bytes(d.wal_size), "sidecar", Number(d.wal_size) > 50 * 1024 * 1024 ? "accent-red" : "", "WAL")}
      ${metricCard("Tables", d.table_count ?? 0, "SQLite", "", "T")}
      ${metricCard("Last Backup", d.last_backup?.modified_at || "See Maintenance", "approved backup folder", d.last_backup ? "accent-green" : "", "BK")}
    </div>
    <div class="card"><h2>Database Files</h2>${table([d.db_file || {}].concat(d.sidecars || []))}</div>
    <div class="card"><h2>Largest Tables</h2>${table(d.largest_tables || [])}</div>
    <div class="card"><h2>Warnings</h2>${(d.warnings || []).length ? table(d.warnings) : `<div class="notice success">Database health checks are clean.</div>`}</div>
  `;
}

function renderOperationsErrors(d) {
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr));margin-bottom:14px">
      ${metricCard("Recent Errors", d.recent_errors_count ?? 0, "aggregated rows", Number(d.recent_errors_count) ? "accent-red" : "accent-green", "!")}
      ${metricCard("Command Errors", (d.command_error_logs || []).length, "command_error_logs", "", "CMD")}
      ${metricCard("Failed Queue", (d.failed_commands || []).length, "bot_command_queue", "", "Q")}
      ${metricCard("Radio Failures", (d.radio_failures || []).length, "yt_request_jobs", "", "RAD")}
    </div>
    <div class="card"><h2>Command Error Logs</h2>${table(d.command_error_logs || [])}</div>
    <div class="card"><h2>Failed Commands</h2>${table(d.failed_commands || [])}</div>
    <div class="card"><h2>Failed Admin Actions</h2>${table(d.failed_admin_actions || [])}</div>
    <div class="card"><h2>Radio Failures</h2>${table(d.radio_failures || [])}</div>
  `;
}

function renderOperationsAlerts(d) {
  const alerts = d.alerts || [];
  return `
    <div class="card">
      <div class="card-header"><h2>Operational Alerts</h2>${operationsStatusChip(d.system_status)}</div>
      ${alerts.length ? table(alerts, [
        { key: "severity", label: "Severity", render: (r) => operationsStatusChip(r.severity) },
        { key: "key", label: "Alert Key" },
        { key: "message", label: "Message" },
        { key: "detail", label: "Detail" },
      ]) : `<div class="notice success">No active alerts.</div>`}
    </div>
  `;
}

function renderOperationsLogs(d) {
  return `
    <div class="card"><h2>Audit Logs</h2>${table(d.audit_logs || [])}</div>
    <div class="card"><h2>Admin Action Logs</h2>${table(d.admin_action_logs || [])}</div>
    <div class="card"><h2>Command Queue Failures</h2>${table(d.command_queue_failures || [])}</div>
    <div class="card"><h2>Radio Failures</h2>${table(d.radio_failures || [])}</div>
  `;
}

function renderOperationsAdvanced(d) {
  return `
    <div class="grid">
      <div class="card">
        <h2>PM2 Raw Status</h2>
        ${d.pm2?.available ? table(d.pm2.processes || []) : `<div class="notice warn">${esc(d.pm2?.error || "PM2 unavailable")}</div>`}
      </div>
      <div class="card">
        <h2>Maintenance Links</h2>
        ${(d.advanced?.maintenance_links || []).map((link) => `<div class="inline-actions"><span>Endpoint</span><code>${esc(link)}</code></div>`).join("")}
      </div>
    </div>
    <details class="advanced-collapse">
      <summary class="advanced-summary"><span class="pill warn">RAW</span> Raw Health JSON</summary>
      <div class="advanced-content"><pre style="white-space:pre-wrap;overflow:auto">${esc(JSON.stringify(d.advanced?.raw_health || d, null, 2))}</pre></div>
    </details>
  `;
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
        ${can("manage_bot_config") ? `<button class="btn primary" id="qaRestartBots">🔄 Restart Bots</button>` : ""}
        ${can("manage_radio") ? `<button class="btn cyan" id="qaToggleRequests">${r.queue_open ? "🚫 Close Requests" : "✅ Open Requests"}</button>` : ""}
        ${can("manage_room") || can("manage_events") ? `<button class="btn" id="qaAnnouncement">📣 Queue Announcement</button>` : ""}
        ${can("view_logs") ? `<button class="btn" data-admin-page="Operations Center" data-admin-tab="Alerts">🩺 View Alerts</button>` : ""}
        ${state.user?.role === "owner" ? `<button class="btn" id="qaCreateBackup">🧰 Create Backup</button>` : ""}
        ${can("manage_players") ? `<button class="btn" data-admin-page="Players" data-admin-tab="Search">🔎 Search Player</button>` : ""}
        <button class="btn" data-admin-page="Radio">📻 Radio Controls</button>
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
    ${tab === "Bot Audit"    ? renderBotAuditTab() : ""}
    ${tab === "Queued Commands" ? renderQueuedCommandsTab() : ""}
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

function renderBotAuditTab() {
  const d = state.data || {};
  const rawRows = d.raw_bot_instances || [];
  const cleanupPreview = d.cleanup_preview || [];
  const canonical = d.canonical_bots || d.bots || [];
  const summary = d.summary || {};
  return `
    <div class="card">
      <div class="card-header" style="margin-bottom:10px">
        <h2>Bot Audit</h2>
        <span class="pill info">Read-only</span>
      </div>
      <div class="audit-summary-grid">
        ${auditStat("Canonical Bots", summary.canonical_count ?? canonical.length ?? 0, "expected accounts")}
        ${auditStat("Raw Rows", summary.raw_count ?? rawRows.length ?? 0, "bot_instances")}
        ${auditStat("Merged Rows", summary.merged_count ?? 0, "aliases / duplicates")}
        ${auditStat("Cleanup Preview", cleanupPreview.length, "no deletes exposed")}
      </div>
      ${table(canonical, [
        { key: "display_name", label: "Canonical Bot" },
        { key: "bot_username", label: "Username" },
        { key: "bot_mode", label: "Mode" },
        { key: "status", label: "Status", render: (r) => pill(r.status || "unknown") },
        { key: "raw_row_count", label: "Raw Rows" },
        { key: "source_bot_mode", label: "Source Mode" },
      ])}
    </div>
    <div class="card">
      <h2>Cleanup Preview</h2>
      ${cleanupPreview.length ? table(cleanupPreview, [
        { key: "action", label: "Preview Action" },
        { key: "canonical_username", label: "Canonical Bot" },
        { key: "bot_mode", label: "Raw Mode" },
        { key: "bot_username", label: "Raw Username" },
        { key: "reason", label: "Reason" },
      ]) : `<div class="notice">No alias, duplicate, or debug-only rows detected.</div>`}
    </div>
  `;
}

function renderQueuedCommandsTab() {
  return `<div class="card">
    <div class="card-header">
      <h2>Queued Bot Commands</h2>
      <span class="pill def">Read-only</span>
    </div>
    ${queueHelp()}
    ${renderQueuedBotCommands(state.data || {})}
  </div>`;
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
          { key: "claimed_by", label: "Claimed By" },
          { key: "created_at", label: "Created" },
          { key: "error_text", label: "Error" },
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
          { key: "claimed_by", label: "Claimed By" },
          { key: "completed_at", label: "Completed" },
          { key: "result_text", label: "Result" },
          { key: "error_text", label: "Error" },
        ]) : `<div class="notice">No recent bot commands.</div>`}
      </div>
    </div>
  `;
}

/* ── Emotes Owner Page ───────────────────────────────── */
const EMOTE_BOT_TARGETS = [
  ["dj", "DJ_DUDU"],
  ["host", "ChillTopiaMC"],
  ["security", "KeanuShield"],
  ["blackjack", "AceSinatra"],
  ["poker", "ChipSoprano"],
  ["miner", "GreatestProspector"],
  ["fisher", "MasterAngler"],
  ["banker", "BankingBot"],
];

function botTargetOptions(selected = "dj") {
  return EMOTE_BOT_TARGETS.map(([mode, name]) =>
    `<option value="${esc(mode)}" ${selected === mode ? "selected" : ""}>${esc(name)} (${esc(mode)})</option>`).join("");
}

function renderEmotesOwnerPage(tab) {
  const d = state.data || {};
  return `
    ${tabNav("Emotes")}
    ${tab === "Overview" ? renderEmotesOverview(d) : ""}
    ${tab === "Emote Registry" ? renderEmoteRegistry(d) : ""}
    ${tab === "Bot Emotes" ? renderBotEmotes(d) : ""}
    ${tab === "Custom Packs" ? renderCustomPacks(d) : ""}
    ${tab === "Dancefloor" ? renderDancefloorOwner(d) : ""}
    ${tab === "Sync" ? renderSyncOwner(d) : ""}
    ${tab === "Social / Hearts" ? renderSocialHearts(d) : ""}
    ${tab === "Logs" ? renderEmoteLogs(d) : ""}
    ${tab === "Advanced" ? renderEmotesAdvanced(d) : ""}
  `;
}

function renderEmotesOverview(d) {
  const o = d.overview || {};
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr))">
      ${metricCard("Registry", o.registry_count ?? 0, "emotes.json + custom", "accent-cyan", "🎭")}
      ${metricCard("Bot Loops", o.active_bot_emotes ?? 0, "persistent bot emotes", o.active_bot_emotes ? "accent-green" : "", "🤖")}
      ${metricCard("Dancefloor", o.dancefloor_status || "unknown", "room_settings.dancefloor_active", o.dancefloor_status === "active" ? "accent-green" : "", "💃")}
      ${metricCard("Sync Active", o.sync_active_count ?? 0, "sync_relations active", "accent-cyan", "🔄")}
      ${metricCard("Custom Packs", o.custom_packs_count ?? 0, "custom + dancefloor packs", "", "📦")}
      ${metricCard("Social / Hearts", boolLabel(o.heart_social_status), "room social settings", truthy(o.heart_social_status) ? "accent-green" : "", "💖")}
    </div>
    <div class="grid">
      <div class="card"><h2>Queued Emote Commands</h2>${renderQueuedBotCommands(d.command_queue || {})}</div>
      <div class="card"><h2>Recent Emote Audit</h2>${table(d.audit_logs || [], [
        { key: "created_at", label: "Time" },
        { key: "actor", label: "Actor" },
        { key: "action_type", label: "Action", render: (r) => pill(r.action_type || "audit") },
        { key: "target_type", label: "Target" },
      ])}</div>
    </div>`;
}

function renderEmoteRegistry(d) {
  const rows = d.registry || [];
  return `<div class="card">
    <div class="card-header"><h2>Emote Registry</h2><span class="pill info">READ ONLY</span></div>
    ${renderResourceToolbar({ search: "Search emotes", rarity: false, enabled: false })}
    ${table(rows.slice(0, 300), [
      { key: "alias", label: "Alias" },
      { key: "name", label: "Name" },
      { key: "emote_id", label: "Emote ID" },
      { key: "category", label: "Category" },
      { key: "duration", label: "Time" },
      { key: "bot", label: "Bot" },
      { key: "player", label: "Player" },
      { key: "source", label: "Source" },
    ])}
    ${rows.length > 300 ? `<div class="notice" style="margin-top:12px">Showing first 300 of ${rows.length} registry entries. Use search after load to narrow the visible set.</div>` : ""}
    ${futureControls([
      { endpoint: "POST /api/emotes/registry", purpose: "Add/edit registry entries only after file persistence is safely shared with bot registry", status: "Future" },
    ])}
  </div>`;
}

function renderBotEmotes(d) {
  return `<div class="grid">
    <div class="card">
      <h2>Queue Bot Emote</h2>
      <form id="emoteCommandForm" class="settings-form">
        <select name="target_bot">${botTargetOptions("dj")}</select>
        <input name="emote" placeholder="emote alias" required />
        <input name="duration" type="number" min="0" max="120" placeholder="duration seconds optional" />
        <button class="btn primary">Queue Emote</button>
      </form>
      ${queueHelp()}
    </div>
    <div class="card">
      <h2>Persistent Bot Loop</h2>
      <form id="botEmoteSetForm" class="settings-form">
        <select name="target_bot">${botTargetOptions("dj")}</select>
        <input name="emote" placeholder="emote alias" required />
        <button class="btn primary">Set Persistent Bot Emote</button>
      </form>
      <form id="botEmoteStopForm" class="toolbar" style="margin-top:12px">
        <select name="target_bot">${botTargetOptions("dj")}</select>
        <button class="btn danger">Stop Persistent Bot Emote</button>
      </form>
      ${queueHelp()}
    </div>
    <div class="card"><h2>Active Bot Emotes</h2>${table(d.bot_emotes || [], [
      { key: "bot", label: "Bot" },
      { key: "current_emote", label: "Current Emote" },
      { key: "loop_status", label: "Status", render: (r) => pill(r.loop_status || "stopped", ["active"]) },
      { key: "persistent", label: "Persistent", render: (r) => pill(r.persistent || "no", ["yes"]) },
      { key: "source", label: "Source" },
    ])}</div>
    <div class="card"><h2>Room Emote Loops</h2>${table(d.room_emote_loops?.rows || d.tables?.room_emote_loops?.rows || [])}</div>
  </div>`;
}

function renderCustomPacks(d) {
  const customRows = d.custom_packs || d.tables?.custom_emote_packs?.rows || [];
  const danceRows = d.dancefloor_packs || d.tables?.dancefloor_packs?.rows || [];
  return `<div class="grid">
    <div class="card"><div class="card-header"><h2>Custom Emote Packs</h2><span class="pill info">READ ONLY</span></div>${table(customRows)}</div>
    <div class="card"><div class="card-header"><h2>Dancefloor Packs</h2><span class="pill info">READ ONLY</span></div>${table(danceRows)}</div>
  </div>
  ${futureControls([
    { endpoint: "POST/PUT/DELETE /api/emotes/packs", purpose: "Pack writes need safe owner-scoped schema and confirmation", status: "Future" },
  ])}`;
}

function renderDancefloorOwner(d) {
  const df = d.dancefloor || {};
  return `<div class="grid">
    <div class="card">
      <div class="card-header"><h2>Dancefloor Status</h2>${pill(df.status || "unknown", ["active"])}</div>
      ${table([
        { label: "Box", value: df.box || "unset" },
        { label: "Point 1", value: df.p1 || "unset" },
        { label: "Point 2", value: df.p2 || "unset" },
        { label: "Mode", value: df.mode || "simple/legacy" },
        { label: "Emotes", value: df.emotes || "none" },
      ], [{ key: "label", label: "Field" }, { key: "value", label: "Value" }])}
    </div>
    <div class="card">
      <h2>Queue Dancefloor Command</h2>
      <div class="inline-actions" style="margin-bottom:12px">
        <button class="btn primary" data-dancefloor-command="start">Queue Start</button>
        <button class="btn danger" data-dancefloor-command="stop">Queue Stop</button>
        <button class="btn" data-dancefloor-command="status">Queue Status</button>
        <button class="btn danger" data-dancefloor-command="clear">Queue Clear</button>
      </div>
      <form id="dancefloorSequenceForm" class="settings-form">
        <input name="emotes" placeholder="emote aliases, comma or space separated" required />
        <button class="btn primary sm">Queue Sequence</button>
      </form>
      <form id="dancefloorRandomForm" class="toolbar" style="margin-top:12px;flex-wrap:wrap">
        <input name="count" type="number" min="1" placeholder="random count" />
        <button class="btn sm">Queue Random</button>
      </form>
      <form id="dancefloorRandomTimedForm" class="toolbar" style="margin-top:12px;flex-wrap:wrap">
        <input name="count" placeholder="count or all" value="all" />
        <input name="min_seconds" type="number" min="0.5" step="0.5" placeholder="min sec" required />
        <input name="max_seconds" type="number" min="0.5" step="0.5" placeholder="max sec optional" />
        <button class="btn sm">Queue Random Timed</button>
      </form>
      ${queueHelp()}
    </div>
    <div class="card"><h2>Saved Dancefloor Packs</h2>${table(df.packs || [])}</div>
  </div>`;
}

function renderSyncOwner(d) {
  const sync = d.sync || {};
  return `<div class="grid">
    <div class="card">
      <div class="card-header"><h2>Sync Relations</h2><span class="pill ${truthy(sync.enabled) ? "ok" : "def"}">${esc(boolLabel(sync.enabled))}</span></div>
      ${table(sync.all || [], [
        { key: "follower_username", label: "Follower" },
        { key: "leader_username", label: "Leader" },
        { key: "is_active", label: "Active", render: (r) => pill(String(r.is_active) === "1" ? "ACTIVE" : "STOPPED", ["ACTIVE"]) },
        { key: "persist_enabled", label: "Persistent", render: (r) => pill(String(r.persist_enabled) === "1" ? "PERSISTENT" : "off", ["PERSISTENT"]) },
        { key: "updated_at", label: "Updated" },
      ])}
    </div>
    <div class="card">
      <h2>Queue Sync Controls</h2>
      <form id="syncStartForm" class="settings-form">
        <input name="leader" placeholder="leader username for sync all" required />
        <button class="btn primary">Queue Sync All To Leader</button>
      </form>
      <div class="inline-actions" style="margin-top:12px">
        <button class="btn danger" data-sync-command="stop">Queue Stop All Sync</button>
        <button class="btn" data-sync-persist="true">Queue Persist On</button>
        <button class="btn" data-sync-persist="false">Queue Persist Off</button>
      </div>
      ${queueHelp()}
    </div>
  </div>`;
}

function renderSocialHearts(d) {
  const social = d.social || {};
  return `<div class="grid">
    <div class="card"><div class="card-header"><h2>Heart / Social Settings</h2><span class="pill info">READ ONLY</span></div>${table(social.heart_settings || [])}</div>
    <div class="card"><h2>Heart Totals</h2>${table(social.totals || [])}</div>
    <div class="card"><h2>Recent Hearts</h2>${table(social.hearts || [])}</div>
    <div class="card"><h2>Social Logs</h2>${table(social.logs || [])}</div>
  </div>
  ${futureControls([
    { endpoint: "heart/social limit writes", purpose: "Needs exact active source mapping before normal controls", status: "UNVERIFIED" },
  ])}`;
}

function renderEmoteLogs(d) {
  return `<div class="grid">
    <div class="card"><h2>Emote Audit Logs</h2>${table(d.audit_logs || [])}</div>
    <div class="card"><h2>Queued Emote Commands</h2>${renderQueuedBotCommands(d.command_queue || {})}</div>
    <div class="card"><h2>Room Social Logs</h2>${table(d.room_social_logs?.rows || d.tables?.room_social_logs?.rows || [])}</div>
  </div>`;
}

function renderEmotesAdvanced(d) {
  return `<div class="grid">
    <div class="card"><h2>Raw Files</h2>${table(d.raw_files || [])}</div>
    <div class="card"><h2>Active Emotes Table</h2>${table(d.tables?.active_emotes?.rows || [])}</div>
    <div class="card"><h2>Favorite Emotes</h2>${table(d.tables?.fav_emotes?.rows || [])}</div>
    <div class="card"><h2>Custom Loop Sessions</h2>${table(d.tables?.custom_loop_sessions?.rows || [])}</div>
  </div>
  ${futureControls([
    { endpoint: "hard delete emote packs", purpose: "Not exposed by default; requires typed confirmation", status: "Hidden" },
    { endpoint: "edit JSON registry", purpose: "Needs shared registry write helper", status: "Future" },
  ])}`;
}

/* ── Players Page ────────────────────────────────────── */
function renderOwnerPlayersPage(tab) {
  return `
    ${tabNav("Players")}
    ${tab === "Search" ? renderPlayerSearch() : ""}
    ${tab === "Economy" ? renderPlayerEconomyTab() : ""}
    ${tab === "Inventory" ? renderPlayerInventoryTab() : ""}
    ${tab === "Mining" ? renderPlayerMiningTab() : ""}
    ${tab === "Fishing" ? renderPlayerFishingTab() : ""}
    ${tab === "Titles & Badges" ? renderPlayerTitlesBadgesTab() : ""}
    ${tab === "Moderation" ? renderPlayerModerationTab() : ""}
    ${tab === "Logs" ? renderPlayerLogsTab() : ""}
  `;
}

function selectedPlayerNotice() {
  return `<div class="card"><div class="notice">Search for a player first on the Search tab.</div></div>`;
}

function renderPlayerSearch() {
  const p = state.playerResult;
  return `
    <div class="card">
      <h2>🔍 Player Search</h2>
      <form id="playerSearchForm" class="toolbar" style="flex-wrap:wrap">
        <input name="query" placeholder="Username or User ID" required style="flex:1;min-width:200px" />
        <button class="btn primary">Search</button>
      </form>
      <div id="playerSearchResult" style="margin-top:16px"></div>
    </div>
    ${p ? renderPlayerCard(p) : ""}
  `;
}

function renderPlayerCard(p) {
  const items = p.owned_items || [];
  const s = p.summaries || {};
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
      ${metricCard("VIP", s.is_vip ? "Yes" : "No", "owned_items", s.is_vip ? "accent-green" : "", "👑")}
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
      <button class="btn sm" data-player-jump="Economy">Edit Economy</button>
      <button class="btn sm" data-player-jump="Inventory">Edit Inventory</button>
      <button class="btn sm" data-player-jump="Titles & Badges">Edit Badge / Title</button>
    </div>
  </div>`;
}

function renderPlayerEconomyTab() {
  const p = state.playerResult;
  if (!p) return selectedPlayerNotice();
  return `
    <div class="card">
      <div class="card-header"><h2>Economy — ${esc(p.username)}</h2><span class="pill warn">Owner writes</span></div>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-bottom:12px">
        ${metricCard("Balance", Number(p.balance || 0).toLocaleString(), "users.balance", "accent-green", "💰")}
        ${metricCard("XP", Number(p.xp || 0).toLocaleString(), "users.xp", "", "📈")}
        ${metricCard("Level", p.level ?? 1, "users.level", "", "⭐")}
      </div>
      ${state.user?.role === "owner" ? `
        <form id="playerEconomyForm" class="settings-form">
          <div class="field"><label class="field-label">Action</label><select name="action">
            <option value="add_balance">Add Coins</option>
            <option value="remove_balance">Remove Coins</option>
            <option value="set_balance">Set Balance</option>
            <option value="add_xp">Add XP</option>
            <option value="remove_xp">Remove XP</option>
            <option value="set_xp">Set XP</option>
            <option value="set_level">Set Level</option>
          </select></div>
          <div class="field"><label class="field-label">Amount</label><input type="number" name="amount" required /></div>
          <div class="field"><label class="field-label">Reason</label><input name="reason" required placeholder="Required audit reason" /></div>
          <button class="btn primary">Apply Economy Change</button>
        </form>` : `<div class="notice">Owner role required for economy writes.</div>`}
    </div>
    <div class="card">
      <h2>Recent Economy Activity</h2>
      ${table(p.recent_activity?.ledger || [], [
        { key: "timestamp", label: "Time" },
        { key: "change_amount", label: "Change" },
        { key: "balance_before", label: "Before" },
        { key: "balance_after", label: "After" },
        { key: "reason", label: "Reason" },
      ])}
    </div>
  `;
}

function renderPlayerInventoryTab() {
  const p = state.playerResult;
  if (!p) return selectedPlayerNotice();
  return `<div class="grid">
    <div class="card">
      <h2>Owned Items</h2>
      ${table(p.owned_items || [], [
        { key: "item_type", label: "Type" },
        { key: "item_id", label: "Item ID" },
      ], state.user?.role === "owner" ? (r) => `<button class="btn danger sm" data-remove-item="${esc(r.item_id)}">Remove</button>` : null)}
    </div>
    <div class="card">
      <h2>Add Item</h2>
      ${state.user?.role === "owner" ? `<form id="playerItemForm" class="settings-form">
        <div class="field"><label class="field-label">Item ID</label><input name="item_id" required /></div>
        <div class="field"><label class="field-label">Item Type</label><input name="item_type" required placeholder="vip, rod, pickaxe, badge, item" /></div>
        <div class="field"><label class="field-label">Reason</label><input name="reason" required placeholder="Required audit reason" /></div>
        <button class="btn primary">Add Item</button>
      </form>
      <div class="inline-actions" style="margin-top:12px">
        <button class="btn sm" data-quick-item="vip" data-quick-type="vip">Add VIP</button>
        <button class="btn danger sm" data-remove-item="vip">Remove VIP</button>
      </div>` : `<div class="notice">Owner role required for inventory writes.</div>`}
    </div>
  </div>`;
}

function renderPlayerMiningTab() {
  const p = state.playerResult;
  if (!p) return selectedPlayerNotice();
  return `<div class="card"><h2>Mining Inventory</h2>${table(p.mining_inventory || [])}</div>`;
}

function renderPlayerFishingTab() {
  const p = state.playerResult;
  if (!p) return selectedPlayerNotice();
  return `<div class="card"><h2>Fishing Inventory</h2>${table(p.fishing_inventory || [])}</div>`;
}

function renderPlayerTitlesBadgesTab() {
  const p = state.playerResult;
  if (!p) return selectedPlayerNotice();
  return `<div class="grid">
    <div class="card">
      <h2>Titles</h2>
      ${table([...(p.titles?.user_titles || []), ...(p.titles?.player_titles || [])], [
        { key: "title_id", label: "Title ID" },
        { key: "display", label: "Display" },
        { key: "source", label: "Source" },
        { key: "unlocked_at", label: "Unlocked" },
      ], state.user?.role === "owner" ? (r) => r.title_id ? `<button class="btn danger sm" data-remove-title="${esc(r.title_id)}">Remove</button>` : "" : null)}
      ${state.user?.role === "owner" ? `<form id="playerTitleForm" class="toolbar" style="margin-top:12px"><input name="title_id" required placeholder="title_id" /><input name="reason" required placeholder="reason" /><button class="btn primary">Give Title</button></form>` : ""}
    </div>
    <div class="card">
      <h2>Badges</h2>
      ${table(p.badges?.user_badges || [], [
        { key: "badge_id", label: "Badge ID" },
        { key: "source", label: "Source" },
        { key: "equipped", label: "Equipped" },
        { key: "locked", label: "Locked" },
      ], state.user?.role === "owner" ? (r) => `<button class="btn danger sm" data-remove-badge="${esc(r.badge_id)}">Remove</button>` : null)}
      ${state.user?.role === "owner" ? `<form id="playerBadgeForm" class="toolbar" style="margin-top:12px"><input name="badge_id" required placeholder="badge_id" /><input name="reason" required placeholder="reason" /><button class="btn primary">Give Badge</button></form>` : ""}
    </div>
  </div>`;
}

function renderPlayerModerationTab() {
  const p = state.playerResult;
  if (!p) return selectedPlayerNotice();
  const canModerate = state.user?.role === "owner" || can("manage_moderation") || can("emergency_controls");
  return `<div class="grid">
    <div class="card">
      <h2>Warnings</h2>
      ${table(p.moderation?.warnings || [])}
    </div>
    <div class="card">
      <h2>Mutes</h2>
      ${table(p.moderation?.mutes || [])}
    </div>
    <div class="card">
      <h2>Reports</h2>
      ${table(p.moderation?.reports || [])}
    </div>
    <div class="card">
      <h2>Moderation Actions</h2>
      ${canModerate ? `<form id="playerModerationForm" class="settings-form">
        <div class="field"><label class="field-label">Action</label><select name="action"><option value="warn">Warn</option><option value="mute">Mute</option><option value="unmute">Unmute</option></select></div>
        <div class="field"><label class="field-label">Mute Minutes</label><input type="number" name="minutes" value="60" /></div>
        <div class="field"><label class="field-label">Reason</label><input name="reason" required /></div>
        <button class="btn primary">Apply Moderation Action</button>
      </form>` : `<div class="notice">Moderation permission required.</div>`}
    </div>
  </div>`;
}

function renderPlayerLogsTab() {
  const p = state.playerResult;
  if (!p) return selectedPlayerNotice();
  return `<div class="grid">
    <div class="card"><h2>Ledger</h2>${table(p.recent_activity?.ledger || [])}</div>
    <div class="card"><h2>Economy Transactions</h2>${table(p.recent_activity?.economy_transactions || [])}</div>
    <div class="card"><h2>Bank Transactions</h2>${table(p.recent_activity?.bank_transactions || [])}</div>
    <div class="card"><h2>Moderation Logs</h2>${table(p.moderation?.logs || [])}</div>
  </div>`;
}

/* ── Security / Moderation ───────────────────────────── */
function renderSecurityPage(tab, opts = {}) {
  const pageName = opts.staff ? "Moderation" : "Security";
  return `
    ${tabNav(pageName)}
    ${tab === "Overview" ? renderSecurityOverview() : ""}
    ${tab === "Reports" ? renderSecurityReports() : ""}
    ${tab === "Warnings" ? renderSecurityWarnings() : ""}
    ${tab === "Mutes" ? renderSecurityMutes() : ""}
    ${tab === "Bans / Jail" ? renderSecurityBansJail() : ""}
    ${tab === "Player Lookup" ? renderSecurityPlayerLookup() : ""}
    ${tab === "Security Bot" ? renderSecurityBot() : ""}
    ${tab === "Logs" ? renderSecurityLogs() : ""}
    ${tab === "Advanced" ? renderSecurityAdvanced() : ""}
  `;
}

function securityCanWrite() {
  return state.user?.role === "owner" || can("manage_moderation") || can("emergency_controls");
}

function renderSecurityOverview() {
  const d = state.data || {};
  const o = d.overview || {};
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(170px,1fr))">
      ${metricCard("Open Reports", o.open_reports ?? 0, "reports needing review", o.open_reports ? "accent-red" : "accent-green", "R")}
      ${metricCard("Active Mutes", o.active_mutes ?? 0, "mutes table", o.active_mutes ? "accent-red" : "accent-green", "M")}
      ${metricCard("Recent Warnings", o.recent_warnings ?? 0, "warning rows", "", "W")}
      ${metricCard("Bans / Jail", o.active_bans_or_jail ?? 0, "if supported", o.active_bans_or_jail ? "accent-red" : "", "J")}
      ${metricCard("Security Bot", o.security_bot_status || "missing", "KeanuShield", o.security_bot_status === "online" ? "accent-green" : "accent-red", "S")}
      ${metricCard("Failed Commands", o.failed_moderation_commands ?? 0, "security queue", o.failed_moderation_commands ? "accent-red" : "accent-green", "!")}
    </div>
    <div class="grid">
      <div class="card"><h2>Recent Moderation Actions</h2>${table(d.tables?.moderation_logs?.rows || [])}</div>
      <div class="card"><h2>Failed / Recent Security Commands</h2>${table(d.command_queue?.recent || [])}</div>
    </div>
  `;
}

function renderSecurityReports() {
  const d = state.data || {};
  const rows = d.reports?.rows || d.tables?.reports?.rows || [];
  return `
    <div class="card">
      <h2>Reports</h2>
      ${table(rows, [
        { key: "id", label: "ID" },
        { key: "reporter_username", label: "Reporter" },
        { key: "target_username", label: "Target" },
        { key: "report_type", label: "Type" },
        { key: "reason", label: "Reason" },
        { key: "status", label: "Status", render: (r) => pill(r.status || "open") },
        { key: "handled_by", label: "Handled By" },
      ], securityCanWrite() ? (r) => `<button class="btn sm" data-report-review="${esc(r.id)}">Reviewing</button><button class="btn primary sm" data-report-resolve="${esc(r.id)}">Resolve</button>` : null)}
    </div>
  `;
}

function renderSecurityWarnings() {
  const d = state.data || {};
  const warnings = d.warnings?.rows || d.tables?.warnings?.rows || [];
  const roomWarnings = d.room_warnings?.rows || d.tables?.room_warnings?.rows || [];
  return `
    <div class="grid">
      <div class="card">
        <h2>Issue Warning</h2>
        ${securityCanWrite() ? `<form id="securityWarnForm" class="settings-form">
          <div class="field"><label class="field-label">Username or User ID</label><input name="query" required /></div>
          <div class="field"><label class="field-label">Reason</label><input name="reason" required /></div>
          <button class="btn primary">Issue Warning</button>
        </form>` : `<div class="notice">Requires moderation permission.</div>`}
      </div>
      <div class="card"><h2>Warnings</h2>${table(warnings)}</div>
      <div class="card"><h2>Room Warnings</h2>${table(roomWarnings)}</div>
    </div>
  `;
}

function renderSecurityMutes() {
  const d = state.data || {};
  const rows = d.mutes?.rows || d.tables?.mutes?.rows || [];
  return `
    <div class="grid">
      <div class="card">
        <h2>Mute Player</h2>
        ${securityCanWrite() ? `<form id="securityMuteForm" class="settings-form">
          <div class="field"><label class="field-label">Username or User ID</label><input name="query" required /></div>
          <div class="field"><label class="field-label">Minutes</label><input name="minutes" type="number" min="1" max="10080" value="60" /></div>
          <div class="field"><label class="field-label">Reason</label><input name="reason" required /></div>
          <button class="btn primary">Mute Player</button>
        </form>` : `<div class="notice">Requires moderation permission.</div>`}
      </div>
      <div class="card">
        <h2>Active / Recent Mutes</h2>
        ${table(rows, null, securityCanWrite() ? (r) => {
          const target = r.user_id || r.username || r.target_id || "";
          return target ? `<button class="btn danger sm" data-security-unmute="${esc(target)}">Unmute</button>` : "";
        } : null)}
      </div>
    </div>
  `;
}

function renderSecurityBansJail() {
  const d = state.data || {};
  return `
    <div class="grid">
      <div class="card"><h2>Room Bans</h2>${table(d.tables?.room_bans?.rows || [])}</div>
      <div class="card"><h2>Jail Sentences</h2>${table(d.tables?.jail_sentences?.rows || [])}</div>
    </div>
    ${futureControls([
      { endpoint: "POST /api/security/bans", purpose: "Ban/unban requires verified authoritative table or security bot support", status: "Unverified" },
      { endpoint: "POST /api/security/jail", purpose: "Jail/unjail requires verified bot-side support", status: "Unverified" },
    ], "Bans / Jail Actions")}
  `;
}

function renderSecurityPlayerLookup() {
  const p = state.securityPlayer?.player;
  return `
    <div class="card">
      <h2>Player Moderation Lookup</h2>
      <form id="securityPlayerLookupForm" class="toolbar" style="flex-wrap:wrap">
        <input name="query" required placeholder="username or user_id" style="flex:1;min-width:180px" />
        <button class="btn primary">Search</button>
      </form>
    </div>
    ${p ? `<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(170px,1fr))">
      ${metricCard("Player", p.username || p.user_id, p.user_id, "accent-cyan", "P")}
      ${metricCard("Warnings", p.moderation?.warnings_count ?? p.moderation?.warnings?.length ?? 0, "warnings", "", "W")}
      ${metricCard("Mutes", p.moderation?.mutes_count ?? p.moderation?.mutes?.length ?? 0, "mutes", "", "M")}
      ${metricCard("Reports", p.moderation?.reports_count ?? p.moderation?.reports?.length ?? 0, "reports", "", "R")}
      ${metricCard("Bans", state.securityPlayer?.bans?.length ?? 0, "room_bans", "", "B")}
      ${metricCard("Jail", state.securityPlayer?.jail?.length ?? 0, "jail_sentences", "", "J")}
    </div>
    <div class="grid">
      <div class="card">
        <h2>Actions</h2>
        ${securityCanWrite() ? `<form id="securityPlayerActionForm" class="settings-form">
          <input type="hidden" name="query" value="${esc(p.user_id || p.username)}" />
          <div class="field"><label class="field-label">Action</label><select name="action"><option value="warn">Warn</option><option value="mute">Mute</option><option value="unmute">Unmute</option></select></div>
          <div class="field"><label class="field-label">Minutes</label><input name="minutes" type="number" min="1" max="10080" value="60" /></div>
          <div class="field"><label class="field-label">Reason</label><input name="reason" required /></div>
          <button class="btn primary">Apply Action</button>
        </form>` : `<div class="notice">Requires moderation permission.</div>`}
      </div>
      <div class="card"><h2>Warnings</h2>${table(p.moderation?.warnings || [])}</div>
      <div class="card"><h2>Mutes</h2>${table(p.moderation?.mutes || [])}</div>
      <div class="card"><h2>Reports</h2>${table(p.moderation?.reports || [])}</div>
      <div class="card"><h2>Jail / Bans</h2>${table([...(state.securityPlayer?.jail || []), ...(state.securityPlayer?.bans || [])])}</div>
      <div class="card"><h2>Moderation Logs</h2>${table(p.moderation?.logs || [])}</div>
    </div>` : ""}
  `;
}

function renderSecurityBot() {
  const d = state.data || {};
  return `
    <div class="grid">
      <div class="card">
        <h2>KeanuShield Status</h2>
        ${d.security_bot ? table([d.security_bot]) : `<div class="notice warn">Security bot row not found.</div>`}
      </div>
      <div class="card">
        <h2>Queue Security Alert</h2>
        ${securityCanWrite() ? `<form id="securityAlertForm" class="settings-form">
          <div class="field"><label class="field-label">Message</label><input name="message" required maxlength="240" /></div>
          <button class="btn primary">Queue Security Alert</button>
        </form>
        <div class="toolbar" style="margin-top:12px">
          <button class="btn sm" data-security-bot-action="return_home">Queue Return Home</button>
          <button class="btn sm" data-security-bot-action="stop_emote">Queue Stop Emote</button>
        </div>
        ${queueHelp()}` : `<div class="notice">Requires moderation permission.</div>`}
      </div>
    </div>
    <div class="card"><h2>Security Bot Queue</h2>${table(d.command_queue?.recent || [])}</div>
  `;
}

function renderSecurityLogs() {
  const d = state.data || {};
  return `
    <div class="card"><h2>Moderation Logs</h2>${table(d.moderation_logs?.rows || d.tables?.moderation_logs?.rows || [])}</div>
    <div class="card"><h2>Audit Logs</h2>${table(d.audit_logs?.rows || d.tables?.audit_logs?.rows || [])}</div>
    <div class="card"><h2>Admin Action Logs</h2>${table(d.admin_action_logs?.rows || d.tables?.admin_action_logs?.rows || [])}</div>
    <div class="card"><h2>Security Command Queue</h2>${table(d.command_queue?.recent || [])}</div>
  `;
}

function renderSecurityAdvanced() {
  const d = state.data || {};
  return `
    ${futureControls(d.missing_endpoints || [], "Unverified / Owner-only Moderation Controls")}
    <details class="advanced-collapse">
      <summary class="advanced-summary"><span class="pill warn">RAW</span> Raw Moderation Tables</summary>
      <div class="advanced-content">
        ${["reports","warnings","room_warnings","mutes","room_bans","jail_sentences","moderation_logs"].map((name) =>
          `<h3>${esc(name)}</h3>${table(d.tables?.[name]?.rows || [])}`
        ).join("")}
      </div>
    </details>
  `;
}

/* ── Leaderboards ────────────────────────────────────── */
function renderLeaderboardsPage(tab, opts = {}) {
  return `
    ${tabNav("Leaderboards")}
    ${tab === "Overview" ? renderLeaderboardsOverview() : ""}
    ${tab === "Richest" ? renderLeaderboardGroup("Richest", [
      ["!toprich Richest Players", "richest", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "balance", label: "Balance" }]],
    ]) : ""}
    ${tab === "XP / Level" ? renderLeaderboardGroup("XP / Level", [
      ["Top XP / Level", "xp", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "level", label: "Level" }, { key: "xp", label: "XP" }]],
      ["Top Level", "level", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "level", label: "Level" }, { key: "xp", label: "XP" }]],
      ["Most Games Won", "most_games_won", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "wins", label: "Wins" }, { key: "total_won", label: "Total Won" }]],
    ]) : ""}
    ${tab === "Casino" ? renderLeaderboardGroup("Casino Rankings", [
      ["Casino Overall", "casino_overall", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "wins", label: "Wins" }, { key: "total_won", label: "Total Won" }]],
      ["Blackjack / RBJ", "blackjack", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "wins", label: "Wins" }, { key: "losses", label: "Losses" }, { key: "blackjacks", label: "Blackjacks" }, { key: "total_won", label: "Total Won" }, { key: "net", label: "Net" }]],
      ["Poker", "poker", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "wins", label: "Wins" }, { key: "hands_played", label: "Hands" }, { key: "total_won", label: "Total Won" }, { key: "net", label: "Net" }, { key: "biggest_pot", label: "Biggest Pot" }]],
    ]) : ""}
    ${tab === "Poker" ? renderLeaderboardGroup("Poker Rankings", [
      ["Poker", "poker", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "wins", label: "Wins" }, { key: "hands_played", label: "Hands" }, { key: "total_won", label: "Total Won" }, { key: "net", label: "Net" }, { key: "biggest_pot", label: "Biggest Pot" }]],
    ]) : ""}
    ${tab === "Blackjack" ? renderLeaderboardGroup("Blackjack Rankings", [
      ["Blackjack / RBJ", "blackjack", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "wins", label: "Wins" }, { key: "losses", label: "Losses" }, { key: "blackjacks", label: "Blackjacks" }, { key: "total_won", label: "Total Won" }, { key: "net", label: "Net" }]],
    ]) : ""}
    ${tab === "Mining" ? renderLeaderboardGroup("Mining Rankings", [
      ["!topminers Top Miners", "mining_top", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "level", label: "Level" }, { key: "xp", label: "XP" }, { key: "total_mined", label: "Total Mined" }, { key: "rare_finds", label: "Rare Finds" }, { key: "total_value", label: "Value" }, { key: "best_ore", label: "Best Ore" }]],
      ["Heaviest Ores", "mining_heaviest_ore", [{ key: "rank", label: "#" }, { key: "ore", label: "Ore" }, { key: "username", label: "Player" }, { key: "rarity", label: "Rarity" }, { key: "weight", label: "Weight" }, { key: "value", label: "Value" }]],
      ["Most Valuable Ores", "mining_most_valuable", [{ key: "rank", label: "#" }, { key: "ore", label: "Ore" }, { key: "username", label: "Player" }, { key: "rarity", label: "Rarity" }, { key: "weight", label: "Weight" }, { key: "value", label: "Value" }]],
      ["Rarest Ores", "mining_rarest", [{ key: "rank", label: "#" }, { key: "ore", label: "Ore" }, { key: "username", label: "Player" }, { key: "rarity", label: "Rarity" }, { key: "weight", label: "Weight" }, { key: "value", label: "Value" }]],
      ["Mining Streaks", "mining_streaks", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "level", label: "Level" }, { key: "xp", label: "XP" }, { key: "streak", label: "Streak" }]],
    ]) : ""}
    ${tab === "Fishing" ? renderLeaderboardGroup("Fishing Rankings", [
      ["!topfishers Top Fishers", "fishing_top", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "level", label: "Level" }, { key: "xp", label: "XP" }, { key: "total_catches", label: "Catches" }, { key: "biggest_catch", label: "Biggest" }, { key: "total_value", label: "Value" }]],
      ["Heaviest Fish", "fishing_heaviest_fish", [{ key: "rank", label: "#" }, { key: "fish", label: "Fish" }, { key: "username", label: "Player" }, { key: "rarity", label: "Rarity" }, { key: "weight", label: "Weight" }, { key: "value", label: "Value" }]],
      ["Most Valuable Fish", "fishing_most_valuable", [{ key: "rank", label: "#" }, { key: "fish", label: "Fish" }, { key: "username", label: "Player" }, { key: "rarity", label: "Rarity" }, { key: "weight", label: "Weight" }, { key: "value", label: "Value" }]],
      ["Rarest Fish", "fishing_rarest", [{ key: "rank", label: "#" }, { key: "fish", label: "Fish" }, { key: "username", label: "Player" }, { key: "rarity", label: "Rarity" }, { key: "weight", label: "Weight" }, { key: "value", label: "Value" }]],
      ["Fishing Streaks", "fishing_streaks", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "level", label: "Level" }, { key: "xp", label: "XP" }, { key: "streak", label: "Streak" }]],
    ]) : ""}
    ${tab === "Events" ? renderLeaderboardGroup("Event Rankings", [
      ["Event Points", "event_points", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "points", label: "Points" }]],
    ]) : ""}
    ${tab === "Radio" ? renderLeaderboardGroup("Radio Rankings", [
      ["Top Requesters", "radio_requesters", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "requests", label: "Requests" }]],
      ["Song Stats", "radio_tracks", [{ key: "rank", label: "#" }, { key: "title", label: "Title" }, { key: "artist", label: "Artist" }, { key: "plays", label: "Plays" }, { key: "requests", label: "Requests" }, { key: "likes", label: "Likes" }, { key: "dislikes", label: "Dislikes" }]],
      ["Liked Tracks", "radio_liked", [{ key: "rank", label: "#" }, { key: "name", label: "Track" }, { key: "likes", label: "Likes" }]],
      ["Disliked Tracks", "radio_disliked", [{ key: "rank", label: "#" }, { key: "name", label: "Track" }, { key: "dislikes", label: "Dislikes" }]],
    ]) : ""}
    ${tab === "Social / Reputation" ? renderLeaderboardGroup("Social / Reputation", [
      ["Reputation / Social", "reputation", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "rep_received", label: "Received" }, { key: "rep_given", label: "Given" }]],
    ]) : ""}
    ${tab === "Gold / Tips" ? renderLeaderboardGroup("Gold / Tips", [
      ["!topdonators Gold Supporters", "topdonators", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "total_gold", label: "Gold" }, { key: "entries", label: "Entries" }]],
      ["!toptippers P2P Senders", "toptippers", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "total_gold", label: "Gold" }, { key: "entries", label: "Entries" }]],
      ["!toptipped P2P Receivers", "toptipped", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "total_gold", label: "Gold" }, { key: "entries", label: "Entries" }]],
      ["Tip Transactions", "tip_transactions", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "total_gold", label: "Gold" }, { key: "entries", label: "Entries" }]],
    ]) : ""}
    ${tab === "Streaks" ? renderLeaderboardGroup("Streaks", [
      ["!topstreaks Daily Claim Streaks", "streaks", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "streak", label: "Best Streak" }, { key: "total_claims", label: "Claims" }]],
      ["Mining Streaks", "mining_streaks", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "level", label: "Level" }, { key: "streak", label: "Streak" }]],
      ["Fishing Streaks", "fishing_streaks", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "level", label: "Level" }, { key: "streak", label: "Streak" }]],
    ]) : ""}
    ${tab === "Profiles" ? renderLeaderboardGroup("Profiles", [
      ["!profile Public-Safe Profile Stats", "profiles", [{ key: "rank", label: "#" }, { key: "username", label: "Username" }, { key: "level", label: "Level" }, { key: "xp", label: "XP" }, { key: "balance", label: "Balance" }, { key: "games_won", label: "Games Won" }]],
    ]) : ""}
    ${tab === "Diagnostics" ? renderLeaderboardDiagnostics() : ""}
  `;
}

function renderLeaderboardsOverview() {
  const d = state.data || {};
  const lb = d.leaderboards || d;
  const meta = d.diagnostics || d.metadata || {};
  const sections = [
    ["Richest Players", "richest", "💰", "balance"],
    ["Top XP", "xp", "⭐", "xp"],
    ["Daily Streaks", "streaks", "🔥", "streak"],
    ["Gold Supporters", "topdonators", "🥇", "total_gold"],
    ["Blackjack", "blackjack", "🃏", "wins"],
    ["Poker", "poker", "♠️", "wins"],
    ["Mining", "mining_top", "⛏️", "total_mined"],
    ["Heaviest Ores", "mining_heaviest_ore", "🪨", "weight"],
    ["Fishing", "fishing_top", "🎣", "total_catches"],
    ["Heaviest Fish", "fishing_heaviest_fish", "🐟", "weight"],
    ["Events", "event_points", "🎉", "points"],
    ["Radio", "radio_requesters", "🎵", "requests"],
    ["Reputation", "reputation", "💜", "rep_received"],
  ];
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(170px,1fr));margin-bottom:14px">
      ${metricCard("Generated", meta.generated_at || "—", "ranking snapshot", "accent-cyan", "🏆")}
      ${metricCard("Connected Sources", meta.connected_sources?.length || Object.values(meta.sources || {}).filter((s) => s.status === "connected").length, "real DB sources", "accent-green", "S")}
      ${metricCard("Missing Tables", meta.missing_tables?.length || 0, "safe empty sections", meta.missing_tables?.length ? "accent-red" : "accent-green", "T")}
      ${metricCard("Missing Columns", meta.missing_columns?.length || 0, "partial sources", meta.missing_columns?.length ? "accent-red" : "accent-green", "C")}
    </div>
    <div class="card" style="margin-bottom:14px">
      <div class="card-header"><h2>In-Room Leaderboard Commands</h2><span class="pill info">${(d.menu || []).length} commands</span></div>
      <div class="chip-row">${(d.menu || []).map((item) => `<span class="chip"><code>${esc(item.command)}</code> ${esc(item.label)}</span>`).join("")}</div>
    </div>
    <div class="pub-rankings-grid">
      ${sections.map(([title, key, icon, valueKey]) => renderLeaderboardMini(title, icon, lb[key] || d[key] || [], valueKey)).join("")}
    </div>
  `;
}

function renderLeaderboardMini(title, icon, rows, valueKey) {
  const top = (rows || []).slice(0, 5);
  return `<div class="card"><h3>${icon} ${esc(title)}</h3>
    ${top.length ? `<div class="pub-leaderboard">${top.map((r, i) => `<div class="pub-lb-row">
      <span class="pub-lb-rank ${i < 3 ? "top" + i : ""}">${["🥇","🥈","🥉"][i] || (i + 1)}</span>
      <span class="pub-lb-name">${esc(r.username || r.title || "—")}</span>
      <span class="pub-lb-val">${esc(String(r[valueKey] ?? r.total_won ?? ""))}</span>
    </div>`).join("")}</div>` : `<div class="notice">No connected rows.</div>`}
  </div>`;
}

function renderLeaderboardGroup(title, groups) {
  const d = state.data || {};
  const lb = d.leaderboards || d;
  const meta = d.diagnostics || d.metadata || {};
  return `<div class="grid">
    ${groups.map(([label, key, cols]) => `<div class="card">
      <div class="card-header"><h2>${esc(label)}</h2><span class="pill info">${(lb[key] || d[key] || []).length} rows</span></div>
      ${table(lb[key] || d[key] || [], cols)}
      <div class="muted text-sm" style="margin-top:10px">Source: <code>${esc(meta.sources?.[key]?.table || "not connected")}</code> ${meta.sources?.[key]?.notes ? `· ${esc(meta.sources[key].notes)}` : ""}</div>
    </div>`).join("")}
  </div>`;
}

function renderLeaderboardDiagnostics() {
  const d = state.data || {};
  const meta = d.diagnostics || d.metadata || {};
  const sources = Object.entries(meta.sources || {}).map(([name, info]) => ({ name, ...info, columns: (info.columns || []).join(", ") }));
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr));margin-bottom:14px">
      ${metricCard("Sources", sources.length, "leaderboard source checks", "accent-cyan", "S")}
      ${metricCard("Connected", meta.connected_sources?.length || sources.filter((s) => s.status === "connected").length, "real source mappings", "accent-green", "✓")}
      ${metricCard("Missing Tables", meta.missing_tables?.length || 0, (meta.missing_tables || []).slice(0, 3).join(", "), meta.missing_tables?.length ? "accent-red" : "accent-green", "T")}
      ${metricCard("Missing Columns", meta.missing_columns?.length || 0, (meta.missing_columns || []).slice(0, 3).join(", "), meta.missing_columns?.length ? "accent-red" : "accent-green", "C")}
    </div>
    <div class="card">
      <h2>Source Diagnostics</h2>
      ${table(sources, [
        { key: "name", label: "Leaderboard" },
        { key: "table", label: "Table" },
        { key: "status", label: "Status", render: (r) => auditStatusChip(String(r.status || "").toUpperCase(), r.status === "connected") },
        { key: "row_count", label: "Rows" },
        { key: "columns", label: "Columns" },
        { key: "notes", label: "Notes" },
      ])}
    </div>
    ${futureControls((meta.missing_tables || []).map((t) => ({ endpoint: t, purpose: "Leaderboard source table is not present in this DB.", status: "MISSING TABLE" }))
      .concat((meta.missing_columns || []).map((c) => ({ endpoint: c, purpose: "Leaderboard source column is not present in this DB.", status: "MISSING COLUMN" }))), "Leaderboard Missing Sources")}
  `;
}

/* ── Room & Content ──────────────────────────────────── */
function renderRoomContent(tab) {
  return `
    ${tabNav("Room & Content")}
    ${tab === "Overview" ? renderRoomOverview() : ""}
    ${tab === "Room Settings" ? renderRoomSettings() : ""}
    ${tab === "Welcome" ? renderWelcomeTab() : ""}
    ${tab === "Announcements" ? renderAnnouncementsTab() : ""}
    ${tab === "Events" ? renderEventsTab() : ""}
    ${tab === "Event Rewards" ? renderEventRewardsTab() : ""}
    ${tab === "Rules / Info" ? renderRulesInfoTab() : ""}
    ${tab === "Logs" ? renderRoomLogsTab() : ""}
    ${tab === "Advanced" ? renderRoomAdvancedTab() : ""}
  `;
}

function renderRoomOverview() {
  const d = state.data || {};
  const o = d.overview || {};
  const tables = d.tables || {};
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr))">
      ${metricCard("Room ID", o.room_id || "Not set", "from room_settings", "", "🏠")}
      ${metricCard("Room Users", o.room_users || "—", "if bot reports it", "accent-cyan", "👥")}
      ${metricCard("Welcome", boolLabel(o.welcome_enabled), "welcome_enabled", truthy(o.welcome_enabled) ? "accent-green" : "accent-red", "👋")}
      ${metricCard("Announcements", boolLabel(o.announcements_enabled), "announcements_enabled", truthy(o.announcements_enabled) ? "accent-green" : "accent-red", "📢")}
      ${metricCard("Active Event", o.active_event || "None", "event_settings", o.active_event ? "accent-green" : "", "🎉")}
      ${metricCard("Scheduled", o.scheduled_events_count ?? 0, "scheduled_events rows", "accent-cyan", "📅")}
    </div>
    <div class="grid">
      <div class="card">
        <h2>Recent Room Activity</h2>
        ${table(d.audit_logs || [], [
          { key: "created_at", label: "Time" },
          { key: "actor", label: "Actor" },
          { key: "action_type", label: "Action", render: (r) => pill(r.action_type || "audit") },
          { key: "target_type", label: "Target" },
        ])}
      </div>
      <div class="card">
        <h2>Room Data Sources</h2>
        ${table(Object.entries(tables).map(([name, info]) => ({ table: name, status: info.exists ? "connected" : "missing", rows: (info.rows || []).length })), [
          { key: "table", label: "Table" },
          { key: "status", label: "Status", render: (r) => pill(r.status, ["connected"]) },
          { key: "rows", label: "Rows Loaded" },
        ])}
      </div>
    </div>
  `;
}

function truthy(value) {
  return value === true || value === 1 || ["1", "true", "enabled", "yes", "on"].includes(String(value ?? "").toLowerCase());
}

function boolLabel(value) {
  if (value === undefined || value === null || value === "") return "Unknown";
  return truthy(value) ? "Enabled" : "Disabled";
}

function renderRoomSettings() {
  const d = state.data || {};
  const known = d.known_settings || {};
  const extra = d.extra_settings || [];
  const allRaw = Object.entries(known).map(([key, value]) => ({ key, value, source: "room_settings" })).concat(extra);
  const valMap = settingsMapFrom(allRaw);
  const roomGroups = (SETTINGS_SCHEMA.room || []).filter((g) => ["Social Settings", "Moderation Settings"].includes(g.title));
  return `
    ${roomGroups.map((g, i) => renderSettingsGroup(g, valMap, `room-core-${i}`)).join("")}
    ${futureControls([
      { endpoint: "room user count", purpose: "Requires bot heartbeat/status report", status: "Read only if available" },
      { endpoint: "reset welcome-seen", purpose: "Dangerous cleanup stays hidden until confirmed workflow exists", status: "Advanced" },
    ])}
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

function radioSourceLabel(row) {
  const src = String(row?.source_type || "").trim();
  if (src) return src.replaceAll("_", " ");
  return row?.url ? "YouTube" : "Auto DJ";
}

function renderRadioOwnerPage(tab) {
  const d = state.data || {};
  return `
    ${tabNav("Radio")}
    ${tab === "Overview" ? renderRadioOverview(d) : ""}
    ${tab === "Queue" ? renderRadioQueue(d, true) : ""}
    ${tab === "Requests" ? renderRadioRequests(d) : ""}
    ${tab === "Now Playing" ? renderRadioNowPlaying(d) : ""}
    ${tab === "Recently Played" ? renderRadioRecent(d) : ""}
    ${tab === "Blocklist" ? renderRadioBlocklist(d) : ""}
    ${tab === "Rewards / Stats" ? renderRadioStats(d) : ""}
    ${tab === "Local Library" ? renderRadioLocalLibrary(d) : ""}
    ${tab === "AzuraCast / Stream" ? renderRadioStream(d) : ""}
    ${tab === "Logs" ? renderRadioLogs(d) : ""}
    ${tab === "Advanced" ? renderRadioAdvanced(d) : ""}
  `;
}

function renderRadioOverview(d) {
  const h = d.health || {};
  const np = d.now_playing || {};
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr))">
      ${metricCard("Radio", h.radio_online ? "Online" : "Offline", "DJ_DUDU status", h.radio_online ? "accent-green" : "accent-red", "📻")}
      ${metricCard("Request Gate", d.queue_open ? "Open" : "Closed", "dashboard + radio setting", d.queue_open ? "accent-green" : "accent-red", "🎛")}
      ${metricCard("Queue Size", h.queue_size ?? (d.queue || []).length, "upcoming songs", "accent-cyan", "🎵")}
      ${metricCard("In Pipeline", h.in_pipeline ?? 0, "preparing / uploading", "", "⚙️")}
      ${metricCard("Failed Today", h.failed_today ?? 0, "terminal failures", "accent-red", "⚠️")}
      ${metricCard("Played Today", h.played_today ?? 0, "completed plays", "accent-green", "▶️")}
    </div>
    <div class="grid">
      <div class="card">
        <div class="card-header"><h2>Now Playing</h2><span class="pill info">${esc(np.status || "stream")}</span></div>
        <div style="font-size:18px;font-weight:700;margin-bottom:4px">${esc(np.title || "Auto DJ")}</div>
        ${np.artist ? `<div class="muted text-sm">${esc(np.artist)}</div>` : ""}
        ${np.username ? `<div class="muted text-sm">Requested by ${esc(np.username)}</div>` : ""}
        <div class="inline-actions" style="margin-top:12px">
          <button class="btn danger sm" data-action="radio-skip">Queue Skip</button>
          <button class="btn sm" data-action="radio-refresh">Refresh</button>
        </div>
        ${queueHelp()}
      </div>
      <div class="card">
        <h2>Health</h2>
        ${table([
          { label: "Top Requester", value: h.top_requester?.username ? `${h.top_requester.username} (${h.top_requester.requests})` : "—" },
          { label: "AzuraCast", value: h.azuracast?.status || "unknown" },
          { label: "Stream URL", value: d.radio_url ? "configured" : "not configured" },
          { label: "Queue Worker Heartbeat", value: h.worker_health?.queue_heartbeat || "—" },
          { label: "Playback Heartbeat", value: h.worker_health?.playback_heartbeat || "—" },
        ], [{ key: "label", label: "Signal" }, { key: "value", label: "Value" }])}
      </div>
    </div>
    <div class="card">
      <h2>Queued Bot Commands</h2>
      ${renderQueuedBotCommands(d.command_queue || {})}
    </div>
  `;
}

function renderRadioQueue(d, controls = true) {
  return `
    <div class="grid">
      <div class="card">
        <h2>Current Playing</h2>
        ${d.now_playing ? table([d.now_playing], radioJobColumns()) : `<div class="notice">Nothing currently marked as playing.</div>`}
      </div>
      <div class="card">
        <h2>Queue Controls</h2>
        ${controls ? `<div class="inline-actions">
          <button class="btn danger" data-action="radio-skip">Queue Skip</button>
          <button class="btn danger" data-action="radio-clear">Queue Clear</button>
          <button class="btn" data-action="radio-refresh">Refresh</button>
        </div>${queueHelp()}` : `<div class="notice">Read-only queue view.</div>`}
      </div>
    </div>
    <div class="card">
      <div class="card-header"><h2>Upcoming Queue (${(d.queue || []).length})</h2><span class="pill def">Live DB</span></div>
      ${table(d.queue || [], radioJobColumns(), controls ? (r) => `<button class="btn danger sm" data-remove-request="${r.id}">Remove</button>` : null)}
    </div>
  `;
}

function radioJobColumns() {
  return [
    { key: "pos", label: "#" },
    { key: "title", label: "Title" },
    { key: "artist", label: "Artist" },
    { key: "username", label: "Requester" },
    { key: "source_type", label: "Source", render: radioSourceLabel },
    { key: "status", label: "Status", render: (r) => pill(r.status || "unknown") },
    { key: "started_at", label: "Started" },
  ];
}

function renderRadioRequests(d) {
  const s = d.settings || {};
  return `
    <div class="grid">
      <div class="card">
        <h2>Request Gate</h2>
        <label class="switch" style="margin-bottom:16px">
          <input type="checkbox" id="requestsEnabled" ${d.queue_open ? "checked" : ""} />
          <span>Requests enabled</span>
        </label>
        <div class="notice">Writes the dashboard gate and <code>room_settings.radio_requests_enabled</code>, the source read by DJ_DUDU.</div>
      </div>
      <div class="card">
        <h2>Request Limits</h2>
        <form id="radioSettingsForm" class="settings-form">
          ${settingInput("max_active_queue", "Queue Limit", s.max_active_queue ?? 20)}
          ${settingInput("per_user_queue_limit", "Per User Limit", s.per_user_queue_limit ?? 3)}
          ${settingInput("request_cooldown", "Request Cooldown", s.request_cooldown ?? 300)}
          ${settingInput("request_price", "Request Price", s.request_price ?? 500)}
          ${settingInput("voteskip_threshold", "Voteskip Threshold", s.voteskip_threshold ?? 3)}
          <label class="switch"><input type="checkbox" name="skip_on_leave" ${String(s.skip_on_leave) !== "false" ? "checked" : ""}/><span>Skip if requester leaves</span></label>
          <label class="switch"><input type="checkbox" name="refund_on_leave" ${String(s.refund_on_leave) !== "false" ? "checked" : ""}/><span>Refund if requester leaves</span></label>
          <label class="switch"><input type="checkbox" name="admin_ignore_leave" ${String(s.admin_ignore_leave) !== "false" ? "checked" : ""}/><span>Staff requests ignore leave</span></label>
          <button class="btn primary">Save Request Settings</button>
        </form>
      </div>
    </div>
    ${futureControls([
      { endpoint: "playlist move up/down", purpose: "Reorder queue safely", status: "Future" },
      { endpoint: "local-only / YouTube disable toggle", purpose: "Needs verified active bot setting", status: "Unverified source" },
    ])}
  `;
}

function renderRadioNowPlaying(d) {
  return `<div class="card">
    <h2>Now Playing</h2>
    ${d.now_playing ? table([d.now_playing], [
      { key: "title", label: "Title" },
      { key: "artist", label: "Artist" },
      { key: "username", label: "Requester" },
      { key: "source_type", label: "Source", render: radioSourceLabel },
      { key: "status", label: "Status", render: (r) => pill(r.status) },
      { key: "started_at", label: "Started" },
      { key: "azura_song_id", label: "Azura Song ID" },
    ]) : `<div class="notice">No current request row is marked playing.</div>`}
    ${d.radio_url ? `<div class="notice" style="margin-top:12px">Stream URL configured. AzuraCast API keys are never exposed.</div>` : ""}
  </div>`;
}

function renderRadioRecent(d) {
  return `<div class="card">
    <h2>Recently Played</h2>
    ${table(d.recently_played || d.recent || [], [
      { key: "title", label: "Title" },
      { key: "artist", label: "Artist" },
      { key: "username", label: "Requester" },
      { key: "status", label: "Status", render: (r) => pill(r.status) },
      { key: "played_at", label: "Played" },
      { key: "finished_at", label: "Finished" },
    ])}
  </div>`;
}

function renderRadioBlocklist(d) {
  return `<div class="grid">
    <div class="card">
      <h2>Blocked Requesters</h2>
      <form id="radioBlockRequesterForm" class="toolbar" style="margin-bottom:12px">
        <input name="username" placeholder="username" required />
        <button class="btn primary">Block Requester</button>
      </form>
      ${table(d.blocklist?.requesters || [], [
        { key: "username", label: "Username" },
        { key: "added_by", label: "Added By" },
        { key: "added_at", label: "Added" },
      ], (r) => `<button class="btn danger sm" data-unblock-requester="${esc(r.username)}">Remove</button>`)}
    </div>
    <div class="card">
      <h2>Blocked Tracks</h2>
      <form id="radioBlockTrackForm" class="toolbar" style="margin-bottom:12px">
        <input name="pattern" placeholder="title, URL, or pattern" required />
        <button class="btn primary">Block Track</button>
      </form>
      ${table(d.blocklist?.tracks || [], [
        { key: "id", label: "ID" },
        { key: "pattern", label: "Pattern" },
        { key: "added_by", label: "Added By" },
        { key: "added_at", label: "Added" },
      ], (r) => `<button class="btn danger sm" data-unblock-track="${r.id}">Remove</button>`)}
    </div>
  </div>`;
}

function renderRadioStats(d) {
  const st = d.stats || {};
  return `<div class="grid">
    <div class="card"><h2>Top Requesters</h2>${table(st.top_requesters || [], [{ key: "username", label: "Username" }, { key: "requests", label: "Requests" }])}</div>
    <div class="card"><h2>User Stats</h2>${table(st.radio_user_stats || [])}</div>
    <div class="card"><h2>Song Stats</h2>${table(st.radio_song_stats || [])}</div>
    <div class="card"><h2>Rewards Paid</h2>${table(st.rewards || [])}</div>
  </div>`;
}

function renderRadioLocalLibrary(d) {
  const lib = d.local_library || {};
  return `<div class="grid">
    <div class="card"><h2>Playlists</h2>${table(lib.playlists || [])}</div>
    <div class="card"><h2>Playlist Songs</h2>${table(lib.songs || [])}</div>
    <div class="card"><h2>Local Replay Jobs</h2>${table(lib.replay_jobs || [])}</div>
  </div>`;
}

function renderRadioStream(d) {
  const h = d.health || {};
  return `<div class="grid">
    <div class="card">
      <h2>AzuraCast / Stream</h2>
      ${table([
        { label: "Stream URL", value: d.radio_url || "Not configured" },
        { label: "Azura Status", value: h.azuracast?.status || "unknown" },
        { label: "API Keys Exposed", value: "No" },
        { label: "DJ Bot", value: h.dj_bot?.bot_username || "DJ_DUDU" },
        { label: "DJ Status", value: h.dj_bot?.status || "unknown" },
      ], [{ key: "label", label: "Field" }, { key: "value", label: "Value" }])}
    </div>
    <div class="card">
      <h2>Maintenance</h2>
      <div class="inline-actions">
        <button class="btn danger" data-action="radio-cleanup">Queue Cleanup</button>
        <button class="btn" data-action="radio-reload">Queue Reload</button>
      </div>
      ${queueHelp()}
    </div>
  </div>`;
}

function renderRadioLogs(d) {
  return `<div class="grid">
    <div class="card"><h2>Radio Audit Logs</h2>${table(d.logs?.audit || [])}</div>
    <div class="card"><h2>Command Errors</h2>${table(d.logs?.command_errors || [])}</div>
  </div>`;
}

function renderRadioAdvanced(d) {
  return `<div class="grid">
    <div class="card"><h2>Raw Counts</h2>${table(Object.entries(d.counts || {}).map(([status, count]) => ({ status, count })))}</div>
    <div class="card"><h2>Queued Bot Commands</h2>${renderQueuedBotCommands(d.command_queue || {})}</div>
  </div>
  ${futureControls([
    { endpoint: "hard delete media", purpose: "Not exposed from dashboard", status: "Hidden" },
    { endpoint: "Azura credential edits", purpose: "Use .env/VPS config only", status: "Hidden" },
  ])}`;
}

function renderEventsTab() {
  const d = state.data || {};
  const tables = d.tables || {};
  const hasRows = Object.values(tables).some((info) => info.exists && (info.rows || []).length);
  const active = d.active_event;
  const definitions = tables.event_definitions?.rows || d.definitions || [];
  return `
    <div class="grid">
      <div class="card">
        <div class="card-header"><h2>Active Event</h2>${active ? pill("active") : pill("none")}</div>
        ${active ? table([active], [
          { key: "event_id", label: "Event" },
          { key: "expires_at", label: "Expires" },
        ]) : `<div class="notice">No active room event is stored in event_settings.</div>`}
        <form id="eventStartForm" class="toolbar" style="margin-top:12px;flex-wrap:wrap">
          <input name="event_id" list="eventIds" placeholder="event id or number" required />
          <input name="minutes" type="number" min="1" max="480" value="30" style="max-width:120px" />
          <button class="btn primary">Queue Start</button>
          <button class="btn danger" type="button" data-action="event-stop">Queue Stop</button>
        </form>
        <datalist id="eventIds">${definitions.map((e) => `<option value="${esc(e.event_id || e.id || e.name || e.title || "")}"></option>`).join("")}</datalist>
        ${queueHelp()}
      </div>
      <div class="card">
        <h2>📅 Scheduled Events</h2>
        ${renderEventRows(tables.scheduled_events?.rows || d.scheduled || [])}
        <form id="eventScheduleForm" class="settings-form" style="margin-top:12px">
          <input name="event_id" placeholder="event id" required />
          <input name="starts_at" placeholder="YYYY-MM-DD HH:MM or ISO time" required />
          <input name="minutes" type="number" min="1" max="480" value="30" />
          <button class="btn primary sm">Queue Schedule</button>
        </form>
        ${queueHelp()}
      </div>
    </div>
    ${hasRows ? `
      ${renderEventTableCard("Event Definitions", tables.event_definitions)}
      ${renderEventTableCard("Event History", tables.event_history)}
      ${renderEventTableCard("Event Settings", tables.event_settings)}
      ${renderEventTableCard("Event Votes", tables.event_votes)}
      ${renderEventTableCard("Processed Events", tables.processed_events)}
    ` : `<div class="card"><div class="notice">No event rows found in the live DB tables.</div></div>`}
    <div class="card">
      <h2>Queued Event Commands</h2>
      ${renderQueuedBotCommands(d.command_queue || {})}
    </div>
    ${futureControls([
      { endpoint: "event rewards editor", purpose: "Reward catalog edits need verified table shape", status: "Unverified source" },
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
  const rotating = d.tables?.rotating_announcements || {};
  const subscriber = d.tables?.subscriber_announcements || {};
  const bigSettings = d.tables?.big_announcement_settings || {};
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
    <div class="grid">
      <div class="card">
        <h2>Rotating Announcements</h2>
        ${rotating.exists ? `
          <form id="rotatingAnnouncementForm" class="toolbar" style="margin-bottom:12px;flex-wrap:wrap">
            <input name="message" placeholder="Add rotating announcement" required style="flex:1;min-width:220px" />
            <button class="btn primary">Add</button>
          </form>
          ${table(rotating.rows || [], null, (r) => r.id !== undefined ? `<button class="btn danger sm" data-disable-announcement="${esc(r.id)}">Disable</button>` : "")}
        ` : `<div class="notice">rotating_announcements table is not present.</div>`}
      </div>
      <div class="card"><h2>Subscriber Announcements</h2>${subscriber.exists ? table(subscriber.rows || []) : `<div class="notice">subscriber_announcements table is not present.</div>`}</div>
      <div class="card"><h2>Big Announcement Settings</h2>${bigSettings.exists ? table(bigSettings.rows || []) : `<div class="notice">big_announcement_settings table is not present.</div>`}</div>
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
  const seen = d.tables?.room_welcome_seen || {};
  return `
    ${welGroup ? renderSettingsGroup(welGroup, valMap, "welcome") : ""}
    <div class="card">
      <h2>Welcome Seen</h2>
      ${seen.exists ? table(seen.rows || []) : `<div class="notice">room_welcome_seen table is not present.</div>`}
    </div>
    ${futureControls([
      { endpoint: "reset room_welcome_seen", purpose: "Cleanup requires typed confirmation; no default deletion", status: "Advanced only" },
    ])}
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

function renderDancefloorTab() {
  const d = state.data || {};
  const known = d.known_settings || {};
  const extra = d.extra_settings || [];
  const allRaw = Object.entries(known).map(([key, value]) => ({ key, value, source: "room_settings" })).concat(extra);
  return `
    ${notConnectedCard("Dancefloor", [
      { endpoint: "room_settings dancefloor_*", purpose: "Dancefloor rules and automation", status: "Unverified source" },
      { endpoint: "POST /api/dancefloor/*", purpose: "Start/stop dancefloor workflows", status: "Future" },
    ])}
    ${renderAdvancedCollapse(allRaw.filter((s) => String(s.key || "").includes("dance")))}
  `;
}

function renderSyncTab() {
  const d = state.data || {};
  const known = d.known_settings || {};
  const extra = d.extra_settings || [];
  const allRaw = Object.entries(known).map(([key, value]) => ({ key, value, source: "room_settings" })).concat(extra);
  return `
    ${notConnectedCard("Sync", [
      { endpoint: "sync_relations", purpose: "Read and manage emote sync relations", status: "Future" },
      { endpoint: "room_settings sync_*", purpose: "Sync timing settings", status: "Unverified source" },
    ])}
    ${renderAdvancedCollapse(allRaw.filter((s) => String(s.key || "").includes("sync")))}
  `;
}

function renderEventRewardsTab() {
  const d = state.data || {};
  const tables = d.tables || {};
  return `<div class="grid">
    <div class="card">
      <h2>Event Points Leaderboard</h2>
      ${table(tables.event_points?.rows || d.points || [], null)}
    </div>
    <div class="card">
      <h2>Event Reward Settings</h2>
      ${table(tables.event_settings?.rows || d.settings || [])}
      <div class="notice" style="margin-top:12px">Reward editing is read-only until the active reward table/key mapping is verified.</div>
    </div>
  </div>
  ${futureControls([
    { endpoint: "PUT /api/events/settings", purpose: "Verified non-active event_settings keys only", status: "Backend available" },
    { endpoint: "event reward catalog writes", purpose: "Needs exact active table/key mapping", status: "Unverified source" },
  ])}`;
}

function renderRulesInfoTab() {
  const d = state.data || {};
  const known = d.known_settings || {};
  const extra = d.extra_settings || [];
  const allRaw = Object.entries(known).map(([key, value]) => ({ key, value, source: "room_settings" })).concat(extra);
  const values = settingsMapFrom(allRaw);
  const fields = [
    { key: "room_rules", label: "Room Rules", hint: "One rule per line. Public portal safe.", type: "textarea" },
    { key: "how_to_play", label: "How To Play", hint: "Public room helper text.", type: "textarea" },
    { key: "vip_info", label: "VIP Info", hint: "Public VIP summary.", type: "textarea" },
    { key: "staff_list", label: "Staff List", hint: "Optional public staff list, one per line.", type: "textarea" },
  ];
  return `${renderSettingsGroup({
    title: "Rules / Info",
    description: "Public read-only room information shown in the portal.",
    api: "/api/settings/:key",
    apiOpts: { source: "room_settings" },
    keys: fields,
  }, values, "rules-info")}
  ${futureControls([
    { endpoint: "dedicated rules/info table", purpose: "Use if room_settings becomes too limited", status: "Future" },
  ])}`;
}

function renderRoomLogsTab() {
  const d = state.data || {};
  const tables = d.tables || {};
  return `<div class="grid">
    <div class="card"><h2>Room Social Logs</h2>${table(tables.room_social_logs?.rows || [])}</div>
    <div class="card"><h2>Big Announcement Logs</h2>${table(tables.big_announcement_logs?.rows || [])}</div>
    <div class="card"><h2>Event History</h2>${table(tables.event_history?.rows || [])}</div>
    <div class="card"><h2>Audit Logs</h2>${table(d.audit_logs || [])}</div>
    <div class="card"><h2>Admin Action Logs</h2>${table(tables.admin_action_logs?.rows || [])}</div>
  </div>`;
}

function renderRoomAdvancedTab() {
  const d = state.data || {};
  const known = d.known_settings || {};
  const extra = d.extra_settings || [];
  const allRaw = Object.entries(known).map(([key, value]) => ({ key, value, source: "room_settings" })).concat(extra);
  const tables = d.tables || {};
  return `
    ${futureControls([
      { endpoint: "reset welcome-seen", purpose: "Requires typed confirmation; not exposed by default", status: "Future" },
      { endpoint: "POST /api/room/emote-packs", purpose: "Manage emote packs", status: "Future" },
      { endpoint: "dancefloor/sync controls", purpose: "Needs exact active helper/source mapping", status: "Unverified source" },
      { endpoint: "hard delete room/event logs", purpose: "Not exposed from dashboard", status: "Hidden" },
    ], "Advanced / Future Room Controls")}
    <div class="grid">
      <div class="card"><h2>Raw Event Settings</h2>${table(tables.event_settings?.rows || [])}</div>
      <div class="card"><h2>Module Flags</h2>${table(tables.module_flags?.rows || [])}</div>
      <div class="card"><h2>Room Emote Loops</h2>${table(tables.room_emote_loops?.rows || [])}</div>
      <div class="card"><h2>Room Tags</h2>${table(tables.room_tags?.rows || [])}</div>
    </div>
    ${renderAdvancedCollapse(allRaw)}
  `;
}

/* ── Mining / Fishing Owner Pages ───────────────────── */
function renderResourceToolbar({ search = "Search", rarity = true, enabled = true } = {}) {
  return `<div class="toolbar resource-toolbar" style="margin-bottom:12px;flex-wrap:wrap">
    <input data-table-search placeholder="${esc(search)}" style="flex:1;min-width:180px" />
    ${rarity ? `<select data-rarity-filter>
      <option value="">All rarities</option>
      ${["common","uncommon","rare","epic","legendary","mythic","ultra_rare","prismatic","exotic"].map((r) => `<option value="${r}">${esc(r.replace("_", " "))}</option>`).join("")}
    </select>` : ""}
    ${enabled ? `<select data-enabled-filter>
      <option value="">All states</option>
      <option value="enabled">Enabled</option>
      <option value="disabled">Disabled</option>
    </select>` : ""}
  </div>`;
}

function renderMiningOwnerPage(tab) {
  return `
    ${tabNav("Mining")}
    ${tab === "Overview" ? renderMiningOverviewPage() : ""}
    ${tab === "Settings" ? renderMiningTab() : ""}
    ${tab === "Pickaxes" ? renderMiningPickaxesPage() : ""}
    ${tab === "Ores" ? renderMiningOresPage() : ""}
    ${tab === "Drop Chances" ? renderMiningDropChancesPage() : ""}
    ${tab === "Player Mining" ? renderMiningPlayersPage() : ""}
    ${tab === "Inventory" ? renderMiningInventoryPage() : ""}
    ${tab === "Logs" ? renderMiningLogsPage() : ""}
    ${tab === "Advanced" ? renderMiningAdvancedPage() : ""}
  `;
}

function renderMiningOverviewPage() {
  const d = state.data || {};
  const s = d.stats || {};
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr))">
      ${metricCard("Mining Enabled", s.mining_enabled ?? "—", "mining_settings", s.mining_enabled === "true" || s.mining_enabled === "1" ? "accent-green" : "", "⛏️")}
      ${metricCard("Total Miners", Number(s.total_miners || 0).toLocaleString(), "profiles", "", "👤")}
      ${metricCard("Total Ores Mined", Number(s.total_ores_mined || 0).toLocaleString(), "all time", "", "💎")}
      ${metricCard("Today's Mining", Number(s.todays_mining || 0).toLocaleString(), "actions", "", "📈")}
      ${metricCard("Rare Finds", Number(s.rare_finds || 0).toLocaleString(), "rare+", "accent-green", "✨")}
      ${metricCard("Gold Rain", s.gold_rain_enabled ?? "—", "status", "", "🌧")}
    </div>
    <div class="grid">
      <div class="card"><h2>Recent Ores</h2>${table(d.ores || [], [
        { key: "item_id", label: "Ore ID" },
        { key: "name", label: "Name" },
        { key: "rarity", label: "Rarity" },
        { key: "sell_value", label: "Value", render: (r) => Number(r.sell_value || 0).toLocaleString() },
        { key: "drop_enabled", label: "Drops", render: (r) => pill(Number(r.drop_enabled) ? "enabled" : "disabled") },
      ])}</div>
      <div class="card"><h2>Top Miners</h2>${table(d.players || [], [
        { key: "username", label: "Player" },
        { key: "mining_level", label: "Level" },
        { key: "mining_xp", label: "XP", render: (r) => Number(r.mining_xp || 0).toLocaleString() },
        { key: "total_mines", label: "Mines", render: (r) => Number(r.total_mines || 0).toLocaleString() },
      ])}</div>
    </div>
  `;
}

function renderMiningPickaxesPage() {
  const d = state.data || {};
  return `<div class="card">
    <div class="card-header">
      <div><h2>Pickaxes</h2><div class="muted text-sm">Source: runtime tool levels in <code>modules/mining.py</code></div></div>
      <span class="pill warn">Read-only</span>
    </div>
    ${renderResourceToolbar({ search: "Search pickaxes", rarity: false, enabled: false })}
    ${table(d.rows || [], [
      { key: "item_id", label: "Item ID" },
      { key: "display_name", label: "Pickaxe" },
      { key: "required_level", label: "Level" },
      { key: "cooldown_seconds", label: "Cooldown", render: (r) => `${esc(r.cooldown_seconds)} sec` },
      { key: "source", label: "Source" },
    ])}
    ${futureControls([
      { endpoint: "POST /api/mining/pickaxes", purpose: "Add pickaxe catalog row", status: "Unverified schema" },
      { endpoint: "PUT /api/mining/pickaxes/:id", purpose: "Edit pickaxe stats", status: "Unverified schema" },
    ], "Advanced / Pickaxe Catalog Writes")}
  </div>`;
}

function renderMiningOresPage() {
  const d = state.data || {};
  const rows = d.rows || [];
  return `
    <div class="card">
      <div class="card-header">
        <div><h2>Ores</h2><div class="muted text-sm">Editable source: <code>mining_items</code></div></div>
        ${pill(d.writable ? "enabled" : "read only")}
      </div>
      ${renderResourceToolbar({ search: "Search ores" })}
      <details class="advanced-collapse" open>
        <summary class="advanced-summary"><span class="pill info">Add</span> Add Ore</summary>
        <div class="advanced-content">
          <form id="miningOreAddForm" class="settings-fields">
            ${["item_id","name","emoji","rarity","sell_value"].map((key) => renderSettingsField({ key, label: key.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase()), type: key === "sell_value" ? "number" : "text" }, "")).join("")}
            ${renderSettingsField({ key: "drop_enabled", label: "Drops Enabled", type: "toggle" }, true)}
            <button class="btn primary sm" type="submit">Save Ore</button>
          </form>
        </div>
      </details>
      ${table(rows, [
        { key: "item_id", label: "Ore ID" },
        { key: "name", label: "Name", render: (r) => `<input name="name" value="${esc(r.name || "")}" form="ore_${esc(r.item_id)}" />` },
        { key: "emoji", label: "Icon", render: (r) => `<input name="emoji" value="${esc(r.emoji || "")}" form="ore_${esc(r.item_id)}" />` },
        { key: "rarity", label: "Rarity", render: (r) => `<input name="rarity" value="${esc(r.rarity || "")}" form="ore_${esc(r.item_id)}" />` },
        { key: "sell_value", label: "Base Value", render: (r) => `<input type="number" name="sell_value" value="${esc(r.sell_value || 0)}" form="ore_${esc(r.item_id)}" />` },
        { key: "drop_enabled", label: "Enabled", render: (r) => `<label class="switch compact"><input type="checkbox" name="drop_enabled" form="ore_${esc(r.item_id)}" ${Number(r.drop_enabled) ? "checked" : ""}><span></span></label>` },
      ], (r) => `<form id="ore_${esc(r.item_id)}" data-mining-ore-form="${esc(r.item_id)}" class="inline-actions">
        <button class="btn primary sm" type="submit">Save</button>
        <button class="btn danger sm" type="button" data-mining-ore-disable="${esc(r.item_id)}">Disable</button>
      </form>`)}
      <details class="advanced-collapse">
        <summary class="advanced-summary"><span class="pill bad">Danger</span> Hard Delete Ore</summary>
        <div class="advanced-content">
          <div class="notice warn">Hard delete requires owner role, typed confirmation <code>DELETE ORE</code>, and no inventory references unless force is explicitly sent. Use Disable for normal operations.</div>
        </div>
      </details>
    </div>
  `;
}

function renderMiningDropChancesPage() {
  const d = state.data || {};
  return `<div class="card">
    <div class="card-header">
      <div><h2>Drop Chances</h2><div class="muted text-sm">${esc(d.message || "Calculated from active runtime source.")}</div></div>
      <span class="pill warn">Read-only</span>
    </div>
    ${renderResourceToolbar({ search: "Search ore chances", enabled: true })}
    ${table(d.rows || [], [
      { key: "ore", label: "Ore" },
      { key: "rarity", label: "Rarity" },
      { key: "weight", label: "Weight", render: (r) => Number(r.weight || 0).toFixed(6) },
      { key: "chance_percent", label: "Chance %", render: (r) => `${Number(r.chance_percent || 0).toFixed(6)}%` },
      { key: "enabled", label: "Enabled", render: (r) => pill(Number(r.enabled) ? "enabled" : "disabled") },
      { key: "source", label: "Source" },
    ])}
    ${futureControls([{ endpoint: "PUT /api/mining/drop-weights", purpose: "Edit runtime drop chances", status: "Unverified schema" }], "Advanced / Drop Weight Writes")}
  </div>`;
}

function renderMiningPlayersPage() {
  return `<div class="card"><h2>Player Mining</h2>${renderResourceToolbar({ search: "Search miners", rarity: false, enabled: false })}${table(state.data?.rows || [])}</div>`;
}

function renderMiningInventoryPage() {
  return `<div class="card"><h2>Mining Inventory</h2>${renderResourceToolbar({ search: "Search inventory", rarity: true, enabled: false })}${table(state.data?.rows || [])}</div>`;
}

function renderMiningLogsPage() {
  const d = state.data || {};
  return `
    <div class="card"><h2>Mining Logs</h2>${table(d.mining_logs || [])}</div>
    <div class="card"><h2>Mining Payout Logs</h2>${table(d.mining_payout_logs || [])}</div>
    <div class="card"><h2>Mining Events</h2>${table(d.mining_events || [])}</div>
    <div class="card"><h2>Forced Mining Drops</h2>${table(d.forced_mining_drops || [])}</div>
  `;
}

function renderMiningAdvancedPage() {
  const d = state.data || {};
  const raw = d.raw || {};
  return `
    ${futureControls([
      { endpoint: "DELETE /api/mining/ores/:id hard=true", purpose: "Permanent ore delete with typed confirmation", status: "Owner only" },
      { endpoint: "POST /api/mining/pickaxes", purpose: "Pickaxe catalog writes", status: "Unverified schema" },
      { endpoint: "PUT /api/mining/drop-weights", purpose: "Drop chance edits", status: "Unverified schema" },
    ], "Advanced / Unverified Mining Controls")}
    <div class="card"><h2>Raw Mining Settings</h2>${table(raw.mining_settings || [])}${table(raw.mining_weight_settings || [])}${table(raw.auto_activity_settings || [])}</div>
    <div class="card"><h2>Table Status</h2>${table(Object.entries(d.table_status || {}).map(([table_name, exists]) => ({ table_name, exists: exists ? "present" : "missing" })))}</div>
  `;
}

function renderFishingOwnerPage(tab) {
  return `
    ${tabNav("Fishing")}
    ${tab === "Overview" ? renderFishingOverviewPage() : ""}
    ${tab === "Settings" ? renderFishingTab() : ""}
    ${tab === "Rods" ? renderFishingRodsPage() : ""}
    ${tab === "Fish Catalog" ? renderFishingCatalogPage() : ""}
    ${tab === "Catch Chances" ? renderFishingCatchChancesPage() : ""}
    ${tab === "Player Fishing" ? renderFishingPlayersPage() : ""}
    ${tab === "Inventory" ? renderFishingInventoryPage() : ""}
    ${tab === "Logs" ? renderFishingLogsPage() : ""}
    ${tab === "Advanced" ? renderFishingAdvancedPage() : ""}
  `;
}

function renderFishingOverviewPage() {
  const d = state.data || {};
  const s = d.stats || {};
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr))">
      ${metricCard("Fishing Enabled", s.fishing_enabled ?? "—", "autofish flag", "", "🎣")}
      ${metricCard("Total Fishers", Number(s.total_fishers || 0).toLocaleString(), "profiles", "", "👤")}
      ${metricCard("Total Fish Caught", Number(s.total_fish_caught || 0).toLocaleString(), "records", "", "🐟")}
      ${metricCard("Today's Catches", Number(s.todays_catches || 0).toLocaleString(), "today", "", "📈")}
      ${metricCard("Biggest Catch", s.biggest_catch?.weight ?? "—", s.biggest_catch?.fish_name || "weight", "accent-green", "🏆")}
      ${metricCard("Auto Sell Rows", Number(s.auto_sell_rows || 0).toLocaleString(), "player settings", "", "💰")}
    </div>
    <div class="grid">
      <div class="card"><h2>Top Fishers</h2>${table(d.players || [])}</div>
      <div class="card"><h2>Recent Inventory</h2>${table(d.inventory || [])}</div>
    </div>
  `;
}

function renderFishingRodsPage() {
  const d = state.data || {};
  return `<div class="card">
    <div class="card-header"><div><h2>Rods</h2><div class="muted text-sm">${esc(d.message || "Runtime code catalog")}</div></div><span class="pill warn">Read-only</span></div>
    ${renderResourceToolbar({ search: "Search rods", rarity: false, enabled: false })}
    ${table(d.rows || [], [
      { key: "name", label: "Rod" },
      { key: "price", label: "Price", render: (r) => Number(r.price || 0).toLocaleString() },
      { key: "cooldown", label: "Cooldown" },
      { key: "luck", label: "Luck" },
      { key: "weight_luck", label: "Weight Bonus" },
      { key: "value_bonus", label: "Value Bonus" },
      { key: "fxp_bonus", label: "FXP Bonus" },
      { key: "desc", label: "Description" },
    ])}
    ${futureControls([{ endpoint: "POST /api/fishing/rods", purpose: "Add/edit rod catalog", status: "Unverified schema" }], "Advanced / Rod Writes")}
  </div>`;
}

function renderFishingCatalogPage() {
  const d = state.data || {};
  return `<div class="card">
    <div class="card-header"><div><h2>Fish Catalog</h2><div class="muted text-sm">${esc(d.message || "Runtime code catalog")}</div></div><span class="pill warn">Read-only</span></div>
    ${renderResourceToolbar({ search: "Search fish" })}
    ${table(d.rows || [], [
      { key: "fish_id", label: "Fish ID" },
      { key: "name", label: "Name" },
      { key: "rarity", label: "Rarity" },
      { key: "base_value", label: "Base Value", render: (r) => Number(r.base_value || 0).toLocaleString() },
      { key: "base_fxp", label: "XP" },
      { key: "min_weight", label: "Min Weight" },
      { key: "max_weight", label: "Max Weight" },
      { key: "drop_weight", label: "Catch Weight" },
    ])}
    ${futureControls([{ endpoint: "POST /api/fishing/fish", purpose: "Add/edit fish catalog", status: "Unverified schema" }], "Advanced / Fish Catalog Writes")}
  </div>`;
}

function renderFishingCatchChancesPage() {
  const d = state.data || {};
  return `<div class="card">
    <div class="card-header"><div><h2>Catch Chances</h2><div class="muted text-sm">${esc(d.message || "Calculated from active source")}</div></div><span class="pill warn">Read-only</span></div>
    ${renderResourceToolbar({ search: "Search fish chances", enabled: false })}
    ${table(d.rows || [], [
      { key: "fish", label: "Fish" },
      { key: "rarity", label: "Rarity" },
      { key: "weight", label: "Weight" },
      { key: "chance_percent", label: "Chance %", render: (r) => `${Number(r.chance_percent || 0).toFixed(6)}%` },
      { key: "source", label: "Source" },
    ])}
    ${futureControls([{ endpoint: "PUT /api/fishing/drop-weights", purpose: "Edit catch weights", status: "Unverified schema" }], "Advanced / Catch Weight Writes")}
  </div>`;
}

function renderFishingPlayersPage() {
  return `<div class="card"><h2>Player Fishing</h2>${renderResourceToolbar({ search: "Search fishers", rarity: false, enabled: false })}${table(state.data?.rows || [])}</div>`;
}

function renderFishingInventoryPage() {
  return `<div class="card"><h2>Fishing Inventory</h2>${renderResourceToolbar({ search: "Search inventory", enabled: false })}${table(state.data?.rows || [])}</div>`;
}

function renderFishingLogsPage() {
  const d = state.data || {};
  return `
    <div class="card"><h2>Fish Catch Records</h2>${table(d.fish_catch_records || [])}</div>
    <div class="card"><h2>Forced Fishing Drops</h2>${table(d.forced_fishing_drops || [])}</div>
    <div class="card"><h2>Fish Auto Sell Settings</h2>${table(d.fish_auto_sell_settings || [])}</div>
  `;
}

function renderFishingAdvancedPage() {
  const d = state.data || {};
  const raw = d.raw || {};
  return `
    ${futureControls([
      { endpoint: "POST /api/fishing/fish", purpose: "Fish catalog writes", status: "Unverified schema" },
      { endpoint: "POST /api/fishing/rods", purpose: "Rod catalog writes", status: "Unverified schema" },
      { endpoint: "PUT /api/fishing/drop-weights", purpose: "Catch chance edits", status: "Unverified schema" },
      { endpoint: "forced fishing drops", purpose: "Create/clear forced_fishing_drops", status: "Endpoint needed" },
    ], "Advanced / Unverified Fishing Controls")}
    <div class="card"><h2>Raw Fishing Settings</h2>${table(raw.auto_activity_settings || [])}${table(raw.room_settings || [])}</div>
    <div class="card"><h2>Table Status</h2>${table(Object.entries(d.table_status || {}).map(([table_name, exists]) => ({ table_name, exists: exists ? "present" : "missing" })))}</div>
  `;
}

/* ── Quests & Missions ──────────────────────────────── */
function renderQuestsMissionsPage(tab) {
  return `
    <div class="card page-hero-card">
      <div>
        <h2>📜 Quests & Missions</h2>
        <p class="muted">Daily, weekly, event, player progress and reward tables. Writes only appear when a verified quest catalog exists.</p>
      </div>
      <button class="btn ghost sm" data-admin-page="Economy & Rewards">Economy Summary</button>
    </div>
    ${tabNav("Quests & Missions")}
    ${tab === "Overview"        ? renderQuestOverviewPage() : ""}
    ${tab === "Quest Catalog"   ? renderQuestCatalogPage() : ""}
    ${tab === "Daily Quests"    ? renderQuestListPage("Daily Quests", state.data?.daily_quests || []) : ""}
    ${tab === "Weekly Quests"   ? renderQuestListPage("Weekly Quests", state.data?.weekly_quests || []) : ""}
    ${tab === "Event Quests"    ? renderQuestListPage("Event Quests", state.data?.event_quests || []) : ""}
    ${tab === "Player Progress" ? renderQuestProgressPage() : ""}
    ${tab === "Rewards"         ? renderQuestRewardsPage() : ""}
    ${tab === "Logs"            ? renderQuestLogsPage() : ""}
    ${tab === "Advanced"        ? renderQuestAdvancedPage() : ""}
  `;
}

function questColumns() {
  return [
    { key: "quest_id", label: "Quest ID", render: (r) => esc(r.quest_id || r.mission_id || r.id || r.key || "—") },
    { key: "name", label: "Name", render: (r) => esc(r.name || r.title || r.display_name || "—") },
    { key: "category", label: "Category", render: (r) => esc(r.category || r.quest_type || r.target_type || "—") },
    { key: "period", label: "Period", render: (r) => esc(r.period || r.quest_period || r.frequency || r.period_key || "—") },
    { key: "target_amount", label: "Target", render: (r) => esc(r.target_amount ?? r.required_amount ?? r.goal_amount ?? r.target ?? "—") },
    { key: "reward_coins", label: "Coins", render: (r) => Number(r.reward_coins ?? r.coins ?? 0).toLocaleString() },
    { key: "reward_xp", label: "XP", render: (r) => Number(r.reward_xp ?? r.xp_reward ?? 0).toLocaleString() },
    { key: "enabled", label: "Status", render: (r) => pill(String(r.enabled ?? r.active ?? r.is_active ?? "read-only")) },
  ];
}

function questProgressColumns() {
  return [
    { key: "username", label: "Player", render: (r) => `${esc(r.username || "Unknown Player")}${r.fallback_id ? `<div class="muted text-sm">id ${esc(r.fallback_id)}...</div>` : ""}` },
    { key: "quest_id", label: "Quest", render: (r) => esc(r.quest_id || r.mission_id || r.name || r.title || "—") },
    { key: "progress", label: "Progress", render: (r) => {
      const pct = r.completion_percent;
      const current = r.current_progress ?? r.progress ?? r.current_amount ?? "—";
      const target = r.target_progress ?? r.target_amount ?? r.required_amount ?? "";
      return `<div>${esc(current)}${target !== "" ? ` / ${esc(target)}` : ""}</div>${pct != null ? `<div class="mini-progress"><span style="width:${Math.max(0, Math.min(100, Number(pct)))}%"></span></div><div class="muted text-sm">${pct}%</div>` : ""}`;
    } },
    { key: "period_key", label: "Period", render: (r) => esc(r.period_key || r.period || "—") },
    { key: "claimed", label: "Reward", render: (r) => r.claimed ? pill("claimed") : pill("pending") },
    { key: "updated_at", label: "Updated", render: (r) => esc(r.updated_at || r.completed_at || r.claimed_at || r.created_at || "—") },
  ];
}

function renderQuestOverviewPage() {
  const d = state.data || {};
  const o = d.overview || {};
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr));margin-bottom:14px">
      ${metricCard("Active Quests", o.active_quests ?? 0, "verified catalog rows", "accent-green", "📜")}
      ${metricCard("Daily Quests", o.daily_quests ?? 0, "daily/mission rows", "", "☀️")}
      ${metricCard("Weekly Quests", o.weekly_quests ?? 0, "weekly rows", "", "📅")}
      ${metricCard("Event Quests", o.event_quests ?? 0, "event rows", "", "🎉")}
      ${metricCard("Players Progressing", o.players_with_progress ?? 0, "quest_progress + player_missions", "accent-cyan", "👤")}
      ${metricCard("Pending Rewards", o.pending_rewards ?? 0, "pending_coin_rewards", "", "🎁")}
      ${metricCard("Completed Today", o.completed_today ?? 0, "completed rows", "", "✅")}
      ${metricCard("Claimed Today", o.claimed_rewards_today ?? 0, "reward claims", "", "🏆")}
    </div>
    <div class="grid">
      <div class="card"><h2>Daily Quests</h2>${table((d.daily_quests || []).slice(0, 8), questColumns())}</div>
      <div class="card"><h2>Recent Player Progress</h2>${table((d.progress?.rows || d.quest_progress || []).slice(0, 12), questProgressColumns())}</div>
    </div>
    <div class="card">
      <h2>Quest Source Status</h2>
      ${table(Object.entries(d.table_status || {}).map(([table_name, exists]) => ({ table_name, status: exists ? "present" : "missing" })), [
        { key: "table_name", label: "Table" },
        { key: "status", label: "Status", render: (r) => pill(r.status) },
      ])}
    </div>
  `;
}

function renderQuestCatalogPage() {
  const d = state.data || {};
  const catalog = d.catalog || {};
  const rows = catalog.rows || d.player_mission_sets || [];
  const writable = state.user?.role === "owner" && catalog.writable;
  return `
    <div class="card">
      <div class="card-header">
        <div>
          <h2>Quest Catalog</h2>
          <div class="muted text-sm">Source: <code>${esc(catalog.table || "no verified catalog")}</code></div>
        </div>
        ${pill(writable ? "writable" : "read only")}
      </div>
      ${table(rows, questColumns(), writable ? (r) => {
        const id = r.quest_id || r.mission_id || r.id || r.key;
        return `<button class="btn danger sm" data-quest-disable="${esc(id)}">Disable</button>`;
      } : null)}
    </div>
    ${writable ? `<div class="card">
      <h2>Add Quest</h2>
      <form id="questCatalogCreateForm" class="settings-form">
        <div class="field"><label class="field-label">Quest ID</label><input name="quest_id" required placeholder="daily_mine_10" /></div>
        <div class="field"><label class="field-label">Name</label><input name="name" required placeholder="Mine 10 Times" /></div>
        <div class="field"><label class="field-label">Description</label><input name="description" /></div>
        <div class="field"><label class="field-label">Category</label><input name="category" placeholder="mining" /></div>
        <div class="field"><label class="field-label">Target Type</label><input name="target_type" placeholder="mine" /></div>
        <div class="field"><label class="field-label">Target Amount</label><input name="target_amount" type="number" min="0" /></div>
        <div class="field"><label class="field-label">Reward Coins</label><input name="reward_coins" type="number" min="0" /></div>
        <div class="field"><label class="field-label">Reward XP</label><input name="reward_xp" type="number" min="0" /></div>
        <div class="field"><label class="field-label">Reward Item</label><input name="reward_item" /></div>
        <div class="field"><label class="field-label">Period</label><select name="period"><option>daily</option><option>weekly</option><option>event</option><option>one-time</option></select></div>
        <label class="switch"><input type="checkbox" name="enabled" checked /><span>Enabled</span></label>
        <div class="field"><label class="field-label">Reason</label><input name="reason" required placeholder="Required audit reason" /></div>
        <button class="btn primary">Add Quest</button>
      </form>
    </div>
    <div class="card">
      <h2>Edit Quest</h2>
      <form id="questCatalogUpdateForm" class="settings-form">
        <div class="field"><label class="field-label">Quest ID</label><input name="quest_id" required placeholder="existing quest_id" /></div>
        <div class="field"><label class="field-label">Name</label><input name="name" /></div>
        <div class="field"><label class="field-label">Description</label><input name="description" /></div>
        <div class="field"><label class="field-label">Category</label><input name="category" /></div>
        <div class="field"><label class="field-label">Target Amount</label><input name="target_amount" type="number" min="0" /></div>
        <div class="field"><label class="field-label">Reward Coins</label><input name="reward_coins" type="number" min="0" /></div>
        <div class="field"><label class="field-label">Reward XP</label><input name="reward_xp" type="number" min="0" /></div>
        <label class="switch"><input type="checkbox" name="enabled" checked /><span>Enabled</span></label>
        <div class="field"><label class="field-label">Reason</label><input name="reason" required placeholder="Required audit reason" /></div>
        <button class="btn primary">Save Quest Changes</button>
      </form>
    </div>` : futureControls([
      { endpoint: "POST /api/quests/catalog", purpose: "Add/edit quest definitions", status: catalog.message || "Unverified schema" },
      { endpoint: "DELETE /api/quests/catalog/:id", purpose: "Soft-disable/archive quest definitions", status: "Hidden until catalog schema is verified" },
    ], "Advanced / Future Quest Catalog Controls")}
  `;
}

function renderQuestListPage(title, rows) {
  return `<div class="card">
    <div class="card-header">
      <h2>${esc(title)}</h2>
      <span class="pill def">${rows.length} rows</span>
    </div>
    ${table(rows, questColumns())}
  </div>`;
}

function renderQuestProgressPage() {
  const d = state.data || {};
  const p = d.progress || {};
  const rows = state.questSearch ? (p.player_rows || []) : [...(p.rows || []), ...(p.player_missions || [])].slice(0, 300);
  return `
    <div class="card">
      <h2>Player Quest Progress</h2>
      <form id="questPlayerSearchForm" class="toolbar" style="flex-wrap:wrap;margin-bottom:12px">
        <input name="query" value="${esc(state.questSearch || "")}" placeholder="Search username, user ID, quest ID" style="flex:1;min-width:220px" />
        <button class="btn primary">Search Progress</button>
        ${state.questSearch ? `<button class="btn ghost" type="button" id="questSearchClear">Clear</button>` : ""}
      </form>
      ${table(rows, questProgressColumns())}
    </div>
    ${futureControls([
      { endpoint: "mark quest complete", purpose: "Requires exact bot-side reward/progress semantics", status: "Unverified schema" },
      { endpoint: "reset player quest", purpose: "Preserve live progress until reset source is confirmed", status: "Future" },
      { endpoint: "grant quest reward", purpose: "Use Player Grants for coins/items until quest reward flow is verified", status: "Future" },
    ], "Advanced / Future Progress Controls")}
  `;
}

function renderQuestRewardsPage() {
  const r = state.data?.rewards || {};
  return `<div class="grid">
    <div class="card"><h2>Pending Coin Rewards</h2>${table(r.pending_coin_rewards || [])}</div>
    <div class="card"><h2>Weekly Rewards</h2>${table(r.weekly_rewards || [])}</div>
    <div class="card"><h2>Weekly Snapshots</h2>${table(r.weekly_snapshots || [])}</div>
    <div class="card"><h2>Event Points</h2>${table(r.event_points || [])}</div>
  </div>
  ${futureControls([
    { endpoint: "clear pending reward", purpose: "Needs authoritative reward lifecycle source", status: "Unverified schema" },
    { endpoint: "weekly reward editor", purpose: "Only after reward columns are verified", status: "Future" },
  ], "Advanced / Future Reward Controls")}`;
}

function renderQuestLogsPage() {
  const logs = state.data?.logs || {};
  return `<div class="grid">
    <div class="card"><h2>Quest / Reward Audit Logs</h2>${table(logs.audit_logs || [])}</div>
    <div class="card"><h2>Quest Ledger Rows</h2>${table(logs.ledger || [])}</div>
  </div>`;
}

function renderQuestAdvancedPage() {
  const d = state.data || {};
  const tables = d.tables || {};
  return `
    ${futureControls(d.future_controls || [
      { endpoint: "quest catalog writes", purpose: "Create/edit/disable quests", status: "Unverified schema" },
      { endpoint: "quest reward actions", purpose: "Grant/reset/clear quest rewards", status: "Unverified schema" },
    ], "Advanced / Unverified Quest Controls")}
    <div class="card"><h2>Raw Quest Tables</h2>
      <h3>quest_progress</h3>${table(tables.quest_progress?.rows || [])}
      <h3>player_missions</h3>${table(tables.player_missions?.rows || [])}
      <h3>player_mission_sets</h3>${table(tables.player_mission_sets?.rows || [])}
      <h3>event_settings</h3>${table(tables.event_settings?.rows || [])}
    </div>
    <div class="card"><h2>Schema Warnings</h2>${table(Object.entries(d.columns || {}).map(([table_name, cols]) => ({ table_name, columns: (cols || []).join(", ") || "missing" })))}</div>
  `;
}

/* ── Automation Center ──────────────────────────────── */
function renderAutomationCenterPage(tab) {
  return `
    <div class="card page-hero-card">
      <div>
        <h2>⏱️ Automation Center</h2>
        <p class="muted">Scheduled announcements, promos, reminders, staff alerts and queued room delivery. Live sends go through bot_command_queue.</p>
      </div>
      <button class="btn ghost sm" data-admin-page="Room & Content">Room Tools</button>
    </div>
    ${tabNav("Automation Center")}
    ${tab === "Overview" ? renderAutomationOverview() : ""}
    ${tab === "Scheduled Announcements" ? renderAutomationScheduled() : ""}
    ${tab === "Rotating Announcements" ? renderAutomationRotating() : ""}
    ${tab === "Event Reminders" ? renderAutomationEventReminders() : ""}
    ${tab === "Promo Messages" ? renderAutomationPromos() : ""}
    ${tab === "Staff Alerts" ? renderAutomationStaffAlerts() : ""}
    ${tab === "Delivery Queue" ? renderAutomationDeliveryQueue() : ""}
    ${tab === "Logs" ? renderAutomationLogs() : ""}
    ${tab === "Advanced" ? renderAutomationAdvanced() : ""}
  `;
}

function automationScheduledRows() {
  return state.data?.scheduled_announcements || state.data?.announcements || [];
}

function automationQueueHelp() {
  return `<div class="notice" style="margin-top:12px">Send Now queues <code>target_bot='host'</code>, <code>action='announce'</code>. The host bot must consume <code>bot_command_queue</code>.</div>`;
}

function automationMessagePreview(message) {
  const text = String(message || "");
  return `<div class="message-preview">${esc(text || "Message preview")}</div><div class="muted text-sm">${text.length}/500 characters</div>`;
}

function renderAutomationOverview() {
  const d = state.data || {};
  const o = d.overview || {};
  const next = o.next_announcement || {};
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(170px,1fr));margin-bottom:14px">
      ${metricCard("Active Automations", o.active_automations ?? 0, "scheduled + rotating", "accent-green", "⏱️")}
      ${metricCard("Scheduled", o.scheduled_announcements ?? 0, "dashboard_scheduled_announcements", "", "📅")}
      ${metricCard("Rotating", boolLabel(o.rotating_enabled), "announcements_enabled", truthy(o.rotating_enabled) ? "accent-green" : "accent-red", "🔁")}
      ${metricCard("Next Announcement", next.title || next.next_run_at || "None", next.next_run_at || "manual only", "accent-cyan", "➡️")}
      ${metricCard("Recent Deliveries", o.recent_deliveries ?? 0, "completed queue rows", "", "✅")}
      ${metricCard("Failed Deliveries", o.failed_deliveries ?? 0, "failed queue rows", o.failed_deliveries ? "accent-red" : "", "⚠️")}
      ${metricCard("Host Queue", o.host_queue_pending ?? 0, "pending/claimed/running", "", "📨")}
      ${metricCard("DJ Promo", o.dj_promo_status || "Unknown", "room setting if present", "", "📻")}
    </div>
    <div class="grid">
      <div class="card">
        <h2>Next Scheduled Announcement</h2>
        ${next.id ? table([next], automationScheduleColumns()) : `<div class="notice">No enabled scheduled announcement with a next run time.</div>`}
        ${d.scheduler_note ? `<div class="notice warn" style="margin-top:12px">${esc(d.scheduler_note)}</div>` : ""}
      </div>
      <div class="card">
        <h2>Delivery Queue Snapshot</h2>
        ${renderQueuedBotCommands(d.delivery_queue || {})}
      </div>
    </div>
  `;
}

function automationScheduleColumns() {
  return [
    { key: "title", label: "Title", render: (r) => esc(r.title || `Announcement #${r.id || ""}`) },
    { key: "message", label: "Message", render: (r) => `<span title="${esc(r.message || "")}">${esc(String(r.message || "").slice(0, 90))}${String(r.message || "").length > 90 ? "..." : ""}</span>` },
    { key: "target_bot", label: "Target" },
    { key: "schedule_type", label: "Schedule" },
    { key: "interval_minutes", label: "Interval", render: (r) => r.interval_minutes ? `${esc(r.interval_minutes)} min` : "—" },
    { key: "next_run_at", label: "Next Run" },
    { key: "last_sent_at", label: "Last Sent" },
    { key: "enabled", label: "Status", render: (r) => pill(Number(r.enabled) ? "enabled" : "disabled") },
  ];
}

function renderAutomationScheduled() {
  const rows = automationScheduledRows();
  const owner = state.user?.role === "owner";
  return `
    <div class="card">
      <div class="card-header">
        <div><h2>Scheduled Announcements</h2><div class="muted text-sm">Source: <code>dashboard_scheduled_announcements</code></div></div>
        <span class="pill info">Dashboard owned</span>
      </div>
      ${table(rows, automationScheduleColumns(), (r) => `
        <button class="btn cyan sm" data-automation-send="${esc(r.id)}">Send Now</button>
        ${owner ? `<button class="btn danger sm" data-automation-archive="${esc(r.id)}">Archive</button>` : ""}
      `)}
      ${automationQueueHelp()}
    </div>
    ${owner ? `<div class="grid">
      <div class="card">
        <h2>Add Scheduled Announcement</h2>
        <form id="automationAnnouncementForm" class="settings-form">
          <div class="field"><label class="field-label">Title</label><input name="title" placeholder="Weekend mining promo" /></div>
          <div class="field"><label class="field-label">Message</label><textarea name="message" rows="4" maxlength="500" required placeholder="Room message"></textarea></div>
          <div class="field"><label class="field-label">Target Bot</label><select name="target_bot"><option value="host">host</option><option value="dj">dj</option><option value="security">security</option></select></div>
          <div class="field"><label class="field-label">Schedule Type</label><select name="schedule_type"><option>manual</option><option>once</option><option>interval</option><option>daily</option><option>weekly</option></select></div>
          <div class="field"><label class="field-label">Interval Minutes</label><input name="interval_minutes" type="number" min="1" max="43200" /></div>
          <div class="field"><label class="field-label">Next Run At</label><input name="next_run_at" placeholder="YYYY-MM-DD HH:MM or ISO" /></div>
          <label class="switch"><input type="checkbox" name="enabled" checked /><span>Enabled</span></label>
          <button class="btn primary">Add Announcement</button>
        </form>
      </div>
      <div class="card">
        <h2>Edit Announcement</h2>
        <form id="automationAnnouncementEditForm" class="settings-form">
          <div class="field"><label class="field-label">Announcement ID</label><input name="id" required /></div>
          <div class="field"><label class="field-label">Title</label><input name="title" /></div>
          <div class="field"><label class="field-label">Message</label><textarea name="message" rows="4" maxlength="500"></textarea></div>
          <div class="field"><label class="field-label">Target Bot</label><input name="target_bot" placeholder="host" /></div>
          <div class="field"><label class="field-label">Schedule Type</label><input name="schedule_type" /></div>
          <div class="field"><label class="field-label">Interval Minutes</label><input name="interval_minutes" type="number" min="1" max="43200" /></div>
          <div class="field"><label class="field-label">Next Run At</label><input name="next_run_at" /></div>
          <label class="switch"><input type="checkbox" name="enabled" checked /><span>Enabled</span></label>
          <button class="btn primary">Save Changes</button>
        </form>
      </div>
    </div>` : `<div class="notice">Owner role required to edit scheduled announcements.</div>`}
  `;
}

function renderAutomationRotating() {
  const d = state.data || {};
  const rows = d.rows || d.rotating_announcements || [];
  const tableInfo = d.table || d.tables?.rotating_announcements || {};
  const owner = state.user?.role === "owner";
  return `<div class="grid">
    <div class="card">
      <div class="card-header"><h2>Rotating Announcements</h2>${pill(tableInfo.exists ? "connected" : "missing")}</div>
      ${tableInfo.exists ? table(rows, null, (r) => `
        <button class="btn cyan sm" data-automation-rotating-send="${esc(r.id || "")}">Send Now</button>
        ${owner && r.id !== undefined ? `<button class="btn danger sm" data-automation-rotating-disable="${esc(r.id)}">Disable</button>` : ""}
      `) : `<div class="notice">rotating_announcements is not present in this DB.</div>`}
    </div>
    ${owner && tableInfo.exists ? `<div class="card">
      <h2>Add Rotating Announcement</h2>
      <form id="automationRotatingForm" class="settings-form">
        <div class="field"><label class="field-label">Message</label><textarea name="message" maxlength="500" rows="4" required></textarea></div>
        <button class="btn primary">Add Rotating Message</button>
      </form>
    </div>` : ""}
  </div>`;
}

function renderAutomationEventReminders() {
  const e = state.data?.event_reminders || {};
  return `<div class="grid">
    <div class="card"><h2>Event Definitions</h2>${table(e.event_definitions || [])}</div>
    <div class="card"><h2>Event Settings</h2>${table(e.event_settings || [])}</div>
    <div class="card"><h2>Event History</h2>${table(e.event_history || [])}</div>
    <div class="card">
      <h2>Send Event Reminder</h2>
      <form class="settings-form automationSendForm">
        <input type="hidden" name="source" value="event_reminder" />
        <input type="hidden" name="target_bot" value="host" />
        <div class="field"><label class="field-label">Reminder Message</label><textarea name="message" maxlength="500" rows="4" required placeholder="Event starts soon!"></textarea></div>
        <button class="btn primary">Queue Event Reminder</button>
      </form>
      ${automationQueueHelp()}
    </div>
  </div>
  ${futureControls([{ endpoint: "recurring event reminder scheduler", purpose: "Needs bot-side scheduler/consumer for timed delivery", status: "Future" }], "Advanced / Future Event Reminder Controls")}`;
}

function renderAutomationPromos() {
  const promos = state.data?.promo_messages || [];
  return `<div class="grid">
    ${promos.map((p) => `<div class="card">
      <div class="card-header"><h2>${esc(p.title)}</h2><span class="pill def">${esc(p.category)}</span></div>
      ${automationMessagePreview(p.message)}
      <button class="btn cyan sm" data-automation-promo="${esc(p.message)}" data-automation-source="${esc(p.category)}">Send Now</button>
    </div>`).join("") || `<div class="card"><div class="notice">No promo templates loaded.</div></div>`}
    <div class="card">
      <h2>Custom Promo</h2>
        <form class="settings-form automationSendForm">
        <input type="hidden" name="source" value="promo_message" />
        <div class="field"><label class="field-label">Target Bot</label><select name="target_bot"><option value="host">host</option><option value="dj">dj</option></select></div>
        <div class="field"><label class="field-label">Message</label><textarea name="message" maxlength="500" rows="4" required></textarea></div>
        <button class="btn primary">Queue Promo</button>
      </form>
      ${automationQueueHelp()}
    </div>
  </div>`;
}

function renderAutomationStaffAlerts() {
  const a = state.data?.staff_alerts || {};
  return `<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(170px,1fr));margin-bottom:14px">
    ${metricCard("Pending Reports", a.pending_reports ?? "—", "reports", "", "🛡️")}
    ${metricCard("Failed Commands", a.failed_bot_commands ?? 0, "bot_command_queue", a.failed_bot_commands ? "accent-red" : "", "⚠️")}
    ${metricCard("Radio Failures", a.radio_failures ?? "—", "yt_request_jobs", a.radio_failures ? "accent-red" : "", "📻")}
    ${metricCard("DB Warnings", a.db_warnings ?? 0, "maintenance health", a.db_warnings ? "accent-red" : "accent-green", "🧰")}
  </div>
  <div class="card">
    <h2>Queue Staff Alert</h2>
    <form class="settings-form automationSendForm">
      <input type="hidden" name="source" value="staff_alert" />
      <input type="hidden" name="target_bot" value="security" />
      <div class="field"><label class="field-label">Alert Message</label><textarea name="message" maxlength="500" rows="4" required></textarea></div>
      <button class="btn primary">Queue Staff Alert</button>
    </form>
    <div class="notice">This queues a safe announcement-style command. It does not DM staff unless bot-side support consumes it that way.</div>
  </div>`;
}

function renderAutomationDeliveryQueue() {
  return `<div class="card">
    <h2>Automation Delivery Queue</h2>
    ${renderQueuedBotCommands(state.data?.delivery_queue || {})}
  </div>`;
}

function renderAutomationLogs() {
  const logs = state.data?.logs || {};
  return `<div class="grid">
    <div class="card"><h2>Audit Logs</h2>${table(logs.audit_logs || [])}</div>
    <div class="card"><h2>Big Announcement Logs</h2>${table(logs.big_announcement_logs || [])}</div>
    <div class="card"><h2>Host DM Queue</h2>${table(logs.host_dm_queue || [])}</div>
    <div class="card"><h2>Failed Commands</h2>${table(logs.failed_commands || [])}</div>
    <div class="card"><h2>Admin Action Logs</h2>${table(logs.admin_action_logs || [])}</div>
  </div>`;
}

function renderAutomationAdvanced() {
  const d = state.data || {};
  const tables = d.tables || {};
  return `
    ${futureControls([
      { endpoint: "recurring scheduler consumer", purpose: "Run due dashboard_scheduled_announcements automatically", status: "Needs bot/external scheduler" },
      { endpoint: "hard delete announcement", purpose: "Requires typed confirmation DELETE ANNOUNCEMENT via API", status: "Advanced only" },
      { endpoint: "host_dm_queue writes", purpose: "Schema varies; keep read-only until verified", status: "Unverified schema" },
    ], "Advanced / Future Automation Controls")}
    <div class="card"><h2>Raw Automation Tables</h2>
      <h3>dashboard_scheduled_announcements</h3>${table(tables.dashboard_scheduled_announcements?.rows || [])}
      <h3>subscriber_announcements</h3>${table(tables.subscriber_announcements?.rows || [])}
      <h3>release_announcements</h3>${table(tables.release_announcements?.rows || [])}
      <h3>first_find_announce_pending</h3>${table(tables.first_find_announce_pending?.rows || [])}
      <h3>bot_settings</h3>${table(tables.bot_settings?.rows || [])}
      <h3>room_settings</h3>${table(tables.room_settings?.rows || [])}
    </div>
    <div class="card"><h2>Table Status</h2>${table(Object.entries(d.table_status || {}).map(([table_name, exists]) => ({ table_name, status: exists ? "present" : "missing" })), [
      { key: "table_name", label: "Table" },
      { key: "status", label: "Status", render: (r) => pill(r.status) },
    ])}</div>
  `;
}

/* ── Economy & Rewards ───────────────────────────────── */
function renderEconomyRewards(tab) {
  return `
    ${tabNav("Economy & Rewards")}
    ${tab === "Overview"        ? renderEconomyOverview() : ""}
    ${tab === "Casino"          ? renderCasinoTab() : ""}
    ${tab === "Mining"          ? renderMiningTab() : ""}
    ${tab === "Fishing"         ? renderFishingTab() : ""}
    ${tab === "Games"           ? renderGamesTab() : ""}
    ${tab === "Economy"         ? renderCoinsTab() : ""}
    ${tab === "VIP"             ? renderVipTab() : ""}
    ${tab === "Titles"          ? renderTitlesTab() : ""}
    ${tab === "Badges"          ? renderBadgesTab() : ""}
    ${tab === "Rewards"         ? renderRewardsTab() : ""}
    ${tab === "Shop"            ? renderShopTab() : ""}
    ${tab === "Quests"          ? renderQuestsTab() : ""}
    ${tab === "Player Grants"   ? renderPlayerGrantsTab() : ""}
    ${tab === "Logs"            ? renderRewardLogsTab() : ""}
    ${tab === "Advanced"        ? renderRewardsAdvancedTab() : ""}
  `;
}

function renderEconomyOverview() {
  const d = state.data || {};
  const s = d.stats || {};
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr))">
      ${metricCard("Players", s.player_count ?? "—", "in database", "", "👤")}
      ${metricCard("Total Balance", s.total_balance != null ? Number(s.total_balance).toLocaleString() : "—", "coins", "accent-green", "💰")}
      ${metricCard("Average Balance", s.avg_balance != null ? Math.round(Number(s.avg_balance)).toLocaleString() : "—", "per player", "", "📊")}
      ${metricCard("Richest Balance", s.richest_balance != null ? Number(s.richest_balance).toLocaleString() : "—", "single player", "", "🏆")}
    </div>
    <div class="grid">
      <div class="card">
        <h2>Rich List</h2>
        ${table(d.top_rich || [], [
          { key: "username", label: "Player" },
          { key: "balance", label: "Balance", render: (r) => Number(r.balance ?? 0).toLocaleString() },
          { key: "level", label: "Level" },
        ])}
      </div>
      <div class="card">
        <h2>Top XP</h2>
        ${table(d.top_xp || [], [
          { key: "username", label: "Player" },
          { key: "xp", label: "XP", render: (r) => Number(r.xp ?? 0).toLocaleString() },
          { key: "level", label: "Level" },
        ])}
      </div>
    </div>
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

function renderCasinoOwnerPage() {
  return `
    <div class="card page-hero-card">
      <div>
        <h2>🎲 Casino</h2>
        <p class="muted">Verified Blackjack and Poker settings. Legacy and unverified controls stay collapsed in Advanced sections.</p>
      </div>
    </div>
    ${renderCasinoTab()}
  `;
}

function renderEventsOwnerPage() {
  return `
    <div class="card page-hero-card">
      <div>
        <h2>🎉 Events</h2>
        <p class="muted">Read live event tables and queue safe host-bot event commands.</p>
      </div>
    </div>
    ${renderEventsTab()}
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
        <div class="muted text-sm">Source: <code>poker_settings.max_buyin</code> for the !join buy-in limit. V2 mirror keys stay in Advanced.</div>
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
  const verified = new Set(["poker_enabled", "min_buyin", "max_buyin", "max_players", "turn_timer", "small_blind", "big_blind"]);
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

function renderMiningTab() {
  const d = state.data || {};
  const s = d.settings || {};
  const status = d.table_status || {};
  const fields = [
    { key: "mining_enabled", label: "Mining Enabled", type: "toggle", hint: "mining_settings.mining_enabled" },
    { key: "base_cooldown_seconds", label: "Mine Cooldown", type: "number", suffix: "sec", hint: "mining_settings.base_cooldown_seconds" },
    { key: "mining_requires_room", label: "Requires Room", type: "toggle", hint: "mining_settings.mining_requires_room" },
    { key: "mining_announce_enabled", label: "Mining Announcements", type: "toggle", hint: "mining_settings.mining_announce_enabled" },
    { key: "mining_announce_min_rarity", label: "Announce Minimum Rarity", type: "select", options: [
      ["common", "Common"], ["uncommon", "Uncommon"], ["rare", "Rare"], ["epic", "Epic"],
      ["legendary", "Legendary"], ["mythic", "Mythic"], ["ultra_rare", "Prismatic / Ultra Rare"], ["exotic", "Exotic"],
    ] },
    { key: "normal_multiplier_cap", label: "Normal Multiplier Cap", type: "number", suffix: "x", hint: "mining_settings.normal_multiplier_cap" },
    { key: "blessing_multiplier_cap", label: "Blessing Multiplier Cap", type: "number", suffix: "x", hint: "mining_settings.blessing_multiplier_cap" },
    { key: "weights_enabled", label: "Ore Weights Enabled", type: "toggle", hint: "mining_weight_settings.weights_enabled" },
    { key: "weight_value_multiplier_scale", label: "Ore Value Weight Scale", type: "number", suffix: "x", hint: "mining_weight_settings.weight_value_multiplier_scale" },
    { key: "weight_lb_mode", label: "Weight Leaderboard Mode", type: "select", options: [["best", "Best per player"], ["all", "All records"]] },
    { key: "automine_enabled", label: "Auto Mining Enabled", type: "toggle", hint: "auto_activity_settings.automine_enabled" },
  ];
  const raw = d.raw || {};
  const tables = d.tables || {};
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr));margin-bottom:14px">
      ${metricCard("Mining Settings", status.mining_settings ? "OK" : "Missing", "mining_settings", status.mining_settings ? "accent-green" : "accent-red", "⛏️")}
      ${metricCard("Weight Settings", status.mining_weight_settings ? "OK" : "Missing", "mining_weight_settings", status.mining_weight_settings ? "accent-green" : "", "⚖️")}
      ${metricCard("Ore Items", (tables.mining_items || []).length, "shown", "", "💎")}
      ${metricCard("Forced Drops", (tables.forced_mining_drops || []).length, "recent rows", "", "🎯")}
    </div>
    <div class="card">
      <div class="card-header">
        <div>
          <h2>⛏️ Mining Settings</h2>
          <div class="muted text-sm">Source: <code>${esc(s.source || "mining_settings")}</code></div>
        </div>
        <span class="pill info">Verified</span>
      </div>
      <form id="miningSettingsForm">
        <div class="settings-fields">
          ${fields.map((f) => renderSettingsField(f, s[f.key])).join("")}
        </div>
        <div class="notice" style="margin-top:12px">Only settings read by active mining commands are writable here. Missing optional tables are skipped safely.</div>
        <div style="margin-top:14px">
          <button class="btn primary sm" type="submit">💾 Save Mining Settings</button>
        </div>
      </form>
    </div>
    <div class="card">
      <div class="card-header">
        <h2>💎 Ore Values</h2>
        <span class="pill def">Read-only</span>
      </div>
      ${table(tables.mining_items || [], [
        { key: "name", label: "Ore" },
        { key: "rarity", label: "Rarity" },
        { key: "item_type", label: "Type" },
        { key: "sell_value", label: "Value", render: (r) => Number(r.sell_value || 0).toLocaleString() },
        { key: "drop_enabled", label: "Drops", render: (r) => pill(Number(r.drop_enabled) ? "enabled" : "disabled") },
      ])}
    </div>
    <details class="advanced-collapse">
      <summary class="advanced-summary">
        <span class="pill warn">Advanced</span> Raw Mining Settings
        <span class="muted text-sm">Read-only audit view</span>
      </summary>
      <div class="advanced-content">
        <h3>mining_settings</h3>
        ${table(raw.mining_settings || [], [{ key: "key", label: "Key" }, { key: "value", label: "Value" }])}
        <h3>mining_weight_settings</h3>
        ${table(raw.mining_weight_settings || [], [{ key: "key", label: "Key" }, { key: "value", label: "Value" }])}
        <h3>auto_activity_settings</h3>
        ${table(raw.auto_activity_settings || [], [{ key: "key", label: "Key" }, { key: "value", label: "Value" }])}
        <h3>gold settings</h3>
        ${table(raw.gold_settings || [], [{ key: "table", label: "Table" }, { key: "key", label: "Key" }, { key: "value", label: "Value" }])}
      </div>
    </details>
    <details class="advanced-collapse">
      <summary class="advanced-summary">
        <span class="pill warn">Advanced</span> Mining Events / Forced Drops / Weights
        <span class="muted text-sm">Read-only tables</span>
      </summary>
      <div class="advanced-content">
        <h3>mining_events</h3>
        ${table(tables.mining_events || [])}
        <h3>forced_mining_drops</h3>
        ${table(tables.forced_mining_drops || [])}
        <h3>ore_weight_records</h3>
        ${table(tables.ore_weight_records || [])}
      </div>
    </details>
    ${futureControls([
      { endpoint: "ore value writes", purpose: "Update mining_items sell values/drop flags", status: "Unverified source" },
      { endpoint: "forced mining drops", purpose: "Create/clear forced_mining_drops rows", status: "Endpoint needed" },
      { endpoint: "rarity weight range editor", purpose: "Structured editor for rarity_weight_ranges_json", status: "Endpoint needed" },
      { endpoint: "gold rain controls", purpose: "Gold rain is separate from mining; keep raw only here", status: "Unverified for mining" },
    ], "Advanced / Unverified Mining Controls")}
  `;
}

function renderFishingTab() {
  const d = state.data || {};
  const s = d.settings || {};
  const stats = d.stats || {};
  const status = d.table_status || {};
  const raw = d.raw || {};
  const tables = d.tables || {};
  const fields = [
    { key: "autofish_enabled", label: "AutoFish Enabled", type: "toggle", hint: "auto_activity_settings.autofish_enabled" },
    { key: "fish_base_duration", label: "Base Auto Time", type: "number", suffix: "min", hint: "auto_activity_settings.fish_base_duration" },
    { key: "fish_base_interval", label: "Base Cast Interval", type: "number", suffix: "sec", hint: "auto_activity_settings.fish_base_interval" },
    { key: "fish_min_interval", label: "Minimum Cast Interval", type: "number", suffix: "sec", hint: "auto_activity_settings.fish_min_interval" },
    { key: "fish_base_luck", label: "Base Luck", type: "number", hint: "auto_activity_settings.fish_base_luck" },
    { key: "fish_vip_luck", label: "VIP Luck Bonus", type: "number", hint: "auto_activity_settings.fish_vip_luck" },
    { key: "fish_vip_duration", label: "VIP Duration Bonus", type: "number", suffix: "min", hint: "auto_activity_settings.fish_vip_duration" },
    { key: "fish_vip_speed", label: "VIP Speed Bonus", type: "number", suffix: "sec", hint: "auto_activity_settings.fish_vip_speed" },
    { key: "autofish_duration_minutes", label: "AutoFish Session Duration", type: "number", suffix: "min", hint: "auto_activity_settings.autofish_duration_minutes" },
    { key: "autofish_max_attempts", label: "AutoFish Max Attempts", type: "number", hint: "auto_activity_settings.autofish_max_attempts" },
    { key: "autofish_daily_cap_minutes", label: "AutoFish Daily Cap", type: "number", suffix: "min", hint: "auto_activity_settings.autofish_daily_cap_minutes" },
  ];
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr));margin-bottom:14px">
      ${metricCard("Fishing Settings", status.auto_activity_settings ? "OK" : "Missing", "auto_activity_settings", status.auto_activity_settings ? "accent-green" : "accent-red", "🎣")}
      ${metricCard("Fishers", Number(stats.fish_profiles || 0).toLocaleString(), "profiles", "", "👤")}
      ${metricCard("Catches", Number(stats.catch_records || 0).toLocaleString(), "records", "", "📈")}
      ${metricCard("Unsold Bag Value", Number(stats.unsold_inventory_value || 0).toLocaleString(), "coins", "accent-green", "💰")}
    </div>
    <div class="card">
      <div class="card-header">
        <div>
          <h2>🎣 Fishing Settings</h2>
          <div class="muted text-sm">Source: <code>${esc(s.source || "auto_activity_settings")}</code></div>
        </div>
        <span class="pill info">Verified</span>
      </div>
      <form id="fishingSettingsForm">
        <div class="settings-fields">
          ${fields.map((f) => renderSettingsField(f, s[f.key])).join("")}
        </div>
        <div class="notice" style="margin-top:12px">These are the DB keys read by active AutoFish commands and the fishing luck stack. Manual fish catalog values are read-only code/catalog data.</div>
        <div style="margin-top:14px">
          <button class="btn primary sm" type="submit">💾 Save Fishing Settings</button>
        </div>
      </form>
    </div>
    <div class="grid">
      <div class="card">
        <div class="card-header">
          <h2>🐟 Fish Profiles</h2>
          <span class="pill def">Read-only</span>
        </div>
        ${table(tables.fish_profiles || [], [
          { key: "username", label: "Player" },
          { key: "fishing_level", label: "Level" },
          { key: "fishing_xp", label: "FXP", render: (r) => Number(r.fishing_xp || 0).toLocaleString() },
          { key: "total_catches", label: "Catches", render: (r) => Number(r.total_catches || 0).toLocaleString() },
          { key: "equipped_rod", label: "Rod" },
          { key: "best_fish_name", label: "Best Fish" },
          { key: "best_fish_weight", label: "Best Weight" },
          { key: "best_fish_value", label: "Best Value", render: (r) => Number(r.best_fish_value || 0).toLocaleString() },
        ])}
      </div>
      <div class="card">
        <div class="card-header">
          <h2>📦 Inventory Summary</h2>
          <span class="pill def">Read-only</span>
        </div>
        ${table(tables.fish_inventory || [], [
          { key: "username", label: "Player" },
          { key: "fish_name", label: "Fish" },
          { key: "rarity", label: "Rarity" },
          { key: "weight", label: "Weight" },
          { key: "value", label: "Value", render: (r) => Number(r.value || 0).toLocaleString() },
          { key: "sold", label: "Sold", render: (r) => pill(Number(r.sold) ? "yes" : "no") },
          { key: "caught_at", label: "Caught" },
        ])}
      </div>
    </div>
    <div class="card">
      <div class="card-header">
        <h2>🎣 Recent Catch Records</h2>
        <span class="pill def">Read-only</span>
      </div>
      ${table(tables.fish_catch_records || [], [
        { key: "username", label: "Player" },
        { key: "fish_name", label: "Fish" },
        { key: "rarity", label: "Rarity" },
        { key: "weight", label: "Weight" },
        { key: "base_value", label: "Base", render: (r) => Number(r.base_value || 0).toLocaleString() },
        { key: "final_value", label: "Final", render: (r) => Number(r.final_value || 0).toLocaleString() },
        { key: "fxp_earned", label: "FXP", render: (r) => Number(r.fxp_earned || 0).toLocaleString() },
        { key: "caught_at", label: "Caught" },
      ])}
    </div>
    <details class="advanced-collapse">
      <summary class="advanced-summary">
        <span class="pill warn">Advanced</span> Fishing Auto Sell / Forced Drops
        <span class="muted text-sm">Read-only tables</span>
      </summary>
      <div class="advanced-content">
        <h3>fish_auto_sell_settings</h3>
        ${table(tables.fish_auto_sell_settings || [], [
          { key: "username", label: "Player" },
          { key: "auto_sell_enabled", label: "Auto Sell", render: (r) => pill(Number(r.auto_sell_enabled) ? "enabled" : "disabled") },
          { key: "auto_sell_rare_enabled", label: "Rare Sell", render: (r) => pill(Number(r.auto_sell_rare_enabled) ? "enabled" : "disabled") },
          { key: "updated_at", label: "Updated" },
        ])}
        <h3>forced_fishing_drops</h3>
        ${table(tables.forced_fishing_drops || [])}
      </div>
    </details>
    <details class="advanced-collapse">
      <summary class="advanced-summary">
        <span class="pill warn">Advanced</span> Raw Fishing Settings
        <span class="muted text-sm">Read-only audit view</span>
      </summary>
      <div class="advanced-content">
        <h3>auto_activity_settings</h3>
        ${table(raw.auto_activity_settings || [], [{ key: "key", label: "Key" }, { key: "value", label: "Value" }])}
        <h3>room_settings</h3>
        ${table(raw.room_settings || [], [{ key: "key", label: "Key" }, { key: "value", label: "Value" }])}
        <h3>fish_weight_settings</h3>
        ${table(raw.fish_weight_settings || [])}
      </div>
    </details>
    ${futureControls([
      { endpoint: "!setfishcooldown / room_settings.fishing_base_cooldown", purpose: "Manual catch cooldown override", status: "Unverified source" },
      { endpoint: "!setfishweights / room_settings.fishing_weights_enabled", purpose: "Manual fish weight system toggle", status: "Unverified source" },
      { endpoint: "!setfishannounce / room_settings.fishing_announce_*", purpose: "Announcement threshold controls", status: "Unverified source" },
      { endpoint: "fish catalog editor", purpose: "Fish rarity/chance/base value edits", status: "Code/catalog source" },
      { endpoint: "forced fishing drops", purpose: "Create/clear forced_fishing_drops rows", status: "Endpoint needed" },
    ], "Advanced / Unverified Fishing Controls")}
  `;
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
    <div class="card">
      <h2>Recent Ledger</h2>
      ${table(d.transactions?.ledger || [], [
        { key: "timestamp", label: "Time" },
        { key: "username", label: "Player" },
        { key: "change_amount", label: "Change" },
        { key: "balance_after", label: "After" },
        { key: "reason", label: "Reason" },
      ])}
    </div>
    ${futureControls([
      { endpoint: "POST /api/player/:id/economy", purpose: "Adjust balance, tickets, or XP from Players page", status: "Connected" },
      { endpoint: "POST /api/economy/adjust", purpose: "Bulk economy adjustment", status: "Future" },
      { endpoint: "GET /api/economy/transactions", purpose: "Transaction history", status: "Connected" },
    ])}
  `;
}

function renderVipTab() {
  const d = state.data || {};
  const rows = d.vip_players || [];
  return `<div class="grid">
    <div class="card">
      <div class="card-header">
        <h2>⭐ VIP Players</h2>
        <span class="pill info">owned_items.item_id=vip</span>
      </div>
      ${table(rows, [
        { key: "username", label: "Player" },
        { key: "user_id", label: "User ID" },
        { key: "item_type", label: "Type" },
        { key: "acquired_at", label: "Acquired" },
      ], state.user?.role === "owner" ? (r) => `<button class="btn danger sm" data-vip-remove="${esc(r.user_id)}" data-vip-user="${esc(r.username)}">Remove VIP</button>` : null)}
    </div>
    <div class="card">
      <h2>Grant VIP</h2>
      ${state.user?.role === "owner" ? `<form id="vipGrantForm" class="settings-form">
        <div class="field"><label class="field-label">Username or User ID</label><input name="query" required /></div>
        <div class="field"><label class="field-label">Reason</label><input name="reason" required placeholder="Required audit reason" /></div>
        <button class="btn primary">Grant VIP</button>
      </form>` : `<div class="notice">Owner role required for VIP writes.</div>`}
      <div class="notice" style="margin-top:12px">VIP is verified as an owned item: <code>owned_items.item_id='vip'</code>. Duplicate VIP rows are avoided.</div>
    </div>
  </div>`;
}

function renderTitlesTab() {
  const d = state.data || {};
  const catalog = d.catalog || d.titles?.catalog || [];
  const assigned = d.assigned || d.titles?.assigned || [];
  const dashboardTitles = d.dashboard_titles || d.titles?.dashboard_titles || [];
  return `
    <div class="card">
      <h2>📚 Title Catalog</h2>
      ${table(catalog)}
    </div>
    ${state.user?.role === "owner" ? `<div class="card">
      <h2>Add / Edit Title Catalog</h2>
      <form id="titleCatalogForm" class="settings-form">
        <div class="field"><label class="field-label">Title ID</label><input name="title_id" required placeholder="founder" /></div>
        <div class="field"><label class="field-label">Display / Name</label><input name="display" placeholder="Founder" /></div>
        <div class="field"><label class="field-label">Tier / Rarity</label><input name="tier" placeholder="rare" /></div>
        <div class="field"><label class="field-label">Color</label><input name="color" placeholder="#a855f7" /></div>
        <div class="field"><label class="field-label">Description</label><input name="description" /></div>
        <div class="field"><label class="field-label">Reason</label><input name="reason" required /></div>
        <button class="btn primary">Add Title If Schema Allows</button>
      </form>
      <div class="notice" style="margin-top:12px">Writes only when <code>title_catalog.title_id</code> exists. Otherwise the endpoint returns <code>unverified_schema</code>.</div>
    </div>` : ""}
    <div class="card">
      <h2>📋 Assigned Titles</h2>
      ${table([...(assigned || []), ...(dashboardTitles || [])])}
    </div>
    ${futureControls([
      { endpoint: "Player Grants", purpose: "Give/remove verified user_titles through the selected player workflow", status: "Connected" },
      { endpoint: "set equipped title", purpose: "Only enabled if users equipped title source is verified for writes", status: "Future" },
    ])}
  `;
}

function renderBadgesTab() {
  const d = state.data || {};
  return `<div class="grid">
    <div class="card"><h2>🏅 Owned Badges</h2>${table(d.owned || d.badges?.owned || [])}</div>
    <div class="card"><h2>Badge Claims</h2>${table(d.claims || d.badges?.claims || [])}</div>
    <div class="card"><h2>Badge Market Listings</h2>${table(d.market_listings || [])}</div>
    <div class="card"><h2>Badge Trades</h2>${table(d.trades || [])}</div>
  </div>
  ${futureControls([
    { endpoint: "Player Grants", purpose: "Give/remove verified user_badges through the selected player workflow", status: "Connected" },
    { endpoint: "badge catalog writes", purpose: "No verified badge catalog table exists in live schema", status: "Unverified schema" },
  ])}`;
}

function renderRewardsTab() {
  const d = state.data || {};
  const o = d.overview || {};
  const r = d.rewards || {};
  return `<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr));margin-bottom:14px">
    ${metricCard("Pending Rewards", o.pending_rewards ?? 0, "pending_coin_rewards", "", "🎁")}
    ${metricCard("Weekly Rewards", o.weekly_rewards ?? 0, "weekly_rewards", "", "📅")}
    ${metricCard("VIP Players", o.vip_players ?? 0, "owned_items", "accent-green", "⭐")}
    ${metricCard("Owned Items", o.owned_items ?? 0, "owned_items", "", "🎒")}
  </div>
  <div class="grid">
    <div class="card"><h2>Pending Coin Rewards</h2>${table(r.pending_coin_rewards || [])}</div>
    <div class="card"><h2>Weekly Rewards</h2>${table(r.weekly_rewards || [])}</div>
    <div class="card"><h2>Onboarding Rewards</h2>${table(r.onboarding || [])}</div>
    <div class="card"><h2>Weekly Snapshots</h2>${table(r.weekly_snapshots || [])}</div>
  </div>
  ${futureControls([
    { endpoint: "reward amount edits", purpose: "Edit reward amounts only after exact source keys are verified", status: "Unverified source" },
    { endpoint: "daily reward settings", purpose: "Daily settings are not exposed here until command-source mapping is exact", status: "Future" },
  ])}`;
}

function renderShopTab() {
  const d = state.data || {};
  return `<div class="grid">
    <div class="card"><h2>Purchase History</h2>${table(d.purchases || [])}</div>
    <div class="card"><h2>Premium Balances</h2>${table(d.premium_balances || [])}</div>
    <div class="card"><h2>Premium Transactions</h2>${table(d.premium_transactions || [])}</div>
    <div class="card"><h2>Shop View Sessions</h2>${table(d.sessions || [])}</div>
  </div>
  ${futureControls([
    { endpoint: "POST /api/shop/items", purpose: "Shop item writes", status: "Unverified schema" },
    { endpoint: "PUT /api/shop/items/:id", purpose: "Shop item edits/disable", status: "Unverified schema" },
  ])}`;
}

function renderQuestsTab() {
  const d = state.data || {};
  const o = d.overview || {};
  return `
    <div class="card page-hero-card">
      <div>
        <h2>Quests & Missions Summary</h2>
        <p class="muted">Full quest catalog, player progress, rewards and logs live in the dedicated owner page.</p>
      </div>
      <button class="btn primary sm" data-admin-page="Quests & Missions">Open Quests & Missions</button>
    </div>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr));margin-bottom:14px">
      ${metricCard("Active Quests", o.active_quests ?? 0, "catalog rows", "", "📜")}
      ${metricCard("Players Progressing", o.players_with_progress ?? 0, "progress rows", "accent-cyan", "👤")}
      ${metricCard("Pending Rewards", o.pending_rewards ?? 0, "pending_coin_rewards", "", "🎁")}
      ${metricCard("Completed Today", o.completed_today ?? 0, "completed progress", "", "✅")}
    </div>
    <div class="grid">
      <div class="card"><h2>Daily Quests</h2>${table(d.daily_quests || [], questColumns())}</div>
      <div class="card"><h2>Recent Progress</h2>${table((d.progress?.rows || []).slice(0, 20), questProgressColumns())}</div>
    </div>
  `;
}

function renderPlayerGrantsTab() {
  const p = state.playerResult;
  return `
    <div class="card">
      <h2>Player Grants</h2>
      <form id="rewardPlayerSearchForm" class="toolbar" style="flex-wrap:wrap;margin-bottom:12px">
        <input name="query" placeholder="Search username or user ID" required style="flex:1;min-width:200px" />
        <button class="btn primary">Search Player</button>
      </form>
      ${p ? renderPlayerCard(p) : `<div class="notice">Search a player before granting or removing rewards.</div>`}
    </div>
    ${p ? `<div class="grid">
      <div class="card"><h2>Grant Coins</h2><form id="grantCoinsForm" class="settings-form"><input type="hidden" name="action" value="add_balance" /><div class="field"><label class="field-label">Amount</label><input name="amount" type="number" required /></div><div class="field"><label class="field-label">Reason</label><input name="reason" required /></div><button class="btn primary">Grant Coins</button></form></div>
      <div class="card"><h2>VIP</h2><form id="grantVipForm" class="settings-form"><input name="reason" required placeholder="Reason" /><button class="btn primary">Grant VIP</button><button class="btn danger" type="button" id="removeVipGrantBtn">Remove VIP</button></form></div>
      <div class="card"><h2>Grant Item</h2><form id="grantItemForm" class="settings-form"><div class="field"><label class="field-label">Item ID</label><input name="item_id" required /></div><div class="field"><label class="field-label">Item Type</label><input name="item_type" required /></div><div class="field"><label class="field-label">Reason</label><input name="reason" required /></div><button class="btn primary">Grant Item</button></form></div>
      <div class="card"><h2>Grant Title</h2><form id="grantTitleForm" class="settings-form"><input name="title_id" required placeholder="title_id" /><input name="reason" required placeholder="Reason" /><button class="btn primary">Grant Title</button></form></div>
      <div class="card"><h2>Grant Badge</h2><form id="grantBadgeForm" class="settings-form"><input name="badge_id" required placeholder="badge_id" /><input name="reason" required placeholder="Reason" /><button class="btn primary">Grant Badge</button></form></div>
    </div>` : ""}
  `;
}

function renderRewardLogsTab() {
  const d = state.data || {};
  return `<div class="grid">
    <div class="card"><h2>Reward Audit Logs</h2>${table(d.audit_logs || [])}</div>
    <div class="card"><h2>Purchase History</h2>${table(d.purchase_history || [])}</div>
    <div class="card"><h2>Premium Transactions</h2>${table(d.premium_transactions || [])}</div>
  </div>`;
}

function renderRewardsAdvancedTab() {
  const d = state.data || {};
  const statusRows = Object.entries(d.table_status || {}).map(([table_name, present]) => ({ table_name, status: present ? "present" : "missing", columns: (d.columns?.[table_name] || []).join(", ") }));
  return `<div class="grid">
    <div class="card"><h2>Reward Table Status</h2>${table(statusRows)}</div>
    <div class="card"><h2>Owned Items Raw</h2>${table(d.owned_items || [])}</div>
    <div class="card"><h2>Subscriber Users</h2>${table((d.rewards?.subscriber_users || d.subscriber_users || []))}</div>
  </div>`;
}

/* ── Staff Page (shared for owner Staff page) ────────── */
function renderStaffPage_shared() {
  const d = state.data || {};
  const canManageStaffAccounts = state.user?.role === "owner";
  return `
    <div class="grid">
      <div class="card">
        <h2>➕ Create Staff Account</h2>
        ${canManageStaffAccounts ? `<form id="staffCreateForm">
          <div class="field"><label class="field-label">Username</label><input name="username" required /></div>
          <div class="field"><label class="field-label">Password</label><input name="password" type="password" required /></div>
          <div class="field" style="margin-bottom:14px">
            <label class="field-label">Role</label>
            <select name="role">
              <option value="viewer">Viewer</option>
              <option value="moderator">Moderator</option>
              <option value="staff" selected>Staff</option>
              <option value="admin">Admin</option>
              ${state.user?.role === "owner" ? `<option value="owner">Owner</option>` : ""}
            </select>
          </div>
          <div style="margin-bottom:14px">
            <div class="field-label" style="margin-bottom:8px">Permissions</div>
            ${permissionChecks({})}
          </div>
          <button class="btn primary">Create Account</button>
        </form>` : `<div class="notice">Owner-only staff account control.</div>`}
      </div>
      <div class="card">
        <h2>🤖 Bot Role Management</h2>
        <p class="muted text-sm" style="margin-bottom:12px">Manages roles in the bot's own tables (owner_users, admin_users, managers, moderators, dj_users).</p>
        ${canManageStaffAccounts ? `<form id="botRoleForm">
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
        </form>` : `<div class="notice">Owner-only bot role control.</div>`}
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
        ${canManageStaffAccounts ? `<button class="btn danger sm" data-remove-staff="${r.id}">Remove</button>` : ""}
      `)}
    </div>
    <div class="grid">
      ${canManageStaffAccounts ? (d.dashboard_users || []).map((u) => `<details class="advanced-collapse">
        <summary class="advanced-summary">
          <span class="pill ${u.disabled ? "warn" : "ok"}">${u.disabled ? "DISABLED" : "ACTIVE"}</span>
          ${esc(u.username)} permissions
          <span class="muted text-sm">${esc(u.role)}</span>
        </summary>
        <div class="advanced-content">
          <form class="staffEditForm" data-staff-id="${u.id}">
            <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr));margin-bottom:12px">
              <div class="field">
                <label class="field-label">Role</label>
                <select name="role">
                  ${["viewer","moderator","staff","admin"].concat(state.user?.role === "owner" ? ["owner"] : []).map((role) => `<option value="${role}" ${u.role === role ? "selected" : ""}>${role}</option>`).join("")}
                </select>
              </div>
              <label class="switch" style="align-self:end"><input type="checkbox" name="disabled" ${u.disabled ? "checked" : ""}/><span>Disabled</span></label>
            </div>
            ${permissionChecks(u.permissions || {})}
            <button class="btn primary sm" style="margin-top:12px">Save Staff Permissions</button>
          </form>
        </div>
      </details>`).join("") : ""}
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
    ${tab === "Public Settings" ? renderPublicSettings() : ""}
    ${tab === "Settings Audit" ? renderSettingsAudit() : ""}
    ${tab === "Permissions Audit" ? renderPermissionsAudit() : ""}
    ${tab === "QA Audit" ? renderQaAudit() : ""}
    ${tab === "E2E Audit" ? renderE2eAudit() : ""}
    ${tab === "Maintenance Center" ? renderMaintenanceCenter() : ""}
    ${tab === "Database" ? renderSystemDatabase() : ""}
    ${tab === "Emergency" ? renderEmergency() : ""}
    ${tab === "Missing/Future Controls" ? renderSystemFutureControls() : ""}
  `;
}

function renderPublicSettings() {
  const d = state.data || {};
  const settings = d.settings || {};
  const sources = d.sources || {};
  const enabled = settings.public_rankings_hide_staff_bots !== false;
  return `
    <div class="grid">
      <div class="card">
        <div class="card-header">
          <h2>Public Rankings</h2>
          ${pill(enabled ? "enabled" : "disabled")}
        </div>
        <form id="publicSettingsForm" style="display:grid;gap:14px">
          <label class="switch">
            <input type="checkbox" name="public_rankings_hide_staff_bots" ${enabled ? "checked" : ""} />
            <span>Hide staff, owners, and bots from public leaderboards</span>
          </label>
          <div class="notice">Public rankings apply this server-side before data reaches the public portal. Owner diagnostics can still use unfiltered leaderboard data.</div>
          <button class="btn primary">Save Public Settings</button>
        </form>
      </div>
      <div class="card">
        <h2>Verified Source</h2>
        ${table(Object.entries(sources).map(([key, source]) => ({ key, table: source.table, default: source.default ? "true" : "false" })), [
          { key: "key", label: "Setting" },
          { key: "table", label: "Table" },
          { key: "default", label: "Default" },
        ])}
      </div>
    </div>
  `;
}

function renderQaAudit() {
  const d = state.data || {};
  const issues = d.issues || [];
  const countBySeverity = (severity) => issues.filter((issue) => issue.severity === severity).length;
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr));margin-bottom:14px">
      ${metricCard("Broken Routes", d.broken_routes_count ?? countBySeverity("CRITICAL"), "missing renderers/endpoints", countBySeverity("CRITICAL") ? "accent-red" : "accent-green", "QA")}
      ${metricCard("Missing Endpoints", (d.missing_endpoints || []).length, "frontend API references", (d.missing_endpoints || []).length ? "accent-red" : "accent-green", "API")}
      ${metricCard("Buttons Without Handlers", (d.buttons_without_handlers || []).length, "clickable controls", (d.buttons_without_handlers || []).length ? "accent-red" : "accent-green", "BTN")}
      ${metricCard("Public Safety", (d.public_safety_warnings || []).length, "public portal warnings", (d.public_safety_warnings || []).length ? "accent-red" : "accent-green", "PUB")}
    </div>
    <div class="card">
      <div class="card-header">
        <div>
          <h2>Dashboard QA Audit</h2>
          <div class="muted text-sm">Last checked: ${esc(d.last_checked_at || "—")}</div>
        </div>
        <span class="pill ${issues.length ? "warn" : "ok"}">${issues.length} issue${issues.length === 1 ? "" : "s"}</span>
      </div>
      ${issues.length ? table(issues, [
        { key: "severity", label: "Severity", render: (r) => `<span class="pill ${r.severity === "CRITICAL" ? "bad" : r.severity === "WARNING" ? "warn" : "info"}">${esc(r.severity)}</span>` },
        { key: "area", label: "Area" },
        { key: "item", label: "Item", render: (r) => `<code>${esc(r.item)}</code>` },
        { key: "message", label: "Issue" },
      ]) : `<div class="notice success">No broken routes, missing endpoints, handler gaps, or public safety warnings detected by the static QA audit.</div>`}
    </div>
    <div class="grid">
      <div class="card"><h2>Public Routes</h2>${table(d.public_routes || [])}</div>
      <div class="card"><h2>Admin Routes</h2>${table(d.admin_routes || [])}</div>
      <div class="card"><h2>Staff Routes</h2>${table(d.staff_routes || [])}</div>
    </div>
    <div class="card">
      <h2>Frontend Pages</h2>
      ${table(d.frontend_pages || [], [
        { key: "type", label: "Type" },
        { key: "page", label: "Page" },
        { key: "api", label: "API", render: (r) => r.api ? `<code>${esc(r.api)}</code>` : `<span class="muted">local render</span>` },
      ])}
    </div>
    <div class="card">
      <h2>API Endpoints</h2>
      ${table(d.api_endpoints || [], [
        { key: "method", label: "Method" },
        { key: "path", label: "Path", render: (r) => `<code>${esc(r.path)}</code>` },
        { key: "auth", label: "Auth", render: (r) => auditStatusChip(r.auth, r.auth !== "unprotected") },
      ])}
    </div>
  `;
}

function renderE2eAudit() {
  const d = state.data || {};
  const critical = d.critical_issues || [];
  const warnings = d.warnings || [];
  const passed = d.passed_checks || [];
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr));margin-bottom:14px">
      ${metricCard("Critical Issues", critical.length, "must fix before deploy", critical.length ? "accent-red" : "accent-green", "!")}
      ${metricCard("Warnings", warnings.length, "review before launch", warnings.length ? "accent-red" : "accent-green", "WARN")}
      ${metricCard("Passed Checks", passed.filter((row) => row.status === "PASS").length, "static E2E checks", "accent-green", "OK")}
      ${metricCard("Queue Failed", d.command_queue_health?.counts?.failed ?? 0, "bot_command_queue", d.command_queue_health?.counts?.failed ? "accent-red" : "accent-green", "Q")}
    </div>
    <div class="card">
      <div class="card-header">
        <div>
          <h2>Final Dashboard E2E Audit</h2>
          <div class="muted text-sm">Generated: ${esc(d.generated_at || "—")}</div>
        </div>
        <span class="pill ${critical.length ? "bad" : warnings.length ? "warn" : "ok"}">${critical.length ? "CRITICAL" : warnings.length ? "WARNING" : "PASS"}</span>
      </div>
      ${critical.length ? table(critical, [
        { key: "severity", label: "Severity", render: (r) => `<span class="pill bad">${esc(r.severity)}</span>` },
        { key: "area", label: "Area" },
        { key: "item", label: "Item" },
        { key: "message", label: "Issue" },
      ]) : `<div class="notice success">No critical E2E issues detected by the static audit.</div>`}
    </div>
    <div class="grid">
      <div class="card">
        <h2>Warnings</h2>
        ${warnings.length ? table(warnings, [
          { key: "severity", label: "Severity", render: (r) => `<span class="pill warn">${esc(r.severity)}</span>` },
          { key: "area", label: "Area" },
          { key: "item", label: "Item" },
          { key: "message", label: "Warning" },
        ]) : `<div class="notice success">No warnings detected.</div>`}
      </div>
      <div class="card">
        <h2>Passed Checks</h2>
        ${table(passed, [
          { key: "check", label: "Check" },
          { key: "status", label: "Status", render: (r) => auditStatusChip(r.status, r.status === "PASS") },
        ])}
      </div>
    </div>
    <div class="grid">
      <div class="card"><h2>Public Pages</h2>${table(d.public_pages || [])}</div>
      <div class="card"><h2>Owner Pages</h2>${table(d.owner_pages || [])}</div>
      <div class="card"><h2>Staff Pages</h2>${table(d.staff_pages || [])}</div>
    </div>
    <div class="card">
      <h2>Bot Command Queue Smoke Coverage</h2>
      ${table(d.command_queue_health?.smoke_tests || [], [
        { key: "action", label: "Visible Action" },
        { key: "endpoint", label: "Endpoint", render: (r) => `<code>${esc(`${r.method} ${r.endpoint}`)}</code>` },
        { key: "endpoint_status", label: "Endpoint", render: (r) => auditStatusChip(r.endpoint_status, r.endpoint_status === "PASS") },
        { key: "consumer_action", label: "Consumer Action" },
        { key: "consumer_status", label: "Consumer", render: (r) => auditStatusChip(r.consumer_status, r.consumer_status === "PASS") },
      ])}
    </div>
    <div class="grid">
      <div class="card"><h2>Broken Buttons</h2>${table(d.broken_buttons || [])}</div>
      <div class="card"><h2>Missing Endpoints</h2>${table(d.missing_endpoints || [])}</div>
      <div class="card"><h2>Permission Warnings</h2>${table(d.permission_issues || [])}</div>
      <div class="card"><h2>Public Safety</h2>${table(d.public_safety_issues || [])}</div>
    </div>
    <div class="card">
      <h2>Recommendations</h2>
      ${table(d.recommendations || [])}
    </div>
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
        <input id="logTarget" placeholder="Target" value="${esc(state.logs.target)}" style="flex:1;min-width:100px" />
        <input id="logStatus" placeholder="Status" value="${esc(state.logs.status)}" style="flex:1;min-width:90px" />
        <input id="logDate" type="date" value="${esc(state.logs.date)}" style="flex:1;min-width:135px" />
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
  const cls = s === "CONNECTED" ? "ok" : s === "LEGACY" || s === "READ ONLY" ? "def" : s === "UNKNOWN" || s === "UNVERIFIED" || s.startsWith("MISSING") ? "warn" : "bad";
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

function renderPermissionsAudit() {
  const d = state.data || {};
  const warnings = d.warnings || [];
  const routes = d.routes || [];
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr));margin-bottom:14px">
      ${metricCard("Protected Routes", d.protected_count ?? 0, "public/auth/permission/owner", "accent-green", "🔐")}
      ${metricCard("Warnings", d.warning_count ?? warnings.length, "routes needing review", warnings.length ? "accent-red" : "accent-green", "!")}
      ${metricCard("Total Routes", d.route_count ?? routes.length, "registered Express routes", "accent-cyan", "🧭")}
    </div>
    <div class="card">
      <h2>Permission Warnings</h2>
      ${warnings.length ? table(warnings, [
        { key: "method", label: "Method" },
        { key: "path", label: "Route" },
        { key: "warning", label: "Warning", render: (r) => `<span class="pill bad">${esc(r.warning)}</span>` },
      ]) : `<div class="notice">No route permission warnings detected.</div>`}
    </div>
    <div class="card">
      <h2>Route Permission Matrix</h2>
      ${table(routes, [
        { key: "method", label: "Method" },
        { key: "path", label: "Route" },
        { key: "auth", label: "Auth", render: (r) => auditStatusChip(r.auth, r.auth !== "unprotected") },
        { key: "required_permission", label: "Permission", render: (r) => `<code>${esc(r.required_permission || (r.public ? "PUBLIC" : "AUTH"))}</code>` },
      ])}
    </div>
  `;
}

function renderMaintenanceCenter() {
  const tab = state.maintenanceTab || "Overview";
  return `
    <div class="tabs sub-tabs" style="margin-bottom:14px">
      ${MAINTENANCE_TABS.map((t) => `<button class="${tab === t ? "active" : ""}" data-maint-tab="${esc(t)}">${esc(t)}</button>`).join("")}
    </div>
    ${tab === "Overview" ? renderMaintenanceOverview() : ""}
    ${tab === "Backups" ? renderMaintenanceBackups() : ""}
    ${tab === "Restore" ? renderMaintenanceRestore() : ""}
    ${tab === "Database Health" ? renderMaintenanceDbHealth() : ""}
    ${tab === "Cleanup Preview" ? renderMaintenanceCleanup() : ""}
    ${tab === "Runtime Health" ? renderMaintenanceRuntime() : ""}
    ${tab === "Logs" ? renderMaintenanceLogs() : ""}
    ${tab === "Advanced" ? renderMaintenanceAdvanced() : ""}
  `;
}

function bytes(n) {
  const value = Number(n || 0);
  if (!Number.isFinite(value)) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
  return `${size.toFixed(unit ? 1 : 0)} ${units[unit]}`;
}

function maintenanceStatusPill(value) {
  const s = String(value ?? "unknown");
  const cls = /ok|online|connected|open/i.test(s) ? "ok" : /warn|missing|failed|error|unavailable|not_found/i.test(s) ? "warn" : "def";
  return `<span class="pill ${cls}">${esc(s)}</span>`;
}

function renderMaintenanceOverview() {
  const d = state.data || {};
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr));margin-bottom:14px">
      ${metricCard("DB Size", bytes(d.db_file?.size), d.db_path || "SQLite", "accent-cyan", "DB")}
      ${metricCard("Last Backup", d.last_backup?.modified_at || "None", d.last_backup?.name || "No DB backup found", d.last_backup ? "accent-green" : "accent-red", "BK")}
      ${metricCard("SQLite", d.sqlite_integrity_status || "unknown", "integrity_check", d.sqlite_integrity_status === "ok" ? "accent-green" : "accent-red", "✓")}
      ${metricCard("Warnings", d.warning_count ?? 0, "maintenance warnings", d.warning_count ? "accent-red" : "accent-green", "!")}
    </div>
    <div class="grid">
      <div class="card">
        <h2>System Snapshot</h2>
        <div style="display:grid;gap:8px">
          <div class="inline-actions"><span>Current DB Path</span><code>${esc(d.db_path || "—")}</code></div>
          <div class="inline-actions"><span>WAL Size</span><span>${esc(bytes(d.wal_size))}</span></div>
          <div class="inline-actions"><span>PM2 Dashboard</span>${maintenanceStatusPill(d.pm2_dashboard_status)}</div>
          <div class="inline-actions"><span>Last Dashboard Restart</span><span class="muted">${esc(d.last_dashboard_restart || "—")}</span></div>
          <div class="inline-actions"><span>Last Bot Restart</span><span class="muted">${esc(d.last_bot_restart || "—")}</span></div>
        </div>
      </div>
      <div class="card">
        <h2>Bot PM2 Status</h2>
        ${table(d.pm2_bot_status || [], [
          { key: "name", label: "Process" },
          { key: "status", label: "Status", render: (r) => maintenanceStatusPill(r.status) },
          { key: "restart_time", label: "Restarts" },
        ])}
      </div>
    </div>
    <div class="card">
      <h2>Warnings</h2>
      ${(d.warnings || []).length ? table(d.warnings) : `<div class="notice">No maintenance warnings detected.</div>`}
    </div>
  `;
}

function renderMaintenanceBackups() {
  const d = state.data || {};
  return `
    <div class="grid">
      <div class="card">
        <h2>Create Backups</h2>
        <div class="toolbar" style="flex-wrap:wrap">
          <button class="btn primary" data-maint-action="backup-db">Create DB Backup</button>
          <button class="btn" data-maint-action="backup-dashboard">Backup Dashboard Files</button>
          <button class="btn danger" data-maint-action="backup-env">Backup Env Server-Side</button>
        </div>
        <div class="notice warn" style="margin-top:12px">Env backup never returns token values. It requires typed confirmation: <code>BACKUP ENV</code>.</div>
      </div>
      <div class="card">
        <h2>Environment Metadata</h2>
        <div class="inline-actions"><span>Path</span><code>${esc(d.env_metadata?.env_path || "—")}</code></div>
        <div class="inline-actions"><span>Exists</span>${pill(d.env_metadata?.file_exists ? "yes" : "no")}</div>
        <div class="inline-actions"><span>Modified</span><span class="muted">${esc(d.env_metadata?.modified_at || "—")}</span></div>
        ${table(Object.entries(d.env_metadata?.token_keys || {}).map(([key, status]) => ({ key, status })), [
          { key: "key", label: "Secret Key" },
          { key: "status", label: "Status", render: (r) => maintenanceStatusPill(r.status) },
        ])}
      </div>
    </div>
    <div class="card">
      <h2>Backup Files</h2>
      ${table(d.backups || [], [
        { key: "name", label: "Name" },
        { key: "type", label: "Type" },
        { key: "size", label: "Size", render: (r) => bytes(r.size) },
        { key: "modified_at", label: "Modified" },
        { key: "path", label: "Path", render: (r) => `<code>${esc(r.path)}</code>` },
      ])}
    </div>
  `;
}

function renderMaintenanceRestore() {
  const d = state.data || {};
  const dbBackups = (d.backups || []).filter((b) => b.type === "sqlite_db");
  return `
    <div class="notice warn">Database restore is owner-only, requires <code>RESTORE DATABASE</code>, creates a pre-restore backup, and requires bot/dashboard restart after completion.</div>
    <div class="card danger-zone">
      <h2>Restore Preview</h2>
      <form id="restorePreviewForm" class="toolbar" style="flex-wrap:wrap;margin-bottom:14px">
        <select name="backup" required style="flex:2;min-width:260px">
          <option value="">Select DB backup</option>
          ${dbBackups.map((b) => `<option value="${esc(b.path)}">${esc(b.name)} (${bytes(b.size)})</option>`).join("")}
        </select>
        <button class="btn">Preview</button>
      </form>
      <div id="restorePreviewResult"></div>
      <form id="restoreDbForm" class="toolbar" style="flex-wrap:wrap">
        <input name="backup" placeholder="Approved backup path" required style="flex:2;min-width:260px" />
        <input name="confirmation" placeholder="Type RESTORE DATABASE" required style="flex:1;min-width:220px" />
        <button class="btn danger">Restore DB</button>
      </form>
    </div>
    <div class="card">
      <h2>Dashboard File Restore</h2>
      <div class="notice">Dashboard file restore is not automated here. Use the timestamped backups under the approved folder and deploy through git or a controlled shell session.</div>
    </div>
  `;
}

function renderMaintenanceDbHealth() {
  const d = state.data || {};
  return `
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr));margin-bottom:14px">
      ${metricCard("Integrity", d.integrity_check || "unknown", "PRAGMA integrity_check", d.integrity_check === "ok" ? "accent-green" : "accent-red", "✓")}
      ${metricCard("Quick Check", d.quick_check || "unknown", "PRAGMA quick_check", d.quick_check === "ok" ? "accent-green" : "accent-red", "QC")}
      ${metricCard("Tables", d.table_count ?? 0, "SQLite tables", "accent-cyan", "T")}
      ${metricCard("Pending Commands", d.pending_bot_command_queue ?? 0, "bot_command_queue", "", "Q")}
    </div>
    <div class="grid">
      <div class="card">
        <h2>Database Files</h2>
        ${table([d.db_file || {}].concat(d.sidecars || []), [
          { key: "path", label: "Path", render: (r) => `<code>${esc(r.path || "—")}</code>` },
          { key: "exists", label: "Exists", render: (r) => pill(r.exists ? "yes" : "no") },
          { key: "size", label: "Size", render: (r) => bytes(r.size) },
          { key: "modified_at", label: "Modified" },
        ])}
      </div>
      <div class="card">
        <h2>Health Counters</h2>
        ${table([
          { metric: "Stale bot_instances", value: d.stale_bot_instances },
          { metric: "Failed bot commands", value: d.failed_bot_command_queue },
          { metric: "Failed radio jobs", value: d.failed_yt_request_jobs },
          { metric: "Orphaned inventory rows", value: d.orphaned_inventory_rows ?? "not checked" },
        ])}
      </div>
    </div>
    <div class="card"><h2>Largest Tables</h2>${table(d.largest_tables || [])}</div>
  `;
}

function renderMaintenanceCleanup() {
  const d = state.data || {};
  return `
    <div class="notice warn">Cleanup preview does not delete rows. Actual DB row cleanup is disabled in this build unless a future safe cleanup type is explicitly added.</div>
    <div class="card">
      <div class="card-header"><h2>Cleanup Candidates</h2><span class="pill info">Retention ${esc(d.retention_days || 30)} days</span></div>
      ${table(d.candidates || [], [
        { key: "cleanup_type", label: "Type" },
        { key: "description", label: "Description" },
        { key: "count", label: "Count" },
        { key: "dry_run_only", label: "Mode", render: (r) => pill(r.dry_run_only ? "dry-run only" : "can clean") },
      ], (r) => `<button class="btn sm" data-maint-cleanup="${esc(r.cleanup_type)}">Dry Run</button>`)}
    </div>
  `;
}

function renderMaintenanceRuntime() {
  const d = state.data || {};
  return `
    <div class="grid">
      <div class="card">
        <h2>PM2 Processes</h2>
        ${d.pm2?.available ? table(d.pm2.processes || [], [
          { key: "name", label: "Name" },
          { key: "status", label: "Status", render: (r) => maintenanceStatusPill(r.status) },
          { key: "restart_time", label: "Restarts" },
          { key: "memory", label: "Memory", render: (r) => bytes(r.memory) },
          { key: "cpu", label: "CPU" },
        ]) : `<div class="notice warn">${esc(d.pm2?.error || "PM2 unavailable")}</div>`}
      </div>
      <div class="card">
        <h2>Command Queue Counts</h2>
        ${table(d.command_queue_counts || [])}
      </div>
    </div>
    <div class="card"><h2>Bot Heartbeats</h2>${table(d.bot_heartbeats || [])}</div>
    <div class="card"><h2>Command Queue Failures</h2>${table(d.command_queue_failures || [])}</div>
  `;
}

function renderMaintenanceLogs() {
  const d = state.data || {};
  return `
    <div class="card"><h2>Audit Logs</h2>${table(d.audit_logs || [])}</div>
    <div class="card"><h2>Admin Action Logs</h2>${table(d.admin_action_logs || [])}</div>
    <div class="card"><h2>Command Queue Failures</h2>${table(d.command_queue_failures || [])}</div>
    <div class="card"><h2>Radio Failures</h2>${table(d.radio_failures || [])}</div>
  `;
}

function renderMaintenanceAdvanced() {
  const d = state.data || {};
  return `
    <div class="grid">
      <div class="card">
        <h2>Approved Paths</h2>
        <div class="inline-actions"><span>DB Path</span><code>${esc(d.db_path || "—")}</code></div>
        ${(d.approved_backup_folders || []).map((folder) => `<div class="inline-actions"><span>Backup Folder</span><code>${esc(folder)}</code></div>`).join("")}
      </div>
      <div class="card">
        <h2>Restore Warnings</h2>
        ${(d.restore_warnings || []).map((w) => `<div class="notice warn" style="margin-bottom:8px">${esc(w)}</div>`).join("")}
      </div>
    </div>
    <details class="advanced-collapse">
      <summary class="advanced-summary"><span class="pill warn">RAW</span> Raw Health JSON</summary>
      <div class="advanced-content"><pre style="white-space:pre-wrap;overflow:auto">${esc(JSON.stringify(d.raw_health || {}, null, 2))}</pre></div>
    </details>
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

function renderSystemFutureControls() {
  return `
    ${futureControls([
      { endpoint: "dashboard tab registry", purpose: "Add future tabs in one place via TAB_REGISTRY arrays", status: "Connected" },
      { endpoint: "unverified setting writes", purpose: "Keep hidden until Settings Audit maps exact DB sources", status: "Policy" },
      { endpoint: "destructive DB cleanup", purpose: "No delete endpoint exposed from dashboard", status: "Blocked" },
      { endpoint: "runtime bot API calls", purpose: "Use bot_command_queue instead of calling HighRise APIs directly", status: "Policy" },
    ], "Missing / Future Controls")}
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
    case "Moderation":  return renderSecurityPage(activeTab("Moderation"), { staff: true });
    case "Leaderboards": return renderLeaderboardsPage(activeTab("Leaderboards"), { staff: true });
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
  const active = d.active_event;
  return `
    <div class="grid">
      <div class="card">
        <div class="card-header"><h2>Active Event</h2>${active ? pill("active") : pill("none")}</div>
        ${active ? table([active]) : `<div class="notice">No active event.</div>`}
        ${can("manage_events") || can("emergency_controls") ? `
          <form id="eventStartForm" class="toolbar" style="margin-top:12px;flex-wrap:wrap">
            <input name="event_id" placeholder="event id or number" required />
            <input name="minutes" type="number" min="1" max="480" value="30" style="max-width:120px" />
            <button class="btn primary">Queue Start</button>
            <button class="btn danger" type="button" data-action="event-stop">Queue Stop</button>
          </form>
          ${queueHelp()}
        ` : `<div class="notice">Requires event management permission.</div>`}
      </div>
      <div class="card">
        <h2>📅 Scheduled Events</h2>
        ${renderEventRows(scheduled)}
      </div>
    </div>
    ${renderEventTableCard("Event Definitions", tables.event_definitions)}
    ${renderEventTableCard("Event History", tables.event_history)}
    ${renderEventTableCard("Event Points", tables.event_points)}
  `;
}

function renderStaffRoomTools() {
  const d = state.data || {};
  const known = d.known_settings || {};
  const canEditRoomSettings = can("emergency_controls");
  const canAnnounce = can("manage_room") || can("manage_events") || can("emergency_controls");

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
        ${canEditRoomSettings ? `
          ${boolRow("welcome_enabled", "Welcome messages")}
          ${boolRow("public_emotes_enabled", "Public emotes")}
          ${boolRow("social_enabled", "Social features")}
          ${boolRow("announcements_enabled", "Announcements")}
        ` : `<div class="notice">Requires <code>emergency_controls</code> permission — contact the owner to enable this access.</div>`}
      </div>
      <div class="card">
        <h2>👋 Welcome Message</h2>
        ${canEditRoomSettings ? `
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
      ${canAnnounce ? `
        <form id="announcementForm" style="display:grid;gap:10px">
          <textarea name="message" rows="4" required maxlength="500" placeholder="Announcement message"></textarea>
          <button class="btn primary">Queue Announcement</button>
        </form>
        ${queueHelp()}
      ` : `<div class="notice">Requires room or event management permission to queue announcements.</div>`}
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
        <input id="logTarget" placeholder="Target" value="${esc(state.logs.target)}" style="flex:1;min-width:100px" />
        <input id="logStatus" placeholder="Status" value="${esc(state.logs.status)}" style="flex:1;min-width:90px" />
        <input id="logDate" type="date" value="${esc(state.logs.date)}" style="flex:1;min-width:135px" />
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
  const all = state.data?.permissions || [
    "view_dashboard","manage_radio","manage_casino","manage_games","manage_mining","manage_fishing",
    "manage_room","manage_events","manage_automation","manage_emotes","manage_players","manage_economy","manage_inventory",
    "manage_rewards","manage_moderation","manage_staff","manage_bots","manage_bot_config","view_logs","emergency_controls","db_admin",
  ];
  const groups = {
    Radio: ["manage_radio"],
    Players: ["manage_players", "manage_inventory", "manage_moderation"],
    Economy: ["manage_economy", "manage_rewards"],
    Games: ["manage_casino", "manage_games", "manage_mining", "manage_fishing"],
    Room: ["manage_room", "manage_events", "manage_automation", "manage_emotes"],
    Bots: ["manage_bots", "manage_bot_config"],
    Logs: ["view_logs"],
    Emergency: ["emergency_controls"],
    System: ["view_dashboard", "manage_staff", "db_admin"],
  };
  return `<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px">
    ${Object.entries(groups).map(([group, keys]) => `<div>
      <div class="field-label" style="margin-bottom:6px">${esc(group)}</div>
      <div style="display:grid;gap:6px">${keys.filter((p) => all.includes(p)).map((p) => `<label class="switch">
        <input type="checkbox" name="perm_${p}" ${perms[p] ? "checked" : ""} />
        <span>${p.replace(/_/g, " ")}</span>
      </label>`).join("")}</div>
    </div>`).join("")}
  </div>`;
}

/* ═══════════════════════════════════════════════════════
   EVENT BINDING
══════════════════════════════════════════════════════ */
function bindAdminPageEvents() {
  document.getElementById("vipGrantForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    confirmAction("Grant VIP", `Grant VIP to ${data.query}?`, async () => {
      await action("VIP granted.", () => api("/api/vip/add", { method: "POST", body: JSON.stringify(data) }));
      await loadAdmin();
    });
  });
  document.querySelectorAll("[data-vip-remove]").forEach((btn) => btn.addEventListener("click", () => {
    const user_id = btn.dataset.vipRemove;
    const username = btn.dataset.vipUser || user_id;
    const reason = prompt(`Reason for removing VIP from ${username}?`);
    if (!reason) return;
    confirmAction("Remove VIP", `Remove VIP from ${username}?`, async () => {
      await action("VIP removed.", () => api("/api/vip/remove", { method: "POST", body: JSON.stringify({ user_id, reason }) }));
      await loadAdmin();
    });
  }));
  document.getElementById("titleCatalogForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    confirmAction("Add Title", `Add title ${data.title_id}?`, async () => {
      await action("Title catalog updated.", () => api("/api/titles/catalog", { method: "POST", body: JSON.stringify(data) }));
      await loadAdmin();
    });
  });
  document.getElementById("rewardPlayerSearchForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const query = new FormData(e.currentTarget).get("query");
    const data = await api(`/api/player/search?q=${encodeURIComponent(query)}`);
    state.playerResult = data.player || null;
    render();
  });
  document.getElementById("grantCoinsForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const p = state.playerResult;
    const data = Object.fromEntries(new FormData(e.currentTarget));
    confirmAction("Grant Coins", `Grant ${data.amount} coins to @${p?.username}?`, async () => {
      await applyPlayerWrite("Coins granted.", () => api(`/api/player/${encodeURIComponent(p.user_id)}/economy`, { method: "POST", body: JSON.stringify(data) }));
    });
  });
  document.getElementById("grantVipForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const p = state.playerResult;
    const data = Object.fromEntries(new FormData(e.currentTarget));
    confirmAction("Grant VIP", `Grant VIP to @${p?.username}?`, async () => {
      await applyPlayerWrite("VIP granted.", () => api("/api/vip/add", { method: "POST", body: JSON.stringify({ user_id: p.user_id, reason: data.reason }) }));
    });
  });
  document.getElementById("removeVipGrantBtn")?.addEventListener("click", () => {
    const p = state.playerResult;
    const reason = document.querySelector("#grantVipForm input[name='reason']")?.value?.trim();
    if (!reason) return action("Reason required.", () => Promise.reject(new Error("reason_required")));
    confirmAction("Remove VIP", `Remove VIP from @${p?.username}?`, async () => {
      await applyPlayerWrite("VIP removed.", () => api("/api/vip/remove", { method: "POST", body: JSON.stringify({ user_id: p.user_id, reason }) }));
    });
  });
  document.getElementById("grantItemForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const p = state.playerResult;
    const data = Object.fromEntries(new FormData(e.currentTarget));
    confirmAction("Grant Item", `Grant ${data.item_id} to @${p?.username}?`, async () => {
      await applyPlayerWrite("Item granted.", () => api(`/api/player/${encodeURIComponent(p.user_id)}/items`, { method: "POST", body: JSON.stringify(data) }));
    });
  });
  document.getElementById("grantTitleForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const p = state.playerResult;
    const data = Object.fromEntries(new FormData(e.currentTarget));
    confirmAction("Grant Title", `Grant ${data.title_id} to @${p?.username}?`, async () => {
      await applyPlayerWrite("Title granted.", () => api(`/api/player/${encodeURIComponent(p.user_id)}/titles`, { method: "POST", body: JSON.stringify(data) }));
    });
  });
  document.getElementById("grantBadgeForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const p = state.playerResult;
    const data = Object.fromEntries(new FormData(e.currentTarget));
    confirmAction("Grant Badge", `Grant ${data.badge_id} to @${p?.username}?`, async () => {
      await applyPlayerWrite("Badge granted.", () => api(`/api/player/${encodeURIComponent(p.user_id)}/badges`, { method: "POST", body: JSON.stringify(data) }));
    });
  });

  document.getElementById("publicSettingsForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    await action("Public settings saved.", () => api("/api/public-settings", {
      method: "PUT",
      body: JSON.stringify({ public_rankings_hide_staff_bots: fd.get("public_rankings_hide_staff_bots") === "on" }),
    }));
    await loadAdmin();
  });

  ["settingsAuditStatus", "settingsAuditModule", "settingsAuditPage"].forEach((id) => {
    document.getElementById(id)?.addEventListener("change", (e) => {
      const key = id.replace("settingsAudit", "").toLowerCase();
      state.settingsAudit[key] = e.target.value;
      render();
    });
  });

  document.querySelector('[data-maint-action="backup-db"]')?.addEventListener("click", () => {
    confirmAction("Create DB Backup", "Create a consistent SQLite backup in the approved server backup folder?", async () => {
      await action("Database backup created.", () => api("/api/maintenance/backup/db", { method: "POST", body: JSON.stringify({}) }));
      await loadMaintenanceTab("Backups");
    });
  });
  document.querySelector('[data-maint-action="backup-dashboard"]')?.addEventListener("click", async () => {
    await action("Dashboard files backed up.", () => api("/api/maintenance/backup/dashboard", { method: "POST", body: JSON.stringify({}) }));
    await loadMaintenanceTab("Backups");
  });
  document.querySelector('[data-maint-action="backup-env"]')?.addEventListener("click", () => {
    const confirmation = prompt('Type "BACKUP ENV" to create a server-side .env backup. Token values will not be returned.');
    if (confirmation !== "BACKUP ENV") return;
    confirmAction("Backup Env", "Create a server-side 0600 .env backup without exposing contents?", async () => {
      await action("Environment backup created server-side.", () => api("/api/maintenance/backup/env-metadata", { method: "POST", body: JSON.stringify({ confirmation }) }));
      await loadMaintenanceTab("Backups");
    });
  });
  document.getElementById("restorePreviewForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    try {
      const preview = await api(`/api/maintenance/restore-preview?backup=${encodeURIComponent(data.backup)}`);
      const target = document.getElementById("restorePreviewResult");
      if (target) target.innerHTML = `<div class="notice warn">Backup verified: <code>${esc(preview.backup?.path || "")}</code><br/>Required confirmation: <code>${esc(preview.required_confirmation)}</code></div>`;
      const restoreForm = document.getElementById("restoreDbForm");
      if (restoreForm) restoreForm.querySelector("[name='backup']").value = data.backup;
    } catch (err) {
      state.error = err.message; render();
    }
  });
  document.getElementById("restoreDbForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    confirmAction("Restore Database", "This overwrites the live SQLite DB file. A pre-restore backup will be created first.", async () => {
      await action("Database restored. Restart bots and dashboard.", () => api("/api/maintenance/restore-db", { method: "POST", body: JSON.stringify(data) }));
      await loadMaintenanceTab("Restore");
    });
  });
  document.querySelectorAll("[data-maint-cleanup]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await action("Cleanup dry-run recorded.", () => api("/api/maintenance/cleanup", {
        method: "POST",
        body: JSON.stringify({ cleanup_type: btn.dataset.maintCleanup, confirmation: "CLEANUP", dry_run: true }),
      }));
      await loadMaintenanceTab("Cleanup Preview");
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
      await action("Queue clear queued for DJ_DUDU.", () =>
        api("/api/radio/clear", { method: "POST", body: JSON.stringify({}) }));
    });
  });
  document.getElementById("qaAnnouncement")?.addEventListener("click", () => {
    const message = prompt("Announcement message");
    if (!message?.trim()) return;
    action("Announcement queued.", () =>
      api("/api/room/announce", { method: "POST", body: JSON.stringify({ message: message.trim() }) }));
  });
  document.getElementById("qaCreateBackup")?.addEventListener("click", () => {
    confirmAction("Create DB Backup", "Create a timestamped SQLite backup now?", async () => {
      await action("Database backup created.", () =>
        api("/api/maintenance/backup/db", { method: "POST", body: JSON.stringify({}) }));
    });
  });

  /* Radio controls */
  document.querySelectorAll('[data-action="radio-refresh"]').forEach((btn) => btn.addEventListener("click", () => loadAdmin()));
  document.querySelectorAll('[data-action="radio-skip"]').forEach((btn) => btn.addEventListener("click", () => {
    confirmAction("Skip Song", "Request a skip of the current song.", async () => {
      await action("Skip queued for DJ_DUDU.", () => api("/api/radio/skip", { method: "POST", body: JSON.stringify({}) }));
    });
  }));
  document.querySelectorAll('[data-action="radio-clear"]').forEach((btn) => btn.addEventListener("click", () => {
    confirmAction("Clear Queue", "Queue a DJ_DUDU cleanup-aware clear of all pending song requests?", async () => {
      await action("Queue clear queued for DJ_DUDU.", () => api("/api/radio/clear", { method: "POST", body: JSON.stringify({}) }));
    });
  }));
  document.querySelectorAll('[data-action="radio-cleanup"]').forEach((btn) => btn.addEventListener("click", () => {
    confirmAction("Cleanup Radio", "Queue DJ_DUDU to reconcile the Requests playlist and cleanup stale request files?", async () => {
      const confirmation = prompt('Type "CLEANUP RADIO" to queue radio maintenance cleanup.');
      if (confirmation !== "CLEANUP RADIO") return;
      await action("Radio cleanup queued for DJ_DUDU.", () => api("/api/radio/maintenance/cleanup", { method: "POST", body: JSON.stringify({ confirmation }) }));
    });
  }));
  document.querySelectorAll('[data-action="radio-reload"]').forEach((btn) => btn.addEventListener("click", () => {
    confirmAction("Reload Radio", "Queue DJ_DUDU to reload safe radio runtime caches?", async () => {
      await action("Radio reload queued for DJ_DUDU.", () => api("/api/bot-command", { method: "POST", body: JSON.stringify({ target_bot: "dj", action: "radio_reload", payload: {} }) }));
    });
  }));
  document.getElementById("requestsEnabled")?.addEventListener("change", async (e) => {
    await action(e.target.checked ? "Requests enabled." : "Requests disabled.", () =>
      api("/api/radio/requests-enabled", { method: "PUT", body: JSON.stringify({ enabled: e.target.checked }) }));
  });
  document.querySelectorAll('[data-action="queue-review"]').forEach((btn) => btn.addEventListener("click", () => {
    const id = btn.dataset.commandId;
    confirmAction("Mark Failed Command Reviewed", `Mark failed command #${id} as reviewed without deleting it?`, async () => {
      await action("Failed command marked reviewed.", () => api(`/api/bot-command-queue/${encodeURIComponent(id)}/review`, { method: "POST", body: JSON.stringify({}) }));
    });
  }));
  document.getElementById("radioSettingsForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const data = Object.fromEntries(new FormData(form));
    await action("Radio settings saved.", () =>
      api("/api/radio/settings", {
        method: "PUT",
        body: JSON.stringify({
          max_active_queue: data.max_active_queue,
          per_user_queue_limit: data.per_user_queue_limit,
          request_cooldown: data.request_cooldown,
          request_price: data.request_price,
          voteskip_threshold: data.voteskip_threshold,
          skip_on_leave: form.elements.skip_on_leave?.checked,
          refund_on_leave: form.elements.refund_on_leave?.checked,
          admin_ignore_leave: form.elements.admin_ignore_leave?.checked,
        }),
      }));
  });
  document.getElementById("radioBlockRequesterForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = new FormData(e.currentTarget).get("username");
    await action("Requester blocked.", () => api("/api/radio/blocklist/requester", { method: "POST", body: JSON.stringify({ username }) }));
  });
  document.getElementById("radioBlockTrackForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const pattern = new FormData(e.currentTarget).get("pattern");
    await action("Track blocked.", () => api("/api/radio/blocklist/track", { method: "POST", body: JSON.stringify({ pattern }) }));
  });
  document.querySelectorAll("[data-unblock-requester]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const username = btn.dataset.unblockRequester;
      confirmAction("Remove Block", `Allow @${username} to request songs again?`, async () => {
        await action("Requester removed from blocklist.", () => api(`/api/radio/blocklist/requester/${encodeURIComponent(username)}`, { method: "DELETE" }));
      });
    });
  });
  document.querySelectorAll("[data-unblock-track]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.unblockTrack;
      confirmAction("Remove Track Block", `Remove blocked track pattern #${id}?`, async () => {
        await action("Track removed from blocklist.", () => api(`/api/radio/blocklist/track/${encodeURIComponent(id)}`, { method: "DELETE" }));
      });
    });
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

  document.getElementById("miningSettingsForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const body = {};
    Array.from(form.elements).forEach((el) => {
      if (!el.name) return;
      body[el.name] = el.type === "checkbox" ? el.checked : el.value;
    });
    await action("Mining settings saved.", () =>
      api("/api/mining-settings", { method: "PUT", body: JSON.stringify(body) }));
  });

  document.getElementById("fishingSettingsForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const body = {};
    Array.from(form.elements).forEach((el) => {
      if (!el.name) return;
      body[el.name] = el.type === "checkbox" ? el.checked : el.value;
    });
    await action("Fishing settings saved.", () =>
      api("/api/fishing-settings", { method: "PUT", body: JSON.stringify(body) }));
  });

  document.getElementById("questCatalogCreateForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const body = {};
    Array.from(form.elements).forEach((el) => {
      if (!el.name) return;
      body[el.name] = el.type === "checkbox" ? el.checked : el.value;
    });
    await action("Quest saved.", async () => {
      await api("/api/quests/catalog", { method: "POST", body: JSON.stringify(body) });
      state.data = await api("/api/quests");
    });
  });

  document.getElementById("questCatalogUpdateForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const body = {};
    Array.from(form.elements).forEach((el) => {
      if (!el.name) return;
      body[el.name] = el.type === "checkbox" ? el.checked : el.value;
    });
    const questId = String(body.quest_id || "").trim();
    if (!questId) return;
    await action("Quest updated.", async () => {
      await api(`/api/quests/catalog/${encodeURIComponent(questId)}`, { method: "PUT", body: JSON.stringify(body) });
      state.data = await api("/api/quests");
    });
  });

  document.querySelectorAll("[data-quest-disable]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const questId = btn.dataset.questDisable;
      const reason = prompt(`Reason for disabling quest ${questId}?`);
      if (!reason) return;
      confirmAction("Disable Quest", `Soft-disable/archive quest ${questId}?`, async () => {
        await action("Quest disabled.", async () => {
          await api(`/api/quests/catalog/${encodeURIComponent(questId)}`, { method: "DELETE", body: JSON.stringify({ reason }) });
          state.data = await api("/api/quests");
        });
      });
    });
  });

  document.getElementById("questPlayerSearchForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const query = e.currentTarget.querySelector("[name='query']")?.value.trim() || "";
    state.questSearch = query;
    try {
      state.data = await api(`/api/quests${query ? `?q=${encodeURIComponent(query)}` : ""}`);
      state.notice = query ? `Loaded quest progress for ${query}.` : "Showing all quest progress.";
      state.error = "";
      render();
    } catch (err) {
      state.error = err.message;
      render();
    }
  });
  document.getElementById("questSearchClear")?.addEventListener("click", async () => {
    state.questSearch = "";
    state.data = await api("/api/quests").catch(() => state.data);
    render();
  });

  document.getElementById("automationAnnouncementForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const body = {};
    Array.from(form.elements).forEach((el) => {
      if (!el.name) return;
      body[el.name] = el.type === "checkbox" ? el.checked : el.value;
    });
    await action("Scheduled announcement saved.", async () => {
      await api("/api/automation/announcements", { method: "POST", body: JSON.stringify(body) });
      state.data = await api("/api/automation");
    });
  });
  document.getElementById("automationAnnouncementEditForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const body = {};
    Array.from(form.elements).forEach((el) => {
      if (!el.name || el.name === "id") return;
      if (el.type === "checkbox") body[el.name] = el.checked;
      else if (String(el.value || "").trim() !== "") body[el.name] = el.value;
    });
    const id = new FormData(form).get("id");
    await action("Scheduled announcement updated.", async () => {
      await api(`/api/automation/announcements/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) });
      state.data = await api("/api/automation");
    });
  });
  document.querySelectorAll("[data-automation-send]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.automationSend;
      confirmAction("Queue Announcement", `Send scheduled announcement #${id} now?`, async () => {
        await action("Announcement queued.", () => api(`/api/automation/announcements/${encodeURIComponent(id)}/send-now`, { method: "POST", body: JSON.stringify({}) }));
      });
    });
  });
  document.querySelectorAll("[data-automation-archive]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.automationArchive;
      confirmAction("Archive Announcement", `Archive scheduled announcement #${id}?`, async () => {
        await action("Announcement archived.", async () => {
          await api(`/api/automation/announcements/${encodeURIComponent(id)}`, { method: "DELETE", body: JSON.stringify({}) });
          state.data = await api("/api/automation");
        });
      });
    });
  });
  document.getElementById("automationRotatingForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.currentTarget));
    await action("Rotating announcement saved.", async () => {
      await api("/api/automation/rotating", { method: "POST", body: JSON.stringify(body) });
      state.data = await api("/api/automation");
    });
  });
  document.querySelectorAll("[data-automation-rotating-disable]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.automationRotatingDisable;
      confirmAction("Disable Rotating Message", `Disable rotating announcement #${id}?`, async () => {
        await action("Rotating announcement disabled.", async () => {
          await api(`/api/automation/rotating/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify({ enabled: false }) });
          state.data = await api("/api/automation");
        });
      });
    });
  });
  document.querySelectorAll("[data-automation-rotating-send]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const row = (state.data?.rows || state.data?.rotating_announcements || []).find((r) => String(r.id || "") === String(btn.dataset.automationRotatingSend || ""));
      const message = row?.message || row?.text || row?.body || row?.content || "";
      if (!message) return;
      confirmAction("Queue Rotating Message", "Send this rotating announcement now?", async () => {
        await action("Rotating announcement queued.", () => api("/api/automation/send", { method: "POST", body: JSON.stringify({ target_bot: "host", source: "rotating_announcement", message }) }));
      });
    });
  });
  document.querySelectorAll("[data-automation-promo]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const message = btn.dataset.automationPromo;
      const source = btn.dataset.automationSource || "promo_message";
      confirmAction("Queue Promo", "Send this promo message now?", async () => {
        await action("Promo queued.", () => api("/api/automation/send", { method: "POST", body: JSON.stringify({ target_bot: "host", source, message }) }));
      });
    });
  });
  document.querySelectorAll(".automationSendForm").forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = Object.fromEntries(new FormData(form));
      await action("Automation message queued.", () => api("/api/automation/send", { method: "POST", body: JSON.stringify(body) }));
    });
  });

  document.querySelectorAll("[data-table-search], [data-rarity-filter], [data-enabled-filter]").forEach((control) => {
    const applyFilter = () => {
      const card = control.closest(".card");
      if (!card) return;
      const q = String(card.querySelector("[data-table-search]")?.value || "").toLowerCase();
      const rarity = String(card.querySelector("[data-rarity-filter]")?.value || "").toLowerCase();
      const enabled = String(card.querySelector("[data-enabled-filter]")?.value || "").toLowerCase();
      card.querySelectorAll("tbody tr").forEach((row) => {
        const text = row.textContent.toLowerCase();
        const rarityOk = !rarity || text.includes(rarity);
        const enabledOk = !enabled || text.includes(enabled);
        row.style.display = (!q || text.includes(q)) && rarityOk && enabledOk ? "" : "none";
      });
    };
    control.addEventListener("input", applyFilter);
    control.addEventListener("change", applyFilter);
  });

  document.getElementById("miningOreAddForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const body = {};
    Array.from(form.elements).forEach((el) => {
      if (!el.name) return;
      body[el.name] = el.type === "checkbox" ? el.checked : el.value;
    });
    await action("Ore saved.", () => api("/api/mining/ores", { method: "POST", body: JSON.stringify(body) }));
  });

  document.querySelectorAll("[data-mining-ore-form]").forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const itemId = form.dataset.miningOreForm;
      const body = {};
      Array.from(form.elements).forEach((el) => {
        if (!el.name) return;
        body[el.name] = el.type === "checkbox" ? el.checked : el.value;
      });
      await action("Ore updated.", () => api(`/api/mining/ores/${encodeURIComponent(itemId)}`, { method: "PUT", body: JSON.stringify(body) }));
    });
  });

  document.querySelectorAll("[data-mining-ore-disable]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const itemId = btn.dataset.miningOreDisable;
      confirmAction("Disable Ore", `Soft-disable ${itemId}? Existing player inventories are preserved.`, async () => {
        await action("Ore disabled.", () => api(`/api/mining/ores/${encodeURIComponent(itemId)}`, { method: "DELETE", body: JSON.stringify({}) }));
      });
    });
  });

  document.getElementById("announcementForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = e.currentTarget.querySelector("[name='message']")?.value.trim() || "";
    if (!message) return;
    await action("Command queued. Bot must consume bot_command_queue.", () =>
      api("/api/room/announce", { method: "POST", body: JSON.stringify({ message }) }));
  });

  document.getElementById("rotatingAnnouncementForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = e.currentTarget.querySelector("[name='message']")?.value.trim() || "";
    if (!message) return;
    await action("Rotating announcement saved.", () =>
      api("/api/room/announcements", { method: "POST", body: JSON.stringify({ message }) }));
  });

  document.querySelectorAll("[data-disable-announcement]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.disableAnnouncement;
      confirmAction("Disable Announcement", `Soft-disable rotating announcement #${id}?`, async () => {
        await action("Rotating announcement disabled.", () =>
          api(`/api/room/announcements/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify({ enabled: false }) }));
      });
    });
  });

  document.getElementById("securityWarnForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    confirmAction("Issue Warning", `Warn ${data.query}?`, async () => {
      await action("Warning recorded or queued.", () => api("/api/security/warnings", { method: "POST", body: JSON.stringify(data) }));
      await loadAdmin();
    });
  });
  document.getElementById("securityMuteForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    confirmAction("Mute Player", `Mute ${data.query} for ${data.minutes || 60} minutes?`, async () => {
      await action("Mute recorded or queued.", () => api("/api/security/mutes", { method: "POST", body: JSON.stringify(data) }));
      await loadAdmin();
    });
  });
  document.querySelectorAll("[data-security-unmute]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.securityUnmute;
      confirmAction("Unmute Player", `Unmute ${target}?`, async () => {
        await action("Player unmuted.", () => api(`/api/security/mutes/${encodeURIComponent(target)}`, { method: "DELETE", body: JSON.stringify({}) }));
        await loadAdmin();
      });
    });
  });
  document.querySelectorAll("[data-report-review], [data-report-resolve]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.reportReview || btn.dataset.reportResolve;
      const status = btn.dataset.reportResolve ? "resolved" : "reviewing";
      const resolution = status === "resolved" ? prompt("Resolution note (optional):") || "" : "";
      confirmAction("Update Report", `Mark report #${id} as ${status}?`, async () => {
        await action("Report updated.", () => api(`/api/security/reports/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify({ status, resolution }) }));
        await loadAdmin();
      });
    });
  });
  document.getElementById("securityPlayerLookupForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const query = e.currentTarget.querySelector("[name='query']")?.value.trim() || "";
    if (!query) return;
    try {
      state.securityPlayer = await api(`/api/security/player/${encodeURIComponent(query)}`);
      state.notice = `Loaded moderation history for ${state.securityPlayer.player?.username || query}.`;
      state.error = "";
      render();
    } catch (err) {
      state.securityPlayer = null;
      state.error = err.message;
      render();
    }
  });
  document.getElementById("securityPlayerActionForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    confirmAction("Apply Moderation Action", `${data.action} ${data.query}?`, async () => {
      const url = data.action === "warn" ? "/api/security/warnings"
        : data.action === "mute" ? "/api/security/mutes"
        : `/api/security/mutes/${encodeURIComponent(data.query)}`;
      const method = data.action === "unmute" ? "DELETE" : "POST";
      await action("Moderation action applied.", () => api(url, { method, body: JSON.stringify(data) }));
      state.securityPlayer = await api(`/api/security/player/${encodeURIComponent(data.query)}`).catch(() => state.securityPlayer);
      render();
    });
  });
  document.getElementById("securityAlertForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = e.currentTarget.querySelector("[name='message']")?.value.trim() || "";
    if (!message) return;
    await action("Security alert queued. Bot must consume bot_command_queue.", () =>
      api("/api/bot-command", { method: "POST", body: JSON.stringify({ target_bot: "security", action: "security_alert", payload: { message } }) }));
  });
  document.querySelectorAll("[data-security-bot-action]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const actionName = btn.dataset.securityBotAction;
      confirmAction("Queue Security Bot Command", `Queue ${actionName} for KeanuShield?`, async () => {
        await action("Security bot command queued.", () =>
          api("/api/bot-command", { method: "POST", body: JSON.stringify({ target_bot: "security", action: actionName, payload: {} }) }));
      });
    });
  });

  document.getElementById("eventStartForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    await action("Event start queued. Bot must consume bot_command_queue.", () =>
      api("/api/events/start", { method: "POST", body: JSON.stringify(data) }));
  });

  document.querySelector('[data-action="event-stop"]')?.addEventListener("click", () => {
    confirmAction("Stop Event", "Queue a host command to stop the active event?", async () => {
      await action("Event stop queued. Bot must consume bot_command_queue.", () =>
        api("/api/events/stop", { method: "POST", body: JSON.stringify({ target: "all" }) }));
    });
  });

  document.getElementById("eventScheduleForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    await action("Event schedule queued. Bot must consume bot_command_queue.", () =>
      api("/api/events/schedule", { method: "POST", body: JSON.stringify(data) }));
  });

  document.getElementById("emoteCommandForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    await action("Command queued. Bot must consume bot_command_queue.", () =>
      api("/api/emotes/trigger", {
        method: "POST",
        body: JSON.stringify({
          target_bot: String(data.target_bot || "").trim(),
          emote: String(data.emote || "").trim(),
          duration: data.duration || undefined,
        }),
      }));
  });

  document.getElementById("botEmoteSetForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    await action("Persistent bot emote queued.", () =>
      api("/api/bot-command", {
        method: "POST",
        body: JSON.stringify({
          target_bot: String(data.target_bot || "").trim(),
          action: "botemote_set",
          payload: { emote: String(data.emote || "").trim() },
        }),
      }));
  });

  document.getElementById("botEmoteStopForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    await action("Persistent bot emote stop queued.", () =>
      api("/api/bot-command", {
        method: "POST",
        body: JSON.stringify({
          target_bot: String(data.target_bot || "").trim(),
          action: "botemote_stop",
          payload: {},
        }),
      }));
  });

  document.querySelectorAll("[data-dancefloor-command]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const command = btn.dataset.dancefloorCommand;
      const run = () => action(`Dancefloor ${command} queued.`, () =>
        api("/api/dancefloor/command", { method: "POST", body: JSON.stringify({ command, payload: {} }) }));
      if (["stop", "clear"].includes(command)) {
        confirmAction("Queue Dancefloor Command", `Queue dancefloor ${command}?`, run);
      } else {
        run();
      }
    });
  });

  document.getElementById("dancefloorSequenceForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    await action("Dancefloor sequence queued.", () =>
      api("/api/dancefloor/command", { method: "POST", body: JSON.stringify({ command: "sequence", payload: { emotes: data.emotes } }) }));
  });

  document.getElementById("dancefloorRandomForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    await action("Dancefloor random queued.", () =>
      api("/api/dancefloor/command", { method: "POST", body: JSON.stringify({ command: "random", payload: { count: data.count || undefined } }) }));
  });

  document.getElementById("dancefloorRandomTimedForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    await action("Dancefloor random timed queued.", () =>
      api("/api/dancefloor/command", {
        method: "POST",
        body: JSON.stringify({ command: "randomtimed", payload: { count: data.count || "all", min_seconds: data.min_seconds, max_seconds: data.max_seconds || undefined } }),
      }));
  });

  document.getElementById("syncStartForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    await action("Sync command queued.", () =>
      api("/api/sync/command", { method: "POST", body: JSON.stringify({ command: "start", payload: { leader: data.leader } }) }));
  });

  document.querySelectorAll("[data-sync-command]").forEach((btn) => {
    btn.addEventListener("click", () => {
      confirmAction("Queue Sync Command", "Queue stop all sync relations?", async () => {
        await action("Sync stop queued.", () =>
          api("/api/sync/command", { method: "POST", body: JSON.stringify({ command: "stop", payload: { scope: "all" } }) }));
      });
    });
  });

  document.querySelectorAll("[data-sync-persist]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const enabled = btn.dataset.syncPersist === "true";
      await action(`Sync persistence ${enabled ? "on" : "off"} queued.`, () =>
        api("/api/sync/command", { method: "POST", body: JSON.stringify({ command: "persist", payload: { enabled } }) }));
    });
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
  document.querySelectorAll("[data-player-jump]").forEach((btn) => {
    btn.addEventListener("click", () => switchTab("Players", btn.dataset.playerJump));
  });
  document.getElementById("playerSearchForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const query = e.currentTarget.querySelector("[name='query']").value.trim();
    const result = document.getElementById("playerSearchResult");
    if (!result) return;
    result.innerHTML = `<div class="notice">Searching…</div>`;
    try {
      const data = await api(`/api/player/search?q=${encodeURIComponent(query)}`);
      if (!data.player) {
        state.playerResult = null;
        state.notice = ""; state.error = `No player found for ${query}.`;
        render();
      } else {
        state.playerResult = data.player;
        state.notice = `Loaded ${data.player.username}.`; state.error = "";
        render();
      }
    } catch (err) {
      result.innerHTML = `<div class="notice error">${esc(err.message)}</div>`;
    }
  });

  async function applyPlayerWrite(label, fn) {
    try {
      const data = await fn();
      if (data?.player) state.playerResult = data.player;
      state.notice = label; state.error = "";
      render();
    } catch (err) {
      state.error = err.message; state.notice = ""; render();
    }
  }
  document.getElementById("playerEconomyForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const p = state.playerResult;
    const data = Object.fromEntries(new FormData(e.currentTarget));
    confirmAction("Apply Economy Change", `${data.action} ${data.amount} for @${p?.username}?`, async () => {
      await applyPlayerWrite("Player economy updated.", () => api(`/api/player/${encodeURIComponent(p.user_id)}/economy`, {
        method: "POST",
        body: JSON.stringify(data),
      }));
    });
  });
  document.getElementById("playerItemForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const p = state.playerResult;
    const data = Object.fromEntries(new FormData(e.currentTarget));
    confirmAction("Add Item", `Add ${data.item_id} to @${p?.username}?`, async () => {
      await applyPlayerWrite("Item added.", () => api(`/api/player/${encodeURIComponent(p.user_id)}/items`, {
        method: "POST",
        body: JSON.stringify(data),
      }));
    });
  });
  document.querySelectorAll("[data-quick-item]").forEach((btn) => btn.addEventListener("click", () => {
    const p = state.playerResult;
    const item_id = btn.dataset.quickItem;
    const item_type = btn.dataset.quickType || "item";
    confirmAction("Add Item", `Add ${item_id} to @${p?.username}?`, async () => {
      await applyPlayerWrite("Item added.", () => api(`/api/player/${encodeURIComponent(p.user_id)}/items`, {
        method: "POST",
        body: JSON.stringify({ item_id, item_type, reason: "dashboard quick action" }),
      }));
    });
  }));
  document.querySelectorAll("[data-remove-item]").forEach((btn) => btn.addEventListener("click", () => {
    const p = state.playerResult;
    const itemId = btn.dataset.removeItem;
    const reason = prompt(`Reason for removing ${itemId} from @${p?.username}?`);
    if (!reason) return;
    confirmAction("Remove Item", `Remove ${itemId} from @${p?.username}?`, async () => {
      await applyPlayerWrite("Item removed.", () => api(`/api/player/${encodeURIComponent(p.user_id)}/items/${encodeURIComponent(itemId)}`, { method: "DELETE", body: JSON.stringify({ reason }) }));
    });
  }));
  document.getElementById("playerTitleForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const p = state.playerResult;
    const data = Object.fromEntries(new FormData(e.currentTarget));
    confirmAction("Give Title", `Give title ${data.title_id} to @${p?.username}?`, async () => {
      await applyPlayerWrite("Title added.", () => api(`/api/player/${encodeURIComponent(p.user_id)}/titles`, { method: "POST", body: JSON.stringify(data) }));
    });
  });
  document.querySelectorAll("[data-remove-title]").forEach((btn) => btn.addEventListener("click", () => {
    const p = state.playerResult;
    const titleId = btn.dataset.removeTitle;
    const reason = prompt(`Reason for removing title ${titleId} from @${p?.username}?`);
    if (!reason) return;
    confirmAction("Remove Title", `Remove title ${titleId} from @${p?.username}?`, async () => {
      await applyPlayerWrite("Title removed.", () => api(`/api/player/${encodeURIComponent(p.user_id)}/titles/${encodeURIComponent(titleId)}`, { method: "DELETE", body: JSON.stringify({ reason }) }));
    });
  }));
  document.getElementById("playerBadgeForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const p = state.playerResult;
    const data = Object.fromEntries(new FormData(e.currentTarget));
    confirmAction("Give Badge", `Give badge ${data.badge_id} to @${p?.username}?`, async () => {
      await applyPlayerWrite("Badge added.", () => api(`/api/player/${encodeURIComponent(p.user_id)}/badges`, { method: "POST", body: JSON.stringify(data) }));
    });
  });
  document.querySelectorAll("[data-remove-badge]").forEach((btn) => btn.addEventListener("click", () => {
    const p = state.playerResult;
    const badgeId = btn.dataset.removeBadge;
    const reason = prompt(`Reason for removing badge ${badgeId} from @${p?.username}?`);
    if (!reason) return;
    confirmAction("Remove Badge", `Remove badge ${badgeId} from @${p?.username}?`, async () => {
      await applyPlayerWrite("Badge removed.", () => api(`/api/player/${encodeURIComponent(p.user_id)}/badges/${encodeURIComponent(badgeId)}`, { method: "DELETE", body: JSON.stringify({ reason }) }));
    });
  }));
  document.getElementById("playerModerationForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const p = state.playerResult;
    const data = Object.fromEntries(new FormData(e.currentTarget));
    confirmAction("Moderation Action", `${data.action} @${p?.username}?`, async () => {
      await applyPlayerWrite("Moderation action applied.", () => api(`/api/player/${encodeURIComponent(p.user_id)}/${encodeURIComponent(data.action)}`, {
        method: "POST",
        body: JSON.stringify(data),
      }));
    });
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
  document.querySelectorAll(".staffEditForm").forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const data = Object.fromEntries(new FormData(form));
      const perms = {};
      for (const [k, v] of Object.entries(data)) if (k.startsWith("perm_")) perms[k.slice(5)] = v === "on";
      await action("Staff permissions updated.", () =>
        api(`/api/staff/${form.dataset.staffId}`, {
          method: "PUT",
          body: JSON.stringify({ role: data.role, disabled: data.disabled === "on", permissions: perms }),
        }));
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
    state.logs.target = document.getElementById("logTarget")?.value || "";
    state.logs.status = document.getElementById("logStatus")?.value || "";
    state.logs.date = document.getElementById("logDate")?.value || "";
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
        state.adminPage = btn.dataset.adminPage;
        if (btn.dataset.adminTab) state.adminTab[state.adminPage] = btn.dataset.adminTab;
        state.notice = ""; state.error = ""; loadAdmin();
      });
    }
  });
}

/* ── Boot ────────────────────────────────────────────── */
init();
