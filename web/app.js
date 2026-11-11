"use strict";
/* Mandate dashboard. Inbox text comes from emails, so every string is set with
   textContent (or AG Grid's default text rendering), never as HTML. */

const $ = (id) => document.getElementById(id);
const ACTION = { payout: "Pay", refund: "Refund", invoice: "Invoice", reminder: "Chase", none: "Read" };
const DONE_VERB = { payout: "Paid", refund: "Refunded", invoice: "Invoiced", reminder: "Chased", none: "Handled" };
const STATUS = { new: "New", done: "Done", waiting: "Waiting for you", declined: "Declined", checking: "Checking" };
const SYMBOL = { GBP: "£", EUR: "€", USD: "$" };

let state = null;
let draft = null;
let selectedRule = null;

const money = (n, cur = "GBP") =>
  (SYMBOL[cur] ?? `${cur} `) + Number(n).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    throw new Error("Mandate isn't running. Start it with: python app.py serve");
  }
  let data = {};
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status}).`);
  return data;
}

function toast(message, isError = false) {
  const t = $("toast");
  t.textContent = message;
  t.classList.toggle("error", isError);
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 4500);
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/* -- grid cells ------------------------------------------------------------ */
function amountCell(p) {
  const d = p.data;
  if (!d) return "";
  if (d.kind === "reminder" || d.kind === "none") return el("span", "sub", "No money moves");
  const wrap = el("span", "num-cell", money(d.amount, d.currency));
  if (d.currency !== "GBP") wrap.append(el("span", "sub", `(${money(d.amount_gbp)})`));
  return wrap;
}

function decidedCell(p) {
  const v = p.value || "";
  if (/^R\d+$/.test(v)) return el("span", "chip rule", v);
  if (v === "You approved" || v === "You") return el("span", "chip you", "You");
  if (v === "You declined") return el("span", "chip no", "You declined");
  return v;
}

function paypalCell(p) {
  const d = p.data;
  if (!d || !d.paypal_id) return el("span", "sub", d && d.outcome === "Declined" ? "Nothing sent" : "");
  const wrap = el("span", "paypal-ref", d.paypal_id);
  if (d.paypal_status) wrap.append(el("span", "sub", d.paypal_status));
  return wrap;
}

function whyItWaits(q) {
  const failed = (q.checks || []).find((c) => !c.ok);
  if (failed) return failed.label;
  return (q.risk_flags || [])[0] || q.summary || "";
}

/* -- grids ------------------------------------------------------------------ */
const shared = {
  domLayout: "autoHeight",
  defaultColDef: { resizable: true, sortable: true, suppressMovable: true },
  getRowId: (p) => p.data.id,
  overlayNoRowsTemplate: " ",
  tooltipShowDelay: 300,
};

const queueGrid = agGrid.createGrid($("queue-grid"), {
  ...shared,
  rowSelection: { mode: "singleRow", checkboxes: false, enableClickSelection: true },
  columnDefs: [
    { headerName: "Item", field: "subject", flex: 2, minWidth: 190 },
    { headerName: "Action", field: "kind", width: 100, valueFormatter: (p) => ACTION[p.value] ?? p.value },
    { headerName: "To", field: "payee", flex: 1, minWidth: 140 },
    { headerName: "Amount", field: "amount", width: 170, cellRenderer: amountCell },
    { headerName: "Why it waits", colId: "why", flex: 2, minWidth: 220, cellClass: "why",
      valueGetter: (p) => whyItWaits(p.data), tooltipValueGetter: (p) => p.data.summary },
  ],
  onSelectionChanged: () => renderDetail(),
});

const activityGrid = agGrid.createGrid($("activity-grid"), {
  ...shared,
  rowSelection: { mode: "singleRow", checkboxes: false, enableClickSelection: true },
  columnDefs: [
    { headerName: "Time", field: "time", width: 76 },
    { headerName: "Done", colId: "done", width: 100,
      valueGetter: (p) => (["Declined", "Failed"].includes(p.data.outcome) ? p.data.outcome
        : DONE_VERB[p.data.kind] ?? p.data.kind) },

    { headerName: "To", field: "payee", flex: 1, minWidth: 130 },
    { headerName: "Amount", field: "amount", width: 168, cellRenderer: amountCell },
    { headerName: "Allowed by", field: "decided_by", width: 112, cellRenderer: decidedCell,
      tooltipValueGetter: (p) => p.data.rule_text || p.data.summary },
    { headerName: "PayPal", field: "paypal_id", flex: 1, minWidth: 150, cellRenderer: paypalCell,
      tooltipValueGetter: (p) => p.data.paypal_id },
    { headerName: "VAT", field: "vat", flex: 1, minWidth: 140, tooltipValueGetter: (p) => p.data.vat },
  ],
  onSelectionChanged: (e) => {
    const [row] = e.api.getSelectedRows();
    selectedRule = row && /^R\d+$/.test(row.decided_by) ? row.decided_by : null;
    highlightRule(selectedRule);
  },
  onCellMouseOver: (e) => highlightRule(/^R\d+$/.test(e.data?.decided_by) ? e.data.decided_by : selectedRule),
  onCellMouseOut: () => highlightRule(selectedRule),
});

const booksGrid = agGrid.createGrid($("books-grid"), {
  ...shared,
  getRowId: (p) => p.data.id ?? "totals",
  columnDefs: [
    { headerName: "Date", field: "date", width: 116 },
    { headerName: "Ref", field: "ref", width: 116 },
    // A nominal code is a label, not a quantity: as "text", the totals row's blank code
    // isn't shown as "Invalid Number" (AG Grid would infer a number from 6002).
    { headerName: "Code", field: "code", width: 84, cellDataType: "text" },
    { headerName: "Account", field: "account", flex: 1, minWidth: 170 },
    { headerName: "Debit", field: "debit", width: 120, type: "rightAligned",
      valueFormatter: (p) => (p.value ? money(p.value) : "") },
    { headerName: "Credit", field: "credit", width: 120, type: "rightAligned",
      valueFormatter: (p) => (p.value ? money(p.value) : "") },
    { headerName: "Memo", field: "memo", flex: 1, minWidth: 180 },
  ],
});

/* -- rendering ---------------------------------------------------------------- */
function render() {
  $("firm").textContent = state.firm;
  const ai = $("mode-ai");
  ai.textContent = `AI: ${state.ai}`;
  ai.classList.toggle("live", state.ai.startsWith("Claude"));
  const pp = $("mode-paypal");
  pp.textContent = state.paypal === "sandbox" ? "PayPal: sandbox" : "PayPal: offline";
  pp.classList.toggle("live", state.paypal === "sandbox");

  renderMandate();
  renderInbox();

  queueGrid.setGridOption("rowData", state.queue);
  $("queue-count").textContent = String(state.queue.length);
  $("queue-empty").hidden = state.queue.length > 0;
  $("queue-grid").hidden = state.queue.length === 0;
  if (state.queue.length && !queueGrid.getSelectedRows().length) {
    queueGrid.getDisplayedRowAtIndex(0)?.setSelected(true);
  }
  renderDetail();

  const activity = [...state.activity].reverse();
  activityGrid.setGridOption("rowData", activity);
  $("activity-empty").hidden = activity.length > 0;
  $("activity-grid").hidden = activity.length === 0;

  booksGrid.setGridOption("rowData", state.journal);
  $("export-books").hidden = state.journal.length === 0;
  const debit = state.journal.reduce((s, l) => s + l.debit, 0);
  const credit = state.journal.reduce((s, l) => s + l.credit, 0);
  booksGrid.setGridOption("pinnedBottomRowData", state.journal.length
    ? [{ date: null, ref: null, code: null, account: "Totals", debit, credit, memo: Math.abs(debit - credit) < 0.005 ? "Balanced" : "Out of balance" }]
    : []);
}

function clauseNode(rule, uses) {
  const li = el("li", "clause");
  li.dataset.rule = rule.id;
  li.append(el("span", "num", rule.id), el("span", "words", rule.text));
  if (uses !== undefined) li.append(el("span", "uses", uses ? `Used ${plural(uses, "time", "times")} today` : "Not used yet"));
  return li;
}

function renderMandate() {
  const list = $("clauses");
  list.replaceChildren(...state.mandate.rules.map((r) =>
    clauseNode(r, state.activity.filter((a) => a.decided_by === r.id && a.date === state.today).length)));
  const cap = state.mandate.daily_cap_gbp;
  $("cap").textContent = cap ? `No more than ${money(cap)} goes out in a day without asking me.` : "";
  $("cap").hidden = !cap;
  $("signature").textContent = state.owner.name;
  const day = new Date(`${state.today}T12:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
  $("signed-by").textContent = `${state.owner.name}, director, ${day}`;
  highlightRule(selectedRule);
}

