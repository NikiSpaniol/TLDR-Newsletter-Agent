"""
Phase 1 - Step 1: extraction.

Reads a raw TLDR newsletter email (.eml), pulls the text/plain part,
and returns the list of real article stories (title, url, blurb),
dropping sponsors / self-promo / nav / social links per the build brief.
"""

import email
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

SECTION_HEADERS = {
    "HEADLINES & LAUNCHES",
    "DEEP DIVES & ANALYSIS",
    "ENGINEERING & RESEARCH",
    "MISCELLANEOUS",
    "QUICK LINKS",
}

# domains that are never real articles, per the build brief
DROP_DOMAINS = {
    "advertise.tldr.tech",
    "a.tldrnewsletter.com",
    "refer.tldr.tech",
    "hub.sparklp.co",
    "jobs.ashbyhq.com",
    "twitter.com",
    "linkedin.com",
}


def load_plaintext(eml_path: str) -> str:
    with open(eml_path, "r", encoding="utf-8", errors="replace") as f:
        msg = email.message_from_file(f)

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(
                    part.get_content_charset() or "utf-8", errors="replace"
                )
        raise ValueError("No text/plain part found")
    else:
        return msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8", errors="replace"
        )


def split_body_and_links(text: str):
    marker = re.search(r"^Links:\s*\n-+\s*\n", text, re.MULTILINE)
    if not marker:
        raise ValueError("Could not find 'Links:' section")
    return text[: marker.start()], text[marker.end() :]


def parse_links(links_text: str) -> dict:
    links = {}
    for match in re.finditer(r"^\[(\d+)\]\s+(\S+)\s*$", links_text, re.MULTILINE):
        links[int(match.group(1))] = match.group(2)
    return links


def to_paragraphs(body_text: str):
    # collapse word-wrapped single newlines into spaces, keep blank-line breaks
    joined = re.sub(r"(?<!\n)\n(?!\n)", " ", body_text)
    paras = [p.strip() for p in re.split(r"\n\s*\n", joined)]
    return [p for p in paras if p]


def is_heading(paragraph: str) -> bool:
    # heading paragraphs end with a [N] reference and are mostly upper-case
    if not re.search(r"\[\d+\]\s*$", paragraph):
        return False
    letters = re.sub(r"[^A-Za-z]", "", paragraph)
    if not letters:
        return False
    upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
    return upper_ratio > 0.8


def extract_stories(eml_path: str):
    text = load_plaintext(eml_path)
    body_text, links_text = split_body_and_links(text)
    links = parse_links(links_text)
    paragraphs = to_paragraphs(body_text)

    current_section = None
    kept, dropped = [], []
    i = 0
    while i < len(paragraphs):
        para = paragraphs[i]

        if para.upper() in SECTION_HEADERS:
            current_section = para.upper()
            i += 1
            continue

        if para.startswith("Love TLDR?"):
            break  # footer begins, stop entirely

        if is_heading(para):
            ref_match = re.search(r"\[(\d+)\]\s*$", para)
            n = int(ref_match.group(1))
            heading = para[: ref_match.start()].strip()
            blurb = ""
            if i + 1 < len(paragraphs) and not is_heading(paragraphs[i + 1]) and paragraphs[i + 1].upper() not in SECTION_HEADERS:
                blurb = paragraphs[i + 1].strip()
                i += 1

            url = links.get(n, "")
            domain = urlparse(url).netloc.replace("www.", "") if url else ""

            story = {
                "ref": n,
                "section": current_section,
                "heading": heading,
                "url": url,
                "blurb": blurb,
            }

            if "(SPONSOR)" in heading.upper():
                story["drop_reason"] = "sponsor tag"
                dropped.append(story)
            elif domain in DROP_DOMAINS or any(domain.endswith("." + d) for d in DROP_DOMAINS):
                story["drop_reason"] = f"drop-list domain ({domain})"
                dropped.append(story)
            elif not url:
                story["drop_reason"] = "no article URL (self-promo / mailto)"
                dropped.append(story)
            else:
                kept.append(story)

        i += 1

    return kept, dropped


def main(eml_path: str) -> Path:
    kept, dropped = extract_stories(eml_path)

    result = {"kept": kept, "dropped": dropped}
    out_path = Path("output") / (Path(eml_path).stem + "_extracted.json")
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))

    print(f"KEPT ({len(kept)}):")
    for s in kept:
        print(f"  [{s['ref']}] {s['heading']}  ->  {s['url']}")

    print(f"\nDROPPED ({len(dropped)}):")
    for s in dropped:
        print(f"  [{s['ref']}] {s['heading']}  ({s['drop_reason']})")

    print(f"\nFull details written to {out_path}")
    return out_path


if __name__ == "__main__":
    main(sys.argv[1])
