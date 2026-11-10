"""Mandate command line.

    python app.py serve            open the dashboard at http://127.0.0.1:8600
    python app.py run              process the inbox here in the terminal
    python app.py check            show which AI and which PayPal this machine will use
    python app.py check-ai         score live Claude on the sample inbox (no PayPal, records untouched)
    python app.py seed-sandbox     put the sample sales and overdue invoice into your PayPal sandbox
    python app.py reset            start again from the sample inbox
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
STATE_PATH = ROOT / "data" / "state.json"


def load_env(path: Path = ROOT / ".env") -> None:
    """Read KEY=VALUE lines from .env without overriding the real environment.
    utf-8-sig so a file saved by Notepad with a BOM still parses."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def build_engine():
    from mandate.brain import make_brain
    from mandate.engine import Engine
    from mandate.paypal import FakePayPal, LivePayPal
    from mandate.seed import FIRM, SAMPLE_MANDATE
    from mandate.store import Store

    store = Store.open(STATE_PATH, date.today())
    use_live = (os.environ.get("MANDATE_PAYPAL", "").lower() != "offline"
                and os.environ.get("PAYPAL_CLIENT_ID") and os.environ.get("PAYPAL_CLIENT_SECRET"))
    paypal = LivePayPal.from_env() if use_live else FakePayPal()
    return Engine(store, make_brain(FIRM, SAMPLE_MANDATE), paypal)


def cmd_serve(args) -> None:
    from server import serve

    serve(build_engine(), port=args.port)


def cmd_run(args) -> None:
    engine = build_engine()
    counts = engine.run_inbox()
    for a in engine.s["activity"]:
        print(f"  DONE     {a['event_id']}  {a['kind']:<8} {a['payee']:<22} {a['amount']:>9,.2f} {a['currency']}  "
              f"{a['decided_by']}  {a['paypal_id']}")
    for q in engine.s["queue"]:
        if q["outcome"] == "Waiting":
            print(f"  WAITING  {q['event_id']}  {q['kind']:<8} {q['payee']:<22} {q['amount']:>9,.2f} {q['currency']}  "
                  f"{q['summary'][:90]}")
    print(f"\n{counts['done']} done, {counts['waiting']} waiting for you, {counts['nothing']} needed nothing.")


def cmd_check(args) -> None:
    engine = build_engine()
    print(f"AI:     {engine.brain.name}")
    print(f"PayPal: {engine.paypal.mode}")
    if engine.paypal.mode == "sandbox":
        try:
            engine.paypal._access_token()
            print("        sandbox keys accepted")
        except Exception as exc:  # noqa: BLE001
            print(f"        {exc}")
    if engine.brain.name.startswith("Claude") and args.ai:
        try:
            p = engine.brain.propose({"id": "T1", "source": "Owner note", "subject": "Test",
                                      "body": "Nothing to pay today, just checking in."}, {})
            print(f"        model answered: action={p.action}")
        except Exception as exc:  # noqa: BLE001
            print(f"        {exc}")


def cmd_check_ai(args) -> None:
    from mandate.brain import ClaudeBrain
    from mandate.check_ai import score
    from mandate.seed import FIRM

    bedrock = os.environ.get("MANDATE_AI", "").lower() == "bedrock"
    if not bedrock and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Put ANTHROPIC_API_KEY in .env first (or set MANDATE_AI=bedrock).")
    brain = ClaudeBrain(FIRM, provider="bedrock" if bedrock else "anthropic")
    print(f"Asking {brain.name} about the 8 sample inbox items. Nothing is sent to PayPal "
          "and your records are not changed.\n")
    rows = score(brain, date.today())
    for r in rows:
        mark = "PASS" if r["ok"] else "DIFF"
        print(f"  {mark}  {r['id']}  {r['subject'][:40]:<40}  expected {r['expected']:<8} got {r['got']}")
        for d in r["differences"]:
            print(f"        - {d}")
        if not r["ok"] and r["reasoning"]:
            print(f"        Claude: {r['reasoning'][:160]}")
        if r["flags"] and not r["ok"]:
            print(f"        Flags: {'; '.join(r['flags'])[:160]}")
    passed = sum(r["ok"] for r in rows)
    print(f"\n{passed} of {len(rows)} match the demo's expected answers.")
    if passed < len(rows):
        print("A DIFF isn't always wrong: read Claude's reasoning, then tune the prompt or the sample item.")


