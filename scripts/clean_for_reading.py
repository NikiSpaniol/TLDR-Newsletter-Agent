"""
Not part of the pipeline -- a one-off helper to strip obvious nav/ticker/
boilerplate noise out of fetched article text so it's faster to read
during manual summarization. Heuristic only, never used for the actual
stored article_text that gets summarized against.
"""
import json
import re
import sys

NAV_LINK_LINE = re.compile(r"^\s*[\*\-]?\s*(\[.*?\]\(https?://\S+\)\s*)+\s*$")
PRICE_LINE = re.compile(r"^\$[\d,.]+\s*$")
PCT_LINE = re.compile(r"^-?\d+(\.\d+)?%\s*$")
TICKER_HEADER = re.compile(r"^###\s*\[[A-Z0-9]{2,10}\]\(https?://\S+/price/")


def clean(text: str) -> str:
    lines = text.split("\n")
    out = []
    skip_run = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        if TICKER_HEADER.match(line):
            # skip the ticker header + its price + pct lines (up to 4 lines)
            i += 1
            while i < len(lines) and (not lines[i].strip() or PRICE_LINE.match(lines[i].strip()) or PCT_LINE.match(lines[i].strip())):
                i += 1
            continue
        if NAV_LINK_LINE.match(line) and len(line.strip()) < 200:
            i += 1
            continue
        out.append(line)
        i += 1
    cleaned = "\n".join(out)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


if __name__ == "__main__":
    data = json.loads(open(sys.argv[1]).read())
    for s in data["kept"]:
        if s.get("fetch_status") == "ok":
            print("=" * 100)
            print(f"[{s['ref']}] {s['heading']}")
            print(f"URL: {s['url']}")
            print("-" * 100)
            print(clean(s["article_text"]))
            print()
