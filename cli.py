#!/usr/bin/env python3
"""kts — KTS Records unified CLI (thin wrapper over KTS Core API)"""

import argparse
import json
import sys
import os
from datetime import datetime
from typing import Optional, List

from core.api import KTSAPI, get_api
from core.models import (
    EventActor, WorklogEntry, EntityRef, Relationship,
    Ticket, Client, Contact, Organization, Asset, User,
    Project, Task, Knowledge, Problem, Change, Request, Contract
)
from core.services.ticket_service import TicketService, enforce_isolation


def main():
    parser = argparse.ArgumentParser(prog="kts", description="KTS Records unified CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # Global options
    parser.add_argument("--vault", help="Vault path; must match the active vault (sandbox-aware, cannot override)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--actor", help="Actor ID (default: USR-000001)")

    # =========================================================================
    # TICKET COMMANDS
    # =========================================================================
    p_ticket = sub.add_parser("ticket", help="Ticket operations")
    t_sub = p_ticket.add_subparsers(dest="ticket_cmd", required=True)

    # ticket list
    p_list = t_sub.add_parser("list", help="List tickets")
    p_list.add_argument("--all", action="store_true", help="Include closed/cancelled")
    p_list.add_argument("--waiting", action="store_true", help="Waiting only")
    p_list.add_argument("--overdue", action="store_true", help="Waiting with follow_up <= today")
    p_list.add_argument("--client", help="Filter by client name")
    p_list.add_argument("--status", help="Filter by status")
    p_list.add_argument("--priority", help="Filter by priority")
    p_list.add_argument("--assignee", help="Filter by assignee")

    # ticket show
    p_show = t_sub.add_parser("show", help="Show a ticket")
    p_show.add_argument("id", help="Ticket ID")

    # ticket new
    p_new = t_sub.add_parser("new", help="Create a ticket")
    p_new.add_argument("--client", required=True, help="Client name")
    p_new.add_argument("--title", required=True, help="Ticket title")
    p_new.add_argument("--type", default="incident", help="Ticket type")
    p_new.add_argument("--priority", choices=["critical", "high", "medium", "low", "informational"])
    p_new.add_argument("--category", choices=["support", "setup", "maintenance", "project", "billing", "follow-up", "security", "backup"])
    p_new.add_argument("--work-type", dest="work_type", choices=["virus", "network", "email", "hardware", "software", "audio", "security", "backup", "consult", "other"])
    p_new.add_argument("--channel", choices=["phone", "email", "onsite", "remote", "web"], default="phone")
    p_new.add_argument("--status", choices=["new", "triaged", "in-progress", "waiting", "resolved", "closed", "cancelled"], default="new")
    p_new.add_argument("--org", dest="organization_id", help="Organization ID")
    p_new.add_argument("--client-id", dest="client_id", help="Client (person) ID, CUS-")
    p_new.add_argument("--contact", dest="contact_id", help="Contact ID (deprecated, transition only)")
    p_new.add_argument("--asset", dest="asset_id", help="Asset ID")
    p_new.add_argument("--summary", help="Initial summary")

    # ticket log
    p_log = t_sub.add_parser("log", help="Add worklog entry")
    p_log.add_argument("id", help="Ticket ID")
    p_log.add_argument("--date", help="Date (YYYY-MM-DD, default: today)")
    p_log.add_argument("--mode", choices=["onsite", "remote", "email", "phone", "web"], default="remote")
    p_log.add_argument("--hours", type=float, default=0)
    p_log.add_argument("--amount", type=float, default=0)
    p_log.add_argument("--payment", choices=["paid-cash", "paid-card", "paid-check", "invoiced", "uninvoiced", "no-charge"], default="uninvoiced")
    p_log.add_argument("--note", help="Work description")

    # ticket status
    p_status = t_sub.add_parser("status", help="Change ticket status")
    p_status.add_argument("id", help="Ticket ID")
    p_status.add_argument("--status", choices=["new", "triaged", "in-progress", "waiting", "resolved", "closed", "cancelled"], required=True)
    p_status.add_argument("--priority", choices=["critical", "high", "medium", "low", "informational"])
    p_status.add_argument("--follow-up", help="Follow-up date (YYYY-MM-DD)")
    p_status.add_argument("--clear-follow-up", action="store_true")

    # ticket assign
    p_assign = t_sub.add_parser("assign", help="Assign ticket to user")
    p_assign.add_argument("id", help="Ticket ID")
    p_assign.add_argument("--user", required=True, help="User ID (USR-XXXXXX)")

    # ticket link
    p_link = t_sub.add_parser("link", help="Link ticket to another entity")
    p_link.add_argument("id", help="Ticket ID")
    p_link.add_argument("--entity", required=True, help="Target entity ID")
    p_link.add_argument("--type", required=True, help="Relationship type")

    # ticket similar
    p_similar = t_sub.add_parser("similar", help="Find similar tickets")
    p_similar.add_argument("id", help="Ticket ID")
    p_similar.add_argument("--limit", type=int, default=5)

    # ticket ai-summary
    p_ai_sum = t_sub.add_parser("ai-summary", help="Generate AI summary")
    p_ai_sum.add_argument("id", help="Ticket ID")

    # ticket ai-troubleshoot
    p_ai_trouble = t_sub.add_parser("ai-troubleshoot", help="AI troubleshooting suggestions")
    p_ai_trouble.add_argument("id", help="Ticket ID")

    # ticket bill
    p_bill = t_sub.add_parser("bill", help="Show billing summary")
    p_bill.add_argument("--client", help="Filter by client")

    # ticket refresh
    t_sub.add_parser("refresh", help="Regenerate Dashboard.md billing block")

    # ticket next-id
    t_sub.add_parser("next-id", help="Show next ticket ID")

    # =========================================================================
    # CLIENT COMMANDS
    # =========================================================================
    p_client = sub.add_parser("client", help="Client operations")
    c_sub = p_client.add_subparsers(dest="client_cmd", required=True)

    c_sub.add_parser("list", help="List clients").add_argument("--type", choices=["business", "residential"])

    p_cshow = c_sub.add_parser("show", help="Show client")
    p_cshow.add_argument("id")

    p_cnew = c_sub.add_parser("new", help="Create client")
    p_cnew.add_argument("--name", required=True)
    p_cnew.add_argument("--type", choices=["business", "residential"], required=True)
    p_cnew.add_argument("--company", help="Company name (business)")
    p_cnew.add_argument("--email", action="append")
    p_cnew.add_argument("--phone", action="append")
    p_cnew.add_argument("--address", help="Address")
    p_cnew.add_argument("--rate", type=float, help="Hourly rate")
    p_cnew.add_argument("--terms", choices=["due-upon-receipt", "net15", "net30", "net60"])
    p_cnew.add_argument("--org", dest="organization_id", help="Organization ID")

    # =========================================================================
    # CONTACT COMMANDS
    # =========================================================================
    p_contact = sub.add_parser("contact", help="Contact operations")
    co_sub = p_contact.add_subparsers(dest="contact_cmd", required=True)

    co_sub.add_parser("list", help="List contacts").add_argument("--org", help="Filter by organization")

    p_co_show = co_sub.add_parser("show", help="Show contact")
    p_co_show.add_argument("id")

    # B2/B4 merge: `contact new` removed (people are created as clients); list/show kept read-only until Phase F.

    # =========================================================================
    # ORGANIZATION COMMANDS
    # =========================================================================
    p_org = sub.add_parser("org", help="Organization operations")
    o_sub = p_org.add_subparsers(dest="org_cmd", required=True)

    o_sub.add_parser("list", help="List organizations")

    p_o_show = o_sub.add_parser("show", help="Show organization")
    p_o_show.add_argument("id")

    p_o_new = o_sub.add_parser("new", help="Create organization")
    p_o_new.add_argument("--name", required=True)
    p_o_new.add_argument("--industry")
    p_o_new.add_argument("--size", choices=["small", "medium", "large", "enterprise"])
    p_o_new.add_argument("--tier", choices=["managed", "project", "break-fix", "vendor"])
    p_o_new.add_argument("--address")
    p_o_new.add_argument("--rate", type=float)
    p_o_new.add_argument("--terms", choices=["due-upon-receipt", "net15", "net30", "net60"])

    # =========================================================================
    # ASSET COMMANDS
    # =========================================================================
    p_asset = sub.add_parser("asset", help="Asset operations")
    a_sub = p_asset.add_subparsers(dest="asset_cmd", required=True)

    a_sub.add_parser("list", help="List assets").add_argument("--org", help="Filter by organization")

    p_a_show = a_sub.add_parser("show", help="Show asset")
    p_a_show.add_argument("id")

    p_a_new = a_sub.add_parser("new", help="Create asset")
    p_a_new.add_argument("--hostname", required=True)
    p_a_new.add_argument("--type", required=True, choices=["workstation", "laptop", "server", "network", "printer", "mobile", "software", "storage", "ups", "camera", "access_control", "phone_system", "other"])
    p_a_new.add_argument("--org", required=True, help="Organization ID")
    p_a_new.add_argument("--serial")
    p_a_new.add_argument("--manufacturer")
    p_a_new.add_argument("--model")
    p_a_new.add_argument("--os")

    # =========================================================================
    # USER COMMANDS
    # =========================================================================
    p_user = sub.add_parser("user", help="User/technician operations")
    u_sub = p_user.add_subparsers(dest="user_cmd", required=True)

    u_sub.add_parser("list", help="List users")

    p_u_show = u_sub.add_parser("show", help="Show user")
    p_u_show.add_argument("id")

    # =========================================================================
    # PROJECT COMMANDS
    # =========================================================================
    p_project = sub.add_parser("project", help="Project operations")
    pj_sub = p_project.add_subparsers(dest="project_cmd", required=True)

    pj_sub.add_parser("list", help="List projects")

    p_pj_show = pj_sub.add_parser("show", help="Show project")
    p_pj_show.add_argument("id")

    # =========================================================================
    # TASK COMMANDS
    # =========================================================================
    p_task = sub.add_parser("task", help="Task operations")
    tk_sub = p_task.add_subparsers(dest="task_cmd", required=True)

    tk_sub.add_parser("list", help="List tasks")

    # =========================================================================
    # KNOWLEDGE COMMANDS
    # =========================================================================
    p_kb = sub.add_parser("knowledge", help="Knowledge base operations")
    kb_sub = p_kb.add_subparsers(dest="kb_cmd", required=True)

    kb_sub.add_parser("list", help="List articles")

    # =========================================================================
    # SEARCH
    # =========================================================================
    p_search = sub.add_parser("search", help="Search across all entities")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--type", help="Entity type filter")
    p_search.add_argument("--limit", type=int, default=20)

    # =========================================================================
    # INVOICE / RECEIPT NUMBERING PREVIEWS (Option B — read-only, never persists)
    # =========================================================================
    p_invoice = sub.add_parser("invoice", help="Invoice operations")
    inv_sub = p_invoice.add_subparsers(dest="invoice_cmd", required=True)
    inv_sub.add_parser("next-id", help="Preview next invoice number (INV-YYYY-###, no write)")

    p_receipt = sub.add_parser("receipt", help="Receipt operations")
    rcp_sub = p_receipt.add_subparsers(dest="receipt_cmd", required=True)
    rcp_sub.add_parser("next-id", help="Preview next receipt number (RCP-YYYY-###, no write)")

    # =========================================================================
    # INDEX / VALIDATE
    # =========================================================================
    sub.add_parser("index", help="Rebuild all indexes")
    sub.add_parser("validate", help="Validate vault integrity").add_argument("--fix", action="store_true")

    # =========================================================================
    # MIGRATIONS
    # =========================================================================
    p_migrate = sub.add_parser("migrate", help="Run migration")
    p_migrate.add_argument("version", help="Migration version")
    p_migrate.add_argument("--force", action="store_true")

    sub.add_parser("migrate-status", help="Show migration status")

    # =========================================================================
    # SANDBOX
    # =========================================================================
    p_sandbox = sub.add_parser(
        "sandbox",
        help="Test sandbox lifecycle (full isolated copy — never touches real data)",
    )
    sb_sub = p_sandbox.add_subparsers(dest="sandbox_cmd", required=True)
    sb_sub.add_parser("create", help="Create a fresh sandbox (refuses if one already exists)")
    sb_sub.add_parser("reset", help="Wipe the sandbox (only the sandbox) and recreate it")
    sb_sub.add_parser("status", help="Show active mode, paths, and sandbox state")
    sb_sub.add_parser("env", help="Print export lines to activate the sandbox")

    # =========================================================================
    # EVENTS
    # =========================================================================
    p_events = sub.add_parser("events", help="Show event history")
    p_events.add_argument("entity", help="Entity reference (e.g., TKT-2026-001)")
    p_events.add_argument("--limit", type=int, default=50)

    # =========================================================================
    # AI
    # =========================================================================
    p_ai = sub.add_parser("ai", help="AI operations")
    ai_sub = p_ai.add_subparsers(dest="ai_cmd", required=True)

    p_ai_classify = ai_sub.add_parser("classify", help="Classify ticket")
    p_ai_classify.add_argument("id", help="Ticket ID")

    p_ai_similar = ai_sub.add_parser("similar", help="Find similar tickets")
    p_ai_similar.add_argument("id", help="Ticket ID")

    p_ai_trouble = ai_sub.add_parser("troubleshoot", help="Generate troubleshooting")
    p_ai_trouble.add_argument("id", help="Ticket ID")

    p_ai_summary = ai_sub.add_parser("summary", help="Generate summary")
    p_ai_summary.add_argument("id", help="Ticket ID")

    # =========================================================================
    # PARSE AND EXECUTE
    # =========================================================================
    args = parser.parse_args()

    # Fail fast if sandbox env vars are partial/overlapping (also re-checked
    # inside KTSAPI.__init__, this just catches it before argparse side effects).
    enforce_isolation()

    # Setup: pass --vault through as-is (may be None). KTSAPI resolves the
    # active vault from paths.py and REFUSES a --vault that disagrees with it,
    # so --vault can never override sandbox mode.
    actor_id = args.actor or "USR-000001"
    actor = EventActor(id=actor_id, type="human", name="Bradley Kars")

    api = KTSAPI(args.vault)

    try:
        # Execute command
        if args.cmd == "ticket":
            result = handle_ticket(api, actor, args)
        elif args.cmd == "client":
            result = handle_client(api, actor, args)
        elif args.cmd == "contact":
            result = handle_contact(api, actor, args)
        elif args.cmd == "org":
            result = handle_org(api, actor, args)
        elif args.cmd == "asset":
            result = handle_asset(api, actor, args)
        elif args.cmd == "user":
            result = handle_user(api, actor, args)
        elif args.cmd == "project":
            result = handle_project(api, actor, args)
        elif args.cmd == "task":
            result = handle_task(api, actor, args)
        elif args.cmd == "knowledge":
            result = handle_knowledge(api, actor, args)
        elif args.cmd == "search":
            result = handle_search(api, args)
        elif args.cmd == "invoice":
            result = handle_invoice(args)
        elif args.cmd == "receipt":
            result = handle_receipt(args)
        elif args.cmd == "index":
            result = handle_index(args)
        elif args.cmd == "validate":
            result = handle_validate(args)
        elif args.cmd == "migrate":
            result = handle_migrate(args)
        elif args.cmd == "migrate-status":
            result = handle_migrate_status(args)
        elif args.cmd == "events":
            result = handle_events(args)
        elif args.cmd == "sandbox":
            result = handle_sandbox(args)
        elif args.cmd == "ai":
            result = handle_ai(args)
        else:
            parser.print_help()
            return 1

        if args.json and result is not None:
            print(json.dumps(result, default=str, indent=2))
        return 0

    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        if os.environ.get("KTS_DEBUG"):
            import traceback
            traceback.print_exc()
        return 1


# =========================================================================
# Command Handlers
# =========================================================================

def handle_ticket(api: KTSAPI, actor: EventActor, args) -> Any:
    if args.ticket_cmd == "list":
        ticket_svc = api.get_service("ticket")
        # CLI flags are NOT frontmatter keys: service.list() matches raw FM
        # keys, so passing all/waiting/overdue through as filters returned
        # zero rows. Pop them and apply their semantics client-side.
        want_all = bool(args.all)
        want_waiting = bool(args.waiting)
        want_overdue = bool(args.overdue)
        filters = {
            k: v for k, v in vars(args).items()
            if v and k not in ["ticket_cmd", "cmd", "vault", "json", "actor",
                               "all", "waiting", "overdue"]
        }
        if "client" in filters:
            filters["client_name"] = filters.pop("client")
        tickets = ticket_svc.list(filters=filters, limit=500)
        open_statuses = ("new", "triaged", "in-progress", "waiting")
        today = datetime.now().date().isoformat()
        shown = []
        for t in tickets:
            if want_overdue:
                if t.status != "waiting" or not getattr(t, "follow_up", None):
                    continue
                if str(t.follow_up) > today:
                    continue
            elif want_waiting:
                if t.status != "waiting":
                    continue
            elif not want_all and t.status not in open_statuses:
                continue
            shown.append(t)
        if args.json:
            return [t.model_dump(by_alias=True) for t in shown]
        for t in shown:
            client = getattr(t, "client_name", "") or getattr(t, "client", "") or ""
            print(f"{t.id:<14} {t.status:<11} {t.priority:<7} {client:<30} {t.title}")

    elif args.ticket_cmd == "show":
        ticket = api.get_ticket(args.id)
        if not ticket:
            print(f"Ticket {args.id} not found", file=sys.stderr)
            return 1
        if args.json:
            return ticket.model_dump(by_alias=True)
        print(ticket.model_dump_json(by_alias=True, indent=2))

    elif args.ticket_cmd == "new":
        data = {
            "title": args.title,
            "ticket_type": args.type,
            "priority": args.priority or "medium",
            "category": args.category or "support",
            "work_type": args.work_type or "other",
            "channel": args.channel,
            "status": args.status,
            "asset_id": args.asset_id,
        }
        # Only add organization_id if it matches the expected pattern
        if args.organization_id and args.organization_id.startswith("ORG-"):
            data["organization_id"] = args.organization_id
        if args.client:
            data["client_name"] = args.client
        if args.contact_id:
            data["contact_id"] = args.contact_id
        if getattr(args, "client_id", None):
            data["client_id"] = args.client_id
        if args.asset_id:
            data["asset_id"] = args.asset_id
        if args.summary:
            data["description"] = args.summary
        ticket = api.create_ticket(data, actor)
        print(f"Created {ticket.id}: {ticket.title}")
        return ticket.model_dump(by_alias=True)

    elif args.ticket_cmd == "log":
        entry = WorklogEntry(
            date=datetime.fromisoformat(args.date) if args.date else datetime.now(),
            mode=args.mode,
            hours=args.hours,
            amount=args.amount,
            payment=args.payment,
            description=args.note,
        )
        ticket = api.add_worklog(args.id, entry, actor)
        print(f"Logged {entry.hours:g}h, ${entry.amount:g} on {args.id}")
        return ticket.model_dump(by_alias=True)

    elif args.ticket_cmd == "status":
        # Finding 1 (Phase F, Bradley 2026-10-07): route status through
        # change_ticket_status so the dedicated ticket.status_changed event
        # is emitted, matching the API/AI service path. priority and
        # clear-follow-up are not status changes and still use the generic
        # update (ticket.updated).
        follow = datetime.fromisoformat(args.follow_up) if args.follow_up else None
        ticket = api.change_ticket_status(args.id, args.status, actor, follow_up=follow)
        extra = {}
        if args.priority:
            extra["priority"] = args.priority
        if args.clear_follow_up:
            extra["follow_up"] = None
        if extra:
            ticket = api.update_ticket(args.id, extra, actor)
        print(f"{args.id}: status -> {args.status}")
        return ticket.model_dump(by_alias=True)

    elif args.ticket_cmd == "assign":
        ticket = api.assign_ticket(args.id, args.user, actor)
        print(f"{args.id}: assigned to {args.user}")
        return ticket.model_dump(by_alias=True)

    elif args.ticket_cmd == "link":
        api.link(
            EntityRef(type="ticket", id=args.id),
            EntityRef(type="ticket", id=args.entity),
            args.type,
            actor,
        )
        print(f"Linked {args.id} -> {args.entity} ({args.type})")

    elif args.ticket_cmd == "similar":
        ai_svc = api.get_service("ai")
        results = ai_svc.find_similar_tickets(args.id, limit=args.limit)
        if args.json:
            return results
        for r in results:
            print(f"  {r['id']} (score: {r['score']:.2f}) - {r['snippet'][:80]}")

    elif args.ticket_cmd == "ai-summary":
        ai_svc = api.get_service("ai")
        summary = ai_svc.generate_summary(args.id)
        print(summary)
        return {"summary": summary}

    elif args.ticket_cmd == "ai-troubleshoot":
        ai_svc = api.get_service("ai")
        suggestion = ai_svc.suggest_troubleshooting(args.id)
        print(f"Suggestion {suggestion.suggestion_id} created (pending approval)")
        return suggestion.model_dump(by_alias=True)

    elif args.ticket_cmd == "bill":
        ticket_svc = api.get_service("ticket")
        tickets = ticket_svc.list(limit=10000)
        # Aggregate billing
        return {"tickets": len(tickets)}

    elif args.ticket_cmd == "refresh":
        # Call the standalone ticket command's refresh function
        import subprocess
        result = subprocess.run(["ticket", "refresh"], capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Error: {result.stderr}", file=sys.stderr)
            return 1
        print(result.stdout.strip())

    elif args.ticket_cmd == "next-id":
        ticket_svc = api.get_service("ticket")
        print(ticket_svc.next_id())

    return None


def handle_client(api: KTSAPI, actor: EventActor, args) -> Any:
    if args.client_cmd == "list":
        client_svc = api.get_service("client")
        clients = client_svc.list(limit=1000)
        if args.json:
            return [c.model_dump(by_alias=True) for c in clients]
        for c in clients:
            print(f"{c.id}  {c.display_name or c.title}  ({c.class_})")

    elif args.client_cmd == "show":
        client = api.get("client", args.id)
        if not client:
            print(f"Client {args.id} not found", file=sys.stderr)
            return 1
        if args.json:
            return client.model_dump(by_alias=True)
        print(client.model_dump_json(by_alias=True, indent=2))

    elif args.client_cmd == "new":
        data = {
            "title": args.name,
            "class_": args.type,
            "company_name": args.company,
            "email": args.email or [],
            "phone": args.phone or [],
            "address": {"street": args.address} if args.address else None,
            "billing": {
                "rate": args.rate or 0,
                "terms": args.terms or "due-upon-receipt",
            },
            "organization_id": args.organization_id,
        }
        client = api.create("client", data, actor)
        print(f"Created client {client.id}: {client.title}")
        return client.model_dump(by_alias=True)

    return None


def handle_contact(api: KTSAPI, actor: EventActor, args) -> Any:
    if args.contact_cmd == "list":
        contact_svc = api.get_service("contact")
        contacts = contact_svc.list(limit=1000)
        if args.json:
            return [c.model_dump(by_alias=True) for c in contacts]
        for c in contacts:
            print(f"{c.id}  {c.display_name}  ({c.organization_id})")

    elif args.contact_cmd == "show":
        contact = api.get("contact", args.id)
        if not contact:
            print(f"Contact {args.id} not found", file=sys.stderr)
            return 1
        if args.json:
            return contact.model_dump(by_alias=True)
        print(contact.model_dump_json(by_alias=True, indent=2))

    return None


def handle_org(api: KTSAPI, actor: EventActor, args) -> Any:
    if args.org_cmd == "list":
        org_svc = api.get_service("organization")
        orgs = org_svc.list(limit=1000)
        if args.json:
            return [o.model_dump(by_alias=True) for o in orgs]
        for o in orgs:
            print(f"{o.id}  {o.name}  ({o.industry or 'unknown'})")

    elif args.org_cmd == "show":
        org = api.get("organization", args.id)
        if not org:
            print(f"Organization {args.id} not found", file=sys.stderr)
            return 1
        if args.json:
            return org.model_dump(by_alias=True)
        print(org.model_dump_json(by_alias=True, indent=2))

    elif args.org_cmd == "new":
        data = {
            "name": args.name,
            "industry": args.industry,
            "size": args.size,
            "tier": args.tier or "break-fix",
            "address": {"street": args.address} if args.address else None,
            "billing": {
                "rate": args.rate or 0,
                "terms": args.terms or "net30",
            },
        }
        org = api.create("organization", data, actor)
        print(f"Created organization {org.id}: {org.name}")
        return org.model_dump(by_alias=True)

    return None


def handle_asset(api: KTSAPI, actor: EventActor, args) -> Any:
    if args.asset_cmd == "list":
        asset_svc = api.get_service("asset")
        assets = asset_svc.list(limit=1000)
        if args.json:
            return [a.model_dump(by_alias=True) for a in assets]
        for a in assets:
            print(f"{a.id}  {a.hostname}  ({a.asset_type})  [{a.status}]")

    elif args.asset_cmd == "show":
        asset = api.get("asset", args.id)
        if not asset:
            print(f"Asset {args.id} not found", file=sys.stderr)
            return 1
        if args.json:
            return asset.model_dump(by_alias=True)
        print(asset.model_dump_json(by_alias=True, indent=2))

    elif args.asset_cmd == "new":
        data = {
            "hostname": args.hostname,
            "asset_type": args.type,
            "organization_id": args.org,
            "serial_number": args.serial,
            "manufacturer": args.manufacturer,
            "model": args.model,
            "operating_system": args.os,
        }
        asset = api.create("asset", data, actor)
        print(f"Created asset {asset.id}: {asset.hostname}")
        return asset.model_dump(by_alias=True)

    return None


def handle_user(api: KTSAPI, actor: EventActor, args) -> Any:
    if args.user_cmd == "list":
        user_svc = api.get_service("user")
        users = user_svc.list(limit=1000)
        if args.json:
            return [u.model_dump(by_alias=True) for u in users]
        for u in users:
            print(f"{u.id}  {u.display_name}  ({u.role})")

    elif args.user_cmd == "show":
        user = api.get("user", args.id)
        if not user:
            print(f"User {args.id} not found", file=sys.stderr)
            return 1
        if args.json:
            return user.model_dump(by_alias=True)
        print(user.model_dump_json(by_alias=True, indent=2))

    return None


def handle_project(api: KTSAPI, actor: EventActor, args) -> Any:
    if args.project_cmd == "list":
        project_svc = api.get_service("project")
        projects = project_svc.list(limit=1000)
        if args.json:
            return [p.model_dump(by_alias=True) for p in projects]
        for p in projects:
            print(f"{p.id}  {p.title}  [{p.status}]")

    elif args.project_cmd == "show":
        project = api.get("project", args.id)
        if not project:
            print(f"Project {args.id} not found", file=sys.stderr)
            return 1
        if args.json:
            return project.model_dump(by_alias=True)
        print(project.model_dump_json(by_alias=True, indent=2))

    return None


def handle_task(api: KTSAPI, actor: EventActor, args) -> Any:
    if args.task_cmd == "list":
        task_svc = api.get_service("task")
        tasks = task_svc.list(limit=1000)
        if args.json:
            return [t.model_dump(by_alias=True) for t in tasks]
        for t in tasks:
            print(f"{t.id}  {t.title}  [{t.status}]")

    return None


def handle_knowledge(api: KTSAPI, actor: EventActor, args) -> Any:
    if args.kb_cmd == "list":
        kb_svc = api.get_service("knowledge")
        articles = kb_svc.list(limit=1000)
        if args.json:
            return [k.model_dump(by_alias=True) for k in articles]
        for k in articles:
            print(f"{k.id}  {k.title}  [{k.status}]")

    return None


def handle_search(api: KTSAPI, args) -> Any:
    from core.models import SearchQuery
    query = SearchQuery(
        query=args.query,
        entity_types=[args.type] if args.type else None,
        limit=args.limit,
    )
    results = api.search_tickets(query)
    if args.json:
        return [r.model_dump() for r in results]
    for r in results:
        print(f"  {r.entity.type}:{r.entity.id} (score: {r.score:.2f}) - {r.snippet[:100]}")
    return None


def handle_index(args) -> Any:
    api = KTSAPI()
    stats = api.rebuild_indexes()
    if args.json:
        return stats
    print("Index rebuild complete:")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    return stats


def handle_sandbox(args) -> Any:
    from core import sandbox as sb

    if args.sandbox_cmd == "env":
        if args.json:
            return sb.activation_env()
        print("# Paste these into your shell to activate the test sandbox:")
        for line in sb.env_lines():
            print(line)
        print("# The sandbox .env contains no real Stripe keys.")
        print("# Unset all three to return to real data.")
        return 0

    if args.sandbox_cmd == "status":
        st = sb.status()
        if args.json:
            return st
        print(f"Mode:        {st['mode']}")
        print(f"Active vault:{st['active_vault']}")
        print(f"Active home: {st['active_home']}")
        print(f"Sandbox vault ({st['sandbox_vault_files']} files): "
              f"{'EXISTS' if st['sandbox_vault_exists'] else 'absent'} - {st['sandbox_vault']}")
        print(f"Sandbox home  ({st['sandbox_home_files']} files): "
              f"{'EXISTS' if st['sandbox_home_exists'] else 'absent'} - {st['sandbox_home']}")
        if not st["sandbox_exists"]:
            print("Sandbox not created yet. Run: kts sandbox create")
        else:
            print("Activate with: kts sandbox env")
        return 0

    # create / reset
    if args.sandbox_cmd == "create":
        summary = sb.create()
    else:
        summary = sb.reset()

    if args.json:
        return summary

    print(f"Sandbox {summary['action']}:")
    print(f"  vault: {summary['vault_files']} files -> {summary['sandbox_vault']}")
    print(f"  home:  {summary['home_files']} files -> {summary['sandbox_home']}")
    print(f"  real vault files (unchanged): {summary['real_vault_files']}")
    print(f"  sandbox .env has no real keys: {summary['sandbox_env_has_no_real_keys']}")
    print(f"  verified: {summary['verified']}")
    for problem in summary["problems"]:
        print(f"  PROBLEM: {problem}")
    print("Activate with: kts sandbox env")
    return 0 if summary["verified"] else 1


def handle_invoice(args) -> Any:
    """Invoice ops — next-id is a READ-ONLY peek (peek_seq, never next_seq)."""
    if args.invoice_cmd == "next-id":
        from core.counters import peek_seq
        from core.paths import vault_root
        year = datetime.now().strftime("%Y")
        scan_max = 0
        d = vault_root() / "Invoices"
        if d.exists():
            for f in d.glob(f"INV-{year}-*.json"):
                try:
                    scan_max = max(scan_max, int(f.stem.split("-")[-1]))
                except (ValueError, IndexError):
                    pass
        print(f"INV-{year}-{peek_seq(f'invoice:{year}', scan_max):03d}")
        return None
    return 1


def handle_receipt(args) -> Any:
    """Receipt ops — next-id is a READ-ONLY peek (peek_seq, never next_seq)."""
    if args.receipt_cmd == "next-id":
        from core.counters import peek_seq
        from core.paths import vault_root
        year = datetime.now().strftime("%Y")
        scan_max = 0
        d = vault_root() / "Receipts"
        if d.exists():
            for f in d.glob(f"RCP-{year}-*.json"):
                try:
                    scan_max = max(scan_max, int(f.stem.split("-")[-1]))
                except (ValueError, IndexError):
                    pass
        print(f"RCP-{year}-{peek_seq(f'receipt:{year}', scan_max):03d}")
        return None
    return 1


def handle_validate(args) -> Any:
    api = KTSAPI()
    report = api.validate(fix=args.fix)
    if args.json:
        return {
            "total_entities": report.total_entities,
            "errors": report.errors,
            "warnings": report.warnings,
            "info": report.info,
            "issues": [i.__dict__ for i in report.issues],
        }
    print(f"Validation complete: {report.total_entities} entities, {report.errors} errors, {report.warnings} warnings")
    for issue in report.issues:
        print(f"  [{issue.severity}] {issue.entity_type}:{issue.entity_id} - {issue.message}")
    return 1 if report.errors > 0 else 0


def handle_migrate(args) -> Any:
    api = KTSAPI()
    result = api.run_migration(args.version, force=args.force)
    if args.json:
        return result.__dict__
    print(f"Migration {args.version}: {'SUCCESS' if result.success else 'FAILED'} - {result.message}")
    return 0 if result.success else 1


def handle_migrate_status(args) -> Any:
    api = KTSAPI()
    status = api.migration_status()
    if args.json:
        return status
    print("Applied:", ", ".join(status["applied"]) or "none")
    print("Pending:", ", ".join(status["pending"]) or "none")
    return status


def handle_events(args) -> Any:
    api = KTSAPI()
    # Parse entity reference
    parts = args.entity.split(":", 1)
    entity = EntityRef(type=parts[0] if len(parts) > 1 else "ticket", id=parts[-1])
    events = api.get_events(entity, limit=args.limit)
    if args.json:
        return [e.model_dump(by_alias=True) for e in events]
    for e in events:
        print(f"  {e.timestamp}  {e.actor.id} ({e.actor.type})  {e.event_type}  {e.reason}")
    return None


def handle_ai(args) -> Any:
    api = KTSAPI()
    api.ensure_initialized()
    if args.ai_cmd == "classify":
        ai_svc = api.get_service("ai")
        result = ai_svc.classify_ticket(args.id)
        print(f"Classification: {result['categories']} (confidence: {result['confidence']:.2f})")
        return result

    elif args.ai_cmd == "similar":
        ai_svc = api.get_service("ai")
        results = ai_svc.find_similar_tickets(args.id)
        for r in results:
            print(f"  {r['id']} (score: {r['score']:.2f})")
        return results

    elif args.ai_cmd == "troubleshoot":
        ai_svc = api.get_service("ai")
        suggestion = ai_svc.suggest_troubleshooting(args.id)
        print(f"Suggestion {suggestion.suggestion_id} created (pending)")
        return suggestion.model_dump(by_alias=True)

    elif args.ai_cmd == "summary":
        ai_svc = api.get_service("ai")
        summary = ai_svc.generate_summary(args.id)
        print(summary)
        return {"summary": summary}

    return None


if __name__ == "__main__":
    sys.exit(main())