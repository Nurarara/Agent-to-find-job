"""
main.py — CLI orchestrator for the job application automation system.

Commands:
    python main.py --discover           Fetch new jobs from all APIs
    python main.py --filter             Score + filter today's jobs
    python main.py --apply              Apply to top-scored pending jobs
    python main.py --apply --dry-run    Simulate without submitting
    python main.py --review             Print custom Q&A review queue
    python main.py --stats              Print today's application stats
    python main.py --run-all            Run full pipeline: discover → filter → apply
    python main.py --init               Initialise DB (first run)
"""

import argparse
import sys
import io
from datetime import datetime

# Force UTF-8 output on Windows so job titles with special chars don't crash
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def cmd_init():
    from src.tracker import init_db
    init_db()
    print("[main] Database initialised.")


def cmd_discover(prompt: str | None = None, profile: str = "ron"):
    from src.discovery import run_discovery
    n = run_discovery(prompt=prompt, profile_key=profile)
    print(f"[main] Discovery complete. {n} new jobs added.")


def cmd_filter(profile: str = "ron"):
    from src.filter import run_filter, top_jobs
    summary = run_filter(profile_key=profile)
    print(f"\n[main] Top jobs after filter:")
    for j in top_jobs(15, profile_key=profile):
        tier_label = {1: "easy", 2: "medium", 3: "skip"}.get(j["difficulty_tier"], "?")
        print(
            f"  [{j['relevance_score']:4.1f}] "
            f"{j['title'][:40]:<40} @ {j['company'][:25]:<25} "
            f"[{j['ats_type']}/{tier_label}]"
        )


def cmd_apply(limit: int, dry_run: bool):
    from src.apply_engine import run_applications
    run_applications(limit=limit, dry_run=dry_run)


def cmd_review():
    from src.qa_engine import print_review_queue
    print_review_queue()


def cmd_stats():
    from src.tracker import get_stats, get_jobs
    stats = get_stats()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    print(f"\n{'='*50}")
    print(f"  APPLICATION STATS — {today}")
    print(f"{'='*50}")
    print(f"  Jobs found today:      {stats.get('found_today', 0)}")
    print(f"  Applied today:         {stats.get('applied_today', 0)}")
    print(f"  Pending review (Q&A):  {stats.get('pending_review', 0)}")
    print(f"  Interviews:            {stats.get('interviews', 0)}")
    print(f"  Skipped:               {stats.get('skipped', 0)}")
    print(f"{'='*50}")

    # Show pending jobs count
    pending = get_jobs(status="pending", limit=1000)
    print(f"  Pending applications:  {len(pending)}")
    print()


def cmd_run_all(limit: int, dry_run: bool, prompt: str | None = None, profile: str = "ron"):
    print("[main] === FULL PIPELINE ===")
    print("\n[main] Step 1/3: Discover jobs...")
    cmd_discover(prompt=prompt, profile=profile)

    print("\n[main] Step 2/3: Filter and score...")
    cmd_filter(profile=profile)

    print(f"\n[main] Step 3/3: Apply (limit={limit}, dry_run={dry_run})...")
    cmd_apply(limit=limit, dry_run=dry_run)

    print("\n[main] Pipeline complete.")
    cmd_stats()


def main():
    parser = argparse.ArgumentParser(
        description="Job Application Automation — London",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--init",     action="store_true", help="Initialise database")
    parser.add_argument("--discover", action="store_true", help="Fetch new jobs")
    parser.add_argument("--filter",   action="store_true", help="Score + filter jobs")
    parser.add_argument("--apply",    action="store_true", help="Apply to jobs")
    parser.add_argument("--review",   action="store_true", help="Show Q&A review queue")
    parser.add_argument("--stats",    action="store_true", help="Show today's stats")
    parser.add_argument("--run-all",  action="store_true", help="Full pipeline")
    parser.add_argument("--dry-run",  action="store_true", help="Simulate without submitting")
    parser.add_argument("--limit",    type=int, default=50, help="Max applications per session")
    parser.add_argument("--prompt",   help="Natural-language job-search goal")
    parser.add_argument("--profile",  choices=["ron", "heba"], default="ron", help="Candidate profile")

    args = parser.parse_args()

    action_args = {k: v for k, v in vars(args).items() if k not in ("prompt", "profile")}
    if not any(action_args.values()):
        parser.print_help()
        return

    if args.init:
        cmd_init()
    if args.discover:
        cmd_discover(prompt=args.prompt, profile=args.profile)
    if args.filter:
        cmd_filter(profile=args.profile)
    if args.apply:
        cmd_apply(limit=args.limit, dry_run=args.dry_run)
    if args.review:
        cmd_review()
    if args.stats:
        cmd_stats()
    if args.run_all:
        cmd_run_all(limit=args.limit, dry_run=args.dry_run, prompt=args.prompt, profile=args.profile)


if __name__ == "__main__":
    main()
