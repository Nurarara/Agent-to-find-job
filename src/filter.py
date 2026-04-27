"""
filter.py — Score and filter jobs already in the DB.

Scoring is based on keyword overlap between the job description / title
and Rounak's skill set. Jobs that score below MIN_SCORE are marked 'skipped'.
Runs after discovery to rank the day's batch.

Usage:
    python -m src.filter
"""

import re
from datetime import datetime
from src.tracker import get_conn, update_job, get_jobs
from src.profiles import get_profile

# ── Rounak's profile keywords (weighted) ─────────────────────────────────────
# Format: (keyword, weight)
SKILL_WEIGHTS: list[tuple[str, float]] = [
    # Core must-haves
    ("python",           3.0),
    ("sql",              2.5),
    ("data engineer",    3.0),
    ("data engineering", 3.0),
    ("etl",              2.5),
    ("pipeline",         2.0),

    # ML / AI stack
    ("machine learning", 3.0),
    ("ml",               2.0),
    ("deep learning",    2.5),
    ("pytorch",          2.5),
    ("tensorflow",       2.0),
    ("scikit",           2.0),
    ("hugging face",     2.5),
    ("transformers",     2.5),
    ("nlp",              2.5),
    ("llm",              2.5),
    ("ai engineer",      3.0),
    ("generative ai",    2.5),

    # Cloud / infra
    ("gcp",              2.5),
    ("google cloud",     2.0),
    ("bigquery",         2.5),
    ("spark",            2.0),
    ("airflow",          2.0),
    ("dbt",              2.0),
    ("kafka",            1.5),
    ("aws",              1.5),
    ("azure",            1.5),

    # Analytics / BI
    ("data scientist",        2.5),
    ("analytics engineer",    2.5),
    ("power bi",              2.5),
    ("business intelligence", 2.5),
    ("bi developer",          2.5),
    ("bi engineer",           2.5),
    ("data analyst",          2.0),
    ("tableau",               1.5),
    ("looker",                1.5),
    ("eda",                   1.5),
    ("a/b test",              1.5),
    ("reporting",             1.0),
    ("dashboard",             1.0),

    # General tech
    ("typescript",       1.5),
    ("git",              1.0),
    ("api",              1.0),
    ("docker",           1.5),
    ("kubernetes",       1.5),
    ("rest",             1.0),

    # Soft / role signals
    ("graduate",         0.5),
    ("junior",           1.0),
    ("mid",              1.0),
    ("london",           1.5),
    ("hybrid",           1.0),
    ("remote",           1.0),
]

# Normalise to 0-10 against a realistic strong-match score.
# A good posting will mention a role title plus several relevant tools, not
# every keyword in the profile.
_MAX_RAW = 18.0

# Jobs below this normalised score get auto-skipped
MIN_SCORE = 1.5

# Skip jobs where max salary is explicitly below this threshold
MIN_SALARY = 32000

# Hard exclude: if title or description contains these → skip immediately
EXCLUDE_KEYWORDS = [
    "senior staff", "principal engineer", "staff engineer",
    "principal", "vp of", "head of", "director", "lead engineer", "lead ",
    "c++", "c#", ".net", "java ", "ios developer", "android developer",
    "embedded", "devops only", "salesforce", "sap ", "erp",
]


def score_job(title: str, description: str, profile_key: str = "ron") -> float:
    profile = get_profile(profile_key)
    text = f"{title} {description}".lower()
    raw = sum(w for kw, w in profile.skill_weights if kw in text)
    return round(min(10.0, (raw / _MAX_RAW) * 10), 2)


def should_exclude(title: str, description: str, profile_key: str = "ron") -> bool:
    profile = get_profile(profile_key)
    text = f"{title} {description}".lower()
    return any(kw in text for kw in profile.exclude_keywords)


def run_filter(min_score: float = MIN_SCORE, profile_key: str = "ron") -> dict:
    """Score all pending jobs; skip low-scorers. Returns summary counts."""
    profile = get_profile(profile_key)
    conn = get_conn()
    cur = conn.cursor()

    # Fetch pending jobs
    cur.execute("""
        SELECT id, title, company, description, status, salary_min, salary_max
        FROM jobs
        WHERE status = 'pending' AND COALESCE(profile, 'ron') = ?
    """, (profile.key,))
    rows = cur.fetchall()
    conn.close()

    scored = skipped = updated = 0

    for row in rows:
        job_id     = row["id"]
        title      = row["title"] or ""
        desc       = row["description"] or ""
        salary_max = row["salary_max"]

        if should_exclude(title, desc, profile.key):
            update_job(job_id, status="skipped", notes="auto-excluded by keyword filter")
            skipped += 1
            continue

        # Skip jobs explicitly paying below minimum threshold
        if salary_max and salary_max < MIN_SALARY:
            update_job(job_id, status="skipped",
                       notes=f"salary too low (max £{int(salary_max):,})")
            skipped += 1
            continue

        score = score_job(title, desc, profile.key)
        if score < min_score:
            update_job(job_id, status="skipped",
                       relevance_score=score,
                       notes=f"low score ({score})")
            skipped += 1
        else:
            update_job(job_id, relevance_score=score)
            updated += 1
        scored += 1

    summary = {
        "total_processed": scored,
        "kept":            updated,
        "skipped":         skipped,
        "timestamp":       datetime.utcnow().isoformat(),
    }
    print(f"[filter] {profile.label}: Processed {scored} jobs -> kept {updated}, skipped {skipped}")
    return summary


def top_jobs(n: int = 20, profile_key: str = "ron") -> list:
    """Return top-n pending jobs by relevance score."""
    profile = get_profile(profile_key)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, title, company, url, ats_type, difficulty_tier, relevance_score
        FROM jobs
        WHERE status = 'pending' AND relevance_score > 0 AND COALESCE(profile, 'ron') = ?
        ORDER BY relevance_score DESC
        LIMIT ?
    """, (profile.key, n))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    run_filter()
    print("\nTop 10 jobs right now:")
    for j in top_jobs(10):
        print(f"  [{j['relevance_score']:4.1f}] {j['title']} @ {j['company']}  [{j['ats_type']}]")
