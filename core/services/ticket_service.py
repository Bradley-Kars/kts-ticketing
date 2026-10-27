"""Base Service — Common CRUD operations for all entities"""

import logging
import os
import re
import sys
import yaml
import shutil
import datetime as dt
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Type, TypeVar, Generic
from contextlib import contextmanager

from ..models import (
    BaseRecord, AnyEntity, EntityType, EntityRef, MODEL_MAP, get_model_for_type,
    get_id_prefix, validate_id, extract_id_prefix, Event, EventActor, EventEntity,
    EventChanges, Ticket, Client, Contact, Organization, Asset, User,
    Project, Task, Knowledge, Problem, Change, Request, Contract,
    WorklogEntry, Relationship
)
from ..events import EventEmitter
from ..permissions import PermissionChecker
from ..safe_io import safe_mkdir, safe_write, safe_move


from ..paths import (
    vault_root, home_root, enforce_isolation,
    tickets_dir, clients_dir, contacts_dir, organizations_dir,
    assets_dir, users_dir, projects_dir, tasks_dir,
    knowledge_dir, problems_dir, changes_dir, requests_dir,
    contracts_dir, real_vault_root, real_home_root,
)

logger = logging.getLogger(__name__)

T = TypeVar('T', bound=BaseRecord)


class VersionMismatch(Exception):
    """Optimistic concurrency precondition failed (if_version)."""
    def __init__(self, entity_id: str, expected: int, actual: int):
        self.entity_id = entity_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Version mismatch for {entity_id}: expected {expected}, actual {actual}"
        )

ENTITY_DIRS = {
    "ticket": tickets_dir(),
    "client": clients_dir(),
    "contact": contacts_dir(),
    "organization": organizations_dir(),
    "asset": assets_dir(),
    "user": users_dir(),
    "project": projects_dir(),
    "task": tasks_dir(),
    "knowledge": knowledge_dir(),
    "problem": problems_dir(),
    "change": changes_dir(),
    "request": requests_dir(),
    "contract": contracts_dir(),
}

CLIENT_SUBDIRS = {
    "business": "Business",
    "residential": "Residential",
}


