# TLDR Audio Briefing — Build Brief (v2)

*Hand this whole document to Claude Code. Build in phases, one step at a time, and prove each stage on real content before moving on. The person driving this is a non-coder — explain plainly and wait for confirmation between steps.*

---

## 1. Goal

An automated pipeline that, twice a week, turns the **TLDR AI** and **TLDR Marketing** newsletters into engaging **~10–15 minute podcast-style scripts** — summarising the **full linked articles** (not the short email blurbs), ignoring all ads/sponsors/promo, with a **Customer Strategy lens woven in where a story genuinely relates to it**. Later add-on: audio via ElevenLabs.

---

## 2. How we're building it — TWO PHASES (important)

**Phase 1 — prove it locally (do this first).**
Run on the person's own computer, driven by Claude Code, using **Claude Code's own summarisation** — *not* the Anthropic API. No GitHub, no cloud, no billing. The single goal of Phase 1 is to confirm the summaries are genuinely good.
- **Start by testing the whole pipeline on ONE TLDR AI email the person pastes in** — extraction → Jina fetch → summary. No Gmail setup at all yet.
- Once that output looks great, wire up reading Gmail automatically (see §7) so it can pull the right editions itself, still run locally.

**Phase 2 — automate it (only once Phase 1 is proven).**
Move to unattended, scheduled runs via **GitHub Actions** (free, runs in the cloud, no computer needs to be on).
- A headless cloud job **cannot** use the Claude subscription, so Phase 2 introduces an **Anthropic API key** (pay-per-use) for the summarisation, plus the Gmail OAuth refresh-token flow for silent login.
- Don't start here. It's the upgrade, not the on-ramp.

---

## 3. Runtime & schedule (target end-state)

- Two runs per week, **Australia/Sydney** time:
  - **Tuesday 6am** → process the **Friday + Monday** editions.
  - **Friday 6am** → process the **Tuesday + Wednesday + Thursday** editions.
- Covers all five weekday editions exactly once. Each run processes **both** newsletters.
- **Pooling is critical:** each run pulls **every** edition in its window and combines all their articles into **ONE** script per newsletter — never "just the latest email," never one script per email.

---

## 4. Pipeline (per newsletter, per run)

1. **Get the editions** — the emails in the run's date window (§7). Collect and **dedupe articles across all of them** before summarising.
2. **Extract the real article URLs** (§5).
3. **Fetch full article text** via Jina Reader (§6).
4. **Summarise each article** — one Claude call each, using the per-article prompt (§8, "map").
5. **Weave into one episode script** — one Claude call, using the episode prompt (§8, "reduce").
6. **Deliver** — two scripts per run (one TLDR AI, one TLDR Marketing), as **two separate emails**. Save the raw text too (reused later for ElevenLabs).

---

## 5. Extraction spec

- **Use the `text/plain` part of the email.** It ends with a `Links:` section mapping each `[N]` to the **real destination URL, already unwrapped** — no `tracking.tldrnewsletter.com/CL0/…` redirect to decode. This is the clean source of truth.
- Body is organised into sections (`HEADLINES & LAUNCHES`, `DEEP DIVES & ANALYSIS`, `ENGINEERING & RESEARCH`, `MISCELLANEOUS`, `QUICK LINKS`). Each story = a heading with a `[N]` ref + a blurb.
- **KEEP** real article stories. **DROP:**
  - Any story whose heading contains **`(Sponsor)`** / `(SPONSOR)` — the tag is the reliable signal. Sponsors use normal domains (e.g. `ibm.com`), so do NOT filter sponsors by domain; filter by the tag.
  - The `Together With` sponsor block; TLDR self-promo (hiring, "Love TLDR?", referral, advertise, manage subscriptions, unsubscribe); author socials; nav (Sign Up, Advertise, View Online).
  - In practice also drop `[N]` refs to: `advertise.tldr.tech`, `a.tldrnewsletter.com`, `refer.tldr.tech`, `hub.sparklp.co`, `jobs.ashbyhq.com`, `twitter.com`, `linkedin.com`, `tldr.tech/ai/manage`.
- Some links are TLDR short-links (`links.tldrnewsletter.com/…`) that redirect — keep them; the fetch step follows redirects.
- **Encoding gotcha:** the plain-text part is **quoted-printable** — long URLs wrap across lines with `=` soft-breaks and encode `=` as `=3D`. Decode quoted-printable before parsing URLs.

---

## 6. Fetch full article text (Jina Reader)

- For each kept URL, request **`https://r.jina.ai/<the-article-url>`** → clean, readable article text.
- Use a **free Jina API key** (header) for higher rate limits. *(The person already has this key.)*
- Jina handles JavaScript-rendered pages, redirects, and PDFs.
- **Fail gracefully:** on timeout/block/error, skip that article, log which one, and continue. One bad fetch must never kill the run.
- Optional cost control: cap article text fed to the model (e.g. first ~6,000 tokens).

