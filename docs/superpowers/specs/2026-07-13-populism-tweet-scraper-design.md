# Politician Tweet Scraper — Design

**Date:** 2026-07-13
**Project:** Wendy Project / Populism Scraping
**Purpose:** Collect tweets from a defined set of politicians' public X accounts for
downstream populism analysis, as academic research at Utrecht University.

## Scope & decisions (agreed)

- **History depth:** Recent timeline + a bounded date window (not full lifetime archive).
- **Route:** Self-serve scraping via `twscrape`.
- **Account pool:** Start with 5 dedicated X accounts (redundancy + overnight throughput).

## Legal / ethical posture

- Collecting public political speech for research is permitted under EU research law
  (DSM Directive Art. 3 TDM exception; DSA research provisions).
- **However**, self-serve scraping violates X's Terms of Service and risks account
  suspension. This is a deliberate, documented choice to be surfaced to the ethics /
  data-management review — not a legal blocker, but a governance one.
- Politicians' tweets are **personal data** under GDPR even when public. The Data
  Management Plan must record: legal basis (research exemption), storage location,
  retention period, and access controls.

## Architecture

Two decoupled stages so re-running analysis never re-scrapes:

```
politicians.csv ─┐
cookies (x5) ────┤
                 ▼
        [1] account-pool loader ──► accounts.db
                 ▼
        [2] two-pass collector ───► tweets.sqlite (raw JSON, tweet_id PK)
                 │  (checkpoint per handle)
                 ▼
        [3] flatten step ─────────► tweets.parquet (tidy analysis table)
```

### Components

1. **Account-pool loader** — reads exported browser cookies for the 5 accounts into
   twscrape's `accounts.db`; verifies each account is live before a run. Cookie-based
   auth (not password) to avoid login-challenge lockouts.

2. **Two-pass collector** — for each handle:
   - Resolve `user_id` from handle.
   - Pass A: `user_tweets(user_id)` for the recent timeline (capped ~3,200 by X).
   - Pass B: `search("from:handle since:YYYY-MM-DD until:YYYY-MM-DD")`, chunked
     month-by-month, to cover the bounded window and bypass the 3,200 ceiling.
   - Dedup A vs B by `tweet_id`.
   - twscrape rotates the account pool and backs off on rate limits automatically.

3. **Storage** — raw tweet JSON appended to SQLite, `tweet_id` as primary key so
   reruns are idempotent. Full raw object retained to future-proof against needing a
   field not extracted up front.

4. **Checkpoint / resume** — per-handle progress record so a suspension, rate-limit
   wall, or crash restarts where it stopped rather than from zero.

5. **Flatten step** — SQLite → tidy Parquet/CSV:
   `tweet_id, handle, created_at, text, lang, like_count, retweet_count, reply_count,
   is_retweet, is_reply, conversation_id`. Separate script; never triggers scraping.

6. **Run log + validation** — record accounts that hit limits; flag handles returning
   empty (deleted / suspended / renamed); spot-check collected counts against reality.

## Inputs (user-provided)

- `politicians.csv` — `handle, name, party, country` (the sampling frame; also the
  reproducibility record).
- Cookies for 5 dedicated X accounts (never personal/institutional).
- Bounded-window `since` / `until` dates.

## Out of scope

- Full-lifetime historical archives (would need DSA data-access or purchased data).
- Media/attachment downloading (text + metadata only for v1).
- Real-time streaming (batch collection only).

## Open questions for user review

- Confirm the exact `since`/`until` window.
- Confirm SQLite as the store (vs JSONL) — SQLite chosen for idempotent resume.
- Confirm text-only (no media) for v1.
