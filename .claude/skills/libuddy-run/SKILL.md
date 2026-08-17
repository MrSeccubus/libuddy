---
name: libuddy-run
description: Triage LinkedIn connection requests - scan pending invites via Claude in Chrome, classify requesters (vendor/recruiter/unclear/accept), propose actions for approval, execute approved replies/accepts/declines, and sweep for overdue follow-ups. Use when the user says /libuddy-run, asks to process LinkedIn invites, or wants to check connection requests. Optional argument "scan-only" stops after classification without proposing execution.
---

# libuddy run

Process Frank's pending LinkedIn connection requests end-to-end. Follow the phases
in order. The hard safety rules in the repo root `CLAUDE.md` override everything
in this file; re-read them before starting.

Argument: if the user invoked this with `scan-only`, stop after Phase 4 — persist
classifications as `status: proposed`, print the summary, and do not present the
approval queue or execute anything.

## State record schema (`state/requests.json`)

Object keyed by **normalized profile URL** (lowercase, scheme+host+path only,
strip query string and trailing junk, keep trailing slash):

```json
"https://www.linkedin.com/in/jane-example/": {
  "name": "Jane Example",
  "headline": "Account Executive at SecVendor",
  "first_seen": "2026-08-17",
  "classification": "vendor",
  "confidence": "high",
  "rationale": "Sales title at security vendor, invite note pitches a demo",
  "status": "replied",
  "action_taken": "sent_template",
  "action_date": "2026-08-17",
  "template_used": "vendor.txt",
  "thread_url": "https://www.linkedin.com/messaging/thread/...",
  "followup_deadline": "2026-09-16",
  "reply_received": false,
  "reply_summary": null,
  "final_outcome": null
}
```

