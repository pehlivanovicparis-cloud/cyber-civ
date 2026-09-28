# CYBER CIV Assistant — Deploy Guide

The CYBER CIV assistant (**CIV-GUIDE**) answers questions about the project,
explains the simulation, and routes licensing inquiries. Three ways to deploy:

## 1. Web demo (bring your own key) — live now
`docs/assistant.html` is a chat UI that calls any OpenAI-compatible API
directly from the visitor's browser. The visitor pastes their own API key
(stored only in their browser's localStorage, never sent anywhere except
their chosen API endpoint).

## 2. Hosted platforms (no code)
- **ChatGPT**: create a Custom GPT → paste `SYSTEM_PROMPT.md` as instructions
  → upload `KNOWLEDGE.md` as knowledge.
- **Claude**: create a Project → set `SYSTEM_PROMPT.md` as project
  instructions → add `KNOWLEDGE.md` as project knowledge.
- Any other LLM platform with system prompts + file knowledge works the same.

## 3. Licensed rental (live)
`../rental/` is the metered backend: per-customer API keys, monthly tier
limits, rate limiting, usage ledger, and an OpenAI-compatible endpoint.
The chat UI in `docs/assistant.html` already speaks that format — point its
base URL at your rental server and hand out customer keys instead of
provider keys. See `../rental/README.md` for deploy and `../rental/billing.md`
for Stripe wiring. Suggested rental tiers: **Starter** $29/mo (1k messages),
**Pro** $99/mo (10k messages), **Enterprise** custom (SSO, SLA, private deploy).
