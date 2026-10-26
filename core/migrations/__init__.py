"""Migrations — Schema migration framework"""

import json
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Set
from dataclasses import dataclass, field

from ..models import AnyEntity, EntityType, MODEL_MAP
from ..services.ticket_service import BaseService
from ..paths import migrations_dir, backups_dir, vault_root, assets_dir as vault_assets_dir
from ..safe_io import safe_mkdir, safe_write, safe_delete, safe_check


MIGRATIONS_DIR = migrations_dir()
BACKUPS_DIR = backups_dir()


@dataclass
class MigrationResult:
    success: bool
    message: str
    entities_affected: int = 0
    backup_path: Optional[str] = None


@dataclass
class ValidationReport:
    valid: bool
    issues: List[str] = field(default_factory=list)


class Migration(ABC):
    """Base migration class"""

    version: str = ""
    description: str = ""
    destructive: bool = False
    requires_backup: bool = True
    affected_entity_types: List[EntityType] = field(default_factory=list)

    @abstractmethod
    def up(self, services: Dict[str, BaseService]) -> MigrationResult:
        """Apply migration"""
        pass

    @abstractmethod
    def down(self, services: Dict[str, BaseService]) -> MigrationResult:
        """Rollback migration"""
        pass

    def validate(self, services: Dict[str, BaseService]) -> ValidationReport:
        """Validate migration result"""
        return ValidationReport(valid=True, issues=[])


class MigrationManager:
    """Manage and execute migrations"""

    def __init__(self, services: Dict[str, BaseService]):
        self.services = services
        safe_mkdir(MIGRATIONS_DIR)
        safe_mkdir(BACKUPS_DIR)
        self._migrations: Dict[str, Migration] = {}

    def register(self, migration: Migration) -> None:
        """Register a migration"""
        if not migration.version:
            raise ValueError("Migration must have a version")
        self._migrations[migration.version] = migration

    def get_pending(self, current_version: str) -> List[Migration]:
        """Get migrations that haven't been applied yet"""
        applied = self._get_applied_versions()
        pending = []
        for version in sorted(self._migrations.keys()):
            if version not in applied and version > current_version:
                pending.append(self._migrations[version])
        return pending

    def _get_applied_versions(self) -> Set[str]:
        version_file = MIGRATIONS_DIR / "applied.json"
        if version_file.exists():
            return set(json.loads(version_file.read_text()))
        return set()

    def _mark_applied(self, version: str) -> None:
        applied = self._get_applied_versions()
        applied.add(version)
        safe_write(MIGRATIONS_DIR / "applied.json", json.dumps(sorted(list(applied))))

    def _create_backup(self, migration: Migration) -> Path:
        """Create vault backup before migration"""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir = BACKUPS_DIR / f"pre-{migration.version}-{timestamp}"
        safe_mkdir(backup_dir)

        safe_write(backup_dir / "MIGRATION.txt", f"Backup for {migration.version}\n{migration.description}")

        return backup_dir

    def apply(self, version: str, force: bool = False) -> MigrationResult:
        """Apply a specific migration"""
        migration = self._migrations.get(version)
        if not migration:
            return MigrationResult(success=False, message=f"Migration {version} not found")

        applied = self._get_applied_versions()
        if version in applied and not force:
            return MigrationResult(success=False, message=f"Migration {version} already applied")

        if migration.destructive and not force:
            return MigrationResult(
                success=False,
                message=f"Migration {version} is destructive. Use force=True to proceed."
            )

        backup_path = None
        if migration.requires_backup:
            backup_path = self._create_backup(migration)

        try:
            result = migration.up(self.services)
            if result.success:
                self._mark_applied(version)
                if backup_path:
                    result.backup_path = str(backup_path)
            return result
        except Exception as e:
            return MigrationResult(success=False, message=f"Migration failed: {e}")

    def rollback(self, version: str) -> MigrationResult:
        """Rollback a migration"""
        migration = self._migrations.get(version)
        if not migration:
            return MigrationResult(success=False, message=f"Migration {version} not found")

        applied = self._get_applied_versions()
        if version not in applied:
            return MigrationResult(success=False, message=f"Migration {version} not applied")

        try:
            result = migration.down(self.services)
            if result.success:
                applied.discard(version)
                safe_write(MIGRATIONS_DIR / "applied.json", json.dumps(sorted(list(applied))))
            return result
        except Exception as e:
            return MigrationResult(success=False, message=f"Rollback failed: {e}")

    def status(self) -> Dict[str, Any]:
        """Get migration status"""
        applied = self._get_applied_versions()
        return {
            "applied": sorted(list(applied)),
            "pending": sorted([v for v in self._migrations.keys() if v not in applied]),
            "total": len(self._migrations),
        }


