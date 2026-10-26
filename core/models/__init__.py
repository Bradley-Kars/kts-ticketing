"""KTS Core Models — Pydantic models for all entities"""

from typing import Optional, List, Dict, Any, Literal, Union
from pydantic import BaseModel, Field, EmailStr, validator
from datetime import datetime
import re

# =============================================================================
# Base Models
# =============================================================================

class ActorRef(BaseModel):
    """Reference to an actor (user, AI, system, automation)"""
    id: str = Field(..., pattern=r"^(USR|AI|SYS|AUT)-[A-Z0-9]{6,}$")
    type: Literal["human", "ai", "system", "automation"]
    name: Optional[str] = None
    email: Optional[EmailStr] = None


class EntityRef(BaseModel):
    """Reference to any entity"""
    type: Literal[
        "ticket", "client", "contact", "organization", "asset", "user",
        "project", "task", "knowledge", "problem", "change", "request", "contract"
    ]
    id: str = Field(..., pattern=r"^(TKT-\d{4}-\d{3}|[A-Z]{3,4}-\d{6,}|PRJ-\d{4}-\d{6}|TSK-\d{4}-\d{6}|KB-\d{6,})$")


class Relationship(BaseModel):
    """Relationship between entities"""
    type: Literal[
        "parent_of", "child_of", "related", "duplicate_of", "duplicated_by",
        "caused_by", "causes", "resolves", "resolved_by", "affected_asset",
        "affects", "affected_customer", "affected_organization", "reported_by",
        "reported", "assigned_to", "assigned", "documents_resolution",
        "resolution_documented_in", "blocks", "blocked_by", "depends_on", "dependency_of"
    ]
    target: str = Field(..., pattern=r"^(TKT-\d{4}-\d{3}|[A-Z]{3,4}-\d{6,}|PRJ-\d{4}-\d{6}|TSK-\d{4}-\d{6}|KB-\d{6,})$")
    metadata: Optional[Dict[str, Any]] = None


class AIMetadata(BaseModel):
    """Minimal AI metadata in authoritative records"""
    last_summary_at: Optional[datetime] = None
    last_classification_at: Optional[datetime] = None
    pending_suggestions: List[str] = Field(default_factory=list)
    confidence_score: Optional[float] = Field(None, ge=0, le=1)


class BaseRecord(BaseModel):
    """Universal base record — all entities extend this"""
    id: str = Field(..., pattern=r"^(TKT-\d{4}-\d{3}|[A-Z]{3,4}-\d{6,}|PRJ-\d{4}-\d{6}|TSK-\d{4}-\d{6}|KB-\d{6,})$")
    type: Literal[
        "ticket", "client", "contact", "organization", "asset", "user",
        "project", "task", "knowledge", "problem", "change", "request", "contract",
        "event", "ai_observation", "ai_suggestion", "ai_action"
    ]
    title: str = Field(..., min_length=1, max_length=200)
    status: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    created_by: Optional[str] = Field(default=None, pattern=r"^(USR|AI|SYS|AUT|legacy)-[A-Z0-9]{6,}$")
    updated_by: Optional[str] = Field(default=None, pattern=r"^(USR|AI|SYS|AUT|legacy)-[A-Z0-9]{6,}$")
    tags: List[str] = Field(default_factory=list)
    relationships: List[Relationship] = Field(default_factory=list)
    ai: Optional[AIMetadata] = None
    version: int = Field(default=1, ge=1)

    @validator("id")
    def validate_id_format(cls, v):
        """Validate ID matches expected format for its type"""
        patterns = {
            "TKT": r"^TKT-\d{4}-\d{3}$",
            "TCK": r"^TCK-\d{6,}$",
            "CUS": r"^CUS-\d{6,}$",
            "CON": r"^CON-\d{6,}$",
            "ORG": r"^ORG-\d{6,}$",
            "AST": r"^AST-\d{6,}$",
            "USR": r"^USR-\d{6,}$",
"PRJ": r"^PRJ-\d{4}-\d{6}$",
"TSK": r"^TSK-\d{4}-\d{6}$",
            "KB":  r"^KB-\d{6,}$",
            "PRB": r"^PRB-\d{6,}$",
            "CHG": r"^CHG-\d{6,}$",
            "REQ": r"^REQ-\d{6,}$",
            "CRT": r"^CRT-\d{6,}$",
            "EVT": r"^EVT-\d{8,}$",
            "AIS": r"^AIS-\d{6,}$",
            "AIA": r"^AIA-\d{6,}$",
            "AIO": r"^AIO-\d{6,}$",
        }
        prefix = v.split("-")[0]
        if prefix in patterns:
            if not re.match(patterns[prefix], v):
                raise ValueError(f"ID {v} does not match pattern for {prefix}")
        return v

