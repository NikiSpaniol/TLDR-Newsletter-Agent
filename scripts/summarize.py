"""
Phase 2 - Step: summarize each article via the Anthropic API ("map" step).

Reads the fetched-articles JSON from fetch.py and produces one summary +
strategic-hook assessment per article, using the per-article prompt from
the build brief (section 8), verbatim. One API call per article.

Uses Claude Haiku 4.5 -- this step is mechanical/factual (extract what
happened + assess a strategic hook), not a place that needs a stronger
model's judgment.
"""

import json
import re
import sys
from pathlib import Path

import anthropic

MAP_MODEL = "claude-haiku-4-5"
MAX_TOKENS = 800

# Jina's fetch of an X/Twitter post (via TLDR's redirect short-links, so the
# URL itself doesn't reveal the destination) has a distinctive fingerprint:
# a title like `Title: <name> on X: "..."` plus the logged-out X/Twitter
# "Log in" / "Sign up" boilerplate.
THIN_SOURCE_RE = re.compile(r'^Title:.*\bon X:\s*"', re.IGNORECASE)

PROMPT_TEMPLATE = """You are summarising a single article that appeared in the TLDR {newsletter} newsletter. Produce a tight, accurate summary that captures: what happened, the key specifics (numbers, names, mechanisms), and why it matters. Keep it to 4-6 sentences. Do not invent anything not present in the article.

Then, on a new line, assess whether this story has a genuine connection to Customer Strategy work -- identifying customer needs, market/competitor analysis, customer research, customer-journey mapping, understanding business strategy, or building business cases. If it does, write "Strategic hook:" followed by one specific sentence naming the concrete application. If it does not, write "Strategic hook: none." Be honest -- most stories will have none.

Formatting: plain prose only. No markdown headers, no bold/italic markers, no bullet points, no horizontal rules. Just the summary sentences, then a blank line, then the "Strategic hook:" line.

Article heading: {heading}

Article text:
{article_text}"""


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


def detect_newsletter(fetched_json_path: str) -> str:
    stem = Path(fetched_json_path).stem.lower()
    return "Marketing" if "marketing" in stem else "AI"


def clean_markdown_artifacts(text: str) -> str:
    text = re.sub(r"^#{1,6}\s*.*$", "", text, flags=re.MULTILINE)  # markdown headers
    text = re.sub(r"^-{3,}\s*$", "", text, flags=re.MULTILINE)  # horizontal rules
    text = text.replace("**", "").replace("__", "")  # bold markers
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def split_summary_and_hook(text: str):
    match = re.search(r"Strategic hook:\s*(.*)", text, re.IGNORECASE | re.DOTALL)
    if not match:
        return clean_markdown_artifacts(text), "none"
    summary = clean_markdown_artifacts(text[: match.start()])
    hook = match.group(1).strip()
    return summary, hook


def is_thin_source(article_text: str) -> bool:
    return bool(THIN_SOURCE_RE.match(article_text.strip()))


def summarize_story(client: anthropic.Anthropic, story: dict, newsletter: str) -> dict:
    prompt = PROMPT_TEMPLATE.format(
        newsletter=newsletter,
        heading=story["heading"],
        article_text=story["article_text"],
    )
    response = client.messages.create(
        model=MAP_MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    summary, hook = split_summary_and_hook(text)

    result = {
        "ref": story["ref"],
        "heading": story["heading"],
        "url": story["url"],
        "summary": summary,
        "strategic_hook": hook,
    }
    if story.get("source_email"):
        result["source_email"] = story["source_email"]
    if is_thin_source(story["article_text"]):
        result["source_note"] = "thin source: X/Twitter teaser only, not the full linked piece"
    return result


def main(fetched_json_path: str):
    env = load_env()
    api_key = env.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not found in .env")
        sys.exit(1)

    # Explicit timeout. The SDK's 10-minute default is far too generous for a
    # single Haiku summary of one article, and on 2026-08-23 a local run hung
    # for 3.5 hours here: the laptop slept mid-run, the in-flight HTTPS
    # connection died silently, and the socket blocked in recv() on a peer that
    # was never coming back. A tight timeout turns that from an indefinite hang
    # into a retry. max_retries covers the transient case.
    client = anthropic.Anthropic(api_key=api_key, timeout=120.0, max_retries=3)
    newsletter = detect_newsletter(fetched_json_path)

    data = json.loads(Path(fetched_json_path).read_text())
    kept = data["kept"]

    summaries = []
    skipped = []

    print(f"Summarizing {len(kept)} articles for TLDR {newsletter} via {MAP_MODEL}...\n")

    for story in kept:
        if not story.get("article_text"):
            skipped.append(story)
            print(f"  SKIP [{story['ref']}] {story['heading']}  -> {story.get('fetch_status', 'no article text')}")
            continue
        try:
            result = summarize_story(client, story, newsletter)
            summaries.append(result)
            flag = " (thin source)" if "source_note" in result else ""
            print(f"  OK   [{story['ref']}] {story['heading']}{flag}")
        except Exception as e:
            skipped.append({**story, "fetch_status": f"summarize failed: {e}"})
            print(f"  FAIL [{story['ref']}] {story['heading']}  -> {e}")

    out_path = Path(fetched_json_path).with_name(
        Path(fetched_json_path).stem.replace("_fetched", "") + "_summaries.json"
    )
    out_path.write_text(json.dumps(summaries, indent=2))
    print(f"\nSaved {len(summaries)} summaries to {out_path}")

    if skipped:
        skipped_path = Path(fetched_json_path).with_name(
            Path(fetched_json_path).stem.replace("_fetched", "") + "_skipped.json"
        )
        skipped_path.write_text(json.dumps(skipped, indent=2))
        print(f"Saved {len(skipped)} skipped articles to {skipped_path}")

    return out_path


if __name__ == "__main__":
    main(sys.argv[1])
