"""
apply_engine.py — Automated job application submission engine.

Tier 1: LinkedIn Easy Apply (Playwright + human-behaviour simulation)
Tier 2: Greenhouse / Lever direct apply (Playwright form fill)
Tier 3: Skip — iCIMS, Workday, Cloudflare-protected -> manual queue

IMPORTANT:
- LinkedIn cap: 50 Easy Apply per day (account safety)
- Custom questions always flagged for review — never auto-submitted
- Dry-run mode available: simulates without submitting

Usage:
    from src.apply_engine import run_applications
    run_applications(limit=50, dry_run=False)
"""

import os
import re
import time
import random
import asyncio
from pathlib import Path
from dotenv import load_dotenv

from src.tracker import (
    get_conn, update_job, mark_applied,
    get_jobs, get_stats
)
from src.resume_tailor import tailor_resume
from src.cover_letter import generate_cover_letter
from src.company_research import research_company, find_direct_apply_url
from src.discovery import resolve_redirect_url, detect_ats as _detect_ats
from src.qa_engine import (
    is_logistical, _handle_logistical, _answer_single,
    detect_custom_questions, answer_questions,
)
from src.utils import get_gemini_client, load_voice, SUCCESS_SIGNALS

load_dotenv()

LINKEDIN_DAILY_CAP = 50
ATS_DAILY_CAP      = 100   # safety cap for Greenhouse/Lever/Ashby per day
ASSETS_DIR = Path(__file__).parent.parent / "assets"

# ── Human-behaviour helpers ───────────────────────────────────────────────────

def _jitter(lo: float = 0.3, hi: float = 2.5) -> None:
    """Sleep for a random duration to simulate human timing."""
    time.sleep(random.uniform(lo, hi))


async def _human_type(page, selector: str, text: str):
    """Type text with realistic per-character delays."""
    await page.click(selector)
    for char in text:
        await page.keyboard.type(char)
        await asyncio.sleep(random.uniform(0.04, 0.18))


async def _safe_click(page, selector: str):
    """Click with a small random offset to avoid bot-perfect centre clicks."""
    try:
        element = await page.query_selector(selector)
        if element:
            box = await element.bounding_box()
            if box:
                x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
                y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
                await page.mouse.move(x, y)
                await asyncio.sleep(random.uniform(0.1, 0.4))
                await page.mouse.click(x, y)
                return
        await page.click(selector)
    except Exception:
        pass


# ── LinkedIn Easy Apply ───────────────────────────────────────────────────────

async def apply_linkedin_easy_apply(
    page,
    job: dict,
    resume_path: str,
    cover_letter: str,
    dry_run: bool = False,
) -> bool:
    """
    Navigate to a LinkedIn job URL and submit Easy Apply.
    Returns True if successfully applied (or dry_run succeeded).
    """
    from playwright.async_api import TimeoutError as PWTimeout

    url = job["url"]
    job_id = job["id"]

    print(f"  [linkedin] Navigating to job {job_id}...")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(1.5, 3.0))

        # Look for Easy Apply button
        easy_apply_btn = await page.query_selector(
            "button.jobs-apply-button, button[aria-label*='Easy Apply'], "
            "button[data-control-name='jobdetails_topcard_inapply']"
        )

        if not easy_apply_btn:
            # Try to extract "Apply on company website" URL and update the job
            try:
                ext_btn = await page.query_selector(
                    "button:has-text('Apply on company website'), "
                    "a:has-text('Apply on company website'), "
                    "a:has-text('Apply now'), "
                    ".jobs-apply-button--top-card"
                )
                if ext_btn:
                    href = await ext_btn.get_attribute("href")
                    if not href:
                        # Some LI buttons navigate programmatically — click and capture
                        async with page.expect_popup(timeout=5000) as popup_info:
                            await ext_btn.click()
                        popup = await popup_info.value
                        href = popup.url
                        await popup.close()
                    if href and href.startswith("http"):
                        new_ats, new_diff = _detect_ats(href)
                        print(f"  [linkedin] Company website found ({new_ats}): {href}")
                        update_job(job_id, url=href, ats_type=new_ats,
                                   difficulty_tier=new_diff,
                                   notes=f"LinkedIn → {new_ats}")
                        job["url"] = href
                        job["ats_type"] = new_ats
                        job["difficulty_tier"] = new_diff
                        if new_ats in ("greenhouse", "lever", "ashby", "bamboohr") and new_diff < 3:
                            return await apply_greenhouse_lever(
                                page, job, resume_path, cover_letter, company_context="", dry_run=dry_run
                            )
            except Exception as e:
                print(f"  [linkedin] Company website extraction failed: {e}")
            print(f"  [linkedin] No Easy Apply button found for job {job_id}")
            update_job(job_id, status="skipped", notes="No Easy Apply button")
            return False

        if dry_run:
            print(f"  [linkedin] [DRY RUN] Would click Easy Apply for: {job['title']} @ {job['company']}")
            return True

        await _safe_click(page, "button.jobs-apply-button, button[aria-label*='Easy Apply']")
        await asyncio.sleep(random.uniform(1.0, 2.0))

        # Handle multi-step modal
        max_steps = 8
        for step in range(max_steps):
            # Check for custom questions in this step
            form_labels = await page.eval_on_selector_all(
                "label, legend, h3, .jobs-easy-apply-form-section__grouping",
                "els => els.map(e => e.innerText.trim())"
            )
            custom_qs = detect_custom_questions(form_labels)

            if custom_qs:
                print(f"  [linkedin] Custom questions detected — flagging for review")
                context = research_company(job["company"])
                answer_questions(job_id, custom_qs, company_context=context)
                # Close modal and stop — mark for review
                await page.keyboard.press("Escape")
                return False  # Will be in custom_q_review status

            # Upload resume if file input present
            file_inputs = await page.query_selector_all("input[type='file']")
            if file_inputs and resume_path and Path(resume_path).exists():
                await file_inputs[0].set_input_files(resume_path)
                await asyncio.sleep(random.uniform(0.5, 1.5))

            # Fill cover letter textarea if present
            cover_textarea = await page.query_selector(
                "textarea[id*='cover'], textarea[aria-label*='cover'], "
                "div[data-test*='cover-letter'] textarea"
            )
            if cover_textarea and cover_letter:
                await cover_textarea.scroll_into_view_if_needed()
                await cover_textarea.fill(cover_letter[:1500])
                await asyncio.sleep(random.uniform(0.5, 1.0))

            # Look for Next / Review / Submit button
            next_btn = await page.query_selector(
                "button[aria-label='Continue to next step'], "
                "button[aria-label*='Review'], "
                "button[aria-label*='Submit application']"
            )

            if not next_btn:
                print(f"  [linkedin] No navigation button found at step {step+1}")
                break

            btn_text = await next_btn.inner_text()

            if "Submit" in btn_text:
                await _safe_click(page, f"button[aria-label*='Submit application']")
                await asyncio.sleep(random.uniform(2.0, 4.0))
                print(f"  [linkedin] Applied: {job['title']} @ {job['company']}")
                mark_applied(job_id, resume_path, cover_letter)
                return True
            else:
                await _safe_click(
                    page,
                    "button[aria-label='Continue to next step'], button[aria-label*='Review']"
                )
                await asyncio.sleep(random.uniform(1.0, 2.5))

        # If we exit the loop without submitting
        await page.keyboard.press("Escape")
        update_job(job_id, status="skipped", notes="Could not complete Easy Apply flow")
        return False

    except PWTimeout:
        print(f"  [linkedin] Timeout on job {job_id}")
        update_job(job_id, status="skipped", notes="Playwright timeout")
        return False
    except Exception as e:
        print(f"  [linkedin] Error on job {job_id}: {e}")
        update_job(job_id, status="skipped", notes=f"Error: {str(e)[:100]}")
        return False