# =============================================================================
# Ticket Models
# =============================================================================

class WorklogEntry(BaseModel):
    date: datetime
    mode: Literal["onsite", "remote", "email", "phone", "web", "meeting", "research", "payment"]
    hours: float = Field(ge=0)
    amount: float = Field(ge=0)
    payment: Literal["paid-cash", "paid-card", "paid-check", "invoiced", "uninvoiced", "no-charge", "paid", "cancelled"]
    description: Optional[str] = None


class Ticket(BaseRecord):
    type: Literal["ticket"] = "ticket"
    ticket_type: Literal[
        "incident", "service_request", "problem", "change", "task",
        "maintenance", "project", "internal", "question"
    ] = "incident"
    status: Literal[
        "new", "triaged", "in-progress", "waiting", "resolved", "closed", "cancelled"
    ] = "new"
    priority: Literal["critical", "high", "medium", "low", "informational"] = "medium"
    impact: Literal["critical", "high", "medium", "low"] = "medium"
    urgency: Literal["critical", "high", "medium", "low"] = "medium"
    priority_override: bool = False
    override_reason: Optional[str] = None
    category: Literal[
        "support", "setup", "maintenance", "project", "billing",
        "follow-up", "security", "backup"
    ] = "support"
    subcategory: Optional[str] = None
    work_type: Literal[
        "virus", "network", "email", "hardware", "software", "audio",
        "security", "backup", "consult", "other"
    ] = "other"
    organization_id: Optional[str] = Field(None, pattern=r"^ORG-\d{6,}$")
    contact_id: Optional[str] = Field(None, pattern=r"^CON-\d{6,}$")
    client_id: Optional[str] = Field(None, pattern=r"^CUS-\d{6,}$")
    asset_id: Optional[str] = Field(None, pattern=r"^AST-\d{6,}$")
    client_name: Optional[str] = None
    parent_ticket_id: Optional[str] = Field(None, pattern=r"^(TKT-\d{4}-\d{3}|TCK-\d{6,})$")
    root_cause_id: Optional[str] = Field(None, pattern=r"^PRB-\d{6,}$")
    assigned_to: Optional[str] = Field(None, pattern=r"^USR-\d{6,}$")
    assigned_team: Optional[str] = None
    sla_policy: str = "standard"
    sla_response_due: Optional[datetime] = None
    sla_resolution_due: Optional[datetime] = None
    sla_paused: bool = False
    sla_pause_reason: Optional[str] = None
    source: Literal["phone", "email", "web", "walk-in", "monitoring", "chat", "referral"] = "phone"
    channel: Literal["phone", "email", "onsite", "remote", "web", "chat"] = "phone"
    next_action: Optional[str] = Field(None, max_length=200)
    due: Optional[datetime] = None
    follow_up: Optional[datetime] = None
    worklog: List[WorklogEntry] = Field(default_factory=list)

    @validator("worklog", pre=True)
    def _null_worklog_becomes_empty(cls, v):
        """Accept `worklog:` (YAML null) written by the standalone
        ticket script: treat it as an empty list instead of failing."""
        return [] if v is None else v

    resolution: Optional[str] = None
    resolution_category: Optional[Literal[
        "fixed", "workaround", "no_issue_found", "customer_cancelled",
        "out_of_scope", "duplicate"
    ]] = None

    # Legacy field aliases for reading existing vault data
    created: Optional[datetime] = Field(None, alias="created_at")
    updated: Optional[datetime] = Field(None, alias="updated_at")
    created_by: Optional[str] = Field(default=None)
    updated_by: Optional[str] = Field(default=None)