class BaseService(Generic[T]):
    """Base service with common CRUD operations"""

    def __init__(
        self,
        entity_type: EntityType,
        event_emitter: Optional[EventEmitter] = None,
        permission_checker: Optional[PermissionChecker] = None,
    ):
        self.entity_type = entity_type
        self.model_class = MODEL_MAP[entity_type]
        self.id_prefix = get_id_prefix(entity_type)
        self.entity_dir = ENTITY_DIRS[entity_type]
        self.event_emitter = event_emitter
        self.permission_checker = permission_checker

    def _get_dir(self, **kwargs) -> Path:
        """Get directory for entity (handles client subdirs)"""
        if self.entity_type == "client":
            client_class = kwargs.get("class_", "residential")
            subdir = CLIENT_SUBDIRS.get(client_class, "Residential")
            return self.entity_dir / subdir
        return self.entity_dir

    def _get_file_path(self, entity_id: str, **kwargs) -> Path:
        """Get file path for an entity ID"""
        if self.entity_type == "ticket":
            # Tickets have special naming: TKT-YYYY-### - Client - Title.md
            # _find_file() globs "{entity_id} - *.md", so clientless tickets
            # MUST still use the " - Title" shape or they land as {id}.md
            # and become unfetchable (404 on subsequent get/update).
            files = list(self.entity_dir.glob(f"{entity_id} - *.md"))
            if files:
                return files[0]
            # For new tickets, we need to generate the proper filename
            # This is a fallback - the caller should provide the full filename via kwargs
            title = kwargs.get("title", "")
            client = kwargs.get("client", "") or ""
            # Extract client name from wikilink format [[Client Name]]
            if client.startswith("[[") and client.endswith("]]"):
                client = client[2:-2]
            parts = [entity_id]
            if client:
                parts.append(re.sub(r'[<>:"/\\|?*]', '-', client))
            if title:
                parts.append(re.sub(r'[<>:"/\\|?*]', '-', title))
            return self.entity_dir / (" - ".join(parts) + ".md")
        elif self.entity_type == "client":
            # Clients: {Name}.md in Business/ or Residential/
            class_ = kwargs.get("class_", "residential")
            subdir = CLIENT_SUBDIRS.get(class_, "Residential")
            # Need to find by name - we'd need the title
            return self.entity_dir / subdir / f"{entity_id}.md"
        elif self.entity_type == "organization":
            # Organizations: {Name}.md in Organizations/
            # Use title from kwargs for proper naming
            title = kwargs.get("title", "")
            if title:
                safe_title = re.sub(r'[<>:"/\\|?*]', '-', title)
                return self.entity_dir / f"{entity_id} - {safe_title}.md"
            return self.entity_dir / f"{entity_id}.md"
        else:
            return self.entity_dir / f"{entity_id}.md"

    def _read_frontmatter(self, file_path: Path) -> Dict[str, Any]:
        """Read YAML frontmatter from a Markdown file"""
        if not file_path.exists():
            return {}
        content = file_path.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return {}
        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}
        try:
            return yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            return {}

    def _write_frontmatter(self, file_path: Path, data: Dict[str, Any], body: str = "") -> None:
        """Write entity to Markdown file with YAML frontmatter"""
        safe_mkdir(file_path.parent)
        fm = yaml.dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
        content = f"---\n{fm}---\n\n{body}"
        safe_write(file_path, content, encoding="utf-8")

    def _serialize_for_yaml(self, model: BaseRecord) -> Dict[str, Any]:
        """Convert Pydantic model to dict suitable for YAML frontmatter"""
        data = model.model_dump(
            exclude_none=True,
            exclude={"ai"},  # ai in .ticketing/ai/
            by_alias=True,
        )
        # Convert datetime to ISO string
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
            elif isinstance(value, list):
                data[key] = [
                    v.isoformat() if isinstance(v, datetime) else v
                    for v in value
                ]
        return data

    def _emit_event(
        self,
        event_type: str,
        entity: EntityRef,
        changes: Dict[str, EventChanges],
        actor: EventActor,
        reason: Optional[str] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
    ) -> None:
        """Emit an event if event emitter is configured"""
        if self.event_emitter:
            self.event_emitter.emit(
                event_type=event_type,
                entity=entity,
                changes=changes,
                actor=actor,
                reason=reason,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )

    def _check_permission(self, actor: EventActor, permission: str) -> bool:
        """Check if actor has permission"""
        if self.permission_checker:
            return self.permission_checker.check(actor, permission)
        return True  # Default allow if no checker

    # --- CRUD Operations ---

    def create(self, model: T, actor: EventActor) -> T:
        """Create a new entity"""
        # Check permissions
        if not self._check_permission(actor, f"{self.entity_type}.create"):
            raise PermissionError(f"Actor {actor.id} lacks {self.entity_type}.create permission")

        # Validate ID
        if not validate_id(self.entity_type, model.id):
            raise ValueError(f"Invalid ID format for {self.entity_type}: {model.id}")

        # Check if already exists
        if self.exists(model.id):
            raise ValueError(f"Entity {model.id} already exists")

        # Set timestamps
        now = datetime.now()
        model.created_at = now
        model.updated_at = now
        model.created_by = actor.id
        model.updated_by = actor.id
        model.version = 1

        # Write to vault
        # Generate proper filename using the entity-specific method
        file_path = self._get_file_path(model.id, title=model.title, client=getattr(model, "client_name", ""))
        body = self._get_body_for_model(model)
        self._write_frontmatter(file_path, self._serialize_for_yaml(model), body)

        # Emit event
        self._emit_event(
            event_type=f"{self.entity_type}.created",
            entity=EntityRef(type=self.entity_type, id=model.id),
            changes={},
            actor=actor,
            reason=f"{self.entity_type} created",
        )

        return model

    def get(self, entity_id: str, **kwargs) -> Optional[T]:
        """Get entity by ID"""
        file_path = self._find_file(entity_id, **kwargs)
        if not file_path:
            return None
        fm = self._read_frontmatter(file_path)
        if not fm:
            return None
        try:
            return self.model_class(**fm)
        except Exception as e:
            raise ValueError(f"Failed to parse {entity_id}: {e}")

    @staticmethod
    def _version_mismatch(
        entity_id: str, if_version: int, current_version: int
    ) -> "VersionMismatch":
        return VersionMismatch(
            entity_id=entity_id,
            expected=if_version,
            actual=current_version,
        )

    def update(
        self,
        entity_id: str,
        changes: Dict[str, Any],
        actor: EventActor,
        reason: Optional[str] = None,
        if_version: Optional[int] = None,
    ) -> T:
        """Update an entity

        Field-level permission checks:
        - Changes to assigned_to or assigned_team (ticket entity only)
          require f"{entity_type}.assign".
        - Every other changed field requires f"{entity_type}.update".
        - Empty changes still require f"{entity_type}.update".
        - All required permissions are checked before any read or write.
        """
        # Compute required permissions from the keys in the request,
        # not from a value diff. Setting a field to its current value
        # still requires the permission.
        assignment_keys = {"assigned_to", "assigned_team"} & set(changes.keys())
        if self.entity_type == "ticket":
            required: set[str] = set()
            if assignment_keys:
                required.add("ticket.assign")
            if "status" in changes:
                required.add("ticket.change_status")
            # Empty changes or any non-assignment/non-status field
            # still requires ticket.update
            non_assignment_keys = set(changes.keys()) - {"assigned_to", "assigned_team", "status"}
            if non_assignment_keys or not changes:
                required.add("ticket.update")
        else:
            required: set[str] = {f"{self.entity_type}.update"}

        # Check ALL required permissions BEFORE any read or write.
        for perm in required:
            if not self._check_permission(actor, perm):
                logger.warning(
                    "Permission denied: actor=%s missing=%s entity=%s entity_type=%s",
                    actor.id, perm, entity_id, self.entity_type,
                )
                raise PermissionError(
                    f"Actor {actor.id} lacks {perm} permission "
                    f"(required for changing entity {entity_id})"
                )

        # Read happens only after permission is granted — no info leak.
        current = self.get(entity_id)
        if not current:
            raise ValueError(f"Entity {entity_id} not found")
        if if_version is not None and current.version != if_version:
            raise self._version_mismatch(entity_id, if_version, current.version)

        # Build changes dict for event
        event_changes = {}
        for key, new_value in changes.items():
            old_value = getattr(current, key, None)
            if old_value != new_value:
                event_changes[key] = EventChanges(
                    old=str(old_value) if old_value is not None else None,
                    new=str(new_value) if new_value is not None else None,
                )
                setattr(current, key, new_value)

        if not event_changes:
            return current  # No changes

        # Update timestamps and version
        current.updated_at = datetime.now()
        current.updated_by = actor.id
        current.version += 1

        # Write back
        file_path = self._find_file(entity_id)
        if not file_path:
            raise ValueError(f"File for {entity_id} not found")
        body = self._get_body_for_model(current)
        self._write_frontmatter(file_path, self._serialize_for_yaml(current), body)

        # Emit event
        self._emit_event(
            event_type=f"{self.entity_type}.updated",
            entity=EntityRef(type=self.entity_type, id=entity_id),
            changes=event_changes,
            actor=actor,
            reason=reason,
        )

        return current

    def delete(self, entity_id: str, actor: EventActor, reason: Optional[str] = None) -> bool:
        """Delete (archive) an entity — moves to _Archive"""
        if not self._check_permission(actor, f"{self.entity_type}.delete"):
            raise PermissionError(f"Actor {actor.id} lacks {self.entity_type}.delete permission")

        file_path = self._find_file(entity_id)
        if not file_path:
            return False

        # Move to archive
        archive_dir = VAULT_ROOT / "_Archive" / self.entity_type.capitalize() / str(datetime.now().year)
        safe_mkdir(archive_dir)
        archive_path = archive_dir / file_path.name
        safe_move(file_path, archive_path)

        # Emit event
        self._emit_event(
            event_type=f"{self.entity_type}.deleted",
            entity=EntityRef(type=self.entity_type, id=entity_id),
            changes={},
            actor=actor,
            reason=reason or f"{self.entity_type} archived",
        )

        return True

    def list(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "updated_at",
        sort_desc: bool = True,
    ) -> List[T]:
        """List entities with optional filters"""
        entities = []
        for file_path in self.entity_dir.rglob("*.md"):
            if len(entities) >= limit + offset:
                break
            fm = self._read_frontmatter(file_path)
            if not fm:
                continue
            # Apply filters
            if filters:
                match = True
                for key, value in filters.items():
                    if fm.get(key) != value:
                        match = False
                        break
                if not match:
                    continue
            try:
                entity = self.model_class(**fm)
                entities.append(entity)
            except Exception as exc:
                # Report instead of silently swallowing: a skipped file
                # makes next_id() reissue taken IDs and hides tickets.
                print(
                    f"warning: skipped unreadable {self.entity_type} file "
                    f"{file_path.name}: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                continue

        # Sort
        def sort_key(e):
            val = getattr(e, sort_by, None)
            if val is None:
                return datetime.min if sort_desc else datetime.max
            return val
        entities.sort(key=sort_key, reverse=sort_desc)
        return entities[offset:offset + limit]

    def exists(self, entity_id: str) -> bool:
        """Check if entity exists"""
        return self._find_file(entity_id) is not None

    def _find_file(self, entity_id: str, **kwargs) -> Optional[Path]:
        """Find file by entity ID"""
        if self.entity_type == "ticket":
            files = list(self.entity_dir.glob(f"{entity_id} - *.md"))
            return files[0] if files else None
        elif self.entity_type == "client":
            # Search Business/ and Residential/ regardless of class_ (update()
            # and exists() call _find_file without kwargs; business clients
            # were unreachable before this fallback, 2026-10-07).
            class_ = kwargs.get("class_")
            if class_:
                subdir = CLIENT_SUBDIRS.get(class_, "Residential")
                file_path = self.entity_dir / subdir / f"{entity_id}.md"
                if file_path.exists():
                    return file_path
            for sub in dict.fromkeys(CLIENT_SUBDIRS.values()):
                file_path = self.entity_dir / sub / f"{entity_id}.md"
                if file_path.exists():
                    return file_path
            return None
        elif self.entity_type == "organization":
            # Organizations have naming: ORG-XXXXXX - Name.md
            files = list(self.entity_dir.glob(f"{entity_id} - *.md"))
            return files[0] if files else None
        else:
            file_path = self.entity_dir / f"{entity_id}.md"
            return file_path if file_path.exists() else None

    def _get_body_for_model(self, model: BaseRecord) -> str:
        """Get Markdown body for model (override in subclasses)"""
        return f"# {model.id} — {model.title}\n\n"


class TicketService(BaseService[Ticket]):
    """Ticket-specific service with worklog, status transitions, etc."""

    def __init__(
        self,
        event_emitter: Optional[EventEmitter] = None,
        permission_checker: Optional[PermissionChecker] = None,
    ):
        super().__init__("ticket", event_emitter, permission_checker)

    def next_id(self) -> str:
        """Generate the next ticket ID"""
        tickets = self.list(limit=10000)
        year = dt.date.today().year
        prefix = f"TKT-{year}-"
        maxn = 0
        for t in tickets:
            mid = t.id
            if mid.startswith(prefix):
                try:
                    maxn = max(maxn, int(mid[len(prefix):]))
                except ValueError:
                    pass
        return f"{prefix}{maxn + 1:03d}"

    def _generate_ticket_filepath(self, model: Ticket) -> Path:
        """Generate proper filepath for ticket: TKT-YYYY-### - Client - Title.md"""
        # Use client_name field if available, otherwise fall back to empty string
        client_name = getattr(model, 'client_name', '') or ""
        
        safe_client = re.sub(r'[<>:"/\\|?*]', '-', client_name) if client_name else ""
        title = getattr(model, 'title', '')
        safe_title = re.sub(r'[<>:"/\\|?*]', '-', title) if title else ""
        
        if client_name:
            filename = f"{model.id} - {safe_client} - {safe_title}.md"
        else:
            filename = f"{model.id} - {safe_title}.md"
        
        return self.entity_dir / filename

    def _get_body_for_model(self, model: Ticket) -> str:
        """Generate Markdown body for ticket"""
        lines = [
            f"# {model.id} — {model.title}",
            "",
            "## Summary",
            "Open ticket.",
            "",
            "> [!info]- Description — symptoms, context, findings",
            "> Details: symptoms, client's words, context. Copy from the intake email or call notes.",
            "",
            "> [!check]- Checklist",
            "> - [ ]",
            "",
            "## Work Log",
            "<!-- Oldest at top, newest at bottom. YAML `worklog:` above is the billing truth. -->",
        ]

        for entry in model.worklog:
            lines.append(
                f"### {entry.date.strftime('%Y-%m-%d')} — {entry.mode} — {entry.hours:g}h — ${entry.amount:g} — {entry.payment}"
            )
            if entry.description:
                lines.append(entry.description)
            lines.append("")

        lines.extend([
            "## Related",
            "- ",
            "",
            "## Resolution",
            "<!-- Filled when resolved/closed. -->",
        ])
        return "\n".join(lines)

    def add_worklog(
        self,
        ticket_id: str,
        entry: WorklogEntry,
        actor: EventActor,
        reason: Optional[str] = None,
        if_version: Optional[int] = None,
    ) -> Ticket:
        """Add a worklog entry to a ticket"""
        ticket = self.get(ticket_id)
        if not ticket:
            raise ValueError(f"Ticket {ticket_id} not found")
        if if_version is not None and ticket.version != if_version:
            raise self._version_mismatch(ticket_id, if_version, ticket.version)

        # Check permissions
        if not self._check_permission(actor, "ticket.add_worklog"):
            raise PermissionError(f"Actor {actor.id} lacks ticket.add_worklog permission")

        ticket.worklog.append(entry)
        ticket.updated_at = datetime.now()
        ticket.updated_by = actor.id
        ticket.version += 1

        # Write back
        file_path = self._find_file(ticket_id)
        if not file_path:
            raise ValueError(f"File for {ticket_id} not found")

        # Add to YAML worklog
        fm = self._read_frontmatter(file_path)
        if "worklog" not in fm:
            fm["worklog"] = []
        fm["worklog"].append(entry.model_dump(by_alias=True))
        fm["updated"] = ticket.updated_at.date().isoformat()
        fm["version"] = ticket.version

        # Update body
        body = self._get_body_for_model(ticket)
        self._write_frontmatter(file_path, fm, body)

        # Emit event
        self._emit_event(
            event_type="ticket.worklog_added",
            entity=EntityRef(type="ticket", id=ticket_id),
            changes={"worklog": EventChanges(old=None, new=str(entry))},
            actor=actor,
            reason=reason or f"Worklog added: {entry.mode} {entry.hours}h ${entry.amount}",
        )

        return ticket

    def remove_worklog(
        self,
        ticket_id: str,
        entry_index: int,
        actor: EventActor,
        reason: str,
        if_version: Optional[int] = None,
    ) -> Ticket:
        """Remove a worklog entry from a ticket (billing correction).

        reason is mandatory: every removal is a billing change and must
        carry an audit justification. Emits ticket.worklog_removed.
        """
        ticket = self.get(ticket_id)
        if not ticket:
            raise ValueError(f"Ticket {ticket_id} not found")
        if if_version is not None and ticket.version != if_version:
            raise self._version_mismatch(ticket_id, if_version, ticket.version)

        if not reason or not str(reason).strip():
            raise ValueError("remove_worklog requires a non-empty reason")

        if not self._check_permission(actor, "ticket.remove_worklog"):
            raise PermissionError(f"Actor {actor.id} lacks ticket.remove_worklog permission")

        if not isinstance(entry_index, int) or entry_index < 0 or entry_index >= len(ticket.worklog):
            raise IndexError(
                f"worklog entry_index {entry_index!r} out of range "
                f"for {ticket_id} (0..{len(ticket.worklog) - 1})"
            )

        entry = ticket.worklog[entry_index]
        ticket.worklog.pop(entry_index)
        ticket.updated_at = datetime.now()
        ticket.updated_by = actor.id
        ticket.version += 1

        # Write back
        file_path = self._find_file(ticket_id)
        if not file_path:
            raise ValueError(f"File for {ticket_id} not found")

        fm = self._read_frontmatter(file_path)
        yaml_worklog = fm.get("worklog") or []
        if len(yaml_worklog) != len(ticket.worklog) + 1:
            raise ValueError(
                f"worklog YAML/model length mismatch for {ticket_id}: "
                f"yaml={len(yaml_worklog)} model={len(ticket.worklog) + 1}; refusing removal"
            )
        yaml_worklog.pop(entry_index)
        fm["worklog"] = yaml_worklog
        fm["updated"] = ticket.updated_at.date().isoformat()
        fm["version"] = ticket.version

        # Regenerate body (work log section rebuilt from remaining entries)
        body = self._get_body_for_model(ticket)
        self._write_frontmatter(file_path, fm, body)

        # Emit event
        self._emit_event(
            event_type="ticket.worklog_removed",
            entity=EntityRef(type="ticket", id=ticket_id),
            changes={"worklog": EventChanges(old=str(entry), new=None)},
            actor=actor,
            reason=reason,
        )

        return ticket

    def change_status(
        self,
        ticket_id: str,
        new_status: str,
        actor: EventActor,
        follow_up: Optional[datetime] = None,
        reason: Optional[str] = None,
        if_version: Optional[int] = None,
    ) -> Ticket:
        """Change ticket status with validation"""
        ticket = self.get(ticket_id)
        if not ticket:
            raise ValueError(f"Ticket {ticket_id} not found")
        if if_version is not None and ticket.version != if_version:
            raise self._version_mismatch(ticket_id, if_version, ticket.version)

        # Validate status transition (would load from config)
        valid_statuses = ["new", "triaged", "in-progress", "waiting", "resolved", "closed", "cancelled"]
        if new_status not in valid_statuses:
            raise ValueError(f"Invalid status: {new_status}")

        old_status = ticket.status
        changes = {"status": new_status, "updated_by": actor.id}
        if follow_up:
            changes["follow_up"] = follow_up

        updated = self.update(ticket_id, changes, actor, reason, if_version=if_version)

        # Emit specific event
        self._emit_event(
            event_type="ticket.status_changed",
            entity=EntityRef(type="ticket", id=ticket_id),
            changes={"status": EventChanges(old=old_status, new=new_status)},
            actor=actor,
            reason=reason or f"Status changed from {old_status} to {new_status}",
        )

        return updated


class ClientService(BaseService[Client]):
    """Client service"""

    def __init__(
        self,
        event_emitter: Optional[EventEmitter] = None,
        permission_checker: Optional[PermissionChecker] = None,
    ):
        super().__init__("client", event_emitter, permission_checker)

    def _get_dir(self, **kwargs) -> Path:
        class_ = kwargs.get("class_", "residential")
        subdir = CLIENT_SUBDIRS.get(class_, "Residential")
        return self.entity_dir / subdir

    def _find_file(self, entity_id: str, **kwargs) -> Optional[Path]:
        # Base implementation searches both Business/ and Residential/,
        # honoring an explicit class_ kwarg when given.
        return super()._find_file(entity_id, **kwargs)


class ContactService(BaseService["Contact"]):
    def __init__(
        self,
        event_emitter: Optional[EventEmitter] = None,
        permission_checker: Optional[PermissionChecker] = None,
    ):
        super().__init__("contact", event_emitter, permission_checker)


class OrganizationService(BaseService["Organization"]):
    def __init__(
        self,
        event_emitter: Optional[EventEmitter] = None,
        permission_checker: Optional[PermissionChecker] = None,
    ):
        super().__init__("organization", event_emitter, permission_checker)


class AssetService(BaseService["Asset"]):
    def __init__(
        self,
        event_emitter: Optional[EventEmitter] = None,
        permission_checker: Optional[PermissionChecker] = None,
    ):
        super().__init__("asset", event_emitter, permission_checker)


class UserService(BaseService["User"]):
    def __init__(
        self,
        event_emitter: Optional[EventEmitter] = None,
        permission_checker: Optional[PermissionChecker] = None,
    ):
        super().__init__("user", event_emitter, permission_checker)


class ProjectService(BaseService["Project"]):
    def __init__(
        self,
        event_emitter: Optional[EventEmitter] = None,
        permission_checker: Optional[PermissionChecker] = None,
    ):
        super().__init__("project", event_emitter, permission_checker)


class TaskService(BaseService["Task"]):
    def __init__(
        self,
        event_emitter: Optional[EventEmitter] = None,
        permission_checker: Optional[PermissionChecker] = None,
    ):
        super().__init__("task", event_emitter, permission_checker)


class KnowledgeService(BaseService["Knowledge"]):
    def __init__(
        self,
        event_emitter: Optional[EventEmitter] = None,
        permission_checker: Optional[PermissionChecker] = None,
    ):
        super().__init__("knowledge", event_emitter, permission_checker)


class ProblemService(BaseService["Problem"]):
    def __init__(
        self,
        event_emitter: Optional[EventEmitter] = None,
        permission_checker: Optional[PermissionChecker] = None,
    ):
        super().__init__("problem", event_emitter, permission_checker)


class ChangeService(BaseService["Change"]):
    def __init__(
        self,
        event_emitter: Optional[EventEmitter] = None,
        permission_checker: Optional[PermissionChecker] = None,
    ):
        super().__init__("change", event_emitter, permission_checker)


class RequestService(BaseService["Request"]):
    def __init__(
        self,
        event_emitter: Optional[EventEmitter] = None,
        permission_checker: Optional[PermissionChecker] = None,
    ):
        super().__init__("request", event_emitter, permission_checker)


class ContractService(BaseService["Contract"]):
    def __init__(
        self,
        event_emitter: Optional[EventEmitter] = None,
        permission_checker: Optional[PermissionChecker] = None,
    ):
        super().__init__("contract", event_emitter, permission_checker)