---

## 7. Gmail access

- **Note:** there is no ready-made Gmail connector available in this setup, so reading Gmail requires the **Gmail API via a Google Cloud project + OAuth** ("installed app" flow). This is needed whenever the pipeline reads the inbox live — in both phases.
- **For the very first Phase 1 test, skip Gmail entirely** — have the person paste one real TLDR AI email so we can prove extraction → Jina → summary without any Google setup.
- When ready to read Gmail automatically, walk the person through creating the Google Cloud project + OAuth client **one screen at a time** (they're a beginner). Key settings: user type **External**; app can stay in **Testing** mode; add the person's newsletter email as a **test user**.
- **Access scope:** the person uses a **separate Gmail account** made just for these newsletters, with **read-only** scope. Do not touch any other account or Google Drive.
- Senders: **TLDR AI = `dan@tldrnewsletter.com`**. Confirm the **TLDR Marketing** sender against a real email before trusting it.

---

## 8. The AI prompts (the tuned core — use verbatim)

### Per-article prompt ("map" step)

> You are summarising a single article that appeared in the TLDR **{AI|Marketing}** newsletter. Produce a tight, accurate summary that captures: what happened, the key specifics (numbers, names, mechanisms), and why it matters. Keep it to 4–6 sentences. Do not invent anything not present in the article.
>
> Then, on a new line, assess whether this story has a **genuine** connection to Customer Strategy work — identifying customer needs, market/competitor analysis, customer research, customer-journey mapping, understanding business strategy, or building business cases. If it does, write `Strategic hook:` followed by one specific sentence naming the concrete application. If it does not, write `Strategic hook: none.` Be honest — most stories will have none.

### Episode prompt ("reduce" step)

> You are writing a single spoken-word podcast episode that briefs Niki on this edition of TLDR **{AI|Marketing}**. You'll receive summaries of each article, each with a flagged strategic hook. Write **one flowing script to be read aloud**.
>
> **VOICE:** A smart, friendly colleague catching Niki up on the commute — warm, brisk, a bit of personality and wit, but always accurate. Bake a clear *why it matters* into each story rather than just relaying facts. Write for the ear: short sentences, natural spoken rhythm, spell out acronyms on first use, no jargon dumps. **No headings, no bullet points, no URLs or citations — clean spoken prose only.** Open with a short intro and close with a brief sign-off.
>
> **STRATEGIC LENS:** Niki works in Customer Strategy — identifying customer needs, market and competitor analysis, customer research, building customer journeys, understanding the overarching business strategy and formulating customer strategy around it, and building business cases. **Where a story genuinely connects** (use the flagged hooks), weave in how it impacts her work or a client use case — and land on something **concrete**: a specific competitor axis to map, a business-case assumption to test, a segmentation or customer-journey insight. Connect each relevant story to a specific part of her work, and vary which part across the episode.
>
> **CRITICAL — do not force it.** Stories without a real hook get no strategic angle, or at most a one-line "competitor-watch" note. The *selectivity* is what makes the relevant ones land; tacking an angle onto everything makes the whole thing hollow and untrustworthy.
>
> **LENGTH:** Aim for a 10–15 minute listen (~1,500–2,250 words). Deep/important stories get 90 seconds to 2 minutes (~200 words); quick items get 20–30 seconds (~50–60 words). If there are many articles, prioritise depth on the most significant and keep the rest brief to stay under the ceiling.
>
> **STRUCTURE:** Brief intro → stories with natural spoken transitions between them → brief outro.

---

## 9. How to drive this build (for Claude Code)

1. Read this brief and confirm understanding in plain English before building anything.
2. **Phase 1, Step 1:** ask the person to paste one real TLDR AI email. Build and prove extraction → Jina fetch → per-article summaries → one woven script, using your own summarisation. Show the result and iterate on it.
3. **Phase 1, Step 2:** once the output is great, set up Gmail reading via Google Cloud OAuth (§7), one screen at a time, so it pulls the right pooled editions automatically — still run locally.
4. **Phase 2:** only after Phase 1 is solid, add the Anthropic API key and a GitHub Actions schedule for hands-off twice-weekly runs.
5. Verify **TLDR Marketing** separately on a real email — its layout may differ slightly; the same principles apply.

Store any secrets (Jina key, later the API key / OAuth token) safely — in a local `.env` for Phase 1, and as GitHub Actions secrets in Phase 2. Never hard-code them.

---

## 10. Later: ElevenLabs

Once the text pipeline is solid, add a final step that sends each finished script to ElevenLabs TTS and attaches or links the resulting audio.
