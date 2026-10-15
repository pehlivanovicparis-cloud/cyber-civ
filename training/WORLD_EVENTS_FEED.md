# World-Events Feed — web-grounded simulation input

**Status:** live since 2026-10-15. **Cost:** $0.

## What it is

Cyber CIV's civilizations used to react to purely random world events
(`rng.choice(WORLDS)` etc. in `generate_event`). The feed gives them the
real world instead: each event is a genuine recent news story — a breach, an
outage, a regulation, a sanction — mapped onto the sim's schema. The
civilizations read the headline in their decision prompt and react to it.

This is the "$0 tier" of the big-model plan: the 8B local engine keeps making
the high-frequency decisions for free, while the *world it lives in* comes
from the open web.

## How it works

1. **Feed file:** `cyber_civ/world_events.jsonl` (repo root, gitignored —
   refreshed data is never committed).
2. **Consumer:** `generate_event()` in `cyber_civ.py` pops the first line
   (FIFO) via `_pop_feed_event()`. Missing/empty/malformed feed → falls back
   to the old random generation. The sim never breaks because the feed is
   stale.
3. **Prompt:** `_decision_prompt()` in `cyber_civ_llm.py` appends
   `Real-world context: <headline> (via <source>)` when the event carries one.
4. **Refresh:** a weekly scheduled job researches live cyber/tech/finance/
   regulatory news and rewrites the feed with ~10 fresh events. Events older
   than the refresh are replaced, not accumulated — the sim always eats
   current news.

## Schema (one JSON object per line)

```json
{
  "world": "crypto | banking | network | governance",
  "country": "US | CN | NG | DE",
  "risk": 0.0,
  "flavor": "regulation | election | sanction | treaty | \"\"",
  "headline": "one neutral factual sentence, <= 140 chars",
  "source": "outlet name",
  "date": "2026-10-15"
}
```

### Mapping rules for the research job

- **world:** crypto → exchange hack, theft, crypto regulation; banking →
  bank outage, fraud wave, rate/policy shock; network → major outage,
  undersea cable, BGP hijack, state-sponsored intrusion; governance → AI
  regulation, sanctions regime, election interference, treaty/pact.
- **country:** map the story's geography to the nearest of US/CN/NG/DE;
  default US for global stories.
- **risk:** 0.85–1.0 major incident (nation-state, billions, critical infra);
  0.6–0.84 significant (large breach, major outage); 0.3–0.59 moderate;
  below 0.3 minor. Calibrate against the sim's old uniform distribution —
  real news skews high, so keep at least 2–3 low-risk items per batch to
  preserve variance.
- **flavor:** only for governance events; "" otherwise.
- **headline:** neutral, factual, no hype words ("massive", "shocking").
  Never invent details not in the reporting.

## Failure modes

- Feed empty mid-run → random fallback, logged nowhere (by design — the
  sim must not halt on missing news).
- Weekly job fails → last feed keeps being consumed until it runs dry, then
  random fallback. No alert needed beyond the normal cron failure path.
