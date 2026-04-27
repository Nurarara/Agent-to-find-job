"""
tracker.py — SQLite database schema and helper functions for job tracking.
"""

import sqlite3
import json
import re
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "jobs.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create all tables if they don't exist."""
    conn = get_conn()
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            title           TEXT NOT NULL,
            company         TEXT NOT NULL,
            location        TEXT,
            salary_min      INTEGER,
            salary_max      INTEGER,
            description     TEXT,
            url             TEXT NOT NULL,
            source          TEXT,           -- adzuna | reed | serpapi
            ats_type        TEXT,           -- greenhouse | lever | workday | icims | linkedin | unknown
            difficulty_tier INTEGER DEFAULT 2,  -- 1=easy, 2=medium, 3=hard/skip
            relevance_score REAL DEFAULT 0.0,
            date_found      TEXT,
            date_posted     TEXT,
            status          TEXT DEFAULT 'pending',
            -- pending | applied | custom_q_review | skipped | rejected | interview | offer
            resume_path     TEXT,
            cover_letter    TEXT,
            custom_qa       TEXT,           -- JSON list of {question, answer}
            notes           TEXT,
            applied_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS daily_stats (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT UNIQUE,
            found       INTEGER DEFAULT 0,
            applied     INTEGER DEFAULT 0,
            skipped     INTEGER DEFAULT 0,
            interviews  INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_jobs_status   ON jobs(status);
        CREATE INDEX IF NOT EXISTS idx_jobs_source   ON jobs(source);
        CREATE INDEX IF NOT EXISTS idx_jobs_found    ON jobs(date_found);
        CREATE INDEX IF NOT EXISTS idx_jobs_company  ON jobs(company);
    """)
    _ensure_column(cur, "jobs", "profile", "TEXT DEFAULT 'ron'")
    _ensure_column(cur, "jobs", "role_family", "TEXT")
    _ensure_column(cur, "jobs", "interview_probability", "REAL DEFAULT 0.0")
    _ensure_column(cur, "jobs", "probability_reason", "TEXT")
    _ensure_column(cur, "jobs", "recruiter_profiles", "TEXT")
    _ensure_column(cur, "jobs", "last_enriched_at", "TEXT")
    _ensure_column(cur, "jobs", "job_key", "TEXT")
    _migrate_url_unique_constraint(conn, cur)
    _backfill_job_keys(cur)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status   ON jobs(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_source   ON jobs(source)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_found    ON jobs(date_found)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_company  ON jobs(company)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_profile_key ON jobs(profile, job_key)")

    conn.commit()
    conn.close()
    print(f"[tracker] DB initialised at {DB_PATH}")


def _ensure_column(cur: sqlite3.Cursor, table: str, column: str, definition: str) -> None:
    cur.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cur.fetchall()}
    if column not in existing:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate_url_unique_constraint(conn: sqlite3.Connection, cur: sqlite3.Cursor) -> None:
    """Older DBs had url UNIQUE, which prevents Ron and Heba tracking the same job."""
    cur.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'jobs'")
    row = cur.fetchone()
    if not row or "TEXT UNIQUE NOT NULL" not in (row[0] or ""):
        return

    cur.execute("ALTER TABLE jobs RENAME TO jobs_old")
    cur.executescript("""
        CREATE TABLE jobs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            title           TEXT NOT NULL,
            company         TEXT NOT NULL,
            location        TEXT,
            salary_min      INTEGER,
            salary_max      INTEGER,
            description     TEXT,
            url             TEXT NOT NULL,
            source          TEXT,
            ats_type        TEXT,
            difficulty_tier INTEGER DEFAULT 2,
            relevance_score REAL DEFAULT 0.0,
            date_found      TEXT,
            date_posted     TEXT,
            status          TEXT DEFAULT 'pending',
            resume_path     TEXT,
            cover_letter    TEXT,
            custom_qa       TEXT,
            notes           TEXT,
            applied_at      TEXT,
            profile         TEXT DEFAULT 'ron',
            role_family     TEXT,
            interview_probability REAL DEFAULT 0.0,
            probability_reason TEXT,
            recruiter_profiles TEXT,
            last_enriched_at TEXT,
            job_key         TEXT
        );
    """)
    cur.execute("""
        INSERT INTO jobs (
            id, title, company, location, salary_min, salary_max, description, url,
            source, ats_type, difficulty_tier, relevance_score, date_found, date_posted,
            status, resume_path, cover_letter, custom_qa, notes, applied_at, profile,
            role_family, interview_probability, probability_reason, recruiter_profiles,
            last_enriched_at, job_key
        )
        SELECT
            id, title, company, location, salary_min, salary_max, description, url,
            source, ats_type, difficulty_tier, relevance_score, date_found, date_posted,
            status, resume_path, cover_letter, custom_qa, notes, applied_at,
            COALESCE(profile, 'ron'), role_family, interview_probability,
            probability_reason, recruiter_profiles, last_enriched_at, job_key
        FROM jobs_old
    """)
    cur.execute("DROP TABLE jobs_old")
    conn.commit()


def _backfill_job_keys(cur: sqlite3.Cursor) -> None:
    cur.execute("SELECT id, title, company, location FROM jobs WHERE job_key IS NULL OR job_key = ''")
    rows = cur.fetchall()
    for row in rows:
        cur.execute(
            "UPDATE jobs SET job_key = ? WHERE id = ?",
            (make_job_key(row[1], row[2], row[3]), row[0]),
        )


def make_job_key(title: str, company: str, location: str = "") -> str:
    """Create a stable duplicate key across job boards."""
    def clean(value: str) -> str:
        value = (value or "").lower()
        value = re.sub(r"\b(senior|junior|mid|lead|principal|associate)\b", "", value)
        value = re.sub(r"[^a-z0-9]+", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    return "|".join(part for part in [clean(company), clean(title), clean(location)[:30]] if part)


# ── CRUD helpers ──────────────────────────────────────────────────────────────

def insert_job(job: dict) -> int | None:
    """Insert a job. Returns new row id, or None if URL already exists."""
    conn = get_conn()
    cur = conn.cursor()
    job_key = job.get("job_key") or make_job_key(
        job.get("title", ""),
        job.get("company", ""),
        job.get("location", ""),
    )
    profile = job.get("profile", "ron")
    cur.execute("SELECT id FROM jobs WHERE profile = ? AND job_key = ? LIMIT 1", (profile, job_key))
    if cur.fetchone():
        conn.close()
        return None
    try:
        cur.execute("""
            INSERT INTO jobs
                (title, company, location, salary_min, salary_max, description,
                 url, source, ats_type, difficulty_tier, relevance_score,
                 date_found, date_posted, status, role_family, interview_probability,
                 probability_reason, recruiter_profiles, last_enriched_at, job_key, profile)
            VALUES
                (:title, :company, :location, :salary_min, :salary_max, :description,
                 :url, :source, :ats_type, :difficulty_tier, :relevance_score,
                 :date_found, :date_posted, 'pending', :role_family, :interview_probability,
                 :probability_reason, :recruiter_profiles, :last_enriched_at, :job_key, :profile)
        """, {
            "title":          job.get("title", ""),
            "company":        job.get("company", ""),
            "location":       job.get("location", "London"),
            "salary_min":     job.get("salary_min"),
            "salary_max":     job.get("salary_max"),
            "description":    job.get("description", ""),
            "url":            job["url"],
            "source":         job.get("source", "unknown"),
            "ats_type":       job.get("ats_type", "unknown"),
            "difficulty_tier": job.get("difficulty_tier", 2),
            "relevance_score": job.get("relevance_score", 0.0),
            "date_found":     datetime.utcnow().isoformat(),
            "date_posted":    job.get("date_posted"),
            "role_family":     job.get("role_family"),
            "interview_probability": job.get("interview_probability", 0.0),
            "probability_reason": job.get("probability_reason"),
            "recruiter_profiles": job.get("recruiter_profiles"),
            "last_enriched_at": job.get("last_enriched_at"),
            "job_key":         job_key,
            "profile":         profile,
        })
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None  # duplicate URL
    finally:
        conn.close()


def update_job(job_id: int, **fields):
    """Update arbitrary fields on a job row."""
    if not fields:
        return
    # If updating url, skip if another row already owns that url
    if "url" in fields:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT id FROM jobs WHERE url = ? AND id != ?", (fields["url"], job_id))
        if cur.fetchone():
            # URL belongs to a different job — don't update url, keep rest
            fields = {k: v for k, v in fields.items() if k != "url"}
            conn.close()
            if not fields:
                return
        else:
            conn.close()
    conn = get_conn()
    cur = conn.cursor()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [job_id]
    cur.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


def mark_applied(job_id: int, resume_path: str, cover_letter: str):
    update_job(
        job_id,
        status="applied",
        resume_path=resume_path,
        cover_letter=cover_letter,
        applied_at=datetime.utcnow().isoformat(),
    )


def mark_custom_q_review(job_id: int, qa_list: list[dict]):
    """Flag job as needing human review for custom questions."""
    update_job(job_id, status="custom_q_review", custom_qa=json.dumps(qa_list))


def get_jobs(status: str | None = None, limit: int = 200) -> list[sqlite3.Row]:
    conn = get_conn()
    cur = conn.cursor()
    if status:
        cur.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY relevance_score DESC LIMIT ?",
            (status, limit),
        )
    else:
        cur.execute("SELECT * FROM jobs ORDER BY date_found DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_stats(date: str | None = None, profile: str | None = None) -> dict:
    """Return application stats for a given date (default: today)."""
    date = date or datetime.utcnow().strftime("%Y-%m-%d")
    conn = get_conn()
    cur = conn.cursor()

    profile_filter = "AND COALESCE(profile, 'ron') = ?" if profile else ""
    params = [date]
    if profile:
        params.append(profile)
    params.append(date)
    if profile:
        params.extend([profile, profile, profile, profile])

    cur.execute(f"""
        SELECT
            COUNT(*) FILTER (WHERE date_found LIKE ? || '%' {profile_filter})       AS found_today,
            COUNT(*) FILTER (WHERE status = 'applied'
                               AND applied_at LIKE ? || '%' {profile_filter})       AS applied_today,
            COUNT(*) FILTER (WHERE status = 'custom_q_review' {profile_filter})     AS pending_review,
            COUNT(*) FILTER (WHERE status = 'interview' {profile_filter})           AS interviews,
            COUNT(*) FILTER (WHERE status = 'skipped' {profile_filter})             AS skipped
        FROM jobs
    """, params)
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {}


_STAT_COLUMNS = frozenset({"found", "applied", "skipped", "interviews"})

def bump_daily_stats(date: str, **kwargs):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO daily_stats (date) VALUES (?)", (date,)
    )
    for col, delta in kwargs.items():
        if col not in _STAT_COLUMNS:
            raise ValueError(f"Invalid stat column: {col!r}")
        cur.execute(
            f"UPDATE daily_stats SET {col} = {col} + ? WHERE date = ?",
            (delta, date),
        )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("[tracker] Tables ready.")
    print("[tracker] Stats today:", get_stats())