# =============================================================================
# Client Models
# =============================================================================

class BillingInfo(BaseModel):
    rate: float = Field(default=0, ge=0)
    terms: Literal["due-upon-receipt", "net15", "net30", "net60", "custom", "cash-on-site"] = "due-upon-receipt"
    currency: str = "USD"
    tax_exempt: bool = False
    tax_id: Optional[str] = None
    po_required: bool = False
    billing_contact_id: Optional[str] = Field(None, pattern=r"^CUS-\d{6,}$")
    paypal: Optional[str] = None
    notes: Optional[str] = None


class Address(BaseModel):
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    country: str = "US"


class Client(BaseRecord):
    type: Literal["client"] = "client"
    class_: Literal["business", "residential"] = Field(default="residential", alias="class")
    client_id: str = Field(..., pattern=r"^CUS-\d{6,}$")
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company_name: Optional[str] = None
    display_name: Optional[str] = None
    organization_id: Optional[str] = Field(None, pattern=r"^ORG-\d{6,}$")
    email: List[EmailStr] = Field(default_factory=list)
    phone: List[str] = Field(default_factory=list)
    preferred_contact_method: Literal["email", "phone", "sms", "mail", "teams", "slack"] = "email"
    role: Optional[str] = None
    department: Optional[str] = None
    mobile: List[str] = Field(default_factory=list)
    timezone: str = "America/Chicago"
    language: str = "en"
    preferences: Dict[str, bool] = Field(default_factory=dict)
    address: Optional[Address] = None
    billing: Optional[BillingInfo] = None
    sla_policy: str = "standard"
    contract_id: Optional[str] = Field(None, pattern=r"^CRT-\d{6,}$")
    status: Literal["active", "prospect", "inactive", "former"] = "active"
    equipment: List[str] = Field(default_factory=list)
    custom_fields: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        populate_by_name = True


# =============================================================================
# Contact Models
# =============================================================================

class Contact(BaseRecord):
    type: Literal["contact"] = "contact"
    first_name: str
    last_name: str
    display_name: Optional[str] = None
    organization_id: str = Field(..., pattern=r"^(ORG|CUS)-\d{6,}$")
    role: Optional[str] = None
    department: Optional[str] = None
    email: List[EmailStr] = Field(default_factory=list)
    phone: List[str] = Field(default_factory=list)
    mobile: List[str] = Field(default_factory=list)
    preferred_contact_method: Literal["email", "phone", "sms", "teams", "slack"] = "email"
    timezone: str = "America/Chicago"
    language: str = "en"
    preferences: Dict[str, bool] = Field(default_factory=dict)
    status: Literal["active", "inactive", "former"] = "active"
    custom_fields: Dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# Organization Models
# =============================================================================

class OrganizationBilling(BaseModel):
    rate: float = Field(default=0, ge=0)
    terms: Literal["due-upon-receipt", "net15", "net30", "net60", "custom"] = "net30"
    currency: str = "USD"
    tax_exempt: bool = False
    tax_id: Optional[str] = None
    po_required: bool = False


