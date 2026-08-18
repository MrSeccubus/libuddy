---
name: libuddy-run
description: Triage LinkedIn connection requests via fast in-browser Voyager API reads and a decisions file. Default mode SCAN reads all pending invites + replies and writes state/decisions.md with recommendations; mode "apply" bulk-executes the checked decisions via verified UI clicks. Use when the user says /libuddy-run, /libuddy-run apply, or asks to process LinkedIn invites.
---

# libuddy run (v2)

Two modes. `/libuddy-run` (or `scan`) = SCAN: read everything fast, classify,
write `state/decisions.md`. `/libuddy-run apply` = APPLY: execute what Frank
checked in that file. The hard safety rules in the repo root `CLAUDE.md`
override everything here; re-read them before starting.

Core split: **reads go through the in-browser Voyager API (GET only), writes go
through the real UI with screenshot verification.** Never mix these up.

## State record schema (`state/requests.json`)

Object keyed by **normalized profile URL** (lowercase, scheme+host+path only,
strip query string, keep trailing slash). Fields: `name`, `headline`,
`first_seen`, `classification` (`vendor|recruiter|unclear_en|unclear_nl|accept|manual`),
`confidence` (`high|medium|low`), `rationale`, `status`
(`new|proposed|replied|accepted|declined|closed|manual`), `action_taken`
(`sent_template|accepted|declined|none`), `action_date`, `template_used`,
`proposed_template`, `thread_url`, `followup_deadline`, `reply_received`,
`reply_summary`, `final_outcome`
(`declined_no_reply|rebuffed_declined|accepted|accepted_after_reply|handled_manually|rejected_by_user|invite_withdrawn|null`),
plus `urn` (the `ACoAA...` recipient id from Voyager — needed for compose
verification). Keep the file pretty-printed with sorted keys.

# SCAN mode (default — read-only, zero LinkedIn writes)

## S0 — Preflight

1. Read `config.json`, `state/requests.json` (missing → `{}`), and
   `state/voyager-endpoints.json` (missing → discovery needed).
2. Invoke the `claude-in-chrome` skill; verify browser access with a screenshot.
   Not available → stop with a clear message.
3. Note today's date (ISO).

## S1 — Voyager recipe

The confirmed working recipe lives in `state/voyager-endpoints.json`
(gitignored). Use it directly. Only re-discover (via
`mcp__claude-in-chrome__read_network_requests` with pattern `voyager` while
loading the invitation manager / messaging pages) if a saved endpoint returns
4xx/5xx — LinkedIn rotates the messaging graphql `queryId` hashes periodically.
**GET only — never POST/PUT/DELETE to voyager endpoints (CLAUDE.md rule 2a).**

The `csrf-token` header = the `JSESSIONID` cookie value (quotes stripped). Note
the extension's data filter blocks any JS result that echoes cookies or query
strings — have `fetch()` code stash results in a `window.__x` var and read back
only sanitized counts/fields in a follow-up call.

## S2 — Fetch all pending invitations

In-page `fetch()` per the recipe: `invitationViews?start=N&count=100&q=receivedInvitation`,
paginate `start` by 100 until a page returns <100 invitations. Entities are in
the top-level `included[]` array (normalized+json). Keep
`$type endsWith 'Invitation' && invitationType==='PENDING'` (other types are
event invites — they count toward the UI total but are not connection
requests). Join `invitation['*fromMember']` → MiniProfile by `entityUrn` for
name (`firstName`+`lastName`), profile URL
(`/in/{publicIdentifier}/`), URN (`objectUrn` tail, `ACoAA...`), headline
(`occupation`). Note text = `customMessage || message` (full, untruncated).
Diff against state:
- Not in state → new record, `first_seen: today`, `status: new`.
- In state with terminal `final_outcome` but pending again (re-invite after
  decline) → `status: manual`, flag prominently; never re-message.
- Sanity check: connection invites + event invites should equal the UI count
  ("All (N)"); if wildly off, stop and report.

## S3 — Message-history check (MANDATORY — prevents double-contact)

Fetch the FULL conversations list (paginate via
`metadata.nextCursor`, recipe in `state/voyager-endpoints.json`) and build a
map of every profile Frank already has a thread with:
`ACoAA-id → thread-id`. **Match invites to threads on the MiniProfile `ACoAA`
entityUrn, NOT the numeric objectUrn** (they differ; using the wrong one
silently matches nothing).

