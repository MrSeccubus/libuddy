---
name: libuddy-status
description: Read-only summary of libuddy state - pending proposals, requests awaiting a reply with days remaining, overdue follow-ups, and recent action log. Use when the user says /libuddy-status or asks how the LinkedIn triage is going. Never touches the browser or LinkedIn.
---

# libuddy status

Read-only. Do not open Chrome, do not touch LinkedIn, do not modify any file.

Fastest path: run **`bin/libuddy.py status`** — it prints status counts,
awaiting-reply deadlines (with days left / overdue flags), replies-received
("your turn"), open proposals by classification, and the recent log. Relay and
enrich its output. The manual steps below are the fallback / detail spec.

1. Read `config.json` and `state/requests.json`. If the state file is missing or
   `{}`, say so and stop.
2. Print, using today's date:
   - **Needs decision**: records with `status: proposed` or `status: manual`, and
     records with `reply_received: true` and no `final_outcome` — name,
     classification, confidence, rationale / reply summary.
   - **Awaiting reply**: records with `status: replied`, `reply_received: false`
     — name, template sent, `action_date`, days until `followup_deadline`
     (negative = overdue, mark clearly as due for decline on next run).
   - **Recently closed**: last 5 records with a `final_outcome`, with outcome and
     date.
   - **Totals**: counts per status and per classification, plus proposed-vs-
     approved ratio per category if derivable from `state/log.md` (useful
     evidence for enabling `auto_mode` flags).
3. Print the last 10 lines of `state/log.md`.
4. If any `auto_mode` flag is true in `config.json`, mention which ones, so it is
   never a surprise.