# ── Screenshot helper ─────────────────────────────────────────────────────────

SCREENSHOTS_DIR = Path(__file__).parent.parent / "output" / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

async def _screenshot(page, job_id: int, label: str) -> str:
    path = str(SCREENSHOTS_DIR / f"{job_id}_{label}.png")
    try:
        await page.screenshot(path=path, full_page=True)
    except Exception:
        pass
    return path


# ── Generic field filler ──────────────────────────────────────────────────────

async def _fill_field(page, selectors: list[str], value: str):
    """Try a list of selectors in order, fill the first one found."""
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.scroll_into_view_if_needed()
                await el.click()
                await el.fill("")
                await el.type(value, delay=random.randint(40, 120))
                return True
        except Exception:
            continue
    return False


async def _select_option(page, selectors: list[str], keywords: list[str]) -> bool:
    """
    Find a <select> element and pick the option whose text best matches one of keywords.
    keywords are tried in order — first match wins.
    """
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if not el or not await el.is_visible():
                continue
            # Get all option texts
            options = await el.eval_on_selector_all(
                "option",
                "opts => opts.map(o => ({value: o.value, text: o.innerText.trim().toLowerCase()}))"
            ) if hasattr(el, 'eval_on_selector_all') else []
            # Fallback: use page.eval_on_selector
            if not options:
                options = await page.eval_on_selector(
                    sel,
                    "el => Array.from(el.options).map(o => ({value: o.value, text: o.text.trim().toLowerCase()}))"
                )
            for kw in keywords:
                kw_lower = kw.lower()
                for opt in options:
                    if kw_lower in opt["text"] and opt["value"] not in ("", "--"):
                        await el.scroll_into_view_if_needed()
                        await el.select_option(value=opt["value"])
                        await asyncio.sleep(random.uniform(0.2, 0.5))
                        return True
        except Exception:
            continue
    return False


async def _dismiss_autocomplete(page, field_el):
    """Press Escape or Tab to close any autocomplete dropdown after typing."""
    try:
        await asyncio.sleep(0.4)
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.2)
    except Exception:
        pass


