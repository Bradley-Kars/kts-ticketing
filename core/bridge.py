"""KTS bridge (H2b): FastAPI app with per-device token auth on EVERY request.

No local-trust bypass: source address is never consulted for auth — a
loopback request without a token gets 401 exactly like any other
(SPEC amendment 1: tailscale serve rewrites sources to 127.0.0.1).

Binding unchanged: run manually via uvicorn --host 127.0.0.1 --port 8799; this
module never binds/listens by itself.

Routes:
  GET  /health                    — liveness + sandbox flag (read)
  GET  /tickets                   — read-only ticket list (H2c slice 1)
  POST /tickets                   — T4: createTicket (ticket sequence only) as USR-000002
  POST /tickets/{ticket_id}/worklog — T1: append worklog entry as USR-000002
  POST /tickets/{ticket_id}/status  — T2: change ticket status as USR-000002
  PATCH /tickets/{ticket_id}        — T3: edit ticket fields as USR-000002
  POST /tasks                      — T5: createTask (task sequence only) as USR-000002
  POST /knowledge                  — T7: createKnowledge (KB sequence only) as USR-000002
  PATCH /knowledge/{knowledge_id}  — T7: updateKnowledge (field edits) as USR-000002

Scope (H2b/MC-2): every token carries scope="read" (default) or "write".
GET/HEAD/OPTIONS require "read"; POST/PUT/PATCH/DELETE require "write".
Read tokens are rejected with 403 on mutating routes.
"""
import re
import time
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .auth import TokenTable, HEADER, default_token_table
from . import paths
from .services.ticket_service import VersionMismatch


# Service actor for bridge-originated writes (MC-2 T1).
# Re-created inside each test setup; never hand-edited into the shared
# sandbox users.yaml (suite reset wipes it).
BRIDGE_ACTOR_ID = "USR-000002"
BRIDGE_ACTOR_NAME = "KTS Bridge"

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Ticket status values for T2. Must match Ticket.status Literal
# (core/models) and TicketService.change_status's valid_statuses list.
# Route-level check keeps bad payloads out of the service entirely, so
# the ticket file cannot change on a 422 (T2 proof 4).
VALID_STATUSES = (
    "new",
    "triaged",
    "in-progress",
    "waiting",
    "resolved",
    "closed",
    "cancelled",
)

# --- T3 (MC-2): updateTicket field whitelist -----------------------------
# Field edits only. Deliberately NOT editable here:
#   status      -> dedicated T2 route (validated transitions)
#   worklog     -> dedicated T1 route (billing entries)
#   id/type/version/ai/created_*/updated_*  -> identity + system fields
#   numbering fields                          -> T4+, out of T3 scope
# Route-level validation keeps bad payloads out of the service entirely,
# so the ticket file cannot change on a 422 (T3 proof 4).
EDITABLE_FIELDS = frozenset(
    {
        "title",
        "priority",
        "impact",
        "urgency",
        "category",
        "work_type",
        "subcategory",
        "assigned_to",
        "assigned_team",
        "next_action",
        "due",
        "follow_up",
        "tags",
        "resolution",
        "resolution_category",
        "sla_paused",
        "sla_pause_reason",
    }
)

# Named with a pointed error so the caller is steered to the right route.
FORBIDDEN_FIELDS = frozenset({"status", "worklog"})

# --- T4 (MC-2): createTicket field whitelist ------------------------------
# title is required; the rest are optional and take model defaults.
# id/type/version/status/worklog are system-owned and rejected (422):
#   id/type/version  -> allocated by next_id + create()
#   status           -> create always starts "new"
#   worklog          -> dedicated T1 route
CREATE_ALLOWED = frozenset(
    {
        "title",
        "priority",
        "impact",
        "urgency",
        "category",
        "work_type",
        "subcategory",
        "client_name",
        "organization_id",
        "contact_id",
        "client_id",
        "asset_id",
        "assigned_to",
        "assigned_team",
        "next_action",
        "due",
        "follow_up",
        "tags",
        "ticket_type",
        "source",
        "channel",
    }
)

_FIELD_CHOICES = {
    "priority": ("critical", "high", "medium", "low", "informational"),
    "impact": ("critical", "high", "medium", "low"),
    "urgency": ("critical", "high", "medium", "low"),
    "category": (
        "support", "setup", "maintenance", "project",
        "billing", "follow-up", "security", "backup",
    ),
    "work_type": (
        "virus", "network", "email", "hardware", "software", "audio",
        "security", "backup", "consult", "other",
    ),
    "resolution_category": (
        "fixed", "workaround", "no_issue_found",
        "customer_cancelled", "out_of_scope", "duplicate",
    ),
}

