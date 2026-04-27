"""
qa_engine.py — Detect and answer custom application questions.

Custom questions (e.g. "Why do you want to join us?") are answered using Claude
with company context + Rounak's voice profile. Answers are NEVER auto-submitted —
they are flagged for human review first.

Usage:
    from src.qa_engine import detect_custom_questions, answer_questions
    questions = detect_custom_questions(form_fields)
    answered  = answer_questions(job_id, questions)
"""

import re
import json
from dotenv import load_dotenv
from src.tracker import get_conn, get_jobs, mark_custom_q_review
from src.utils import get_gemini_client, load_voice, MODEL, VOICE_PROFILE

load_dotenv()

# ── Question detection patterns ───────────────────────────────────────────────

# Logistical questions — answered automatically, never flagged for review
LOGISTICAL_PATTERNS = [
    r"salary (expectation|requirement|range|preference)",
    r"(what (is|are) your|expected|desired) (salary|compensation|pay)",
    r"notice period",
    r"when (can|could|would) you (start|be available)",
    r"earliest (start|available)",
    r"right to work",
    r"work (authoris|authoriz)",
    r"visa (status|sponsorship|required|requirement)",
    r"require.*sponsorship",
    r"(are you|do you) (eligible|authorised|authorized)",
    r"additional (information|comments|details)",
    r"anything else (you.d|you would) like (us|to) know",
    r"any other (information|comments)",
    r"cover letter",
]

# Genuine custom questions — require Gemini + human review before submit
CUSTOM_Q_PATTERNS = [
    r"why (do you want to|are you interested in) (join|work|apply)",
    r"why (us|our company|this (role|position|company))",
    r"what (interests|attracts|drew) you (to|about)",
    r"tell us (about yourself|why you)",
    r"what (do you know about|can you tell us about) (us|our company)",
    r"why (should we|would you be) (hire|a good fit)",
    r"what (are your|is your) (strength|weakness|goal|motivation)",
    r"describe (a time|an experience|yourself|your)",
    r"how (do|would) you (handle|approach|describe)",
    r"what (makes|sets) you apart",
    r"where do you see yourself",
    r"(have you|did you) (built|worked on|experience)",
    r"tell me about (a|your)",
    r"what (excites|appeals|attracts) you",
    r"what (would you|do you) bring",
    r"why are you (leaving|looking)",
]

_LOGISTICAL_COMPILED = [re.compile(p, re.IGNORECASE) for p in LOGISTICAL_PATTERNS]
_COMPILED_PATTERNS   = [re.compile(p, re.IGNORECASE) for p in CUSTOM_Q_PATTERNS]


def is_logistical(field: str) -> bool:
    """Return True if this field is a standard logistical question (auto-answerable)."""
    return any(p.search(field) for p in _LOGISTICAL_COMPILED)


def detect_custom_questions(fields: list[str]) -> list[str]:
    """
    Return only open-ended custom questions requiring a thoughtful answer.
    Standard logistical questions (salary, notice, visa) are excluded.
    """
    custom = []
    for field in fields:
        if is_logistical(field):
            continue  # handled automatically — don't flag for review
        if any(p.search(field) for p in _COMPILED_PATTERNS):
            custom.append(field)
    return custom


def _load_voice() -> str:
    if VOICE_PROFILE.exists():
        return VOICE_PROFILE.read_text(encoding="utf-8")
    return ""


def _answer_single(
    question: str,
    job_title: str,
    company: str,
    job_description: str,
    company_context: str,
    voice: str,
    client,
) -> str:
    """Generate a single answer using Claude."""

    # Special handling for factual/logistical questions
    if is_logistical(question):
        return _handle_logistical(question, company_context=company_context)

    prompt = f"""You are writing a job application answer for Rounak Thakur.
This answer will be submitted directly to a human recruiter. It must NOT sound AI-generated.

## Rounak's background and voice:
{voice}

## Job being applied for:
Title: {job_title}
Company: {company}

## Job Description (excerpt):
{job_description[:1500]}

## Company research:
{company_context[:1000] if company_context else "Not available."}

## Question to answer:
"{question}"

## Rules (critical):
1. Write in first person as Rounak
2. Be specific — reference something real about this company or role
3. Connect to Rounak's actual experience (use the real details in his profile)
4. DO NOT start with "I am writing..." or "I am excited/thrilled/passionate..."
5. DO NOT use: leverage, utilize, synergy, innovative, groundbreaking, cutting-edge,
   passionate about, excited to, thrilled, robust, spearhead
6. Vary sentence length. Include 1 short punchy sentence. Some medium. Max 1 long.
7. Sound like a smart, direct person — not a template
8. Length: 3-5 sentences for short answer fields; 2-3 paragraphs max for longer ones
9. Be honest and grounded — if Rounak would need to learn something, say he's keen to
10. Reference ONE specific detail about the company (from the research above)

Output ONLY the answer text, nothing else."""

    response = client.models.generate_content(model=MODEL, contents=prompt)
    return response.text.strip()


