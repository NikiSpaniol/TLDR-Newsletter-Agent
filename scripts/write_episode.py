"""
Phase 2 - Step: weave per-article summaries into one episode script
via the Anthropic API ("reduce" step).

Reads the summaries JSON from summarize.py and makes a single API call
using the episode prompt from the build brief (section 8), verbatim.

Uses Claude Sonnet 5 -- this step is the one that needs real judgment
(voice, selectivity, where to land a strategic hook), so it gets the
stronger model.
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

import anthropic

REDUCE_MODEL = "claude-sonnet-5"
MAX_TOKENS = 8000

EPISODE_PROMPT = """You are writing a single spoken-word podcast episode that briefs Niki on this edition of TLDR {newsletter}, covering {edition_label}. You'll receive summaries of each article, each with a flagged strategic hook. Write one flowing script to be read aloud.

VOICE: A smart, friendly colleague catching Niki up on the commute -- warm, brisk, a bit of personality and wit, but always accurate. Bake a clear why it matters into each story rather than just relaying facts. Write for the ear: short sentences, natural spoken rhythm, spell out acronyms on first use, no jargon dumps. No headings, no bullet points, no URLs or citations -- clean spoken prose only. Open with a short intro that names the date range ({edition_label}) and close with a brief sign-off.

Keep the energy and conversational tone consistent from the first story to the last -- don't let the writing flatten into a dry recitation of facts as the script goes on. The closing stories deserve the same personality, wit, and varied sentence rhythm as the opening ones; this matters more than usual since the script gets read aloud end to end.

Do not narrate the pipeline. This is an absolute rule with no exceptions. Specifically: no story counts ("nine stories today"), no describing your own selection process, and above all no mentioning articles that were stripped out, dropped, skipped, empty, or failed to fetch. If a story arrived without usable content, it does not exist -- do not refer to it, do not flag it as one to watch, and do not group such items into a closing note. Never write anything resembling "there was meant to be a story on X, but the source came through empty." Just tell him the news, as a colleague would; if something isn't worth including, leave it out silently rather than mentioning that you left it out.

STRATEGIC LENS: Niki works in Customer Strategy -- identifying customer needs, market and competitor analysis, customer research, building customer journeys, understanding the overarching business strategy and formulating customer strategy around it, and building business cases. Where a story genuinely connects (use the flagged hooks), weave in how it impacts his work or a client use case -- and land on something concrete: a specific competitor axis to map, a business-case assumption to test, a segmentation or customer-journey insight. Connect each relevant story to a specific part of his work, and vary which part across the episode.

CRITICAL -- do not force it. Stories without a real hook get no strategic angle, or at most a one-line "competitor-watch" note. The selectivity is what makes the relevant ones land; tacking an angle onto everything makes the whole thing hollow and untrustworthy.

LENGTH: Aim for a 10-15 minute listen (~1,500-2,250 words). Deep/important stories get 90 seconds to 2 minutes (~200 words); quick items get 20-30 seconds (~50-60 words). If there are many articles, prioritise depth on the most significant and keep the rest brief to stay under the ceiling.

STRUCTURE: Brief intro -> stories with natural spoken transitions between them -> brief outro.

Output format: plain spoken prose only, starting directly with the intro sentence. No title line, no markdown headers, no bullet points, no bold/italic markers.

Here are the article summaries for this edition:

{stories_block}"""


def load_env(env_path: str = ".env") -> dict:
    env = {}
    path = Path(env_path)
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


def detect_newsletter(summaries_json_path: str) -> str:
    stem = Path(summaries_json_path).stem.lower()
    return "Marketing" if "marketing" in stem else "AI"


def ordinal(day: int) -> str:
    if 11 <= day % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def format_edition_label(dates: list) -> str:
    dates = sorted(set(dates))
    if len(dates) == 1:
        d = dates[0]
        return f"{d.strftime('%A')} the {ordinal(d.day)} of {d.strftime('%B')}"
    if all(d.month == dates[0].month and d.year == dates[0].year for d in dates):
        parts = [f"{d.strftime('%A')} the {ordinal(d.day)}" for d in dates]
        return " and ".join(parts) + f" of {dates[0].strftime('%B')}"
    parts = [f"{d.strftime('%A')} the {ordinal(d.day)} of {d.strftime('%B')}" for d in dates]
    return " and ".join(parts)


def detect_edition_label(summaries_json_path: str, summaries: list) -> str:
    # Prefer the real source-edition dates (from dedupe_pool's source_email
    # tracking) over the pool/run-date in the filename -- those aren't the
    # same thing, and the filename date can be misleading (it's when the
    # pool was built, not necessarily an edition date).
    source_dates = set()
    for s in summaries:
        match = re.search(r"(\d{4}-\d{2}-\d{2})", s.get("source_email", ""))
        if match:
            source_dates.add(date.fromisoformat(match.group(1)))

    if source_dates:
        return format_edition_label(list(source_dates))

    # Single-email run (no source_email field) -- fall back to the date in
    # the summaries filename itself, e.g. tldr_ai_2026-07-02 -> 2026-07-02.
    stem = Path(summaries_json_path).stem.replace("_summaries", "")
    match = re.search(r"(\d{4}-\d{2}-\d{2})$", stem)
    if match:
        return format_edition_label([date.fromisoformat(match.group(1))])
    return stem


def build_stories_block(summaries: list) -> str:
    blocks = []
    for s in summaries:
        block = f"Story: {s['heading']}\nSummary: {s['summary']}\nStrategic hook: {s['strategic_hook']}"
        if s.get("source_note"):
            block += f"\nNote: {s['source_note']}"
        blocks.append(block)
    return "\n\n".join(blocks)


def main(summaries_json_path: str):
    env = load_env()
    api_key = env.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not found in .env")
        sys.exit(1)

    # Longer timeout than summarize.py -- this is one large generation (up to
    # MAX_TOKENS of episode script), not a short per-article call. Still bounded
    # so a dead socket can't hang the run indefinitely; see the note in
    # summarize.py for the failure this guards against.
    client = anthropic.Anthropic(api_key=api_key, timeout=300.0, max_retries=3)
    newsletter = detect_newsletter(summaries_json_path)
    summaries = json.loads(Path(summaries_json_path).read_text())
    edition_label = detect_edition_label(summaries_json_path, summaries)

    print(f"Writing episode script for TLDR {newsletter} ({edition_label}) from {len(summaries)} summaries via {REDUCE_MODEL}...\n")

    prompt = EPISODE_PROMPT.format(
        newsletter=newsletter,
        edition_label=edition_label,
        stories_block=build_stories_block(summaries),
    )

    response = client.messages.create(
        model=REDUCE_MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    script = "".join(block.text for block in response.content if block.type == "text")

    out_path = Path(summaries_json_path).with_name(
        Path(summaries_json_path).stem.replace("_summaries", "") + "_episode.txt"
    )
    out_path.write_text(script)

    word_count = len(script.split())
    print(f"Wrote {word_count} words ({response.usage.input_tokens} in / {response.usage.output_tokens} out tokens) to {out_path}")
    return out_path


if __name__ == "__main__":
    main(sys.argv[1])
