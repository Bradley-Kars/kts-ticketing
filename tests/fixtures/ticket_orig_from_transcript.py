#!/usr/bin/env python3
"""ticket — KTS Records ticketing CLI (stdlib only).

Operates on the Obsidian-based KTS Records vault in ~/Documents/KTS Records.
Ticket files: Tickets/TKT-YYYY-### - Client - Title.md with YAML frontmatter
(worklog list = billing source of truth).

Subcommands:
  list            List tickets (default: open). Flags: --all --waiting --overdue
                  --client X --json
  show ID         Print a ticket (frontmatter + body)
  new             Create a ticket. --client "Name" --title "..." [--priority]
                  [--category] [--channel] [--status] [--follow-up] [--summary]
  log ID          Append a work-log entry (billing). --mode --hours --amount
                  --payment [--date] [--note]
  status ID       Set status/priority/follow-up. --status X [--priority Y]
                  [--follow-up DATE] [--clear-follow-up]
  bill            Outstanding/paid summary by client (also --json)
  refresh         Regenerate Dashboard.md billing block (between markers)
  next-id         Print the next ticket ID

Exit code 0 on success, 1 on error. Never touches credentials.
"""

import argparse, datetime as dt, json, os, re, sys, textwrap

VAULT = os.path.expanduser("~/Documents/KTS Records")
TICKETS_DIR = os.path.join(VAULT, "Tickets")
DASHBOARD = os.path.join(VAULT, "Dashboard.md")
BILL_START = "<!-- TICKET-BILLING-START -->"
BILL_END = "<!-- TICKET-BILLING-END -->"

STATUSES = ("new", "triaged", "in-progress", "waiting", "resolved", "closed", "cancelled")
PRIORITIES = ("low", "medium", "high", "urgent")
CATEGORIES = ("support", "setup", "maintenance", "project", "billing", "follow-up")
MODES = ("onsite", "remote", "email", "phone")
PAYMENTS = ("paid-cash", "paid-card", "paid-check", "invoiced", "uninvoiced", "no-charge")
OPEN_STATUSES = ("new", "triaged", "in-progress", "waiting")