class Organization(BaseRecord):
    type: Literal["organization"] = "organization"
    name: str
    legal_name: Optional[str] = None
    industry: Optional[Literal[
        "healthcare", "education", "government", "manufacturing", "retail",
        "professional_services", "technology", "hospitality", "construction",
        "finance", "real_estate", "non_profit", "other"
    ]] = None
    size: Optional[Literal["small", "medium", "large", "enterprise"]] = None
    status: Literal["active", "prospect", "former", "vendor"] = "active"
    tier: Literal["managed", "project", "break-fix", "vendor"] = "break-fix"
    primary_contact_id: Optional[str] = Field(None, pattern=r"^CUS-\d{6,}$")
    billing_contact_id: Optional[str] = Field(None, pattern=r"^CUS-\d{6,}$")
    technical_contact_id: Optional[str] = Field(None, pattern=r"^CUS-\d{6,}$")
    address: Optional[Address] = None
    billing: Optional[OrganizationBilling] = None
    sla_policy: str = "standard"
    contract_id: Optional[str] = Field(None, pattern=r"^CRT-\d{6,}$")
    tags: List[str] = Field(default_factory=list)
    custom_fields: Dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# Asset Models
# =============================================================================

class InstalledSoftware(BaseModel):
    name: str
    version: Optional[str] = None
    license_key: Optional[str] = None
    install_date: Optional[datetime] = None


class NetworkInfo(BaseModel):
    switch_port: Optional[str] = None
    vlan: Optional[int] = None
    dhcp: Optional[bool] = None
    subnet: Optional[str] = None
    gateway: Optional[str] = None
    dns_servers: List[str] = Field(default_factory=list)


class Asset(BaseRecord):
    type: Literal["asset"] = "asset"
    asset_type: Literal[
        "workstation", "laptop", "server", "network", "printer",
        "mobile", "software", "storage", "ups", "camera",
        "access_control", "phone_system", "other"
    ] = "other"
    hostname: str
    serial_number: Optional[str] = None
    asset_tag: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    model_year: Optional[int] = Field(None, ge=1990, le=2030)
    operating_system: Optional[str] = None
    os_version: Optional[str] = None
    processor: Optional[str] = None
    memory_gb: Optional[int] = Field(None, ge=0)
    storage_gb: Optional[int] = Field(None, ge=0)
    storage_type: Optional[Literal["SSD", "HDD", "NVMe", "Hybrid", "Unknown"]] = None
    organization_id: str = Field(..., pattern=r"^ORG-\d{6,}$")
    assigned_to: Optional[str] = Field(None, pattern=r"^CUS-\d{6,}$")
    location: Optional[str] = None
    status: Literal["active", "maintenance", "spare", "retired", "lost", "stolen"] = "active"
    purchase_date: Optional[datetime] = None
    warranty_expiration: Optional[datetime] = None
    warranty_provider: Optional[str] = None
    ip_addresses: List[str] = Field(default_factory=list)
    mac_addresses: List[str] = Field(default_factory=list)
    installed_software: List[InstalledSoftware] = Field(default_factory=list)
    network_info: Optional[NetworkInfo] = None
    tags: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    custom_fields: Dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# User Models
# =============================================================================

class Certification(BaseModel):
    name: str
    issuer: Optional[str] = None
    obtained_date: Optional[datetime] = None
    expiration_date: Optional[datetime] = None


class NotificationPrefs(BaseModel):
    email_on_assignment: bool = True
    email_on_mentions: bool = True
    email_on_sla_risk: bool = True
    desktop_on_assignment: bool = True
    desktop_on_mentions: bool = True
    desktop_on_sla_risk: bool = True


class User(BaseRecord):
    type: Literal["user"] = "user"
    first_name: str
    last_name: str
    display_name: Optional[str] = None
    username: str = Field(..., pattern=r"^[a-z][a-z0-9._-]{2,}$")
    email: EmailStr
    phone: Optional[str] = None
    role: Literal["admin", "technician", "dispatcher", "manager", "viewer"] = "technician"
    teams: List[str] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)
    certifications: List[Certification] = Field(default_factory=list)
    capacity_hours_per_day: float = Field(default=8, ge=0, le=24)
    timezone: str = "America/Chicago"
    status: Literal["active", "inactive", "on-leave", "terminated"] = "active"
    avatar: Optional[str] = None
    notifications: NotificationPrefs = Field(default_factory=NotificationPrefs)
    custom_fields: Dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# Project/Task Models