function highlightRule(ruleId) {
  document.querySelectorAll("#clauses .clause").forEach((li) => li.classList.toggle("lit", li.dataset.rule === ruleId));
}

function renderInbox() {
  $("inbox").replaceChildren(...state.inbox.map((e) => {
    const status = e.status || "new";
    const li = el("li", "item");
    li.append(el("span", "subject", e.subject), el("span", "meta", `${e.source}, ${e.received.slice(11)}`),
      el("span", `pill ${status}`, STATUS[status] ?? status));
    if (e.note) li.title = e.note;
    return li;
  }));
}

function renderDetail() {
  const [q] = queueGrid.getSelectedRows();
  $("detail").hidden = !q;
  if (!q) return;
  $("detail-summary").textContent = q.summary;
  $("detail-checks").replaceChildren(...(q.checks || []).map((c) => {
    const li = el("li", c.ok ? "ok" : "no");
    const tick = el("span", "tick", c.ok ? "✓" : "✗");
    tick.setAttribute("role", "img");
    tick.setAttribute("aria-label", c.ok ? "Passed" : "Failed");
    li.append(tick, el("span", null, c.label));
    return li;
  }));
  $("detail-reasoning").textContent = q.reasoning;
  $("detail-flags").replaceChildren(...(q.risk_flags || []).map((f) => el("li", null, f)));
  const readOnly = q.kind === "none";  // nothing to send: the owner reads it and marks it handled
  $("approve").hidden = readOnly;
  $("decline").textContent = readOnly ? "Mark as handled" : "Decline";
  if (readOnly) return;
  $("approve").textContent = q.kind === "reminder" ? "Approve and send reminder"
    : q.kind === "invoice" ? "Approve and send invoice" : `Approve and ${ACTION[q.kind].toLowerCase()} ${money(q.amount, q.currency)}`;
}

