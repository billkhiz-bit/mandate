# Devpost entry: Mandate (draft)

Paste each section into the matching Devpost field. Built With: python, claude, anthropic, paypal-payouts,
paypal-invoicing, paypal-orders, ag-grid, uk-chart-of-accounts.
Sponsor tools to tick on the form: AG Grid.

## Tagline

An AI agent that pays and gets paid on PayPal, only within rules the owner writes.

## Inspiration

I'm a finance professional at a small UK accountancy practice. Our clients are the firms that will hand
their PayPal accounts to AI agents first: studios paying freelancers, shops refunding customers, consultants
invoicing abroad. An agent that can pay is useful. An agent that can be talked into paying by one urgent
email is a liability. Every business already has the answer on paper: a bank mandate, a scheme of
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

## How I built it

* Python 3.11, standard library web server, AG Grid Community for the approval queue, activity and books.
* Claude Opus 5.5 through the Anthropic API, using structured outputs for both jobs (reading the mandate,
  proposing actions), with server-side fallback enabled.
* PayPal REST APIs on the sandbox: Payouts, Refunds on captured Orders, Invoicing v2 (create, send,
  remind), and Orders v2 to create the sample sales. Every money-moving call carries a PayPal-Request-Id
  derived from the action, so retries are idempotent.
* The rules engine is a pure function with no model calls, so every decision can be traced and tested.
  75 tests cover the rules, VAT treatment, double-entry books, the full inbox run, and every PayPal and Claude call against local fakes.
* VAT and nominal codes come from uk-chart-of-accounts, my open-source library of the UK chart of accounts.

## Challenges I ran into

Deciding what the AI should never decide. It would be simple to let the model judge whether a payee is
known or a job is signed off, but then a convincing email could claim a history it doesn't have. So the
model only proposes, and every fact the rules check (has this payee been paid before, is the job signed
off, when was the sale) is read from the records. A second gate means a rules-clean action still waits if
the AI flags something odd.

## Accomplishments I'm proud of

The cross-reference: select any payment and the sentence of the mandate that allowed it lights up. The
owner never has to trust the agent, only their own words.

## What I learned

Agents that move money need the same controls a finance team already uses: delegated limits, a second pair
of eyes above a threshold, and an audit trail back to the authority. None of it is new; it just has to run
at machine speed.

## What's next

Run the mandate as a guard in front of PayPal's Agent Toolkit and MCP server, so any agent's money-moving
tool calls pass the same checks. Add webhooks so sign-offs and sales arrive as they happen, then pilot it
with small firms.

## Tools used (AI)

Claude Opus 5.5 (Anthropic API): reads each inbox item and proposes one PayPal action with reasoning and
risk flags; turns the owner's plain-English mandate into checkable rules. Both through structured outputs.

## New or existing project

New for this hackathon, built in November 2026. The idea of an accountant's review policy in front of an
agent comes from an earlier build of mine for a different platform (Zero Suspense, July 2026, not
submitted); no code from it is used.