# =============================================================================

class Project(BaseRecord):
    type: Literal["project"] = "project"
    description: Optional[str] = None
    organization_id: str = Field(..., pattern=r"^ORG-\d{6,}$")
    project_manager: str = Field(..., pattern=r"^USR-\d{6,}$")
    sponsor: Optional[str] = Field(None, pattern=r"^CUS-\d{6,}$")
    status: Literal["planning", "active", "on-hold", "completed", "cancelled"] = "planning"
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    methodology: Literal["agile", "waterfall", "hybrid"] = "waterfall"
    start_date: Optional[datetime] = None
    target_date: Optional[datetime] = None
    actual_end_date: Optional[datetime] = None
    budget_hours: float = Field(default=0, ge=0)
    budget_amount: float = Field(default=0, ge=0)
    actual_hours: float = Field(default=0, ge=0)
    actual_amount: float = Field(default=0, ge=0)
    tags: List[str] = Field(default_factory=list)
    custom_fields: Dict[str, Any] = Field(default_factory=dict)


class Task(BaseRecord):
    type: Literal["task"] = "task"
    description: Optional[str] = None
    project_id: Optional[str] = Field(None, pattern=r"^PRJ-\d{4}-\d{6}$")
    ticket_id: Optional[str] = Field(None, pattern=r"^(TKT-\d{4}-\d{3}|TCK-\d{6,})$")
    assignee: Optional[str] = Field(None, pattern=r"^USR-\d{6,}$")
    status: Literal["todo", "in-progress", "waiting", "review", "done", "cancelled"] = "todo"
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    due_date: Optional[datetime] = None
    start_date: Optional[datetime] = None
    completed_date: Optional[datetime] = None
    estimated_hours: float = Field(default=0, ge=0)
    actual_hours: float = Field(default=0, ge=0)
    tags: List[str] = Field(default_factory=list)
    custom_fields: Dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# Knowledge/Problem/Change/Request/Contract Models
# =============================================================================

class Knowledge(BaseRecord):
    type: Literal["knowledge"] = "knowledge"
    status: Literal["draft", "review", "published", "archived"] = "draft"
    category: Literal[
        "networking", "security", "hardware", "software", "email",
        "backup", "procedures", "troubleshooting", "onboarding",
        "compliance", "general"
    ]
    subcategory: Optional[str] = None
    author: str = Field(..., pattern=r"^USR-\d{6,}$")
    reviewer: Optional[str] = Field(None, pattern=r"^USR-\d{6,}$")
    review_date: Optional[datetime] = None
    version: int = Field(default=1, ge=1)
    visibility: Literal["internal", "customer", "public"] = "internal"
    content: str
    related_tickets: List[str] = Field(default_factory=list)
    related_assets: List[str] = Field(default_factory=list)
    related_problems: List[str] = Field(default_factory=list)
    related_changes: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    published_at: Optional[datetime] = None
    ai: Dict[str, Any] = Field(default_factory=dict)


class Problem(BaseRecord):
    type: Literal["problem"] = "problem"
    description: str
    status: Literal["investigating", "known_error", "workaround", "resolved", "closed"] = "investigating"
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    impact: Literal["critical", "high", "medium", "low"] = "medium"
    urgency: Literal["critical", "high", "medium", "low"] = "medium"
    root_cause: Optional[str] = None
    workaround: Optional[str] = None
    organization_id: Optional[str] = Field(None, pattern=r"^ORG-\d{6,}$")
    affected_assets: List[str] = Field(default_factory=list)
    related_incidents: List[str] = Field(default_factory=list)
    known_error_article: Optional[str] = Field(None, pattern=r"^KB-\d{6,}$")
    tags: List[str] = Field(default_factory=list)
    resolved_at: Optional[datetime] = None
    custom_fields: Dict[str, Any] = Field(default_factory=dict)