_USR_RE = re.compile(r"^USR-[A-Z0-9]{6,}$")

# --- T5 (MC-2): createTask field whitelist ---------------------------------
# title is required; the rest are optional and take model defaults.
# id/type/version/status are system-owned and rejected (422):
#   id/type/version  -> allocated by next_id + create()
#   status           -> create always starts "todo" (task default)
#   project_id/ticket_id allowed here (task may reference them);
#   assignee validated against the user table on create (400 if absent).
TASK_ALLOWED = frozenset(
    {
        "title",
        "description",
        "priority",
        "assignee",
        "project_id",
        "ticket_id",
        "due_date",
        "start_date",
        "estimated_hours",
        "tags",
    }
)

_TASK_FIELD_CHOICES = {
    "priority": ("critical", "high", "medium", "low", "informational"),
}


def _normalize_task_field(key: str, value):
    """Validate one task-create field; return the value to pass to the model.
    Raises ValueError with a field-named message on bad input."""
    if key == "title":
        if not isinstance(value, str) or not (1 <= len(value) <= 200):
            raise ValueError("title must be a non-empty string (max 200 chars)")
        return value
    if key == "description":
        if value is None:
            return value
        if not isinstance(value, str):
            raise ValueError("description must be a string or null")
        return value
    if key == "priority":
        if value not in _TASK_FIELD_CHOICES["priority"]:
            allowed = ", ".join(_TASK_FIELD_CHOICES["priority"])
            raise ValueError(f"priority must be one of: {allowed}")
        return value
    if key == "assignee":
        if value is None:
            return value
        if not isinstance(value, str) or not _USR_RE.match(value):
            raise ValueError("assignee must be a USR- id or null")
        return value
    if key in ("project_id", "ticket_id"):
        if value is None:
            return value
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a non-empty string or null")
        return value
    if key in ("due_date", "start_date"):
        if value is None:
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(
                    f"{key} must be an ISO-8601 datetime string or null"
                ) from exc
        raise ValueError(f"{key} must be an ISO-8601 datetime string or null")
    if key == "estimated_hours":
        if value is None:
            return 0
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("estimated_hours must be a number or null")
        if value < 0:
            raise ValueError("estimated_hours must be >= 0")
        return value
    if key == "tags":
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
            raise ValueError("tags must be a list of strings")
        return value
    raise ValueError(f"unknown field: {key}")


KNOWLEDGE_ALLOWED = frozenset(
    {
        "title",
        "content",
        "category",
        "subcategory",
        "author",
        "reviewer",
        "visibility",
        "tags",
        "related_tickets",
        "related_assets",
        "related_problems",
        "related_changes",
    }
)

_KNOWLEDGE_FIELD_CHOICES = {
    "category": (
        "networking", "security", "hardware", "software", "email",
        "backup", "procedures", "troubleshooting", "onboarding",
        "compliance", "general",
    ),
    "visibility": ("internal", "customer", "public"),
}

_KNOWLEDGE_CONTENT_MAX = 100_000
_KNOWLEDGE_RELATED_MAX = 100


def _normalize_knowledge_field(key: str, value):
    """Validate one knowledge-create field; return the value to pass to the model.
    Raises ValueError with a field-named message on bad input.
    status is system-owned (always draft on create) and not accepted here."""
    if key == "title":
        if not isinstance(value, str) or not (1 <= len(value) <= 200):
            raise ValueError("title must be a non-empty string (max 200 chars)")
        return value
    if key == "content":
        if not isinstance(value, str):
            raise ValueError("content must be a string")
        if value == "" or len(value) > _KNOWLEDGE_CONTENT_MAX:
            raise ValueError(
                f"content must be 1..{_KNOWLEDGE_CONTENT_MAX} chars"
            )
        return value
    if key == "category":
        if value not in _KNOWLEDGE_FIELD_CHOICES["category"]:
            allowed = ", ".join(_KNOWLEDGE_FIELD_CHOICES["category"])
            raise ValueError(f"category must be one of: {allowed}")
        return value
    if key == "subcategory":
        if value is None:
            return value
        if not isinstance(value, str):
            raise ValueError("subcategory must be a string or null")
        return value
    if key in ("author", "reviewer"):
        if not isinstance(value, str) or not _USR_RE.match(value):
            raise ValueError(f"{key} must be a USR- id")
        return value
    if key == "visibility":
        if value not in _KNOWLEDGE_FIELD_CHOICES["visibility"]:
            allowed = ", ".join(_KNOWLEDGE_FIELD_CHOICES["visibility"])
            raise ValueError(f"visibility must be one of: {allowed}")
        return value
    if key == "tags":
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
            raise ValueError("tags must be a list of strings")
        return value
    if key in (
        "related_tickets",
        "related_assets",
        "related_problems",
        "related_changes",
    ):
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
            raise ValueError(f"{key} must be a list of strings")
        if len(value) > _KNOWLEDGE_RELATED_MAX:
            raise ValueError(
                f"{key} must have at most {_KNOWLEDGE_RELATED_MAX} entries"
            )
        return value
    raise ValueError(f"unknown field: {key}")


