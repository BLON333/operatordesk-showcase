"""Wires inbox -> memory -> draft -> approval gate -> send -> ledger."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .drafting import ClaudeDrafter, RuleDrafter
from .gate import Approval, ApprovalError, approve, draft_hash, send
from .ledger import Ledger
from .memory import ClientHistory, build_history
from .models import Draft, Email

FIRM_ADDRESS = "desk@northgate.example"

# A reviewer is anything that looks at (email, history, draft) and returns a
# decision: ("approve", name), ("skip", name), or ("edit", name, new_text) with an
# optional 4th item {"promises", "requests", "fulfils"} the reviewer confirmed for
# the edited text. Without it, an edit drops the drafter's structured fields, so
# memory never records a promise the person may have taken out.
Reviewer = Callable[[Email, ClientHistory, Draft], tuple]

# A clock stamps the steps after an email arrives: (email, minutes_after_arrival) -> ISO time.
Clock = Callable[[Email, int], str]


def wall_clock(email: Email, minutes: int) -> str:
    """Real time: when the step actually happened."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def simulated_clock(email: Email, minutes: int) -> str:
    """Each step a few minutes after the email arrived, so the sample inbox's story
    (sent over several days in September) reads the same whenever you run it."""
    return (datetime.fromisoformat(email.received) + timedelta(minutes=minutes)).isoformat()


DONE = {"sent", "skipped"}


@dataclass
class Outcome:
    email: Email
    history: ClientHistory
    draft: Draft
    decision: str
    sent_to: Path | None = None


def load_inbox(path: Path) -> tuple[dict, list[Email]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return raw, [Email.from_dict(e) for e in raw["emails"]]


class Desk:
    def __init__(self, state_dir: Path, inbox_path: Path, use_claude: bool = False,
                 use_memory: bool = True, clock: Clock = wall_clock):
        self.state = Path(state_dir)
        self.ledger = Ledger(self.state / "ledger.jsonl")
        self.outbox = self.state / "outbox"
        self.meta, self.emails = load_inbox(inbox_path)
        notes = self.meta.get("case_notes", {})
        self.drafter = ClaudeDrafter(notes) if use_claude else RuleDrafter(notes)
        self.use_memory = use_memory
        self.clock = clock

    def processed_ids(self) -> set[str]:
        """Emails with a final decision. One that was received but never decided
        (the reviewer quit halfway) comes back next time."""
        return {e.data["email_id"] for e in self.ledger.entries() if e.kind in DONE}

    def history(self, client: str, before_seq: int | None = None) -> ClientHistory:
        if not self.use_memory:
            return ClientHistory(client)
        return build_history(self.ledger, client, before_seq)

    def process(self, email: Email, reviewer: Reviewer,
                before_send: Callable[[Draft], None] | None = None) -> Outcome:
        # History is read BEFORE this email is logged, so "what we knew when we drafted"
        # is exactly what the ledger held at that moment. If the email was already
        # logged on an earlier, unfinished run, rebuild history as of that entry.
        earlier = next((e for e in self.ledger.for_client(email.client)
                        if e.kind == "received" and e.data["email_id"] == email.id), None)
        if earlier:
            history = self.history(email.client, before_seq=earlier.seq)
        else:
            history = self.history(email.client)
            self.ledger.append(email.client, "received", {
                "email_id": email.id, "from": email.sender, "subject": email.subject,
                "attachments": list(email.attachments)}, email.received)

        draft = self.drafter.draft(email, history)
        self.ledger.append(email.client, "drafted", {
            "email_id": email.id, "drafter": self.drafter.name,
            "draft_sha256": draft_hash(draft), "used_memory": draft.used_memory},
            self.clock(email, 1))

        decision = reviewer(email, history, draft)
        kind, name = decision[0], decision[1]
        if kind == "skip":
            self.ledger.append(email.client, "skipped", {"email_id": email.id, "reviewer": name},
                               self.clock(email, 4))
            return Outcome(email, history, draft, "skipped")
        if kind == "edit":
            draft.text = decision[2]
            kept = decision[3] if len(decision) > 3 else {}
            draft.promises = list(kept.get("promises", []))
            draft.requests = list(kept.get("requests", []))
            draft.fulfils = list(kept.get("fulfils", []))
            self.ledger.append(email.client, "edited", {
                "email_id": email.id, "reviewer": name, "draft_sha256": draft_hash(draft),
                "kept_commitments": bool(kept)}, self.clock(email, 4))

        approval = approve(draft, name, self.clock(email, 4))
        self.ledger.append(email.client, "approved", {
            "email_id": email.id, "reviewer": name,
            "draft_sha256": approval.draft_hash}, approval.ts)
        if before_send:  # used by the demo to simulate a change after approval
            before_send(draft)
        ok, _ = self.try_deliver(draft, approval, self.clock(email, 5))
        if not ok:
            return Outcome(email, history, draft, "blocked")
        return Outcome(email, history, draft, "sent", self.outbox / f"{email.id}.eml")

    def reapprove(self, draft: Draft, reviewer: str, email: Email) -> Path:
        approval = approve(draft, reviewer, self.clock(email, 9))
        self.ledger.append(draft.client, "approved", {
            "email_id": draft.email_id, "reviewer": reviewer,
            "draft_sha256": approval.draft_hash}, approval.ts)
        return self.deliver(draft, approval, self.clock(email, 10))

    def deliver(self, draft: Draft, approval: Approval, ts: str | None = None) -> Path:
        path = send(draft, approval, self.outbox, FIRM_ADDRESS)  # raises if approval is void
        self.ledger.append(draft.client, "sent", {
            "email_id": draft.email_id, "subject": draft.subject,
            "draft_sha256": approval.draft_hash, "approved_by": approval.reviewer,
            "promises": draft.promises, "requests": draft.requests, "fulfils": draft.fulfils,
            "approved_at": approval.ts, "outbox_file": path.name}, ts or approval.ts)
        return path

    def try_deliver(self, draft: Draft, approval: Approval, ts: str) -> tuple[bool, str]:
        try:
            self.deliver(draft, approval, ts)
            return True, "sent"
        except ApprovalError as err:
            self.ledger.append(draft.client, "blocked", {
                "email_id": draft.email_id, "reason": str(err),
                "draft_sha256": draft_hash(draft)}, ts)
            return False, str(err)
