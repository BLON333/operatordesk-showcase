"""Per-client memory, rebuilt from the ledger every time.

The ledger is the only source of truth. There is no separate "memory store"
that can drift: what the agent remembers is exactly what was approved and sent,
plus what the client sent us.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .ledger import Ledger


@dataclass
class ClientHistory:
    client: str
    received: list[dict] = field(default_factory=list)         # {"date", "subject", "attachments"}
    sent: list[dict] = field(default_factory=list)             # {"date", "subject"}
    open_promises: list[dict] = field(default_factory=list)    # {"topic", "what", "due", "date"}
    kept_promises: list[dict] = field(default_factory=list)
    provided: dict[str, str] = field(default_factory=dict)     # item -> date the client sent it
    outstanding: dict[str, str] = field(default_factory=dict)  # item we asked for -> date asked

    @property
    def is_new(self) -> bool:
        return not self.sent

    def summary_lines(self) -> list[str]:
        if self.is_new and not self.received:
            return ["no history: first contact"]
        lines = [f"{len(self.received)} received, {len(self.sent)} sent"]
        for p in self.open_promises:
            lines.append(f"open promise ({p['date']}): {p['what']} by {p['due']}")
        for item, date in self.provided.items():
            lines.append(f"client already sent: {item} ({date})")
        for item, date in self.outstanding.items():
            lines.append(f"still waiting on: {item} (asked {date})")
        return lines


def _day(ts: str) -> str:
    # "2026-09-14T09:12:00" -> "Sep 14"
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    y, m, d = ts[:10].split("-")
    return f"{months[int(m) - 1]} {int(d)}"


def build_history(ledger: Ledger, client: str) -> ClientHistory:
    h = ClientHistory(client=client)
    for e in ledger.for_client(client):
        day = _day(e.ts)
        if e.kind == "received":
            h.received.append({"date": day, "subject": e.data["subject"],
                               "attachments": e.data.get("attachments", [])})
            for item in e.data.get("attachments", []):
                h.provided.setdefault(item, day)
                h.outstanding.pop(item, None)
        elif e.kind == "sent":
            h.sent.append({"date": day, "subject": e.data["subject"]})
            for topic in e.data.get("fulfils", []):
                for p in list(h.open_promises):
                    if p["topic"] == topic:
                        h.open_promises.remove(p)
                        h.kept_promises.append({**p, "kept": day})
            for p in e.data.get("promises", []):
                h.open_promises.append({**p, "date": day})
            for item in e.data.get("requests", []):
                if item not in h.provided:
                    h.outstanding.setdefault(item, day)
    return h
