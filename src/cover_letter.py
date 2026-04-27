"""
cover_letter.py — Generate a tailored cover letter using Claude API.

The letter is short (3 paragraphs), confident, specific to the company,
and sounds like Rounak wrote it himself — not an AI template.

Usage:
    from src.cover_letter import generate_cover_letter
    text = generate_cover_letter(job_id=42)
"""

from dotenv import load_dotenv
from src.tracker import get_conn, update_job
from src.utils import get_gemini_client, load_voice, MODEL

load_dotenv()


def generate_cover_letter(
    job_id: int,
    company_context: str = "",
) -> str | None:
    """
    Generate a 3-paragraph cover letter for the given job_id.
    company_context: optional extra info (from company_research.py).
    Saves to DB and returns the text.
    """
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    job  = cur.fetchone()
    conn.close()

    if not job:
        print(f"[cover_letter] Job {job_id} not found.")
        return None

    title   = job["title"]
    company = job["company"]
    desc    = job["description"] or ""
    voice   = load_voice()

    client = get_gemini_client()

    prompt = f"""You are writing a cover letter for Rounak Thakur.

## Rounak's background and voice:
{voice}

## Target role:
Title: {title}
Company: {company}
Job Description:
{desc[:2500]}

## Extra company context (if available):
{company_context or "None provided."}

## Instructions:
Write a cover letter with exactly 3 short paragraphs. No header, no sign-off line needed.
Just the 3 paragraphs of body text.

Paragraph 1 (3-4 sentences):
- Why this specific role at this specific company — reference something real from the JD or company context
- What Rounak brings that directly matches
- NO generic opener like "I am writing to apply for..."

Paragraph 2 (3-4 sentences):
- One or two concrete examples from Rounak's work history
- Use real numbers and tech names from his background
- Show that his past work directly relates to what this company needs

Paragraph 3 (2-3 sentences):
- Confident close. Express genuine interest without being desperate.
- Offer to discuss further. Keep it short.

Tone rules (strict):
- First person, direct, slightly informal
- DO NOT use: leverage, utilize, passionate, thrilled, excited, innovative, groundbreaking,
  synergy, cutting-edge, robust, spearhead, game-changer
- Vary sentence length — mix short and medium
- Sound like a smart person talking, not a template
- Maximum 250 words total

Output ONLY the 3 paragraphs, no extra text."""

    response = client.models.generate_content(model=MODEL, contents=prompt)
    letter = response.text.strip()

    # Save to DB
    update_job(job_id, cover_letter=letter)
    print(f"[cover_letter] Generated for job {job_id}: {title} @ {company}")
    return letter


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m src.cover_letter <job_id>")
    else:
        result = generate_cover_letter(int(sys.argv[1]))
        if result:
            print("\n--- Cover Letter ---")
            print(result)
