"""
Phase 1 - Step: pool multiple editions' extracted articles into one
deduped list per newsletter, ready for the fetch step.

Combines the "kept" lists from several extract.py output files,
drops duplicate articles (same URL), and reassigns clean sequential
IDs -- while keeping a record of which source email each article
came from, for traceability.
"""

import json
import sys
from pathlib import Path


def pool(extracted_paths: list[str], newsletter: str, out_path: str):
    seen_urls = {}
    pooled = []
    duplicate_count = 0

    for path in extracted_paths:
        data = json.loads(Path(path).read_text())
        for story in data["kept"]:
            url = story["url"]
            if url in seen_urls:
                duplicate_count += 1
                print(f"  DUPLICATE (skipped): {story['heading']}  (also in {seen_urls[url]})")
                continue
            seen_urls[url] = Path(path).name
            story = dict(story)
            story["source_email"] = Path(path).name
            pooled.append(story)

    for i, story in enumerate(pooled, start=1):
        story["ref"] = i

    print(f"\nPooled {len(pooled)} unique articles for TLDR {newsletter} "
          f"({duplicate_count} duplicate(s) removed) from {len(extracted_paths)} editions.")

    Path(out_path).write_text(json.dumps({"kept": pooled, "dropped": []}, indent=2))
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    # Usage: python3 scripts/dedupe_pool.py <newsletter_name> <out_path> <extracted1.json> <extracted2.json> ...
    newsletter = sys.argv[1]
    out_path = sys.argv[2]
    extracted_paths = sys.argv[3:]
    pool(extracted_paths, newsletter, out_path)
