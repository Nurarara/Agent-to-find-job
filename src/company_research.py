"""
company_research.py — Scrape basic company info to feed into the Q&A engine.

Fetches the company's About/Careers page and optionally recent news via SerpAPI.
Returns a concise context string used to personalise cover letters and Q&A answers.

Usage:
    from src.company_research import research_company
    context = research_company("DeepMind", "https://deepmind.google")
"""

import os
import re
import time
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}
REQUEST_TIMEOUT = 10


def _scrape_page(url: str) -> str:
    """Fetch a URL and return visible text (max ~3000 chars)."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove noise
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        text = soup.get_text(separator=" ", strip=True)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text)
        return text[:3000]
    except Exception as e:
        return f"[scrape error: {e}]"


def _get_about_url(base_url: str) -> list[str]:
    """Return candidate About/Mission URLs to try."""
    base = base_url.rstrip("/")
    return [
        f"{base}/about",
        f"{base}/about-us",
        f"{base}/company",
        f"{base}/careers",
        base,
    ]


def _search_recent_news(company_name: str) -> str:
    """Use SerpAPI to find 1-2 recent news items about the company."""
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key:
        return ""

    try:
        resp = requests.get(
            "https://serpapi.com/search",
            params={
                "engine":  "google",
                "q":       f"{company_name} news 2025 OR 2026",
                "tbm":     "nws",
                "num":     3,
                "api_key": api_key,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        snippets = []
        for item in data.get("news_results", [])[:3]:
            snippets.append(f"- {item.get('title', '')}: {item.get('snippet', '')}")
        return "\n".join(snippets)
    except Exception as e:
        return f"[news search error: {e}]"


def research_company(company_name: str, company_url: str = "") -> str:
    """
    Returns a short context string (~500 words max) about the company.
    Used to personalise cover letters and custom Q&A answers.
    """
    sections = [f"## Company: {company_name}"]

    # 1. Try to scrape the About page
    if company_url:
        for url in _get_about_url(company_url):
            text = _scrape_page(url)
            if len(text) > 200 and "scrape error" not in text:
                sections.append(f"### About (from {url}):\n{text[:1500]}")
                break
            time.sleep(0.5)

    # 2. Get recent news
    news = _search_recent_news(company_name)
    if news and "error" not in news:
        sections.append(f"### Recent news:\n{news}")

    context = "\n\n".join(sections)
    return context[:3000]


# ── Direct ATS URL finder ─────────────────────────────────────────────────────

# ATS domains we can automate (ordered by preference)
PREFERRED_ATS_DOMAINS = [
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "smartrecruiters.com",
    "bamboohr.com",
    "workable.com",
]

# One SerpAPI query searches all of them at once
_ATS_SITE_QUERY = " OR ".join(f"site:{d}" for d in PREFERRED_ATS_DOMAINS)


def find_direct_apply_url(company_name: str, job_title: str) -> str | None:
    """
    Search SerpAPI for the same job posted directly on the company's ATS
    (Greenhouse, Lever, Ashby, etc.).

    Returns the direct apply URL if found, otherwise None.

    Example query:
      (site:greenhouse.io OR site:lever.co OR ...) "Monzo" "Data Engineer"
    """
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key:
        return None

    # Strip common noise from job title for a tighter search
    clean_title = re.sub(r"\b(junior|senior|mid|lead|staff|principal|associate)\b",
                         "", job_title, flags=re.IGNORECASE).strip()

    query = f'({_ATS_SITE_QUERY}) "{company_name}" "{clean_title}"'

    try:
        resp = requests.get(
            "https://serpapi.com/search",
            params={
                "engine":  "google",
                "q":       query,
                "num":     5,
                "gl":      "uk",
                "hl":      "en",
                "api_key": api_key,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        for result in data.get("organic_results", []):
            link = result.get("link", "")
            # Verify it's actually an ATS job page, not a blog post
            if any(d in link for d in PREFERRED_ATS_DOMAINS):
                # Quick sanity check: company name in URL or title
                title_text = result.get("title", "").lower()
                snippet     = result.get("snippet", "").lower()
                company_lower = company_name.lower()
                if (company_lower in link.lower()
                        or company_lower in title_text
                        or company_lower in snippet):
                    return link

        # Fallback: return the first ATS result even without company name match
        for result in data.get("organic_results", []):
            link = result.get("link", "")
            if any(d in link for d in PREFERRED_ATS_DOMAINS):
                return link

    except Exception as e:
        print(f"[company_research] find_direct_apply_url error: {e}")

    return None


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "--find":
        # Usage: python -m src.company_research --find "Monzo" "Data Engineer"
        company  = sys.argv[2]
        job_title = sys.argv[3] if len(sys.argv) > 3 else ""
        url = find_direct_apply_url(company, job_title)
        print(f"Direct apply URL: {url or 'Not found'}")
    else:
        name = sys.argv[1] if len(sys.argv) > 1 else "DeepMind"
        url  = sys.argv[2] if len(sys.argv) > 2 else ""
        print(research_company(name, url))
