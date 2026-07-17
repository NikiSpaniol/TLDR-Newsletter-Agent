"""
Phase 3 - Step: convert a finished episode script to audio via the ElevenLabs API.

Reads a plain-text episode script (from write_episode.py) and produces an
MP3 using Eleven Turbo v2.5 -- chosen over the full-quality Multilingual v2
model because it costs about half the credits per character for a minor
quality tradeoff on straightforward narration, which matters at this
project's real volume (~165k-200k chars/month).

Long single-request generations tend to drift toward a flatter, more
monotone delivery -- and, on Turbo v2.5 specifically, a progressively
metallic timbre and dropping volume -- after a couple of minutes of
audio. To avoid that, the script is split into short paragraph-aligned
chunks synthesized as fully independent requests (no request-id audio
stitching between them -- that was tried and made the degradation worse,
likely because it carries the degraded audio state of one chunk forward
into the next, compounding it). The chunks are concatenated into a single
MP3 -- output is still one audio file per newsletter, same as before.

Character-based ElevenLabs billing is unaffected by any of this: total
characters sent equals the original script length whether it's one
request or many, and voice_settings don't add to the character count.
"""

import sys
from pathlib import Path

import requests

VOICE_ID = "aGkVQvWUZi16EH8aZJvT"
MODEL_ID = "eleven_turbo_v2_5"
TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

CHUNK_CHAR_LIMIT = 500
VOICE_SETTINGS = {"stability": 0.7, "similarity_boost": 0.75, "style": 0.3}


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


def chunk_text(text: str, limit: int = CHUNK_CHAR_LIMIT) -> list:
    """Group paragraphs into chunks under `limit` chars, never splitting mid-paragraph."""
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    chunks = []
    current = ""
    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) > limit and current:
            chunks.append(current)
            current = para
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def synthesize_chunk(text: str, api_key: str) -> bytes:
    payload = {
        "text": text,
        "model_id": MODEL_ID,
        "voice_settings": VOICE_SETTINGS,
    }

    response = requests.post(
        TTS_URL.format(voice_id=VOICE_ID),
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        json=payload,
    )
    if response.status_code != 200:
        print(f"ERROR: ElevenLabs API returned {response.status_code}: {response.text}")
        sys.exit(1)

    return response.content


def main(episode_txt_path: str, title: str = None):
    env = load_env()
    api_key = env.get("ELEVENLABS_API_KEY")
    if not api_key:
        print("ERROR: ELEVENLABS_API_KEY not found in .env")
        sys.exit(1)

    script_path = Path(episode_txt_path)
    text = script_path.read_text().strip()
    chunks = chunk_text(text)
    print(f"Generating audio for {script_path} ({len(text)} chars, {len(chunks)} chunk(s)) via {MODEL_ID}...\n")

    audio_parts = []
    for i, chunk in enumerate(chunks, 1):
        print(f"  chunk {i}/{len(chunks)} ({len(chunk)} chars)...")
        audio_parts.append(synthesize_chunk(chunk, api_key))

    filename = f"{title}.mp3" if title else script_path.stem.replace("_episode", "") + "_audio.mp3"
    out_path = script_path.with_name(filename)
    out_path.write_bytes(b"".join(audio_parts))

    size_kb = out_path.stat().st_size / 1024
    print(f"Wrote {size_kb:.0f} KB to {out_path}")
    return out_path


if __name__ == "__main__":
    title_arg = sys.argv[2] if len(sys.argv) > 2 else None
    main(sys.argv[1], title=title_arg)
