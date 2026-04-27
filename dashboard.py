"""
Streamlit dashboard for finding, tracking, and applying to high-fit data jobs.

Run with:
    streamlit run dashboard.py
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from src.discovery import run_discovery
from src.enrichment import build_outreach_message, find_linkedin_contacts, is_recent_job, persist_enrichment
from src.filter import run_filter
from src.profiles import PROFILES, get_profile
from src.tracker import get_conn, get_stats, init_db, mark_applied, update_job


TARGET_JOB_COUNT = 50
AUTO_REFRESH_HOURS = 6

st.set_page_config(
    page_title="Job Hunt Command Center",
    page_icon=":briefcase:",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_db()


def _read_df(query: str, params: tuple = ()) -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql(query, conn, params=params)
    conn.close()
    return df


def load_jobs(status: str | None = None, limit: int = 500, profile_key: str = "ron") -> pd.DataFrame:
    if status:
        return _read_df(
            "SELECT * FROM jobs WHERE status = ? AND COALESCE(profile, 'ron') = ? ORDER BY relevance_score DESC LIMIT ?",
            (status, profile_key, limit),
        )
    return _read_df(
        "SELECT * FROM jobs WHERE COALESCE(profile, 'ron') = ? ORDER BY date_found DESC LIMIT ?",
        (profile_key, limit),
    )


def load_recent_matches(limit: int = TARGET_JOB_COUNT, profile_key: str = "ron") -> pd.DataFrame:
    df = _read_df(
        """
        SELECT *
        FROM jobs
        WHERE status IN ('pending', 'applied', 'custom_q_review', 'interview')
          AND COALESCE(profile, 'ron') = ?
        ORDER BY interview_probability DESC, relevance_score DESC, date_found DESC
        LIMIT 300
        """,
        (profile_key,),
    )
    if df.empty:
        return df

    if "job_key" in df.columns:
        df = df.drop_duplicates(subset=["job_key"], keep="first")

    recent_mask = df["date_posted"].apply(lambda value: is_recent_job(value, max_age_days=5))
    df = df[recent_mask].copy()
    if df.empty:
        return df

    missing = df["interview_probability"].fillna(0).astype(float) <= 0
    for _, row in df[missing].head(100).iterrows():
        persist_enrichment(int(row["id"]), row.to_dict(), include_contacts=False, profile_key=profile_key)

    if missing.any():
        df = _read_df(
            """
            SELECT *
            FROM jobs
            WHERE status IN ('pending', 'applied', 'custom_q_review', 'interview')
              AND COALESCE(profile, 'ron') = ?
            ORDER BY interview_probability DESC, relevance_score DESC, date_found DESC
            LIMIT 300
            """,
            (profile_key,),
        )
        if "job_key" in df.columns:
            df = df.drop_duplicates(subset=["job_key"], keep="first")
        df = df[df["date_posted"].apply(lambda value: is_recent_job(value, max_age_days=5))].copy()

    return df.head(limit)


def load_qa_queue(profile_key: str = "ron") -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, title, company, url, custom_qa
        FROM jobs
        WHERE status = 'custom_q_review'
          AND COALESCE(profile, 'ron') = ?
        ORDER BY date_found DESC
        """,
        (profile_key,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def last_refresh_at(profile_key: str = "ron") -> datetime | None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT MAX(date_found) AS last_found FROM jobs WHERE COALESCE(profile, 'ron') = ?", (profile_key,))
    row = cur.fetchone()
    conn.close()
    if not row or not row["last_found"]:
        return None
    try:
        return datetime.fromisoformat(row["last_found"])
    except ValueError:
        return None


def refresh_jobs(prompt: str, profile_key: str) -> int:
    new_count = run_discovery(prompt=prompt, profile_key=profile_key)
    run_filter(profile_key=profile_key)
    return new_count


def maybe_auto_refresh(prompt: str, profile_key: str) -> None:
    last = last_refresh_at(profile_key)
    stale = not last or last < datetime.utcnow() - timedelta(hours=AUTO_REFRESH_HOURS)
    refresh_key = f"auto_refreshed_{profile_key}"
    if stale and not st.session_state.get(refresh_key):
        with st.spinner("Refreshing job links from the last 5 days..."):
            refresh_jobs(prompt, profile_key)
        st.session_state[refresh_key] = True


def parse_contacts(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def render_contacts(job_id: int, company: str, title: str, raw_contacts: str | None) -> None:
    contacts = parse_contacts(raw_contacts)
    if st.button("Find LinkedIn contacts", key=f"contacts_{job_id}"):
        with st.spinner("Searching LinkedIn profiles..."):
            contacts = find_linkedin_contacts(company, title)
            update_job(job_id, recruiter_profiles=json.dumps(contacts), last_enriched_at=datetime.utcnow().isoformat())
        st.rerun()

    if contacts:
        st.caption("Suggested recruiter / hiring-manager profiles")
        for contact in contacts:
            url = contact.get("url", "")
            label = contact.get("name") or "LinkedIn profile"
            if url:
                st.markdown(f"- [{label}]({url}) - {contact.get('title', '')}")
            else:
                st.markdown(f"- {label} - {contact.get('title', '')}")


def mark_job_applied(job_id: int) -> None:
    mark_applied(job_id, resume_path="", cover_letter="")


st.sidebar.title("Job Hunt")
st.sidebar.caption(datetime.utcnow().strftime("%a %d %b %Y"))

profile_label = st.sidebar.radio("Candidate", ["Ron", "Heba"], horizontal=True)
profile_key = profile_label.lower()
profile = get_profile(profile_key)

search_prompt = st.sidebar.text_area("Search prompt", profile.search_prompt, height=190, key=f"prompt_{profile_key}")

if st.sidebar.button("Refresh jobs now", type="primary"):
    with st.spinner("Finding fresh roles and scoring matches..."):
        added = refresh_jobs(search_prompt, profile.key)
    st.sidebar.success(f"Refresh complete. {added} new jobs added.")

if st.sidebar.button("Find contacts for top matches"):
    df_contacts = load_recent_matches(TARGET_JOB_COUNT, profile.key)
    top_matches = df_contacts[
        (df_contacts["interview_probability"].fillna(0).astype(float) >= 45)
        & (df_contacts["recruiter_profiles"].fillna("") == "")
    ].head(10)
    with st.spinner("Finding LinkedIn contacts for strongest matches..."):
        for _, row in top_matches.iterrows():
            contacts = find_linkedin_contacts(row.get("company", ""), row.get("title", ""))
            update_job(
                int(row["id"]),
                recruiter_profiles=json.dumps(contacts),
                last_enriched_at=datetime.utcnow().isoformat(),
            )
    st.sidebar.success(f"Updated {len(top_matches)} jobs.")

auto_refresh = st.sidebar.checkbox("Auto-refresh stale results", value=True)
if auto_refresh:
    maybe_auto_refresh(search_prompt, profile.key)

page = st.sidebar.radio(
    "Navigate",
    ["Job Matches", "Tracker", "Q&A Review", "Applied", "All Jobs"],
)

stats = get_stats(profile=profile.key)

if page == "Job Matches":
    st.title("Fresh Job Matches")
    st.caption(f"{profile.label}'s latest roles from the last 5 days. Sorted by estimated interview probability.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Fresh Matches", len(load_recent_matches(profile_key=profile.key)))
    c2.metric("Applied Today", stats.get("applied_today", 0))
    c3.metric("Pending Review", stats.get("pending_review", 0))
    c4.metric("Skipped", stats.get("skipped", 0))

    df = load_recent_matches(TARGET_JOB_COUNT, profile.key)
    if df.empty:
        st.info("No fresh matches yet. Use Refresh jobs now.")
        st.stop()

    col_a, col_b, col_c = st.columns([1.2, 1, 1])
    with col_a:
        role_options = sorted(df["role_family"].dropna().unique()) if "role_family" in df else []
        role_filter = st.multiselect("Role family", role_options)
    with col_b:
        min_probability = st.slider("Minimum interview probability", 0, 75, 15, 5)
    with col_c:
        hide_applied = st.checkbox("Hide applied", value=False)

    filtered = df[df["interview_probability"].fillna(0) >= min_probability]
    if role_filter:
        filtered = filtered[filtered["role_family"].isin(role_filter)]
    if hide_applied:
        filtered = filtered[filtered["status"] != "applied"]

    st.write(f"Showing {len(filtered)} of {len(df)} fresh matches")

    for _, row in filtered.iterrows():
        job_id = int(row["id"])
        probability = float(row.get("interview_probability") or 0)
        status = row.get("status", "pending")
        applied = status == "applied"
        title = row.get("title", "")
        company = row.get("company", "")
        role_family = row.get("role_family") or "Relevant role"

        label = f"{'Applied - ' if applied else ''}{probability:.0f}% | {title} @ {company} | {role_family}"
        with st.expander(label, expanded=False):
            top_cols = st.columns([3, 1, 1])
            with top_cols[0]:
                st.markdown(f"**Role:** {title}")
                st.markdown(f"**Company:** {company}")
                st.markdown(f"**Location:** {row.get('location') or 'Unknown'}")
                st.markdown(f"**Posted:** {row.get('date_posted') or 'Unknown'}")
                st.markdown(f"**Apply link:** [{row.get('url')}]({row.get('url')})")
            with top_cols[1]:
                st.metric("Interview probability", f"{probability:.0f}%")
                st.caption(row.get("probability_reason") or "Needs enrichment")
            with top_cols[2]:
                checked = st.checkbox("Applied", value=applied, key=f"applied_{job_id}")
                if checked and not applied:
                    mark_job_applied(job_id)
                    st.rerun()
                if not checked and applied:
                    update_job(job_id, status="pending", applied_at=None)
                    st.rerun()

                if st.button("Skip", key=f"skip_{job_id}"):
                    update_job(job_id, status="skipped", notes="Manually skipped from dashboard")
                    st.rerun()

            if row.get("description"):
                st.text_area("Description", str(row["description"])[:1500], height=180, key=f"desc_{job_id}")

            render_contacts(job_id, company, title, row.get("recruiter_profiles"))

    st.divider()
    st.subheader("Top 5 recruiter messages")
    outreach_df = filtered[
        (filtered["interview_probability"].fillna(0).astype(float) >= 45)
        & (filtered["status"] != "applied")
    ].head(5)
    if outreach_df.empty:
        st.caption("No strong outreach candidates at the current filters.")
    for _, row in outreach_df.iterrows():
        contacts = parse_contacts(row.get("recruiter_profiles"))
        contact_name = contacts[0].get("name", "") if contacts else ""
        with st.expander(f"{row['company']} - {row['title']}"):
            st.write("Message a recruiter or hiring manager for this one because the fit score is high and the role is fresh.")
            st.text_area(
                "Personalized message",
                build_outreach_message(profile.key, row["company"], row["title"], contact_name),
                height=210,
                key=f"outreach_{profile.key}_{int(row['id'])}",
            )


elif page == "Tracker":
    st.title("Application Tracker")
    df = load_jobs(limit=1000, profile_key=profile.key)
    if df.empty:
        st.info("No jobs in the tracker.")
        st.stop()

    status_filter = st.multiselect("Status", sorted(df["status"].dropna().unique()), default=["pending", "applied"])
    if status_filter:
        df = df[df["status"].isin(status_filter)]

    cols = [
        "id", "title", "company", "role_family", "status", "interview_probability",
        "ats_type", "source", "date_posted", "applied_at", "url",
    ]
    visible_cols = [col for col in cols if col in df.columns]
    st.dataframe(df[visible_cols], use_container_width=True, hide_index=True)


elif page == "Q&A Review":
    st.title("Custom Q&A Review Queue")
    queue = load_qa_queue(profile.key)
    if not queue:
        st.success("No custom questions pending review.")
        st.stop()

    for job in queue:
        st.divider()
        st.subheader(f"{job['title']} @ {job['company']}")
        st.markdown(f"[Apply link]({job['url']})")
        if not job["custom_qa"]:
            continue
        try:
            qa_list = json.loads(job["custom_qa"])
        except json.JSONDecodeError:
            st.error("Could not parse Q&A data.")
            continue

        updated_qa = []
        all_reviewed = True
        for i, qa in enumerate(qa_list):
            st.markdown(f"**Q{i + 1}: {qa['question']}**")
            answer = st.text_area(f"Answer {i + 1}", value=qa.get("answer", ""), height=120, key=f"qa_{job['id']}_{i}")
            reviewed = st.checkbox("Approved", value=qa.get("reviewed", False), key=f"rev_{job['id']}_{i}")
            all_reviewed = all_reviewed and reviewed
            updated_qa.append({"question": qa["question"], "answer": answer, "reviewed": reviewed})

        if st.button(f"Save Q&A - Job {job['id']}", key=f"save_{job['id']}"):
            update_job(job["id"], custom_qa=json.dumps(updated_qa))
            st.success("Saved.")

        if all_reviewed and st.button(f"Move back to pending - Job {job['id']}", key=f"ready_{job['id']}"):
            update_job(job["id"], status="pending", custom_qa=json.dumps(updated_qa), notes="Q&A approved")
            st.rerun()


elif page == "Applied":
    st.title("Applied Jobs")
    df = load_jobs("applied", limit=500, profile_key=profile.key)
    if df.empty:
        st.info("No applications marked applied yet.")
        st.stop()
    st.dataframe(
        df[["id", "title", "company", "role_family", "applied_at", "interview_probability", "url"]],
        use_container_width=True,
        hide_index=True,
    )


elif page == "All Jobs":
    st.title("All Jobs")
    df = load_jobs(limit=1000, profile_key=profile.key)
    if df.empty:
        st.info("No jobs in database.")
        st.stop()
    st.dataframe(df, use_container_width=True, hide_index=True)
