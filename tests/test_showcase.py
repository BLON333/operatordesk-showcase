import json
from pathlib import Path

import pytest

from operatordesk.cli import main
from operatordesk.desk import Desk, simulated_clock
from operatordesk.drafting import DraftError
from operatordesk.gate import ApprovalError, approve, send
from operatordesk.ledger import Ledger
from operatordesk.memory import build_history
from operatordesk.models import Draft

INBOX = Path(__file__).resolve().parent.parent / "data" / "sample_inbox.json"


def approve_all(email, history, draft):
    return ("approve", "tester")


def run_until(tmp_path, email_id, use_memory=True):
    desk = Desk(tmp_path, INBOX, use_memory=use_memory, clock=simulated_clock)
    for email in desk.emails:
        outcome = desk.process(email, approve_all)
        if email.id == email_id:
            return desk, outcome
    raise AssertionError(email_id)


# -- the gate --------------------------------------------------------------

def make_draft(text="Hello"):
    return Draft(email_id="x1", client="c", to="a@b.example", subject="Re: hi", text=text)


def test_send_without_approval_is_refused(tmp_path):
    with pytest.raises(ApprovalError, match="no approval"):
        send(make_draft(), None, tmp_path, "desk@x.example")
    assert not list(tmp_path.glob("*.eml"))


def test_edit_after_approval_voids_the_approval(tmp_path):
    draft = make_draft("Price is $300")
    approval = approve(draft, "Sam", "2026-01-01T00:00:00")
    draft.text = "Price is $250"
    with pytest.raises(ApprovalError, match="changed after it was approved"):
        send(draft, approval, tmp_path, "desk@x.example")


def test_changing_a_promise_after_approval_voids_the_approval(tmp_path):
    draft = make_draft("I'll confirm by Friday")
    draft.promises = [{"topic": "hst", "what": "confirm the HST status", "due": "Fri Sep 18"}]
    approval = approve(draft, "Sam", "2026-01-01T00:00:00")
    draft.promises[0]["due"] = "Fri Oct 30"
    with pytest.raises(ApprovalError, match="changed after it was approved"):
        send(draft, approval, tmp_path, "desk@x.example")


def test_approval_needs_a_named_reviewer():
    with pytest.raises(ApprovalError):
        approve(make_draft(), "  ", "2026-01-01T00:00:00")


def test_skipped_drafts_are_never_sent(tmp_path):
    desk = Desk(tmp_path, INBOX)
    outcome = desk.process(desk.emails[0], lambda e, h, d: ("skip", "tester"))
    assert outcome.decision == "skipped"
    assert not (tmp_path / "outbox").exists()


# -- the ledger ------------------------------------------------------------

def test_ledger_chain_verifies_and_catches_edits(tmp_path):
    desk, _ = run_until(tmp_path, "e08")
    ok, _ = desk.ledger.verify()
    assert ok
    lines = desk.ledger.path.read_text().splitlines()
    entry = json.loads(lines[2])
    entry["data"]["reviewer"] = "someone else"
    lines[2] = json.dumps(entry)
    desk.ledger.path.write_text("\n".join(lines) + "\n")
    ok, msg = Ledger(desk.ledger.path).verify()
    assert not ok and "entry 3" in msg


def test_deleting_a_middle_entry_breaks_the_chain(tmp_path):
    desk, _ = run_until(tmp_path, "e03")
    lines = desk.ledger.path.read_text().splitlines()
    del lines[4]
    desk.ledger.path.write_text("\n".join(lines) + "\n")
    ok, msg = Ledger(desk.ledger.path).verify()
    assert not ok and "chain broken" in msg


# -- memory ----------------------------------------------------------------

def test_memory_stops_the_desk_re_asking_for_documents(tmp_path):
    _, with_mem = run_until(tmp_path / "a", "e06")
    _, without = run_until(tmp_path / "b", "e06", use_memory=False)
    assert "No need" in with_mem.draft.text and "Sep 14" in with_mem.draft.text
    assert "send your bank statements" in without.draft.text


def test_memory_surfaces_an_overdue_promise(tmp_path):
    _, outcome = run_until(tmp_path, "e05")
    assert "I'm late" in outcome.draft.text
    assert outcome.draft.fulfils == ["hst"]


def test_kept_promise_closes(tmp_path):
    desk, _ = run_until(tmp_path, "e05")
    h = build_history(desk.ledger, "harbourline-cafe")
    assert not h.open_promises and len(h.kept_promises) == 1


def test_partial_documents_ask_only_for_what_is_missing(tmp_path):
    desk, outcome = run_until(tmp_path, "e08")
    assert outcome.draft.requests == ["SIN and start date"]
    h = build_history(desk.ledger, "kestrel-landscaping")
    assert list(h.outstanding) == ["SIN and start date"]


def test_only_approved_replies_become_memory(tmp_path):
    desk = Desk(tmp_path, INBOX)
    desk.process(desk.emails[0], lambda e, h, d: ("skip", "tester"))  # e01 skipped
    h = build_history(desk.ledger, "harbourline-cafe")
    assert not h.open_promises  # the unsent promise was never recorded as made


