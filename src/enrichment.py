"""
enrichment.py - Fit scoring, recent-job filtering, and LinkedIn contact lookup.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

from src.discovery import detect_ats
from src.filter import MIN_SALARY, score_job
from src.profiles import get_profile
from src.tracker import update_job

load_dotenv()

GOOD_ATS = {"greenhouse", "lever", "ashby", "linkedin", "bamboohr"}


def parse_posted_at(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    now = datetime.utcnow()

    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            candidate = raw[:19].replace("Z", "") if "%H" in fmt else raw[:10]
            return datetime.strptime(candidate, fmt.replace("Z", ""))
        except ValueError:
            pass

    lower = raw.lower()
    if "today" in lower or "just" in lower:
        return now
    if "yesterday" in lower:
        return now - timedelta(days=1)

    match = re.search(r"(\d+)\s+(minute|hour|day|week|month)s?\s+ago", lower)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit == "minute":
            return now - timedelta(minutes=amount)
        if unit == "hour":
            return now - timedelta(hours=amount)
        if unit == "day":
            return now - timedelta(days=amount)
        if unit == "week":
            return now - timedelta(days=amount * 7)
        if unit == "month":
            return now - timedelta(days=amount * 30)

    reed_match = re.search(r"/Date\((\d+)", raw)
    if reed_match:
        return datetime.utcfromtimestamp(int(reed_match.group(1)) / 1000)

    return None


def is_recent_job(date_posted: str | None, max_age_days: int = 5) -> bool:
    parsed = parse_posted_at(date_posted)
    if not parsed:
        return False
    return parsed >= datetime.utcnow() - timedelta(days=max_age_days)


def classify_role(title: str, description: str = "", profile_key: str = "ron") -> str:
    profile = get_profile(profile_key)
    text = f"{title} {description}".lower()
    scores = {
        family: sum(1 for keyword in keywords if keyword in text)
        for family, keywords in profile.role_keywords.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "Relevant Tech/Data"


def estimate_interview_probability(job: dict, profile_key: str = "ron") -> tuple[float, str]:
    profile = get_profile(profile_key)
    title = job.get("title", "")
    desc = job.get("description", "") or ""
    text = f"{title} {desc}".lower()
    relevance = score_job(title, desc, profile.key)
    ats, difficulty = detect_ats(job.get("url", ""))
    salary_max = job.get("salary_max")

    probability = 8 + relevance * 5.5
    reasons = [f"fit score {relevance}/10"]

    if ats in GOOD_ATS:
        probability += 8
        reasons.append(f"direct/easier ATS: {ats}")
    elif ats in {"workday", "icims", "taleo"}:
        probability -= 8
        reasons.append(f"hard ATS: {ats}")
    elif ats == "unknown":
        probability -= 5
        reasons.append("apply link not resolved")

    if difficulty >= 3:
        probability -= 8

    if salary_max and salary_max < MIN_SALARY:
        probability -= 10
        reasons.append("salary below target")

    if any(keyword in text for keyword in profile.exclude_keywords):
        probability -= 22
        reasons.append("seniority/stack mismatch")

    if any(keyword in text for keyword in profile.junior_bonus_keywords):
        probability += 8
        reasons.append("level may match")

    if any(keyword in text for keyword in profile.senior_penalty_keywords):
        probability -= 12
        reasons.append("likely too senior for profile")

    probability = max(1.0, min(75.0, round(probability, 1)))
    return probability, "; ".join(reasons)


def enrich_job(job: dict, include_contacts: bool = False, profile_key: str = "ron") -> dict:
    profile = get_profile(profile_key)
    enriched = dict(job)
    enriched["profile"] = profile.key
    enriched["role_family"] = classify_role(enriched.get("title", ""), enriched.get("description", ""), profile.key)
    probability, reason = estimate_interview_probability(enriched, profile.key)
    enriched["interview_probability"] = probability
    enriched["probability_reason"] = reason
    enriched["last_enriched_at"] = datetime.utcnow().isoformat()
    if include_contacts:
        enriched["recruiter_profiles"] = json.dumps(find_linkedin_contacts(
            enriched.get("company", ""),
            enriched.get("title", ""),
        ))
    return enriched


def persist_enrichment(job_id: int, job: dict, include_contacts: bool = False, profile_key: str = "ron") -> dict:
    enriched = enrich_job(job, include_contacts=include_contacts, profile_key=profile_key)
    update_job(
        job_id,
        role_family=enriched.get("role_family"),
        interview_probability=enriched.get("interview_probability", 0.0),
        probability_reason=enriched.get("probability_reason"),
        recruiter_profiles=enriched.get("recruiter_profiles"),
        last_enriched_at=enriched.get("last_enriched_at"),
        profile=enriched.get("profile"),
    )
    return enriched


def build_outreach_message(profile_key: str, company: str, title: str, contact_name: str = "") -> str:
    profile = get_profile(profile_key)
    greeting = f"Hi {contact_name.split()[0]}," if contact_name else "Hi,"
    return (
        f"{greeting}\n\n"
        f"I saw the {title} role at {company} and thought it looked like a strong match. "
        f"{profile.outreach_context} I have applied or am about to apply, and wanted to reach out directly "
        f"because the role lines up closely with my background.\n\n"
        f"If you are the right person for this role, I would appreciate any guidance on the hiring process. "
        f"If not, I would be grateful if you could point me toward the right recruiter or hiring manager.\n\n"
        f"Best,\n{profile.first_name}"
    )


def find_linkedin_contacts(company: str, job_title: str, limit: int = 5) -> list[dict]:
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key or not company:
        return []

    role_terms = " OR ".join([
        '"talent acquisition"',
        "recruiter",
        '"hiring manager"',
        '"head of data"',
        '"data engineering manager"',
        '"analytics manager"',
        '"machine learning manager"',
    ])
    query = f'site:linkedin.com/in "{company}" ({role_terms}) "{job_title.split()[0]}"'

    try:
        resp = requests.get(
            "https://serpapi.com/search",
            params={"engine": "google", "q": query, "num": limit, "hl": "en", "gl": "uk", "api_key": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return [{"name": "LinkedIn search failed", "title": str(exc)[:120], "url": ""}]

    contacts: list[dict] = []
    seen: set[str] = set()
    for result in data.get("organic_results", []):
        link = result.get("link", "")
        if "linkedin.com/in/" not in link or link in seen:
            continue
        seen.add(link)
        title = result.get("title", "")
        name = title.split(" - ")[0].split(" | ")[0].strip()
        contacts.append({
            "name": name or title,
            "title": result.get("snippet", "")[:220],
            "url": link,
        })
        if len(contacts) >= limit:
            break
    return contacts