/* -- actions ---------------------------------------------------------------- */
let actionsInFlight = 0;  // while this page is acting, it already gets fresh state back

async function withBusy(button, label, fn) {
  const before = button.textContent;
  button.disabled = true;
  button.textContent = label;
  actionsInFlight += 1;
  try { await fn(); } catch (err) { toast(err.message, true); } finally {
    actionsInFlight -= 1;
    button.disabled = false;
    button.textContent = before;
  }
}

async function load() {
  try {
    state = await api("/api/state");
    render();
    schedulePayoutRefresh();  // a reload picks up payouts still waiting on PayPal
  } catch (err) {
    toast(err.message, true);
  }
  setTimeout(watchForChanges, 3000);
}

/* Another AI agent can ask Mandate for something through MCP at any time. While the page
   is open, check every few seconds and show what arrived. The revision number only goes
   up, so an older answer can never replace newer state. */
async function watchForChanges() {
  if (state && !actionsInFlight && document.visibilityState === "visible") {
    try {
      const next = await api("/api/state");
      if (!next.busy && !actionsInFlight && next.rev > state.rev) {
        const known = new Set(state.inbox.map((e) => e.id));
        const arrived = next.inbox.filter((e) => !known.has(e.id) && e.id.startsWith("M"));
        state = next;
        render();
        arrived.forEach((e) => toast(`${e.source}: ${e.subject}. ${e.status === "done"
          ? "Done within the mandate." : "Waiting for you."}`));
        schedulePayoutRefresh();
      }
    } catch { /* the dashboard may be restarting: try again next time */ }
  }
  setTimeout(watchForChanges, 3000);
}

function markReading() {
  // Show which item the AI is reading now: the first one still new.
  const index = state.inbox.findIndex((i) => (i.status || "new") === "new");
  const pill = document.querySelectorAll("#inbox .item .pill")[index];
  if (pill) { pill.className = "pill reading"; pill.textContent = "Reading…"; }
}

/* PayPal reports a new payout as PENDING. Ask again, backing off (5 s, 10 s, 20 s...),
   while any payout is still interim, and stop after a few minutes. */
const INTERIM = new Set(["", "PENDING", "PROCESSING", "NEW", "ONHOLD", "UNCLAIMED"]);
let refreshTimer = null;
const hasInterimPayouts = () => state && state.paypal === "sandbox" && state.activity.some(
  (a) => a.kind === "payout" && a.outcome === "Done" && a.paypal_id && INTERIM.has(a.paypal_status || ""));

function schedulePayoutRefresh(delay = 5000) {
  clearTimeout(refreshTimer);
  if (!hasInterimPayouts() || delay > 160000) return;
  refreshTimer = setTimeout(async () => {
    try {
      const data = await api("/api/refresh-payouts", {});
      if (data.changed) { state = data.state; render(); }
    } catch { /* try again on the next tick */ }
    schedulePayoutRefresh(delay * 2);
  }, delay);
}