- `classification`: `vendor` | `recruiter` | `unclear_en` | `unclear_nl` | `accept` | `manual`
- `status`: `new` (seen, not yet classified) | `proposed` (classified, awaiting user
  decision) | `replied` (template sent, awaiting their response) | `accepted` |
  `declined` | `closed` | `manual` (needs Frank's personal attention)
- `action_taken`: `sent_template` | `accepted` | `declined` | `none`
- `final_outcome`: `declined_no_reply` | `accepted` | `accepted_after_reply` |
  `handled_manually` | `rejected_by_user` | null while open
- Keep the file pretty-printed with sorted keys so diffs stay readable.

## Phase 0 — Preflight

1. Read `config.json` and `state/requests.json` (treat a missing/empty state file
   as `{}`).
2. Invoke the `claude-in-chrome` skill, then verify browser access works (e.g.
   take a screenshot of the current tab). If Chrome or the extension is
   unavailable or linkedin.com permission is not granted, stop immediately with
   a clear message. Take no LinkedIn actions.
3. Note today's date (ISO, YYYY-MM-DD). Initialize the action budget from
   `config.max_actions_per_run`.

## Phase 1 — Scan invitations (read-only)

1. Navigate to `https://www.linkedin.com/mynetwork/invitation-manager/` and
   screenshot.
2. For every pending invitation, capture: name, headline, profile URL
   (normalized), the invitation note if present (and note explicitly when there
   is NO note — that matters in Phase 6), and how long ago it was sent.
   Scroll/paginate until the list is exhausted.
   **Always expand a note's "show more" and capture the FULL text** — a
   vendor/recruiter rebuf may only ever be proposed on the strength of the
   complete note, never on a truncated opener plus a job title.
3. Diff against state:
   - URL not in state → add a record with `first_seen: today`, `status: new`.
   - URL in state with a terminal `final_outcome` but appearing again as a fresh
     invite (they re-sent after being declined) → set `status: manual` and flag
     for the user; never re-message (CLAUDE.md rule 1).
   - URL in state and still pending → no change.

## Phase 1b — Backfill pre-libuddy replies (read-only, first runs)

Frank replied to some pending invites manually before libuddy existed. For
pending invites NOT yet in state (prioritize the oldest), click the card's
"Message" / "Reply to <name>" link on the invitation manager — the compose
overlay that opens shows the full existing message history with that person
(empty box = no history). If the thread contains a prior reply from Frank:
backfill the record as
`status: replied`, `action_taken: sent_template`, `template_used: "manual"`,
`action_date` = the date of Frank's message (visible in the thread),
`followup_deadline` = that date + `config.followup_window_days`, and capture
`thread_url`. These records then flow through Phases 2 and 4 normally.
Frank's manual replies end with a bare `YYYYMMDD` date stamp (e.g. `20260223`);
libuddy's templates end with "(This reply was sent on YYYY-MM-DD.)" — recognize
both when dating a reply.

## Phase 2 — Check replies (read-only)

For each record with `status: replied` and `reply_received: false`:

1. Open its `thread_url` (fall back to searching the person's name in
   `https://www.linkedin.com/messaging/` if `thread_url` is missing).
2. If the requester sent anything after our dated template message: set
   `reply_received: true`, write a one-line `reply_summary`, and flag the record
   for user attention in Phase 5. Do not act on replies automatically.

## Phase 3 — Classify new invites (read-only)

For each `status: new` record, oldest first:

1. Open the requester's profile. Read headline, current employer, about section,
   and recent activity, together with the invitation note.
2. Classify:
   - `accept` — current employer matches `config.colleague_employers`, or the
     person matches `config.vip_criteria`.
   - `vendor` — clear sales pitch (sales/BD/AE title, vendor pitch in the note).
   - `recruiter` — recruiter by title or note (both "hiring you" and "selling
     recruitment services").
   - `unclear_nl` — intent unclear and the person appears Dutch-speaking per
     `config.dutch_signals`.
   - `unclear_en` — intent unclear, everyone else.
3. Record `confidence` (high/medium/low) and a one-line `rationale`.
4. Confidence downgrade rule: low confidence on `vendor`/`recruiter`/`accept` →
   reclassify as the appropriate `unclear_*` (asking intent is the safe
   default). If even that feels wrong → `classification: manual`.
5. **Template selection — the invite note decides, not the profile:**
   - `vendor.txt` / `recruiter.txt` ONLY when the invitation **note itself**
     clearly makes the sales pitch or recruitment approach. Frank never sends a
     rebuf to someone who hasn't actually pitched anything yet.
   - **Language follows the note.** A Dutch invite note gets the Dutch variant
     (`vendor_dutch.txt` / `recruiter_dutch.txt`); an English (or other) note
     gets the English one. For no-note invites, language comes from
     `config.dutch_signals` (this already selected between the two
     `linkedin_assistant` variants).
   - Invite **without a note** (or with a note that doesn't clarify intent) →
     always the AI-assistant ask-intent template, regardless of how vendor-ish
     the profile looks: `linkedin_assistant.txt` (English) or
     `linkedin_assistant_dutch.txt` (Dutch per `config.dutch_signals`). The
     classification label (vendor/recruiter/unclear) is still recorded — it
     drives stats and `auto_mode` — but the message asks for their reason.
   - `linkedin.txt` / `linkedin_dutch.txt` are Frank's personal versions; the
     agent sends the `_assistant` variants, which identify the sender as
     Frank's AI assistant.
   Record the chosen template in the record as `proposed_template`.
6. Set `status: proposed`. Persist all classifications to `state/requests.json`
   now (classification is not a LinkedIn state change, so this is always safe).

## Phase 4 — Follow-up sweep

- Records with `status: replied`, `reply_received: false`, and
  `followup_deadline` before today → add to the proposal queue as **decline
  stale** (proposed `final_outcome: declined_no_reply`).
- Records flagged in Phase 2 with `reply_received: true` → add to the queue as
  **reply received — needs decision**, with the reply summary and a suggested
  handling (e.g. "reply sounds like a genuine peer, consider accepting"). These
  are always decided by the user, never auto-executed regardless of `auto_mode`.

**If invoked with `scan-only`: stop here.** Print the Phase 7 summary and exit.

## Phase 5 — Present proposal queue (interactive)

1. Print a numbered table of every open item: name, classification, confidence,
   one-line rationale, proposed action, template (if any). **Always render the
   name as a clickable markdown link to the profile URL** — same in the Phase 7
   summary — so Frank can review any profile manually before deciding.
2. For each item, get an explicit decision via AskUserQuestion (batch related
   items into one call where sensible, up to 4 questions per call):
   - **Approve** — execute the proposed action.
   - **Edit** — user changes the category/template or tweaks the message text.
   - **Reject** — take no LinkedIn action; set `status: manual`,
     `final_outcome: rejected_by_user`.
   - **Defer** — leave `status: proposed`; it reappears next run.
3. Exception per `auto_mode`: items whose category flag is `true` in
   `config.auto_mode` AND whose confidence is `high` skip the question and are
   treated as approved (log them as `[auto]`). Medium/low confidence always asks,
   regardless of flags.

## Phase 6 — Execute approved actions

Respect the action budget and wait at least `config.min_seconds_between_actions`
between state-changing actions. If `config.dry_run` is true, do everything below
except the final state-changing click, and log each item as `DRY-RUN would have …`.

For each approved item:

- **Send template** (vendor/recruiter/unclear):
  1. Render the template from `templates/<name>.txt`, replacing `{{DATE}}` with
     today's ISO date.
  2. On the invitation, use the Reply/Message affordance for the invite. **If
     the invite was sent without a note, LinkedIn offers no reply option** — do
     not improvise; ask the user whether to decline the invite instead or mark
     it `manual`.
  3. **Target the right compose box.** Multiple message overlays can be open at
     once, and "first contenteditable in the DOM" may belong to a DIFFERENT
     person's window. Note: clicking "Reply to <name>" may navigate to the full
     /messaging/ page instead of an overlay; there the entire messaging UI
     (including the compose box and the "<name> requested to connect" header)
     lives INSIDE the `linkedin.com/preload/` iframe — search that iframe's
     contentDocument, not the top document. The compose URL's `recipient=` URN
     must match the one captured for this person during the Phase 1 scan. Before inserting text: close every other message overlay,
     then locate the compose box strictly INSIDE the overlay whose header shows
     the intended recipient's name. After inserting, screenshot and verify BOTH
     that the text is in the box AND that the overlay header names the intended
     recipient. Wrong window → clear the box, close overlays, retry once; if it
     still mis-targets, mark the item `manual` and stop.
  4. Screenshot BEFORE clicking Send, send, screenshot AFTER and verify the
     message (with its date line) appears in the thread.
  5. Capture the messaging `thread_url`. Update the record: `status: replied`,
     `action_taken: sent_template`, `action_date: today`, `template_used`,
     `thread_url`, `followup_deadline: today + config.followup_window_days`.
  6. **Rebufs also decline the invite.** A vendor/recruiter rebuf is a
     definitive no — immediately after the verified send, click Ignore on the
     invitation (screenshot-verify it left the pending list) and set
     `status: declined`, `final_outcome: rebuffed_declined`,
     `followup_deadline: null`. The 30-day window (invite stays pending,
     Phase 4 sweeps it) is ONLY for ask-intent messages
     (`linkedin_assistant*`), where a good answer may still lead to an
     accept. Send+decline is one queue item and counts as one action.
- **Accept**: click Accept on the invitation, screenshot-verify it left the
  pending list. Update: `status: accepted`, `action_taken: accepted`,
  `action_date`, `final_outcome: accepted`.
- **Decline stale / decline invite**: click Ignore on the invitation,
  screenshot-verify it left the pending list. Update: `status: declined`,
  `action_taken: declined`, `action_date`, `final_outcome`
  (`declined_no_reply` for stale sweeps).

After EVERY executed action (including dry-run and failures):

1. Write `state/requests.json` immediately (CLAUDE.md rule 3).
2. Append one line to `state/log.md`:
   `2026-08-17 14:32 SENT vendor.txt to Jane Example (https://www.linkedin.com/in/jane-example/) [approved by user]`
   — use `[auto: <category>]` for auto_mode actions, `DRY-RUN`/`FAILED` prefixes
   as applicable.

If any screenshot verification fails: mark that item `manual`, stop executing
the remaining queue, and report what happened.

## Phase 7 — Summary

Report to the user:

- New invites found and their classifications.
- Actions executed / skipped / deferred / failed.
- Replies received (with summaries) and what was decided.
- Upcoming follow-up deadlines (next 7 days).
- Remind: `git add -A && git commit` in this repo to snapshot the state change.
