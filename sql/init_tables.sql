CREATE TABLE IF NOT EXISTS songs (
    id SERIAL PRIMARY KEY,
    song_name TEXT NOT NULL,
    difficulty TEXT NOT NULL,
    level_str TEXT,
    constant NUMERIC(3, 1),
    jp_constant NUMERIC(3, 1),
    cn_constant NUMERIC(3, 1),
    jacket_path TEXT,
    source_url TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (song_name, difficulty)
);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    account TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    qq_user_id TEXT UNIQUE,
    user_group TEXT NOT NULL DEFAULT 'normal_users',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CHECK (user_group IN ('normal_users', 'honored_users', 'admin'))
);

CREATE TABLE IF NOT EXISTS scores (
    id SERIAL PRIMARY KEY,
    user_id INTEGER,
    song_name TEXT NOT NULL,
    difficulty TEXT NOT NULL,
    score INTEGER NOT NULL,
    grade_label TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_cache (
    user_id INTEGER PRIMARY KEY,
    csv_hash TEXT,
    board_cache_key TEXT,
    board_image_path TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE scores ADD COLUMN IF NOT EXISTS user_id INTEGER;

UPDATE scores SET user_id = 1 WHERE user_id IS NULL;

ALTER TABLE scores ALTER COLUMN user_id SET NOT NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'scores_song_name_difficulty_key'
          AND conrelid = 'scores'::regclass
    ) THEN
        ALTER TABLE scores DROP CONSTRAINT scores_song_name_difficulty_key;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'scores_user_song_difficulty_key'
          AND conrelid = 'scores'::regclass
    ) THEN
        ALTER TABLE scores
        ADD CONSTRAINT scores_user_song_difficulty_key
        UNIQUE (user_id, song_name, difficulty);
    END IF;
END $$;

ALTER TABLE songs ADD COLUMN IF NOT EXISTS jp_constant NUMERIC(3, 1);
ALTER TABLE songs ADD COLUMN IF NOT EXISTS cn_constant NUMERIC(3, 1);

UPDATE songs
SET jp_constant = constant
WHERE jp_constant IS NULL AND constant IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_songs_level_difficulty
    ON songs (level_str, difficulty);

CREATE INDEX IF NOT EXISTS idx_songs_jp_constant
    ON songs (jp_constant);

CREATE INDEX IF NOT EXISTS idx_songs_cn_constant
    ON songs (cn_constant);

CREATE INDEX IF NOT EXISTS idx_scores_song_difficulty
    ON scores (user_id, song_name, difficulty);