def _parse_if_version(body: dict):
    """Optional optimistic-concurrency precondition.

    Returns (if_version, error_response). error_response is a 422 JSONResponse
    when if_version is present but not a non-negative int; else (int, None).
    Absent if_version -> (None, None): behaves as before.
    """
    if "if_version" not in body:
        return None, None
    v = body["if_version"]
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        return None, JSONResponse(
            {"detail": "if_version must be a non-negative integer"},
            status_code=422,
        )
    return v, None


def _normalize_field(key: str, value):
    """Validate one editable field; return the value to setattr.
    Raises ValueError with a field-named message on bad input."""
    if key == "title":
        if not isinstance(value, str) or not (1 <= len(value) <= 200):
            raise ValueError("title must be a non-empty string (max 200 chars)")
        return value
    if key in _FIELD_CHOICES:
        allowed = _FIELD_CHOICES[key]
        if value not in allowed:
            raise ValueError(f"{key} must be one of: {', '.join(allowed)}")
        return value
    if key == "assigned_to":
        if value is None:
            return value
        if not isinstance(value, str) or not _USR_RE.match(value):
            raise ValueError("assigned_to must be a USR- id or null")
        return value
    if key == "tags":
        if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
            raise ValueError("tags must be a list of strings")
        return value
    if key in ("due", "follow_up"):
        if value is None:
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"{key} must be an ISO-8601 datetime string or null") from exc
        raise ValueError(f"{key} must be an ISO-8601 datetime string or null")
    if key == "sla_paused":
        if not isinstance(value, bool):
            raise ValueError("sla_paused must be a boolean")
        return value
    if key == "next_action":
        if value is not None and (not isinstance(value, str) or len(value) > 200):
            raise ValueError("next_action must be a string (max 200) or null")
        return value
    # remaining keys: optional free strings
    if key in ("subcategory", "assigned_team", "resolution", "sla_pause_reason"):
        if value is not None and (not isinstance(value, str)):
            raise ValueError(f"{key} must be a string or null")
        return value
    # --- create-only free/ID fields (T4; also harmless if update ever reuses them)
    if key == "client_name":
        if value is not None and (not isinstance(value, str) or len(value) > 200):
            raise ValueError("client_name must be a string (max 200) or null")
        return value
    _ID_PATTERNS = {
        "organization_id": r"^ORG-\d{6,}$",
        "contact_id": r"^CON-\d{6,}$",
        "client_id": r"^CUS-\d{6,}$",
        "asset_id": r"^AST-\d{6,}$",
    }
    if key in _ID_PATTERNS:
        if value is None:
            return value
        if not isinstance(value, str) or not re.match(_ID_PATTERNS[key], value):
            raise ValueError(
                f"{key} must match {_ID_PATTERNS[key]} or be null"
            )
        return value
    if key in ("ticket_type", "source", "channel"):
        if value is None:
            return value
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string or null")
        return value
    raise ValueError(f"unknown field: {key}")


