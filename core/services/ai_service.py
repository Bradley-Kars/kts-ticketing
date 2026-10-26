"""AI Service — Observations, Suggestions, Actions, Context Packs"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict

from ..models import (
    AnyEntity, EntityRef, Ticket, Client, Asset, User,
    AIObservation, AISuggestion, AIAction, MODEL_MAP, get_model_for_type,
    Relationship
)
from ..events import EventEmitter, EventActor, EventEntity, EventChanges
from ..permissions import PermissionChecker
from ..paths import ai_dir
from ..safe_io import safe_mkdir, safe_write, safe_check


AI_DIR = ai_dir()
OBSERVATIONS_DIR = AI_DIR / "observations"
SUGGESTIONS_DIR = AI_DIR / "suggestions"
ACTIONS_DIR = AI_DIR / "actions"
EMBEDDINGS_DIR = AI_DIR / "embeddings"
SUMMARIES_DIR = AI_DIR / "summaries"
CLASSIFICATIONS_DIR = AI_DIR / "classifications"
RECOMMENDATIONS_DIR = AI_DIR / "recommendations"
CONTEXT_DIR = AI_DIR / "context"


@dataclass
class AIContextPack:
    """Complete AI context for an entity"""
    entity: EntityRef
    ticket: Optional[Dict[str, Any]] = None
    client: Optional[Dict[str, Any]] = None
    asset: Optional[Dict[str, Any]] = None
    assignee: Optional[Dict[str, Any]] = None
    worklog: List[Dict[str, Any]] = None
    similar_tickets: List[str] = None
    knowledge_articles: List[str] = None
    relationships: List[Dict[str, Any]] = None
    sla: Optional[Dict[str, Any]] = None
    ai: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.worklog is None:
            self.worklog = []
        if self.similar_tickets is None:
            self.similar_tickets = []
        if self.knowledge_articles is None:
            self.knowledge_articles = []
        if self.relationships is None:
            self.relationships = []


class AIService:
    """AI operations: context packs, suggestions, actions, embeddings"""

    def __init__(
        self,
        services: Dict[str, Any],
        event_emitter: Optional[EventEmitter] = None,
        permission_checker: Optional[PermissionChecker] = None,
    ):
        self.services = services
        self.event_emitter = event_emitter
        self.permission_checker = permission_checker

        for d in [OBSERVATIONS_DIR, SUGGESTIONS_DIR, ACTIONS_DIR,
                  EMBEDDINGS_DIR, SUMMARIES_DIR, CLASSIFICATIONS_DIR,
                  RECOMMENDATIONS_DIR, CONTEXT_DIR]:
            safe_mkdir(d)

    def get_context_pack(self, entity: EntityRef) -> AIContextPack:
        """Build complete AI context pack for an entity"""
        ticket_svc = self.services.get("ticket")
        client_svc = self.services.get("client")
        asset_svc = self.services.get("asset")
        user_svc = self.services.get("user")
        knowledge_svc = self.services.get("knowledge")
        relationship_svc = self.services.get("relationship")

        context = AIContextPack(entity=entity)

        if entity.type == "ticket" and ticket_svc:
            ticket = ticket_svc.get(entity.id)
            if ticket:
                context.ticket = ticket.model_dump(by_alias=True)
                context.worklog = [w.model_dump() for w in ticket.worklog]

                if ticket.organization_id and client_svc:
                    org = client_svc.get(ticket.organization_id)
                    if org:
                        context.client = org.model_dump(by_alias=True)

                if ticket.contact_id and client_svc:
                    contact = client_svc.get(ticket.contact_id)
                    if contact:
                        context.client = contact.model_dump(by_alias=True)

                if ticket.asset_id and asset_svc:
                    asset = asset_svc.get(ticket.asset_id)
                    if asset:
                        context.asset = asset.model_dump(by_alias=True)

                if ticket.assigned_to and user_svc:
                    user = user_svc.get(ticket.assigned_to)
                    if user:
                        context.assignee = user.model_dump(by_alias=True)

                context.sla = {
                    "policy": ticket.sla_policy,
                    "response_due": ticket.sla_response_due.isoformat() if ticket.sla_response_due else None,
                    "resolution_due": ticket.sla_resolution_due.isoformat() if ticket.sla_resolution_due else None,
                    "paused": ticket.sla_paused,
                }

                if relationship_svc:
                    rels = relationship_svc.get_relationships(entity)
                    context.relationships = [r.model_dump() for r in rels]

        self._save_context(entity, context)
        return context

    def _save_context(self, entity: EntityRef, context: AIContextPack) -> None:
        file_path = CONTEXT_DIR / f"{entity.id}.json"
        safe_write(file_path, json.dumps(asdict(context), default=str, indent=2))

    def classify_ticket(
        self,
        ticket_id: str,
        model: str = "opencode/nemotron-3-ultra-free",
        actor: Optional[EventActor] = None,
    ) -> Dict[str, Any]:
        ticket_svc = self.services.get("ticket")
        if not ticket_svc:
            raise ValueError("Ticket service not available")

        ticket = ticket_svc.get(ticket_id)
        if not ticket:
            raise ValueError(f"Ticket {ticket_id} not found")

        prompt = self._build_classification_prompt(ticket)
        result = self._call_ai_model(prompt, model)

        classification = {
            "ticket_id": ticket_id,
            "model": model,
            "categories": result.get("categories", []),
            "confidence": result.get("confidence", 0.0),
            "reasoning": result.get("reasoning", ""),
            "generated_at": datetime.now().isoformat(),
        }
        safe_mkdir(CLASSIFICATIONS_DIR)
        safe_write(CLASSIFICATIONS_DIR / f"{ticket_id}.json", json.dumps(classification, indent=2))

        ticket_svc.update(ticket_id, {
            "ai": {
                "classification_confidence": result.get("confidence", 0.0),
                "classification_model": model,
                "classification_categories": result.get("categories", []),
            }
        }, actor or EventActor(id="AI-OPENCODE", type="ai", name="OpenCode"))

        self._emit_observation(
            kind="classification_made",
            target=EntityRef(type="ticket", id=ticket_id),
            data=classification,
            confidence=result.get("confidence", 0.0),
            model=model,
        )

        return classification

    def find_similar_tickets(
        self,
        ticket_id: str,
        limit: int = 5,
        model: str = "opencode/nemotron-3-ultra-free",
        actor: Optional[EventActor] = None,
    ) -> List[Dict[str, Any]]:
        ticket_svc = self.services.get("ticket")
        search_svc = self.services.get("search")
        if not ticket_svc or not search_svc:
            raise ValueError("Required services not available")

        ticket = ticket_svc.get(ticket_id)
        if not ticket:
            raise ValueError(f"Ticket {ticket_id} not found")

        search_query = f"{ticket.title} {ticket.category} {ticket.work_type}"
        results = search_svc.search(
            query=search_query,
            entity_types=["ticket"],
            filters={"status": {"$ne": "closed"}},
            limit=limit + 1,
        )

        similar = [
            {"id": r.entity.id, "score": r.score, "snippet": r.snippet}
            for r in results
            if r.entity.id != ticket_id
        ][:limit]

        ticket_svc.update(ticket_id, {
            "ai": {"similar_tickets": [s["id"] for s in similar]}
        }, actor or EventActor(id="AI-OPENCODE", type="ai", name="OpenCode"))

        self._emit_observation(
            kind="similar_tickets_found",
            target=EntityRef(type="ticket", id=ticket_id),
            data={"tickets": similar},
            confidence=0.9 if similar else 0.0,
            model=model,
        )

        return similar

    def suggest_troubleshooting(
        self,
        ticket_id: str,
        model: str = "opencode/nemotron-3-ultra-free",
        actor: Optional[EventActor] = None,
    ) -> AISuggestion:
        ticket_svc = self.services.get("ticket")
        if not ticket_svc:
            raise ValueError("Ticket service not available")

        ticket = ticket_svc.get(ticket_id)
        if not ticket:
            raise ValueError(f"Ticket {ticket_id} not found")

        context = self.get_context_pack(EntityRef(type="ticket", id=ticket_id))
        prompt = self._build_troubleshooting_prompt(ticket, context)
        result = self._call_ai_model(prompt, model)

        suggestion = AISuggestion(
            type="ai_suggestion",
            id=self._generate_id("AIS"),
            suggestion_id=self._generate_id("AIS"),
            title=f"Troubleshooting suggestions for {ticket_id}",
            status="pending",
            created_at=datetime.now(),
            expires_at=None,
            model=model,
            suggestion={
                "action": "troubleshoot",
                "target": {"type": "ticket", "id": ticket_id},
                "proposed_value": result.get("steps", []),
                "confidence": result.get("confidence", 0.0),
                "reasoning": result.get("reasoning", ""),
                "supporting_evidence": result.get("evidence", []),
                "requires_approval": True,
            },
            relationships=[
                Relationship(type="documents_resolution", target=ticket_id),
            ],
            created_by="AI-OPENCODE",
            updated_by="AI-OPENCODE",
        )

        safe_mkdir(SUGGESTIONS_DIR)
        safe_write(SUGGESTIONS_DIR / f"{suggestion.suggestion_id}.json",
                   json.dumps(suggestion.model_dump(by_alias=True), default=str, indent=2))

        ticket_svc.update(ticket_id, {
            "ai": {"pending_suggestions": [suggestion.suggestion_id]}
        }, actor or EventActor(id="AI-OPENCODE", type="ai", name="OpenCode"))

        return suggestion

    def resolve_suggestion(
        self,
        suggestion_id: str,
        action: str,
        actor: EventActor,
        edited_value: Optional[Any] = None,
    ) -> AIAction:
        file_path = SUGGESTIONS_DIR / f"{suggestion_id}.json"
        if not file_path.exists():
            raise ValueError(f"Suggestion {suggestion_id} not found")

        suggestion = AISuggestion.model_validate_json(file_path.read_text())

        if action == "accept":
            suggestion.status = "accepted"
            suggestion.resolved_at = datetime.now()
            suggestion.resolved_by = actor.id
            suggestion.resolution_action = "accepted"

            # Snapshot the target's events before executing so the mutation
            # event can be identified by set difference (query order within
            # a day is filesystem order, so position cannot be trusted).
            target = suggestion.suggestion["target"]
            target_ref = EntityRef(type=target["type"], id=target["id"])
            before_ids = set()
            if self.event_emitter:
                before_ids = {
                    e.event_id
                    for e in self.event_emitter.get_for_entity(target_ref, limit=200)
                }

            action_result = self._execute_suggestion(suggestion, actor)

            new_events = []
            if self.event_emitter:
                new_events = [
                    e
                    for e in self.event_emitter.get_for_entity(target_ref, limit=200)
                    if e.event_id not in before_ids
                ]
            executed = not (
                isinstance(action_result, dict) and action_result.get("error")
            )
            result_event = None
            if executed and new_events:
                preferred = [
                    e for e in new_events
                    if e.event_type.endswith(".status_changed")
                ] or new_events
                result_event = preferred[0].event_id

            if executed and result_event:
                status = "completed"
                error_message = None
            else:
                status = "failed"
                if isinstance(action_result, dict) and action_result.get("error"):
                    error_message = str(action_result["error"])
                else:
                    error_message = "execution produced no mutation event"

            ai_action_id = self._generate_id("AIA")
            ai_action = AIAction(
                type="ai_action",
                id=ai_action_id,
                action_id=ai_action_id,
                title=f"Executed suggestion {suggestion_id}",
                status=status,
                created_at=datetime.now(),
                updated_at=datetime.now(),
                created_by=actor.id,
                updated_by=actor.id,
                source_suggestion=suggestion_id,
                executed_by=actor.id,
                executed_at=datetime.now(),
                result_event=result_event,
                error_message=error_message,
            )

            safe_mkdir(ACTIONS_DIR)
            safe_write(ACTIONS_DIR / f"{ai_action.action_id}.json",
                       json.dumps(ai_action.model_dump(by_alias=True), default=str, indent=2))

            # persist the accepted suggestion status (the accept branch
            # returns early, so the common write below would be skipped)
            safe_write(file_path,
                       json.dumps(suggestion.model_dump(by_alias=True), default=str, indent=2))

            return ai_action

        elif action == "reject":
            suggestion.status = "rejected"
            suggestion.resolved_at = datetime.now()
            suggestion.resolved_by = actor.id
            suggestion.resolution_action = "rejected"

        elif action == "edit":
            suggestion.status = "edited"
            suggestion.resolved_at = datetime.now()
            suggestion.resolved_by = actor.id
            suggestion.resolution_action = "edited"
            if edited_value:
                suggestion.suggestion["proposed_value"] = edited_value

        safe_write(file_path, json.dumps(suggestion.model_dump(by_alias=True), default=str, indent=2))
        return None

    def _execute_suggestion(self, suggestion: AISuggestion, actor: EventActor) -> Dict[str, Any]:
        action = suggestion.suggestion["action"]
        target = suggestion.suggestion["target"]

        service = self.services.get(target["type"])
        if not service:
            return {"error": "Service not found"}

        if action == "change_status":
            if target["type"] == "ticket":
                ticket_svc = self.services.get("ticket")
                return ticket_svc.change_status(
                    target["id"],
                    suggestion.suggestion["proposed_value"],
                    actor,
                    reason=f"AI suggestion accepted: {suggestion.suggestion.get('reasoning', '')}",
                )

        return {"error": f"Action {action} not implemented"}

    def generate_summary(
        self,
        ticket_id: str,
        model: str = "opencode/nemotron-3-ultra-free",
        actor: Optional[EventActor] = None,
    ) -> str:
        ticket_svc = self.services.get("ticket")
        if not ticket_svc:
            raise ValueError("Ticket service not available")

        ticket = ticket_svc.get(ticket_id)
        if not ticket:
            raise ValueError(f"Ticket {ticket_id} not found")

        context = self.get_context_pack(EntityRef(type="ticket", id=ticket_id))
        prompt = self._build_summary_prompt(ticket, context)
        result = self._call_ai_model(prompt, model)
        summary = result.get("summary", "")

        safe_mkdir(SUMMARIES_DIR)
        safe_write(SUMMARIES_DIR / f"{ticket_id}.json", json.dumps({
            "ticket_id": ticket_id,
            "summary": summary,
            "model": model,
            "generated_at": datetime.now().isoformat(),
        }, indent=2))

        ticket_svc.update(ticket_id, {
            "ai": {
                "summary_generated": True,
                "summary_model": model,
            }
        }, actor or EventActor(id="AI-OPENCODE", type="ai", name="OpenCode"))

        return summary

    def generate_embedding(
        self,
        entity: EntityRef,
        model: str = "nomic-embed-text",
    ) -> List[float]:
        context = self.get_context_pack(entity)
        text = self._context_to_text(context)

        embedding = [0.0] * 768

        safe_mkdir(EMBEDDINGS_DIR)
        import numpy as np
        np.save(safe_check(EMBEDDINGS_DIR / f"{entity.id}.npy"), np.array(embedding))

        return embedding

    def _build_classification_prompt(self, ticket: Ticket) -> str:
        return f"""Classify this IT support ticket:

Title: {ticket.title}
Category: {ticket.category}
Work Type: {ticket.work_type}
Summary: {getattr(ticket, 'description', 'N/A')}

Return JSON:
{{
  "categories": ["network", "hardware", ...],
  "confidence": 0.95,
  "reasoning": "Based on keywords..."
}}"""

    def _build_troubleshooting_prompt(self, ticket: Ticket, context: AIContextPack) -> str:
        return f"""Generate troubleshooting steps for this ticket:

Title: {ticket.title}
Category: {ticket.category}
Work Type: {ticket.work_type}
Asset: {context.asset.get('hostname') if context.asset else 'Unknown'}
OS: {context.asset.get('operating_system') if context.asset else 'Unknown'}

Similar tickets: {[s['id'] for s in context.similar_tickets]}

Return JSON:
{{
  "steps": ["Step 1", "Step 2", ...],
  "confidence": 0.9,
  "reasoning": "Based on similar incidents...",
  "evidence": ["TKT-2026-038", "KB-000001"]
}}"""

    def _build_summary_prompt(self, ticket: Ticket, context: AIContextPack) -> str:
        def get_worklog_field(w, field, default=''):
            val = getattr(w, field, None)
            if val is None and isinstance(w, dict):
                val = w.get(field, default)
            return val if val is not None else default
        
        worklog_summary = "\n".join([
            f"- {get_worklog_field(w, 'date')}: {get_worklog_field(w, 'mode')} {get_worklog_field(w, 'hours')}h - {get_worklog_field(w, 'description', '')[:100]}"
            for w in context.worklog
        ])
        return f"""Summarize this ticket resolution:

