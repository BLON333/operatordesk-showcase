"""Command line: demo, compare, run, verify, history."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from .desk import Desk, Outcome
from .gate import draft_hash
from .ledger import Ledger
from .memory import build_history

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INBOX = ROOT / "data" / "sample_inbox.json"
USE_COLOR = (sys.stdout.isatty() or bool(os.environ.get("FORCE_COLOR"))) and not os.environ.get("NO_COLOR")


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def pretty_client(slug: str) -> str:
    return slug.replace("-", " ").title()


def day(ts: str) -> str:
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{months[int(ts[5:7]) - 1]} {int(ts[8:10])}"


def indent(text: str, prefix: str = "   | ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def pause(seconds: float) -> None:
    if os.environ.get("OPERATORDESK_PACE"):
        time.sleep(seconds)


def show(o: Outcome, text: str | None = None) -> None:
    e = o.email
    print(c(f"\n-- {e.id}  {pretty_client(e.client)} · {e.subject} · {day(e.received)}", "1"))
    print(f"   in:     {e.body}" + (f"  [+ {', '.join(e.attachments)}]" if e.attachments else ""))
    if o.draft.used_memory:
        for note in o.draft.used_memory:
            print(c(f"   memory: {note}", "36"))
    else:
        print(c("   memory: " + ("first contact" if o.history.is_new else "nothing relevant"), "2"))
    print(c("   draft:", "2"))
    print(indent(text or o.draft.text))
    pause(0.6)


def scripted_reviewer(name: str):
    def review(email, history, draft):
        return ("approve", name)
    return review


# -- commands ----------------------------------------------------------------

def cmd_demo(args) -> int:
    state = Path(args.state)
    if state.exists():
        shutil.rmtree(state)
    desk = Desk(state, args.inbox, use_claude=args.claude)
    reviewer_name = desk.meta.get("reviewer", "reviewer")
    print(c("OperatorDesk demo", "1;33") + f" · drafter: {desk.drafter.name}")
    print("Every reply is a draft until a named person approves it. Every step is logged.")
    print(c("(scripted reviewer approves each draft so the demo runs unattended)", "2"))

    approved_text = {}

    def tamper(draft):
        # Simulate someone (or something) changing a draft after it was approved.
        if draft.email_id == "e04" and "$300" in draft.text:
            approved_text[draft.email_id] = draft.text
            draft.text = draft.text.replace("$300", "$250")

    for email in desk.emails:
        outcome = desk.process(email, scripted_reviewer(reviewer_name), before_send=tamper)
        show(outcome, approved_text.get(email.id))
        if outcome.decision == "blocked":
            print(c(f"   gate:   approved by {reviewer_name}; then someone changed $300 to $250 before it went out", "33"))
            print(c("   gate:   BLOCKED - draft changed after it was approved; approval is void", "1;31"))
            path = desk.reapprove(outcome.draft, reviewer_name, email.received)
            print(c(f"   gate:   sent only after {reviewer_name} re-approved the $250 version -> {path.name}", "32"))
        else:
            sha = draft_hash(outcome.draft)[:10]
            print(c(f"   gate:   approved by {reviewer_name} · sha256 {sha} -> sent {email.id}.eml", "32"))

    ok, msg = desk.ledger.verify()
    head = desk.ledger.last()
    print(c("\n-- audit", "1"))
    print(("   " + c("OK ", "32") if ok else "   " + c("FAIL ", "31")) + msg)
    print(f"   head hash: {head.hash[:16]}… (pin this and the ledger can't be silently rewritten)")

    # Tamper test on a copy: change one old entry and show verify catch it.
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "ledger.jsonl"
        lines = desk.ledger.path.read_text(encoding="utf-8").splitlines()
        i = next(n for n, line in enumerate(lines) if '"kind":"approved"' in line)
        lines[i] = lines[i].replace(reviewer_name, "someone else")
        copy.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok2, msg2 = Ledger(copy).verify()
        print("   tamper test (edit an old approval, in a copy): "
              + (c("caught - ", "32") + msg2 if not ok2 else c("NOT DETECTED", "31")))
    print(f"\n   files: {state}/ledger.jsonl and {state}/outbox/*.eml")
    return 0 if ok and not ok2 else 1


def cmd_compare(args) -> int:
    """Draft the same email with and without memory."""
    drafts = {}
    for label, use_memory in (("without memory", False), ("with memory", True)):
        with tempfile.TemporaryDirectory() as tmp:
            desk = Desk(Path(tmp), args.inbox, use_claude=args.claude, use_memory=use_memory)
            reviewer = scripted_reviewer(desk.meta.get("reviewer", "reviewer"))
            for email in desk.emails:
                outcome = desk.process(email, reviewer)
                if email.id == args.email_id:
                    drafts[label] = outcome
                    break
    if not drafts:
        print(f"no email with id {args.email_id}")
        return 1
    e = drafts["with memory"].email
    print(c(f"{e.id}  {pretty_client(e.client)}: \"{e.body}\"", "1"))
    for label, o in drafts.items():
        colour = "31" if label == "without memory" else "32"
        print()
        print(c(f"{label}:", colour))
        print(indent(o.draft.text))
        for note in o.draft.used_memory:
            print(c(f"   memory: {note}", "36"))
    return 0


def cmd_run(args) -> int:
    desk = Desk(Path(args.state), args.inbox, use_claude=args.claude)
    done = desk.processed_ids()
    todo = [e for e in desk.emails if e.id not in done]
    if not todo:
        print("Inbox done. Delete the state folder to start over.")
        return 0
    name = input("Reviewer name: ").strip() or "reviewer"

    def review(email, history, draft):
        print(c(f"\n-- {email.id}  {pretty_client(email.client)} · {email.subject}", "1"))
        print(f"   in: {email.body}")
        for line in history.summary_lines():
            print(c(f"   history: {line}", "36"))
        print(indent(draft.text))
        while True:
            choice = input("   [a]pprove  [e]dit  [s]kip > ").strip().lower()
            if choice == "a":
                return ("approve", name)
            if choice == "s":
                return ("skip", name)
            if choice == "e":
                print("   Type the new reply. End with a line containing only a dot.")
                lines = []
                while (line := input()) != ".":
                    lines.append(line)
                return ("edit", name, "\n".join(lines))

    for email in todo:
        o = desk.process(email, review)
        print(c(f"   -> {o.decision}", "32" if o.decision == "sent" else "33"))
    return 0


def cmd_verify(args) -> int:
    ok, msg = Ledger(Path(args.state) / "ledger.jsonl").verify()
    print(msg)
    return 0 if ok else 1


def cmd_history(args) -> int:
    h = build_history(Ledger(Path(args.state) / "ledger.jsonl"), args.client)
    print(pretty_client(args.client))
    for line in h.summary_lines():
        print(f"  {line}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="operatordesk",
                                description="Approval-gated email drafting with a ledger that doubles as memory.")
    p.add_argument("--inbox", default=str(DEFAULT_INBOX), help="inbox JSON file")
    p.add_argument("--state", default=".operatordesk", help="folder for ledger and outbox")
    p.add_argument("--claude", action="store_true", help="draft with Claude (needs ANTHROPIC_API_KEY)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo", help="run the sample inbox end to end (resets state)")
    cp = sub.add_parser("compare", help="draft one email with and without memory")
    cp.add_argument("email_id")
    sub.add_parser("run", help="review the inbox yourself, one draft at a time")
    sub.add_parser("verify", help="check the ledger's hash chain")
    hp = sub.add_parser("history", help="what the desk remembers about a client")
    hp.add_argument("client")
    args = p.parse_args(argv)
    if args.cmd == "demo" and args.state == ".operatordesk":
        args.state = ".operatordesk-demo"
    return {"demo": cmd_demo, "compare": cmd_compare, "run": cmd_run,
            "verify": cmd_verify, "history": cmd_history}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
