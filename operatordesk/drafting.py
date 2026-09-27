"""Draft writers.

`RuleDrafter` is a deterministic stand-in so the demo runs anywhere with no API
key. `ClaudeDrafter` asks Claude for the same structured output. Both receive
the client's history rebuilt from the ledger, and both only ever return a
Draft: neither can send anything.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from .memory import ClientHistory
from .models import Draft, Email

SIGN_OFF = "Sam\nNorthgate Bookkeeping"
PAYROLL_ITEMS = ["TD1 forms", "void cheques", "SIN and start date"]

TOPICS = {
    "hst": ("hst", "gst", "sales tax"),
    "payroll": ("payroll", "td1", "staff"),
    "statements": ("statement", "bank"),
    "bookkeeping": ("rates", "pricing", "bookkeep", "how much"),
}


def classify(email: Email) -> tuple[str, str]:
    text = f"{email.subject} {email.body}".lower()
    topic = next((t for t, words in TOPICS.items() if any(w in text for w in words)), "general")
    if email.attachments:
        intent = "documents"
    elif "still need" in text or "do you need our" in text:
        intent = "needs_check"
    elif any(w in text for w in ("update", "where are we", "status")):
        intent = "status_check"
    elif "what info" in text or "what do you need" in text:
        intent = "info_request"
    elif any(w in text for w in ("rates", "pricing", "how much")):
        intent = "new_inquiry"
    elif "can you" in text or "set up" in text:
        intent = "service_request"
    else:
        intent = "general"
    return intent, topic


def _first_name(sender: str) -> str:
    name = sender.split("<")[0].strip()
    parts = [p for p in name.replace("Dr.", "").split() if p]
    return parts[0] if parts else "there"


def _next_friday(ts: str) -> str:
    d = datetime.fromisoformat(ts).date()
    d += timedelta(days=(4 - d.weekday()) % 7 or 7)
    return d.strftime("%a %b ") + str(d.day)


def _is_past(due: str, ts: str) -> bool:
    # due looks like "Fri Sep 18"; the demo year is the received year
    received = datetime.fromisoformat(ts).date()
    due_date = datetime.strptime(f"{due} {received.year}", "%a %b %d %Y").date()
    return received > due_date


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items)


class RuleDrafter:
    name = "rule-based stand-in (no API key needed)"

    def __init__(self, case_notes: dict | None = None):
        self.case_notes = case_notes or {}

    def draft(self, email: Email, history: ClientHistory) -> Draft:
        intent, topic = classify(email)
        notes = self.case_notes.get(email.client, {})
        d = Draft(email_id=email.id, client=email.client, to=email.sender,
                  subject=f"Re: {email.subject.removeprefix('Re: ')}", text="")
        hi = f"Hi {_first_name(email.sender)},"
        open_on_topic = [p for p in history.open_promises if p["topic"] == topic]

        if intent == "status_check" and open_on_topic:
            p = open_on_topic[0]
            late = _is_past(p["due"], email.received)
            opener = (f"You're right to chase this. On {p['date']} I said I'd {p['what']} by "
                      f"{p['due']}, and I'm late with it." if late else
                      f"As promised on {p['date']}, here's the update.")
            body = f"{opener}\n\n{notes.get(topic, 'Here is where it stands.')}"
            d.fulfils = [topic]
            d.used_memory.append(f"found open promise from {p['date']} (due {p['due']})"
                                 + (" - overdue" if late else ""))
        elif intent == "status_check":
            due = _next_friday(email.received)
            body = (f"Thanks for checking in. I'm reviewing the file now and will confirm "
                    f"exactly where it stands by {due}.")
            d.promises = [{"topic": topic, "what": f"confirm the {topic.upper()} status", "due": due}]
        elif intent == "needs_check" and topic == "statements" and "bank statements" in history.provided:
            when = history.provided["bank statements"]
            body = (f"No need, we received your bank statements on {when}. "
                    f"You're all set for this month.")
            d.used_memory.append(f"client already sent bank statements on {when}")
        elif intent == "needs_check":
            body = "Yes please, send your bank statements for last month when you can."
            d.requests = ["bank statements"]
        elif intent == "documents":
            got = list(email.attachments)
            asked = [i for i in history.outstanding]
            still = [i for i in asked if i not in got]
            if asked:
                covered = [i for i in asked if i in got]
                body = (f"Thanks, got the {' and '.join(got)}. That covers "
                        f"{len(covered)} of the {len(asked)} items I asked for on "
                        f"{history.outstanding[asked[0]]}.")
                if still:
                    body += f" Still needed:\n\n{_bullets(still)}"
                    d.requests = still
                d.used_memory.append(f"matched attachments against {len(asked)} outstanding requests")
            else:
                body = f"Thanks, got the {' and '.join(got)}. That's everything we need for now."
        elif intent == "service_request" and topic == "payroll":
            body = (f"Yes, we can set that up. {notes.get('payroll', '')}\n\n"
                    f"From each staff member we'll need:\n\n{_bullets(PAYROLL_ITEMS)}")
            d.requests = list(PAYROLL_ITEMS)
        elif intent == "info_request" and topic == "payroll" and history.outstanding:
            items = list(history.outstanding)
            first = history.outstanding[items[0]]
            body = (f"Same list I sent on {first}; nothing has come in yet, so here it is again:"
                    f"\n\n{_bullets(items)}")
            d.used_memory.append(f"same list already sent on {first}; none of it received yet")
        elif intent == "info_request":
            body = f"Here's what we'll need:\n\n{_bullets(PAYROLL_ITEMS)}"
            d.requests = list(PAYROLL_ITEMS)
        elif intent == "new_inquiry":
            body = ("Congratulations on the new studio. Monthly bookkeeping starts at $300 "
                    "(demo pricing). Roughly how many transactions do you have in a month?")
        else:
            body = "Thanks for your note. I'll look into this and get back to you."
        d.text = f"{hi}\n\n{body.strip()}\n\n{SIGN_OFF}"
        return d


class DraftError(Exception):
    """The drafter could not produce a draft (missing key, API error, bad reply)."""


class ClaudeDrafter:
    """Same contract as RuleDrafter, backed by the Anthropic Messages API.

    Set ANTHROPIC_API_KEY (and optionally OPERATORDESK_MODEL) to use it.
    """

    name = "Claude"
    SYSTEM = (
        "You draft email replies for a small bookkeeping firm. You never send anything; "
        "a person approves every draft. Use the client history you are given: do not ask "
        "for items listed in client_already_sent, and if an open promise is on the same "
        "topic as the email (or overdue), address it directly and say so if we are late. "
        "Reply with JSON only, no prose around it: "
        '{"text": str, "promises": [{"topic": str, "what": str, "due": str}], '
        '"requests": [str], "fulfils": [str]}. '
        "Rules for the structured fields: each promise topic must be one of the given "
        "topics; due dates look like \"Fri Sep 18\". Put a topic in fulfils only when "
        "this reply keeps an open promise on that topic. In requests, reuse the exact item "
        "names from still_waiting_on when you ask for the same thing again."
    )

    def __init__(self, case_notes: dict | None = None, model: str | None = None):
        self.case_notes = case_notes or {}
        self.model = model or os.environ.get("OPERATORDESK_MODEL", "claude-sonnet-5")
        self.key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.key:
            raise DraftError("--claude needs ANTHROPIC_API_KEY set in the environment")

    def draft(self, email: Email, history: ClientHistory) -> Draft:
        prompt = json.dumps({
            "email": {"from": email.sender, "subject": email.subject, "body": email.body,
                      "attachments": list(email.attachments), "received": email.received},
            "client_history": history.for_model(),
            "topics": [*TOPICS, "general"],
            "case_notes": self.case_notes.get(email.client, {}),
            "sign_off": SIGN_OFF,
        }, indent=2)
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps({"model": self.model, "max_tokens": 1500, "system": self.SYSTEM,
                             "messages": [{"role": "user", "content": prompt}]}).encode(),
            headers={"x-api-key": self.key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                reply = json.loads(resp.read())
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")[:300]
            raise DraftError(f"Claude API returned HTTP {err.code}: {detail}") from err
        except urllib.error.URLError as err:
            raise DraftError(f"could not reach the Claude API: {err.reason}") from err
        if reply.get("stop_reason") == "max_tokens":
            raise DraftError("Claude's reply was cut off before the JSON finished")
        raw = "".join(b.get("text", "") for b in reply.get("content", []) if b.get("type") == "text")
        try:
            out = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
            text = out["text"]
        except (ValueError, KeyError) as err:
            raise DraftError(f"Claude did not return the expected JSON: {raw[:200]!r}") from err
        return Draft(email_id=email.id, client=email.client, to=email.sender,
                     subject=f"Re: {email.subject.removeprefix('Re: ')}", text=text,
                     promises=out.get("promises", []), requests=out.get("requests", []),
                     fulfils=out.get("fulfils", []),
                     used_memory=["client history passed to Claude"] if history.sent else [])
