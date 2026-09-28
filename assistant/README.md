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

## 3. Licensed rental (roadmap)
Renting assistant access to customers — metered, authenticated, billed —
needs a small backend:
- API proxy holding the provider key (never expose it client-side)
- Auth (per-customer keys), usage metering, rate limits
- Billing (Stripe or similar), per-seat or per-token pricing

Suggested rental tiers: **Starter** $29/mo (1k messages), **Pro** $99/mo
(10k messages + priority), **Enterprise** custom (SSO, SLA, private deploy).
Build the proxy first; the chat UI in `docs/assistant.html` already speaks
the OpenAI chat-completions format, so it can point at your proxy unchanged.
