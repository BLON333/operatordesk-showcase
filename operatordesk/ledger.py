"""Append-only, hash-chained ledger.

Every event (email received, draft written, approval, send) is one JSON line.
Each line carries the hash of the line before it, so editing or deleting any
past entry breaks the chain and `verify()` says exactly where.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

GENESIS = "0" * 64


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Entry:
    seq: int
    ts: str
    client: str
    kind: str
    data: dict
    prev: str
    hash: str

    def body(self) -> dict:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "client": self.client,
            "kind": self.kind,
            "data": self.data,
            "prev": self.prev,
        }


class LedgerError(Exception):
    pass


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    # -- reading -----------------------------------------------------------
    def entries(self) -> Iterator[Entry]:
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield Entry(**json.loads(line))

    def for_client(self, client: str) -> list[Entry]:
        return [e for e in self.entries() if e.client == client]

    def last(self) -> Entry | None:
        last = None
        for last in self.entries():
            pass
        return last

    # -- writing -----------------------------------------------------------
    def append(self, client: str, kind: str, data: dict, ts: str) -> Entry:
        prev = self.last()
        body = {
            "seq": (prev.seq + 1) if prev else 1,
            "ts": ts,
            "client": client,
            "kind": kind,
            "data": data,
            "prev": prev.hash if prev else GENESIS,
        }
        entry = Entry(**body, hash=sha256(canonical(body)))
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(canonical(entry.__dict__) + "\n")
        return entry

    # -- checking ----------------------------------------------------------
    def verify(self) -> tuple[bool, str]:
        expected_prev = GENESIS
        count = 0
        for count, entry in enumerate(self.entries(), start=1):
            if entry.prev != expected_prev:
                return False, f"chain broken at entry {entry.seq}: previous-hash mismatch"
            if sha256(canonical(entry.body())) != entry.hash:
                return False, f"entry {entry.seq} was modified after it was written"
            expected_prev = entry.hash
        return True, f"ledger intact: {count} entries, chain verified"