Title: {ticket.title}
Status: {ticket.status}
Work Log:
{worklog_summary}

Resolution: {ticket.resolution or 'Not yet resolved'}

Return JSON:
{{
  "summary": "Brief summary of issue and resolution"
}}"""

    def _context_to_text(self, context: AIContextPack) -> str:
        parts = []
        if context.ticket:
            parts.append(f"Ticket: {context.ticket['title']}")
            parts.append(f"Status: {context.ticket['status']}")
        if context.client:
            parts.append(f"Client: {context.client.get('display_name', context.client.get('company_name', ''))}")
        if context.asset:
            parts.append(f"Asset: {context.asset.get('hostname', '')} ({context.asset.get('operating_system', '')})")
        if context.worklog:
            parts.append("Work Log:")
            for w in context.worklog:
                parts.append(f"  {w['date']}: {w['mode']} {w['hours']}h - {w.get('description', '')}")
        return "\n".join(parts)

    def _call_ai_model(self, prompt: str, model: str) -> Dict[str, Any]:
        if "Classify" in prompt:
            return {
                "categories": ["network", "connectivity"],
                "confidence": 0.94,
                "reasoning": "Ticket mentions network, DHCP, connectivity"
            }
        elif "troubleshooting" in prompt.lower():
            return {
                "steps": [
                    "Test alternate switch port",
                    "Test alternate Ethernet cable",
                    "Check DHCP server logs",
                    "Verify VLAN configuration"
                ],
                "confidence": 0.91,
                "reasoning": "Pattern matches known switch port failures",
                "evidence": ["TKT-2026-038", "TKT-2026-041"]
            }
        elif "Summarize" in prompt:
            return {
                "summary": "Network connectivity issue resolved by moving workstation to alternate switch port. DHCP failure on primary port Gi1/0/14."
            }
        return {}

    def _generate_id(self, prefix: str) -> str:
        import glob
        files = glob.glob(str(AI_DIR / f"**/{prefix}-*.json"), recursive=True)
        max_num = 0
        for f in files:
            try:
                num = int(Path(f).stem.split("-")[-1])
                max_num = max(max_num, num)
            except:
                pass
        return f"{prefix}-{max_num + 1:06d}"

    def _emit_observation(
        self,
        kind: str,
        target: EntityRef,
        data: Dict[str, Any],
        confidence: float,
        model: str,
    ) -> None:
        obs = AIObservation(
            type="ai_observation",
            id=self._generate_id("AIO"),
            observation_id=self._generate_id("AIO"),
            title=f"AI {kind}: {target.id}",
            status="active",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            created_by="AI-OPENCODE",
            updated_by="AI-OPENCODE",
            observation={
                "kind": kind,
                "data": data,
                "confidence": confidence,
                "generated_at": datetime.now().isoformat(),
                "model": model,
            },
            target_entity=target,
        )
        safe_mkdir(OBSERVATIONS_DIR)
        safe_write(OBSERVATIONS_DIR / f"{obs.observation_id}.json",
                   json.dumps(obs.model_dump(by_alias=True), default=str, indent=2))

        if self.event_emitter:
            self.event_emitter.emit(
                event_type="ai.observation_created",
                entity=target,
                changes={"observation": EventChanges(old=None, new=kind)},
                actor=EventActor(id="AI-OPENCODE", type="ai", name="OpenCode"),
                reason=f"AI {kind} for {target.id}",
            )