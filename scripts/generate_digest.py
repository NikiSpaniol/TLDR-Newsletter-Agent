"""
Phase 2 - Optional step: render a per-run HTML digest page from the
article summaries, and refresh the docs/ site index -- published via
GitHub Pages as a dark, dashboard-style companion to the spoken episode
script. Each story is a compact, click-to-expand row (short teaser by
default, full summary + strategic hook on expand).

Usage: python3 scripts/generate_digest.py <summaries.json>
"""

import json
import re
import sys
from pathlib import Path

from write_episode import detect_edition_label, detect_newsletter

DOCS_DIR = Path("docs")
ASSETS_DIR = DOCS_DIR / "assets"
MANIFEST_PATH = DOCS_DIR / "manifest.json"

# Lightweight keyword rules for a topic color -- not meant to be perfectly
# accurate, just enough visual variety to scan the list quickly. First
# matching category wins; checked in this order.
CATEGORY_RULES = [
    ("regulation", ("regulat", "export control", "white house", "government",
                     "policy", "compliance", "lawsuit", "senate", "congress",
                     " ban ", "antitrust", "eu ")),
    ("business", ("funding", "valuation", " raise", "ipo", "revenue",
                   "acquisition", "acquire", "hiring", "headcount", "layoff",
                   " jobs", "salary", "stake", "investor", "billion", "million")),
    ("research", ("study", "research", "survey", "researchers", "benchmark",
                   "paper", "dataset", "report finds")),
]
DEFAULT_CATEGORY = "product"

CATEGORY_COLORS = {
    "regulation": "#E2574C",
    "business": "#E0A639",
    "research": "#8B7FE8",
    "product": "#4FBF9A",
}

STYLE_CSS = """
* { box-sizing: border-box; }
body {
  margin: 0; padding: 24px 16px 64px; background: #14100D; color: #F3EEE7;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
}
.wrap { max-width: 700px; margin: 0 auto; }
a.back { color: #9C8F84; text-decoration: none; font-size: 13px; }
.hero {
  background: #1B1512; border-radius: 20px; padding: 28px; position: relative;
  overflow: hidden; margin: 16px 0 20px;
}
.hero-arc { position: absolute; top: -40px; right: -60px; opacity: 0.35; }
.hero-top { position: relative; display: flex; justify-content: space-between;
  align-items: flex-start; flex-wrap: wrap; gap: 16px; }
.hero-title { font-size: 28px; line-height: 1.15; }
.hero-title .accent { font-family: Georgia, "Times New Roman", serif; font-style: italic; color: #E8734A; }
.hero-sub { font-size: 13px; color: #9C8F84; margin: 6px 0 0; }
.stats { position: relative; display: flex; gap: 10px; }
.stat { border-radius: 12px; padding: 10px 18px; background: #2A211C; }
.stat.hook { background: #3A251A; }
.stat .num { font-size: 20px; font-weight: 600; }
.stat.hook .num { color: #E8734A; }
.stat .label { font-size: 11px; color: #9C8F84; }
.stat.hook .label { color: #C99074; }
.list { display: flex; flex-direction: column; gap: 1px; background: #2A211C;
  border-radius: 14px; overflow: hidden; }
.row { background: #1F1815; padding: 16px 18px; display: flex; gap: 14px;
  align-items: flex-start; cursor: pointer; }
.row .bar { width: 3px; align-self: stretch; border-radius: 2px; flex-shrink: 0; }
.row .body { flex: 1; min-width: 0; }
.row-top { display: flex; justify-content: space-between; gap: 10px; align-items: flex-start; }
.row-top h3 { flex: 1; margin: 0; }
.row h3 { font-size: 15px; font-weight: 500; }
.row h3 a { color: inherit; text-decoration: none; }
.row h3 a:hover { text-decoration: underline; }
.badge { flex-shrink: 0; font-size: 10px; font-weight: 600; letter-spacing: 0.04em;
  text-transform: uppercase; color: #3A251A; background: #E8734A; border-radius: 20px;
  padding: 3px 10px; }
.teaser { font-size: 13px; color: #9C8F84; margin: 4px 0 0; }
.detail { display: none; margin-top: 10px; font-size: 13px; color: #C7BBAF; line-height: 1.55; }
.detail.open { display: block; }
.detail p { margin: 0; }
.detail .hook-box { background: #2A1B12; border-radius: 10px; padding: 10px 12px; margin-top: 10px; }
.detail .hook-label { font-size: 10px; font-weight: 600; letter-spacing: 0.05em;
  text-transform: uppercase; color: #E8734A; margin-bottom: 4px; display: block; }
.detail .note { font-style: italic; color: #8A7F76; margin-top: 8px; }
.foot-hint { font-size: 12px; color: #6B615A; margin: 14px 4px 0; }
h2.section { font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em;
  color: #9C8F84; margin: 28px 4px 10px; }
.runs a { color: #E8734A; text-decoration: none; }
.runs a:hover { text-decoration: underline; }
.runs ul { list-style: none; margin: 0 0 8px; padding: 0; }
.runs li { padding: 10px 4px; border-bottom: 1px solid #2A211C; font-size: 14px; color: #C7BBAF; }
.runs li:last-child { border-bottom: none; }
"""

SCRIPT_JS = """
document.querySelectorAll('.row').forEach(function (row) {
  row.addEventListener('click', function () {
    var detail = row.querySelector('.detail');
    if (detail) { detail.classList.toggle('open'); }
  });
});
"""

