# Mandate

**An AI agent that pays and gets paid on PayPal, only within rules the owner writes.**

Small firms are about to let AI agents move their money. An agent can already pay a freelancer, refund a
customer or send an invoice through PayPal. What it lacks is the owner's say-so: a clear statement of
what it may do alone, checked every time, with everything else waiting for a person.

Mandate is that statement, made executable. The owner writes it in plain English:

> Pay freelancers we've paid before, up to £500, once the job is signed off.
> Refund customers up to £50 within 30 days of the sale.
> Invoice existing clients, with the right VAT.
> Chase unpaid invoices once they are 14 days overdue.
> Never send more than £1,000 in a day without asking me.
> Anything else: ask me.

Claude reads the business's inbox and proposes PayPal actions. Plain code checks each one against the
mandate and the firm's own records. Allowed actions go to PayPal; everything else waits in an approval
queue with the reasons. Every payment, refund and invoice is posted to UK accounts with the right VAT.

**The AI proposes. The mandate decides. PayPal moves the money.**

## How it works

```
 Inbox item ──► Claude proposes one action ──► Mandate checks it ──┬──► PayPal (Payouts, Refunds, Invoicing) ──► Books
 (job tracker,   (structured output, with       (plain code, facts  │
  email, owner    reasoning and risk flags)      from the records,  └──► Waiting for you: approve or decline
  note)                                          never from the AI)
```

* **Claude** (`mandate/brain.py`) does two jobs, both through structured outputs: it turns the owner's
  wording into rules (shown back for confirmation before they take effect), and it reads each inbox item
  and proposes at most one action, with its reasoning and anything a careful bookkeeper would question.
* **The mandate** (`mandate/rules.py`) is plain, tested code. The facts it checks come from the firm's
  records (the job tracker, the sale, the invoice), so a convincing email can't talk its way past it.
* **Money moves only when both agree**: the rules allow it *and* the AI raised no flag. Either can stop a
  payment; neither can push one through alone.
* **PayPal** (`mandate/paypal.py`) is called through its REST APIs on the sandbox. Every call that moves
  money carries a `PayPal-Request-Id` built from Mandate's own action id, so a retry can never pay twice.
  A payout isn't treated as final when PayPal accepts it: the dashboard checks its status again for a few
  minutes afterwards and whenever the page opens, and a payout that fails or comes back is reversed in the
  books, so the job shows unpaid again.
