import { useEffect, useState, useCallback, useRef } from "react";

const API_URL = "/api/dj/status";
const REFRESH_MS = 15_000;

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
  pos: number;
  title: string;
  username: string;
  dedication: string;
  priority: number;
}

interface RecentEntry {
  title: string;
  username: string;
  played_at: string;
}

interface Stats {
  total_queued: number;
  total_played_today: number;
  total_favorites: number;
  total_likes: number;
}

interface DjStatus {
  now_playing: NowPlaying | null;
  queue: QueueEntry[];
  recent: RecentEntry[];
  stats: Stats;
  radio_url: string | null;
  queue_open: boolean;
  updated_at: string;
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

function formatTime(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso.includes("T") ? iso : iso.replace(" ", "T") + "Z");
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function priorityLabel(p: number) {
  if (p >= 2) return { label: "VIP", cls: "badge-vip" };
  if (p >= 1) return { label: "Priority", cls: "badge-priority" };
  return null;
}

/* ── Equalizer animation ── */
function Equalizer({ active }: { active: boolean }) {
  return (
    <div className={`eq ${active ? "eq-active" : "eq-idle"}`} aria-hidden>
      <span className="eq-bar" style={{ animationDelay: "0ms" }} />
      <span className="eq-bar" style={{ animationDelay: "180ms" }} />
      <span className="eq-bar" style={{ animationDelay: "90ms" }} />
      <span className="eq-bar" style={{ animationDelay: "270ms" }} />
    </div>
  );
}

/* ── Countdown ring ── */
function CountdownRing({ refreshMs, lastFetch }: { refreshMs: number; lastFetch: Date | null }) {
  const [pct, setPct] = useState(100);
  useEffect(() => {
    if (!lastFetch) return;
    const tick = () => {
      const elapsed = Date.now() - lastFetch.getTime();
      setPct(Math.max(0, 100 - (elapsed / refreshMs) * 100));
    };
    tick();
    const id = setInterval(tick, 250);
    return () => clearInterval(id);
  }, [lastFetch, refreshMs]);
  const r = 8;
  const circ = 2 * Math.PI * r;
  return (
    <svg className="countdown-ring" width="22" height="22" viewBox="0 0 22 22">
      <circle cx="11" cy="11" r={r} fill="none" strokeWidth="2.5" className="countdown-track" />
      <circle
        cx="11" cy="11" r={r} fill="none" strokeWidth="2.5"
        className="countdown-fill"
        strokeDasharray={circ}
        strokeDashoffset={circ * (1 - pct / 100)}
        strokeLinecap="round"
        transform="rotate(-90 11 11)"
      />
    </svg>
  );
}

/* ── Now Playing hero ── */
function NowPlayingHero({ song, radioUrl }: { song: NowPlaying | null; radioUrl: string | null }) {
  const badge = song ? priorityLabel(song.priority) : null;
  return (
    <div className={`hero ${song ? "hero-active" : "hero-idle"}`}>
      <div className="hero-glow" />
      <div className="hero-inner">
        <div className="hero-left">
          <div className="hero-label">
            {song && <span className="live-dot" />}
            <span className="hero-label-text">{song ? "Now Playing" : "Nothing Playing"}</span>
          </div>
          <Equalizer active={!!song} />
        </div>
        <div className="hero-content">
          {song ? (
            <>
              <div className="hero-title">{song.title}</div>
              <div className="hero-meta">
                <span className="hero-user">
                  <span className="hero-user-at">@</span>{song.username}
                </span>
                {badge && <span className={`badge ${badge.cls}`}>{badge.label}</span>}
              </div>
              {song.dedication && (
                <div className="hero-dedication">💌 {song.dedication}</div>
              )}
              <div className="hero-actions">
                {song.youtube_url && (
                  <a href={song.youtube_url} target="_blank" rel="noopener noreferrer" className="btn btn-yt">
                    <span>▶</span> YouTube
                  </a>
                )}
                {radioUrl && (
                  <>
                    <a href={radioUrl} target="_blank" rel="noopener noreferrer" className="btn btn-radio">
                      📻 Radio
                    </a>
                    <a href={radioUrl} target="_blank" rel="noopener noreferrer" className="btn btn-webplayer">
                      🌐 Web Player
                    </a>
                  </>
                )}
              </div>
            </>
          ) : (
            <div className="hero-empty">
              <p>The DJ queue is ready — request a song with <code>!request</code> in the room.</p>
              {radioUrl && (
                <div className="hero-actions" style={{ marginTop: "0.75rem" }}>
                  <a href={radioUrl} target="_blank" rel="noopener noreferrer" className="btn btn-radio">
                    📻 Radio
                  </a>
                  <a href={radioUrl} target="_blank" rel="noopener noreferrer" className="btn btn-webplayer">
                    🌐 Web Player
                  </a>
                </div>
              )}
            </div>
          )}
        </div>
        <div className="hero-vinyl" aria-hidden>
          <div className={`vinyl ${song ? "vinyl-spin" : ""}`}>
            <div className="vinyl-label">🎵</div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Stats bar ── */
function StatsBar({ stats }: { stats: Stats }) {
  const items = [
    { icon: "📋", value: stats.total_queued, label: "In Queue", color: "var(--purple-h)" },
    { icon: "🎵", value: stats.total_played_today, label: "Played Today", color: "var(--cyan)" },
    { icon: "👍", value: stats.total_likes, label: "Total Likes", color: "var(--green)" },
    { icon: "⭐", value: stats.total_favorites, label: "Favorites", color: "var(--yellow)" },
  ];
  return (
    <div className="stats-bar">
      {items.map((item, i) => (
        <div key={i} className="stat">
          <span className="stat-icon">{item.icon}</span>
          <span className="stat-value" style={{ color: item.color }}>{item.value}</span>
          <span className="stat-label">{item.label}</span>
        </div>
      ))}
    </div>
  );
}

/* ── Up Next card ── */
function UpNextCard({ entry }: { entry: QueueEntry | null }) {
  if (!entry) return null;
  const badge = priorityLabel(entry.priority);
  return (
    <div className="card card-upnext">
      <div className="card-header">
        <span className="upnext-num">1</span>
        <span className="card-title">Up Next</span>
        {badge && <span className={`badge ${badge.cls}`}>{badge.label}</span>}
      </div>
      <div className="upnext-body">
        <div className="upnext-title">{truncate(entry.title, 70)}</div>
        <div className="upnext-user">@{entry.username}</div>
        {entry.dedication && (
          <div className="upnext-dedication">💌 {truncate(entry.dedication, 50)}</div>
        )}
      </div>
    </div>
  );
}

/* ── Queue list ── */
function QueueCard({ queue, queueOpen }: { queue: QueueEntry[]; queueOpen: boolean }) {
  const rest = queue.slice(1);
  return (
    <div className="card card-queue">
      <div className="card-header">
        <span className="icon">📋</span>
        <span className="card-title">Full Queue</span>
        <span className="stat-chip">{queue.length}</span>
        <span className={`queue-status ${queueOpen ? "open" : "locked"}`}>
          {queueOpen ? "Open" : "Locked"}
        </span>
      </div>
      {queue.length === 0 ? (
        <div className="empty-state">
          <span>🎵</span>
          <p>Queue is empty<br /><code>!request [song]</code> to add one</p>
        </div>
      ) : (
        <div className="queue-scroll-wrap">
          <ul className="queue-list">
            {queue.map((entry) => {
              const badge = priorityLabel(entry.priority);
              const isNext = entry.pos === 1;
              return (
                <li key={entry.id} className={`queue-item ${isNext ? "queue-item-next" : ""}`}>
                  <span className={`queue-pos ${isNext ? "queue-pos-next" : ""}`}>
                    {isNext ? "▶" : `#${entry.pos}`}
                  </span>
                  <div className="queue-info">
                    <span className="queue-title">{truncate(entry.title, 55)}</span>
                    <span className="queue-user">@{entry.username}</span>
                    {entry.dedication && (
                      <span className="queue-dedication">💌 {truncate(entry.dedication, 35)}</span>
                    )}
                  </div>
                  {badge && <span className={`badge ${badge.cls}`}>{badge.label}</span>}
                </li>
              );
            })}
          </ul>
          {rest.length === 0 && queue.length === 1 && (
            <p className="queue-hint">Only one song queued — be the next!</p>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Recent songs ── */
function RecentStrip({ recent }: { recent: RecentEntry[] }) {
  if (recent.length === 0) return null;
  return (
    <div className="card card-recent">
      <div className="card-header">
        <span className="icon">🕐</span>
        <span className="card-title">Recently Played</span>
      </div>
      <div className="recent-scroll">
        {recent.map((r, i) => (
          <div key={i} className="recent-pill">
            <div className="recent-pill-index">{i + 1}</div>
            <div className="recent-pill-body">
              <span className="recent-pill-title">{truncate(r.title, 45)}</span>
              <span className="recent-pill-meta">@{r.username} · {formatTime(r.played_at)}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Root app ── */
export default function App() {
  const [status, setStatus] = useState<DjStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastFetch, setLastFetch] = useState<Date | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const resp = await fetch(API_URL);
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        setError((body as { error?: string }).error ?? `HTTP ${resp.status}`);
        return;
      }
      const data: DjStatus = await resp.json();
      setStatus(data);
      setError(null);
      setLastFetch(new Date());
    } catch {
      setError("Could not reach the DJ status API.");
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    const id = setInterval(fetchStatus, REFRESH_MS);
    return () => clearInterval(id);
  }, [fetchStatus]);

  const upNext = status?.queue[0] ?? null;

  return (
    <div className="app">
      {/* ── Header ── */}
      <header className="header">
        <div className="header-inner">
          <div className="logo">
            <div className="logo-icon-wrap" aria-hidden>🎧</div>
            <div>
              <div className="logo-name">DJ <span className="logo-accent">DUDU</span></div>
              <div className="logo-sub">ChillTopia Live Room</div>
            </div>
          </div>
          <div className="header-right">
            {status?.radio_url && (
              <a href={status.radio_url} target="_blank" rel="noopener noreferrer" className="header-btn header-btn-radio">
                📻 Radio
              </a>
            )}
            <div className="refresh-chip">
              <CountdownRing refreshMs={REFRESH_MS} lastFetch={lastFetch} />
              <span className="refresh-chip-text">
                {lastFetch
                  ? lastFetch.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
                  : "Loading…"}
              </span>
            </div>
          </div>
        </div>
      </header>

      {/* ── Main ── */}
      <main className="main">
        {error && <div className="error-banner">⚠️ {error}</div>}

        {!status && !error && (
          <div className="loading">
            <div className="loading-spinner" />
            <p>Connecting to ChillTopia…</p>
          </div>
        )}

        {status && (
          <>
            {/* Hero */}
            <NowPlayingHero song={status.now_playing} radioUrl={status.radio_url} />

            {/* Stats */}
            <StatsBar stats={status.stats} />

            {/* Middle grid */}
            <div className="mid-grid">
              <div className="mid-left">
                <UpNextCard entry={upNext} />
              </div>
              <div className="mid-right">
                <QueueCard queue={status.queue} queueOpen={status.queue_open} />
              </div>
            </div>

            {/* Recent strip */}
            <RecentStrip recent={status.recent} />
          </>
        )}
      </main>

      {/* ── Footer ── */}
      <footer className="footer">
        <span>DJ DUDU · ChillTopia · Highrise</span>
        <span>Refreshes every {REFRESH_MS / 1000}s</span>
      </footer>
    </div>
  );
}
