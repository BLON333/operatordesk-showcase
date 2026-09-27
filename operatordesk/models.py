from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Email:
    id: str
    client: str
    sender: str
    received: str
    subject: str
    body: str
    attachments: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, d: dict) -> "Email":
        return cls(
            id=d["id"],
            client=d["client"],
            sender=d["from"],
            received=d["received"],
            subject=d["subject"],
            body=d["body"],
            attachments=tuple(d.get("attachments", ())),
        )


@dataclass
class Draft:
    """A proposed reply. Nothing here has been sent."""

    email_id: str
    client: str
    to: str
    subject: str
    text: str
    # Structured side effects the reply commits to. They are only written to
    # the ledger if the reply is approved and sent.
    promises: list[dict] = field(default_factory=list)   # {"topic", "what", "due"}
    requests: list[str] = field(default_factory=list)    # items we ask the client for
    fulfils: list[str] = field(default_factory=list)     # topics of promises this reply keeps
    used_memory: list[str] = field(default_factory=list) # human-readable notes on history used