async def _select_uk_phone_country(page) -> bool:
    """
    Find a country-code dropdown near the phone field and select United Kingdom.
    Returns True if UK was selected (caller should use local 07... format).
    Handles both standard <select> and intl-tel-input custom flag dropdowns.
    """
    # 1. Standard <select> country code
    for sel in [
        "select[name*='country_code']", "select[id*='country_code']",
        "select[name*='phone_country']", "select[id*='phone_country']",
        "select[class*='country']", "select[aria-label*='country']",
        "select[aria-label*='Country']",
    ]:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                for val in ["GB", "gb", "UK", "uk", "+44", "44"]:
                    try:
                        await el.select_option(value=val)
                        return True
                    except Exception:
                        pass
                try:
                    await el.select_option(label="United Kingdom")
                    return True
                except Exception:
                    pass
        except Exception:
            continue

    # 2. intl-tel-input / flag-based custom dropdowns
    for flag_sel in [
        ".iti__flag-container", ".iti__selected-flag",
        "[class*='flag-container']", "[class*='phone-flag']",
        "[class*='country-selector']", "[class*='dial-code']",
    ]:
        try:
            flag_btn = await page.query_selector(flag_sel)
            if flag_btn and await flag_btn.is_visible():
                await flag_btn.click()
                await asyncio.sleep(0.6)
                # Search for UK in dropdown list
                for uk_sel in [
                    "[data-country-code='gb']",
                    "li[data-dial-code='44']",
                    ".iti__country[data-country-code='gb']",
                    "li:has-text('United Kingdom')",
                    "[class*='country']:has-text('United Kingdom')",
                ]:
                    uk_opt = await page.query_selector(uk_sel)
                    if uk_opt:
                        await uk_opt.click()
                        await asyncio.sleep(0.3)
                        return True
                # If search box available, type UK
                search = await page.query_selector(
                    ".iti__search-input, input[placeholder*='Search']"
                )
                if search:
                    await search.type("United Kingdom", delay=80)
                    await asyncio.sleep(0.4)
                    uk_opt = await page.query_selector(
                        ".iti__country[data-country-code='gb'], li:has-text('United Kingdom')"
                    )
                    if uk_opt:
                        await uk_opt.click()
                        await asyncio.sleep(0.3)
                        return True
                # Couldn't pick UK — close dropdown
                await page.keyboard.press("Escape")
        except Exception:
            continue

    # 3. Check if parent HTML already shows +44 / UK
    try:
        parent_html = await page.eval_on_selector(
            "input[type='tel'], #phone, input[name*='phone']",
            "el => el.closest('div')?.innerHTML || ''"
        )
        if "+44" in parent_html or "united kingdom" in parent_html.lower():
            return True
    except Exception:
        pass

    return False


async def _fill_all_form_questions(page, job: dict, company_context: str) -> bool:
    """
    Scan the page for ALL unfilled text/textarea fields associated with a label
    and fill them — logistical fields get preset answers, open-ended fields get
    Gemini-generated answers. Nothing is flagged for review.
    """

    try:
        client = get_gemini_client()
    except EnvironmentError:
        return
    voice = load_voice()

    seen_questions: set[str] = set()
    review_questions: list[str] = []
    auto_submit_custom = os.getenv("AUTO_SUBMIT_CUSTOM_QUESTIONS", "").lower() == "true"

    # Fields that should stay blank (between roles)
    SKIP_FIELDS = {"current company", "current employer", "company", "organization", "employer"}

    async def _answer_and_fill(question: str, field_el):
        q_clean = question.strip()
        if not q_clean or q_clean.lower() in seen_questions:
            return
        # Leave current company empty
        if any(skip in q_clean.lower() for skip in SKIP_FIELDS):
            return
        seen_questions.add(q_clean.lower())

        try:
            current_val = await field_el.input_value()
            if current_val and len(current_val.strip()) > 5:
                return  # already filled

            if is_logistical(q_clean):
                answer = _handle_logistical(q_clean, company_context=company_context)
            elif detect_custom_questions([q_clean]) and not auto_submit_custom:
                review_questions.append(q_clean)
                return
            elif not auto_submit_custom:
                return
            else:
                answer = _answer_single(
                    question=q_clean,
                    job_title=job["title"],
                    company=job["company"],
                    job_description=(job.get("description") or "")[:2000],
                    company_context=company_context,
                    voice=voice,
                    client=client,
                )

            if not answer:
                return

            await field_el.scroll_into_view_if_needed()
            await field_el.fill(answer)
            await asyncio.sleep(random.uniform(0.4, 1.2))
            print(f"  [form] Filled: {q_clean[:70]}")
        except Exception as e:
            print(f"  [form] Skip: {q_clean[:50]} — {e}")

    # ── Strategy 1a: label[for] → linked input/textarea ──────────────────────
    try:
        labels = await page.query_selector_all("label")
        for lbl in labels:
            try:
                lbl_text = (await lbl.inner_text()).strip()
                if not lbl_text or len(lbl_text) < 4:
                    continue
                lbl_for = await lbl.get_attribute("for")
                if not lbl_for:
                    continue
                for tag in ["textarea", "input[type='text']", "input:not([type])"]:
                    field = await page.query_selector(f"{tag}#{lbl_for}")
                    if field and await field.is_visible():
                        await _answer_and_fill(lbl_text, field)
                        break
            except Exception:
                continue
    except Exception:
        pass

    if review_questions:
        print(f"  [form] Custom questions detected - queued for review ({len(review_questions)})")
        answer_questions(job["id"], review_questions, company_context=company_context)
        return False

    return True

    # ── Strategy 1b: aria-labelledby (Ashby React forms) ─────────────────────
    try:
        aria_fields = await page.query_selector_all(
            "input[aria-labelledby], textarea[aria-labelledby]"
        )
        for field in aria_fields:
            try:
                if not await field.is_visible():
                    continue
                aria_id = await field.get_attribute("aria-labelledby")
                if not aria_id:
                    continue
                lbl = await page.query_selector(f"#{aria_id}")
                if not lbl:
                    continue
                lbl_text = (await lbl.inner_text()).strip()
                if lbl_text and len(lbl_text) >= 4:
                    await _answer_and_fill(lbl_text, field)
            except Exception:
                continue
    except Exception:
        pass

    # ── Strategy 2: container div has label sibling + textarea/input ──────────
    for container_sel in [
        "[class*='question']", "[class*='form-row']",
        "[class*='form-field']", "[class*='field-block']",
        "[data-field-type]",
    ]:
        try:
            containers = await page.query_selector_all(container_sel)
            for c in containers:
                try:
                    q_el = await c.query_selector("label, p, span[class*='label'], h3, h4")
                    field = await c.query_selector(
                        "textarea, input[type='text'], input:not([type])"
                    )
                    if not q_el or not field:
                        continue
                    if not await field.is_visible():
                        continue
                    q_text = (await q_el.inner_text()).strip()
                    if q_text:
                        await _answer_and_fill(q_text, field)
                except Exception:
                    continue
        except Exception:
            continue

    # ── Strategy 3: standalone textareas with placeholder as question ─────────
    try:
        textareas = await page.query_selector_all("textarea")
        for ta in textareas:
            try:
                if not await ta.is_visible():
                    continue
                placeholder = await ta.get_attribute("placeholder") or ""
                aria_label  = await ta.get_attribute("aria-label") or ""
                hint = placeholder or aria_label
                if hint and len(hint) > 6:
                    await _answer_and_fill(hint, ta)
            except Exception:
                continue
    except Exception:
        pass


