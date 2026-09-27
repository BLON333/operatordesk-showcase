"""Wires inbox -> memory -> draft -> approval gate -> send -> ledger."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .drafting import ClaudeDrafter, RuleDrafter
from .gate import Approval, ApprovalError, approve, draft_hash, send
from .ledger import Ledger
from .memory import ClientHistory, build_history
from .models import Draft, Email

FIRM_ADDRESS = "desk@northgate.example"

# A reviewer is anything that looks at (email, history, draft) and returns a
# decision: ("approve", name), ("edit", name, new_text) or ("skip", name).
Reviewer = Callable[[Email, ClientHistory, Draft], tuple]


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
                 use_memory: bool = True):
        self.state = Path(state_dir)
        self.ledger = Ledger(self.state / "ledger.jsonl")
        self.outbox = self.state / "outbox"
        self.meta, self.emails = load_inbox(inbox_path)
        notes = self.meta.get("case_notes", {})
        self.drafter = ClaudeDrafter(notes) if use_claude else RuleDrafter(notes)
        self.use_memory = use_memory

    def processed_ids(self) -> set[str]:
        return {e.data["email_id"] for e in self.ledger.entries() if e.kind == "received"}

    def history(self, client: str) -> ClientHistory:
        return build_history(self.ledger, client) if self.use_memory else ClientHistory(client)

    def process(self, email: Email, reviewer: Reviewer,
                before_send: Callable[[Draft], None] | None = None) -> Outcome:
        # History is read BEFORE this email is logged, so "what we knew when we drafted"
        # is exactly what the ledger held at that moment.
        history = self.history(email.client)
        self.ledger.append(email.client, "received", {
            "email_id": email.id, "from": email.sender, "subject": email.subject,
            "attachments": list(email.attachments)}, email.received)

        draft = self.drafter.draft(email, history)
        self.ledger.append(email.client, "drafted", {
            "email_id": email.id, "drafter": self.drafter.name,
            "draft_sha256": draft_hash(draft), "used_memory": draft.used_memory}, email.received)

        decision = reviewer(email, history, draft)
        kind, name = decision[0], decision[1]
        if kind == "skip":
            self.ledger.append(email.client, "skipped", {"email_id": email.id, "reviewer": name},
                               email.received)
            return Outcome(email, history, draft, "skipped")
        if kind == "edit":
            draft.text = decision[2]
            self.ledger.append(email.client, "edited", {
                "email_id": email.id, "reviewer": name,
                "draft_sha256": draft_hash(draft)}, email.received)

        approval = approve(draft, name, email.received)
        self.ledger.append(email.client, "approved", {
            "email_id": email.id, "reviewer": name,
            "draft_sha256": approval.draft_hash}, email.received)
        if before_send:  # used by the demo to simulate a change after approval
            before_send(draft)
        ok, _ = self.try_deliver(draft, approval)
        if not ok:
            return Outcome(email, history, draft, "blocked")
        return Outcome(email, history, draft, "sent", self.outbox / f"{email.id}.eml")

    def reapprove(self, draft: Draft, reviewer: str, ts: str) -> Path:
        approval = approve(draft, reviewer, ts)
        self.ledger.append(draft.client, "approved", {
            "email_id": draft.email_id, "reviewer": reviewer,
            "draft_sha256": approval.draft_hash}, ts)
        return self.deliver(draft, approval)

    def deliver(self, draft: Draft, approval: Approval) -> Path:
        path = send(draft, approval, self.outbox, FIRM_ADDRESS)  # raises if approval is void
        self.ledger.append(draft.client, "sent", {
            "email_id": draft.email_id, "subject": draft.subject,
            "draft_sha256": approval.draft_hash, "approved_by": approval.reviewer,
            "promises": draft.promises, "requests": draft.requests, "fulfils": draft.fulfils,
            "outbox_file": path.name}, approval.ts)
        return path

    def try_deliver(self, draft: Draft, approval: Approval) -> tuple[bool, str]:
        try:
            self.deliver(draft, approval)
            return True, "sent"
        except ApprovalError as err:
            self.ledger.append(draft.client, "blocked", {
                "email_id": draft.email_id, "reason": str(err),
                "draft_sha256": draft_hash(draft)}, approval.ts)
            return False, str(err)
