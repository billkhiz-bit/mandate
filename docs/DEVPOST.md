# Devpost entry: Mandate (draft)

Paste each section into the matching Devpost field. Built With: python, claude, anthropic, paypal-payouts,
paypal-invoicing, paypal-orders, ag-grid, mcp, uk-chart-of-accounts.
Sponsor tools to tick on the form: AG Grid.
Prizes, if the form asks which to enter: all the general ones, plus Best Use of Agentic Commerce, Best Use of
PayPal + AI and Best Use of AG Grid (a long shot: that award favours AG Studio, and Mandate uses AG Grid Community).

## Tagline

An AI agent that pays and gets paid on PayPal, only within rules the owner writes.

## Inspiration

I'm a finance professional at a small UK accountancy practice. Our clients are the firms that will hand
their PayPal accounts to AI agents first: studios paying freelancers, shops refunding customers, consultants
invoicing abroad. PayPal's brief for this hackathon asks for agents that actually transact, such as an
assistant that pays contractors when a job is marked complete. That agent is useful. An agent that can be
talked into paying by one urgent email is a liability. Every business already has the answer on paper: a bank mandate, a scheme of
delegation, "anything over £500, ask me". Mandate makes that piece of paper executable.

## What it does

The owner writes a mandate in plain English: pay freelancers we've paid before up to £500 once the job is
signed off; refund customers up to £50 within 30 days; invoice existing clients with the right VAT; chase
invoices 14 days overdue; never more than £1,000 out in a day without asking. Claude turns it into rules
and shows them back for the owner to confirm.

Then Mandate reads the business's inbox: job-tracker updates, customer emails, the owner's notes. For each
item Claude proposes one PayPal action, with its reasoning and anything a careful bookkeeper would question.
Plain code checks the proposal against the mandate, using facts from the firm's own records rather than the
email. If the rules allow it and the AI raised no flag, Mandate acts on PayPal: Payouts for freelancers,
Refunds for customers, Invoicing for clients and reminders. Everything else waits in an approval queue that
shows each check that passed or failed. Every action is posted to UK accounts, and each one points back to
the sentence of the mandate that allowed it.

In the demo, a fictional London studio's inbox of eight items becomes four actions on PayPal and four
items waiting for the owner, including an email impersonating the director that asks for £2,400 to a new
supplier today. The rules stop it because no job or invoice exists, and Claude flags the impersonation.
Nothing reaches PayPal.

Mandate also guards agents it doesn't run. Through its MCP server, any agent (Claude Desktop, Claude Code
or your own) can ask it to pay, refund, invoice or chase. The request lands in the owner's inbox under the
agent's name and goes through the same mandate and the same queue. The agent can ask, and see what
happened; there is no tool to approve. For agent-to-agent commerce, that makes Mandate the checkpoint
between another agent's request and the business's money.

## How I built it

* Python 3.11, standard library web server, AG Grid Community for the approval queue, activity and books.
* Claude Opus 5.5 through the Anthropic API, using structured outputs for both jobs (reading the mandate,
  proposing actions), with server-side fallback enabled.
* PayPal REST APIs on the sandbox: Payouts, Refunds on captured Orders, Invoicing v2 (create, send,
  remind), and Orders v2 to create the sample sales. Every money-moving call carries a PayPal-Request-Id
  derived from the action, so retries are idempotent.
* The rules engine is a pure function with no model calls, so every decision can be traced and tested.
  100 tests cover the rules, VAT treatment, double-entry books, the full inbox run, the MCP tools, and every PayPal and Claude call against local fakes.
* An MCP server (the Python MCP SDK) with six tools: read the mandate, request a payout, refund, invoice
  or reminder, and check a request. It talks to the dashboard over HTTP on the same machine, so the
  dashboard is the only thing that writes the records.
* AG Grid also exports the journal as a CSV for the firm's accountant.
* VAT and nominal codes come from uk-chart-of-accounts, my open-source library of the UK chart of accounts.

## Challenges I ran into

Deciding what the AI should never decide. It would be simple to let the model judge whether a payee is
known or a job is signed off, but then a convincing email could claim a history it doesn't have. So the
model only proposes, and every fact the rules check (has this payee been paid before, is the job signed
off, when was the sale) is read from the records. A second gate means a rules-clean action still waits if
the AI flags something odd.

Knowing when a payment is really made. PayPal accepts a payout as PENDING, and a payout to an email with
no account sits UNCLAIMED until PayPal returns it. So Mandate keeps checking each payout's status, and if
one fails or comes back it reverses the entries, marks the job unpaid and frees the daily limit again.

## Accomplishments I'm proud of

The cross-reference: select any payment and the sentence of the mandate that allowed it lights up. The
owner never has to trust the agent, only their own words.

## What I learned

Agents that move money need the same controls a finance team already uses: delegated limits, a second pair
of eyes above a threshold, and an audit trail back to the authority. None of it is new; it just has to run
at machine speed.

## What's next

Put the same checks in front of PayPal's own Agent Toolkit and MCP server, so agents that call PayPal
directly are bound by the mandate too. Add webhooks so sign-offs and sales arrive as they happen, then pilot it
with small firms.

## Tools used (AI)

Claude Opus 5.5 (Anthropic API): reads each inbox item and proposes one PayPal action with reasoning and
risk flags; turns the owner's plain-English mandate into checkable rules. Both through structured outputs.

## New or existing project

New for this hackathon, built in November 2026. The idea of an accountant's review policy in front of an
agent comes from an earlier build of mine for a different platform (Zero Suspense, July 2026, not
submitted); no code from it is used.
