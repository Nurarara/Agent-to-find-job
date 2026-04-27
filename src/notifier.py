"""
notifier.py — Send Gmail confirmation emails after each application session.

Uses Gmail SMTP with an App Password (no OAuth needed).
Setup:
  1. Go to myaccount.google.com/security
  2. Enable 2-Step Verification
  3. Search "App passwords" -> create one for "Mail"
  4. Add to .env:
       GMAIL_ADDRESS=your_email@example.com
       GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx

Usage:
    from src.notifier import send_session_report
    send_session_report(applied_jobs)
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


def send_session_report(applied_jobs: list[dict], skipped_count: int = 0):
    """
    Send a Gmail summary of the current application session.

    applied_jobs: list of dicts with keys:
        title, company, url, ats_type, salary_min, salary_max, applied_at
    """
    gmail_addr = os.getenv("GMAIL_ADDRESS")
    app_password = os.getenv("GMAIL_APP_PASSWORD")

    if not gmail_addr or not app_password:
        print("[notifier] GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set — skipping email.")
        return

    if not applied_jobs:
        print("[notifier] No applications to report.")
        return

    now = datetime.utcnow().strftime("%d %b %Y %H:%M UTC")
    subject = f"Job Hunt: {len(applied_jobs)} applications sent — {datetime.utcnow().strftime('%d %b %Y')}"

    # ── Build HTML table ──────────────────────────────────────────────────────
    rows = ""
    for j in applied_jobs:
        salary = ""
        if j.get("salary_min") and j.get("salary_max"):
            salary = f"£{int(j['salary_min']):,} – £{int(j['salary_max']):,}"
        elif j.get("salary_min"):
            salary = f"£{int(j['salary_min']):,}+"
        elif j.get("salary_max"):
            salary = f"up to £{int(j['salary_max']):,}"
        else:
            salary = "Not listed"

        ats = j.get("ats_type", "unknown").capitalize()
        time_applied = j.get("applied_at", "")[:16].replace("T", " ") if j.get("applied_at") else now

        rows += f"""
        <tr>
            <td style="padding:8px;border-bottom:1px solid #eee;">
                <a href="{j.get('url','')}" style="color:#0066cc;text-decoration:none;">
                    {j.get('title','')}</a>
            </td>
            <td style="padding:8px;border-bottom:1px solid #eee;">{j.get('company','')}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;">{salary}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;">{ats}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;">{time_applied}</td>
        </tr>"""

    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;max-width:900px;margin:auto;">
    <h2 style="color:#1a1a2e;">Job Applications — {datetime.utcnow().strftime('%d %b %Y')}</h2>
    <p>
        <strong>{len(applied_jobs)}</strong> applications submitted &nbsp;|&nbsp;
        <strong>{skipped_count}</strong> skipped (hard ATS / no URL) &nbsp;|&nbsp;
        Sent at {now}
    </p>
    <table style="width:100%;border-collapse:collapse;font-size:14px;">
        <thead>
            <tr style="background:#1a1a2e;color:white;">
                <th style="padding:10px;text-align:left;">Role</th>
                <th style="padding:10px;text-align:left;">Company</th>
                <th style="padding:10px;text-align:left;">Salary</th>
                <th style="padding:10px;text-align:left;">Applied via</th>
                <th style="padding:10px;text-align:left;">Time</th>
            </tr>
        </thead>
        <tbody>{rows}</tbody>
    </table>
    <p style="margin-top:20px;font-size:12px;color:#888;">
        Automated by your Job Hunt Agent &nbsp;·&nbsp; Run daily to keep applying.
    </p>
    </body></html>
    """

    # ── Send ──────────────────────────────────────────────────────────────────
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = gmail_addr
    msg["To"]      = gmail_addr
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_addr, app_password)
            server.sendmail(gmail_addr, gmail_addr, msg.as_string())
        print(f"[notifier] Email sent to {gmail_addr} — {len(applied_jobs)} applications reported.")
    except Exception as e:
        print(f"[notifier] Failed to send email: {e}")


if __name__ == "__main__":
    # Test with dummy data
    test_jobs = [
        {
            "title": "Data Engineer",
            "company": "Test Company",
            "url": "https://example.com",
            "ats_type": "greenhouse",
            "salary_min": 50000,
            "salary_max": 70000,
            "applied_at": datetime.utcnow().isoformat(),
        }
    ]
    send_session_report(test_jobs, skipped_count=5)
