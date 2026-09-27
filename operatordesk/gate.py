"""The approval gate.

An approval is bound to the SHA-256 of exactly what a person saw: the
recipient, subject and text, plus the promises and requests the reply commits
to (those become the client's memory once it is sent). `send()` refuses unless
the draft in hand still hashes to that value, so a draft changed after approval
(by a person or by a model) cannot go out on the old approval. "Sending" in
this demo writes an .eml file to the outbox folder.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path

from .ledger import canonical, sha256
from .models import Draft


class ApprovalError(Exception):
    pass


@dataclass(frozen=True)
class Approval:
    email_id: str
    draft_hash: str
    reviewer: str
    ts: str


def draft_hash(draft: Draft) -> str:
    return sha256(canonical({
        "to": draft.to, "subject": draft.subject, "text": draft.text,
        "promises": draft.promises, "requests": draft.requests, "fulfils": draft.fulfils,
    }))


def approve(draft: Draft, reviewer: str, ts: str) -> Approval:
    if not reviewer.strip():
        raise ApprovalError("an approval needs a named reviewer")
    return Approval(draft.email_id, draft_hash(draft), reviewer, ts)


def send(draft: Draft, approval: Approval | None, outbox: Path, sender: str) -> Path:
    if approval is None:
        raise ApprovalError("not sent: no approval on record")
    if approval.email_id != draft.email_id:
        raise ApprovalError("not sent: approval belongs to a different email")
    if approval.draft_hash != draft_hash(draft):
        raise ApprovalError("not sent: draft changed after it was approved; approval is void")
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = draft.to
    msg["Subject"] = draft.subject
    when = datetime.fromisoformat(approval.ts)
    msg["Date"] = format_datetime(when if when.tzinfo else when.replace(tzinfo=timezone.utc))
    msg["Message-ID"] = make_msgid(idstring=draft.email_id, domain=sender.split("@")[-1])
    msg["X-OperatorDesk-Approved-By"] = approval.reviewer
    msg["X-OperatorDesk-Draft-SHA256"] = approval.draft_hash
    msg.set_content(draft.text)
    outbox = Path(outbox)
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / f"{draft.email_id}.eml"
    path.write_bytes(bytes(msg))
    return path
