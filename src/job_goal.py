"""
job_goal.py - Parse a natural-language job-search prompt into runtime settings.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from dotenv import load_dotenv

from src.utils import MODEL, get_gemini_client

load_dotenv()


DEFAULT_ROLES = [
    "Data Engineer",
    "Analytics Engineer",
    "ETL Developer",
    "ML Engineer",
    "Machine Learning Engineer",
    "AI Engineer",
    "NLP Engineer",
    "LLM Engineer",
    "Generative AI Engineer",
    "Data Scientist",
    "Quantitative Analyst Python",
    "Business Intelligence Engineer",
    "BI Developer Power BI",
    "Power BI Developer",
    "Data Analyst Python",
    "Python Developer",
    "Backend Engineer Python",
]


@dataclass
class JobGoal:
    roles: list[str] = field(default_factory=lambda: DEFAULT_ROLES.copy())
    location: str = "London"
    include_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    min_salary: int | None = None
    max_pages_per_role: int = 2


def _extract_json(text: str) -> dict:
    raw = text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start:end + 1]
    return json.loads(raw)


def _fallback_parse(prompt: str) -> JobGoal:
    lower = prompt.lower()
    goal = JobGoal()

    if "remote" in lower:
        goal.location = "United Kingdom"
        goal.include_keywords.append("remote")
    elif "london" in lower:
        goal.location = "London"
    elif "uk" in lower or "united kingdom" in lower:
        goal.location = "United Kingdom"

    roles = []
    role_patterns = [
        ("data engineer", "Data Engineer"),
        ("analytics engineer", "Analytics Engineer"),
        ("machine learning", "Machine Learning Engineer"),
        ("ml engineer", "ML Engineer"),
        ("ai engineer", "AI Engineer"),
        ("data scientist", "Data Scientist"),
        ("data analyst", "Data Analyst Python"),
        ("business intelligence", "Business Intelligence Engineer"),
        ("power bi", "Power BI Developer"),
        ("python developer", "Python Developer"),
        ("backend", "Backend Engineer Python"),
    ]
    for needle, role in role_patterns:
        if needle in lower and role not in roles:
            roles.append(role)
    if roles:
        goal.roles = roles

    salary_match = re.search(r"(?:£|gbp|pounds?)\s?(\d{2,3})\s?k", lower)
    if salary_match:
        goal.min_salary = int(salary_match.group(1)) * 1000

    for keyword in ["contract", "senior", "lead", "principal", "java", ".net", "c#"]:
        if f"no {keyword}" in lower or f"not {keyword}" in lower or f"exclude {keyword}" in lower:
            goal.exclude_keywords.append(keyword)

    return goal


def parse_job_goal(prompt: str | None) -> JobGoal:
    if not prompt or not prompt.strip():
        return JobGoal()

    try:
        client = get_gemini_client()
        response = client.models.generate_content(
            model=MODEL,
            contents=f"""Extract a job-search goal from this user prompt.

Return ONLY valid JSON with these keys:
{{
  "roles": ["2 to 8 concise job search titles"],
  "location": "city/country/search location",
  "include_keywords": ["keywords that should increase relevance"],
  "exclude_keywords": ["keywords that should be rejected"],
  "min_salary": integer_or_null,
  "max_pages_per_role": integer_between_1_and_4
}}

Defaults if unclear: location London, max_pages_per_role 2.
Prompt: {prompt}
""",
        )
        data = _extract_json(response.text)
        return JobGoal(
            roles=[str(r).strip() for r in data.get("roles", []) if str(r).strip()] or DEFAULT_ROLES.copy(),
            location=str(data.get("location") or "London").strip(),
            include_keywords=[str(k).strip().lower() for k in data.get("include_keywords", []) if str(k).strip()],
            exclude_keywords=[str(k).strip().lower() for k in data.get("exclude_keywords", []) if str(k).strip()],
            min_salary=data.get("min_salary"),
            max_pages_per_role=max(1, min(4, int(data.get("max_pages_per_role") or 2))),
        )
    except Exception as exc:
        print(f"[job_goal] Gemini parse failed, using local parser: {exc}")
        return _fallback_parse(prompt)
