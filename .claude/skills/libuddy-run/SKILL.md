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
- **Thread, last is from THEM** → split by whether **Frank ever sent a message
  in the thread**:
  - **Frank participated** (he replied earlier, they answered) → **Your turn**:
    unchecked, thread link, Frank answers personally. Never auto-message.
  - **Inbound-only** (every message is theirs, Frank never wrote back — e.g. an
    invite note / cold pitch that auto-created a thread) → there is NO
    double-message risk, so classify it like a normal candidate **by the note**:
    a clear pitch → `rebuf-*`/`recruit-*` (checked); otherwise `ask-*`. This is
    the Dhishan Ramdas case: all messages came from them, it's a vendor pitch →
    it should get a rebuf, NOT sit in "your turn".
  Determining "did Frank ever send" needs the thread's messages
  (`messengerMessages` endpoint — re-discover its queryId if it 4xx's, it was
  400ing on 2026-08-18/19). **If that endpoint is unavailable you CANNOT prove
  a thread is inbound-only, so you must NOT auto-send to anyone with an existing
  thread whose last message is theirs** — even a clear pitch. Frank may have
  already replied and the pitch you see is their follow-up (the Tom Soper /
  watchTowr case, 2026-08-19: last msg was his, libuddy never asked him, so the
  scan wrongly queued a rebuf — but Frank had already answered). Route ALL such
  `lastFromThem` items to **"They messaged you — your turn"** with the thread
  link, unchecked. Reserve the inbound-only → rebuf/ask path for when the
  messages endpoint positively confirms Frank never sent in that thread.
This is the CLAUDE.md rule-1 guarantee (never message the same person twice) —
any thread where FRANK has sent keeps the item out of auto-send. Frank can
override any line by changing the action word + checkbox.

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

1. `bin/libuddy.py plan` parses `state/decisions.md` → JSON with `execute` /
   `deferred` / `skipped` lists. It already applies the never-twice safety gate
   (drops anything with a prior `sent_template`/`accepted`/`declined` in state
   into `deferred`) and maps each action word to its template. Work the
   `execute` list; unchecked lines and `skip` are handled for you.
2. (The safety gate is in `plan`, but still re-check per item before a send.)
3. Freshness: if the decisions file predates the newest scan, or an item's
   invite no longer exists (quick Voyager re-fetch), record
   `invite_withdrawn` and skip it.

## A1 — Execute (real UI, 3s+ between actions, no cap)

**Hard-won mechanics (2026-08-18 run — follow these, they prevent real bugs):**
- **JS calls have a ~45s ceiling.** Keep each browser call short: do at most a
  few state-changing actions (or one send) per call, then verify. A batch with
  long internal `setTimeout` waits will time out mid-way and you won't know what
  landed — so **verify via Voyager after every batch** (a declined/accepted
  invite disappears from the `PENDING` list; a sent message's dated footer
  appears in the thread). Never trust a timed-out call's outcome.
- **Virtualized list:** the invitation list only keeps ~30 cards in the DOM.
  To reach a card, scroll `main` in small increments (≈330px) from the top,
  checking each step — jumping to the bottom skips the middle. Accented slugs
  are URL-encoded in hrefs (`célia` → `c%C3%A9lia`); match on the encoded form.
- **Compose targeting (the fragile part):** open ONLY via the card's
  "Reply to <name>" / "Message" link — **never a `/messaging/compose/` URL
  (paid InMail, forbidden).** Then pick the compose box that is (a) a
  `contenteditable` whose ancestor text contains the recipient's exact name AND
  a compose marker (`requested to connect` / `New message` / `Write a message`),
  AND (b) **actually in the viewport** (`0 < rect.y < window.innerHeight-30`,
  `height > 15`). Off-screen/hidden duplicate boxes exist (from docked chat
  widgets) and are the #1 cause of mis-targeting — the viewport filter is what
  disambiguates. If the compose text mentions InMail → abort, mark `manual`.
- **Insert** via `document.execCommand('insertText')` (never keystrokes — Enter
  sends), then **dispatch an `input` event** on the box or the Send button
  stays disabled. Clear the box first (`selectAll`+`delete`) to kill any stray
  draft.
- **Send:** click the enabled `Send` button that is in the viewport. Post-send
  the box detaches, so a "did the dated footer appear" DOM scan is unreliable —
  screenshot-verify periodically (recipient header + message) and rely on the
  Voyager pending-count drop / message presence for the rest.
- **Between sends:** press Escape (or click the overlay's header ✕) to close the
  overlay, so overlays/boxes don't stack. Docked minimized chat bubbles have no
  clean close and cause both duplicate boxes and a "Leave site? unsaved changes"
  dialog on reload — if they pile up, clear all draft boxes then reload the page
  (re-inject the Voyager csrf/token, templates, and helpers afterward).
- **Renderer freezes** after long sessions; a page reload clears the state and
  restores responsiveness.

Per approved item:
- **Send template** (`rebuf-*`, `recruit-*`, `ask-*`):
  1. Render template with today's ISO date for `{{DATE}}`.
  2. Scroll-find the card, open its Reply/Message link, insert per the mechanics
     above, verify recipient name in the overlay, Send, dispatch input if Send
     is disabled.
  3. Update record: `status: replied`, `action_taken: sent_template`,
     `template_used`, `action_date`, `thread_url` (if captured),
     `followup_deadline: today + config.followup_window_days`.
  4. **Rebufs (`rebuf-*`/`recruit-*`) also decline the invite immediately**:
     click Ignore, verify the card left pending (Voyager or "Invitation
     ignored.") → `status: declined`, `final_outcome: rebuffed_declined`,
     `followup_deadline: null`. Send+decline = one item. (Never click the
     "I don't know <name>" link — that reports the sender.)
  Wrong-recipient or InMail detected at any point → clear ALL boxes, mark the
  item `manual`, and stop the queue.
- **Accept**: click Accept on the card, verify it left pending →
  `status: accepted`, `final_outcome: accepted`.
- **Decline**: click Ignore, verify → `status: declined`,
  `final_outcome: declined_no_reply` (stale) .

After EVERY verified action (and every failure): record it with the bookkeeping
CLI — do NOT hand-write JSON. One call writes `state/requests.json` and appends
to `state/log.md` crash-safely:

    bin/libuddy.py record <profile-url> <sent|declined|accepted|rebuffed|manual|skip> \
        [--template <file>] [--thread <url>] [--name "Full Name"] [--date YYYY-MM-DD] [--reason "…"]

  - ask-intent send → `sent --template linkedin_assistant[_dutch].txt` (sets
    `replied` + a fresh `followup_deadline`).
  - rebuf send+decline → `rebuffed --template vendor.txt` (sets `declined` +
    `rebuffed_declined`). Then do the Ignore click in the UI.
  - stale/other decline → `declined`; accept → `accepted`; skip → `skip`;
    park for Frank → `manual`.
  `record` warns if the person already has a prior `sent_template` (double-message
  guard). Any verification mismatch → `record … manual`, STOP the queue, report.

## A2 — Apply summary

Per-item outcomes (executed / deferred / skipped / withdrawn / failed),
replies awaiting Frank with thread links, next deadlines, pending-count
before/after. Suggest `git add -A && git commit`.
