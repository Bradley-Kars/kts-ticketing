"""Search Service — Full-text and structured search"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
from dataclasses import dataclass

from ..models import AnyEntity, EntityRef, MODEL_MAP, EntityType
from ..events import EventEmitter
from ..paths import indexes_dir
from ..safe_io import safe_mkdir, safe_write, safe_read


INDEX_DIR = indexes_dir()
INDEX_FILE = INDEX_DIR / "search-index.json"


@dataclass
class SearchResult:
    entity: EntityRef
    score: float
    highlights: Dict[str, List[str]]
    snippet: str


class SearchService:
    """Full-text and structured search across all entities"""

    def __init__(
        self,
        services: Dict[str, Any],
        event_emitter: Optional[EventEmitter] = None,
    ):
        self.services = services
        self.event_emitter = event_emitter
        self._index: Dict[str, Any] = {}
        self._loaded = False

    def load_index(self) -> None:
        """Load search index from disk"""
        if INDEX_FILE.exists():
            self._index = json.loads(safe_read(INDEX_FILE))
        self._loaded = True

    def rebuild_index(self) -> Dict[str, Any]:
        """Rebuild search index from all entities"""
        print("Rebuilding search index...")
        index = {
            "version": 1,
            "updated": datetime.now().isoformat(),
            "documents": [],
        }

        for entity_type, service in self.services.items():
            if entity_type in ("event", "ai_observation", "ai_suggestion", "ai_action", "relationship"):
                continue  # Skip internal types
            if not hasattr(service, 'list'):
                continue
            entities = service.list(limit=10000)
            for entity in entities:
                doc = self._entity_to_document(entity)
                if doc:
                    index["documents"].append(doc)

        safe_mkdir(INDEX_DIR)
        safe_write(INDEX_FILE, json.dumps(index, indent=2))

        self._index = index
        self._loaded = True
        print(f"Indexed {len(index['documents'])} documents")
        return index

    def _entity_to_document(self, entity: AnyEntity) -> Optional[Dict[str, Any]]:
        """Convert entity to search document"""
        try:
            # Extract searchable text
            text_parts = [
                entity.title,
                getattr(entity, "description", ""),
                getattr(entity, "content", ""),
            ]

            # Add field-specific text
            for key, value in entity.model_dump().items():
                if isinstance(value, str):
                    text_parts.append(value)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            text_parts.append(item)
                        elif isinstance(item, dict):
                            for v in item.values():
                                if isinstance(v, str):
                                    text_parts.append(v)

            full_text = " ".join(filter(None, text_parts))
            if not full_text.strip():
                return None

            # Build document
            doc = {
                "id": entity.id,
                "type": entity.type,
                "title": entity.title,
                "content": full_text[:10000],  # Truncate for index size
                "fields": {
                    "status": getattr(entity, "status", ""),
                    "priority": getattr(entity, "priority", ""),
                    "category": getattr(entity, "category", ""),
                    "tags": getattr(entity, "tags", []),
                },
                "boost": 1.0,
            }

            # Add entity-specific fields
            if entity.type == "ticket":
                doc["fields"]["client"] = getattr(entity, "client", {}).get("name", "") if hasattr(entity, "client") else ""
                doc["fields"]["assignee"] = getattr(entity, "assigned_to", "")
                doc["fields"]["organization"] = getattr(entity, "organization_id", "")
                doc["fields"]["asset"] = getattr(entity, "asset_id", "")
            elif entity.type == "client":
                doc["fields"]["organization"] = getattr(entity, "organization_id", "")
                doc["fields"]["class"] = getattr(entity, "class_", "")

            return doc
        except Exception as e:
            print(f"Error indexing {entity.id}: {e}")
            return None

    def search(
        self,
        query: str,
        entity_types: Optional[List[EntityType]] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 20,
    ) -> List[SearchResult]:
        """Search the index"""
        if not self._loaded:
            self.load_index()

        if not self._index.get("documents"):
            return []

        # Parse query
        parsed = self._parse_query(query)
        terms = parsed["terms"]
        phrases = parsed["phrases"]
        field_filters = parsed["field_filters"]

        # Merge with explicit filters
        if filters:
            for k, v in filters.items():
                field_filters[k] = v

        results = []
        for doc in self._index["documents"]:
            # Filter by entity type
            if entity_types and doc["type"] not in entity_types:
                continue

            # Apply field filters
            if not self._match_filters(doc, field_filters):
                continue

            # Score
            score = self._score_document(doc, terms, phrases)
            if score > 0:
                highlights = self._highlight(doc, terms, phrases)
                snippet = self._make_snippet(doc, terms, phrases)
                results.append(SearchResult(
                    entity=EntityRef(type=doc["type"], id=doc["id"]),
                    score=score,
                    highlights=highlights,
                    snippet=snippet,
                ))

        # Sort by score descending
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def _parse_query(self, query: str) -> Dict[str, Any]:
        """Parse search query into terms, phrases, and field filters"""
        terms = []
        phrases = []
        field_filters = {}

        # Extract phrases (quoted)
        phrase_pattern = r'"([^"]+)"'
        for match in re.finditer(phrase_pattern, query):
            phrases.append(match.group(1))
            query = query.replace(match.group(0), "")

        # Extract field filters (key:value)
        filter_pattern = r'(\w+):(\S+)'
        for match in re.finditer(filter_pattern, query):
            field_filters[match.group(1)] = match.group(2)
            query = query.replace(match.group(0), "")

        # Remaining terms
        remaining = query.strip()
        if remaining:
            terms = remaining.split()

        return {
            "terms": terms,
            "phrases": phrases,
            "field_filters": field_filters,
        }

    def _match_filters(self, doc: Dict[str, Any], filters: Dict[str, Any]) -> bool:
        for key, value in filters.items():
            if key in doc["fields"]:
                doc_value = doc["fields"][key]
                if isinstance(doc_value, list):
                    if value not in doc_value:
                        return False
                elif str(doc_value).lower() != str(value).lower():
                    return False
        return True

    def _score_document(
        self,
        doc: Dict[str, Any],
        terms: List[str],
        phrases: List[str],
    ) -> float:
        score = 0.0
        content = doc["content"].lower()
        title = doc["title"].lower()

        # Phrase matches (highest weight)
        for phrase in phrases:
            phrase_lower = phrase.lower()
            if phrase_lower in title:
                score += 10.0
            if phrase_lower in content:
                score += 5.0

        # Term matches
        for term in terms:
            term_lower = term.lower()
            # Title matches
            if term_lower in title:
                score += 3.0
            # Content matches
            count = content.count(term_lower)
            score += min(count * 0.5, 5.0)

        # Boost
        score *= doc.get("boost", 1.0)

        return score

    def _highlight(
        self,
        doc: Dict[str, Any],
        terms: List[str],
        phrases: List[str],
    ) -> Dict[str, List[str]]:
        """Extract highlighted snippets"""
        highlights = {}
        content = doc["content"]
        for phrase in phrases:
            idx = content.lower().find(phrase.lower())
            if idx >= 0:
                start = max(0, idx - 50)
                end = min(len(content), idx + len(phrase) + 50)
                highlights.setdefault("content", []).append(content[start:end])
        return highlights

    def _make_snippet(
        self,
        doc: Dict[str, Any],
        terms: List[str],
        phrases: List[str],
    ) -> str:
        """Create a result snippet"""
        content = doc["content"]
        # Find first match
        best_pos = len(content)
        for phrase in phrases:
            idx = content.lower().find(phrase.lower())
            if idx >= 0 and idx < best_pos:
                best_pos = idx
        for term in terms:
            idx = content.lower().find(term.lower())
            if idx >= 0 and idx < best_pos:
                best_pos = idx

        if best_pos < len(content):
            start = max(0, best_pos - 80)
            end = min(len(content), best_pos + 160)
            return "..." + content[start:end] + "..."
        return content[:200] + "..."

    def suggest(self, prefix: str, entity_type: Optional[EntityType] = None, limit: int = 10) -> List[str]:
        """Autocomplete suggestions"""
        if not self._loaded:
            self.load_index()

        suggestions = set()
        prefix_lower = prefix.lower()

        for doc in self._index["documents"]:
            if entity_type and doc["type"] != entity_type:
                continue
            if doc["title"].lower().startswith(prefix_lower):
                suggestions.add(doc["title"])
            # Also check tags
            for tag in doc["fields"].get("tags", []):
                if tag.lower().startswith(prefix_lower):
                    suggestions.add(tag)

        return sorted(suggestions)[:limit]