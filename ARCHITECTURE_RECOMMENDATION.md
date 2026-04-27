# Recommended Direction

The current project tries to be a fully autonomous job applier. That is the least reliable shape for this problem.
Job portals change constantly, block automation, hide final application URLs behind aggregators, and ask custom questions
that can damage your signal if an LLM answers them blindly.

The better product is a supervised job-search operating system:

1. Discover from high-quality sources first.
   Prefer direct company career pages, Greenhouse, Lever, Ashby, Wellfound, Otta, LinkedIn saved searches, and recruiter
   email alerts. Use Adzuna/Reed/Google Jobs as lead generators, not as final apply targets.

2. Normalize every job into one schema.
   Store title, company, location, salary, source URL, direct apply URL, ATS, description, fit score, blockers, resume path,
   cover letter, custom questions, status, and follow-up date.

3. Split the agents by responsibility.
   - sourcing-agent: finds new jobs and resolves direct apply URLs
   - fit-agent: rejects weak matches and explains why
   - materials-agent: selects the best resume variant and drafts the cover letter
   - form-agent: fills standard fields and captures screenshots
   - review-agent: queues anything subjective, uncertain, or legally sensitive
   - follow-up-agent: tracks replies and drafts follow-up emails

4. Use approval gates instead of blind submission.
   Auto-submit only when the form has standard fields, no custom questions, no CAPTCHA, and the final screenshot passes
   validation. Everything else should produce a ready-to-send application pack with a one-click/manual final submit.

5. Optimize for response rate, not volume.
   A useful daily workflow is 15 to 25 high-fit jobs:
   - 5 direct applications to strong company matches
   - 5 recruiter or hiring-manager outreach messages
   - 5 to 15 assisted applications through easier ATS flows
   - follow-ups for applications older than 5 to 7 business days

6. Keep browser automation narrow.
   Do not build one generic Selenium/Playwright monster for every ATS. Build small, tested adapters for Greenhouse,
   Lever, Ashby, and LinkedIn Easy Apply, then queue all other ATS types for manual review.

Repos worth studying rather than copying outright:

- `santifer/career-ops`: strong direction for a Claude Code skill-based job-search system, dashboard, and batch workflow.
- `11844/Auto_Jobs_Applier_AIHawk`: mature auto-apply project with a larger ecosystem, but still inherits the brittleness
  of LinkedIn/browser automation.
- `imon333/Job-apply-AI-agent`: useful n8n/Selenium/OpenAI workflow ideas, especially for external orchestration.

My recommendation for this codebase:

1. Keep the SQLite tracker and Streamlit dashboard.
2. Replace hard-coded searches with prompt-driven search goals.
3. Stop treating aggregator URLs as application targets.
4. Add a direct-URL resolver before scoring.
5. Convert full auto-submit into a guarded mode, not the default mode.
6. Add application packs: resume, cover letter, answers, screenshot, direct URL, and one final user approval step.
