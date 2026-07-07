"""
Smoke test: confirm the saved Gmail token actually works, read-only.
Lists the subject/date of the most recent emails from a given sender.
"""

import sys
from pathlib import Path
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN_PATH = Path("token.json")


def main(sender: str, max_results: int = 5):
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    service = build("gmail", "v1", credentials=creds)

    results = service.users().messages().list(
        userId="me", q=f"from:{sender}", maxResults=max_results
    ).execute()
    messages = results.get("messages", [])

    print(f"Found {len(messages)} messages from {sender} (showing up to {max_results}):\n")
    for m in messages:
        msg = service.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["Subject", "Date"]
        ).execute()
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        print(f"  {headers.get('Date', '?')}  |  {headers.get('Subject', '?')}")


if __name__ == "__main__":
    sender = sys.argv[1] if len(sys.argv) > 1 else "dan@tldrnewsletter.com"
    main(sender)
