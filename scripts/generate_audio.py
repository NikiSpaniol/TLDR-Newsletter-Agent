"""
Phase 3 - Step: convert a finished episode script to audio via the ElevenLabs API.

Reads a plain-text episode script (from write_episode.py) and produces an MP3.

MODEL CHOICE
------------
Eleven Turbo v2.5 (the original choice here, picked to halve credit cost) is
now deprecated -- ElevenLabs' own docs describe it as outclassed by the Flash
models. eleven_flash_v2_5 is its direct successor at comparable cost, so it's
the default. Set ELEVENLABS_MODEL_ID in .env to override:

  eleven_flash_v2_5      cheapest, least expressive, 40k chars/request
  eleven_multilingual_v2 "most stable on long-form", 10k chars/request, ~2x cost
  eleven_v3              most expressive/emotional, 5k chars/request, priciest

For a warm conversational briefing, multilingual_v2 or v3 are the upgrades
worth paying for -- flash is the safe cost-neutral floor.

CHUNKING AND PROSODY
--------------------
Long single-request generations drift toward a flatter, metallic delivery
after a couple of minutes of audio. The original fix here was to split the
script into 500-character chunks synthesized as fully independent requests.
That solved the drift but caused a worse problem: with no context, the model
restarts its intonation cold every ~3 sentences, which is the single biggest
reason the output sounds robotic.

Stitching via previous_request_ids was tried and made things worse, because it
carries the *degraded audio state* of one chunk into the next, compounding it.

The fix is previous_text / next_text instead: the model gets the surrounding
*text* as context, so prosody flows naturally across the seam, but no audio
state is carried forward. Combined with a much larger chunk size (still short
enough to stay under the drift threshold), this cuts a typical episode from
~30 cold starts to a handful of context-aware ones.

Character-based ElevenLabs billing is unaffected: only `text` is billed, not
previous_text/next_text or voice_settings.
"""

import re
import sys
import time
from pathlib import Path

import requests

VOICE_ID = "aGkVQvWUZi16EH8aZJvT"
DEFAULT_MODEL_ID = "eleven_flash_v2_5"
TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
SUBSCRIPTION_URL = "https://api.elevenlabs.io/v1/user/subscription"

# Roughly one minute of speech (~1000 chars/min). Override with
# ELEVENLABS_CHUNK_CHARS in .env to A/B this without editing code.
#
# Set from measurement, not guesswork. A 1800-char run on 2026-08-23 was
# decoded and analysed per chunk: mean within-chunk level decay -3.6 dB, worst
# chunk -13.4 dB, with volume visibly resetting at every chunk boundary. So
# flash v2.5 does inherit Turbo v2.5's drift -- the 1800 bet was wrong.
# Crucially, dynamic variation held flat (+0.7 dB) across the same spans, so
# the model keeps modulating and only the gain sags; this is a gain envelope
# problem, not the delivery going monotone.
#
# 900 keeps each generation near a minute, well inside where the sag becomes
# audible, while previous_text/next_text supplies the prosodic continuity that
# the old 500-char config lacked -- that absence, not the length itself, is
# what made 500 sound robotic. Do NOT reach for previous_request_ids: it was
# tried in July and compounded the degradation across chunks.
DEFAULT_CHUNK_CHAR_LIMIT = 900

# stability 0.7 (the previous value) sits well above ElevenLabs' 0.5 default,
# and their docs are explicit that higher stability means a more *monotonous*
# read. Dropping below the default trades a little consistency for the
# emotional range this format actually wants.
VOICE_SETTINGS = {
    "stability": 0.45,
    "similarity_boost": 0.75,
    "style": 0.3,
    "use_speaker_boost": True,
}

OUTPUT_FORMAT = "mp3_44100_128"
REQUEST_TIMEOUT = 120
MAX_ATTEMPTS = 3
RETRY_STATUS = {429, 500, 502, 503, 504}


class AudioGenerationError(RuntimeError):
    """Raised instead of exiting, so the caller can keep the rest of the run alive."""


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


