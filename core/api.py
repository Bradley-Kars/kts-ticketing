"""KTS Core API — Main entry point for all clients (CLI, GUI, AI, Automation)"""

import os
import json
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta

from .models import (
    AnyEntity, EntityType, EntityRef, EventActor, EventEntity, EventChanges,
    Ticket, Client, Contact, Organization, Asset, User,
    Project, Task, Knowledge, Problem, Change, Request, Contract,
    WorklogEntry, Relationship, SearchQuery, SearchResult,
    get_model_for_type, validate_id, MODEL_MAP
)
from .services import (
    TicketService, ClientService, ContactService, OrganizationService,
    AssetService, UserService, ProjectService, TaskService,
    KnowledgeService, ProblemService, ChangeService, RequestService,
    ContractService, RelationshipService, SearchService, AIService
)
from .events import EventStore, EventEmitter
from .permissions import PermissionChecker
from .indexer import Indexer, VaultScanner
from .validator import Validator, ValidationReport
from .migrations import MigrationManager, create_migration_manager
from .paths import vault_root, home_root, enforce_isolation
from .safe_io import safe_mkdir, safe_write
from .counters import next_seq


class KTSAPI:
    """
    KTS Core API — Central service layer for all operations.
    
    This is the single authoritative interface for:
    - CLI (kts command)
    - GUI (Obsidian plugin / Tauri app)
    - AI agents (OpenCode, etc.)
    - Automation engine
    
    All mutations go through this API. Vault files are the source of truth.
    """

    def __init__(self, vault_path: Optional[str] = None):
        enforce_isolation()
        active_vault = vault_root()
        if vault_path is not None:
            given = Path(os.path.expanduser(vault_path)).resolve()
            if given != active_vault.resolve():
                raise SystemExit(
                    f"FATAL: requested vault {given} disagrees with the active "
                    f"vault {active_vault}. The active vault is fixed by "
                    "KTS_VAULT (sandbox) or the default location; --vault "
                    "cannot override it."
                )
        self.vault_path = active_vault
        self.system_path = home_root()

        # Core infrastructure
        self.event_store = EventStore()
        self.event_emitter = EventEmitter(self.event_store)
        self.permission_checker = PermissionChecker()

        # Services (initialized lazily)
        self._services: Dict[str, Any] = {}
        self._initialized = False

        # Auxiliary systems
        self.indexer = None
        self.validator = None
        self.migration_manager = None

    def initialize(self) -> None:
        """Initialize all services and auxiliary systems"""
        if self._initialized:
            return

        # Initialize services in dependency order
        self._services["ticket"] = TicketService(self.event_emitter, self.permission_checker)
        self._services["client"] = ClientService(self.event_emitter, self.permission_checker)
        self._services["contact"] = ContactService(self.event_emitter, self.permission_checker)
        self._services["organization"] = OrganizationService(self.event_emitter, self.permission_checker)
        self._services["asset"] = AssetService(self.event_emitter, self.permission_checker)
        self._services["user"] = UserService(self.event_emitter, self.permission_checker)
        self._services["project"] = ProjectService(self.event_emitter, self.permission_checker)
        self._services["task"] = TaskService(self.event_emitter, self.permission_checker)
        self._services["knowledge"] = KnowledgeService(self.event_emitter, self.permission_checker)
        self._services["problem"] = ProblemService(self.event_emitter, self.permission_checker)
        self._services["change"] = ChangeService(self.event_emitter, self.permission_checker)
        self._services["request"] = RequestService(self.event_emitter, self.permission_checker)
        self._services["contract"] = ContractService(self.event_emitter, self.permission_checker)

        # Cross-cutting services
        self._services["relationship"] = RelationshipService(self._services, self.event_emitter, self.permission_checker)
        self._services["search"] = SearchService(self._services, self.event_emitter)
        self._services["ai"] = AIService(self._services, self.event_emitter, self.permission_checker)

        # Auxiliary systems
        self.indexer = Indexer(self._services)
        self.validator = Validator(self._services)
        self.migration_manager = create_migration_manager(self._services)

        self._initialized = True

    def ensure_initialized(self):
        if not self._initialized:
            self.initialize()

    # =========================================================================
    # Service Access
    # =========================================================================

    def get_service(self, entity_type: EntityType):
        """Get service for entity type"""
        self.ensure_initialized()
        return self._services.get(entity_type)

    # =========================================================================
    # Ticket Operations
    # =========================================================================

    def create_ticket(self, data: Dict[str, Any], actor: EventActor) -> Ticket:
        """Create a new ticket"""
        self.ensure_initialized()
        svc = self._services["ticket"]
        # Generate ticket ID
        ticket_id = svc.next_id()
        data["id"] = ticket_id
        model = get_model_for_type("ticket")(**data)
        return svc.create(model, actor)

    def create_project(self, data: Dict[str, Any], actor: EventActor) -> Project:
        """Create a new project"""
        self.ensure_initialized()
        svc = self._services["project"]
        # Generate project ID
        project_id = svc.next_id()
        data["id"] = project_id
        model = get_model_for_type("project")(**data)
        return svc.create(model, actor)

    def create_task(self, data: Dict[str, Any], actor: EventActor) -> Task:
        """Create a new task (T5). Allocates the id via TaskService.next_id
        and writes via the real create path."""
        self.ensure_initialized()
        svc = self._services["task"]
        task_id = svc.next_id()
        data["id"] = task_id
        model = get_model_for_type("task")(**data)
        return svc.create(model, actor)

    def create_knowledge(self, data: Dict[str, Any], actor: EventActor) -> Knowledge:
        """Create a new knowledge article (T7). Allocates the id via
        KnowledgeService.next_id and writes via the real create path."""
        self.ensure_initialized()
        svc = self._services["knowledge"]
        knowledge_id = svc.next_id()
        data["id"] = knowledge_id
        model = get_model_for_type("knowledge")(**data)
        return svc.create(model, actor)

    def update_knowledge(
        self,
        entity_id: str,
        changes: Dict[str, Any],
        actor: EventActor,
        reason: Optional[str] = None,
        if_version: Optional[int] = None,
    ) -> Knowledge:
        """Update knowledge article fields (T7 updateKnowledge).

        Optimistic concurrency via if_version: when given, the current version
        must match or VersionMismatch is raised (mapped to 409 at the bridge).
        Delegates to KnowledgeService.update (fixed 2026-10-12: real
        EventChanges/EventEntity emit sites, version serialized to YAML,
        service-layer permission + if_version check).
        """
        self.ensure_initialized()
        return self._services["knowledge"].update(
            entity_id, changes, actor, reason, if_version=if_version
        )

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID"""
        self.ensure_initialized()
        return self._services["task"].get(task_id)

    def get_ticket(self, ticket_id: str) -> Optional[Ticket]:
        """Get ticket by ID"""
        self.ensure_initialized()
        return self._services["ticket"].get(ticket_id)

    def update_ticket(
        self,
        ticket_id: str,
        changes: Dict[str, Any],
        actor: EventActor,
        reason: Optional[str] = None,
        if_version: Optional[int] = None,
    ) -> Ticket:
        """Update ticket fields"""
        self.ensure_initialized()
        return self._services["ticket"].update(
            ticket_id, changes, actor, reason, if_version=if_version
        )

    def search_tickets(self, query: SearchQuery) -> List[SearchResult]:
        """Search tickets"""
        self.ensure_initialized()
        return self._services["search"].search(
            query=query.query,
            entity_types=query.entity_types,
            filters=query.filters,
            limit=query.limit,
        )

    def add_worklog(
        self,
        ticket_id: str,
        entry: WorklogEntry,
        actor: EventActor,
        reason: Optional[str] = None,
        if_version: Optional[int] = None,
    ) -> Ticket:
        """Add worklog entry to ticket"""
        self.ensure_initialized()
        return self._services["ticket"].add_worklog(
            ticket_id, entry, actor, reason, if_version=if_version
        )

    def remove_worklog(
        self,
        ticket_id: str,
        entry_index: int,
        actor: EventActor,
        reason: str,
        if_version: Optional[int] = None,
    ) -> Ticket:
        """Remove one worklog entry from a ticket.

        entry_index is the 0-based position in ticket.worklog (YAML order).
        reason is mandatory (billing audit trail). Emits ticket.worklog_removed.
        """
        self.ensure_initialized()
        return self._services["ticket"].remove_worklog(
            ticket_id, entry_index, actor, reason, if_version=if_version
        )

    def change_ticket_status(
        self,
        ticket_id: str,
        status: str,
        actor: EventActor,
        follow_up: Optional[datetime] = None,
        reason: Optional[str] = None,
        if_version: Optional[int] = None,
    ) -> Ticket:
        """Change ticket status"""
        self.ensure_initialized()
        return self._services["ticket"].change_status(
            ticket_id, status, actor, follow_up, reason, if_version=if_version
        )

    def user_exists(self, user_id: str) -> bool:
        """Check whether a user record with this ID exists (assigned_to validation)."""
        self.ensure_initialized()
        return self._services["user"].exists(user_id)

    def assign_ticket(
        self,
        ticket_id: str,
        user_id: str,
        actor: EventActor,
        reason: Optional[str] = None,
    ) -> Ticket:
        """Assign ticket to user"""
        self.ensure_initialized()
        return self.update_ticket(ticket_id, {"assigned_to": user_id}, actor, reason)

    # =========================================================================
    # Generic Entity Operations
    # =========================================================================

    def create(self, entity_type: EntityType, data: Dict[str, Any], actor: EventActor) -> AnyEntity:
        """Create any entity"""
        self.ensure_initialized()
        svc = self._services[entity_type]
        model_class = get_model_for_type(entity_type)
        model = model_class(**data)
        return svc.create(model, actor)

    def get(self, entity_type: EntityType, entity_id: str) -> Optional[AnyEntity]:
        """Get any entity by ID"""
        self.ensure_initialized()
        return self._services[entity_type].get(entity_id)

    def update(
        self,
        entity_type: EntityType,
        entity_id: str,
        changes: Dict[str, Any],
        actor: EventActor,
        reason: Optional[str] = None,
    ) -> AnyEntity:
        """Update any entity"""
        self.ensure_initialized()
        return self._services[entity_type].update(entity_id, changes, actor, reason)

    def delete(self, entity_type: EntityType, entity_id: str, actor: EventActor, reason: Optional[str] = None) -> bool:
        """Delete (archive) any entity"""
        self.ensure_initialized()
        return self._services[entity_type].delete(entity_id, actor, reason)

    # =========================================================================
    # Relationships
    # =========================================================================

    def link(
        self,
        source: EntityRef,
        target: EntityRef,
        relationship_type: str,
        actor: EventActor,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Create bidirectional relationship"""
        self.ensure_initialized()
        return self._services["relationship"].link(source, target, relationship_type, actor, metadata)

    def unlink(
        self,
        source: EntityRef,
        target: EntityRef,
        relationship_type: str,
        actor: EventActor,
    ) -> bool:
        """Remove bidirectional relationship"""
        self.ensure_initialized()
        return self._services["relationship"].unlink(source, target, relationship_type, actor)

    def get_related(self, entity: EntityRef, relationship_types: Optional[List[str]] = None) -> List[EntityRef]:
        """Get related entities"""
        self.ensure_initialized()
        return self._services["relationship"].get_related(entity, relationship_types)

    # =========================================================================
    # Events
    # =========================================================================

    def get_events(
        self,
        entity: EntityRef,
        limit: int = 100,
        event_type: Optional[str] = None,
        actor_id: Optional[str] = None,
    ) -> List[Any]:
        """Get event history for entity"""
        self.ensure_initialized()
        return self.event_store.get_for_entity(entity, limit)

    def stream_events(self, filter: Dict[str, Any]) -> Any:
        """Stream events matching filter"""
        self.ensure_initialized()
        return self.event_emitter.subscribe(filter)

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
        """Emit an event to the event store"""
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

    # =========================================================================
    # AI Operations
    # =========================================================================

    def get_ai_context(self, entity: EntityRef) -> Any:
        """Get AI context pack for entity"""
        self.ensure_initialized()
        return self._services["ai"].get_context_pack(entity)

    def submit_ai_suggestion(self, suggestion: Any) -> Any:
        """Persist an AI suggestion for human approval (append-only).

        The suggestion is written to the AI suggestions directory once;
        an existing suggestion_id is never overwritten.
        """
        from .services.ai_service import SUGGESTIONS_DIR

        self.ensure_initialized()
        if hasattr(suggestion, "model_dump"):
            data = suggestion.model_dump(by_alias=True)
            sid = getattr(suggestion, "suggestion_id", None) or data.get("suggestion_id")
        elif isinstance(suggestion, dict):
            data = suggestion
            sid = suggestion.get("suggestion_id") or suggestion.get("id")
        else:
            raise TypeError("suggestion must be an AISuggestion or dict")
        if not sid:
            raise ValueError("suggestion has no suggestion_id")
        path = SUGGESTIONS_DIR / f"{sid}.json"
        if path.exists():
            raise FileExistsError(
                f"Suggestion {sid} already exists; suggestions are append-only"
            )
        safe_mkdir(SUGGESTIONS_DIR)
        safe_write(path, json.dumps(data, indent=2, default=str))
        return suggestion

    def resolve_ai_suggestion(
        self,
        suggestion_id: str,
        action: str,
        actor: EventActor,
        edited_value: Optional[Any] = None,
    ) -> Any:
        """Resolve AI suggestion (accept/reject/edit)"""
        self.ensure_initialized()
        return self._services["ai"].resolve_suggestion(suggestion_id, action, actor, edited_value)

    # =========================================================================
    # Indexing & Validation
    # =========================================================================

    def rebuild_indexes(self) -> Dict[str, Any]:
        """Rebuild all search indexes from vault"""
        self.ensure_initialized()
        return self.indexer.rebuild_all()

    def validate(self, fix: bool = False) -> ValidationReport:
        """Validate vault integrity"""
        self.ensure_initialized()
        return self.validator.validate_all(fix=fix)

    # =========================================================================
    # Migrations
    # =========================================================================

    def migration_status(self) -> Dict[str, Any]:
        """Get migration status"""
        self.ensure_initialized()
        return self.migration_manager.status()

    def run_migration(self, version: str, force: bool = False) -> Any:
        """Run a migration"""
        self.ensure_initialized()
        return self.migration_manager.apply(version, force)

    def rollback_migration(self, version: str) -> Any:
        """Rollback a migration"""
        self.ensure_initialized()
        return self.migration_manager.rollback(version)

    # =========================================================================
    # Reporting
    # =========================================================================

    def generate_report(self, report_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Generate report (placeholder)"""
        self.ensure_initialized()
        # Would implement various reports
        return {"report_type": report_type, "params": params, "data": []}

    # =========================================================================
    # Invoice Operations
    # =========================================================================

    def generate_invoice(self, ticket_id: str, actor: EventActor) -> Dict[str, Any]:
        """Generate invoice from ticket"""
        self.ensure_initialized()
        svc = self._services["ticket"]
        ticket = svc.get(ticket_id)
        if not ticket:
            raise ValueError(f"Ticket {ticket_id} not found")

        # Build invoice from ticket and worklog
        worklog = ticket.worklog or []
        items = []
        for entry in worklog:
            items.append({
                "description": entry.description,
                "qty": entry.hours,
                "unit_price": entry.amount / entry.hours if entry.hours > 0 else 0,
                "total": entry.amount
            })

        subtotal = sum(item["total"] for item in items)
        tax_rate = 0  # Could be configurable per client
        tax = subtotal * tax_rate
        discount = 0
        total = subtotal + tax - discount

        # Year-scoped invoice number (Option B, Bradley 2026-10-08):
        # persisted counter, never reuses a number even if the invoice
        # file was deleted. Legacy INV-YYYYMMDD-### files are
        # grandfathered — the INV-{year}-* glob never matches them.
        inv_year = datetime.now().strftime("%Y")
        scan_max = 0
        inv_dir = self.vault_path / "Invoices"
        if inv_dir.exists():
            for f in inv_dir.glob(f"INV-{inv_year}-*.json"):
                try:
                    scan_max = max(scan_max, int(f.stem.split("-")[-1]))
                except (ValueError, IndexError):
                    pass
        invoice_number = f"INV-{inv_year}-{next_seq(f'invoice:{inv_year}', scan_max):03d}"

        invoice = {
            "ticket_id": ticket_id,
            "company": {
                "name": "Kars Tech Solutions",
                "address": "501 Maple ST, Brainerd MN 56401",
                "phone": "+1 (218) 656-0574",
                "email": "Bradley.Kars@KarsTechSolutions.com"
            },
            "client": {
                "name": ticket.client_name or "Unknown",
                "address": "",
                "email": ""
            },
            "invoice": {
                "number": invoice_number,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "due_date": (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"),
                "terms": "Due Upon Receipt",
                "po_number": "",
                "tax_rate": 0,
                "items": items,
                "subtotal": subtotal,
                "discount": 0,
                "tax": 0,
                "total": total,
                "notes": ticket.resolution or "",
                "payment_info": ""
            }
        }

        # Save invoice to vault
        invoice_path = self.vault_path / "Invoices" / f"{invoice['invoice']['number']}.json"
        safe_mkdir(invoice_path.parent)
        safe_write(invoice_path, json.dumps(invoice, indent=2))

        # Emit event
        self._emit_event(
            event_type="invoice.generated",
            entity=EntityRef(type="ticket", id=ticket_id),
            changes={"invoice": EventChanges(old=None, new=invoice["invoice"]["number"])},
            actor=actor,
            reason=f"Invoice generated for ticket {ticket_id}"
        )

        return invoice

    def get_invoice(self, invoice_number: str) -> Optional[Dict[str, Any]]:
        """Get invoice by number"""
        self.ensure_initialized()
        invoice_path = self.vault_path / "Invoices" / f"{invoice_number}.json"
        if not invoice_path.exists():
            return None
        import json
        return json.loads(invoice_path.read_text())

    def list_invoices(self) -> List[Dict[str, Any]]:
        """List all invoices"""
        self.ensure_initialized()
        invoices_dir = self.vault_path / "Invoices"
        if not invoices_dir.exists():
            return []
        invoices = []
        for f in invoices_dir.glob("*.json"):
            import json
            invoices.append(json.loads(f.read_text()))
        return invoices

    def create_receipt(self, ticket_id: str, payment_data: Dict[str, Any], actor: EventActor) -> Dict[str, Any]:
        """Create receipt for payment"""
        self.ensure_initialized()
        ticket = self._services["ticket"].get(ticket_id)
        if not ticket:
            raise ValueError(f"Ticket {ticket_id} not found")

        # Year-scoped receipt number (Option B, Bradley 2026-10-08):
        # persisted counter via next_seq, never reuses; legacy
        # RCP-YYYYMMDD-### files are grandfathered (the RCP-{year}-*
        # glob never matches them). Replaces the old timestamp-suffix
        # scheme, which had no counter at all.
        rcp_year = datetime.now().strftime("%Y")
        rcp_scan = 0
        rcp_dir = self.vault_path / "Receipts"
        if rcp_dir.exists():
            for f in rcp_dir.glob(f"RCP-{rcp_year}-*.json"):
                try:
                    rcp_scan = max(rcp_scan, int(f.stem.split("-")[-1]))
                except (ValueError, IndexError):
                    pass
        receipt_number = f"RCP-{rcp_year}-{next_seq(f'receipt:{rcp_year}', rcp_scan):03d}"

        receipt = {
            "company": {
                "name": "Kars Tech Solutions",
                "address": "501 Maple ST, Brainerd MN 56401",
                "phone": "+1 (218) 656-0574",
                "email": "Bradley.Kars@KarsTechSolutions.com"
            },
            "client": {
                "name": ticket.client_name,
                "address": "",
                "email": ""
            },
            "receipt": {
                "number": receipt_number,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "invoice_number": f"INV-{ticket_id.split('-')[-1]}",
                "payment_method": payment_data.get("method", "unknown"),
                "amount": payment_data["amount"],
                "items": [{
                    "description": f"Payment for {ticket.title}",
                    "amount": payment_data["amount"]
                }],
                "total": payment_data["amount"],
                "notes": payment_data.get("notes", f"Payment received for ticket {ticket_id}")
            }
        }

        # Save receipt
        receipt_path = self.vault_path / "Receipts" / f"{receipt['receipt']['number']}.json"
        safe_mkdir(receipt_path.parent)
        safe_write(receipt_path, json.dumps(receipt, indent=2))

        # Emit event
        self._emit_event(
            event_type="receipt.created",
            entity=EntityRef(type="ticket", id=ticket_id),
            changes={"receipt": EventChanges(old=None, new=receipt["receipt"]["number"])},
            actor=actor,
            reason=f"Receipt generated for ticket {ticket_id}"
        )

        return receipt

    def record_payment(self, ticket_id: str, payment_data: Dict[str, Any], actor: EventActor) -> Dict[str, Any]:
        """Record payment and generate receipt"""
        self.ensure_initialized()

        # Add worklog entry for payment
        from .models import WorklogEntry
        from datetime import datetime

        entry = WorklogEntry(
            date=datetime.now().strftime("%Y-%m-%d"),
            mode="payment",
            hours=0,
            amount=payment_data["amount"],
            payment=payment_data.get("payment_method", "paid-card"),
            description=f"Payment received: {payment_data.get('method', 'unknown')}{payment_data.get('notes', '')}"
        )

        ticket_svc = self._services["ticket"]
        ticket_svc.add_worklog(ticket_id, entry, actor, f"Payment received: {payment_data.get('method', 'unknown')}")

        # Generate receipt
        receipt = self.create_receipt(ticket_id, {
            "method": payment_data.get("method", "unknown"),
            "amount": payment_data["amount"],
            "notes": payment_data.get("notes", "")
        }, actor)

        return receipt

    def create_payment_link(self, ticket_id: str, actor: EventActor) -> str:
        """Create Stripe payment link for ticket"""
        # This would integrate with Stripe to create a payment link
        # For now, return a placeholder
        return f"https://pay.kars-tech.com/invoice/{ticket_id}"

    # =========================================================================
    # Stripe Operations
    # =========================================================================

    def create_payment_intent(self, ticket_id: str, actor: EventActor) -> Dict[str, Any]:
        """Create a Stripe PaymentIntent for a ticket"""
        self.ensure_initialized()
        ticket = self._services["ticket"].get(ticket_id)
        if not ticket:
            raise ValueError(f"Ticket {ticket_id} not found")
        
        # Generate invoice first if needed
        invoice = self.generate_invoice(ticket_id, actor)
        
        # Create Stripe PaymentIntent
        # This would integrate with actual Stripe API
        # For now, return the payment link placeholder
        return {
            "payment_intent_id": f"pi_{ticket_id}_{int(datetime.now().timestamp())}",
            "client_secret": f"pi_{ticket_id}_secret_{int(datetime.now().timestamp())}",
            "amount": invoice["invoice"]["total"],
            "currency": "usd",
            "payment_url": f"https://pay.kars-tech.com/invoice/{ticket_id}",
            "status": "requires_payment_method"
        }

    def create_stripe_customer(self, ticket_id: str, actor: EventActor) -> Dict[str, Any]:
        """Create or retrieve Stripe customer for ticket"""
        self.ensure_initialized()
        ticket = self._services["ticket"].get(ticket_id)
        if not ticket:
            raise ValueError(f"Ticket {ticket_id} not found")
        
        # Create or retrieve Stripe customer
        # This would use Stripe API
        # Try to get email from contact or client
        email = ""
        if ticket.contact_id:
            contact = self._services["contact"].get(ticket.contact_id)
            if contact and contact.email:
                email = contact.email[0] if contact.email else ""
        if not email and ticket.client_name:
            # Try to find client by name
            clients = self._services["client"].list()
            for c in clients:
                # Check business client (company_name) or residential (display_name)
                if c.company_name == ticket.client_name or c.display_name == ticket.client_name:
                    if c.email:
                        email = c.email[0]
                        break
        
        return {
            "customer_id": f"cus_{ticket_id}_{int(datetime.now().timestamp())}",
            "email": email or "",
            "name": ticket.client_name or "Unknown"
        }

    def create_checkout_session(self, ticket_id: str, actor: EventActor, success_url: str, cancel_url: str) -> Dict[str, Any]:
        """Create Stripe Checkout Session for ticket payment"""
        self.ensure_initialized()
        ticket = self._services["ticket"].get(ticket_id)
        if not ticket:
            raise ValueError(f"Ticket {ticket_id} not found")
        
        invoice = self.generate_invoice(ticket_id, actor)
        
        # Create Stripe Checkout Session
        # This would use Stripe API
        return {
            "session_id": f"cs_{ticket_id}_{int(datetime.now().timestamp())}",
            "url": f"https://checkout.stripe.com/pay/cs_{ticket_id}_{int(datetime.now().timestamp())}",
            "amount": invoice["invoice"]["total"],
            "currency": "usd",
            "status": "open",
            "expires_at": (datetime.now() + timedelta(minutes=30)).isoformat()
        }

    def handle_stripe_webhook(self, payload: bytes, signature: str) -> Dict[str, Any]:
        """Handle Stripe webhook events"""
        self.ensure_initialized()
        # This would verify the webhook signature and process events
        # For now, return placeholder
        return {
            "received": True,
            "event_type": "payment_intent.succeeded",
            "processed": True
        }

    # Singleton instance for convenience
_api_instance: Optional[KTSAPI] = None


def get_api(vault_path: Optional[str] = None) -> KTSAPI:
    """Get or create singleton KTSAPI instance"""
    global _api_instance
    if _api_instance is None:
        _api_instance = KTSAPI(vault_path)
    return _api_instance


# Convenience functions for direct use
def create_ticket(data: Dict[str, Any], actor: EventActor) -> Ticket:
    return get_api().create_ticket(data, actor)


def get_ticket(ticket_id: str) -> Optional[Ticket]:
    return get_api().get_ticket(ticket_id)


def update_ticket(ticket_id: str, changes: Dict[str, Any], actor: EventActor, reason: Optional[str] = None) -> Ticket:
    return get_api().update_ticket(ticket_id, changes, actor, reason)


def generate_invoice(ticket_id: str, actor: EventActor) -> Dict[str, Any]:
    return get_api().generate_invoice(ticket_id, actor)


def create_receipt(ticket_id: str, payment_data: Dict[str, Any], actor: EventActor) -> Dict[str, Any]:
    return get_api().create_receipt(ticket_id, payment_data, actor)


def record_payment(ticket_id: str, payment_data: Dict[str, Any], actor: EventActor) -> Dict[str, Any]:
    return get_api().record_payment(ticket_id, payment_data, actor)


def create_payment_link(ticket_id: str, actor: EventActor) -> str:
    return get_api().create_payment_link(ticket_id, actor)