HERO_ARC_SVG = """<svg class="hero-arc" width="280" height="280" viewBox="0 0 280 280" fill="none">
  <circle cx="140" cy="140" r="139" stroke="#E8734A" stroke-width="0.5"/>
  <circle cx="140" cy="140" r="100" stroke="#E8734A" stroke-width="0.5"/>
</svg>"""

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TLDR {newsletter} -- {edition_label}</title>
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
<div class="wrap">
  <a class="back" href="index.html">&larr; all editions</a>
  <div class="hero">
    {arc}
    <div class="hero-top">
      <div>
        <div class="hero-title"><span>TLDR</span> <span class="accent">{newsletter}</span></div>
        <p class="hero-sub">{edition_label}</p>
      </div>
      <div class="stats">
        <div class="stat"><div class="num">{story_count}</div><div class="label">stories</div></div>
        <div class="stat hook"><div class="num">{hook_count}</div><div class="label">strategic hooks</div></div>
      </div>
    </div>
  </div>

  <div class="list">
    {rows}
  </div>
  <p class="foot-hint">Click a story to expand the full summary and strategic hook.</p>
</div>
<script src="assets/script.js"></script>
</body>
</html>
"""

INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TLDR Digest</title>
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
<div class="wrap">
  <div class="hero">
    {arc}
    <div class="hero-top">
      <div>
        <div class="hero-title"><span>TLDR</span> <span class="accent">Digest</span></div>
        <p class="hero-sub">All editions, newest first</p>
      </div>
    </div>
  </div>
  <div class="runs">
    {runs}
  </div>
</div>
</body>
</html>
"""


def hook_is_real(hook: str) -> bool:
    return bool(hook) and hook.strip().rstrip(".").lower() != "none"


def first_sentence(text: str) -> str:
    match = re.search(r"^.*?[.!?](?=\s|$)", text.strip())
    return match.group(0).strip() if match else text.strip()


def categorize(heading: str, summary: str) -> str:
    haystack = f"{heading} {summary}".lower()
    for category, keywords in CATEGORY_RULES:
        if any(kw in haystack for kw in keywords):
            return category
    return DEFAULT_CATEGORY


def render_row(story: dict) -> str:
    category = categorize(story["heading"], story["summary"])
    color = CATEGORY_COLORS[category]
    hook = story.get("strategic_hook", "")
    has_hook = hook_is_real(hook)
    badge_html = '<span class="badge">hook</span>' if has_hook else ""
    note_html = f'<p class="note">{story["source_note"]}</p>' if story.get("source_note") else ""
    hook_html = (
        f'<div class="hook-box"><span class="hook-label">Strategic hook</span>{hook}</div>'
        if has_hook else ""
    )
    return f"""<div class="row">
      <div class="bar" style="background:{color};"></div>
      <div class="body">
        <div class="row-top">
          <h3><a href="{story['url']}" target="_blank" rel="noopener" onclick="event.stopPropagation()">{story['heading']}</a></h3>
          {badge_html}
        </div>
        <p class="teaser">{first_sentence(story['summary'])}</p>
        <div class="detail">
          <p>{story['summary']}</p>
          {note_html}
          {hook_html}
        </div>
      </div>
    </div>"""


def render_page(newsletter: str, edition_label: str, summaries: list) -> str:
    hook_count = sum(1 for s in summaries if hook_is_real(s.get("strategic_hook", "")))
    rows = "\n".join(render_row(s) for s in summaries)
    return PAGE_TEMPLATE.format(
        newsletter=newsletter,
        edition_label=edition_label,
        arc=HERO_ARC_SVG,
        story_count=len(summaries),
        hook_count=hook_count,
        rows=rows,
    )


def extract_run_date(stem: str) -> str:
    match = re.search(r"(\d{4}-\d{2}-\d{2})", stem)
    return match.group(1) if match else "0000-00-00"


def load_manifest() -> list:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text())
    return []


def update_manifest(entry: dict) -> list:
    manifest = load_manifest()
    manifest = [e for e in manifest if e["filename"] != entry["filename"]]
    manifest.append(entry)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    return manifest


def render_index(manifest: list) -> str:
    by_run_date = {}
    for entry in manifest:
        by_run_date.setdefault(entry["run_date"], []).append(entry)

    blocks = []
    for run_date in sorted(by_run_date.keys(), reverse=True):
        entries = sorted(by_run_date[run_date], key=lambda e: e["newsletter"])
        links = "\n".join(
            f'<li><a href="{e["filename"]}">TLDR {e["newsletter"]}</a> -- {e["edition_label"]}</li>'
            for e in entries
        )
        blocks.append(f'<h2 class="section">{run_date}</h2>\n<ul>{links}</ul>')

    return INDEX_TEMPLATE.format(arc=HERO_ARC_SVG, runs="\n".join(blocks))


def write_assets():
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    (ASSETS_DIR / "style.css").write_text(STYLE_CSS)
    (ASSETS_DIR / "script.js").write_text(SCRIPT_JS)


def main(summaries_json_path: str) -> Path:
    summaries = json.loads(Path(summaries_json_path).read_text())
    newsletter = detect_newsletter(summaries_json_path)
    edition_label = detect_edition_label(summaries_json_path, summaries)

    stem = Path(summaries_json_path).stem.replace("_summaries", "")
    out_filename = f"{stem}_digest.html"

    DOCS_DIR.mkdir(exist_ok=True)
    (DOCS_DIR / ".nojekyll").touch()
    write_assets()

    out_path = DOCS_DIR / out_filename
    out_path.write_text(render_page(newsletter, edition_label, summaries))
    print(f"Wrote digest page to {out_path}")

    manifest = update_manifest({
        "filename": out_filename,
        "newsletter": newsletter,
        "edition_label": edition_label,
        "run_date": extract_run_date(stem),
    })
    (DOCS_DIR / "index.html").write_text(render_index(manifest))
    print(f"Updated {DOCS_DIR / 'index.html'}")

    return out_path


if __name__ == "__main__":
    main(sys.argv[1])
