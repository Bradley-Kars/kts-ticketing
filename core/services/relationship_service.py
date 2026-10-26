"""Relationship Service — Manage entity relationships"""

from typing import List, Optional, Dict, Any
from datetime import datetime

from ..models import (
    BaseRecord, EntityRef, Relationship, AnyEntity, MODEL_MAP,
    EventActor, EventEntity, EventChanges
)
from ..events import EventEmitter
from ..permissions import PermissionChecker


RELATIONSHIP_INVERSE = {
    "parent_of": "child_of",
    "child_of": "parent_of",
    "related": "related",
    "duplicate_of": "duplicated_by",
    "duplicated_by": "duplicate_of",
    "caused_by": "causes",
    "causes": "caused_by",
    "resolves": "resolved_by",
    "resolved_by": "resolves",
    "affected_asset": "affects",
    "affects": "affected_asset",
    "affected_customer": "affects",
    "affected_organization": "affects",
    "reported_by": "reported",
    "reported": "reported_by",
    "assigned_to": "assigned",
    "assigned": "assigned_to",
    "documents_resolution": "resolution_documented_in",
    "resolution_documented_in": "documents_resolution",
    "blocks": "blocked_by",
    "blocked_by": "blocks",
    "depends_on": "dependency_of",
    "dependency_of": "depends_on",
}