def create_app(table: Optional[TokenTable] = None) -> FastAPI:
    """Create the FastAPI app. If table is None, uses the default
    location under the current home (sandbox-aware via paths.py)."""
    table = table or default_token_table()

    app = FastAPI(title="kts-bridge", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        """Middleware that validates X-API-Token on EVERY request.
        No IP-based bypass anywhere.
        Enforces token scope: write methods need scope="write"."""
        start = time.perf_counter()
        presented = request.headers.get(HEADER, "")
        rec = table.verify(presented) if presented else None
        client_host = request.client.host if request.client else "unknown"

        if rec is None:
            # auth failure audit
            table.audit_request(
                method=request.method,
                path=request.url.path,
                status=401,
                latency_ms=int((time.perf_counter() - start) * 1000),
                source=client_host,
                reason="missing or invalid token",
            )
            return JSONResponse(
                {"detail": "missing or invalid token"}, status_code=401
            )

        token_scope = rec.get("scope", "read")
        if request.method in _WRITE_METHODS and token_scope != "write":
            table.audit_request(
                method=request.method,
                path=request.url.path,
                status=403,
                latency_ms=int((time.perf_counter() - start) * 1000),
                source=client_host,
                reason=f"scope={token_scope} insufficient for {request.method}",
            )
            return JSONResponse(
                {"detail": f"token scope {token_scope!r} cannot {request.method}"},
                status_code=403,
            )

        # attach for downstream
        request.state.token_id = rec["token_id"]
        request.state.label = rec["label"]
        request.state.scope = token_scope

        response = await call_next(request)

        # success audit (SPEC: bridge log every remote call)
        table.audit_request(
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            latency_ms=int((time.perf_counter() - start) * 1000),
            token_id=rec["token_id"],
            label=rec["label"],
        )
        return response

    @app.get("/health")
    def health():
        return {"ok": True, "sandbox": paths.is_sandbox_mode()}

    @app.get("/tickets")
    def tickets(all: bool = False):
        """Read-only ticket list (H2c slice 1). GET only, no mutation.

        Mirrors the H1 spike route: TicketService.list, open statuses by
        default, `all=true` for everything. Auth comes from the middleware
        above - no token, no answer (401).
        """
        from .api import get_api  # lazy: keep module import side-effect free

        OPEN_STATUSES = ("new", "triaged", "in-progress", "waiting")
        api = get_api()
        svc = api.get_service("ticket")
        rows = svc.list(filters={}, limit=500)
        if not all:
            rows = [t for t in rows if t.status in OPEN_STATUSES]
        return {
            "count": len(rows),
            "tickets": [
                {
                    "id": t.id,
                    "status": t.status,
                    "priority": t.priority,
                    "client_name": getattr(t, "client_name", ""),
                    "title": t.title,
                }
                for t in rows
            ],
        }

    # --- T4 (MC-2): createTicket — ticket-sequence write path -------------
    # Only the ticket-side numbering path is in MC-2. CREATE_ALLOWED is
    # module-level (next to EDITABLE_FIELDS). Fields are a whitelist:
    # title required; everything else optional with model defaults. No
    # status (create always starts "new"), no worklog, no id/type/version
    # (system). No if_version precondition: create allocates a brand-new id,
    # so there is no prior version to check against; a bool/non-int
    # if_version still 422s for consistency with the update routes.
    @app.post("/tickets")
    def create_ticket(body: dict):
        """T4 (MC-2): createTicket via the real write path (ticket sequence only).

        Allocates the next TKT id (TicketService.next_id), writes the vault
        file at version 1, emits ticket.created. Acts as BRIDGE_ACTOR
        (USR-000002, KTS Bridge). Requires scope="write" (middleware).
        Body is a whitelist of optional fields plus required "title";
        id/type/version are system-owned and rejected. if_version is
        accepted for route consistency but has no precondition target on
        create (new id -> always version 1). Sandbox-aware via paths.py.
        """
        from .api import get_api
        from .models import EventActor

        if not isinstance(body, dict) or not body:
            return JSONResponse(
                {"detail": "body must be a non-empty object with at least 'title'"},
                status_code=422,
            )

        if_version, if_err = _parse_if_version(body if isinstance(body, dict) else {})
        if if_err is not None:
            return if_err
        # Valid if_version on create is ignored: new ticket is always
        # version 1. (Kept so the field is type-checked like update routes.)

        title = body.get("title")
        if not isinstance(title, str) or not title.strip():
            return JSONResponse({"detail": "title is required"}, status_code=422)
        if len(title) > 200:
            return JSONResponse(
                {"detail": "title too long (max 200)"}, status_code=422
            )

        data = {"title": title}
        for key, value in body.items():
            if key in ("if_version", "title"):
                continue
            if key in ("id", "type", "version", "status", "worklog"):
                return JSONResponse(
                    {
                        "detail": (
                            f"field {key!r} is system-owned and not accepted "
                            "on create; use the update/status/worklog routes"
                        )
                    },
                    status_code=422,
                )
            if key not in CREATE_ALLOWED:
                return JSONResponse(
                    {"detail": f"unknown field: {key!r}"}, status_code=422
                )
            try:
                data[key] = _normalize_field(key, value)
            except ValueError as exc:
                return JSONResponse(
                    {"detail": f"invalid {key}: {exc}"}, status_code=422
                )

        from .api import get_api as _get_api_now
        _api = _get_api_now()
        if data.get("assigned_to") is not None and not _api.user_exists(data["assigned_to"]):
            return JSONResponse(
                {
                    "detail": (
                        f"assigned_to user {data['assigned_to']} does not exist"
                    )
                },
                status_code=400,
            )

        actor = EventActor(id=BRIDGE_ACTOR_ID, type="human", name=BRIDGE_ACTOR_NAME)
        try:
            ticket = _api.create_ticket(data, actor)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=422)
        except PermissionError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=403)

        return {
            "ticket_id": ticket.id,
            "title": ticket.title,
            "status": ticket.status,
            "version": ticket.version,
            "created_by": ticket.created_by,
            "actor": BRIDGE_ACTOR_ID,
        }

    @app.post("/tickets/{ticket_id}/worklog")
    def add_worklog(ticket_id: str, body: dict):
        """T1 (MC-2): append a worklog entry via the real write path.

        Acts as BRIDGE_ACTOR (USR-000002, KTS Bridge). Requires
        scope="write" (enforced by middleware). Sandbox-aware through
        paths.py / get_api — never touches the real vault when
        KTS_SANDBOX=1 + KTS_VAULT + KTS_HOME are set.
        """
        from .api import get_api
        from .models import EventActor, WorklogEntry

        if_version, if_err = _parse_if_version(body if isinstance(body, dict) else {})
        if if_err is not None:
            return if_err
        body_no_if = {k: v for k, v in (body or {}).items() if k != "if_version"} if isinstance(body, dict) else body

        try:
            entry = WorklogEntry.model_validate(body_no_if)
        except Exception as exc:
            return JSONResponse(
                {"detail": f"invalid worklog entry: {exc}"}, status_code=422
            )

        actor = EventActor(id=BRIDGE_ACTOR_ID, type="human", name=BRIDGE_ACTOR_NAME)
        api = get_api()
        try:
            ticket = api.add_worklog(ticket_id, entry, actor, if_version=if_version)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404)
        except PermissionError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=403)
        except VersionMismatch as exc:
            return JSONResponse(
                {
                    "detail": str(exc),
                    "entity_id": exc.entity_id,
                    "expected_version": exc.expected,
                    "actual_version": exc.actual,
                },
                status_code=409,
            )

        return {
            "ticket_id": ticket.id,
            "version": ticket.version,
            "worklog_count": len(ticket.worklog),
            "last_entry": entry.model_dump(mode="json"),
            "actor": BRIDGE_ACTOR_ID,
        }

    @app.post("/tickets/{ticket_id}/status")
    def change_ticket_status(ticket_id: str, body: dict):
        """T2 (MC-2): change ticket status via the real write path.

        Status field only (plus optional reason for the event trail).
        Acts as BRIDGE_ACTOR (USR-000002, KTS Bridge). Requires
        scope="write" (enforced by middleware). Body/status is validated
        HERE before any service call: an invalid payload returns 422
        without touching the ticket file (proof 4 by construction).
        Sandbox-aware through paths.py / get_api — never touches the
        real vault when KTS_SANDBOX=1 + KTS_VAULT + KTS_HOME are set.
        """
        from .api import get_api
        from .models import EventActor

        status = body.get("status") if isinstance(body, dict) else None
        if not isinstance(status, str) or status not in VALID_STATUSES:
            return JSONResponse(
                {
                    "detail": (
                        f"invalid status: {status!r} "
                        f"(allowed: {', '.join(VALID_STATUSES)})"
                    )
                },
                status_code=422,
            )
        reason = body.get("reason")
        if reason is not None and not isinstance(reason, str):
            return JSONResponse(
                {"detail": "reason must be a string"}, status_code=422
            )

        if_version, if_err = _parse_if_version(body)
        if if_err is not None:
            return if_err

        actor = EventActor(id=BRIDGE_ACTOR_ID, type="human", name=BRIDGE_ACTOR_NAME)
        api = get_api()
        try:
            ticket = api.change_ticket_status(
                ticket_id, status, actor, reason=reason, if_version=if_version
            )
        except ValueError as exc:
            code = 404 if "not found" in str(exc).lower() else 422
            return JSONResponse({"detail": str(exc)}, status_code=code)
        except PermissionError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=403)
        except VersionMismatch as exc:
            return JSONResponse(
                {
                    "detail": str(exc),
                    "entity_id": exc.entity_id,
                    "expected_version": exc.expected,
                    "actual_version": exc.actual,
                },
                status_code=409,
            )

        return {
            "ticket_id": ticket.id,
            "status": ticket.status,
            "version": ticket.version,
            "updated_by": ticket.updated_by,
            "actor": BRIDGE_ACTOR_ID,
        }

    @app.patch("/tickets/{ticket_id}")
    def update_ticket_fields(ticket_id: str, body: dict):
        """T3 (MC-2): edit ticket fields via the real write path (update_ticket).

        Field edits only — no status (T2 route), no worklog (T1 route),
        no numbering (T4+, out of T3 scope). Acts as BRIDGE_ACTOR
        (USR-000002, KTS Bridge). Requires scope="write" (middleware).
        Every key/value is validated HERE before any service call: unknown
        keys, forbidden keys, or bad values return 422 without touching the
        ticket file (proof 4 by construction). Sandbox-aware via paths.py
        / get_api — never touches the real vault when KTS_SANDBOX=1 +
        KTS_VAULT + KTS_HOME are set.
        """
        from .api import get_api
        from .models import EventActor

        if not isinstance(body, dict) or not body:
            return JSONResponse(
                {"detail": "body must be a non-empty object of editable fields"},
                status_code=422,
            )

        reason = body.get("reason")
        if reason is not None and not isinstance(reason, str):
            return JSONResponse({"detail": "reason must be a string"}, status_code=422)

        if_version, if_err = _parse_if_version(body)
        if if_err is not None:
            return if_err

        field_keys = [k for k in body if k not in ("reason", "if_version")]
        if not field_keys:
            return JSONResponse(
                {"detail": "no editable fields provided"}, status_code=422
            )

        changes = {}
        for key in field_keys:
            if key in FORBIDDEN_FIELDS:
                route = "/status" if key == "status" else "/worklog"
                return JSONResponse(
                    {
                        "detail": (
                            f"field {key!r} is not editable here; "
                            f"use the {route} route"
                        )
                    },
                    status_code=422,
                )
            if key not in EDITABLE_FIELDS:
                return JSONResponse(
                    {"detail": f"unknown field: {key!r}"}, status_code=422
                )
            try:
                changes[key] = _normalize_field(key, body[key])
            except ValueError as exc:
                return JSONResponse(
                    {"detail": f"invalid {key}: {exc}"}, status_code=422
                )

        from .api import get_api as _get_api_now
        _api = _get_api_now()
        if changes.get("assigned_to") is not None and not _api.user_exists(changes["assigned_to"]):
            return JSONResponse(
                {
                    "detail": (
                        f"assigned_to user {changes['assigned_to']} does not exist"
                    )
                },
                status_code=400,
            )

        actor = EventActor(id=BRIDGE_ACTOR_ID, type="human", name=BRIDGE_ACTOR_NAME)
        api = _api
        try:
            ticket = api.update_ticket(
                ticket_id, changes, actor, reason=reason, if_version=if_version
            )
        except ValueError as exc:
            code = 404 if "not found" in str(exc).lower() else 422
            return JSONResponse({"detail": str(exc)}, status_code=code)
        except PermissionError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=403)
        except VersionMismatch as exc:
            return JSONResponse(
                {
                    "detail": str(exc),
                    "entity_id": exc.entity_id,
                    "expected_version": exc.expected,
                    "actual_version": exc.actual,
                },
                status_code=409,
            )

        return {
            "ticket_id": ticket.id,
            "updated_fields": sorted(changes),
            "version": ticket.version,
            "updated_by": ticket.updated_by,
            "actor": BRIDGE_ACTOR_ID,
        }

    @app.post("/tasks")
    def create_task(body: dict):
        """T5 (MC-2): create a task via the real write path (TaskService.next_id + create).

        title is required; optional fields are whitelisted. id/type/version/status
        are system-owned and rejected (422) so the task file cannot change on a bad
        payload. if_version is type-checked (422 on bad type) but ignored on create
        (new task is always version 1). Acts as BRIDGE_ACTOR (USR-000002). Requires
        scope="write" (middleware). Sandbox-aware via paths.py / get_api.
        """
        from .api import get_api
        from .models import EventActor

        if not isinstance(body, dict) or not body:
            return JSONResponse(
                {"detail": "body must be a non-empty object with at least 'title'"},
                status_code=422,
            )

        if_version, if_err = _parse_if_version(body if isinstance(body, dict) else {})
        if if_err is not None:
            return if_err
        # Valid if_version on create is ignored: new task is always version 1.

        title = body.get("title")
        if not isinstance(title, str) or not title.strip():
            return JSONResponse({"detail": "title is required"}, status_code=422)
        if len(title) > 200:
            return JSONResponse(
                {"detail": "title too long (max 200)"}, status_code=422
            )

        data = {"title": title}
        for key, value in body.items():
            if key in ("if_version", "title"):
                continue
            if key in ("id", "type", "version", "status"):
                return JSONResponse(
                    {
                        "detail": (
                            f"field {key!r} is system-owned and not accepted "
                            "on create; use the update routes"
                        )
                    },
                    status_code=422,
                )
            if key not in TASK_ALLOWED:
                return JSONResponse(
                    {"detail": f"unknown field: {key!r}"}, status_code=422
                )
            try:
                data[key] = _normalize_task_field(key, value)
            except ValueError as exc:
                return JSONResponse(
                    {"detail": f"invalid {key}: {exc}"}, status_code=422
                )

        from .api import get_api as _get_api_now
        _api = _get_api_now()
        if data.get("assignee") is not None and not _api.user_exists(data["assignee"]):
            return JSONResponse(
                {
                    "detail": (
                        f"assignee user {data['assignee']} does not exist"
                    )
                },
                status_code=400,
            )

        actor = EventActor(id=BRIDGE_ACTOR_ID, type="human", name=BRIDGE_ACTOR_NAME)
        try:
            task = _api.create_task(data, actor)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=422)
        except PermissionError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=403)

        return {
            "task_id": task.id,
            "title": task.title,
            "status": task.status,
            "version": task.version,
            "created_by": task.created_by,
            "actor": BRIDGE_ACTOR_ID,
        }

    @app.post("/knowledge")
    def create_knowledge(body: dict):
        """T7 (MC-2): create a knowledge article via the real write path.

        Allocates the KB sequence (KnowledgeService.next_id) and writes via the
        real create path. title, content, category, author are required; the
        rest are optional. id/type/version/status are system-owned and rejected
        (422). status is always "draft" on create (published_at/review_date are
        set only by the desktop publish flow, not the bridge). if_version is
        type-checked (422 on bad type) but ignored on create (always version 1).
        Acts as BRIDGE_ACTOR (USR-000002). Requires scope="write" (middleware).
        """
        from .api import get_api
        from .models import EventActor

        if not isinstance(body, dict) or not body:
            return JSONResponse(
                {
                    "detail": (
                        "body must be a non-empty object with title, "
                        "content, category and author"
                    )
                },
                status_code=422,
            )

        if_version, if_err = _parse_if_version(body if isinstance(body, dict) else {})
        if if_err is not None:
            return if_err
        # Valid if_version on create is ignored: new article is always version 1.

        required = ("title", "content", "category", "author")
        missing = [k for k in required if k not in body]
        if missing:
            return JSONResponse(
                {"detail": f"missing required fields: {', '.join(missing)}"},
                status_code=422,
            )

        data = {}
        for key, value in body.items():
            if key == "if_version":
                continue
            if key in ("id", "type", "version", "status", "ai"):
                return JSONResponse(
                    {
                        "detail": (
                            f"field {key!r} is system-owned and not accepted "
                            "on create; use the update routes"
                        )
                    },
                    status_code=422,
                )
            if key not in KNOWLEDGE_ALLOWED:
                return JSONResponse(
                    {"detail": f"unknown field: {key!r}"}, status_code=422
                )
            try:
                data[key] = _normalize_knowledge_field(key, value)
            except ValueError as exc:
                return JSONResponse(
                    {"detail": f"invalid {key}: {exc}"}, status_code=422
                )

        # status is system-owned: the bridge always creates a draft.
        data["status"] = "draft"
        # author is required and USR-checked by the normalizer; default it to
        # the bridge actor when omitted, but a mismatched author is rejected by
        # _check_author below so the route cannot create records for other users.
        from .api import get_api as _get_api_now
        _api = _get_api_now()
        if data.get("author") != BRIDGE_ACTOR_ID:
            return JSONResponse(
                {
                    "detail": (
                        "author must equal the bridge actor "
                        f"{BRIDGE_ACTOR_ID} on create"
                    )
                },
                status_code=403,
            )

        actor = EventActor(id=BRIDGE_ACTOR_ID, type="human", name=BRIDGE_ACTOR_NAME)
        try:
            knowledge = _api.create_knowledge(data, actor)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=422)
        except PermissionError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=403)

        return {
            "knowledge_id": knowledge.id,
            "title": knowledge.title,
            "status": knowledge.status,
            "category": knowledge.category,
            "author": knowledge.author,
            "version": knowledge.version,
            "created_by": knowledge.created_by,
            "actor": BRIDGE_ACTOR_ID,
        }

    @app.patch("/knowledge/{knowledge_id}")
    def update_knowledge_fields(knowledge_id: str, body: dict):
        """T7 (MC-2): edit knowledge fields via the real write path.

        Field edits only — no status (system-owned, publish flow), no id/type/
        version/ai (system-owned). Acts as BRIDGE_ACTOR (USR-000002, KTS
        Bridge). Requires scope="write" (middleware). Optimistic concurrency
        via if_version (stale -> 409 audited). Unknown/system fields and bad
        values return 422 without touching the file. Author must equal the
        bridge actor. Sandbox-aware via paths.py / get_api — never touches
        the real vault when KTS_SANDBOX=1 + KTS_VAULT + KTS_HOME are set.
        """
        from .api import get_api
        from .models import EventActor

        if not isinstance(body, dict) or not body:
            return JSONResponse(
                {"detail": "body must be a non-empty object of editable fields"},
                status_code=422,
            )

        reason = body.get("reason")
        if reason is not None and not isinstance(reason, str):
            return JSONResponse({"detail": "reason must be a string"}, status_code=422)

        if_version, if_err = _parse_if_version(body)
        if if_err is not None:
            return if_err

        field_keys = [k for k in body if k not in ("reason", "if_version")]
        if not field_keys:
            return JSONResponse(
                {"detail": "no editable fields provided"}, status_code=422
            )

        changes = {}
        for key in field_keys:
            if key in ("id", "type", "version", "status", "ai"):
                return JSONResponse(
                    {
                        "detail": (
                            f"field {key!r} is system-owned and not accepted "
                            "on update; use the publish flow or a dedicated route"
                        )
                    },
                    status_code=422,
                )
            if key not in KNOWLEDGE_ALLOWED:
                return JSONResponse(
                    {"detail": f"unknown field: {key!r}"}, status_code=422
                )
            try:
                changes[key] = _normalize_knowledge_field(key, body[key])
            except ValueError as exc:
                return JSONResponse(
                    {"detail": f"invalid {key}: {exc}"}, status_code=422
                )

        from .api import get_api as _get_api_now
        _api = _get_api_now()
        if changes.get("author") is not None and changes["author"] != BRIDGE_ACTOR_ID:
            return JSONResponse(
                {
                    "detail": (
                        "author must equal the bridge actor "
                        f"{BRIDGE_ACTOR_ID} on update"
                    )
                },
                status_code=403,
            )

        actor = EventActor(id=BRIDGE_ACTOR_ID, type="human", name=BRIDGE_ACTOR_NAME)
        try:
            knowledge = _api.update_knowledge(
                knowledge_id, changes, actor, reason=reason, if_version=if_version
            )
        except ValueError as exc:
            code = 404 if "not found" in str(exc).lower() else 422
            return JSONResponse({"detail": str(exc)}, status_code=code)
        except PermissionError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=403)
        except VersionMismatch as exc:
            return JSONResponse(
                {
                    "detail": str(exc),
                    "entity_id": exc.entity_id,
                    "expected_version": exc.expected,
                    "actual_version": exc.actual,
                },
                status_code=409,
            )

        return {
            "knowledge_id": knowledge.id,
            "updated_fields": sorted(changes),
            "version": knowledge.version,
            "updated_by": knowledge.updated_by,
            "actor": BRIDGE_ACTOR_ID,
        }

    return app


# Module-level app for uvicorn core.bridge:app
app = create_app()