class Change(BaseRecord):
    type: Literal["change"] = "change"
    change_type: Literal["standard", "normal", "emergency"] = "normal"
    risk: Literal["low", "medium", "high"] = "low"
    impact: Literal["low", "medium", "high"] = "low"
    description: str
    status: Literal[
        "requested", "assessing", "approved", "scheduled",
        "implementing", "reviewing", "closed", "cancelled"
    ] = "requested"
    requester: str = Field(..., pattern=r"^USR-\d{6,}$")
    approver: Optional[str] = Field(None, pattern=r"^USR-\d{6,}$")
    implementer: Optional[str] = Field(None, pattern=r"^USR-\d{6,}$")
    organization_id: Optional[str] = Field(None, pattern=r"^ORG-\d{6,}$")
    affected_assets: List[str] = Field(default_factory=list)
    planned_start: Optional[datetime] = None
    planned_end: Optional[datetime] = None
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    downtime_required: bool = False
    downtime_minutes: int = Field(default=0, ge=0)
    backout_plan: Optional[str] = None
    test_plan: Optional[str] = None
    implementation_plan: Optional[str] = None
    related_problem: Optional[str] = Field(None, pattern=r"^PRB-\d{6,}$")
    related_tickets: List[str] = Field(default_factory=list)
    change_advisory_board: List[str] = Field(default_factory=list)
    approval_date: Optional[datetime] = None
    tags: List[str] = Field(default_factory=list)
    custom_fields: Dict[str, Any] = Field(default_factory=dict)


class Request(BaseRecord):
    type: Literal["request"] = "request"
    request_type: Literal[
        "access", "hardware", "software", "account", "permission",
        "information", "onboarding", "offboarding", "relocation", "other"
    ] = "other"
    description: str
    status: Literal["submitted", "approved", "fulfilled", "rejected", "cancelled"] = "submitted"
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    requester: str = Field(..., pattern=r"^CON-\d{6,}$")
    organization_id: Optional[str] = Field(None, pattern=r"^ORG-\d{6,}$")
    approver: Optional[str] = Field(None, pattern=r"^USR-\d{6,}$")
    fulfiller: Optional[str] = Field(None, pattern=r"^USR-\d{6,}$")
    approval_date: Optional[datetime] = None
    fulfillment_date: Optional[datetime] = None
    cost: float = Field(default=0, ge=0)
    recurring_cost: float = Field(default=0, ge=0)
    tags: List[str] = Field(default_factory=list)
    custom_fields: Dict[str, Any] = Field(default_factory=dict)


class ContractBilling(BaseModel):
    amount: float = Field(default=0, ge=0)
    frequency: Literal["monthly", "quarterly", "annually"] = "monthly"
    currency: str = "USD"
    payment_terms: Literal["due-upon-receipt", "net15", "net30", "net60"] = "net30"
    included_hours: float = Field(default=0, ge=0)
    overage_rate: float = Field(default=0, ge=0)


class Contract(BaseRecord):
    type: Literal["contract"] = "contract"
    contract_type: Literal[
        "managed_services", "project", "break-fix", "retainer", "sla", "nda", "msp"
    ] = "managed_services"
    organization_id: str = Field(..., pattern=r"^ORG-\d{6,}$")
    status: Literal["draft", "active", "renewal_pending", "expired", "terminated"] = "draft"
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    auto_renew: bool = True
    renewal_notice_days: int = Field(default=30, ge=0)
    billing: ContractBilling = Field(default_factory=ContractBilling)
    sla_policy: str = "standard"
    covered_assets: List[str] = Field(default_factory=list)
    covered_services: List[str] = Field(default_factory=list)
    exclusions: List[str] = Field(default_factory=list)
    document_path: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    custom_fields: Dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# Event Model
