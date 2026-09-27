# OperatorDesk showcase

**Email drafting where nothing goes out without a person's approval, and every approved reply becomes the agent's memory for that client.**

![Demo: eight emails drafted, gated, sent and logged](docs/demo.gif)

This is a small, runnable sample of the pattern I use on my own insurance desk and build for others: bookkeepers, law and accounting firms, property managers, brokerages. It runs on an invented inbox with no API key, in about two minutes.

## What it shows

**1. An approval gate that can't be sidestepped.** A draft is only a draft. Approving it records the reviewer's name, the time, and the SHA-256 of exactly what they saw: the text plus anything the reply commits to (promises, requests). If anything changes after that, even one number, the send is refused and the approval is void. In the demo, a price is edited from $300 to $250 after approval, and the gate blocks it until the new version gets its own approval.

**2. The log is the memory.** Every sent reply is recorded per client: what they asked, what we promised, what we asked them for, what they sent. The next draft for that client is written against that history, so the desk doesn't contradict itself or re-ask for things.

| Email | Without memory | With memory |
|---|---|---|
| "Do you still need our August statements?" | "Yes please, send your bank statements." | "No need, we received them on Sep 14." |
| "Any update on the HST?" (a week after we promised one) | "I'll confirm where it stands by Fri Sep 25." A second promise, no mention of the first. | "On Sep 14 I said I'd confirm by Fri Sep 18, and I'm late with it." Then the actual status. |
| Client sends 2 of the 3 forms we asked for | "Thanks, that's everything we need." (It isn't.) | "That covers 2 of the 3 items I asked for on Sep 15. Still needed: SIN and start date." |

Only approved, sent replies become memory. A skipped draft's promises are never recorded as made, and if a person rewrites a draft, the drafter's promises and requests are only kept when the reviewer confirms the new text still says them.

**3. A ledger you can check.** Every step (received, drafted, edited, approved, blocked, sent) is one line in an append-only, hash-chained JSONL file. Editing or deleting an earlier line breaks the chain, and `verify` names the entry. Someone with write access could recompute every later hash too, so the demo prints the head hash for you to keep somewhere else; checked against that copy, no rewrite goes unnoticed.

## Run it

Python 3.10 or newer, no dependencies.

```bash
git clone https://github.com/BLON333/operatordesk-showcase
cd operatordesk-showcase

python -m operatordesk demo          # the full sample inbox, end to end
python -m operatordesk verify        # check the ledger the demo just wrote
python -m operatordesk history kestrel-landscaping   # what the desk remembers
python -m operatordesk compare e06   # same email drafted with and without memory
python -m operatordesk run --fresh   # now review each draft yourself: approve, edit or skip
```

To draft with Claude instead of the built-in stand-in, set `ANTHROPIC_API_KEY` (optionally `OPERATORDESK_MODEL`, default `claude-sonnet-5`) and add `--claude` to `demo`, `compare` or `run`. Claude gets the same client history as structured data and returns the same structured draft. It still can't send anything.

The sample inbox spans a week in September, so by default each step is stamped a few minutes after its email arrived and the story reads the same whenever you run it. Add `--clock real` to stamp steps with the actual time.

Tests: `pip install pytest && pytest -q` (use a virtual environment if your system Python blocks global installs). CI runs them on Linux and Windows.

## How it works

```mermaid
flowchart LR
    A[Email arrives] --> B[Rebuild client history<br/>from the ledger]
    B --> C[Draft reply<br/>rule-based or Claude]
    C --> D{Person reviews}
    D -- skip --> L
    D -- edit / approve --> E[Approval bound to<br/>SHA-256 of the text]
    E --> F{Text still matches?}
    F -- no --> G[Blocked, approval void] --> L
    F -- yes --> H[Send<br/>demo: .eml in outbox/] --> L[(Hash-chained ledger)]
    L --> B
```

## Guarantees and limits

- **It never sends real email.** "Send" writes an `.eml` file to `outbox/` with the approver and draft hash in its headers. In a client build this step becomes an Outlook or Gmail send, behind the same gate.
- **Memory is rebuilt from the ledger every time.** There's no second store that can drift from what actually went out.
- **The hash chain catches edits and deletions, as long as the head hash is kept somewhere else.** On its own, the file shows any edit that wasn't followed by rewriting every later hash. Checked against a pinned head hash, it also shows full rewrites and entries chopped off the end. A client build would pin it automatically (a daily email to the owner, or a second store).
- **The built-in drafter is a stand-in.** It uses simple rules so the demo is deterministic and free to run. A client build would use Claude (the `--claude` option here) with the same contract.
- **All data is invented.** Every business, person and address in `data/sample_inbox.json` is fictional.

## Layout

| Path | What it does |
|---|---|
| `operatordesk/ledger.py` | Append-only, hash-chained event log with `verify()` |
| `operatordesk/memory.py` | Rebuilds each client's history (promises, requests, documents) from the ledger |
| `operatordesk/drafting.py` | Rule-based stand-in and Claude drafter, same interface |
| `operatordesk/gate.py` | Approval bound to the draft's hash; `send()` refuses anything else |
| `operatordesk/desk.py` | Wires inbox, memory, drafting, gate and ledger together |
| `operatordesk/cli.py` | `demo`, `compare`, `run`, `verify`, `history` |
| `tests/` | Gate, ledger, memory and resume behaviour, the README commands, and the Claude drafter contract (with the API call faked) |

## About

Built by Jason B. as a public sample of the approval-gated desk automation I build for clients. A longer walkthrough of a document workflow built on the same principles, with its evidence ledger, is [here](https://claude.ai/artifact/U45M7w3e7EGMeRzEZRFyYc). I take on projects through [Upwork](https://www.upwork.com/freelancers/~0188908dd31f18b432).

MIT licensed.