def check_auth(api_key: str) -> str:
    """
    Cheap pre-flight validation of the ElevenLabs key.

    Worth doing before the pipeline spends several minutes and real Anthropic
    credits on summarization: a dead key used to surface only at the very end,
    after all that work had already been paid for and was about to be thrown
    away. Returns a short human-readable quota summary on success.
    """
    if not api_key:
        raise AudioGenerationError("ELEVENLABS_API_KEY not found in .env")

    if not api_key.startswith("sk_"):
        raise AudioGenerationError(
            "ELEVENLABS_API_KEY does not start with 'sk_'. ElevenLabs now issues "
            "keys in that format; older-style values are treated as key IDs and "
            "rejected. Rotate the key in the ElevenLabs dashboard (Settings -> "
            "API Keys) and update both .env and the GitHub ELEVENLABS_API_KEY secret."
        )

    try:
        response = requests.get(
            SUBSCRIPTION_URL, headers={"xi-api-key": api_key}, timeout=30
        )
    except requests.RequestException as e:
        raise AudioGenerationError(f"could not reach ElevenLabs: {e}") from e

    if response.status_code != 200:
        raise AudioGenerationError(
            f"ElevenLabs rejected the API key ({response.status_code}): {response.text[:300]}"
        )

    data = response.json()
    used = data.get("character_count")
    limit = data.get("character_limit")
    tier = data.get("tier", "unknown")
    if isinstance(used, int) and isinstance(limit, int):
        return f"tier={tier}, {used:,}/{limit:,} characters used ({limit - used:,} remaining)"
    return f"tier={tier}"


def chunk_text(text: str, limit: int = DEFAULT_CHUNK_CHAR_LIMIT) -> list:
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


def synthesize_chunk(
    text: str,
    api_key: str,
    model_id: str,
    previous_text: str = None,
    next_text: str = None,
) -> bytes:
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": VOICE_SETTINGS,
    }
    # Context for prosody only -- neither field is billed or spoken.
    if previous_text:
        payload["previous_text"] = previous_text
    if next_text:
        payload["next_text"] = next_text

    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.post(
                TTS_URL.format(voice_id=VOICE_ID),
                headers={
                    "xi-api-key": api_key,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                params={"output_format": OUTPUT_FORMAT},
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as e:
            last_error = f"network error: {e}"
        else:
            if response.status_code == 200:
                return response.content
            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            # 4xx other than rate-limiting won't fix themselves on a retry.
            if response.status_code not in RETRY_STATUS:
                break

        if attempt < MAX_ATTEMPTS:
            backoff = 2**attempt
            print(f"    attempt {attempt} failed ({last_error}); retrying in {backoff}s...")
            time.sleep(backoff)

    raise AudioGenerationError(f"ElevenLabs synthesis failed: {last_error}")


def safe_filename(name: str) -> str:
    """Strip path separators and other characters that break filenames."""
    return re.sub(r'[/\\:*?"<>|]', "-", name).strip()


def main(episode_txt_path: str, title: str = None, api_key: str = None):
    if api_key is None:
        api_key = load_env().get("ELEVENLABS_API_KEY")
    if not api_key:
        raise AudioGenerationError("ELEVENLABS_API_KEY not found in .env")

    env = load_env()
    model_id = env.get("ELEVENLABS_MODEL_ID") or DEFAULT_MODEL_ID
    chunk_limit = int(env.get("ELEVENLABS_CHUNK_CHARS") or DEFAULT_CHUNK_CHAR_LIMIT)

    script_path = Path(episode_txt_path)
    text = script_path.read_text().strip()
    chunks = chunk_text(text, chunk_limit)
    print(
        f"Generating audio for {script_path} "
        f"({len(text)} chars, {len(chunks)} chunk(s) @ {chunk_limit}) via {model_id}...\n"
    )

    audio_parts = []
    for i, chunk in enumerate(chunks):
        print(f"  chunk {i + 1}/{len(chunks)} ({len(chunk)} chars)...")
        audio_parts.append(
            synthesize_chunk(
                chunk,
                api_key,
                model_id,
                previous_text=chunks[i - 1] if i > 0 else None,
                next_text=chunks[i + 1] if i + 1 < len(chunks) else None,
            )
        )

    filename = f"{safe_filename(title)}.mp3" if title else script_path.stem.replace("_episode", "") + "_audio.mp3"
    out_path = script_path.with_name(filename)
    out_path.write_bytes(b"".join(audio_parts))

    size_kb = out_path.stat().st_size / 1024
    print(f"Wrote {size_kb:.0f} KB to {out_path}")
    return out_path


if __name__ == "__main__":
    title_arg = sys.argv[2] if len(sys.argv) > 2 else None
    try:
        main(sys.argv[1], title=title_arg)
    except AudioGenerationError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
