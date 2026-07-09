"""
Phase 3 - Step: email the finished audio briefing to Niki.

Sends the generated MP3 as an attachment via Gmail SMTP, using an App
Password on the same nikiagenticai@gmail.com account the pipeline already
reads newsletters from. Plain smtplib -- no OAuth scope changes needed,
since App Passwords sidestep the Gmail API's restricted-scope verification
requirements entirely.
"""

import smtplib
import sys
from email.mime.audio import MIMEAudio
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SENDER = "nikiagenticai@gmail.com"
RECIPIENT = "niki.spaniol@gmail.com"


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


def main(audio_path: str, subject: str = None):
    env = load_env()
    app_password = env.get("GMAIL_APP_PASSWORD")
    if not app_password:
        print("ERROR: GMAIL_APP_PASSWORD not found in .env")
        sys.exit(1)

    audio_file = Path(audio_path)
    if subject is None:
        subject = f"TLDR audio briefing: {audio_file.stem}"

    msg = MIMEMultipart()
    msg["From"] = SENDER
    msg["To"] = RECIPIENT
    msg["Subject"] = subject
    msg.attach(MIMEText("Your audio briefing is attached.", "plain"))

    audio_data = audio_file.read_bytes()
    attachment = MIMEAudio(audio_data, _subtype="mpeg")
    attachment.add_header("Content-Disposition", "attachment", filename=audio_file.name)
    msg.attach(attachment)

    print(f"Sending {audio_file.name} ({len(audio_data) / 1024:.0f} KB) from {SENDER} to {RECIPIENT}...\n")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SENDER, app_password)
        server.sendmail(SENDER, RECIPIENT, msg.as_string())

    print("Sent.")


if __name__ == "__main__":
    subject_arg = sys.argv[2] if len(sys.argv) > 2 else None
    main(sys.argv[1], subject_arg)