# =============================================================================

class EventChanges(BaseModel):
    old: Optional[str] = None
    new: Optional[str] = None


class EventActor(BaseModel):
    id: str = Field(..., pattern=r"^(USR|AI|SYS|AUT)-[A-Z0-9]{6,}$")
    type: Literal["human", "ai", "system", "automation"]
    name: Optional[str] = None
    email: Optional[EmailStr] = None


class EventEntity(BaseModel):
    type: Literal[
        "ticket", "client", "contact", "organization", "asset", "user",
        "project", "task", "knowledge", "problem", "change", "request", "contract"
    ]
    id: str = Field(..., pattern=r"^(TKT-\d{4}-\d{3}|[A-Z]{3,4}-\d{6,}|PRJ-\d{4}-\d{6}|TSK-\d{4}-\d{6}|KB-\d{6,})$")


class Event(BaseRecord):
    type: Literal["event"] = "event"
    event_id: str = Field(..., pattern=r"^EVT-\d{8,}$")
    timestamp: datetime
    actor: EventActor
    event_type: str = Field(..., pattern=r"^[a-z]+\.[a-z_]+$")
    entity: EventEntity
    changes: Dict[str, EventChanges] = Field(default_factory=dict)
    reason: Optional[str] = None
    correlation_id: Optional[str] = Field(None, pattern=r"^COR-\d{8,}$")
    causation_id: Optional[str] = Field(None, pattern=r"^EVT-\d{8,}$")
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# AI Models
# =============================================================================

class AIObservation(BaseRecord):
    type: Literal["ai_observation"] = "ai_observation"
    id: str = Field(..., pattern=r"^AIO-\d{6,}$")
    observation_id: str = Field(..., pattern=r"^AIO-\d{6,}$")
    observation: Dict[str, Any]
    target_entity: EntityRef
    status: Literal["active", "acknowledged", "dismissed"] = "active"


class AISuggestion(BaseRecord):
    type: Literal["ai_suggestion"] = "ai_suggestion"
    id: str = Field(..., pattern=r"^AIS-\d{6,}$")
    suggestion_id: str = Field(..., pattern=r"^AIS-\d{6,}$")
    suggestion: Dict[str, Any]
    status: Literal["pending", "accepted", "rejected", "edited", "expired"] = "pending"
    created_at: datetime
    expires_at: Optional[datetime] = None
    model: str
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = Field(None, pattern=r"^(USR|AI|SYS|AUT)-[A-Z0-9]{6,}$")
    resolution_action: Optional[str] = None


class AIAction(BaseRecord):
    type: Literal["ai_action"] = "ai_action"
    id: str = Field(..., pattern=r"^AIA-\d{6,}$")
    action_id: str = Field(..., pattern=r"^AIA-\d{6,}$")
    source_suggestion: str = Field(..., pattern=r"^AIS-\d{6,}$")
    executed_by: str = Field(..., pattern=r"^(USR|AI|SYS|AUT)-[A-Z0-9]{6,}$")
    executed_at: datetime
    # None only when execution produced no mutation event (status=failed);
    # completed actions always carry a real EVT id.
    result_event: Optional[str] = Field(None, pattern=r"^EVT-\d{8,}$")
    status: Literal["completed", "failed", "rolled_back"] = "completed"
    error_message: Optional[str] = None


# =============================================================================
# Union Types & Helpers
# =============================================================================

AnyEntity = Union[
    Ticket, Client, Contact, Organization, Asset, User,
    Project, Task, Knowledge, Problem, Change, Request, Contract,
    Event, AIObservation, AISuggestion, AIAction
]

EntityType = Literal[
    "ticket", "client", "contact", "organization", "asset", "user",
    "project", "task", "knowledge", "problem", "change", "request", "contract"
]