The conversations list includes each thread's **last message** (its `*sender`
and `deliveredAt`). Capture, per thread: `lastFromMe` (does `*sender` contain
Frank's own ACoAA id), `ageDays`, and `isAsk` = does the last message body
match an ask/rebuf signature. **Ask signatures** (Frank's own outgoing asks):
"Frank's AI assistant", "elaborate on the purpose of your invitation", "in my
efforts to get to most out of LinkedIn", "Could you tell us the purpose",
the Dutch equivalents, the "(This reply was sent on …)" / "(Dit antwoord …)"
footers, AND a bare `YYYYMMDD` stamp (Frank's manual asks end with one, e.g.
`20260706`). No per-message endpoint needed — the last message is enough
(the `messengerMessages` endpoint was 400ing on 2026-08-18 anyway).

Then, for EVERY send-candidate (ask/rebuf/accept alike), route by thread state:
- **No thread** → safe; keep in its send section.
- **Thread, last is Frank's ASK, >`followup_window_days` old** → this is a dead
  follow-up: recommend **decline** (checked), with the thread link. (e.g. an
  ask sent 42 days ago, no reply.)
- **Thread, last is Frank's, but NOT an ask** (a genuine conversation gone
  quiet) → **Review**, unchecked — never auto-decline a real conversation.
- **Thread, last is Frank's ask, ≤ window old** → **Awaiting reply**, no action.
- **Thread, last is from THEM** → **Your turn**: they replied or messaged;
  unchecked, with the thread link so Frank answers personally.
This is the CLAUDE.md rule-1 guarantee (never message the same person twice) —
any prior exchange keeps the item out of the auto-send sections. Frank can
still override any line by changing the action word + checkbox.

## S4 — Classify

For every `status: new` record, classify from headline + full note (fetch a
Voyager profile only when genuinely ambiguous):
- `accept` — employer in `config.colleague_employers` or matches
  `config.vip_criteria`.
- `vendor` / `recruiter` — clear pitch or recruitment approach (either
  direction: hiring Frank, or selling recruitment services).
- `unclear_nl` / `unclear_en` — intent unclear; Dutch per `config.dutch_signals`.
- Low confidence on vendor/recruiter/accept → downgrade to `unclear_*`; if
  even that feels wrong → `manual`.

**Template selection — the invite note decides, not the profile:**
- Rebuf templates ONLY when the note itself makes the pitch. Language follows
  the note: `vendor.txt`/`recruiter.txt` (EN) or `vendor_dutch.txt`/
  `recruiter_dutch.txt` (NL).
- No note, or note doesn't clarify intent → ask-intent:
  `linkedin_assistant.txt` / `linkedin_assistant_dutch.txt` (per
  `dutch_signals`). Classification label is still recorded for stats.
Persist all classifications to state now (`status: proposed`).

## S5 — Follow-up sweep

- `status: replied`, `reply_received: false`, `followup_deadline < today` →
  recommend `decline`.
- Reply received → "Replies received" section (S3), never auto-acted.

## S6 — Write `state/decisions.md` (gitignored)

```markdown
# libuddy decisions — generated YYYY-MM-DD HH:MM. Edit, then run: /libuddy-run apply
# Checked = execute. Unchecked = defer to next scan. Change the action word to override.
# Actions: rebuf-en rebuf-nl recruit-en recruit-nl ask-en ask-nl accept decline manual skip

## Accept (colleague / VIP)
- [x] accept    | [Name](profile-url) | accept/high — Schuberg Philis colleague

## Rebufs (send template + decline invite)
- [x] rebuf-en  | [Name](profile-url) | vendor/high — note: "…full pitch quote…"
- [x] recruit-nl| [Name](profile-url) | recruiter/high — note: "…"

## Ask intent (send question, invite stays pending 30 days)
- [x] ask-en    | [Name](profile-url) | vendor/medium — no note, security vendor GTM
- [x] ask-nl    | [Name](profile-url) | unclear_nl/high — no note, NL profile

## Replies received — awaiting YOUR answer (click thread to reply personally)
- [ ] manual    | [Name](profile-url) | replied: "…summary…" | [open thread](https://www.linkedin.com/messaging/thread/<id>/)

## Stale (no reply within window)
- [x] decline   | [Name](profile-url) | ask sent 2026-07-01, no reply

## Needs your eyes (re-invites, oddities)
- [ ] manual    | [Name](profile-url) | re-sent invite after earlier decline
```

Action → execution mapping: `rebuf-en`=vendor.txt, `rebuf-nl`=vendor_dutch.txt,
`recruit-en`=recruiter.txt, `recruit-nl`=recruiter_dutch.txt,
`ask-en`=linkedin_assistant.txt, `ask-nl`=linkedin_assistant_dutch.txt,
`accept`=Accept click, `decline`=Ignore click, `manual`=mark manual (no
LinkedIn action), `skip`=reject (`rejected_by_user`, no LinkedIn action).

## S7 — Scan summary

Counts per section, the decisions-file path, replies awaiting Frank's answer
(with thread links), upcoming deadlines. Remind: edit the file, then
`/libuddy-run apply`. Scan itself never touches LinkedIn state.

# APPLY mode

## A0 — Parse and re-verify

1. Read `state/decisions.md`. Checked lines = approved
   (`[approved via decisions.md]` in the log); unchecked = defer; `skip` =
   `status: manual`, `final_outcome: rejected_by_user`.
2. Per item, safety gate BEFORE acting: look up the URL in state — any prior
   send/accept/decline → refuse and mark `manual` (never message twice).
3. Freshness: if the decisions file predates the newest scan, or an item's
   invite no longer exists (quick Voyager re-fetch), record
   `invite_withdrawn` and skip it.

## A1 — Execute (real UI, screenshot-verified, 3s between actions, no cap)

- **Send template** (`rebuf-*`, `recruit-*`, `ask-*`):
  1. Render template with today's ISO date for `{{DATE}}`.
  2. Open the compose ONLY via the invitation card's "Reply to <name>" /
     "Message" link on the invitation manager — **never a direct
     /messaging/compose/ URL (routes as paid InMail — forbidden, CLAUDE.md).**
     If the compose UI mentions InMail credits → abort item, mark `manual`.
  3. Target the compose box with **subtree scoping**: find the smallest
     visible container holding BOTH "New message" (or the thread header) AND
     the recipient's name, then the single `contenteditable` INSIDE it.
     Multiple overlays may be open — close others first (Escape closes the
     active overlay). If "Reply to" navigated to the full /messaging/ page,
     the entire UI lives inside the `linkedin.com/preload/` iframe — search
     its contentDocument. Insert via `document.execCommand('insertText')` —
     never keystroke typing (Enter may send). Verify by screenshot: text in
     the box AND the recipient's name in the header. Wrong target → clear
     ALL boxes, retry once, else `manual` + stop the queue.
  4. Screenshot before Send → click Send → verify the message with its date
     line appears in the thread. Capture `thread_url`.
  5. Update record: `status: replied`, `action_taken: sent_template`,
     `template_used`, `action_date`, `thread_url`,
     `followup_deadline: today + config.followup_window_days`.
  6. **Rebufs (`rebuf-*`/`recruit-*`) also decline the invite immediately**:
     click Ignore, verify the card shows "Invitation ignored." →
     `status: declined`, `final_outcome: rebuffed_declined`,
     `followup_deadline: null`. Send+decline = one item. (Never click the
     "I don't know <name>" link — that reports the sender.)
- **Accept**: click Accept on the card, verify it left pending →
  `status: accepted`, `final_outcome: accepted`.
- **Decline**: click Ignore, verify → `status: declined`,
  `final_outcome: declined_no_reply` (stale) .

After EVERY action (and every failure): write `state/requests.json`
immediately, append one line to `state/log.md`
(`YYYY-MM-DD HH:MM SENT <template> to <name> (<url>) [approved via decisions.md]`).
Any verification mismatch → mark item `manual`, STOP the remaining queue,
report.

## A2 — Apply summary

Per-item outcomes (executed / deferred / skipped / withdrawn / failed),
replies awaiting Frank with thread links, next deadlines, pending-count
before/after. Suggest `git add -A && git commit`.
