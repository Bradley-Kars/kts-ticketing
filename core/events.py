"""Events Module — Event Store and Emitter"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, AsyncIterator
from dataclasses import dataclass

from .models import Event, EventActor, EventEntity, EventChanges, EntityRef
from .paths import events_dir
from .safe_io import safe_mkdir, safe_write, safe_delete


EVENTS_DIR = events_dir()


@dataclass
class EventSubscription:
    """Subscription to event stream"""
    filter: Dict[str, Any]
    queue: "asyncio.Queue"


class EventStore:
    """Append-only immutable event store"""

    def __init__(self):
        safe_mkdir(EVENTS_DIR)
        safe_mkdir(EVENTS_DIR / "current", parents=False)

    def _get_day_dir(self, dt: datetime) -> Path:
        return EVENTS_DIR / str(dt.year) / f"{dt.month:02d}" / f"{dt.day:02d}"

    def append(self, event: Event) -> None:
        """Append event to store (immutable)"""
        day_dir = self._get_day_dir(event.timestamp)
        safe_mkdir(day_dir)

        file_path = day_dir / f"{event.event_id}.json"
        if file_path.exists() or file_path.is_symlink():
            raise FileExistsError(
                f"event store is append-only: {file_path.name} already exists; "
                "existing events are never overwritten or edited"
            )
        safe_write(
            file_path,
            json.dumps(event.model_dump(by_alias=True), default=str, indent=2)
        )

        # Also write to current symlink for fast access
        current_link = EVENTS_DIR / "current" / f"{event.event_id}.json"
        if current_link.exists() or current_link.is_symlink():
            safe_delete(current_link)
        current_link.symlink_to(file_path)

    def get(self, event_id: str) -> Optional[Event]:
        """Get event by ID"""
        # Check current first
        current_link = EVENTS_DIR / "current" / f"{event_id}.json"
        if current_link.exists():
            return Event.model_validate_json(current_link.read_text())

        # Search by date (would need index for production)
        for year_dir in EVENTS_DIR.iterdir():
            if year_dir.name in ("current",):
                continue
            for month_dir in year_dir.iterdir():
                for day_dir in month_dir.iterdir():
                    file_path = day_dir / f"{event_id}.json"
                    if file_path.exists():
                        return Event.model_validate_json(file_path.read_text())
        return None

    def query(
        self,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        event_type: Optional[str] = None,
        actor_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[Event]:
        """Query events with filters"""
        results = []

        for year_dir in sorted(EVENTS_DIR.iterdir(), reverse=True):
            if year_dir.name in ("current",):
                continue
            if start_date and int(year_dir.name) < start_date.year:
                continue
            if end_date and int(year_dir.name) > end_date.year:
                break

            for month_dir in sorted(year_dir.iterdir(), reverse=True):
                for day_dir in sorted(month_dir.iterdir(), reverse=True):
                    for file_path in day_dir.glob("*.json"):
                        if len(results) >= limit:
                            return results

                        try:
                            event = Event.model_validate_json(file_path.read_text())

                            if entity_type and event.entity.type != entity_type:
                                continue
                            if entity_id and event.entity.id != entity_id:
                                continue
                            if event_type and event.event_type != event_type:
                                continue
                            if actor_id and event.actor.id != actor_id:
                                continue
                            if start_date and event.timestamp < start_date:
                                continue
                            if end_date and event.timestamp > end_date:
                                continue

                            results.append(event)
                        except Exception:
                            continue

        return results

    def get_for_entity(self, entity: EntityRef, limit: int = 100) -> List[Event]:
        """Get all events for a specific entity"""
        return self.query(entity_type=entity.type, entity_id=entity.id, limit=limit)


class EventEmitter:
    """Emit events to store and notify subscribers"""

    def __init__(self, store: Optional[EventStore] = None):
        self.store = store or EventStore()
        self._subscriptions: List[EventSubscription] = []

    def emit(
        self,
        event_type: str,
        entity: EntityRef,
        changes: Dict[str, EventChanges],
        actor: EventActor,
        reason: Optional[str] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
    ) -> Event:
        """Create and store an event"""
        now = datetime.now()
        event_id = self._generate_event_id()
        event = Event(
            type="event",
            id=event_id,
            title=f"{event_type}: {entity.id}",
            status="recorded",
            created_at=now,
            updated_at=now,
            created_by=actor.id,
            updated_by=actor.id,
            event_id=event_id,
            timestamp=now,
            actor=actor,
            event_type=event_type,
            entity=EventEntity(type=entity.type, id=entity.id),
            changes=changes,
            reason=reason,
            correlation_id=correlation_id,
            causation_id=causation_id,
            tags=[],
        )

        self.store.append(event)

        # Notify subscribers
        self._notify_subscribers(event)

        return event

    def _generate_event_id(self) -> str:
        """Generate next event ID (monotonic, never reuses deleted IDs)"""
        from .counters import next_seq
        max_num = 0
        for f in EVENTS_DIR.rglob("EVT-*.json"):
            try:
                num = int(Path(f).stem.split("-")[-1])
                max_num = max(max_num, num)
            except (ValueError, IndexError):
                pass
        return f"EVT-{next_seq('event', max_num):08d}"

    def subscribe(self, filter: Dict[str, Any]) -> "asyncio.Queue":
        """Subscribe to event stream with filter"""
        import asyncio
        queue = asyncio.Queue()
        sub = EventSubscription(filter=filter, queue=queue)
        self._subscriptions.append(sub)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue") -> None:
        self._subscriptions = [s for s in self._subscriptions if s.queue != queue]

    def _notify_subscribers(self, event: Event) -> None:
        for sub in self._subscriptions:
            if self._match_filter(event, sub.filter):
                try:
                    sub.queue.put_nowait(event)
                except Exception:
                    pass

    def _match_filter(self, event: Event, filter: Dict[str, Any]) -> bool:
        for key, value in filter.items():
            if key == "event_type" and event.event_type != value:
                return False
            if key == "entity_type" and event.entity.type != value:
                return False
            if key == "entity_id" and event.entity.id != value:
                return False
            if key == "actor_id" and event.actor.id != value:
                return False
        return True

    def get_recent(self, limit: int = 100) -> List[Event]:
        """Get recent events"""
        return self.store.query(limit=limit)

    def get_for_entity(self, entity: EntityRef, limit: int = 100) -> List[Event]:
        return self.store.get_for_entity(entity, limit)