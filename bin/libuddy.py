#!/usr/bin/env python3
"""libuddy state bookkeeping CLI.

Deterministic, local-only operations on state/requests.json, state/log.md and
state/decisions.md. This script NEVER touches Chrome, the network, or LinkedIn —
all state-changing LinkedIn actions still go through the verified browser UI.
It just replaces the ad-hoc `python3 - <<PYEOF` heredocs used during a run.

Usage:
  libuddy.py record <profile-url> <sent|declined|accepted|rebuffed|manual|skip>
                    [--template vendor.txt] [--thread <url>] [--name "Full Name"]
                    [--date YYYY-MM-DD] [--reason "one-line note"]
  libuddy.py plan            # parse decisions.md -> JSON action list (checked, minus already-done)
  libuddy.py status          # counts, awaiting-reply deadlines, recent log
  libuddy.py sets            # emit asked / manual / handled slug sets (for the scan diff)

Run from anywhere; paths are resolved relative to the repo root (parent of bin/).
"""
import argparse, datetime, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUESTS = ROOT / "state" / "requests.json"
LOG = ROOT / "state" / "log.md"
DECISIONS = ROOT / "state" / "decisions.md"
CONFIG = ROOT / "config.json"

# decisions-file action word -> template file (mirrors the skill's mapping)
ACTION_TEMPLATE = {
    "rebuf-en": "vendor.txt", "rebuf-nl": "vendor_dutch.txt",
    "recruit-en": "recruiter.txt", "recruit-nl": "recruiter_dutch.txt",
    "ask-en": "linkedin_assistant.txt", "ask-nl": "linkedin_assistant_dutch.txt",
    # tolerated synonyms Frank has typed:
    "vendor-en": "vendor.txt", "vendor-nl": "vendor_dutch.txt",
}
# decisions action word -> the record `kind` executed in the browser
ACTION_KIND = {
    **{a: ("rebuf" if ("rebuf" in a or "recruit" in a or "vendor" in a) else "ask")
       for a in ACTION_TEMPLATE},
    "accept": "accept", "decline": "decline", "manual": "manual", "skip": "skip",
}

FIELDS = ["name", "headline", "first_seen", "classification", "confidence",
          "rationale", "status", "action_taken", "action_date", "template_used",
          "proposed_template", "thread_url", "followup_deadline", "reply_received",
          "reply_summary", "final_outcome", "urn"]


def _today():
    # callers pass --date; default to the OS date (this is bookkeeping, not a run action)
    return datetime.date.today().isoformat()


def _load(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text() or "null") or default


