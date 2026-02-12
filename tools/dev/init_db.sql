CREATE TABLE IF NOT EXISTS reminders (
    id              SERIAL PRIMARY KEY,
    user_id         VARCHAR(64) NOT NULL,
    content         TEXT NOT NULL,
    due_at          TIMESTAMP,
    priority        VARCHAR(16) DEFAULT 'medium',
    case_id         VARCHAR(64),
    status          VARCHAR(16) DEFAULT 'pending',
    chat_id         VARCHAR(64),
    notified_at     TIMESTAMP,
    locked_by       VARCHAR(64),
    locked_at       TIMESTAMP,
    retry_count     INT DEFAULT 0,
    last_error      TEXT,
    source          VARCHAR(16) DEFAULT 'manual',
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reminders_user_status ON reminders(user_id, status);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_at) WHERE status = 'pending';
