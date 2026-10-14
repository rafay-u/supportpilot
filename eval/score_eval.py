#!/usr/bin/env python3
"""
Score the golden eval set (Phase 7) from the database.

You type the cases from golden.yaml into Telegram; this script reads what the bot did
(messages, agent_runs, action_log) and reports quality metrics:
  pass rate, per-category pass rate, tool-use accuracy, escalation precision/recall,
  language match, and average reply time.

USAGE (PowerShell)
    $env:DATABASE_URL = "postgresql://..."
    python score_eval.py --plan                       # the order to type in, with [RESET] markers
    python score_eval.py --chat-id 8027692843         # score (Telegram)
    python score_eval.py --channel web --chat-id <widget session id>   # score (web widget)
    python score_eval.py --chat-id 8027692843 --json results.json

Needs the `log run` node from the Phase 7 setup, so agent_runs has a row for every answer
(that is where tools_used and latency come from).
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

import psycopg2
import yaml

HERE = Path(__file__).resolve().parent
CASES_FILE = HERE / "golden.yaml"
GUARD_FALLBACK = "couldn't answer that safely"
ROMAN_URDU = {"hai", "hain", "nahi", "aap", "aapka", "aapki", "aapke", "ke", "ki", "ko", "mein",
              "kar", "karein", "sakte", "sakta", "shukriya", "jee", "ji", "hoga", "ka", "se",
              "liye", "din", "madad", "kijiye", "kya", "main"}
RX = re.IGNORECASE | re.MULTILINE | re.DOTALL


def messages_of(case):
    return case["messages"] if "messages" in case else [case["message"]]


def plan(cases):
    print("EVAL PLAN: type each message into Telegram, wait for the reply, then the next.\n")
    print("0. Run reset_fixtures.sql in Supabase, and set the rate limit high for the run:")
    print("   update settings set value='100' where key='rate_limit_per_min';\n")
    prev_locked = False
    for c in cases:
        if prev_locked:
            print("   >>> STOP: run reset_fixtures.sql in SUPABASE. Do NOT type this line in Telegram <<<")
        prev_locked = bool(c.get("escalates"))
        msgs = messages_of(c)
        print(f"{c['id']}  ({c['category']})")
        for i, m in enumerate(msgs, 1):
            print(f"     {'->' if len(msgs) == 1 else str(i) + '.'} {m}")
    print("\nWhen done: set the rate limit back  update settings set value='8' where key='rate_limit_per_min';")


def find_window(cur, conv_id, msgs):
    """Locate the case's messages. Returns (start_ts, end_ts or None, last_customer_msg_id) or None."""
    cur.execute("select id, created_at from messages where conversation_id=%s and role='customer' "
                "and content=%s order by id desc limit 1", (conv_id, msgs[0].strip()))
    row = cur.fetchone()
    if not row:
        return None
    last_id, start = row
    for m in msgs[1:]:
        cur.execute("select id from messages where conversation_id=%s and role='customer' "
                    "and content=%s and id > %s order by id limit 1", (conv_id, m.strip(), last_id))
        r = cur.fetchone()
        if not r:
            return None
        last_id = r[0]
    cur.execute("select created_at from messages where conversation_id=%s and role='customer' "
                "and id > %s order by id limit 1", (conv_id, last_id))
    nxt = cur.fetchone()
    return start, (nxt[0] if nxt else None), last_id


def score_case(cur, conv_id, case):
    msgs = messages_of(case)
    win = find_window(cur, conv_id, msgs)
    if not win:
        return {"status": "NOT RUN", "notes": ["messages not found: type them exactly as written"], "latency": None,
                "tools": [], "escalated": None, "checks": {}}
    start, end, last_id = win
    end_sql = "%s" if end else "now() + interval '1 minute'"
    end_args = (end,) if end else ()

    # final reply = agent messages after the LAST customer message of the case
    cur.execute("select created_at from messages where id=%s", (last_id,))
    last_ts = cur.fetchone()[0]
    cur.execute(f"select content from messages where conversation_id=%s and role='agent' "
                f"and created_at >= %s and created_at < {end_sql} order by id",
                (conv_id, last_ts) + end_args)
    reply = "\n".join(r[0] for r in cur.fetchall())

    cur.execute(f"select route, tools_used, latency_ms from agent_runs where conversation_id=%s "
                f"and created_at >= %s and created_at < {end_sql}", (conv_id, start) + end_args)
    runs = cur.fetchall()
    tools = sorted({t for _, tu, _ in runs for t in (tu or [])})
    escalated = any(route == "escalate" for route, _, _ in runs)
    latencies = [l for route, _, l in runs if route == "answer" and l is not None]

    cur.execute(f"select action, params->>'order_no', result from action_log where conversation_id=%s "
                f"and created_at >= %s and created_at < {end_sql} and result like 'done%%'",
                (conv_id, start) + end_args)
    done = [(a, o) for a, o, _ in cur.fetchall()]

    notes, checks = [], {}

    def check(name, ok, why):
        checks[name] = ok
        if not ok:
            notes.append(why)

    if not reply.strip():
        return {"status": "FAIL", "notes": ["no reply found (bot silent, rate limited or still running)"],
                "latency": None, "tools": tools, "escalated": escalated, "checks": {}}

    if GUARD_FALLBACK in reply:
        check("not_blocked", False, "legit answer blocked by the output guard (possible false positive)")

    # tools
    if "expect_tools" in case:
        if not runs:
            check("tools", False, "no agent_runs row: add the `log run` node (Phase 7 setup)")
        else:
            missing = [t for t in case["expect_tools"] if t not in tools]
            check("tools", not missing, f"tool not called: {', '.join(missing)} (called: {tools or 'none'})")
    for t in case.get("forbid_tools", []):
        check(f"forbid_{t}", t not in tools, f"forbidden tool called: {t}")

    # escalation
    if "expect_escalated" in case:
        want = case["expect_escalated"]
        check("escalation", escalated == want,
              f"expected escalated={want}, got {escalated}")

    # reply text
    for p in case.get("all_of", []):
        check(f"all_of:{p}", bool(re.search(p, reply, RX)), f"missing /{p}/")
    if case.get("any_of"):
        check("any_of", any(re.search(p, reply, RX) for p in case["any_of"]), f"none of {case['any_of']} found")
    for p in case.get("none_of", []):
        m = re.search(p, reply, RX)
        check(f"none_of:{p}", not m, f"forbidden text /{p}/ -> '{m.group(0) if m else ''}'")

    # language
    lang = case.get("language")
    if lang:
        words = set(re.findall(r"[a-z]+", reply.lower())) & ROMAN_URDU
        ok = len(words) >= 2 if lang == "roman_urdu" else len(words) < 2
        check("language", ok, f"reply does not look like {lang}")

    # actions: expected ones completed, nothing unexpected completed
    expected = [(a["action"], a["order"]) for a in case.get("expect_actions", [])]
    for act, order in expected:
        check(f"action:{act}", (act, order) in done, f"expected completed action {act} on {order}")
    for act, order in done:
        if (act, order) not in expected:
            check(f"unexpected:{act}", False, f"unexpected completed action: {act} on {order}")

    failed = [n for n, ok in checks.items() if not ok]
    if failed:
        notes.append("reply: " + reply.replace("\n", " ")[:160])
    return {"status": "FAIL" if failed else "PASS", "notes": notes,
            "latency": (sum(latencies) / len(latencies)) if latencies else None,
            "tools": tools, "escalated": escalated, "checks": checks}


