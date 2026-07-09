"""
Phase 3 - Step: convert a finished episode script to audio via the ElevenLabs API.

Reads a plain-text episode script (from write_episode.py) and produces an
MP3 using Eleven Turbo v2.5 -- chosen over the full-quality Multilingual v2
model because it costs about half the credits per character for a minor
quality tradeoff on straightforward narration, which matters at this
project's real volume (~165k-200k chars/month).

Turbo v2.5 supports up to 40,000 characters per request, comfortably above
our episode scripts (~8k-12k chars), so no chunking is needed.
"""

import sys
from pathlib import Path

import requests

VOICE_ID = "aGkVQvWUZi16EH8aZJvT"
MODEL_ID = "eleven_turbo_v2_5"
TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


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


def main(episode_txt_path: str):
    env = load_env()
    api_key = env.get("ELEVENLABS_API_KEY")
    if not api_key:
        print("ERROR: ELEVENLABS_API_KEY not found in .env")
        sys.exit(1)

    script_path = Path(episode_txt_path)
    text = script_path.read_text().strip()
    print(f"Generating audio for {script_path} ({len(text)} chars) via {MODEL_ID}...\n")

    response = requests.post(
        TTS_URL.format(voice_id=VOICE_ID),
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        json={
            "text": text,
            "model_id": MODEL_ID,
        },
    )
    if response.status_code != 200:
        print(f"ERROR: ElevenLabs API returned {response.status_code}: {response.text}")
        sys.exit(1)

    out_path = script_path.with_name(script_path.stem.replace("_episode", "") + "_audio.mp3")
    out_path.write_bytes(response.content)

    size_kb = len(response.content) / 1024
    print(f"Wrote {size_kb:.0f} KB to {out_path}")
    return out_path


if __name__ == "__main__":
    main(sys.argv[1])
