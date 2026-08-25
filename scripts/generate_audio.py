"""
Phase 3 - Step: convert a finished episode script to audio via the ElevenLabs API.

Reads a plain-text episode script (from write_episode.py) and produces an MP3.

MODEL CHOICE
------------
eleven_v3 is the default, chosen by blind listening test on 2026-08-25 after
the flash output was still judged dull. Set ELEVENLABS_MODEL_ID in .env to
override:

  eleven_v3              most expressive; 5k chars/request; no text context
  eleven_flash_v2_5      cheapest and fastest, noticeably flatter delivery
  eleven_multilingual_v2 middle ground, 10k chars/request

v3 was originally ruled out as "priciest". That was wrong, and it was assumed
rather than measured. Metering the account balance either side of a real
render put it at 0.55 credits/character -- effectively the same as flash --
which is ~103k credits for a typical month against a 202,555 allowance. Always
measure a rate before letting it drive a decision.

Beyond sounding better, v3 also removed the volume sag that four rounds of
chunk tuning had only reduced: whole-file drift went from -4.4 dB on flash to
+0.4 dB, and the mean level came up 10 dB (-33.5 -> -23.6 dBFS). It is far
slower (163s vs 17s for a 6.3k-char script), which does not matter for a
scheduled run.

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
DEFAULT_MODEL_ID = "eleven_v3"
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

# Settled by listening test: four variants of the same 430-char opening were
# rendered and compared by ear, and this pairing (on eleven_v3) was the pick.
# stability sits below ElevenLabs' 0.5 default because their docs are explicit
# that higher stability means a more *monotonous* read; style pushes the model
# to lean into expressive delivery. An attempt to rank the variants by measured
# dynamic range ranked them backwards -- loudness variance counts pauses as
# "dynamics" -- so this is one of the few things here not settled by a number.
VOICE_SETTINGS = {
    "stability": 0.35,
    "similarity_boost": 0.75,
    "style": 0.55,
    "use_speaker_boost": True,
}

# eleven_v3 rejects previous_text/next_text outright (HTTP 400
# unsupported_model), so prosodic context cannot be supplied to it. Because
# nothing bridges its chunk seams, v3 is instead given far larger chunks --
# fewer cold starts beats short ones with no continuity. Its hard per-request
# ceiling is 5,000 characters.
MODELS_WITHOUT_TEXT_CONTEXT = {"eleven_v3"}
MODEL_CHAR_CEILING = {"eleven_v3": 5000, "eleven_multilingual_v2": 10000}
MODEL_CHUNK_LIMIT = {"eleven_v3": 3200}

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


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _split_units(text: str, limit: int) -> list:
    """
    Break the script into (piece, separator) units small enough to pack.

    Paragraphs are the natural unit, but one longer than `limit` would become an
    oversized chunk on its own, so those are split further at sentence
    boundaries. The separator records how each piece rejoins the one before it
    -- a blank line between paragraphs, a single space between sentences of the
    same paragraph -- so a reassembled chunk keeps the pacing cues the model
    reads.
    """
    units = []
    for para in (p.strip() for p in text.split("\n\n")):
        if not para:
            continue
        if len(para) <= limit:
            units.append((para, "\n\n"))
            continue
        current, leading = "", True
        for sentence in _SENTENCE_END.split(para):
            candidate = f"{current} {sentence}" if current else sentence
            if len(candidate) > limit and current:
                units.append((current, "\n\n" if leading else " "))
                leading = False
                current = sentence
            else:
                current = candidate
        if current:
            units.append((current, "\n\n" if leading else " "))
    return units


def chunk_text(
    text: str,
    limit: int = DEFAULT_CHUNK_CHAR_LIMIT,
    min_chars: int = None,
    hard_max: int = None,
) -> list:
    """
    Pack the script into chunks of at most `limit` chars and, where possible, at
    least `min_chars`.

    The minimum matters as much as the maximum. The opening greeting is its own
    short paragraph, so a purely greedy packer emitted it as a ~150-char chunk:
    a standalone ~9 second generation with its own energy arc, followed by the
    first story starting cold in a separate request. That seam was clearly
    audible and read as unpolished. Folding runts into a neighbour keeps the
    greeting and the story it introduces inside one generation, so they share a
    single delivery.
    """
    if min_chars is None:
        min_chars = max(1, limit // 2)
    # Merging a runt can push a chunk past `limit`, which is fine for models
    # that only treat it as a target -- but eleven_v3 rejects anything over
    # 5,000 characters outright, so the merge passes must respect a hard cap.
    if hard_max is None:
        hard_max = limit + min_chars

    chunks = []
    current = ""
    for piece, sep in _split_units(text, limit):
        candidate = f"{current}{sep}{piece}" if current else piece
        if len(candidate) > limit and current:
            chunks.append(current)
            current = piece
        else:
            current = candidate
    if current:
        chunks.append(current)

    # Fold each runt forward into the chunk that follows it, so the greeting
    # rides along with the story it introduces rather than standing alone.
    merged = []
    for chunk in chunks:
        if (
            merged
            and len(merged[-1]) < min_chars
            and len(merged[-1]) + len(chunk) + 2 <= hard_max
        ):
            merged[-1] = f"{merged[-1]}\n\n{chunk}"
        else:
            merged.append(chunk)
    # A trailing runt (the sign-off) has nothing after it, so fold it backward.
    # Pop first: evaluating merged.pop() inside the assignment's right-hand side
    # shrinks the list before the target index is resolved, so a two-chunk script
    # ending in a short sign-off raised IndexError.
    if (
        len(merged) > 1
        and len(merged[-1]) < min_chars
        and len(merged[-2]) + len(merged[-1]) + 2 <= hard_max
    ):
        tail = merged.pop()
        merged[-1] = f"{merged[-1]}\n\n{tail}"

    # Last resort. Sentence splitting cannot help a paragraph containing no
    # sentence terminator (or one enormous sentence), and eleven_v3 rejects
    # any request over its ceiling outright, so force a break at a word
    # boundary rather than let the request fail.
    capped = []
    for chunk in merged:
        while len(chunk) > hard_max:
            cut = chunk.rfind(" ", 0, hard_max)
            if cut <= 0:
                cut = hard_max
            capped.append(chunk[:cut].rstrip())
            chunk = chunk[cut:].lstrip()
        capped.append(chunk)
    return capped


def _mp3_frame_length(header: bytes) -> int:
    """Byte length of the MPEG audio frame these 4 header bytes describe, else 0."""
    if len(header) < 4 or header[0] != 0xFF or (header[1] & 0xE0) != 0xE0:
        return 0
    version = (header[1] >> 3) & 0x03     # 3=MPEG1, 2=MPEG2, 0=MPEG2.5
    layer = (header[1] >> 1) & 0x03       # 1=Layer III
    bitrate_index = (header[2] >> 4) & 0x0F
    rate_index = (header[2] >> 2) & 0x03
    padding = (header[2] >> 1) & 0x01
    if layer != 1 or bitrate_index in (0, 15) or rate_index == 3 or version == 1:
        return 0
    mpeg1 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
    mpeg2 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
    bitrate = (mpeg1 if version == 3 else mpeg2)[bitrate_index] * 1000
    rates = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}
    return (144 * bitrate) // rates[version][rate_index] + padding


def strip_mp3_container(data: bytes) -> bytes:
    """
    Return only the audio frames of one MP3, dropping ID3 tags and any
    Xing/Info metadata frame.

    Each chunk ElevenLabs returns is a complete standalone MP3: an ID3v2 tag,
    then a Xing/Info frame declaring that chunk's own duration. Byte-joining
    them leaves one such header per chunk, and players honour the first -- which
    describes chunk 1 alone. That is why a 15 minute briefing reported itself as
    9 seconds in QuickTime, iTunes and the Gmail preview, with seeking and
    excerpting broken. Stripping every chunk's container leaves a clean
    constant-bitrate frame stream, whose duration players derive from its size.
    """
    if data[:3] == b"ID3" and len(data) > 10:
        size = (
            ((data[6] & 0x7F) << 21) | ((data[7] & 0x7F) << 14)
            | ((data[8] & 0x7F) << 7) | (data[9] & 0x7F)
        )
        data = data[10 + size:]
    if data[-128:][:3] == b"TAG":  # ID3v1 trailer
        data = data[:-128]
    length = _mp3_frame_length(data[:4])
    if length and (b"Xing" in data[:length] or b"Info" in data[:length]):
        data = data[length:]
    return data


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
    # Context for prosody only -- neither field is billed or spoken. Omitted
    # for models that reject them (see MODELS_WITHOUT_TEXT_CONTEXT).
    if model_id not in MODELS_WITHOUT_TEXT_CONTEXT:
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
    chunk_limit = int(
        env.get("ELEVENLABS_CHUNK_CHARS")
        or MODEL_CHUNK_LIMIT.get(model_id, DEFAULT_CHUNK_CHAR_LIMIT)
    )
    chunk_limit = min(chunk_limit, MODEL_CHAR_CEILING.get(model_id, chunk_limit))

    script_path = Path(episode_txt_path)
    text = script_path.read_text().strip()
    chunks = chunk_text(
        text, chunk_limit, hard_max=MODEL_CHAR_CEILING.get(model_id)
    )
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
    out_path.write_bytes(b"".join(strip_mp3_container(part) for part in audio_parts))

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