# ── Greenhouse / Lever / Ashby ────────────────────────────────────────────────

async def apply_greenhouse_lever(
    page,
    job: dict,
    resume_path: str,
    cover_letter: str,
    company_context: str = "",
    dry_run: bool = False,
) -> bool:
    """Submit a Greenhouse / Lever / Ashby application via Playwright."""
    from playwright.async_api import TimeoutError as PWTimeout

    url    = job["url"]
    job_id = job["id"]
    ats    = job["ats_type"]
    email  = os.getenv("APPLICANT_EMAIL", "")
    raw_phone   = os.getenv("APPLICANT_PHONE", "")
    phone_intl  = raw_phone
    phone_local = "0" + raw_phone.lstrip("+").lstrip("44") if raw_phone.startswith("+44") else raw_phone

    print(f"  [{ats}] Navigating to job {job_id}...")
    try:
        # Load page — networkidle can hang on SPA pages; use domcontentloaded + manual wait
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            await page.goto(url, wait_until="commit", timeout=20000)
        await asyncio.sleep(random.uniform(3.0, 5.0))  # let JS render

        if dry_run:
            print(f"  [{ats}] [DRY RUN] Would apply to: {job['title']} @ {job['company']}")
            return True

        # ── SmartRecruiters: navigate directly to /position/apply URL ────────
        if ats == "smartrecruiters" and "/position/apply" not in url:
            apply_url = url.rstrip("/") + "/position/apply"
            print(f"  [{ats}] Navigating to application form...")
            await page.goto(apply_url, wait_until="networkidle", timeout=45000)
            await asyncio.sleep(random.uniform(2.0, 3.0))
        else:
            # ── Click "Apply" button on job page if present (Ashby/Greenhouse show
            #    the job description first, form is on a separate step) ───────────
            apply_btn = None
            for sel in [
                "a:has-text('Apply for this job')",
                "a:has-text('Apply for this link')",
                "a:has-text('Apply now')",
                "a:has-text('Apply Now')",
                "button:has-text('Apply for this job')",
                "button:has-text('Apply')",
                "[data-qa='btn-apply']",
                "[data-qa-id='apply-btn']",
                "a.btn-apply",
                "a[href*='application']",
            ]:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        apply_btn = el
                        break
                except Exception:
                    continue

            if apply_btn:
                print(f"  [{ats}] Clicking Apply button...")
                await apply_btn.click()
                await page.wait_for_load_state("networkidle", timeout=15000)
                await asyncio.sleep(random.uniform(1.5, 2.5))

        # ── Applicant profile from env ────────────────────────────────────────
        linkedin_url = os.getenv("APPLICANT_LINKEDIN", "https://www.linkedin.com/in/rounakthakur")
        github_url   = os.getenv("APPLICANT_GITHUB", "https://github.com/rounakthakur")
        portfolio    = os.getenv("APPLICANT_PORTFOLIO", "")
        location     = os.getenv("APPLICANT_LOCATION", "London, UK")

        # ── Fill standard fields (broad selector lists per ATS) ───────────────
        await _fill_field(page, [
            "#first_name", "input[name='firstName']",
            "input[name='first_name']",
            "input[name='job_application[first_name]']",
            "input[placeholder*='First name']", "input[placeholder*='First Name']",
            "input[autocomplete='given-name']", "input[data-field='first_name']",
        ], "Rounak")

        await _fill_field(page, [
            "#last_name", "input[name='lastName']",
            "input[name='last_name']",
            "input[name='job_application[last_name]']",
            "input[placeholder*='Last name']", "input[placeholder*='Last Name']",
            "input[autocomplete='family-name']", "input[data-field='last_name']",
        ], "Thakur")

        await _fill_field(page, [
            "#email", "input[name='email']",
            "input[name='job_application[email]']",
            "input[type='email']",
            "input[placeholder*='Email']", "input[placeholder*='email']",
            "input[autocomplete='email']",
        ], email)

        # Phone: select UK from country-code dropdown first, then fill number
        uk_selected = await _select_uk_phone_country(page)
        use_phone = phone_local if uk_selected else phone_intl

        await _fill_field(page, [
            "#phone", "input[name='phone']",
            "input[name='job_application[phone]']",
            "input[type='tel']",
            "input[placeholder*='Phone']", "input[placeholder*='phone']",
            "input[autocomplete='tel']",
        ], use_phone)

        # Location / city
        await _fill_field(page, [
            "#location", "input[name='location']",
            "input[name='job_application[location]']",
            "input[placeholder*='City']", "input[placeholder*='Location']",
            "input[placeholder*='city']", "input[autocomplete='address-level2']",
        ], location)

        # Current company — leave blank (between roles / MSc graduate)
        await _fill_field(page, [
            "input[name='org']", "input[data-field='org']",
            "input[placeholder*='Current company']",
            "input[placeholder*='current company']",
            "input[placeholder*='Company']",
            "input[aria-label*='Current company']",
        ], "")

        # LinkedIn URL
        await _fill_field(page, [
            "input[placeholder*='LinkedIn']", "input[id*='linkedin']",
            "input[aria-label*='LinkedIn']", "input[name*='linkedin']",
            "input[name='job_application[answers_attributes][0][text_value]']",
        ], linkedin_url)

        # GitHub URL (if field present)
        await _fill_field(page, [
            "input[placeholder*='GitHub']", "input[placeholder*='Github']",
            "input[id*='github']", "input[name*='github']",
            "input[aria-label*='GitHub']",
        ], github_url)

        # Portfolio / website (if field present)
        if portfolio:
            await _fill_field(page, [
                "input[placeholder*='Portfolio']", "input[placeholder*='Website']",
                "input[id*='website']", "input[name*='website']",
                "input[placeholder*='website']", "input[placeholder*='portfolio']",
            ], portfolio)

        await asyncio.sleep(random.uniform(0.5, 1.0))

        # ── Resume upload ─────────────────────────────────────────────────────
        if resume_path and Path(resume_path).exists():
            file_inputs = await page.query_selector_all("input[type='file']")
            if file_inputs:
                await file_inputs[0].set_input_files(resume_path)
                await asyncio.sleep(random.uniform(1.5, 3.0))
                print(f"  [{ats}] Resume uploaded")

        # ── Cover letter ──────────────────────────────────────────────────────
        for sel in ["textarea[name*='cover']", "textarea[id*='cover']",
                    "textarea[placeholder*='cover']", "textarea[placeholder*='Cover']",
                    "div[data-automation*='coverLetter'] textarea"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.fill(cover_letter[:2000])
                    await asyncio.sleep(random.uniform(0.3, 0.8))
                    break
            except Exception:
                continue

        # ── Work authorisation (right to work in UK) ──────────────────────────
        # Select dropdowns first
        await _select_option(page, [
            "select[name*='work_auth']", "select[name*='authorization']",
            "select[name*='authorisation']", "select[id*='work_auth']",
            "select[id*='visa']", "select[name*='visa']",
            "select[aria-label*='work']", "select[aria-label*='authoris']",
        ], ["yes", "right to work", "authorised", "authorized", "uk", "eligible"])

        # Radio buttons for right-to-work — resolve by label text, not by value
        # (many forms use numeric or UUID values, not "yes")
        try:
            work_auth_containers = await page.query_selector_all(
                "fieldset:has-text('right to work'), fieldset:has-text('authoris'), "
                "fieldset:has-text('work in the uk'), fieldset:has-text('eligible to work'), "
                "div[class*='question']:has-text('right to work'), "
                "div[class*='question']:has-text('authoris'), "
                "div[class*='field']:has-text('right to work')"
            )
            for container in work_auth_containers:
                try:
                    radios = await container.query_selector_all("input[type='radio']")
                    for rb in radios:
                        rb_id = await rb.get_attribute("id")
                        lbl_text = ""
                        if rb_id:
                            lbl = await page.query_selector(f"label[for='{rb_id}']")
                            if lbl:
                                lbl_text = (await lbl.inner_text()).lower()
                        if not lbl_text:
                            lbl_text = (await rb.get_attribute("value") or "").lower()
                        if any(w in lbl_text for w in ["yes", "right to work", "eligible", "authoris", "i do", "i am"]):
                            await rb.click()
                            await asyncio.sleep(0.3)
                            break
                except Exception:
                    continue
        except Exception:
            pass

        # ── Salary expectations (if text field present) ───────────────────────
        await _fill_field(page, [
            "input[name*='salary']", "input[id*='salary']",
            "input[placeholder*='salary']", "input[placeholder*='Salary']",
            "input[placeholder*='compensation']",
        ], "45000-65000")

        # Salary select dropdown
        await _select_option(page, [
            "select[name*='salary']", "select[id*='salary']",
        ], ["45", "50", "55", "60", "40-60", "50-70"])

        # ── Notice period ─────────────────────────────────────────────────────
        await _fill_field(page, [
            "input[name*='notice']", "input[id*='notice']",
            "input[placeholder*='notice']", "input[placeholder*='Notice']",
        ], "2-4 weeks")

        await _select_option(page, [
            "select[name*='notice']", "select[id*='notice']",
        ], ["immediate", "2 week", "4 week", "1 month", "less than"])

        # ── "How did you hear about us?" ──────────────────────────────────────
        await _select_option(page, [
            "select[name*='source']", "select[name*='referral']",
            "select[name*='hear']", "select[id*='source']",
            "select[id*='hear']",
            "select[aria-label*='hear']", "select[aria-label*='source']",
        ], ["linkedin", "job board", "online", "internet", "google", "other"])

        await _fill_field(page, [
            "input[name*='source']", "input[id*='source']",
            "input[placeholder*='hear about']", "input[placeholder*='Hear about']",
        ], "LinkedIn")

        # ── EEO / DEI voluntary fields ────────────────────────────────────────
        # Gender
        await _select_option(page, [
            "select[name*='gender']", "select[id*='gender']",
            "select[aria-label*='gender']", "select[aria-label*='Gender']",
        ], ["male", "man", "he/him"])

        # Race / Ethnicity
        await _select_option(page, [
            "select[name*='race']", "select[id*='race']",
            "select[name*='ethnicity']", "select[id*='ethnicity']",
            "select[aria-label*='race']", "select[aria-label*='ethnicity']",
        ], ["asian", "asian indian", "south asian", "indian", "asian or pacific"])

        # Disability status
        await _select_option(page, [
            "select[name*='disab']", "select[id*='disab']",
            "select[aria-label*='disab']",
        ], ["no", "do not have", "i don't", "not", "decline"])

        # Veteran status (US-style forms sometimes appear on UK roles)
        await _select_option(page, [
            "select[name*='veteran']", "select[id*='veteran']",
            "select[aria-label*='veteran']",
        ], ["not", "no", "i am not", "decline"])

        # Pronouns (Ashby / Greenhouse forms)
        await _select_option(page, [
            "select[name*='pronoun']", "select[id*='pronoun']",
            "select[aria-label*='pronoun']", "select[aria-label*='Pronoun']",
            "select[placeholder*='pronoun']",
        ], ["he/him", "he", "male"])

        await asyncio.sleep(random.uniform(0.5, 1.0))

        # ── Auto-fill ALL form questions (logistical + open-ended) ────────────
        # No review queue — Gemini answers everything and we submit
        questions_ok = await _fill_all_form_questions(page, job, company_context=company_context)
        if not questions_ok:
            await _screenshot(page, job_id, "custom_q")
            print(f"  [{ats}] Custom questions need review - not submitting")
            return False

        # ── Screenshot BEFORE submit (so you can verify it looks right) ───────
        screenshot_path = await _screenshot(page, job_id, "before_submit")
        print(f"  [{ats}] Screenshot saved: {screenshot_path}")

        # ── Find and click submit ─────────────────────────────────────────────
        submit_btn = None
        for sel in [
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Submit application')",
            "button:has-text('Submit Application')",
            "button:has-text('Submit')",
            "button:has-text('Apply')",
            "button:has-text('Send application')",
            "button:has-text('Complete application')",
            "button:has-text('Complete Application')",
            "a[data-submits-form]",
            "button[data-submit]",
            "[data-qa='btn-submit']",
        ]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    submit_btn = el
                    break
            except Exception:
                continue

        if not submit_btn:
            await _screenshot(page, job_id, "no_submit_btn")
            update_job(job_id, status="skipped", notes="Submit button not found")
            print(f"  [{ats}] Submit button not found - skipped")
            return False

        # ── Verify we are on an actual application form before submitting ──────
        form_check = await page.query_selector(
            "input[type='email'], input[type='file'], "
            "input[name*='first_name'], input[name*='firstName']"
        )
        if not form_check:
            await _screenshot(page, job_id, "not_a_form")
            update_job(job_id, status="skipped", notes="Not an application form — skip")
            print(f"  [{ats}] No application form detected — skipped")
            return False

        await submit_btn.scroll_into_view_if_needed()
        await asyncio.sleep(random.uniform(0.5, 1.0))
        await submit_btn.click()
        await asyncio.sleep(random.uniform(3.0, 5.0))

        # ── CAPTCHA detection — ask user to solve, wait up to 3 minutes ───────
        captcha_signals = [
            "iframe[src*='hcaptcha']", "iframe[src*='recaptcha']",
            "iframe[src*='captcha']", ".h-captcha", ".g-recaptcha",
            "#captcha", "[class*='captcha']", "[id*='captcha']",
            "iframe[title*='captcha']", "iframe[title*='challenge']",
        ]
        captcha_found = False
        for cs in captcha_signals:
            try:
                el = await page.query_selector(cs)
                if el and await el.is_visible():
                    captcha_found = True
                    break
            except Exception:
                continue

        if captcha_found:
            await _screenshot(page, job_id, "captcha")
            print(f"\n  [{ats}] *** CAPTCHA DETECTED for job {job_id}: {job['title']} @ {job['company']} ***")
            print(f"  [{ats}] Please solve the CAPTCHA in the browser window NOW.")
            print(f"  [{ats}] Waiting up to 3 minutes...")

            # Poll every 5 seconds for up to 3 minutes
            confirmed = False
            for _ in range(36):
                await asyncio.sleep(5)
                try:
                    page_text = (await page.inner_text("body")).lower()
                    success_signals = SUCCESS_SIGNALS
                    if any(s in page_text for s in success_signals):
                        confirmed = True
                        break
                    # Also check if CAPTCHA is gone (user solved it, form re-submitted)
                    still_captcha = False
                    for cs in captcha_signals:
                        el = await page.query_selector(cs)
                        if el and await el.is_visible():
                            still_captcha = True
                            break
                    if not still_captcha:
                        # CAPTCHA solved — wait a bit more for confirmation
                        await asyncio.sleep(4)
                        page_text = (await page.inner_text("body")).lower()
                        if any(s in page_text for s in success_signals):
                            confirmed = True
                        break
                except Exception:
                    break

            await _screenshot(page, job_id, "after_captcha")
            if confirmed:
                print(f"  [{ats}] CONFIRMED applied after CAPTCHA: {job['title']} @ {job['company']}")
                mark_applied(job_id, resume_path, cover_letter)
                return True
            else:
                print(f"  [{ats}] CAPTCHA not solved in time — skipped. Apply manually: {job['url']}")
                update_job(job_id, status="skipped", notes="CAPTCHA — apply manually")
                return False

        # ── Verify success: only mark applied if confirmation text found ───────
        page_text = (await page.inner_text("body")).lower()
        success_signals = SUCCESS_SIGNALS
        confirmed = any(s in page_text for s in success_signals)

        await _screenshot(page, job_id, "after_submit")

        if confirmed:
            print(f"  [{ats}] CONFIRMED applied: {job['title']} @ {job['company']}")
            mark_applied(job_id, resume_path, cover_letter)
            return True
        else:
            await _screenshot(page, job_id, "unconfirmed")
            print(f"  [{ats}] No confirmation detected — NOT marking as applied. Check screenshot.")
            update_job(job_id, status="skipped",
                       notes="Submitted but no confirmation — check manually")
            return False

    except PWTimeout:
        print(f"  [{ats}] Timeout on job {job_id}")
        await _screenshot(page, job_id, "timeout")
        update_job(job_id, status="skipped", notes="Playwright timeout")
        return False
    except Exception as e:
        print(f"  [{ats}] Error: {e}")
        await _screenshot(page, job_id, "error")
        update_job(job_id, status="skipped", notes=f"Error: {str(e)[:100]}")
        return False


# ── Main orchestrator ─────────────────────────────────────────────────────────

async def _run_batch(jobs: list, dry_run: bool = False):
    """Run a batch of applications using Playwright."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("[apply_engine] Playwright not installed. Run: pip install playwright && playwright install chromium")
        return

    linkedin_cookie = os.getenv("LINKEDIN_SESSION_COOKIE", "")
    applied_today = 0
    linkedin_today = 0
    ats_today = 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )

        # Inject LinkedIn session cookie
        if linkedin_cookie:
            await context.add_cookies([{
                "name":   "li_at",
                "value":  linkedin_cookie,
                "domain": ".linkedin.com",
                "path":   "/",
            }])

        page = await context.new_page()
        # Mask automation signals
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        for job in jobs:
            job = dict(job)
            job_id   = job["id"]
            ats_type = job.get("ats_type", "unknown")

            # Skip hard-to-automate ATS
            if job.get("difficulty_tier", 2) >= 3:
                update_job(job_id, status="skipped", notes="Manual queue: hard ATS")
                print(f"  [skip] {job['title']} @ {job['company']} -> manual queue ({ats_type})")
                continue

            # ── URL resolution: for unknown-ATS jobs (mostly Adzuna redirects),
            #    follow the redirect to get the real apply URL and detect ATS.
            #    For LinkedIn jobs, use SerpAPI xref to find direct company posting.
            if ats_type == "unknown":
                real_url = resolve_redirect_url(job["url"])
                new_ats, new_difficulty = _detect_ats(real_url)
                if new_ats != "unknown":
                    print(f"  [redirect] Resolved to {new_ats}: {real_url}")
                    update_job(job_id, url=real_url, ats_type=new_ats,
                               difficulty_tier=new_difficulty,
                               notes=f"Resolved redirect -> {new_ats}")
                    job["url"]             = real_url
                    job["ats_type"]        = new_ats
                    job["difficulty_tier"] = new_difficulty
                    ats_type = new_ats
                    # Re-check difficulty after resolution
                    if new_difficulty >= 3:
                        update_job(job_id, status="skipped", notes=f"Hard ATS after redirect: {new_ats}")
                        print(f"  [skip] {job['title']} -> hard ATS ({new_ats}), manual queue")
                        continue
                else:
                    # Redirect didn't reveal ATS — try SerpAPI as last resort
                    direct_url = find_direct_apply_url(job["company"], job["title"])
                    if direct_url:
                        new_ats2, new_diff2 = _detect_ats(direct_url)
                        if new_ats2 in ("greenhouse", "lever", "ashby", "smartrecruiters", "bamboohr"):
                            print(f"  [xref] Found direct URL ({new_ats2}): {direct_url}")
                            update_job(job_id, url=direct_url, ats_type=new_ats2,
                                       difficulty_tier=new_diff2,
                                       notes=f"xref -> {new_ats2}")
                            job["url"]             = direct_url
                            job["ats_type"]        = new_ats2
                            job["difficulty_tier"] = new_diff2
                            ats_type = new_ats2
                        else:
                            update_job(job_id, status="skipped", notes=f"Unresolvable ATS: {new_ats2}")
                            continue
                    else:
                        update_job(job_id, status="skipped", notes="Could not resolve apply URL")
                        continue

            elif ats_type == "linkedin":
                # For LinkedIn jobs try to find direct company posting first
                direct_url = find_direct_apply_url(job["company"], job["title"])
                if direct_url:
                    new_ats, new_difficulty = _detect_ats(direct_url)
                    if new_ats in ("greenhouse", "lever", "ashby", "smartrecruiters", "bamboohr"):
                        print(f"  [xref] Found direct URL ({new_ats}): {direct_url}")
                        update_job(job_id, url=direct_url, ats_type=new_ats,
                                   difficulty_tier=new_difficulty,
                                   notes=f"LinkedIn -> {new_ats}")
                        job["url"]             = direct_url
                        job["ats_type"]        = new_ats
                        job["difficulty_tier"] = new_difficulty
                        ats_type = new_ats
                    else:
                        print(f"  [xref] No supported direct ATS found - using LinkedIn Easy Apply")
                else:
                    print(f"  [xref] No direct posting found - using LinkedIn Easy Apply")

            # Prepare materials
            print(f"\n[apply] Preparing: {job['title']} @ {job['company']} [{ats_type}]")
            resume_path  = tailor_resume(job_id) or ""
            company_ctx  = research_company(job["company"])
            cover_letter = generate_cover_letter(job_id, company_context=company_ctx) or ""

            success = False

            if ats_type == "linkedin":
                if linkedin_today >= LINKEDIN_DAILY_CAP:
                    print(f"  [linkedin] Daily cap ({LINKEDIN_DAILY_CAP}) reached — stopping LinkedIn.")
                    update_job(job_id, status="pending", notes="LinkedIn cap reached")
                    continue
                success = await apply_linkedin_easy_apply(page, job, resume_path, cover_letter, dry_run)
                if success:
                    linkedin_today += 1

            elif ats_type in ("greenhouse", "lever", "ashby", "smartrecruiters", "bamboohr"):
                if ats_today >= ATS_DAILY_CAP:
                    print(f"  [ats] Daily cap ({ATS_DAILY_CAP}) reached — stopping.")
                    update_job(job_id, status="pending", notes="ATS cap reached")
                    continue
                success = await apply_greenhouse_lever(page, job, resume_path, cover_letter, company_ctx, dry_run)
                if success:
                    ats_today += 1

            else:
                update_job(job_id, status="skipped", notes=f"No engine for ATS: {ats_type}")
                print(f"  [skip] {job['title']} -> no engine for '{ats_type}'")
                continue

            if success:
                applied_today += 1

            # Human-like gap between applications
            _jitter(3.0, 8.0)

            # ── Page recovery: if page is dead, open a fresh one ───────────────
            try:
                await page.evaluate("1 + 1")
            except Exception:
                print("  [browser] Page dead — opening fresh tab")
                try:
                    page = await context.new_page()
                    await page.add_init_script(
                        "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
                    )
                except Exception:
                    print("  [browser] Context dead — stopping session")
                    break

        try:
            await browser.close()
        except Exception:
            pass

    print(f"\n[apply_engine] Session complete: {applied_today} applied, {linkedin_today} via LinkedIn")


def run_applications(limit: int = 50, dry_run: bool = False):
    """Public entry point. Fetches top pending jobs and runs the batch."""
    # Check today's applied count
    stats = get_stats()
    already_applied = stats.get("applied_today", 0)
    remaining = max(0, limit - already_applied)

    if remaining == 0:
        print(f"[apply_engine] Daily limit of {limit} already reached for today.")
        return

    print(f"[apply_engine] Already applied today: {already_applied}. Running up to {remaining} more.")

    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT * FROM jobs
        WHERE status = 'pending'
          AND relevance_score > 0
          AND difficulty_tier < 3
        ORDER BY relevance_score DESC
        LIMIT ?
    """, (remaining,))
    jobs = cur.fetchall()
    conn.close()

    if not jobs:
        print("[apply_engine] No pending jobs to apply to. Run discovery + filter first.")
        return

    print(f"[apply_engine] Applying to {len(jobs)} jobs (dry_run={dry_run})...")
    asyncio.run(_run_batch(jobs, dry_run=dry_run))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_applications(limit=args.limit, dry_run=args.dry_run)
