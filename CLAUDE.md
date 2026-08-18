# libuddy

Personal agent that triages Frank's LinkedIn connection requests. It reads pending
invitations via the Claude in Chrome extension (Frank's real, logged-in browser),
classifies each requester, and — after per-item approval — replies with a rebuf
template, accepts, or declines. Templates come from https://github.com/MrSeccubus/rebufs.

## File map

- `config.json` — all tunables: `auto_mode` flags per category, follow-up window,
  rate limits, colleague employers, VIP/Dutch classification criteria.
- `templates/` — vendored rebuf templates. `{{DATE}}` is replaced with today's
  ISO date (YYYY-MM-DD) at send time.
- `state/requests.json` — single source of truth, an object keyed by normalized
  profile URL. Schema documented in `.claude/skills/libuddy-run/SKILL.md`.
- `state/log.md` — append-only human-readable action log.
- `.claude/skills/libuddy-run/SKILL.md` — the main workflow (`/libuddy-run`).
- `.claude/skills/libuddy-status/SKILL.md` — read-only state summary (`/libuddy-status`).
- `launchd/com.fbreedijk.libuddy.plist` — optional scheduled scan-and-notify nudge.

## Hard safety rules (never violate these)

1. **Never message the same profile URL twice.** Before any send, look the
   normalized URL up in `state/requests.json`; if a record exists with any
   send/accept/decline already taken, only follow-up-sweep actions are allowed.
   Re-invites from previously handled people are surfaced as `manual`.
2. **No LinkedIn state change without approval.** Unless `auto_mode[<category>]`
   is `true` in `config.json` AND classification confidence is `high`, every
   accept/send/decline requires an explicit per-item user approval in the
   current session. `dry_run: true` overrides everything: never perform the
   final state-changing click.
3. **Write state immediately after each executed action**, not at the end of
   the run. A crashed run must never cause a duplicate send on the next run.
4. **Screenshot-verify every state-changing click** (before and after). If the
   after-screenshot doesn't show the expected outcome, mark the item `manual`,
   stop executing further actions, and report to the user. Never guess or retry
   blindly.
5. **Human-paced**: at most `max_actions_per_run` state-changing actions per
   run, at least `min_seconds_between_actions` between them, no rapid-fire
   navigation.
6. **No credentials anywhere in this repo.** Browser access uses Frank's live
   Chrome session via the extension only.
7. **Never spend InMail credits.** Never compose via direct
   `/messaging/compose/` URLs or any interface that mentions InMail credits —
   those route as paid InMail. Messages to invite senders are free ONLY via
   the invitation card's "Reply to <name>" / "Message" links on the
   invitation manager. If the only available compose path mentions InMail,
   stop and mark the item `manual`.
8. `state/requests.json` and `state/log.md` contain third-party personal data.
   The GitHub repo is PUBLIC, so these files are gitignored and must NEVER be
   committed or pushed. Before any push, verify `git ls-files state/` shows
   only `state/README.md`.