MODEL_MAP: Dict[EntityType, type] = {
    "ticket": Ticket,
    "client": Client,
    "contact": Contact,
    "organization": Organization,
    "asset": Asset,
    "user": User,
    "project": Project,
    "task": Task,
    "knowledge": Knowledge,
    "problem": Problem,
    "change": Change,
    "request": Request,
    "contract": Contract,
}

ID_PATTERNS = {
    "TKT": r"^TKT-\d{4}-\d{3}$",
    "TCK": r"^TCK-\d{6,}$",
    "CUS": r"^CUS-\d{6,}$",
    "CON": r"^CON-\d{6,}$",
    "ORG": r"^ORG-\d{6,}$",
    "AST": r"^AST-\d{6,}$",
    "USR": r"^USR-\d{6,}$",
    "PRJ": r"^PRJ-\d{4}-\d{6}$",
    "TSK": r"^TSK-\d{4}-\d{6}$",
    "KB": r"^KB-\d{6,}$",
    "KB":  r"^KB-\d{6,}$",
    "PRB": r"^PRB-\d{6,}$",
    "CHG": r"^CHG-\d{6,}$",
    "REQ": r"^REQ-\d{6,}$",
    "CRT": r"^CRT-\d{6,}$",
    "EVT": r"^EVT-\d{8,}$",
    "AIS": r"^AIS-\d{6,}$",
    "AIA": r"^AIA-\d{6,}$",
    "AIO": r"^AIO-\d{6,}$",
}

# Invoice/receipt document numbers (Option B, Bradley 2026-10-08):
# year-scoped INV-YYYY-### / RCP-YYYY-### join the legacy day-scoped
# INV-YYYYMMDD-### / RCP-YYYYMMDD-###, which are grandfathered forever.
# The two shapes are deliberately disjoint (4 digits vs 8 digits before
# the sequence), so old and new sequences can never collide.
DOCUMENT_ID_PATTERNS = {
    "INV": r"^INV-(?:\d{8}|\d{4})-\d{3}$",
    "RCP": r"^RCP-(?:\d{8}|\d{4})-\d{3}$",
}


def validate_document_id(kind: str, doc_id: str) -> bool:
    """Validate an invoice/receipt number: legacy OR year-scoped format."""
    pattern = DOCUMENT_ID_PATTERNS.get(kind)
    if not pattern:
        return False
    return bool(re.match(pattern, doc_id))


# =============================================================================
# Search Models
# =============================================================================

class SearchQuery(BaseModel):
    """Search query parameters"""
    query: str
    entity_types: Optional[List[EntityType]] = None
    filters: Optional[Dict[str, Any]] = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    sort_by: Optional[str] = None
    sort_desc: bool = True


class SearchHit(BaseModel):
    """Individual search result"""
    entity: EntityRef
    score: float = Field(ge=0)
    highlights: Dict[str, List[str]] = Field(default_factory=dict)
    snippet: str = ""


class SearchResult(BaseModel):
    """Search results container"""
    hits: List[SearchHit] = Field(default_factory=list)
    total: int = 0
    took_ms: int = 0
    query: str = ""


# =============================================================================
# Relationship Inverse Mapping
# =============================================================================

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


def get_model_for_type(entity_type: EntityType) -> type:
    """Get the Pydantic model class for an entity type"""
    return MODEL_MAP[entity_type]


def get_id_prefix(entity_type: EntityType) -> str:
    """Get the ID prefix for an entity type"""
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


def validate_id(entity_type: EntityType, entity_id: str) -> bool:
    """Validate an entity ID matches the expected pattern for its type"""
    prefix = get_id_prefix(entity_type)
    if prefix in ID_PATTERNS:
        return bool(re.match(ID_PATTERNS[prefix], entity_id))
    return True


def extract_id_prefix(entity_id: str) -> Optional[str]:
    """Extract the prefix from an entity ID"""
    match = re.match(r"^([A-Z]{3,4})-", entity_id)
    return match.group(1) if match else None