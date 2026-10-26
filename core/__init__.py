"""KTS Core API Package"""

from .api import KTSAPI
from .models import *
from .services import *
from .events import EventStore, EventEmitter
from .permissions import PermissionChecker
from .indexer import Indexer
from .validator import Validator

__all__ = [
    "KTSAPI",
    "EventStore",
    "EventEmitter",
    "PermissionChecker",
    "Indexer",
    "Validator",
]

__version__ = "1.0.0"