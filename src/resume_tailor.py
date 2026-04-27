"""
resume_tailor.py — Generate a tailored resume variant for a specific job.

Takes the base resume template (assets/resume_base.docx) and injects a
job-specific summary + keyword-matched bullets using Claude API.
Saves to output/resumes/<job_id>_<company>.docx

Usage:
    from src.resume_tailor import tailor_resume
    path = tailor_resume(job_id=42)
"""

import os
import re
import json
import shutil
from pathlib import Path
from dotenv import load_dotenv
from src.tracker import get_conn, update_job
from src.utils import get_gemini_client, load_voice, MODEL

load_dotenv()

BASE_RESUME = Path(__file__).parent.parent / "assets" / "resume_base.docx"
RESUMES_DIR = Path(__file__).parent.parent / "assets" / "resumes"
OUTPUT_DIR  = Path(__file__).parent.parent / "output" / "resumes"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Role classifier: pick the best pre-built resume template ─────────────────
ROLE_KEYWORDS = {
    "bi_analyst":    ["power bi", "bi developer", "bi engineer", "business intelligence",
                      "tableau", "looker", "reporting", "dashboard", "data analyst"],
    "data_engineer": ["data engineer", "etl", "pipeline", "dbt", "airflow", "spark",
                      "bigquery", "data platform", "analytics engineer", "lakehouse"],
    "ml_engineer":   ["ml engineer", "machine learning engineer", "mlops", "pytorch",
                      "tensorflow", "model deploy", "deep learning", "computer vision"],
    "ai_engineer":   ["ai engineer", "llm", "generative ai", "nlp engineer", "rag",
                      "large language model", "hugging face", "agentic", "llm engineer"],
    "data_scientist":["data scientist", "data science", "statistical", "a/b test",
                      "predictive model", "scikit", "forecasting", "quantitative"],
}

def pick_resume_template(title: str, description: str) -> Path:
    """Return the best-matching pre-built resume template path."""
    text = f"{title} {description}".lower()
    scores = {}
    for role, keywords in ROLE_KEYWORDS.items():
        scores[role] = sum(1 for kw in keywords if kw in text)
    best = max(scores, key=scores.get)
    template = RESUMES_DIR / f"{best}.docx"
    if template.exists():
        return template
    return BASE_RESUME  # fallback

def generate_tailored_summary(job_title: str, company: str, description: str) -> dict:
    """
    Ask Gemini to extract ATS keywords for a role.
    Returns dict with keys: summary, bullets, keywords
    """
    client = get_gemini_client()
    voice  = load_voice()

    prompt = f"""You are helping Rounak Thakur tailor his resume for a specific job application.

## Rounak's voice and background:
{voice}

## Target job:
Title: {job_title}
Company: {company}
Job Description:
{description[:3000]}

## Your task:
Produce a JSON object with exactly these keys:

{{
  "summary": "2-3 sentence professional summary. First person. Tailored to THIS role. No buzzwords.",
  "bullets": [
    "Bullet 1 — from real experience, uses JD keywords naturally",
    "Bullet 2",
    "Bullet 3",
    "Bullet 4"
  ],
  "keywords": ["keyword1", "keyword2", "...up to 10 ATS keywords from the JD"]
}}

Rules:
- summary must reference the specific role title and something from the JD
- bullets must start with a strong action verb (Built, Designed, Automated, Delivered, etc.)
- bullets must include quantified impact where possible (use Rounak's real numbers)
- keywords should be exact phrases from the JD — used for ATS scanning
- DO NOT use: leverage, utilize, synergy, passionate, thrilled, excited, innovative, cutting-edge
- Output ONLY valid JSON, nothing else"""

    response = client.models.generate_content(model=MODEL, contents=prompt)
    raw = response.text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"summary": raw, "bullets": [], "keywords": []}


def tailor_resume(job_id: int) -> str | None:
    """
    Pick the best pre-built resume template for this job type,
    copy it to output/resumes/, update DB. Returns output path.
    Gemini is only called for keyword extraction (fast, cheap).
    """
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    job  = cur.fetchone()
    conn.close()

    if not job:
        print(f"[resume_tailor] Job {job_id} not found in DB.")
        return None

    title   = job["title"]
    company = job["company"]
    desc    = job["description"] or ""

    # Pick the right template based on role type
    template_path = pick_resume_template(title, desc)
    template_name = template_path.stem
    print(f"[resume_tailor] Using template '{template_name}' for: {title} @ {company}")

    # Just get keywords via Gemini (much faster than full summary generation)
    tailored = generate_tailored_summary(title, company, desc)

    # Copy the template to output dir
    import shutil
    safe_company = re.sub(r"[^\w\-]", "_", company)[:30]
    filename = f"{job_id}_{safe_company}.docx"
    out_path = OUTPUT_DIR / filename
    shutil.copy2(template_path, out_path)

    # Update DB
    update_job(job_id, resume_path=str(out_path))

    print(f"[resume_tailor] Saved -> {out_path}")
    print(f"[resume_tailor] Keywords: {', '.join(tailored.get('keywords', []))}")
    return str(out_path)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m src.resume_tailor <job_id>")
    else:
        tailor_resume(int(sys.argv[1]))
