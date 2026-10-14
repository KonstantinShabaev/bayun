-- Схема памяти Баюна (см. docs/PROJECT_SPEC.md, §4.2).
-- Векторный поиск (sqlite-vec) добавляется позже, сначала FTS5 (docs/IDEAS.md, §3).

CREATE TABLE IF NOT EXISTS people (
  id TEXT PRIMARY KEY,            -- 'masha'
  display_name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'guest' CHECK (role IN ('adult', 'child', 'guest')),
  voice_profile BLOB
);

CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('fact', 'preference', 'episode')),
  about TEXT NOT NULL,            -- people.id или 'home'
  text TEXT NOT NULL,             -- «Маша спит во второй спальне»
  source TEXT NOT NULL CHECK (source IN ('voice', 'camera', 'manual')),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at TEXT                 -- для эпизодов: «уехали до воскресенья»
);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
  text, content='facts', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
  INSERT INTO facts_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

CREATE TABLE IF NOT EXISTS audit_log (
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  speaker TEXT NOT NULL,
  tool TEXT NOT NULL,
  args TEXT,
  decision TEXT NOT NULL CHECK (decision IN ('allowed', 'confirmed', 'denied')),
  result TEXT
);