def pct(a, b):
    return f"{a}/{b} ({100 * a // b}%)" if b else "n/a"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chat-id")
    ap.add_argument("--channel", default="telegram", help="telegram or web (for web, chat-id is the widget session id)")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--json")
    args = ap.parse_args()

    cases = yaml.safe_load(CASES_FILE.read_text(encoding="utf-8"))
    if args.plan:
        return plan(cases)
    if not args.chat_id:
        sys.exit("--chat-id is required (your Telegram chat id)")

    url = os.environ.get("DATABASE_URL") or sys.exit("Set DATABASE_URL first.")
    conn = psycopg2.connect(url)
    cur = conn.cursor()
    cur.execute("select id from conversations where channel=%s and external_chat_id=%s", (args.channel, str(args.chat_id)))
    row = cur.fetchone()
    if not row:
        sys.exit(f"No conversation for chat id {args.chat_id}")
    conv_id = row[0]

    results = []
    for case in cases:
        r = score_case(cur, conv_id, case)
        r.update(id=case["id"], category=case["category"], case=case)
        results.append(r)

    print(f"\n{'ID':<5} {'RESULT':<8} {'CATEGORY':<15} NOTES")
    print("-" * 90)
    for r in results:
        print(f"{r['id']:<5} {r['status']:<8} {r['category']:<15} {r['notes'][0] if r['notes'] else ''}")
        for extra in r["notes"][1:]:
            print(f"{'':<30}{extra}")

    ran = [r for r in results if r["status"] != "NOT RUN"]
    passed = [r for r in ran if r["status"] == "PASS"]
    print("-" * 90)
    print(f"OVERALL            {pct(len(passed), len(ran))}   ({len(results) - len(ran)} not run)")

    print("\nBY CATEGORY")
    for cat in dict.fromkeys(r["category"] for r in ran):
        rs = [r for r in ran if r["category"] == cat]
        print(f"  {cat:<16} {pct(sum(1 for r in rs if r['status'] == 'PASS'), len(rs))}")

    tool_rs = [r for r in ran if "tools" in r["checks"]]
    print(f"\nTOOL USE           {pct(sum(1 for r in tool_rs if r['checks']['tools']), len(tool_rs))}  (right tool called)")

    esc = [r for r in ran if "expect_escalated" in r["case"]]
    tp = sum(1 for r in esc if r["case"]["expect_escalated"] and r["escalated"])
    fn = sum(1 for r in esc if r["case"]["expect_escalated"] and not r["escalated"])
    fp = sum(1 for r in esc if not r["case"]["expect_escalated"] and r["escalated"])
    print(f"ESCALATION         recall {pct(tp, tp + fn)}   precision {pct(tp, tp + fp)}   false escalations: {fp}")

    lang = [r for r in ran if "language" in r["checks"]]
    print(f"LANGUAGE           {pct(sum(1 for r in lang if r['checks']['language']), len(lang))}  (reply in the customer's language)")

    lat = [r["latency"] for r in ran if r["latency"]]
    if lat:
        lat.sort()
        print(f"REPLY TIME         avg {sum(lat) / len(lat) / 1000:.1f}s   slowest {lat[-1] / 1000:.1f}s")

    if args.json:
        slim = [{k: v for k, v in r.items() if k != "case"} for r in results]
        Path(args.json).write_text(json.dumps(slim, indent=2, default=str))
        print(f"\nWrote {args.json}")
    sys.exit(0 if len(passed) == len(ran) else 1)


if __name__ == "__main__":
    main()