* **The books** (`mandate/books.py`) post each action to UK nominal codes from the open-source
  [`uk-chart-of-accounts`](https://pypi.org/project/uk-chart-of-accounts/) library. Invoices get the VAT
  treatment a UK firm needs (`mandate/vat.py`): UK VAT for UK clients, the reverse-charge wording for EU
  businesses, outside the scope for other overseas businesses. The journal downloads as a CSV for the
  firm's accountant.
* **The dashboard** (`web/`, served by `server.py`) uses **AG Grid Community**, one of the hackathon's
  sponsor tools, for the approval queue, the activity list and the books, and for the CSV export of the
  journal.
* **Other AI agents** (`mandate/mcp_server.py`) can ask Mandate for PayPal actions through MCP. Their
  requests go through the same mandate and the same queue (see below).

## What uses PayPal, and what uses AI

| Part | PayPal | AI |
|---|---|---|
| Pay a freelancer when a job is signed off | Payouts API (`POST /v1/payments/payouts`) | Claude reads the job-tracker update |
| Refund a customer | Refunds (`POST /v2/payments/captures/{id}/refund`) | Claude reads the customer's email |
| Invoice a client, VAT correct | Invoicing v2 (create and send) | Claude reads the owner's note |
| Chase an overdue invoice | Invoicing v2 (remind) | Claude reads the invoice alert |
| Spot a scam | Nothing is sent to PayPal | Claude flags impersonation, urgency, unknown payees |
| Write the rules | | Claude turns plain English into checkable rules |
| Another agent asks for a payment | The same PayPal calls, only within the mandate | Any MCP client (Claude Desktop, Claude Code, your own agent) asks; Mandate decides |
| Sample sales for the demo | Orders v2 (create and capture) | |

Model: Claude Opus 5.5 (`claude-opus-5-5`) through the Anthropic API, with structured outputs. It can also run
on Amazon Bedrock (`MANDATE_AI=bedrock`).

Other tools: AG Grid Community (sponsor tool: the dashboard's grids and CSV export), the Python MCP SDK
(`mcp`) and `uk-chart-of-accounts` (UK nominal codes and VAT).

## Run it in two minutes, no keys

Needs Python 3.11 or later.

```bash
pip install -r requirements.txt
python app.py serve
```

Open http://127.0.0.1:8600 and press **Read the inbox**. Without keys, Mandate uses stored sample answers
for the AI and an offline stand-in for PayPal, so you can see the whole flow. `python -m pytest` runs the
tests.

## Run it on the PayPal sandbox with Claude

1. Sign in at [developer.paypal.com](https://developer.paypal.com), open **Apps & Credentials**, choose
   **Sandbox**, create an app, and copy its client ID and secret. Your dashboard already has a sandbox
   business account (the studio) and a sandbox personal account (the buyer).
2. Copy `.env.example` to `.env` and fill in `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` and
   `ANTHROPIC_API_KEY`.
3. `python app.py check` confirms the keys work. With only `ANTHROPIC_API_KEY` set, `python app.py check-ai`
   scores Claude's reading of the eight sample items against the expected answers (done or waiting, amounts,
   references, risk flags) without touching PayPal or your records.
4. `python app.py seed-sandbox` creates the two sample sales (approve each link while signed in as the
   sandbox **personal** account) and sends the overdue invoice from the sample books.
5. `python app.py serve`, then **Read the inbox**. Payouts, refunds and invoices appear in your sandbox
   business account.

This build refuses to talk to live PayPal: `LivePayPal` accepts only the sandbox base URL.

Things the sandbox does that are easy to mistake for bugs (from PayPal's API reference):

* A new payout batch reports `PENDING`; that is never its final state, so the activity list shows the
  latest status each time the dashboard checks. A payout to an email with no sandbox account stays
  `UNCLAIMED`, and PayPal returns the money after 30 days if no one claims it.
* A EUR payout from a GBP-only sandbox business account can fail with `NON_HOLDING_CURRENCY`; add a EUR
  balance to the sandbox account if it does.
* PayPal sends its own wording for invoice reminders, and allows two a day per invoice.
* Re-running the demo: `python app.py reset`, then `seed-sandbox` again, because each refund needs a fresh
  captured sale.

## Let other AI agents ask it (MCP)

More and more agents will be able to pay for things. Mandate can stand in front of PayPal for all of
them: `python app.py mcp` is an MCP server that any MCP client can use to ask for a payout, refund,
invoice or reminder. Each request appears in the dashboard's inbox under the agent's name and goes
through the same mandate and the same queue as everything else. The agent can read the rules, ask, and
check what happened. No tool lets it approve, decline or change the rules: those stay with the owner.

1. Start the dashboard: `python app.py serve`.
2. Add Mandate to your client. In Claude Code:

   ```bash
   claude mcp add mandate -- python /path/to/mandate/app.py mcp
   ```

   In Claude Desktop, open Settings > Developer > Edit Config and add:

   ```json
   {"mcpServers": {"mandate": {"command": "python", "args": ["C:\\path\\to\\mandate\\app.py", "mcp"]}}}
   ```

3. Ask it, for example, "Invoice Harbour Coffee (C-01) for two days of design at £600 a day" (sent under
   R3, with UK VAT) or "Pay Apex Supplies £2,400 today" (waits for the owner: no job, new payee, over the
   limits).

| Tool | What it does |
|---|---|
| `read_mandate` | The owner's rules, the daily limit and how much has gone out today |
| `request_payout` | Pay a freelancer for a job on record (PayPal Payouts) |
| `request_refund` | Refund a sale on record (PayPal Refunds) |
| `request_invoice` | Invoice an existing client; Mandate works out the VAT (PayPal Invoicing) |
| `request_reminder` | Chase an unpaid invoice (PayPal Invoicing) |
| `check_request` | What became of a request: done, waiting, declined, or failed at PayPal |

`MANDATE_URL` points the server at a dashboard on another port, and `MANDATE_AGENT_NAME` sets the name
the inbox shows (by default, the name the client gives).

## What this demo leaves out

* The dashboard answers only on this machine (127.0.0.1, with a Host-header check), and anything on the
  machine can use its approval buttons. A real deployment would put the owner's approvals behind a
  sign-in.
* Exchange rates are fixed demo rates, and PayPal's fees aren't posted to the books.

## The sample firm

Lark Lane Studio Ltd is a fictional London branding studio. Its inbox holds eight items, each testing one
part of the mandate:

| Item | What happens |
|---|---|
| Priya's job signed off, £420 | Paid under R1 |
| Sam asks for a refund of yesterday's £39 template | Refunded under R2 |
| Invoice Nordwerk GmbH (Germany) €6,000 | Sent under R3 with the reverse-charge wording |
| Harbour Coffee's invoice is 21 days overdue | Chased under R4 |
| Tomás's first job, €750 | Waits: new payee, over £500, over the daily limit |
| Alex wants £149 back for an August course | Waits: over £50, outside 30 days |
| Joe asks to be paid before sign-off | Waits: job not signed off |
| "Ben" emails from a Gmail address: pay a new supplier £2,400 today | Waits: no record, and the AI flags impersonation |

## Built for the PayPal AI Hackathon

New for this hackathon (November 2026). The idea of an accountant's review policy in front of an agent
comes from my earlier, unsubmitted Xero hackathon build, Zero Suspense (July 2026); none of its code is
used here. `uk-chart-of-accounts` is my open-source library, used as a dependency.

## Licence

MIT. See [LICENSE](LICENSE).