def _save_requests(d):
    REQUESTS.write_text(json.dumps(d, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def _config():
    return _load(CONFIG, {})


def norm_url(u):
    """Normalize a profile URL the way the state store keys it."""
    u = u.strip().split("?")[0].lower()
    if not u.endswith("/"):
        u += "/"
    return u


def slug_of(url):
    return url.rstrip("/").split("/in/")[-1] if "/in/" in url else url.rstrip("/")


def _blank_record(name, url):
    return {"name": name or slug_of(url), "headline": "", "first_seen": _today(),
            "classification": None, "confidence": None, "rationale": None,
            "status": "new", "action_taken": "none", "action_date": None,
            "template_used": None, "proposed_template": None, "thread_url": None,
            "followup_deadline": None, "reply_received": False, "reply_summary": None,
            "final_outcome": None, "urn": None}


def cmd_record(args):
    url = norm_url(args.url)
    d = _load(REQUESTS, {})
    rec = d.get(url) or _blank_record(args.name, url)
    if args.name:
        rec["name"] = args.name
    date = args.date or _today()
    window = _config().get("followup_window_days", 30)

    action = args.action
    # SAFETY: refuse a second outbound message to someone already messaged,
    # unless this call is itself the send (sent/rebuffed) explicitly requested.
    if action in ("sent", "rebuffed") and rec.get("action_taken") == "sent_template":
        print(f"WARNING: {rec['name']} already has a prior sent_template "
              f"(status={rec['status']}). Recording anyway as an explicit override.",
              file=sys.stderr)

    if action == "sent":                      # ask-intent send (invite stays pending)
        deadline = (datetime.date.fromisoformat(date)
                    + datetime.timedelta(days=window)).isoformat()
        rec.update(status="replied", action_taken="sent_template", action_date=date,
                   template_used=args.template, thread_url=args.thread or rec.get("thread_url"),
                   followup_deadline=deadline)
        logword = f"SENT {args.template or 'ask'}"
    elif action == "rebuffed":                # send template + decline invite
        rec.update(status="declined", action_taken="sent_template", action_date=date,
                   template_used=args.template, thread_url=args.thread or rec.get("thread_url"),
                   final_outcome="rebuffed_declined", followup_deadline=None)
        logword = f"SENT {args.template or 'rebuf'} + DECLINED"
    elif action == "declined":                # ignore invite (stale / user choice)
        rec.update(status="declined", action_taken="declined", action_date=date,
                   final_outcome="declined_no_reply", followup_deadline=None)
        logword = "DECLINED"
    elif action == "accepted":
        rec.update(status="accepted", action_taken="accepted", action_date=date,
                   final_outcome="accepted", followup_deadline=None)
        logword = "ACCEPTED"
    elif action == "manual":
        rec.update(status="manual", final_outcome=rec.get("final_outcome") or "handled_manually")
        logword = "MANUAL"
    elif action == "skip":
        rec.update(status="manual", final_outcome="rejected_by_user")
        logword = "SKIP"
    else:
        sys.exit(f"unknown action: {action}")

    if args.reason:
        rec["rationale"] = args.reason

    d[url] = rec
    _save_requests(d)
    with LOG.open("a") as f:
        extra = f" — {args.reason}" if args.reason else ""
        f.write(f"{date} {logword} {rec['name']} ({url}) [approved via decisions.md]{extra}\n")
    print(f"recorded: {rec['name']} -> status={rec['status']} outcome={rec['final_outcome']}")


DECISION_RE = re.compile(
    r"^- \[(?P<check>[ xX])\]\s*(?P<action>\S+)\s*\|\s*\[(?P<name>[^\]]+)\]"
    r"\((?P<url>https://www\.linkedin\.com/in/[^)]+)\)")


def cmd_plan(args):
    if not DECISIONS.exists():
        sys.exit("no state/decisions.md — run a scan first")
    state = _load(REQUESTS, {})
    checked, deferred, skipped = [], [], []
    for line in DECISIONS.read_text().splitlines():
        m = DECISION_RE.match(line)
        if not m:
            continue
        action = m.group("action")
        url = norm_url(m.group("url"))
        item = {"name": m.group("name"), "url": url, "action": action,
                "kind": ACTION_KIND.get(action, "?"),
                "template": ACTION_TEMPLATE.get(action)}
        if m.group("check").lower() != "x":
            deferred.append(item); continue
        if action == "skip":
            skipped.append(item); continue
        # safety gate: drop anything already terminally acted on in state
        rec = state.get(url)
        if rec and rec.get("action_taken") in ("sent_template", "accepted", "declined"):
            item["note"] = f"ALREADY {rec['action_taken']} — skipped by safety gate"
            deferred.append(item); continue
        checked.append(item)
    out = {"execute": checked, "deferred": deferred, "skipped": skipped,
           "counts": {"execute": len(checked), "deferred": len(deferred),
                      "skipped": len(skipped)}}
    print(json.dumps(out, indent=1, ensure_ascii=False))


def cmd_status(args):
    d = _load(REQUESTS, {})
    if not d:
        print("state/requests.json is empty."); return
    from collections import Counter
    today = datetime.date.today()
    by_status = Counter(r["status"] for r in d.values())
    by_class = Counter(r.get("classification") for r in d.values() if r.get("status") in ("proposed", "new"))
    print("== status ==")
    for k, v in sorted(by_status.items()):
        print(f"  {v:3d}  {k}")
    # awaiting reply, sorted by days remaining
    awaiting = [(r["name"], r.get("followup_deadline")) for r in d.values()
                if r["status"] == "replied" and not r.get("reply_received") and r.get("followup_deadline")]
    if awaiting:
        print("\n== awaiting reply (deadline / days left) ==")
        for name, dl in sorted(awaiting, key=lambda x: x[1] or "9999"):
            days = (datetime.date.fromisoformat(dl) - today).days
            flag = "  <-- OVERDUE, sweep to decline" if days < 0 else ""
            print(f"  {dl}  ({days:+d}d)  {name}{flag}")
    # replies received awaiting Frank
    got = [r["name"] for r in d.values() if r.get("reply_received") and not r.get("final_outcome")]
    if got:
        print("\n== replies received — your turn ==")
        for n in got:
            print(f"  {n}")
    if by_class:
        print("\n== open proposals by classification ==")
        for k, v in sorted(by_class.items(), key=lambda x: str(x[0])):
            print(f"  {v:3d}  {k}")
    if LOG.exists():
        print("\n== recent log ==")
        for line in LOG.read_text().splitlines()[-10:]:
            print("  " + line)


def cmd_sets(args):
    d = _load(REQUESTS, {})
    asked, manual, handled = [], [], []
    for url, r in d.items():
        s = slug_of(url)
        if r["status"] == "replied" and (r.get("template_used") or "").startswith("linkedin_assistant"):
            asked.append(s)
        if r["status"] == "manual":
            manual.append(s)
        if r.get("action_taken") in ("sent_template", "accepted", "declined"):
            handled.append(s)
    print(json.dumps({"asked": sorted(asked), "manual": sorted(manual),
                      "handled": sorted(handled)}, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(prog="libuddy.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="write one executed action to state + log")
    r.add_argument("url")
    r.add_argument("action", choices=["sent", "declined", "accepted", "rebuffed", "manual", "skip"])
    r.add_argument("--template"); r.add_argument("--thread"); r.add_argument("--name")
    r.add_argument("--date"); r.add_argument("--reason")
    r.set_defaults(func=cmd_record)

    sub.add_parser("plan", help="parse decisions.md into an action list").set_defaults(func=cmd_plan)
    sub.add_parser("status", help="counts, deadlines, recent log").set_defaults(func=cmd_status)
    sub.add_parser("sets", help="emit asked/manual/handled slug sets").set_defaults(func=cmd_sets)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