def cmd_seed_sandbox(args) -> None:
    from mandate.paypal import PayPalError

    engine = build_engine()
    if engine.paypal.mode != "sandbox":
        sys.exit("Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET (sandbox keys) in .env first.")
    pp, s = engine.paypal, engine.s

    orders = {}
    for sale_id, sale in s["sales"].items():
        if sale["capture_id"]:
            continue
        order_id, link = pp.create_order(sale_id, sale["item"], sale["amount"], sale["currency"],
                                         return_url=f"http://127.0.0.1:{args.port}/")
        orders[sale_id] = order_id
        print(f"{sale_id} {sale['item']}: approve as your sandbox BUYER account:\n  {link}\n")
    if orders:
        input("Approve each link above in the browser (sandbox buyer login), then press Enter here... ")
        for sale_id, order_id in orders.items():
            try:
                s["sales"][sale_id]["capture_id"] = pp.capture_order(order_id)
                print(f"  {sale_id} captured")
            except PayPalError as exc:
                print(f"  {sale_id}: {exc}")

    inv = s["invoices"]["INV-0041"]
    if not inv["paypal_id"]:
        client = s["clients"][inv["client"]]
        number = f"{inv['number']}-{s['run_id'][:4].upper()}"
        body = {"detail": {"invoice_number": number, "invoice_date": inv["issued"], "currency_code": inv["currency"],
                           "payment_term": {"term_type": "DUE_ON_DATE_SPECIFIED", "due_date": inv["due"]}},
                "invoicer": {"business_name": s["firm"]},
                "primary_recipients": [{"billing_info": {"business_name": client["name"],
                                                         "email_address": client["email"]}}],
                "items": [{"name": "Brand refresh, phase 1", "quantity": "1",
                           "unit_amount": {"currency_code": "GBP", "value": f"{inv['net']:.2f}"},
                           "tax": {"name": "VAT", "percent": "20"}, "unit_of_measure": "QUANTITY"}]}
        try:  # PayPal accepts past invoice dates; a past-dated invoice is sent at once
            out = pp.create_and_send_invoice(f"{s['run_id']}-seed-0041", body)
        except PayPalError as exc:
            engine.store.save()  # keep the captures already made
            sys.exit(f"The overdue invoice wasn't created: {exc}")
        inv["paypal_id"] = out["id"]
        print(f"  INV-0041 sent in the sandbox as {number} ({out['id']})")
    engine.store.save()
    print("\nSandbox ready. Now: python app.py serve")


def cmd_reset(args) -> None:
    from mandate.store import Store

    Store.fresh(STATE_PATH, date.today())  # works even when the old file can't be read
    print("Back to the sample inbox. In sandbox mode, run seed-sandbox again before reading it.")


def main(argv=None) -> None:
    # Windows consoles default to a legacy code page; names like Tomás and the
    # £ sign need UTF-8 to print correctly.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    load_env()
    parser = argparse.ArgumentParser(description="Mandate: an AI agent that moves money on PayPal only within your rules.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="open the dashboard")
    serve.add_argument("--port", type=int, default=8600)
    serve.set_defaults(fn=cmd_serve)
    sub.add_parser("run", help="process the inbox in the terminal").set_defaults(fn=cmd_run)
    check = sub.add_parser("check", help="show which AI and PayPal will be used")
    check.add_argument("--ai", action="store_true", help="also make one small model call")
    check.set_defaults(fn=cmd_check)
    sub.add_parser("check-ai", help="score live Claude on the sample inbox (no PayPal, records untouched)"
                   ).set_defaults(fn=cmd_check_ai)
    seed = sub.add_parser("seed-sandbox", help="put the sample sales and invoice into the PayPal sandbox")
    seed.add_argument("--port", type=int, default=8600)
    seed.set_defaults(fn=cmd_seed_sandbox)
    sub.add_parser("reset", help="start again from the sample inbox").set_defaults(fn=cmd_reset)
    args = parser.parse_args(argv)
    from mandate.store import StoreError

    try:
        args.fn(args)
    except StoreError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
