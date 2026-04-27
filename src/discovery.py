"""
discovery.py — Fetch job listings from Adzuna, Reed, and SerpAPI (Google Jobs).

Usage:
    python -m src.discovery          # runs all sources, saves to DB
    python -m src.discovery --dry    # prints results without saving
"""

import os
import time
import argparse
import requests
from datetime import datetime
from dotenv import load_dotenv
from src.tracker import init_db, insert_job
from src.job_goal import parse_job_goal
from src.profiles import get_profile

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

SEARCH_ROLES = [
    # Data Engineering
    "Data Engineer",
    "Analytics Engineer",
    "ETL Developer",
    # ML / AI
    "ML Engineer",
    "Machine Learning Engineer",
    "AI Engineer",
    "NLP Engineer",
    "LLM Engineer",
    "Generative AI Engineer",
    # Data Science
    "Data Scientist",
    "Quantitative Analyst Python",
    # BI / Reporting
    "Business Intelligence Engineer",
    "BI Developer Power BI",
    "Power BI Developer",
    "Data Analyst Python",
    # Backend / Python
    "Python Developer",
    "Backend Engineer Python",
]

LOCATION = "London"
MAX_PAGES_PER_ROLE = 3      # Adzuna / Reed: 10 results per page → 30 per role
DELAY_BETWEEN_REQUESTS = 0.4  # seconds, keep dashboard refresh usable


# ── ATS detection heuristics ─────────────────────────────────────────────────

ATS_PATTERNS = {
    "greenhouse":     ["greenhouse.io", "boards.greenhouse"],
    "lever":          ["lever.co", "jobs.lever"],
    "workday":        ["myworkdayjobs", "workday.com"],
    "icims":          ["icims.com"],
    "smartrecruiters":["smartrecruiters.com"],
    "ashby":          ["ashbyhq.com", "jobs.ashbyhq"],
    "linkedin":       ["linkedin.com/jobs"],
    "taleo":          ["taleo.net", "oracle.com/taleo"],
    "bamboohr":       ["bamboohr.com"],
}

DIFFICULTY = {
    "greenhouse": 1,
    "lever": 1,
    "ashby": 1,
    "smartrecruiters": 3,  # requires account creation — manual queue
    "bamboohr": 2,
    "linkedin": 1,
    "workday": 3,
    "icims": 3,
    "taleo": 3,
    "unknown": 2,
}


def detect_ats(url: str) -> tuple[str, int]:
    url_lower = url.lower()
    for ats, patterns in ATS_PATTERNS.items():
        if any(p in url_lower for p in patterns):
            return ats, DIFFICULTY[ats]
    return "unknown", 2


def resolve_redirect_url(url: str) -> str:
    """Follow HTTP redirects to get the real destination URL (e.g. Adzuna -> Greenhouse).
    Returns the final URL, or the original if redirect fails."""
    try:
        resp = requests.head(url, allow_redirects=True, timeout=10,
                             headers={"User-Agent": "Mozilla/5.0"})
        return resp.url
    except Exception:
        try:
            resp = requests.get(url, allow_redirects=True, timeout=10,
                                headers={"User-Agent": "Mozilla/5.0"}, stream=True)
            resp.close()
            return resp.url
        except Exception:
            return url


# ── Adzuna ────────────────────────────────────────────────────────────────────

