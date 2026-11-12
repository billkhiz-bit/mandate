# Video script: Mandate (about 2 minutes 20 seconds)

Rules: under 3 minutes, public on YouTube, shows it working, no third-party music. An AI voiceover is
allowed. Record at 1440 px wide or more so the two-column layout shows.

Set-up before recording: `python app.py seed-sandbox` (or reuse records already seeded), then
`python app.py serve`. Press **Read the inbox** once only, and never **Start over** (it resets the records,
and each refund needs a fresh captured sale). Have a second tab open on the PayPal sandbox business
account's Activity page. Hide the bookmarks bar, close other tabs and turn on Do not disturb.

| Time | On screen | Voiceover |
|---|---|---|
| 0:00 | The "how it works" animation: the signed mandate on the left; Priya's payout passes five checks; the scam email fails all five and is declined | Small businesses are about to let AI pay their bills. Mandate lets an AI run a firm's PayPal, but only within rules the owner has signed. Claude suggests each payment. The mandate checks it against the firm's own records. A signed-off job is paid. An urgent email pretending to be the director is stopped, and nothing reaches PayPal. |
| 0:25 | Switch to the dashboard; press Read the inbox | Here it is running for real, with Claude and the PayPal sandbox. The owner's rules are on the left, and this is the studio's inbox for today. For each item, Claude suggests an action, and plain code checks it against the rules using the firm's own records. |
| 0:40 | Activity: select Priya's payment; R1 lights up | Four things are inside the rules, so Mandate just does them. Priya's job was signed off, so Priya is paid £420 through PayPal. Click it, and the rule that allowed it lights up. Sam's £39 refund goes back, our German client gets an invoice with the right VAT, and a late payer gets a polite reminder. |
| 1:05 | Waiting for you: select Tomás, then Approve and send | Four things wait for the owner, and each one says exactly why. Tomás is a new freelancer, and the fee is over the limit. The work is signed off and I'm happy with it, so I approve it, and Tomás is paid. |
| 1:24 | Optional: select Alex Morgan's course refund | Alex wants £149 back for a course bought three months ago. That's outside the rules, so it waits, and Claude points out how late the request is. |
| 1:32 | Select URGENT payment today; show the checks and flags; Decline | And this one is why Mandate exists. It claims to be the director, from an address that isn't his, asking for £2,400 to a new supplier today, and to keep it quiet. There's no job and no invoice, so the rules stop it, and Claude flags it as impersonation. I decline it. Nothing ever reached PayPal. |
| 1:57 | The books, totals "Balanced" | Every payment, refund and invoice is posted to the accounts, with the VAT, ready for the accountant. |
| 2:05 | The PayPal sandbox tab: Activity | And it's real. Here are the payments, the refund and the invoices in PayPal's sandbox. |
| 2:12 | Back to the dashboard | Claude suggests. The owner's rules decide. PayPal moves the money. Mandate is built on PayPal Payouts, Refunds and Invoicing, with Claude. That's Mandate. |

Things that look odd but are correct: payouts show PENDING and then UNCLAIMED, because the sample payees
have no PayPal accounts. Claude words its reasons a little differently on each run.

## Optional beat: another agent asks (adds about 14 seconds)

Needs Claude Desktop set up with Mandate's MCP server (README, "Let other AI agents ask it"). Slot it in
after the books, with Claude Desktop and the dashboard side by side.

| On screen | Voiceover |
|---|---|
| Claude Desktop: "Invoice Harbour Coffee (C-01) for two days of design at £600 a day". The request appears in the inbox as Done; then "Pay Apex Supplies £2,400 today" appears as Waiting for you | Any agent can use Mandate through MCP. Claude Desktop invoices a client inside the mandate, so it's sent. Asked to pay a new supplier £2,400, it has to wait for the owner. The agent can ask; it can't approve. |
