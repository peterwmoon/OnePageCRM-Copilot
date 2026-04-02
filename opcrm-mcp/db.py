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

CREATE TABLE IF NOT EXISTS unmatched_emails (
    id TEXT PRIMARY KEY,
    subject TEXT,
    body_preview TEXT,
    date TEXT,
    direction TEXT,
    from_address TEXT,
    to_addresses TEXT,
    conversation_id TEXT
);

CREATE TABLE IF NOT EXISTS calendar_events (
    id TEXT PRIMARY KEY,
    subject TEXT,
    start_datetime TEXT,
    end_datetime TEXT,
    organizer_email TEXT,
    attendees TEXT,
    body_preview TEXT,
    contact_id TEXT
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
CREATE INDEX IF NOT EXISTS idx_unmatched_from ON unmatched_emails(from_address);
CREATE INDEX IF NOT EXISTS idx_unmatched_date ON unmatched_emails(date);
CREATE INDEX IF NOT EXISTS idx_calendar_start ON calendar_events(start_datetime);
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


# ── Query functions ────────────────────────────────────────────────────────────

def get_contact_by_id(conn, contact_id):
    row = conn.execute(
        "SELECT * FROM contacts WHERE id = ?", (contact_id,)
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["tags"] = [
        r["tag"] for r in conn.execute(
            "SELECT tag FROM contact_tags WHERE contact_id = ?", (contact_id,)
        ).fetchall()
    ]
    return result


def search_contacts(conn, query):
    pattern = f"%{query}%"
    rows = conn.execute("""
        SELECT * FROM contacts
        WHERE name LIKE ? OR company LIKE ? OR email LIKE ?
        ORDER BY name
    """, (pattern, pattern, pattern)).fetchall()
    return [dict(r) for r in rows]


def list_contacts_by_tag(conn, tag):
    rows = conn.execute("""
        SELECT c.* FROM contacts c
        JOIN contact_tags t ON t.contact_id = c.id
        WHERE t.tag = ?
        ORDER BY c.name
    """, (tag,)).fetchall()
    return [dict(r) for r in rows]


def list_contacts_by_owner(conn, owner_id):
    rows = conn.execute(
        "SELECT * FROM contacts WHERE owner_id = ? ORDER BY name", (owner_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def list_overdue_contacts(conn):
    """
    Returns contacts where last touch across all channels exceeds cadence window,
    or contacts with no interaction history at all.
    cadence_months is converted to days using 30.44 days/month.
    """
    rows = conn.execute("""
        WITH last_touch AS (
            SELECT contact_id, MAX(date) AS last_date FROM (
                SELECT contact_id, date FROM notes
                UNION ALL SELECT contact_id, date FROM calls
                UNION ALL SELECT contact_id, date FROM meetings
                UNION ALL SELECT contact_id, date FROM emails
            ) GROUP BY contact_id
        )
        SELECT c.id, c.name, c.company, c.owner_id, c.cadence_months,
               lt.last_date,
               CAST(julianday('now') - julianday(COALESCE(lt.last_date, '2000-01-01')) AS INTEGER)
                   AS days_since
        FROM contacts c
        LEFT JOIN last_touch lt ON lt.contact_id = c.id
        WHERE julianday('now') - julianday(COALESCE(lt.last_date, '2000-01-01'))
              > c.cadence_months * 30.44
        ORDER BY days_since DESC
    """).fetchall()
    return [dict(r) for r in rows]


def get_contact_history(conn, contact_id, limit=50):
    """Unified timeline of notes, calls, meetings, and emails for a contact."""
    rows = conn.execute("""
        SELECT 'note'    AS type, date, text    AS content, NULL AS subject,
               NULL      AS direction, author_id AS from_address
        FROM notes WHERE contact_id = ?
        UNION ALL
        SELECT 'call'    AS type, date, text    AS content, NULL AS subject,
               NULL      AS direction, author_id AS from_address
        FROM calls WHERE contact_id = ?
        UNION ALL
        SELECT 'meeting' AS type, date, text    AS content, NULL AS subject,
               NULL      AS direction, author_id AS from_address
        FROM meetings WHERE contact_id = ?
        UNION ALL
        SELECT 'email'   AS type, date, body_preview AS content, subject,
               direction, from_address
        FROM emails WHERE contact_id = ?
        ORDER BY date DESC
        LIMIT ?
    """, (contact_id, contact_id, contact_id, contact_id, limit)).fetchall()
    return [dict(r) for r in rows]


def get_recent_emails(conn, contact_id, limit=20):
    rows = conn.execute("""
        SELECT * FROM emails WHERE contact_id = ?
        ORDER BY date DESC LIMIT ?
    """, (contact_id, limit)).fetchall()
    return [dict(r) for r in rows]


def get_notes(conn, contact_id):
    rows = conn.execute(
        "SELECT * FROM notes WHERE contact_id = ? ORDER BY date DESC",
        (contact_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_sync_status(conn):
    rows = conn.execute("""
        SELECT * FROM sync_log s
        WHERE s.id = (
            SELECT id FROM sync_log
            WHERE source = s.source
            ORDER BY last_synced_at DESC, id DESC
            LIMIT 1
        )
    """).fetchall()
    return {row["source"]: dict(row) for row in rows}


def get_all_tags(conn):
    rows = conn.execute(
        "SELECT DISTINCT tag FROM contact_tags ORDER BY tag"
    ).fetchall()
    return [r[0] for r in rows]


def upsert_unmatched_email(conn, e):
    conn.execute("""
        INSERT INTO unmatched_emails
            (id, subject, body_preview, date, direction, from_address, to_addresses, conversation_id)
        VALUES
            (:id, :subject, :body_preview, :date, :direction, :from_address, :to_addresses, :conversation_id)
        ON CONFLICT(id) DO UPDATE SET
            subject=excluded.subject, date=excluded.date
    """, e)


def upsert_calendar_event(conn, e):
    conn.execute("""
        INSERT INTO calendar_events
            (id, subject, start_datetime, end_datetime, organizer_email, attendees, body_preview, contact_id)
        VALUES
            (:id, :subject, :start_datetime, :end_datetime, :organizer_email, :attendees, :body_preview, :contact_id)
        ON CONFLICT(id) DO UPDATE SET
            subject=excluded.subject, start_datetime=excluded.start_datetime,
            end_datetime=excluded.end_datetime, attendees=excluded.attendees
    """, e)


def get_last_sync_time(conn, source):
    """Return ISO timestamp of last successful sync for source, or None."""
    row = conn.execute("""
        SELECT last_synced_at FROM sync_log
        WHERE source = ? AND error IS NULL
        ORDER BY last_synced_at DESC LIMIT 1
    """, (source,)).fetchone()
    return row["last_synced_at"] if row else None


def get_unknown_contact_candidates(conn, min_emails=2, limit=50):
    """
    Return inbound unmatched senders grouped by address, sorted by frequency.
    Filters out obvious automated senders (noreply, mailer, etc.).
    """
    rows = conn.execute("""
        SELECT from_address,
               COUNT(*)    AS email_count,
               MAX(date)   AS last_date,
               MIN(date)   AS first_date
        FROM unmatched_emails
        WHERE direction = 'in'
          AND from_address != ''
          AND from_address NOT LIKE '%noreply%'
          AND from_address NOT LIKE '%no-reply%'
          AND from_address NOT LIKE '%donotreply%'
          AND from_address NOT LIKE '%mailer%'
          AND from_address NOT LIKE '%bounce%'
          AND from_address NOT LIKE '%notification%'
          AND from_address NOT LIKE '%alert%'
          AND from_address NOT LIKE '%support@%'
          AND from_address NOT LIKE '%info@%'
          AND from_address NOT LIKE '%newsletter%'
        GROUP BY from_address
        HAVING COUNT(*) >= ?
        ORDER BY email_count DESC, last_date DESC
        LIMIT ?
    """, (min_emails, limit)).fetchall()
    return [dict(r) for r in rows]
