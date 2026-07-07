"""
Phase 1 - Step: Gmail OAuth login.

Run this once to grant the app read-only access to the newsletter Gmail
account. It opens your browser to log in and consent, then saves a
refresh token to token.json so future runs don't need to log in again.
"""

from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CLIENT_SECRET_PATH = Path("credentials/client_secret.json")
TOKEN_PATH = Path("token.json")


def main():
    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), SCOPES)
        creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json())
        print(f"Saved credentials to {TOKEN_PATH}")
    else:
        print("Already have valid credentials in token.json")


if __name__ == "__main__":
    main()
