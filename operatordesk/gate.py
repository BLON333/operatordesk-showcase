"""The approval gate.

An approval is bound to the SHA-256 of the exact draft text a person saw.
`send()` refuses unless the draft in hand still hashes to that value, so a
draft edited after approval (by a person or by a model) cannot go out on the
old approval. "Sending" in this demo writes an .eml file to the outbox folder.
"""
from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from .ledger import sha256
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
    return sha256(f"{draft.to}\n{draft.subject}\n{draft.text}")


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
    msg["X-OperatorDesk-Approved-By"] = approval.reviewer
    msg["X-OperatorDesk-Draft-SHA256"] = approval.draft_hash
    msg.set_content(draft.text)
    outbox = Path(outbox)
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / f"{draft.email_id}.eml"
    path.write_bytes(bytes(msg))
    return path
