-- ChillTopia Radio Rebuild Phase 1 cleanup notes.
--
-- Do not run this file automatically. Back up the production SQLite database
-- first, then archive or drop old radio tables manually during the dedicated
-- database cleanup phase.

-- Core old request lifecycle tables:
-- DROP TABLE IF EXISTS yt_request_jobs;
-- DROP TABLE IF EXISTS local_replay_jobs;

-- Optional old radio/session/history tables if present in production:
-- DROP TABLE IF EXISTS dj_requests;
-- DROP TABLE IF EXISTS dj_search_sessions;
-- DROP TABLE IF EXISTS dj_request_history;
-- DROP TABLE IF EXISTS radio_v2_requests;
-- DROP TABLE IF EXISTS radio_v3_requests;

-- Optional old ratings/favorites/listener stats tables. Keep these if Phase 2
-- will migrate player favorites, likes, or music-credit history forward:
-- DROP TABLE IF EXISTS dj_favorites;
-- DROP TABLE IF EXISTS dj_ratings;
-- DROP TABLE IF EXISTS radio_listener_stats;
-- DROP TABLE IF EXISTS radio_request_events;
