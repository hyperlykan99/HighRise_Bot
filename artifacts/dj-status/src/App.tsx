import { useEffect, useState, useCallback } from "react";

const API_URL = "/api/dj/status";
const REFRESH_MS = 5000;

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

function priorityLabel(p: number): string {
  if (p >= 2) return "VIP";
  if (p >= 1) return "Priority";
  return "";
}

function formatTime(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

function NowPlayingCard({ song, radioUrl }: { song: NowPlaying | null; radioUrl: string | null }) {
  return (
    <div className="card glow-card">
      <div className="card-header">
        <span className="icon">🎧</span>
        <span className="card-title">Now Playing</span>
      </div>
      {song ? (
        <div className="now-playing-content">
          <div className="song-title">{song.title}</div>
          <div className="song-meta">
            <span className="requestor">@{song.username}</span>
            {song.priority >= 2 && <span className="badge badge-vip">VIP</span>}
            {song.priority === 1 && <span className="badge badge-priority">Priority</span>}
            {song.youtube_url && (
              <a
                href={song.youtube_url}
                target="_blank"
                rel="noopener noreferrer"
                className="yt-link"
              >
                ▶ YouTube
              </a>
            )}
          </div>
          {song.dedication && (
            <div className="dedication">
              💌 {song.dedication}
            </div>
          )}
          {radioUrl && (
            <a href={radioUrl} target="_blank" rel="noopener noreferrer" className="radio-btn">
              📻 Listen Live
            </a>
          )}
        </div>
      ) : (
        <div className="empty-state">
          <span>🎵</span>
          <p>No song playing right now</p>
          {radioUrl && (
            <a href={radioUrl} target="_blank" rel="noopener noreferrer" className="radio-btn">
              📻 Listen Live
            </a>
          )}
        </div>
      )}
    </div>
  );
}

function QueueCard({ queue, queueOpen }: { queue: QueueEntry[]; queueOpen: boolean }) {
  return (
    <div className="card">
      <div className="card-header">
        <span className="icon">📋</span>
        <span className="card-title">Queue</span>
        <span className={`queue-status ${queueOpen ? "open" : "locked"}`}>
          {queueOpen ? "Open" : "Locked"}
        </span>
      </div>
      {queue.length === 0 ? (
        <div className="empty-state">
          <span>🎵</span>
          <p>Queue is empty — use !request in the room</p>
        </div>
      ) : (
        <ul className="queue-list">
          {queue.map((entry) => {
            const pl = priorityLabel(entry.priority);
            return (
              <li key={entry.id} className="queue-item">
                <span className="queue-pos">#{entry.pos}</span>
                <div className="queue-info">
                  <span className="queue-title">{truncate(entry.title, 60)}</span>
                  <span className="queue-user">@{entry.username}</span>
                  {entry.dedication && (
                    <span className="queue-dedication">💌 {truncate(entry.dedication, 40)}</span>
                  )}
                </div>
                {pl && (
                  <span className={`badge badge-${pl === "VIP" ? "vip" : "priority"}`}>{pl}</span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function RecentCard({ recent }: { recent: RecentEntry[] }) {
  return (
    <div className="card">
      <div className="card-header">
        <span className="icon">🕐</span>
        <span className="card-title">Recently Played</span>
      </div>
      {recent.length === 0 ? (
        <div className="empty-state">
          <span>🎵</span>
          <p>Nothing played yet</p>
        </div>
      ) : (
        <ul className="recent-list">
          {recent.map((r, i) => (
            <li key={i} className="recent-item">
              <div className="recent-info">
                <span className="recent-title">{truncate(r.title, 55)}</span>
                <span className="recent-user">@{r.username}</span>
              </div>
              <span className="recent-time">{formatTime(r.played_at)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function StatsBar({ stats }: { stats: Stats }) {
  return (
    <div className="stats-bar">
      <div className="stat">
        <span className="stat-icon">📋</span>
        <span className="stat-value">{stats.total_queued}</span>
        <span className="stat-label">In Queue</span>
      </div>
      <div className="stat-divider" />
      <div className="stat">
        <span className="stat-icon">🎵</span>
        <span className="stat-value">{stats.total_played_today}</span>
        <span className="stat-label">Played Today</span>
      </div>
      <div className="stat-divider" />
      <div className="stat">
        <span className="stat-icon">👍</span>
        <span className="stat-value">{stats.total_likes}</span>
        <span className="stat-label">Total Likes</span>
      </div>
      <div className="stat-divider" />
      <div className="stat">
        <span className="stat-icon">⭐</span>
        <span className="stat-value">{stats.total_favorites}</span>
        <span className="stat-label">Favorites</span>
      </div>
    </div>
  );
}

export default function App() {
  const [status, setStatus] = useState<DjStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastFetch, setLastFetch] = useState<Date | null>(null);
  const [tick, setTick] = useState(0);

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
    const id = setInterval(() => {
      fetchStatus();
      setTick((t) => t + 1);
    }, REFRESH_MS);
    return () => clearInterval(id);
  }, [fetchStatus]);

  return (
    <div className="app">
      <header className="header">
        <div className="header-inner">
          <div className="logo">
            <span className="logo-icon">🎧</span>
            <div>
              <div className="logo-name">DJ DUDU</div>
              <div className="logo-sub">Live Room Status</div>
            </div>
          </div>
          <div className="header-right">
            {status?.radio_url && (
              <a href={status.radio_url} target="_blank" rel="noopener noreferrer" className="radio-pill">
                📻 Radio
              </a>
            )}
            <div className="refresh-info">
              {lastFetch ? (
                <span>Updated {lastFetch.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
              ) : (
                <span>Loading…</span>
              )}
            </div>
          </div>
        </div>
      </header>

      <main className="main">
        {error && (
          <div className="error-banner">
            ⚠️ {error}
          </div>
        )}

        {status && (
          <>
            <StatsBar stats={status.stats} />

            <div className="grid">
              <div className="grid-left">
                <NowPlayingCard song={status.now_playing} radioUrl={status.radio_url} />
                <RecentCard recent={status.recent} />
              </div>
              <div className="grid-right">
                <QueueCard queue={status.queue} queueOpen={status.queue_open} />
              </div>
            </div>
          </>
        )}

        {!status && !error && (
          <div className="loading">
            <div className="loading-spinner" />
            <p>Loading DJ status…</p>
          </div>
        )}
      </main>

      <footer className="footer">
        <span>DJ DUDU • Highrise Room Bot</span>
        <span>Auto-refreshes every {REFRESH_MS / 1000}s</span>
      </footer>
    </div>
  );
}