class RelationshipService:
    """Manage bidirectional relationships between entities"""

    def __init__(
        self,
        services: Dict[str, Any],  # Dict of entity_type -> service
        event_emitter: Optional[EventEmitter] = None,
        permission_checker: Optional[PermissionChecker] = None,
    ):
        self.services = services
        self.event_emitter = event_emitter
        self.permission_checker = permission_checker

    def _get_service(self, entity_type: str):
        """Get service for entity type"""
        return self.services.get(entity_type)

    def _check_permission(self, actor: EventActor, entity_type: str, action: str) -> bool:
        if self.permission_checker:
            return self.permission_checker.check(actor, f"{entity_type}.{action}")
        return True

    def _emit_event(
        self,
        event_type: str,
        entity: EntityRef,
        changes: Dict[str, EventChanges],
        actor: EventActor,
        reason: Optional[str] = None,
    ) -> None:
        if self.event_emitter:
            self.event_emitter.emit(
                event_type=event_type,
                entity=entity,
                changes=changes,
                actor=actor,
                reason=reason,
            )

    def link(
        self,
        source: EntityRef,
        target: EntityRef,
        relationship_type: str,
        actor: EventActor,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Create a bidirectional relationship"""
        # Validate relationship type
        if relationship_type not in RELATIONSHIP_INVERSE:
            raise ValueError(f"Unknown relationship type: {relationship_type}")

        # Check permissions on both entities
        if not self._check_permission(actor, source.type, "link"):
            raise PermissionError(f"Actor {actor.id} lacks {source.type}.link permission")
        if not self._check_permission(actor, target.type, "link"):
            raise PermissionError(f"Actor {actor.id} lacks {target.type}.link permission")

        # Get services
        source_service = self._get_service(source.type)
        target_service = self._get_service(target.type)
        if not source_service or not target_service:
            raise ValueError(f"Service not found for {source.type} or {target.type}")

        # Load entities
        source_entity = source_service.get(source.id)
        target_entity = target_service.get(target.id)
        if not source_entity or not target_entity:
            raise ValueError("Source or target entity not found")

        # Check if relationship already exists
        existing = self._find_relationship(source_entity, target.id, relationship_type)
        if existing:
            return False  # Already linked

        # Add forward relationship
        forward_rel = Relationship(
            type=relationship_type,
            target=target.id,
            metadata=metadata or {},
        )
        source_entity.relationships.append(forward_rel)
        source_entity.updated_at = datetime.now()
        source_entity.updated_by = actor.id
        source_entity.version += 1

        # Add inverse relationship
        inverse_type = RELATIONSHIP_INVERSE[relationship_type]
        inverse_rel = Relationship(
            type=inverse_type,
            target=source.id,
            metadata=metadata or {},
        )
        target_entity.relationships.append(inverse_rel)
        target_entity.updated_at = datetime.now()
        target_entity.updated_by = actor.id
        target_entity.version += 1

        # Save both
        source_service.update(source.id, {"relationships": [r.model_dump() for r in source_entity.relationships]}, actor)
        target_service.update(target.id, {"relationships": [r.model_dump() for r in target_entity.relationships]}, actor)

        # Emit events
        self._emit_event(
            event_type="relationship.created",
            entity=source,
            changes={"relationships": EventChanges(old=None, new=relationship_type)},
            actor=actor,
            reason=f"Linked to {target.id} via {relationship_type}",
        )
        self._emit_event(
            event_type="relationship.created",
            entity=target,
            changes={"relationships": EventChanges(old=None, new=inverse_type)},
            actor=actor,
            reason=f"Linked from {source.id} via {inverse_type}",
        )

        return True

    def unlink(
        self,
        source: EntityRef,
        target: EntityRef,
        relationship_type: str,
        actor: EventActor,
    ) -> bool:
        """Remove a bidirectional relationship"""
        if relationship_type not in RELATIONSHIP_INVERSE:
            raise ValueError(f"Unknown relationship type: {relationship_type}")

        if not self._check_permission(actor, source.type, "link"):
            raise PermissionError(f"Actor {actor.id} lacks {source.type}.link permission")
        if not self._check_permission(actor, target.type, "link"):
            raise PermissionError(f"Actor {actor.id} lacks {target.type}.link permission")

        source_service = self._get_service(source.type)
        target_service = self._get_service(target.type)
        if not source_service or not target_service:
            raise ValueError(f"Service not found")

        source_entity = source_service.get(source.id)
        target_entity = target_service.get(target.id)
        if not source_entity or not target_entity:
            raise ValueError("Source or target entity not found")

        # Remove forward
        forward_removed = self._remove_relationship(source_entity, target.id, relationship_type)
        # Remove inverse
        inverse_type = RELATIONSHIP_INVERSE[relationship_type]
        inverse_removed = self._remove_relationship(target_entity, source.id, inverse_type)

        if not forward_removed and not inverse_removed:
            return False

        # Save
        if forward_removed:
            source_entity.updated_at = datetime.now()
            source_entity.updated_by = actor.id
            source_entity.version += 1
            source_service.update(source.id, {"relationships": [r.model_dump() for r in source_entity.relationships]}, actor)

        if inverse_removed:
            target_entity.updated_at = datetime.now()
            target_entity.updated_by = actor.id
            target_entity.version += 1
            target_service.update(target.id, {"relationships": [r.model_dump() for r in target_entity.relationships]}, actor)

        # Emit events
        if forward_removed:
            self._emit_event(
                event_type="relationship.removed",
                entity=source,
                changes={"relationships": EventChanges(old=relationship_type, new=None)},
                actor=actor,
                reason=f"Unlinked from {target.id}",
            )
        if inverse_removed:
            self._emit_event(
                event_type="relationship.removed",
                entity=target,
                changes={"relationships": EventChanges(old=inverse_type, new=None)},
                actor=actor,
                reason=f"Unlinked from {source.id}",
            )

        return True

    def _find_relationship(self, entity: BaseRecord, target_id: str, rel_type: str) -> Optional[Relationship]:
        for rel in entity.relationships:
            if rel.target == target_id and rel.type == rel_type:
                return rel
        return None

    def _remove_relationship(self, entity: BaseRecord, target_id: str, rel_type: str) -> bool:
        for i, rel in enumerate(entity.relationships):
            if rel.target == target_id and rel.type == rel_type:
                entity.relationships.pop(i)
                return True
        return False

    def get_related(
        self,
        entity: EntityRef,
        relationship_types: Optional[List[str]] = None,
    ) -> List[EntityRef]:
        """Get all related entities, optionally filtered by relationship type"""
        service = self._get_service(entity.type)
        if not service:
            return []

        entity_obj = service.get(entity.id)
        if not entity_obj:
            return []

        results = []
        for rel in entity_obj.relationships:
            if relationship_types and rel.type not in relationship_types:
                continue
            # Determine target entity type from ID prefix
            target_prefix = rel.target.split("-")[0]
            target_type = self._prefix_to_type(target_prefix)
            if target_type:
                results.append(EntityRef(type=target_type, id=rel.target))
        return results

    def get_relationships(self, entity: EntityRef) -> List[Relationship]:
        """Get all relationships for an entity"""
        service = self._get_service(entity.type)
        if not service:
            return []
        entity_obj = service.get(entity.id)
        if not entity_obj:
            return []
        return entity_obj.relationships

    def _prefix_to_type(self, prefix: str) -> Optional[str]:
        prefix_map = {
            "TKT": "ticket",
            "TCK": "ticket",
            "CUS": "client",
            "CON": "contact",
            "ORG": "organization",
            "AST": "asset",
            "USR": "user",
            "PRJ": "project",
            "TSK": "task",
            "KB": "knowledge",
            "PRB": "problem",
            "CHG": "change",
            "REQ": "request",
            "CRT": "contract",
        }
        return prefix_map.get(prefix)