def fetch_adzuna(role: str, page: int = 1, location: str = LOCATION) -> list[dict]:
    app_id  = os.getenv("ADZUNA_APP_ID")
    api_key = os.getenv("ADZUNA_API_KEY")
    if not app_id or not api_key:
        print("[adzuna] Missing ADZUNA_APP_ID or ADZUNA_API_KEY — skipping.")
        return []

    url = f"https://api.adzuna.com/v1/api/jobs/gb/search/{page}"
    params = {
        "app_id":           app_id,
        "app_key":          api_key,
        "what":             role,
        "where":            location,
        "distance":         10,          # miles from London centre
        "results_per_page": 10,
        "content-type":     "application/json",
        "sort_by":          "date",
        "max_days_old":      5,
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[adzuna] Error fetching '{role}' page {page}: {e}")
        return []

    jobs = []
    for item in data.get("results", []):
        redirect_url = item.get("redirect_url", "")
        # Keep discovery fast. Redirect resolution is handled later for shortlisted jobs.
        ats_type, difficulty = detect_ats(redirect_url)
        jobs.append({
            "title":       item.get("title", ""),
            "company":     item.get("company", {}).get("display_name", ""),
            "location":    item.get("location", {}).get("display_name", location),
            "salary_min":  item.get("salary_min"),
            "salary_max":  item.get("salary_max"),
            "description": item.get("description", ""),
            "url":         redirect_url,
            "source":      "adzuna",
            "ats_type":    ats_type,
            "difficulty_tier": difficulty,
            "date_posted": item.get("created", ""),
        })

    return jobs


# ── Reed ──────────────────────────────────────────────────────────────────────

def fetch_reed(role: str, skip: int = 0, location: str = LOCATION) -> list[dict]:
    api_key = os.getenv("REED_API_KEY")
    if not api_key:
        print("[reed] Missing REED_API_KEY — skipping.")
        return []

    url = "https://www.reed.co.uk/api/1.0/search"
    params = {
        "keywords":            role,
        "locationName":        location,
        "distanceFromLocation": 10,
        "resultsToTake":       25,
        "resultsToSkip":       skip,
    }

    try:
        resp = requests.get(url, params=params, auth=(api_key, ""), timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[reed] Error fetching '{role}' skip={skip}: {e}")
        return []

    jobs = []
    for item in data.get("results", []):
        job_url = item.get("jobUrl", f"https://www.reed.co.uk/jobs/{item.get('jobId')}")
        ats_type, difficulty = detect_ats(job_url)
        jobs.append({
            "title":       item.get("jobTitle", ""),
            "company":     item.get("employerName", ""),
            "location":    item.get("locationName", location),
            "salary_min":  item.get("minimumSalary"),
            "salary_max":  item.get("maximumSalary"),
            "description": item.get("jobDescription", ""),
            "url":         job_url,
            "source":      "reed",
            "ats_type":    ats_type,
            "difficulty_tier": difficulty,
            "date_posted": item.get("date", ""),
        })

    return jobs


# ── SerpAPI (Google Jobs) ─────────────────────────────────────────────────────

def fetch_serpapi(role: str, location: str = LOCATION) -> list[dict]:
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key:
        print("[serpapi] Missing SERPAPI_KEY — skipping.")
        return []

    url = "https://serpapi.com/search"
    params = {
        "engine":   "google_jobs",
        "q":        f"{role} {location}",
        "hl":       "en",
        "gl":       "uk",
        "api_key":  api_key,
    }

    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[serpapi] Error fetching '{role}': {e}")
        return []

    # Preferred ATS order: direct company ATS > LinkedIn > any
    PREFERRED_ATS = ("greenhouse", "lever", "ashby", "smartrecruiters", "bamboohr")

    jobs = []
    for item in data.get("jobs_results", []):
        apply_links = item.get("apply_options", [])
        if not apply_links:
            job_url = item.get("share_link", "")
            if not job_url:
                continue
        else:
            # Pick the best ATS link — prefer direct company ATS over aggregators
            job_url = apply_links[0].get("link", "")
            for opt in apply_links:
                link = opt.get("link", "")
                ats, _ = detect_ats(link)
                if ats in PREFERRED_ATS:
                    job_url = link
                    break

        if not job_url:
            continue

        ats_type, difficulty = detect_ats(job_url)
        jobs.append({
            "title":       item.get("title", ""),
            "company":     item.get("company_name", ""),
            "location":    item.get("location", location),
            "salary_min":  None,
            "salary_max":  None,
            "description": item.get("description", ""),
            "url":         job_url,
            "source":      "serpapi",
            "ats_type":    ats_type,
            "difficulty_tier": difficulty,
            "date_posted": item.get("detected_extensions", {}).get("posted_at", ""),
        })

    return jobs


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_discovery(dry_run: bool = False, prompt: str | None = None, profile_key: str = "ron") -> int:
    """Fetch from all sources for all roles. Returns total new jobs inserted."""
    from src.enrichment import enrich_job, is_recent_job

    profile = get_profile(profile_key)
    goal = parse_job_goal(prompt or profile.search_prompt)
    roles = goal.roles
    location = goal.location
    max_pages = goal.max_pages_per_role

    if prompt:
        print(f"[discovery] Prompt-driven search for {profile.label}")
        print(f"[discovery] Roles: {', '.join(roles)}")
        print(f"[discovery] Location: {location}")

    if not dry_run:
        init_db()

    total_new = 0
    total_fetched = 0

    for role in roles:
        print(f"\n[discovery] === {role} ===")

        # Adzuna — up to MAX_PAGES_PER_ROLE pages
        for page in range(1, max_pages + 1):
            jobs = fetch_adzuna(role, page, location=location)
            if not jobs:
                break
            total_fetched += len(jobs)
            for j in jobs:
                if not is_recent_job(j.get("date_posted"), max_age_days=5):
                    continue
                j = enrich_job(j, profile_key=profile.key)
                if not dry_run:
                    row_id = insert_job(j)
                    if row_id:
                        total_new += 1
                else:
                    print(f"  [dry] {j['title']} @ {j['company']} ({j['source']}) - {j['interview_probability']}%")
            time.sleep(DELAY_BETWEEN_REQUESTS)

        # Reed — 2 pages × 25 = 50 per role (disabled if returning 500)
        for skip in [0, 25]:
            jobs = fetch_reed(role, skip, location=location)
            if not jobs:
                break
            total_fetched += len(jobs)
            for j in jobs:
                if not is_recent_job(j.get("date_posted"), max_age_days=5):
                    continue
                j = enrich_job(j, profile_key=profile.key)
                if not dry_run:
                    row_id = insert_job(j)
                    if row_id:
                        total_new += 1
                else:
                    print(f"  [dry] {j['title']} @ {j['company']} ({j['source']}) - {j['interview_probability']}%")
            time.sleep(DELAY_BETWEEN_REQUESTS)
            break  # Reed 500s consistently — skip second page until resolved

        # SerpAPI — 1 page per role (free tier conserved)
        jobs = fetch_serpapi(role, location=location)
        total_fetched += len(jobs)
        for j in jobs:
            if not is_recent_job(j.get("date_posted"), max_age_days=5):
                continue
            j = enrich_job(j, profile_key=profile.key)
            if not dry_run:
                row_id = insert_job(j)
                if row_id:
                    total_new += 1
            else:
                print(f"  [dry] {j['title']} @ {j['company']} ({j['source']}) - {j['interview_probability']}%")
        time.sleep(DELAY_BETWEEN_REQUESTS)

    print(f"\n[discovery] Done. Fetched: {total_fetched} | New (no duplicates): {total_new}")
    return total_new


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="Print results without saving")
    parser.add_argument("--prompt", help="Natural-language job-search goal")
    parser.add_argument("--profile", default="ron", choices=["ron", "heba"])
    args = parser.parse_args()
    run_discovery(dry_run=args.dry, prompt=args.prompt, profile_key=args.profile)
