# libuddy

A personal Claude Code agent that triages LinkedIn connection requests the way I
do manually: vendors and recruiters get a polite rebuf from
[MrSeccubus/rebufs](https://github.com/MrSeccubus/rebufs), unclear requests get a
"why do you want to connect?" message (English or Dutch), colleagues and genuine
VIPs get accepted, and anyone who doesn't respond to a rebuf within a month gets
declined.

It drives my real, logged-in Chrome via the **Claude in Chrome** extension — no
stored credentials, no unofficial APIs, human-paced.

> **Privacy note:** this public repo contains code only. `state/requests.json`
> and `state/log.md` hold third-party personal data and are gitignored —
> they live exclusively on the machine that runs libuddy.

## Requirements

- Claude Code with the Claude in Chrome extension installed, Chrome running,
  and the extension granted permission for `linkedin.com`.
- Logged in to LinkedIn in that Chrome profile.

## Usage

```
cd ~/repos/libuddy && claude
/libuddy-run            # full run: scan → classify → approve each item → execute
/libuddy-run scan-only  # read-only: scan + classify + persist proposals, no actions
/libuddy-status         # read-only state summary, no browser needed
```

Run it a few times per week. Every action requires per-item approval until you
flip `auto_mode` flags in `config.json` (Phase 2 — start with `vendor`, keep
`accept` manual longest). `dry_run: true` in `config.json` makes a run do
everything except the final click.

After a run, commit: `git add -A && git commit -m "libuddy run $(date +%F)"` —
every state change stays a reviewable diff.

## Optional: scheduled nudge (launchd)

`launchd/com.fbreedijk.libuddy.plist` runs a read-only `scan-only` pass Monday
and Thursday at 09:30 and posts a macOS notification with the result. Install:

```
cp launchd/com.fbreedijk.libuddy.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.fbreedijk.libuddy.plist
```

Uninstall: `launchctl bootout gui/$UID/com.fbreedijk.libuddy`.

If the headless Chrome-extension bridge proves unreliable, degrade: edit the
plist's ProgramArguments to just the `osascript` notification line ("time to run
libuddy") — a reminder always works.

## Resetting state

- Forget one person: delete their key from `state/requests.json`. **Careful:**
  the state file is what prevents double-messaging someone.
- Full reset: `echo '{}' > state/requests.json` and truncate `state/log.md`.

## First-run verification (do these in order)

1. Set `dry_run: true`, run `/libuddy-run`: check classifications, approve flow,
   and that LinkedIn is untouched afterwards; inspect the `requests.json` diff.
2. Run `/libuddy-run scan-only` on consecutive days: second run must add no
   duplicate records.
3. Set `dry_run: false`, `max_actions_per_run: 1`; approve exactly one send to
   the safest obvious vendor (or a test invite from a spare account); verify the
   dated message in the thread and that `thread_url`/`followup_deadline` were
   recorded.
4. Hand-edit that record's `followup_deadline` to yesterday; next run must
   *propose* (not execute) the decline.
5. Have the test account reply; next run must flag `reply_received` instead of
   declining.
6. Restore `max_actions_per_run: 10`.

## Templates

Vendored from [MrSeccubus/rebufs](https://github.com/MrSeccubus/rebufs) (MIT),
with a trailing `{{DATE}}` line that is filled with the send date so the
one-month follow-up window is visible in the thread itself.
