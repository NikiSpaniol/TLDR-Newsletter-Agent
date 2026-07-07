"""
Phase 1 - Step: pull the right pooled editions from Gmail for a given run.

Implements the schedule from the build brief (section 3):
  - Tuesday run  -> previous Friday + Monday editions
  - Friday run   -> this week's Tuesday + Wednesday + Thursday editions

TLDR AI and TLDR Marketing both send from dan@tldrnewsletter.com, so we
can't filter by address alone -- each email's From header display name
("TLDR AI" vs "TLDR Marketing") tells us which newsletter it is, and we
cross-check against the self-declared edition date printed inside the
email body itself (e.g. "TLDR AI 2026-07-02"), since Gmail's date search
is day-granular and can be timezone-fuzzy.
"""

import base64
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN_PATH = Path("token.json")
SENDER = "dan@tldrnewsletter.com"
EMAILS_DIR = Path("emails")

EDITION_LINE_RE = re.compile(r"TLDR (AI|MARKETING)\s+(\d{4}-\d{2}-\d{2})", re.IGNORECASE)


def get_run_window(run_date: date) -> list[date]:
    weekday = run_date.weekday()  # Monday=0 ... Sunday=6
    if weekday == 1:  # Tuesday
        return [run_date - timedelta(days=4), run_date - timedelta(days=1)]  # Fri, Mon
    elif weekday == 4:  # Friday
        return [run_date - timedelta(days=3), run_date - timedelta(days=2), run_date - timedelta(days=1)]  # Tue, Wed, Thu
    else:
        raise ValueError("run_date must be a Tuesday or a Friday, per the schedule")


def get_service():
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    return build("gmail", "v1", credentials=creds)


def list_candidates(service, target_dates: list[date]):
    earliest = min(target_dates) - timedelta(days=2)
    latest = max(target_dates) + timedelta(days=2)
    query = f"from:{SENDER} after:{earliest:%Y/%m/%d} before:{latest:%Y/%m/%d}"
    results = service.users().messages().list(userId="me", q=query, maxResults=50).execute()
    return results.get("messages", [])


def fetch_raw(service, message_id: str) -> bytes:
    msg = service.users().messages().get(userId="me", id=message_id, format="raw").execute()
    return base64.urlsafe_b64decode(msg["raw"])


def parse_edition_info(raw_bytes: bytes):
    """Returns (newsletter, edition_date) by reading the self-declared date line in the body."""
    import email as email_lib
    msg = email_lib.message_from_bytes(raw_bytes)
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            text = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
            match = EDITION_LINE_RE.search(text)
            if match:
                newsletter = "AI" if match.group(1).upper() == "AI" else "Marketing"
                edition_date = date.fromisoformat(match.group(2))
                return newsletter, edition_date
    return None, None


def pull_editions(run_date: date):
    target_dates = get_run_window(run_date)
    print(f"Run date: {run_date} ({run_date.strftime('%A')})")
    print(f"Target edition dates: {[d.isoformat() for d in target_dates]}\n")

    service = get_service()
    candidates = list_candidates(service, target_dates)
    print(f"Found {len(candidates)} candidate emails from {SENDER} in the search window\n")

    EMAILS_DIR.mkdir(exist_ok=True)
    found = {}  # (newsletter, date) -> filename

    for c in candidates:
        raw = fetch_raw(service, c["id"])
        newsletter, edition_date = parse_edition_info(raw)
        if newsletter is None or edition_date not in target_dates:
            continue

        key = (newsletter, edition_date)
        slug = "tldr_ai" if newsletter == "AI" else "tldr_marketing"
        filename = EMAILS_DIR / f"{slug}_{edition_date.isoformat()}.eml"
        filename.write_bytes(raw)
        found[key] = filename
        print(f"  SAVED  TLDR {newsletter}  {edition_date}  -> {filename}")

    print("\nSummary:")
    for newsletter in ("AI", "Marketing"):
        for d in target_dates:
            status = "OK" if (newsletter, d) in found else "MISSING"
            print(f"  TLDR {newsletter}  {d}  [{status}]")

    return found


if __name__ == "__main__":
    run_date_str = sys.argv[1] if len(sys.argv) > 1 else None
    run_date = date.fromisoformat(run_date_str) if run_date_str else date.today()
    pull_editions(run_date)