def err(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def today():
    return dt.date.today().isoformat()


def parse_frontmatter(text):
    """Very small YAML-subset parser for ticket frontmatter."""
    fm = {}
    if not text.startswith("---"):
        return fm
    lines = text.splitlines()
    i = 1
    in_worklog = False
    worklog = []
    current = None
    while i < len(lines) and lines[i].strip() != "---":
        line = lines[i]
        i += 1
        if not line.strip():
            continue
        if in_worklog:
            m = re.match(r"^  - (\w+):\s*(.*)$", line)
            if m:
                if current is not None:
                    worklog.append(current)
                current = {m.group(1): m.group(2).strip()}
            elif current is not None:
                m2 = re.match(r"^    (\w+):\s*(.*)$", line)
                if m2:
                    current[m2.group(1)] = m2.group(2).strip()
            continue
        m = re.match(r"^(\w[\w-]*):\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if key == "worklog":      # list key — enters worklog mode even with empty value
            in_worklog = True
            continue
        if not val or val.startswith("#"):
            continue
        # strip inline comments, surrounding quotes, and wikilink brackets
        if " #" in val:
            val = val.split(" #", 1)[0].strip()
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1]
        if val.startswith("[[") and val.endswith("]]"):
            val = val[2:-2]
        fm[key] = val
    if current is not None:
        worklog.append(current)
    fm["_worklog"] = worklog
    return fm


def load_tickets():
    tickets = []
    if not os.path.isdir(TICKETS_DIR):
        return tickets
    for fn in sorted(os.listdir(TICKETS_DIR)):
        if not fn.endswith(".md"):
            continue
        path = os.path.join(TICKETS_DIR, fn)
        with open(path, encoding="utf-8") as f:
            fm = parse_frontmatter(f.read())
        if not fm.get("id"):
            continue
        fm["_file"] = path
        fm["_filename"] = fn
        tickets.append(fm)
    return tickets


def find_ticket(tickets, tid):
    tid = tid.upper()
    for t in tickets:
        if t.get("id", "").upper() == tid:
            return t
    return None


def next_id(tickets, year=None):
    year = year or dt.date.today().year
    prefix = f"TKT-{year}-"
    maxn = 0
    for t in tickets:
        mid = t.get("id", "")
        if mid.startswith(prefix):
            try:
                maxn = max(maxn, int(mid[len(prefix):]))
            except ValueError:
                pass
    return f"{prefix}{maxn + 1:03d}"


def default_prio(cat):
    return "high" if cat == "project" else "medium"


def client_exists(name):
    for sub in ("Business", "Residential"):
        p = os.path.join(VAULT, "Clients", sub, f"{name}.md")
        if os.path.isfile(p):
            return True
    return False


def sanitize_title(title):
    return re.sub(r'[\\/:*?"<>|]', "-", title).strip()


def gen_new(args):
    tickets = load_tickets()
    tid = next_id(tickets)
    if not args.client or not args.title:
        err("--client and --title are required")
    if not client_exists(args.client):
        print(f"warning: no client file found for '{args.client}' — ticketing still works, "
              f"but consider adding the client first", file=sys.stderr)
    cat = args.category or "support"
    prio = args.priority or default_prio(cat)
    if prio not in PRIORITIES:
        err(f"--priority must be one of {', '.join(PRIORITIES)}")
    if cat not in CATEGORIES:
        err(f"--category must be one of {', '.join(CATEGORIES)}")
    st = args.status or "new"
    if st not in STATUSES:
        err(f"--status must be one of {', '.join(STATUSES)}")
    title = args.title.strip()
    slug = sanitize_title(title)
    fn = f"{tid} - {args.client} - {slug}.md"
    path = os.path.join(TICKETS_DIR, fn)
    if os.path.exists(path):
        err(f"file already exists: {fn}")
    now = today()
    fu = args.follow_up or ""
    summary = args.summary or ""
    front = textwrap.dedent(f"""\
        ---
        type: ticket
        id: {tid}
        client: "[[{args.client}]]"
        title: {title}
        status: {st}
        priority: {prio}
        category: {cat}
        created: {now}
        updated: {now}
        due:
        follow_up: {fu}
        channel: {args.channel or "email"}
        tags: []
        worklog:
        ---

        # {tid} — {args.client} — {title}

        ## Summary
        {summary or "Open ticket."}

        ## Description
        Details: symptoms, client's words, context. Copy from the intake email or call notes.

        ## Checklist
        - [ ]

        ## Work Log
        <!-- Oldest at top, newest at bottom. YAML worklog is the billing truth. -->

        ## Related
        - 

        ## Resolution
        <!-- Filled when resolved/closed. -->
        """)
    with open(path, "w", encoding="utf-8") as f:
        f.write(front)
    print(f"created {tid}: {path}")
    return 0


def _patch(path, old, new):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if old not in text:
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.replace(old, new, 1))
    return True


