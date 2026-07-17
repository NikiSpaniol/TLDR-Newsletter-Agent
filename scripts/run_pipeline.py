"""
Phase 2 - Orchestrator: runs the full pipeline for one scheduled run,
both newsletters, end to end.

Pull emails (Gmail) -> extract -> pool/dedupe -> fetch (Jina) ->
summarize (Anthropic API, map) -> write_episode (Anthropic API, reduce).

Usage: python3 scripts/run_pipeline.py [YYYY-MM-DD]
Defaults to today. The run date must be a Tuesday or a Friday, per the
brief's schedule (section 3) -- a Tuesday run processes the prior
Friday + Monday editions; a Friday run processes that week's
Tuesday/Wednesday/Thursday editions.
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

import extract
import fetch
import generate_audio
import generate_digest
import send_email
import summarize
import write_episode
from dedupe_pool import pool
from gmail_pull import pull_editions

NEWSLETTERS = ("AI", "Marketing")
SLUG = {"AI": "tldr_ai", "Marketing": "tldr_marketing"}

# Persistent per-newsletter episode counter, committed back to the repo each
# run (like output/ and docs/ already are) so numbering survives across
# separate GitHub Actions runs -- each run is a fresh container with no
# memory of prior ones.
COUNTER_PATH = Path("output/episode_counter.json")


def next_episode_number(newsletter: str) -> int:
    counters = json.loads(COUNTER_PATH.read_text()) if COUNTER_PATH.exists() else {}
    counters[newsletter] = counters.get(newsletter, 0) + 1
    COUNTER_PATH.write_text(json.dumps(counters, indent=2) + "\n")
    return counters[newsletter]


def short_date_range(summaries_json_path: str) -> str:
    """DD.MM or DD.MM - DD.MM spanning the real source-edition dates (not the pool/run date)."""
    summaries = json.loads(Path(summaries_json_path).read_text())
    source_dates = set()
    for s in summaries:
        match = re.search(r"(\d{4}-\d{2}-\d{2})", s.get("source_email", ""))
        if match:
            source_dates.add(date.fromisoformat(match.group(1)))
    if not source_dates:
        return ""
    lo, hi = min(source_dates), max(source_dates)
    if lo == hi:
        return f"{lo.day:02d}.{lo.month:02d}"
    return f"{lo.day:02d}.{lo.month:02d} - {hi.day:02d}.{hi.month:02d}"


def run_for_newsletter(newsletter: str, eml_paths: list, run_date: date):
    if not eml_paths:
        print(f"\nNo editions found for TLDR {newsletter} -- skipping this newsletter for this run.")
        return None

    slug = SLUG[newsletter]
    print(f"\n=== TLDR {newsletter}: {len(eml_paths)} edition(s) ===")

    extracted_paths = [extract.main(str(p)) for p in eml_paths]

    pooled_path = Path("output") / f"{slug}_pool_{run_date.isoformat()}_extracted.json"
    pool([str(p) for p in extracted_paths], newsletter, str(pooled_path))

    fetched_path = fetch.main(str(pooled_path))
    summaries_path = summarize.main(str(fetched_path))
    episode_path = write_episode.main(str(summaries_path))
    digest_path = generate_digest.main(str(summaries_path))

    print(f"\nTLDR {newsletter} episode script ready: {episode_path}")
    print(f"TLDR {newsletter} digest page ready: {digest_path}")

    episode_number = next_episode_number(newsletter)
    title = f"TLDR {newsletter} Newsletter {short_date_range(str(summaries_path))} - Episode {episode_number}"

    audio_path = generate_audio.main(str(episode_path), title=title)
    send_email.main(str(audio_path), subject=title)

    return episode_path


def main(run_date: date):
    print(f"Run date: {run_date} ({run_date.strftime('%A')})\n")
    found = pull_editions(run_date)

    by_newsletter = {"AI": [], "Marketing": []}
    for (newsletter, edition_date), path in sorted(found.items(), key=lambda kv: kv[0][1]):
        by_newsletter[newsletter].append(path)

    episodes = {}
    for newsletter in NEWSLETTERS:
        episodes[newsletter] = run_for_newsletter(newsletter, by_newsletter[newsletter], run_date)

    print("\n=== Run complete ===")
    for newsletter, path in episodes.items():
        status = path if path else "skipped (no editions found)"
        print(f"  TLDR {newsletter}: {status}")


if __name__ == "__main__":
    run_date_str = sys.argv[1] if len(sys.argv) > 1 else None
    run_date = date.fromisoformat(run_date_str) if run_date_str else date.today()
    main(run_date)
