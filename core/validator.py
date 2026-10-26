"""Validator — Check vault integrity and consistency"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Set
from dataclasses import dataclass, field

from .models import (
    AnyEntity, EntityType, MODEL_MAP, get_model_for_type,
    validate_id, ID_PATTERNS, RELATIONSHIP_INVERSE,
    validate_document_id
)
from .services.ticket_service import BaseService
from .paths import vault_root, indexes_dir


VAULT_ROOT = vault_root()
INDEX_DIR = indexes_dir()


def count_prose_worklog_entries(content: str) -> int:
    """Count worklog entry headings in the Work Log section (Item V fix).

    The old formula content.count("### ") - 1 subtracted a "header" that
    does not exist: the section header is the h2 "## Work Log", which never
    matches "### ", so every ticket reported prose one less than YAML.
    Stray h3s outside the section were counted too. Count only ### entry
    headings from the Work Log header until the next h2.
    """
    count = 0
    in_section = False
    for line in content.splitlines():
        if in_section:
            if line.startswith("## "):
                break
            if line.startswith("### "):
                count += 1
        elif line.startswith("## Work Log") or line.startswith("## Worklog"):
            in_section = True
    return count


@dataclass
class ValidationIssue:
    severity: str  # "error" | "warning" | "info"
    category: str  # "id_format", "referential", "schema", "consistency", "worklog", "sla"
    entity_type: str
    entity_id: str
    message: str
    field: Optional[str] = None
    suggested_fix: Optional[str] = None


@dataclass
class ValidationReport:
    total_entities: int = 0
    errors: int = 0
    warnings: int = 0
    info: int = 0
    issues: List[ValidationIssue] = field(default_factory=list)
    checked_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def add_issue(self, issue: ValidationIssue):
        self.issues.append(issue)
        if issue.severity == "error":
            self.errors += 1
        elif issue.severity == "warning":
            self.warnings += 1
        else:
            self.info += 1

    def is_valid(self) -> bool:
        return self.errors == 0


class Validator:
    """Validate vault integrity against schemas and cross-references"""

    def __init__(self, services: Dict[str, Any]):
        self.services = services

    def validate_all(self, fix: bool = False) -> ValidationReport:
        """Run all validation checks"""
        report = ValidationReport()

        # Load all entities
        entities_by_type = self._load_all_entities()

        report.total_entities = sum(len(v) for v in entities_by_type.values())

        # Run checks
        self._check_id_formats(entities_by_type, report)
        self._check_referential_integrity(entities_by_type, report)
        self._check_schema_compliance(entities_by_type, report)
        self._check_worklog_consistency(entities_by_type, report)
        self._check_sla_consistency(entities_by_type, report)
        self._check_relationship_symmetry(entities_by_type, report)
        self._check_status_workflow(entities_by_type, report)
        self._check_duplicate_ids(entities_by_type, report)
        self._check_documents(report)

        # Apply fixes if requested
        if fix and report.errors > 0:
            self._apply_fixes(entities_by_type, report)

        return report

    def _load_all_entities(self) -> Dict[EntityType, List[AnyEntity]]:
        """Load all entities from services"""
        entities = {}
        for entity_type, svc in self.services.items():
            if entity_type in ("event", "ai_observation", "ai_suggestion", "ai_action"):
                continue
            try:
                entities[entity_type] = svc.list(limit=10000)
            except Exception:
                entities[entity_type] = []
        return entities

    def _check_id_formats(self, entities: Dict, report: ValidationReport):
        """Validate all entity IDs match expected patterns"""
        for entity_type, entity_list in entities.items():
            prefix = self._get_id_prefix(entity_type)
            pattern = ID_PATTERNS.get(prefix)
            if not pattern:
                continue
            import re
            regex = re.compile(pattern)
            for entity in entity_list:
                if not regex.match(entity.id):
                    report.add_issue(ValidationIssue(
                        severity="error",
                        category="id_format",
                        entity_type=entity_type,
                        entity_id=entity.id,
                        message=f"ID {entity.id} does not match expected pattern for {prefix}",
                        field="id",
                        suggested_fix=f"Regenerate ID with correct format",
                    ))

    def _check_referential_integrity(self, entities: Dict, report: ValidationReport):
        """Check all references point to existing entities"""
        # Build ID sets by type
        id_sets = {}
        for entity_type, entity_list in entities.items():
            id_sets[entity_type] = {e.id for e in entity_list}

        # Check references
        ref_fields = {
            "ticket": [
                ("organization_id", "organization"),
                ("contact_id", "contact"),
                ("client_id", "client"),
                ("asset_id", "asset"),
                ("parent_ticket_id", "ticket"),
                ("root_cause_id", "problem"),
                ("assigned_to", "user"),
            ],
            "client": [
                ("organization_id", "organization"),
            ],
            "contact": [
                ("organization_id", "organization_or_client"),
            ],
            "asset": [
                ("organization_id", "organization"),
                ("assigned_to", "client"),
            ],
            "problem": [
                ("organization_id", "organization"),
            ],
            "change": [
                ("organization_id", "organization"),
                ("requester", "user"),
                ("approver", "user"),
                ("implementer", "user"),
                ("related_problem", "problem"),
            ],
            "request": [
                ("requester", "contact"),
                ("organization_id", "organization"),
                ("approver", "user"),
                ("fulfiller", "user"),
            ],
            "contract": [
                ("organization_id", "organization"),
            ],
            "project": [
                ("organization_id", "organization"),
                ("project_manager", "user"),
                ("sponsor", "client"),
            ],
            "task": [
                ("project_id", "project"),
                ("ticket_id", "ticket"),
                ("assignee", "user"),
            ],
        }

        for entity_type, fields in ref_fields.items():
            entity_list = entities.get(entity_type, [])
            for entity in entity_list:
                for field_name, target_type in fields:
                    ref_id = getattr(entity, field_name, None)
                    if not ref_id:
                        continue
                    
                    # Special handling for organization_or_client (contacts can link to either orgs or clients)
                    if target_type == "organization_or_client":
                        found = False
                        if "organization" in id_sets and ref_id in id_sets["organization"]:
                            found = True
                        if "client" in id_sets and ref_id in id_sets["client"]:
                            found = True
                        if not found:
                            report.add_issue(ValidationIssue(
                                severity="error",
                                category="referential",
                                entity_type=entity_type,
                                entity_id=entity.id,
                                message=f"Reference {field_name}={ref_id} points to non-existent organization or client",
                                field=field_name,
                                suggested_fix=f"Create organization/client {ref_id} or remove reference",
                            ))
                    elif target_type in id_sets:
                        if ref_id not in id_sets[target_type]:
                            report.add_issue(ValidationIssue(
                                severity="error",
                                category="referential",
                                entity_type=entity_type,
                                entity_id=entity.id,
                                message=f"Reference {field_name}={ref_id} points to non-existent {target_type}",
                                field=field_name,
                                suggested_fix=f"Create {target_type} {ref_id} or remove reference",
                            ))

    def _check_schema_compliance(self, entities: Dict, report: ValidationReport):
        """Validate each entity against its Pydantic schema"""
        for entity_type, entity_list in entities.items():
            model_class = MODEL_MAP.get(entity_type)
            if not model_class:
                continue

            for entity in entity_list:
                try:
                    model_class(**entity.model_dump())
                except Exception as e:
                    report.add_issue(ValidationIssue(
                        severity="error",
                        category="schema",
                        entity_type=entity_type,
                        entity_id=entity.id,
                        message=f"Schema validation failed: {e}",
                        suggested_fix="Fix frontmatter to match schema",
                    ))

    def _check_worklog_consistency(self, entities: Dict, report: ValidationReport):
        """Check ticket worklog YAML matches prose entries (Item V: scoped count)"""
        tickets = entities.get("ticket", [])
        for ticket in tickets:
            if not ticket.worklog:
                continue

            # Load file and check prose entries
            file_path = self._find_ticket_file(ticket.id)
            if not file_path:
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
                prose_count = count_prose_worklog_entries(content)  # Item V fix: Work Log section only
                yaml_count = len(ticket.worklog)

                if prose_count != yaml_count:
                    report.add_issue(ValidationIssue(
                        severity="warning",
                        category="worklog",
                        entity_type="ticket",
                        entity_id=ticket.id,
                        message=f"Worklog count mismatch: YAML={yaml_count}, prose={prose_count}",
                        field="worklog",
                        suggested_fix="Run `ticket refresh` or manually sync YAML and prose",
                    ))
            except Exception:
                pass

    def _check_sla_consistency(self, entities: Dict, report: ValidationReport):
        """Check SLA fields are consistent"""
        tickets = entities.get("ticket", [])
        for ticket in tickets:
            if ticket.sla_response_due and ticket.sla_resolution_due:
                if ticket.sla_response_due > ticket.sla_resolution_due:
                    report.add_issue(ValidationIssue(
                        severity="warning",
                        category="sla",
                        entity_type="ticket",
                        entity_id=ticket.id,
                        message="SLA response due is after resolution due",
                        field="sla_response_due",
                        suggested_fix="Adjust SLA targets so response <= resolution",
                    ))

    def _check_relationship_symmetry(self, entities: Dict, report: ValidationReport):
        """Check bidirectional relationships are consistent"""
        for entity_type, entity_list in entities.items():
            for entity in entity_list:
                for rel in entity.relationships:
                    # Find target entity
                    target_svc = self.services.get(self._prefix_to_type(rel.target))
                    if not target_svc:
                        continue
                    target = target_svc.get(rel.target)
                    if not target:
                        report.add_issue(ValidationIssue(
                            severity="warning",
                            category="referential",
                            entity_type=entity_type,
                            entity_id=entity.id,
                            message=f"Relationship target {rel.target} not found",
                            field="relationships",
                        ))
                        continue

                    # Check inverse exists
                    inverse_type = RELATIONSHIP_INVERSE.get(rel.type)
                    if not inverse_type:
                        continue

                    has_inverse = any(
                        r.target == entity.id and r.type == inverse_type
                        for r in target.relationships
                    )

                    if not has_inverse:
                        report.add_issue(ValidationIssue(
                            severity="warning",
                            category="referential",
                            entity_type=entity_type,
                            entity_id=entity.id,
                            message=f"Missing inverse relationship: {rel.type} -> {inverse_type} on {rel.target}",
                            field="relationships",
                            suggested_fix=f"Run relationship sync or manually add inverse",
                        ))

    def _check_status_workflow(self, entities: Dict, report: ValidationReport):
        """Validate status transitions follow workflow"""
        # Would load from statuses.yaml in production
        valid_transitions = {
            "new": ["triaged", "cancelled"],
            "triaged": ["in-progress", "waiting", "cancelled", "new"],
            "in-progress": ["waiting", "resolved", "triaged", "cancelled"],
            "waiting": ["in-progress", "resolved", "cancelled"],
            "resolved": ["closed", "in-progress", "waiting", "cancelled"],
            "closed": [],
            "cancelled": [],
        }

        tickets = entities.get("ticket", [])
        for ticket in tickets:
            # This would need history to validate transitions
            # For now, just check status is valid
            if ticket.status not in valid_transitions:
                report.add_issue(ValidationIssue(
                    severity="error",
                    category="workflow",
                    entity_type="ticket",
                    entity_id=ticket.id,
                    message=f"Invalid status: {ticket.status}",
                    field="status",
                    suggested_fix=f"Set to one of: {list(valid_transitions.keys())}",
                ))

    def _check_duplicate_ids(self, entities: Dict, report: ValidationReport):
        """Check for duplicate IDs across all entities"""
        seen = {}
        for entity_type, entity_list in entities.items():
            for entity in entity_list:
                if entity.id in seen:
                    report.add_issue(ValidationIssue(
                        severity="error",
                        category="id_format",
                        entity_type=entity_type,
                        entity_id=entity.id,
                        message=f"Duplicate ID: also used by {seen[entity.id]}",
                        field="id",
                        suggested_fix="Regenerate ID for one of the entities",
                    ))
                else:
                    seen[entity.id] = entity_type

    def _check_documents(self, report: ValidationReport, root=None):
        """Invoice/receipt files: filename == inner number, format accepted.

        Option B (Bradley 2026-10-08): BOTH legacy INV-YYYYMMDD-### and
        year-scoped INV-YYYY-### validate (same for RCP) - grandfathered
        documents must never break validation. Documents are not
        entities: findings land in errors/warnings only, total_entities
        is unchanged.
        """
        base = root if root is not None else VAULT_ROOT
        for kind, folder, field_name in (("INV", "Invoices", "invoice"),
                                         ("RCP", "Receipts", "receipt")):
            d = base / folder
            if not d.exists():
                continue
            for f in sorted(d.glob("*.json")):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    report.add_issue(ValidationIssue(
                        severity="error", category="document",
                        entity_type="document", entity_id=f.stem,
                        message=f"{folder}/{f.name}: unreadable JSON",
                        field="number"))
                    continue
                number = (data.get(field_name) or {}).get("number")
                if not number:
                    report.add_issue(ValidationIssue(
                        severity="error", category="document",
                        entity_type="document", entity_id=f.stem,
                        message=f"{folder}/{f.name}: missing {field_name}.number",
                        field="number"))
                elif number != f.stem:
                    report.add_issue(ValidationIssue(
                        severity="error", category="document",
                        entity_type="document", entity_id=f.stem,
                        message=f"{folder}/{f.name}: filename does not match "
                                f"inner number {number}",
                        field="number"))
                elif not validate_document_id(kind, number):
                    report.add_issue(ValidationIssue(
                        severity="error", category="document",
                        entity_type="document", entity_id=f.stem,
                        message=f"{folder}/{f.name}: {number} matches neither "
                                f"legacy nor year-scoped format",
                        field="number",
                        suggested_fix="Regenerate the number via KTSAPI "
                                      "(core/counters.py)"))
                if kind == "INV" and number and validate_document_id(kind, number) and number == f.stem:
                    # ticket_id contract (Bradley 2026-10-10): invoices carry
                    # a stored ticket link; many-to-one allowed (a ticket may
                    # have several invoices; an invoice has exactly one ticket).
                    tid = data.get("ticket_id")
                    if not tid:
                        report.add_issue(ValidationIssue(
                            severity="error", category="document",
                            entity_type="document", entity_id=f.stem,
                            message=f"{folder}/{f.name}: missing ticket_id",
                            field="ticket_id"))
                    elif not re.match(r"^TKT-\d{4}-\d{3}$", str(tid)):
                        report.add_issue(ValidationIssue(
                            severity="error", category="document",
                            entity_type="document", entity_id=f.stem,
                            message=f"{folder}/{f.name}: ticket_id {tid} is not "
                                    f"a TKT-YYYY-NNN id",
                            field="ticket_id"))
                    elif not list((base / "Tickets").glob(f"{tid}*.md")):
                        # ticket files may have full titles (TKT-2026-001 - Title.md)
                        # match by prefix
                        report.add_issue(ValidationIssue(
                            severity="error", category="document",
                            entity_type="document", entity_id=f.stem,
                            message=f"{folder}/{f.name}: ticket_id {tid} has no "
                                    f"ticket file under Tickets/",
                            field="ticket_id"))

    def _apply_fixes(self, entities: Dict, report: ValidationReport):
        """Apply automatic fixes for certain issues (placeholder)"""
        # Would implement specific fixes
        pass

    def _find_ticket_file(self, ticket_id: str) -> Optional[Path]:
        tickets_dir = VAULT_ROOT / "Tickets"
        files = list(tickets_dir.glob(f"{ticket_id} - *.md"))
        return files[0] if files else None

    def _get_id_prefix(self, entity_type: EntityType) -> str:
        prefixes = {
            "ticket": "TKT",
            "client": "CUS",
            "contact": "CON",
            "organization": "ORG",
            "asset": "AST",
            "user": "USR",
            "project": "PRJ",
            "task": "TSK",
            "knowledge": "KB",
            "problem": "PRB",
            "change": "CHG",
            "request": "REQ",
            "contract": "CRT",
        }
        return prefixes.get(entity_type, "ENT")

    def _prefix_to_type(self, prefix: str) -> Optional[str]:
        prefix_map = {
            "TKT": "ticket", "TCK": "ticket",
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