def test_edited_reply_drops_commitments_the_reviewer_did_not_confirm(tmp_path):
    desk = Desk(tmp_path, INBOX, clock=simulated_clock)
    desk.process(desk.emails[0], lambda e, h, d: ("edit", "tester", "Hi Maya, on it. Sam"))
    assert not build_history(desk.ledger, "harbourline-cafe").open_promises


def test_edited_reply_keeps_commitments_the_reviewer_confirmed(tmp_path):
    desk = Desk(tmp_path, INBOX, clock=simulated_clock)

    def edit_and_keep(email, history, draft):
        kept = {"promises": draft.promises, "requests": draft.requests, "fulfils": draft.fulfils}
        return ("edit", "tester", draft.text + "\nThanks for your patience.", kept)

    desk.process(desk.emails[0], edit_and_keep)
    assert len(build_history(desk.ledger, "harbourline-cafe").open_promises) == 1


def test_email_left_undecided_comes_back_with_the_same_history(tmp_path):
    desk = Desk(tmp_path, INBOX, clock=simulated_clock)
    for email in desk.emails[:7]:
        desk.process(email, approve_all)
    e08 = desk.emails[7]

    def quit_midway(email, history, draft):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        desk.process(e08, quit_midway)
    assert "e08" not in desk.processed_ids()
    outcome = desk.process(e08, approve_all)  # second run
    assert outcome.draft.requests == ["SIN and start date"]  # history as of first arrival
    received = [e for e in desk.ledger.entries()
                if e.kind == "received" and e.data["email_id"] == "e08"]
    assert len(received) == 1 and desk.ledger.verify()[0]


def test_approval_is_stamped_when_it_happens_not_when_the_email_arrived(tmp_path):
    desk = Desk(tmp_path, INBOX)  # default: wall clock
    desk.process(desk.emails[0], approve_all)
    by_kind = {e.kind: e for e in desk.ledger.entries()}
    assert by_kind["received"].ts == desk.emails[0].received
    assert by_kind["approved"].ts > by_kind["received"].ts
    assert by_kind["approved"].ts.endswith("+00:00")
    assert by_kind["sent"].data["approved_at"] == by_kind["approved"].ts


def test_malformed_ledger_line_is_reported_not_crashed_on(tmp_path):
    desk, _ = run_until(tmp_path, "e02")
    with desk.ledger.path.open("a") as fh:
        fh.write("{not json\n")
    ok, msg = Ledger(desk.ledger.path).verify()
    assert not ok and "not a valid ledger entry" in msg


def test_outbox_file_carries_approval_headers(tmp_path):
    desk, _ = run_until(tmp_path, "e01")
    eml = (tmp_path / "outbox" / "e01.eml").read_text()
    assert "X-OperatorDesk-Approved-By: tester" in eml
    assert "X-OperatorDesk-Draft-SHA256" in eml
    assert "Date: " in eml and "Message-ID: " in eml


def test_readme_commands_work_in_order(tmp_path, capsys):
    state = str(tmp_path / "state")
    assert main(["demo", "--state", state]) == 0
    assert main(["verify", "--state", state]) == 0
    assert "34 entries" in capsys.readouterr().out
    assert main(["history", "kestrel-landscaping", "--state", state]) == 0
    assert "still waiting on: SIN and start date" in capsys.readouterr().out
    assert main(["compare", "e06", "--state", state]) == 0


# -- Claude drafter contract (no network: the HTTP call is faked) ------------

def test_claude_drafter_parses_structured_reply(monkeypatch, tmp_path):
    import io
    from operatordesk import drafting

    reply = {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps({
        "text": "Hi Omar,\n\nNo need, we have them.\n\nSam", "promises": [],
        "requests": [], "fulfils": []})}]}
    seen = {}

    def fake_urlopen(req, timeout):
        seen["body"] = json.loads(req.data)
        return io.BytesIO(json.dumps(reply).encode())

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(drafting.urllib.request, "urlopen", fake_urlopen)
    desk = Desk(tmp_path, INBOX, use_claude=True)
    outcome = desk.process(desk.emails[1], approve_all)
    assert outcome.decision == "sent"
    content = seen["body"]["messages"][0]["content"]
    assert "client_history" in content and "still_waiting_on" in content


def test_claude_drafter_without_a_key_fails_clearly(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(DraftError, match="ANTHROPIC_API_KEY"):
        Desk(tmp_path, INBOX, use_claude=True)


@pytest.mark.parametrize("reply, message", [
    ({"stop_reason": "max_tokens", "content": [{"type": "text", "text": '{"text": "Hi'}]}, "cut off"),
    ({"stop_reason": "end_turn", "content": [{"type": "text", "text": "Sure, here you go"}]}, "expected JSON"),
])
def test_claude_drafter_bad_replies_raise_draft_error(monkeypatch, tmp_path, reply, message):
    import io
    from operatordesk import drafting

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(drafting.urllib.request, "urlopen",
                        lambda req, timeout: io.BytesIO(json.dumps(reply).encode()))
    desk = Desk(tmp_path, INBOX, use_claude=True)
    with pytest.raises(DraftError, match=message):
        desk.process(desk.emails[0], approve_all)