$("run").addEventListener("click", (e) => withBusy(e.currentTarget, "Reading…", async () => {
  // One item per request, so each inbox item visibly turns Done or Waiting as it is decided.
  // The server, not this page's copy, says how many are left: the page may be out of date.
  const button = e.currentTarget;
  const total = { done: 0, waiting: 0, nothing: 0 };
  let processed = 0;
  let remaining = null;
  try {
    do {
      button.textContent = remaining === null ? "Reading…" : `Reading ${processed + 1} of ${processed + remaining}…`;
      if (state) markReading();
      const data = await api("/api/run", { limit: 1 });
      state = data.state;
      render();
      Object.keys(total).forEach((k) => { total[k] += data.counts[k]; });
      const moved = data.counts.done + data.counts.waiting + data.counts.nothing;
      processed += moved;
      remaining = data.remaining;
      if (!moved) break;  // nothing moved: stop rather than loop
    } while (remaining > 0);
  } catch (err) {
    if (processed) err.message += ` (${plural(processed, "item was", "items were")} handled before this.)`;
    throw err;
  } finally {
    schedulePayoutRefresh();
  }
  toast(total.done + total.waiting === 0 ? "Nothing new in the inbox."
    : `${plural(total.done, "action", "actions")} done within the mandate, ${total.waiting} waiting for you.`);
}));

$("approve").addEventListener("click", (e) => withBusy(e.currentTarget, "Sending…", async () => {
  const [q] = queueGrid.getSelectedRows();
  if (!q) return;
  const data = await api("/api/approve", { id: q.id });
  state = data.state;
  render();
  const a = data.entry;
  toast(`${DONE_VERB[a.kind]} ${a.payee}${a.kind === "reminder" ? "" : ` ${money(a.amount, a.currency)}`}. PayPal ${a.paypal_id}.`);
  schedulePayoutRefresh();
}));

$("decline").addEventListener("click", (e) => withBusy(e.currentTarget, "Declining…", async () => {
  const [q] = queueGrid.getSelectedRows();
  if (!q) return;
  const data = await api("/api/decline", { id: q.id });
  state = data.state;
  render();
  toast(q.kind === "none" ? "Marked as handled." : "Declined. Nothing was sent.");
}));

/* The journal as a CSV an accountant can import: plain numbers (no £), no totals row.
   AG Grid writes it with a UTF-8 byte-order mark, so Excel shows names like Tomás correctly. */
function journalCsv() {
  const slug = state.firm.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  return {
    fileName: `${slug}-journal-${state.today}.csv`,
    skipPinnedBottom: true,
    processCellCallback: (p) => {
      const id = p.column.getColId();
      if (id === "debit" || id === "credit") return p.value ? Number(p.value).toFixed(2) : "";
      return p.value ?? "";
    },
  };
}
$("export-books").addEventListener("click", () => {
  booksGrid.exportDataAsCsv(journalCsv());
  toast("Journal downloaded as CSV.");
});

let resetArmed = false;
$("reset").addEventListener("click", (e) => {
  const button = e.currentTarget;
  if (!resetArmed) {
    resetArmed = true;
    button.textContent = "Click again to start over";
    setTimeout(() => { resetArmed = false; button.textContent = "Start over"; }, 3000);
    return;
  }
  resetArmed = false;
  withBusy(button, "Starting over…", async () => {
    state = (await api("/api/reset", {})).state;
    selectedRule = null;
    render();
    toast(state.paypal === "sandbox"
      ? "Back to the sample inbox. Run python app.py seed-sandbox before reading it again."
      : "Back to the sample inbox.");
  }).then(() => { button.textContent = "Start over"; });
});

$("edit").addEventListener("click", () => {
  $("editor").hidden = false;
  $("draft").hidden = true;
  $("mandate-text").value = state.mandate.text;
  $("mandate-text").focus();
});
$("edit-cancel").addEventListener("click", () => { $("editor").hidden = true; draft = null; });

$("editor").addEventListener("submit", (e) => {
  e.preventDefault();
  withBusy($("compile"), "Reading…", async () => {
    draft = await api("/api/compile", { text: $("mandate-text").value });
    $("draft-rules").replaceChildren(...draft.mandate.rules.map((r) => clauseNode(r)));
    $("draft-unclear").replaceChildren(...draft.unclear.map((u) => el("li", null, u)));
    $("draft").hidden = false;
  });
});

$("use-draft").addEventListener("click", (e) => withBusy(e.currentTarget, "Saving…", async () => {
  if (!draft) return;
  state = (await api("/api/mandate", { mandate: draft.mandate })).state;
  $("editor").hidden = true;
  draft = null;
  render();
  toast("Mandate updated. New items are checked against these rules.");
}));

load();
