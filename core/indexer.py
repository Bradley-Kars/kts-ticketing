"""Indexer — Generate search indexes and relationship graphs from vault"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
import yaml

from .models import AnyEntity, EntityRef, MODEL_MAP, EntityType
from .services.ticket_service import BaseService
from .paths import vault_root, indexes_dir
from .safe_io import safe_mkdir, safe_write


VAULT_ROOT = vault_root()
INDEX_DIR = indexes_dir()


class Indexer:
    """Generate indexes from vault (read-only, rebuildable)"""

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

    def __init__(self, services: Dict[str, Any]):
        self.services = services
        safe_mkdir(INDEX_DIR)

    def rebuild_all(self) -> Dict[str, Any]:
        """Rebuild all indexes from vault"""
        print("Rebuilding all indexes...")

        stats = {
            "tickets": 0,
            "clients": 0,
            "contacts": 0,
            "organizations": 0,
            "assets": 0,
            "users": 0,
            "projects": 0,
            "tasks": 0,
            "knowledge": 0,
            "problems": 0,
            "changes": 0,
            "requests": 0,
            "contracts": 0,
            "relationships": 0,
            "started_at": datetime.now().isoformat(),
        }

        # Rebuild each index
        stats["tickets"] = self._rebuild_tickets()
        stats["clients"] = self._rebuild_clients()
        stats["contacts"] = self._rebuild_contacts()
        stats["organizations"] = self._rebuild_organizations()
        stats["assets"] = self._rebuild_assets()
        stats["users"] = self._rebuild_users()
        stats["projects"] = self._rebuild_projects()
        stats["tasks"] = self._rebuild_tasks()
        stats["knowledge"] = self._rebuild_knowledge()
        stats["problems"] = self._rebuild_problems()
        stats["changes"] = self._rebuild_changes()
        stats["requests"] = self._rebuild_requests()
        stats["contracts"] = self._rebuild_contracts()

        # Build relationship graph
        stats["relationships"] = self._rebuild_relationships()

        # Build search index
        self._rebuild_search_index()

        # Documents (invoices/receipts) — Option B dual-format index
        stats["documents"] = self._rebuild_documents()

        stats["completed_at"] = datetime.now().isoformat()

        # Save stats
        safe_write(INDEX_DIR / "index-stats.json", json.dumps(stats, indent=2))

        print(f"Indexing complete: {stats}")
        return stats

    def scan_documents(self, root: Path = None) -> List[Dict[str, Any]]:
        """Index invoice/receipt JSON files (read-only, Option B 2026-10-08).

        Dual format: legacy INV-YYYYMMDD-### / RCP-YYYYMMDD-### stay
        indexed forever (grandfathered); year-scoped INV-YYYY-### /
        RCP-YYYY-### join them. Each entry carries kind, number, file,
        valid, and format (legacy | year | unknown).
        """
        from .models import validate_document_id
        base = root if root is not None else VAULT_ROOT
        docs: List[Dict[str, Any]] = []
        for kind, folder, field_name in (("INV", "Invoices", "invoice"),
                                         ("RCP", "Receipts", "receipt")):
            d = base / folder
            if not d.exists():
                continue
            for f in sorted(d.glob("*.json")):
                number = f.stem
                data = None
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    number = (data.get(field_name) or {}).get("number") or f.stem
                except (OSError, ValueError):
                    pass
                valid = validate_document_id(kind, number)
                if re.match(rf"^{kind}-\d{{8}}-\d{{3}}$", number):
                    fmt = "legacy"
                elif re.match(rf"^{kind}-\d{{4}}-\d{{3}}$", number):
                    fmt = "year"
                else:
                    fmt = "unknown"
                entry = {
                    "kind": "invoice" if kind == "INV" else "receipt",
                    "number": number,
                    "file": f"{folder}/{f.name}",
                    "valid": valid,
                    "format": fmt,
                }
                if kind == "INV":
                    # stored ticket link (ticket_id contract, 2026-10-10);
                    # None on legacy/unreadable docs.
                    entry["ticket_id"] = data.get("ticket_id") if isinstance(data, dict) else None
                docs.append(entry)
        return docs

    def scan_ticket_invoices(self, root: Path = None) -> Dict[str, List[str]]:
        """Reverse lookup: ticket_id -> [invoice numbers] (2026-10-10).

        Many-to-one: a ticket may map to several invoices. Built from the
        stored ticket_id link, never from client inference.
        """
        reverse: Dict[str, List[str]] = {}
        for d in self.scan_documents(root):
            if d.get("kind") == "invoice" and d.get("ticket_id"):
                reverse.setdefault(d["ticket_id"], []).append(d["number"])
        for k in reverse:
            reverse[k].sort()
        return reverse

    def _rebuild_documents(self) -> int:
        n = self._save_index("documents", self.scan_documents())
        # ticket->invoice reverse index lives beside the documents index
        self._save_index("ticket_invoices", self.scan_ticket_invoices())
        return n

    def _get_service(self, entity_type: str) -> Optional[BaseService]:
        return self.services.get(entity_type)

    def _save_index(self, name: str, data: List[Dict[str, Any]]) -> int:
        file_path = INDEX_DIR / f"{name}.json"
        safe_write(file_path, json.dumps(data, default=str, indent=2))
        return len(data)

    def _rebuild_tickets(self) -> int:
        svc = self._get_service("ticket")
        if not svc:
            return 0
        entities = svc.list(limit=10000)
        data = [e.model_dump(by_alias=True) for e in entities]
        return self._save_index("tickets", data)

    def _rebuild_clients(self) -> int:
        return self._rebuild_from_vault("client", "Clients")

    def _rebuild_contacts(self) -> int:
        return self._rebuild_from_vault("contact", "Contacts")

    def _rebuild_organizations(self) -> int:
        return self._rebuild_from_vault("organization", "Organizations")

    def _rebuild_assets(self) -> int:
        return self._rebuild_from_vault("asset", "Assets")

    def _rebuild_users(self) -> int:
        return self._rebuild_from_vault("user", "Users")

    def _rebuild_projects(self) -> int:
        return self._rebuild_from_vault("project", "Projects")

    def _rebuild_from_vault(self, entity_type: str, dir_name: str) -> int:
        """Read entities directly from vault directory, bypassing model validation"""
        entity_dir = VAULT_ROOT / dir_name
        if not entity_dir.exists():
            # B4 fix (Bradley decision 2): a retired entity dir must also empty its
            # index file, otherwise a stale index (old contacts.json) survives.
            return self._save_index(f"{entity_type}s", [])

        entities = []
        for file_path in entity_dir.rglob("*.md"):
            fm = self._read_frontmatter(file_path)
            if not fm:
                continue
            # Add file path for reference
            fm["_file"] = str(file_path.relative_to(VAULT_ROOT))
            entities.append(fm)

        return self._save_index(f"{entity_type}s", entities)

    def _rebuild_tasks(self) -> int:
        svc = self._get_service("task")
        if not svc:
            return 0
        entities = svc.list(limit=10000)
        data = [e.model_dump(by_alias=True) for e in entities]
        return self._save_index("tasks", data)

    def _rebuild_knowledge(self) -> int:
        svc = self._get_service("knowledge")
        if not svc:
            return 0
        entities = svc.list(limit=10000)
        data = [e.model_dump(by_alias=True) for e in entities]
        return self._save_index("knowledge", data)

    def _rebuild_problems(self) -> int:
        svc = self._get_service("problem")
        if not svc:
            return 0
        entities = svc.list(limit=10000)
        data = [e.model_dump(by_alias=True) for e in entities]
        return self._save_index("problems", data)

    def _rebuild_changes(self) -> int:
        svc = self._get_service("change")
        if not svc:
            return 0
        entities = svc.list(limit=10000)
        data = [e.model_dump(by_alias=True) for e in entities]
        return self._save_index("changes", data)

    def _rebuild_requests(self) -> int:
        svc = self._get_service("request")
        if not svc:
            return 0
        entities = svc.list(limit=10000)
        data = [e.model_dump(by_alias=True) for e in entities]
        return self._save_index("requests", data)

    def _rebuild_contracts(self) -> int:
        svc = self._get_service("contract")
        if not svc:
            return 0
        entities = svc.list(limit=10000)
        data = [e.model_dump(by_alias=True) for e in entities]
        return self._save_index("contracts", data)

    def _rebuild_relationships(self) -> int:
        """Build relationship graph from all entities"""
        relationships = []

        for entity_type, svc in self.services.items():
            if entity_type in ("event", "ai_observation", "ai_suggestion", "ai_action", "relationship"):
                continue
            if not hasattr(svc, 'list'):
                continue
            entities = svc.list(limit=10000)
            for entity in entities:
                for rel in entity.relationships:
                    relationships.append({
                        "source": entity.id,
                        "source_type": entity.type,
                        "target": rel.target,
                        "type": rel.type,
                        "metadata": rel.metadata,
                    })

        return self._save_index("relationships", relationships)

    def _rebuild_search_index(self) -> int:
        """Build full-text search index via SearchService"""
        search_svc = self._get_service("search")
        if search_svc:
            search_svc.rebuild_index()
            return 1
        return 0


class VaultScanner:
    """Scan vault directly without services (for initial bootstrap)"""

    def __init__(self):
        self.vault_root = VAULT_ROOT

    def scan_all(self) -> Dict[str, List[Dict[str, Any]]]:
        """Scan all entity directories and parse frontmatter"""
        results = {}

        dirs = {
            "ticket": self.vault_root / "Tickets",
            "client_business": self.vault_root / "Clients" / "Business",
            "client_residential": self.vault_root / "Clients" / "Residential",
            "contact": self.vault_root / "Contacts",
            "organization": self.vault_root / "Organizations",
            "asset": self.vault_root / "Assets",
            "user": self.vault_root / "Users",
            "project": self.vault_root / "Projects",
            "task": self.vault_root / "Tasks",
            "knowledge": self.vault_root / "Knowledge",
            "problem": self.vault_root / "Problems",
            "change": self.vault_root / "Changes",
            "request": self.vault_root / "Requests",
            "contract": self.vault_root / "Contracts",
        }

        for name, dir_path in dirs.items():
            if not dir_path.exists():
                results[name] = []
                continue

            entities = []
            for file_path in dir_path.rglob("*.md"):
                fm = self._read_frontmatter(file_path)
                if fm:
                    fm["_file"] = str(file_path.relative_to(self.vault_root))
                    entities.append(fm)
            results[name] = entities

        return results

    def _read_frontmatter(self, file_path: Path) -> Optional[Dict[str, Any]]:
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