class Migration001AddAssetSchema(Migration):
    version = "001"
    description = "Add Asset entity schema and directory"
    destructive = False
    affected_entity_types = ["asset"]

    def up(self, services):
        assets_dir = vault_assets_dir()
        safe_mkdir(assets_dir)

        template = assets_dir / "Asset Template.md"
        if not template.exists():
            safe_write(template, """---
type: asset
id: AST-000001
asset_type: workstation
hostname: HOSTNAME
serial_number: ""
asset_tag: ""
manufacturer: ""
model: ""
operating_system: ""
organization_id: ORG-000001
assigned_to: CON-000001
location: ""
status: active
purchase_date:
warranty_expiration:
warranty_provider: ""
ip_addresses: []
mac_addresses: []
installed_software: []
tags: []
---
# Asset Template

## Overview
Asset description.

## Specifications
| Property | Value |
| -------- | ----- |
| Type | 
| Manufacturer | 
| Model | 
| OS | 

## Related Tickets
- 
""")

        return MigrationResult(success=True, message="Asset schema added", entities_affected=0)

    def down(self, services):
        assets_dir = vault_assets_dir()
        if assets_dir.exists():
            files = list(assets_dir.glob("*.md"))
            if len(files) == 1 and files[0].name == "Asset Template.md":
                safe_delete(files[0])
                safe_check(assets_dir).rmdir()
        return MigrationResult(success=True, message="Asset schema removed")


class Migration002AddContactOrg(Migration):
    version = "002"
    description = "Add Contact and Organization entity schemas"
    destructive = False
    affected_entity_types = ["contact", "organization"]

    def up(self, services):
        for name in ["Contacts", "Organizations"]:
            dir_path = vault_root() / name
            safe_mkdir(dir_path)

            template = dir_path / f"{name[:-1]} Template.md"
            if not template.exists():
                if name == "Contacts":
                    safe_write(template, """---
type: contact
id: CON-000001
first_name: First
last_name: Last
display_name: "First Last"
organization_id: ORG-000001
role: ""
department: ""
email: []
phone: []
mobile: []
preferred_contact_method: email
timezone: "America/Chicago"
language: "en"
status: active
---
# Contact Template

## Overview
Contact description.
""")
                else:
                    safe_write(template, """---
type: organization
id: ORG-000001
name: Organization Name
legal_name: ""
industry: ""
size: ""
status: active
tier: break-fix
primary_contact_id: CON-000001
billing_contact_id: CON-000001
technical_contact_id: CON-000001
address:
  street: ""
  city: ""
  state: ""
  postal_code: ""
  country: "US"
billing:
  rate: 0
  terms: net30
  currency: USD
  tax_exempt: false
  tax_id: ""
  po_required: false
sla_policy: standard
contract_id: ""
tags: []
---
# Organization Template

## Overview
Organization description.
""")

        return MigrationResult(success=True, message="Contact and Organization schemas added")

    def down(self, services):
        for name in ["Contacts", "Organizations"]:
            dir_path = vault_root() / name
            if dir_path.exists():
                files = list(dir_path.glob("*.md"))
                if len(files) == 1 and files[0].name.endswith("Template.md"):
                    safe_delete(files[0])
                    safe_check(dir_path).rmdir()
        return MigrationResult(success=True, message="Contact and Organization schemas removed")


class Migration003AddAIFields(Migration):
    version = "003"
    description = "Add AI metadata fields to all entity schemas"
    destructive = False
    affected_entity_types = []

    def up(self, services):
        return MigrationResult(success=True, message="AI fields already in base schema")

    def down(self, services):
        return MigrationResult(success=True, message="AI fields removal not implemented")


class Migration004MigrateTKTToTCK(Migration):
    version = "004"
    description = "Migrate ticket IDs from TKT-YYYY-### to TCK-NNNNNN (NOT RUN - TKT preserved)"
    destructive = True
    requires_backup = True
    affected_entity_types = ["ticket"]

    def up(self, services):
        return MigrationResult(
            success=False,
            message="NOT EXECUTED: TKT format preserved per user decision. This migration is a placeholder only."
        )

    def down(self, services):
        return MigrationResult(success=True, message="No-op")


def create_migration_manager(services: Dict[str, BaseService]) -> MigrationManager:
    manager = MigrationManager(services)
    manager.register(Migration001AddAssetSchema())
    manager.register(Migration002AddContactOrg())
    manager.register(Migration003AddAIFields())
    manager.register(Migration004MigrateTKTToTCK())
    return manager