# Devpost entry: Mandate

Paste each section into the matching Devpost field. Built With: paypal, paypal-payouts, paypal-refunds,
paypal-invoicing, paypal-orders, claude, anthropic, python, ag-grid, mcp, uk-chart-of-accounts.
Sponsor tools to tick on the form: AG Grid.
Prizes, if the form asks which to enter: all the general ones, plus Best Use of Agentic Commerce, Best Use of
PayPal + AI and Best Use of AG Grid (a long shot: that award favours AG Studio, and Mandate uses AG Grid Community).

## Tagline

An AI agent that pays, refunds and invoices on PayPal, but only within rules the business owner has written down.

## Inspiration

I'm a finance professional working at a small accountancy practice in the UK. Our clients are small businesses: studios paying freelancers, shops handling refunds and consultants invoicing clients abroad.

I've already built tools for the practice, and the main thing I've learnt is never to let the software guess. My bookkeeping tool only codes a transaction when a rule says so. Anything it isn't sure about goes into suspense for the accountant to decide.

PayPal's brief asked for agents that actually transact, like an assistant that pays a contractor once the job is marked complete. That's useful, but I wasn't comfortable with an agent that a single convincing email could talk into making a payment. Businesses already handle this on paper with a bank mandate, which sets out what can be paid without asking and what can't. So I wanted to see whether I could turn that mandate into rules the agent has to follow.

## What it does

The owner writes their rules in plain English, for example "pay freelancers we've paid before, up to £500, once the job is signed off". Claude turns them into checks and shows them back so the owner can confirm them first.

Mandate then reads the business's inbox (job tracker updates, customer emails and notes from the owner). For each item, Claude suggests one action on PayPal and explains why. The important part is that Claude doesn't make the decision. Plain code checks the suggestion against the rules, using the business's own records rather than what the email says.

If the action is allowed and Claude hasn't flagged anything, Mandate carries it out on PayPal: a payout, a refund, an invoice or a reminder. If not, it waits for the owner, with a list showing exactly which checks passed and which failed. Everything is posted to the books with the right VAT, and you can click any payment to see the rule that allowed it.

In the demo, a made-up London design studio has eight items in its inbox. Four are completed on PayPal and four wait for the owner. One of these is an email pretending to be from the director, asking for £2,400 to be paid to a new supplier today. There's no job or invoice behind it, so the rules stop it, and Claude also flags it as impersonation. Nothing reaches PayPal.

I also added an MCP server, so other AI agents can ask Mandate to make a payment or send an invoice. Their requests go through the same rules and queue, and there's no tool that lets an agent approve anything.

## How I built it

It's built in Python 3.11 with a simple web dashboard, using AG Grid (Community) for the approval queue, activity list and books.

Claude Opus 5.5, through the Anthropic API, does two jobs: turning the owner's wording into rules and reading each inbox item. Both use structured outputs, so the answers always come back in the same format. Server-side fallback is also switched on.

On the PayPal side, it uses the sandbox REST APIs: Payouts, Refunds on captured Orders, Invoicing v2 (create, send and remind) and Orders v2 for the sample sales. Every call that moves money carries a PayPal-Request-Id based on Mandate's own action, so a retried call can't pay twice.

The rules are plain Python with no AI in them, so every decision can be tested. There are 100 tests, covering the rules, VAT, the double-entry books, the full inbox run, the MCP tools and every PayPal and Claude call (run against local fakes).

VAT and nominal codes come from uk-chart-of-accounts, an open-source library I wrote.

## Challenges I ran into

The hardest part was deciding what the AI should never decide. It would have been easy to let Claude judge whether someone has been paid before or whether a job is signed off, but then a convincing email could just claim it. So Claude only suggests, and every fact the rules check comes from the records.

Another was knowing when a payout has actually gone through. PayPal first accepts a payout as PENDING, and if the recipient doesn't have a PayPal account, it stays UNCLAIMED. So Mandate keeps checking, and if a payout fails or is returned, it reverses the entries and marks the job as unpaid again.

The first time I ran it live, PayPal's checkout asked the buyer for a delivery address, because I hadn't told it the sample products were digital. One setting fixed it, but no test would have caught that.

## Accomplishments that I'm proud of

Being able to click a payment and see the exact rule that allowed it. The owner doesn't have to trust the agent, only their own rules.

It also works on the real PayPal sandbox, not just against test fakes. On the first live run, it made a payout, a refund, a euro invoice and a reminder, and held the other four items for the owner. When I ran Claude live, it reached the right decision on all eight items, and on one it added a warning I hadn't expected: the customer was asking for a refund three months after buying.

## What I learned

Agents that move money need the same controls a finance team already uses: limits, a second pair of eyes above a certain amount and a record of who approved what. None of that is new. It just has to work at the agent's speed.

## What's next for Mandate

At the moment, it's a hackathon build that only runs on the PayPal sandbox with a made-up studio's data.

Next, I'd like to put the same checks in front of PayPal's own Agent Toolkit and MCP server, so agents calling PayPal directly have to follow the mandate too. After that, I'd add webhooks so sign-offs and sales arrive as they happen, and hopefully try it with a few small businesses.

## Tools used

Claude Opus 5.5 (Anthropic API) does two jobs. It turns the owner's plain-English rules into checks, which are shown back for the owner to confirm. It also reads each inbox item and suggests one PayPal action, along with its reasoning and anything that looks wrong (someone impersonating the owner, pressure to pay urgently, a new payee or missing paperwork). Both jobs use structured outputs, and server-side fallback is enabled in case a request is declined. Claude never decides whether money moves; plain code does that, using the business's own records.

PayPal REST APIs (sandbox): Payouts to pay freelancers, Refunds on captured Orders to refund customers, Invoicing v2 to create, send and chase invoices, and Orders v2 to set up the sample sales.

AG Grid Community: the approval queue, activity list and books, plus a CSV export for the accountant.

MCP (Python SDK): lets other AI agents request PayPal actions from Mandate. There's deliberately no tool for approving them.

uk-chart-of-accounts: my open-source library, used for UK nominal codes and VAT.

## Testing instructions

You don't need any keys to try it, just Python 3.11 or later.

1. Clone the repo: git clone https://github.com/billkhiz-bit/mandate, then open the folder.
2. Install the requirements: pip install -r requirements.txt
3. Run python app.py serve, open http://127.0.0.1:8600 and press Read the inbox.

Without keys, Mandate uses stored sample answers for the AI and an offline stand-in for PayPal, so you can see the whole flow. To run the 100 tests, use python -m pytest.

To run it live with Claude on the PayPal sandbox, create a sandbox app at developer.paypal.com (Apps & Credentials, Sandbox), copy .env.example to .env, and add the app's client ID and secret and an Anthropic API key. Then follow "Run it on the PayPal sandbox with Claude" in the README (check, seed-sandbox, serve). During seed-sandbox, you approve two sample orders as the sandbox buyer. You don't need any of my keys, and none are in the repo.

## New or existing project

New: I built it for this hackathon in November 2026. The idea of putting an accountant's review rules in front of an agent came from an earlier project of mine for a different platform (Zero Suspense, July 2026, which I didn't submit). None of its code is reused.
