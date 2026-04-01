import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "crm_cache.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id TEXT PRIMARY KEY,
    name TEXT,
    company TEXT,
    email TEXT,
    phone TEXT,
    owner_id TEXT,
    status TEXT,
    cadence_months INTEGER DEFAULT 6,
    last_synced TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS contact_tags (
    contact_id TEXT NOT NULL,
    tag TEXT,
    PRIMARY KEY (contact_id, tag),
    FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS next_actions (
    id TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL,
    text TEXT,
    due_date TEXT,
    assignee_id TEXT,
    status TEXT,
    FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL,
    text TEXT,
    date TEXT,
    author_id TEXT,
    FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS calls (
    id TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL,
    text TEXT,
    date TEXT,
    author_id TEXT,
    duration INTEGER,
    FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL,
    text TEXT,
    date TEXT,
    author_id TEXT,
    FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS emails (
    id TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL,
    subject TEXT,
    body_preview TEXT,
    date TEXT,
    direction TEXT,
    thread_id TEXT,
    from_address TEXT,
    to_addresses TEXT,
    FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,
    last_synced_at TEXT DEFAULT (datetime('now')),
    contacts_synced INTEGER DEFAULT 0,
    records_synced INTEGER DEFAULT 0,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_notes_contact_id ON notes(contact_id);
CREATE INDEX IF NOT EXISTS idx_calls_contact_id ON calls(contact_id);
CREATE INDEX IF NOT EXISTS idx_meetings_contact_id ON meetings(contact_id);
CREATE INDEX IF NOT EXISTS idx_emails_contact_id ON emails(contact_id);
CREATE INDEX IF NOT EXISTS idx_next_actions_contact_id ON next_actions(contact_id);
"""


def get_conn(db_path=None):
    """Return a connection. Pass ':memory:' for tests."""
    path = db_path if db_path is not None else str(DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn_or_path=None):
    """Create schema. Accepts a connection (for tests) or a path string."""
    if isinstance(conn_or_path, sqlite3.Connection):
        conn_or_path.executescript(SCHEMA)
        return
    conn = get_conn(conn_or_path)
    conn.executescript(SCHEMA)
    conn.close()


def upsert_contact(conn, c):
    conn.execute("""
        INSERT INTO contacts (id, name, company, email, phone, owner_id, status,
                              cadence_months, last_synced, raw_json)
        VALUES (:id, :name, :company, :email, :phone, :owner_id, :status,
                :cadence_months, datetime('now'), :raw_json)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name, company=excluded.company, email=excluded.email,
            phone=excluded.phone, owner_id=excluded.owner_id, status=excluded.status,
            cadence_months=excluded.cadence_months, last_synced=excluded.last_synced,
            raw_json=excluded.raw_json
    """, c)


def upsert_tags(conn, contact_id, tags):
    conn.execute("DELETE FROM contact_tags WHERE contact_id = ?", (contact_id,))
    for tag in tags:
        conn.execute(
            "INSERT OR IGNORE INTO contact_tags (contact_id, tag) VALUES (?, ?)",
            (contact_id, tag)
        )


def upsert_note(conn, n):
    conn.execute("""
        INSERT INTO notes (id, contact_id, text, date, author_id)
        VALUES (:id, :contact_id, :text, :date, :author_id)
        ON CONFLICT(id) DO UPDATE SET
            text=excluded.text, date=excluded.date
    """, n)


def upsert_call(conn, c):
    conn.execute("""
        INSERT INTO calls (id, contact_id, text, date, author_id, duration)
        VALUES (:id, :contact_id, :text, :date, :author_id, :duration)
        ON CONFLICT(id) DO UPDATE SET
            text=excluded.text, date=excluded.date
    """, c)


def upsert_meeting(conn, m):
    conn.execute("""
        INSERT INTO meetings (id, contact_id, text, date, author_id)
        VALUES (:id, :contact_id, :text, :date, :author_id)
        ON CONFLICT(id) DO UPDATE SET
            text=excluded.text, date=excluded.date
    """, m)


def upsert_action(conn, a):
    conn.execute("""
        INSERT INTO next_actions (id, contact_id, text, due_date, assignee_id, status)
        VALUES (:id, :contact_id, :text, :due_date, :assignee_id, :status)
        ON CONFLICT(id) DO UPDATE SET
            text=excluded.text, due_date=excluded.due_date,
            assignee_id=excluded.assignee_id, status=excluded.status
    """, a)


def upsert_email(conn, e):
    conn.execute("""
        INSERT INTO emails (id, contact_id, subject, body_preview, date,
                            direction, thread_id, from_address, to_addresses)
        VALUES (:id, :contact_id, :subject, :body_preview, :date,
                :direction, :thread_id, :from_address, :to_addresses)
        ON CONFLICT(id) DO UPDATE SET
            subject=excluded.subject, body_preview=excluded.body_preview,
            date=excluded.date, direction=excluded.direction
    """, e)


def log_sync(conn, source, contacts_synced=0, records_synced=0, error=None):
    conn.execute("""
        INSERT INTO sync_log (source, contacts_synced, records_synced, error)
        VALUES (?, ?, ?, ?)
    """, (source, contacts_synced, records_synced, error))
