"""Project Service — CRUD operations for projects"""

import re
import yaml
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Type, TypeVar, Generic

from ..models import (
    BaseRecord, AnyEntity, EntityType, EntityRef, MODEL_MAP, get_model_for_type,
    get_id_prefix, validate_id, extract_id_prefix, Event, EventActor, EventEntity,
    EventChanges, Project, Organization, User, Contact,
    WorklogEntry, Relationship
)
from ..events import EventEmitter
from ..permissions import PermissionChecker
from ..paths import vault_root, projects_dir
from ..safe_io import safe_mkdir, safe_write, safe_move


T = TypeVar('T', bound='BaseRecord')


class ProjectService:
    """Project-specific service with project management features"""

    def __init__(
        self,
        event_emitter: Optional[EventEmitter] = None,
        permission_checker: Optional["PermissionChecker"] = None,
    ):
        from ..models import Project
        self.entity_type = "project"
        self.model_class = Project
        self.id_prefix = "PRJ"
        self.entity_dir = projects_dir()
        self.event_emitter = event_emitter
        self.permission_checker = permission_checker
        safe_mkdir(self.entity_dir)

    # --- File helpers ---

    def _get_file_path(self, entity_id: str, **kwargs) -> Path:
        """Get file path for project: PRJ-XXXXXX - Name.md"""
        files = list(self.entity_dir.glob(f"{entity_id} - *.md"))
        if files:
            return files[0]
        # For new projects
        title = kwargs.get("title", "")
        safe_title = re.sub(r'[<>:"/\\|?*]', '-', title) if title else ""
        if safe_title:
            return self.entity_dir / f"{entity_id} - {safe_title}.md"
        return self.entity_dir / f"{entity_id}.md"

    def _read_frontmatter(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """Read YAML frontmatter from a Markdown file"""
        try:
            content = file_path.read_text(encoding="utf-8")
            if not content.startswith("---"):
                return None
            parts = content.split("---", 2)
            if len(parts) < 3:
                return None
            return yaml.safe_load(parts[1]) or {}
        except Exception:
            return None

    def _write_frontmatter(self, file_path: Path, data: Dict[str, Any], body: str = "") -> None:
        """Write entity to Markdown file with YAML frontmatter"""
        safe_mkdir(file_path.parent)
        fm = yaml.dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
        content = f"---\n{fm}---\n\n{body}"
        tmp = file_path.with_name(file_path.name + ".tmp")
        safe_write(tmp, content, encoding="utf-8")
        import os
        os.replace(tmp, file_path)

    def _serialize_for_yaml(self, model) -> Dict[str, Any]:
        """Convert Pydantic model to dict suitable for YAML frontmatter.

        Excludes only 'ai' (stored under .ticketing/ai/). version IS
        serialized so optimistic-concurrency bumps persist across re-reads.
        """
        data = model.model_dump(
            exclude_none=True,
            exclude={"ai"},
            by_alias=True,
        )
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
            elif isinstance(value, list):
                data[key] = [
                    v.isoformat() if isinstance(v, datetime) else v
                    for v in value
                ]
        return data

    def _get_body_for_model(self, model) -> str:
        """Get Markdown body for project"""
        return f"# {model.id} — {model.title}\n\n"

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

    def _check_permission(self, actor, permission: str) -> bool:
        if self.permission_checker:
            return self.permission_checker.check(actor, permission)
        return True

    # --- CRUD Operations ---

    def create(self, model, actor) -> any:
        """Create a new project"""
        if not self._check_permission(actor, f"{self.entity_type}.create"):
            raise PermissionError(f"Actor {actor.id} lacks {self.entity_type}.create permission")

        if not validate_id(self.entity_type, model.id):
            raise ValueError(f"Invalid ID format for {self.entity_type}: {model.id}")

        if self.exists(model.id):
            raise ValueError(f"Entity {model.id} already exists")

        now = datetime.now()
        model.created_at = now
        model.updated_at = now
        model.created_by = actor.id
        model.updated_by = actor.id
        model.version = 1

        file_path = self._get_file_path(model.id, title=model.title)
        body = self._get_body_for_model(model)
        self._write_frontmatter(file_path, self._serialize_for_yaml(model), body)

        self._emit_event(
            event_type=f"{self.entity_type}.created",
            entity=EntityRef(type=self.entity_type, id=model.id),
            changes={},
            actor=actor,
            reason=f"{self.entity_type} created",
        )

        return model

    def get(self, entity_id: str) -> Optional[any]:
        """Get project by ID"""
        file_path = self._find_file(entity_id)
        if not file_path:
            return None
        fm = self._read_frontmatter(file_path)
        if not fm:
            return None
        try:
            from ..models import Project
            return Project(**fm)
        except Exception as e:
            raise ValueError(f"Failed to parse {entity_id}: {e}")

    def update(
        self,
        entity_id: str,
        changes: Dict[str, Any],
        actor,
        reason: Optional[str] = None,
    ) -> any:
        if not self._check_permission(actor, f"{self.entity_type}.update"):
            raise PermissionError(f"Actor {actor.id} lacks {self.entity_type}.update permission")
        current = self.get(entity_id)
        if not current:
            raise ValueError(f"Entity {entity_id} not found")

        event_changes: Dict[str, EventChanges] = {}
        for key, new_value in changes.items():
            old_value = getattr(current, key, None)
            if old_value != new_value:
                event_changes[key] = EventChanges(
                    old=str(old_value) if old_value is not None else None,
                    new=str(new_value) if new_value is not None else None,
                )
                setattr(current, key, new_value)

        if not event_changes:
            return current

        current.updated_at = datetime.now()
        current.updated_by = actor.id
        current.version += 1

        file_path = self._find_file(entity_id)
        if not file_path:
            raise ValueError(f"File for {entity_id} not found")
        body = self._get_body_for_model(current)
        self._write_frontmatter(file_path, self._serialize_for_yaml(current), body)

        self._emit_event(
            event_type=f"{self.entity_type}.updated",
            entity=EntityRef(type=self.entity_type, id=entity_id),
            changes=event_changes,
            actor=actor,
            reason=reason,
        )

        return current

    def delete(self, entity_id: str, actor, reason: Optional[str] = None) -> bool:
        if not self._check_permission(actor, f"{self.entity_type}.delete"):
            raise PermissionError(f"Actor {actor.id} lacks {self.entity_type}.delete permission")

        file_path = self._find_file(entity_id)
        if not file_path:
            return False

        archive_dir = vault_root() / "_Archive" / self.entity_type.capitalize() / str(datetime.now().year)
        safe_mkdir(archive_dir)
        archive_path = archive_dir / file_path.name
        safe_move(file_path, archive_path)

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
    ) -> List[any]:
        entities = []
        for file_path in self.entity_dir.rglob("*.md"):
            if len(entities) >= limit + offset:
                break
            fm = self._read_frontmatter(file_path)
            if not fm:
                continue
            if filters:
                match = True
                for key, value in filters.items():
                    if fm.get(key) != value:
                        match = False
                        break
                if not match:
                    continue
            try:
                from ..models import Project
                entity = Project(**fm)
                entities.append(entity)
            except Exception:
                continue

        def sort_key(e):
            val = getattr(e, sort_by, None)
            if val is None:
                return datetime.min if sort_desc else datetime.max
            return val
        entities.sort(key=sort_key, reverse=sort_desc)
        return entities[offset:offset + limit]

    def exists(self, entity_id: str) -> bool:
        return self._find_file(entity_id) is not None

    def _find_file(self, entity_id: str, **kwargs) -> Optional[Path]:
        files = list(self.entity_dir.glob(f"{entity_id} - *.md"))
        return files[0] if files else None

    def next_id(self) -> str:
        entities = self.list(limit=10000)
        year = datetime.now().year
        prefix = f"PRJ-{year}-"
        maxn = 0
        for e in entities:
            mid = e.id
            if mid.startswith(prefix):
                try:
                    maxn = max(maxn, int(mid[len(prefix):]))
                except ValueError:
                    pass
        return f"{prefix}{maxn + 1:06d}"


def get_project_service():
    """Factory function for ProjectService"""
    from ..events import EventEmitter
    from ..permissions import PermissionChecker
    event_emitter = EventEmitter()
    permission_checker = PermissionChecker()
    return ProjectService(event_emitter=event_emitter, permission_checker=permission_checker)