def _handle_logistical(question: str, company_context: str = "") -> str:
    """Return pre-set answers for factual/logistical questions."""
    q_lower = question.lower()
    if "salary" in q_lower or "compensation" in q_lower or "pay" in q_lower:
        return "£45,000–£65,000 depending on the role and benefits package."
    if "notice" in q_lower:
        return "Immediately available."
    if "when" in q_lower and ("start" in q_lower or "available" in q_lower):
        return "I am immediately available to start."
    if "earliest" in q_lower:
        return "Immediately available."
    if "right to work" in q_lower or "work authoris" in q_lower or "work authoriz" in q_lower:
        return "Yes, I have the right to work in the UK."
    if "sponsorship" in q_lower or "visa" in q_lower or "sponsor" in q_lower:
        # If company is known to sponsor visas, be transparent; otherwise say not required
        ctx_lower = (company_context or "").lower()
        sponsors = any(w in ctx_lower for w in ["visa sponsor", "sponsorship available",
                                                  "tier 2", "skilled worker sponsor",
                                                  "we sponsor"])
        if sponsors:
            return ("I am currently on a Graduate visa with the right to work in the UK "
                    "until January 2028. I would require Skilled Worker sponsorship "
                    "to continue beyond that date.")
        return "I do not require visa sponsorship at this time."
    if "additional" in q_lower or "anything else" in q_lower or "other" in q_lower:
        return ""  # Leave blank — don't flag
    if "cover letter" in q_lower:
        return ""  # Handled separately
    return ""


def answer_questions(
    job_id: int,
    questions: list[str],
    company_context: str = "",
) -> list[dict]:
    """
    Generate answers for all custom questions for a given job.
    Flags the job for human review — does NOT auto-submit.

    Returns list of {question, answer, reviewed} dicts.
    """
    if not questions:
        return []

    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    job  = cur.fetchone()
    conn.close()

    if not job:
        print(f"[qa_engine] Job {job_id} not found.")
        return []

    client = get_gemini_client()
    voice  = load_voice()

    qa_list = []
    for q in questions:
        print(f"[qa_engine] Answering: {q[:80]}...")
        answer = _answer_single(
            question=q,
            job_title=job["title"],
            company=job["company"],
            job_description=job["description"] or "",
            company_context=company_context,
            voice=voice,
            client=client,
        )
        qa_list.append({
            "question": q,
            "answer":   answer,
            "reviewed": False,  # must be reviewed before submission
        })
        print(f"[qa_engine] → {answer[:100]}...")

    # Flag for human review — NEVER auto-submit
    mark_custom_q_review(job_id, qa_list)
    print(f"\n[qa_engine] Job {job_id} flagged for REVIEW. Open dashboard to approve answers.")
    return qa_list


def print_review_queue():
    """Print all jobs pending custom Q&A review."""
    jobs = get_jobs(status="custom_q_review")
    if not jobs:
        print("[qa_engine] No jobs pending review.")
        return

    print(f"\n{'='*60}")
    print(f"  CUSTOM Q&A REVIEW QUEUE ({len(jobs)} jobs)")
    print(f"{'='*60}")

    for job in jobs:
        print(f"\n[Job {job['id']}] {job['title']} @ {job['company']}")
        print(f"  URL: {job['url']}")
        if job["custom_qa"]:
            try:
                qa_list = json.loads(job["custom_qa"])
                for i, qa in enumerate(qa_list, 1):
                    print(f"\n  Q{i}: {qa['question']}")
                    print(f"  A{i}: {qa['answer']}")
                    print(f"  Reviewed: {'Yes' if qa.get('reviewed') else 'NO — needs review'}")
            except json.JSONDecodeError:
                print("  [error parsing Q&A]")


if __name__ == "__main__":
    print_review_queue()