def _set_field(path, field, value, default=""):
    """Replace `field: X` with `field: value` in frontmatter; add if missing."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    pat = re.compile(rf"^({field}:).*$", re.M)
    if pat.search(text):
        new_text = pat.sub(rf"\1 {value}", text, count=1)
    else:
        # insert after status: line or at top of frontmatter
        if text.startswith("---\n"):
            new_text = text.replace("---\n", f"---\n{field}: {value}\n", 1)
        else:
            new_text = f"{field}: {value}\n{text}"
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text)
    return True


def cmd_log(args):
    tickets = load_tickets()
    t = find_ticket(tickets, args.id) or err(f"ticket not found: {args.id}")
    path = t["_file"]
    d = args.date or today()
    if args.mode not in MODES:
        err(f"--mode must be one of {', '.join(MODES)}")
    if args.payment not in PAYMENTS:
        err(f"--payment must be one of {', '.join(PAYMENTS)}")
    hours = float(args.hours or 0)
    amount = float(args.amount or 0)
    note = (args.note or "").strip()

    # 1) YAML worklog entry (billing truth)
    entry = (f"  - date: {d}\n"
             f"    mode: {args.mode}\n"
             f"    hours: {hours:g}\n"
             f"    amount: {amount:g}\n"
             f"    payment: {args.payment}\n")
    if not _patch(path, "worklog:\n---", "worklog:\n" + entry + "---"):
        err(f"could not locate worklog block in {path}")

    # 2) prose entry under ## Work Log (before ## Related if present)
    header = f"### {d} — {args.mode} — {hours:g}h — ${amount:g} — {args.payment}\n"
    body = f"{note}\n\n" if note else "\n"
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if "## Related" in text:
        new_text = text.replace("## Related", header + body + "## Related", 1)
    else:
        new_text = text.rstrip() + "\n\n" + header + body
    # 3) bump updated: (only if added entry)
    new_text = re.sub(r"^(updated:).*$", rf"\1 {today()}", new_text, count=1, flags=re.M)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text)
    print(f"logged {d} on {t['id']}: {hours:g}h, ${amount:g}, {args.payment}")
    return 0


def cmd_status(args):
    tickets = load_tickets()
    t = find_ticket(tickets, args.id) or err(f"ticket not found: {args.id}")
    path = t["_file"]
    if args.status:
        if args.status not in STATUSES:
            err(f"--status must be one of {', '.join(STATUSES)}")
        _set_field(path, "status", args.status)
        print(f"{t['id']}: status -> {args.status}")
    if args.priority:
        if args.priority not in PRIORITIES:
            err(f"--priority must be one of {', '.join(PRIORITIES)}")
        _set_field(path, "priority", args.priority)
        print(f"{t['id']}: priority -> {args.priority}")
    if args.follow_up:
        _set_field(path, "follow_up", args.follow_up)
        print(f"{t['id']}: follow_up -> {args.follow_up}")
    if args.clear_follow_up:
        _set_field(path, "follow_up", "")
        print(f"{t['id']}: follow_up cleared")
    _set_field(path, "updated", today())
    return 0


def _owed(t):
    total = 0.0
    for e in t.get("_worklog", []):
        if e.get("payment") in ("invoiced", "uninvoiced"):
            try:
                total += float(e.get("amount", 0) or 0)
            except ValueError:
                pass
    return total


def _paid(t):
    total = 0.0
    for e in t.get("_worklog", []):
        if e.get("payment", "").startswith("paid-"):
            try:
                total += float(e.get("amount", 0) or 0)
            except ValueError:
                pass
    return total


def cmd_bill(args):
    tickets = load_tickets()
    out, paid = {}, {}
    for t in tickets:
        owe = _owed(t)
        pd = _paid(t)
        if owe:
            out.setdefault(t.get("client", "?"), 0.0)
            out[t.get("client", "?")] += owe
        if pd:
            paid.setdefault(t.get("client", "?"), 0.0)
            paid[t.get("client", "?")] += pd
    if args.json:
        print(json.dumps({"outstanding": out, "paid": paid,
                          "total_outstanding": round(sum(out.values()), 2),
                          "total_paid": round(sum(paid.values()), 2)}, indent=2))
        return 0
    print("OUTSTANDING (invoiced + uninvoiced):")
    if not out:
        print("  none")
    for c, v in sorted(out.items(), key=lambda kv: -kv[1]):
        print(f"  {c}: ${v:,.2f}")
    print(f"  TOTAL: ${sum(out.values()):,.2f}")
    print("PAID:")
    if not paid:
        print("  none")
    for c, v in sorted(paid.items(), key=lambda kv: -kv[1]):
        print(f"  {c}: ${v:,.2f}")
    print(f"  TOTAL: ${sum(paid.values()):,.2f}")
    return 0


def cmd_refresh(args):
    tickets = load_tickets()
    lines = ["", "| Ticket | Client | Amount | Payment |", "| --- | --- | --- | --- |"]
    total = 0.0
    rows = []
    for t in sorted(tickets, key=lambda x: x.get("id", "")):
        for e in t.get("_worklog", []):
            p = e.get("payment", "")
            if p not in ("invoiced", "uninvoiced"):
                continue
            amt = float(e.get("amount", 0) or 0)
            total += amt
            rows.append((t.get("id", "?"), t.get("client", "?"), amt, p))
    for rid, c, amt, p in rows:
        lines.append(f"| [[{rid}]] | {c} | ${amt:,.2f} | {p} |")
    lines.append("")
    lines.append(f"**Total outstanding: ${total:,.2f}**")
    block = BILL_START + "\n" + "\n".join(lines) + "\n" + BILL_END
    with open(DASHBOARD, encoding="utf-8") as f:
        text = f.read()
    if BILL_START not in text or BILL_END not in text:
        err("Dashboard.md missing billing markers — run `ticket refresh` after adding them")
    new_text = re.sub(re.escape(BILL_START) + ".*?" + re.escape(BILL_END),
                      lambda m: block, text, flags=re.S)
    with open(DASHBOARD, "w", encoding="utf-8") as f:
        f.write(new_text)
    print(f"dashboard refreshed: outstanding ${total:,.2f}")
    return 0


def cmd_list(args):
    tickets = load_tickets()
    if args.client:
        tickets = [t for t in tickets if args.client.lower() in t.get("client", "").lower()]
    if args.waiting:
        tickets = [t for t in tickets if t.get("status") == "waiting"]
    elif not args.all:
        tickets = [t for t in tickets if t.get("status") in OPEN_STATUSES]
    if args.overdue:
        now = today()
        tickets = [t for t in tickets
                   if t.get("status") == "waiting" and t.get("follow_up")
                   and t["follow_up"] <= now]
    tickets.sort(key=lambda t: (PRIORITIES.index(t.get("priority", "low"))
                                if t.get("priority") in PRIORITIES else 99, t.get("id", "")))
    rows = []
    for t in tickets:
        rows.append({"id": t.get("id"), "status": t.get("status"),
                     "priority": t.get("priority"), "client": t.get("client"),
                     "title": t.get("title"), "follow_up": t.get("follow_up", ""),
                     "owed": _owed(t)})
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("no tickets")
        return 0
    hdr = f"{'ID':<14} {'STATUS':<11} {'PRIO':<7} {'FOLLOW-UP':<10} {'OWED':>7}  {'TICKET'}"
    print(hdr); print("-" * len(hdr))
    for r in rows:
        owed = f"${r['owed']:,.0f}" if r["owed"] else "-"
        print(f"{r['id']:<14} {r['status']:<11} {r['priority']:<7} {r['follow_up']:<10} "
              f"{owed:>7}  {r['client']} — {r['title']}")
    return 0


def cmd_show(args):
    tickets = load_tickets()
    t = find_ticket(tickets, args.id) or err(f"ticket not found: {args.id}")
    with open(t["_file"], encoding="utf-8") as f:
        print(f.read())
    return 0


def main():
    ap = argparse.ArgumentParser(prog="ticket", description="KTS Records ticketing CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="list tickets (default open)")
    p.add_argument("--all", action="store_true", help="include closed/cancelled")
    p.add_argument("--waiting", action="store_true", help="waiting only")
    p.add_argument("--overdue", action="store_true", help="waiting with follow_up <= today")
    p.add_argument("--client", help="filter by client name substring")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("show", help="show a ticket")
    p.add_argument("id")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("new", help="create a ticket")
    p.add_argument("--client", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--priority", choices=PRIORITIES)
    p.add_argument("--category", choices=CATEGORIES)
    p.add_argument("--channel", choices=MODES + ("web",))
    p.add_argument("--status", choices=STATUSES)
    p.add_argument("--follow-up")
    p.add_argument("--summary")
    p.set_defaults(fn=gen_new)

    p = sub.add_parser("log", help="append work-log entry (billing)")
    p.add_argument("id")
    p.add_argument("--date")
    p.add_argument("--mode", choices=MODES, default="remote")
    p.add_argument("--hours", default="0")
    p.add_argument("--amount", default="0")
    p.add_argument("--payment", choices=PAYMENTS, default="uninvoiced")
    p.add_argument("--note")
    p.set_defaults(fn=cmd_log)

    p = sub.add_parser("status", help="update status/priority/follow-up")
    p.add_argument("id")
    p.add_argument("--status", choices=STATUSES)
    p.add_argument("--priority", choices=PRIORITIES)
    p.add_argument("--follow-up")
    p.add_argument("--clear-follow-up", action="store_true")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("bill", help="outstanding/paid summary")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_bill)

    p = sub.add_parser("refresh", help="regenerate Dashboard billing block")
    p.set_defaults(fn=cmd_refresh)

    p = sub.add_parser("next-id", help="print next ticket ID")
    p.set_defaults(fn=lambda a: print(next_id(load_tickets())) and 0)

    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()

