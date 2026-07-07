"""
Phase 1 - Step 2: fetch full article text via Jina Reader.

Reads the extracted-articles JSON from extract.py, fetches
https://r.jina.ai/<url> for each kept article, and saves the
full article text back into the JSON. Skips and logs failures
without stopping the run.
"""

import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

MAX_CHARS = 24000  # rough cap ~6k tokens, per the build brief's cost-control note


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


BLOCKED_SIGNALS = (
    "Warning: This page maybe requiring CAPTCHA",
    "Warning: This page maybe not yet fully loaded",
    "Warning: Target URL returned error",
)


def fetch_article(url: str, api_key: str, timeout: int = 30) -> str:
    jina_url = f"https://r.jina.ai/{url}"
    req = urllib.request.Request(
        jina_url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0 (compatible; TLDR-Briefing-Agent/1.0)",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def main(extracted_json_path: str):
    env = load_env()
    api_key = env.get("JINA_API_KEY")
    if not api_key:
        print("ERROR: JINA_API_KEY not found in .env")
        sys.exit(1)

    data = json.loads(Path(extracted_json_path).read_text())
    kept = data["kept"]

    print(f"Fetching {len(kept)} articles via Jina Reader...\n")

    for story in kept:
        title = story["heading"]
        url = story["url"]
        try:
            text = fetch_article(url, api_key)
            if any(signal in text for signal in BLOCKED_SIGNALS):
                story["article_text"] = ""
                story["fetch_status"] = "failed: blocked (CAPTCHA/paywall)"
                print(f"  FAIL [{story['ref']}] {title}  -> blocked (CAPTCHA/paywall)")
                continue
            text = text[:MAX_CHARS]
            story["article_text"] = text
            story["fetch_status"] = "ok"
            print(f"  OK   [{story['ref']}] {title}  ({len(text)} chars)")
        except Exception as e:
            story["article_text"] = ""
            story["fetch_status"] = f"failed: {e}"
            print(f"  FAIL [{story['ref']}] {title}  -> {e}")

    out_path = Path(extracted_json_path).with_name(
        Path(extracted_json_path).stem.replace("_extracted", "") + "_fetched.json"
    )
    out_path.write_text(json.dumps(data, indent=2))
    print(f"\nSaved to {out_path}")
    return out_path


if __name__ == "__main__":
    main(sys.argv[1])
