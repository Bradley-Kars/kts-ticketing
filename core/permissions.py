"""Permissions Module — Granular permission checking"""

import yaml
from pathlib import Path
from typing import Dict, List, Set, Optional
from dataclasses import dataclass

from .models import EventActor
from .paths import config_dir
from .safe_io import safe_read


PERMISSIONS_FILE = config_dir() / "permissions.yaml"
USERS_FILE = config_dir() / "users.yaml"


@dataclass
class PermissionRule:
    permission: str
    roles: List[str]


class PermissionChecker:
    """Check permissions for actors.

    Roles are read from config/users.yaml (user id → role) at construction
    and on reload(). No ids are hardcoded: an actor not present in
    users.yaml has no roles and is denied everything. Malformed or missing
    users.yaml yields an empty user map (deny all, no crash). There is no
    vault frontmatter fallback: deleting or renaming users.yaml never
    grants roles from writable vault files.
    """

    def __init__(self):
        self.rules: Dict[str, List[str]] = {}
        self.role_permissions: Dict[str, Set[str]] = {}
        self.users: Dict[str, dict] = {}
        self._users_mtime: float = 0.0
        self._load_permissions()
        self._load_users()
        self._users_mtime = self._mtime()

    def _load_permissions(self) -> None:
        if not PERMISSIONS_FILE.exists():
            return
        try:
            config = yaml.safe_load(safe_read(PERMISSIONS_FILE))
        except Exception:
            return
        if not isinstance(config, dict):
            return

        permissions = config.get("permissions", {})
        if not isinstance(permissions, dict):
            return
        for perm_name, perm_config in permissions.items():
            if not isinstance(perm_config, dict):
                continue
            roles = perm_config.get("roles", [])
            if not isinstance(roles, list):
                continue
            self.rules[perm_name] = roles
            for role in roles:
                if role not in self.role_permissions:
                    self.role_permissions[role] = set()
                self.role_permissions[role].add(perm_name)

        # Handle wildcard in admin role
        if "admin" in self.role_permissions:
            all_perms = set(self.rules.keys())
            self.role_permissions["admin"].update(all_perms)

    def _load_users(self) -> None:
        """Load users from users.yaml. Missing or malformed → empty map (deny all).

        users.yaml is authoritative. There is no vault frontmatter fallback:
        deleting or renaming users.yaml must never grant roles from writable
        vault Users/*.md files (that would be a fail-open privilege-escalation
        path). Legacy vault Users/{id}.md fixtures have been migrated to yaml
        in the test suite.
        """
        self.users = {}
        if not USERS_FILE.exists():
            return
        try:
            config = yaml.safe_load(safe_read(USERS_FILE))
        except Exception:
            # Malformed yaml: deny all.
            return
        if not isinstance(config, dict):
            return
        users = config.get("users", {})
        if not isinstance(users, dict):
            return
        # Validate each user entry is a dict. A malformed entry (e.g. a
        # string value) is skipped rather than crashing or granting roles
        # from a non-dict value.
        validated: Dict[str, dict] = {}
        for uid, udata in users.items():
            if isinstance(udata, dict):
                validated[str(uid)] = udata
        self.users = validated

    def reload(self) -> None:
        """Reload permissions and users from disk."""
        self._load_permissions()
        self._load_users()

    def _mtime(self) -> float:
        """Return the mtime of users.yaml, or 0 if missing."""
        try:
            return USERS_FILE.stat().st_mtime
        except OSError:
            return 0.0

    def _maybe_reload(self) -> None:
        """Reload if users.yaml has changed since last load.

        Called on every check() so demotions apply without restart.
        Compares (mtime_ns, size) to catch same-second edits and atomic
        renames that plain mtime could miss. Fail-closed: a half-written
        yaml read mid-edit yields deny-all for that request.
        """
        try:
            st = USERS_FILE.stat()
            current = (st.st_mtime_ns, st.st_size)
        except OSError:
            current = (0, 0)
        if current != self._users_mtime:
            self.reload()
            self._users_mtime = current

    def check(self, actor: EventActor, permission: str) -> bool:
        """Check if actor has a specific permission"""
        self._maybe_reload()
        roles = self._get_actor_roles(actor)
        if not roles:
            return False

        for role in roles:
            if role in self.role_permissions:
                if "*" in self.role_permissions[role] or permission in self.role_permissions[role]:
                    return True

        return False

    def check_any(self, actor: EventActor, permissions: List[str]) -> bool:
        """Check if actor has ANY of the permissions"""
        return any(self.check(actor, p) for p in permissions)

    def check_all(self, actor: EventActor, permissions: List[str]) -> bool:
        """Check if actor has ALL of the permissions"""
        return all(self.check(actor, p) for p in permissions)

    def get_actor_permissions(self, actor: EventActor) -> Set[str]:
        """Get all permissions for an actor"""
        roles = self._get_actor_roles(actor)
        perms: Set[str] = set()
        for role in roles:
            if role in self.role_permissions:
                perms.update(self.role_permissions[role])
        return perms

    def _get_actor_roles(self, actor: EventActor) -> List[str]:
        """Get roles for an actor from the loaded user map (users.yaml only).
        No ids are hardcoded; no vault frontmatter fallback exists."""
        if actor.type == "ai":
            return ["ai"]
        if actor.type == "system":
            return ["system"]
        if actor.type == "automation":
            # Look up users.yaml by actor id first; fall back to the
            # system-role mapping if the id is not listed. Honor whatever
            # the human branch checks on a users.yaml entry (role).
            user = self.users.get(actor.id)
            if user is not None:
                role = user.get("role")
                if isinstance(role, str) and role:
                    return [role]
            return ["system"]
        if actor.type == "human":
            user = self.users.get(actor.id)
            if user is None:
                return []
            role = user.get("role")
            if isinstance(role, str) and role:
                return [role]
            return []
        return []

    def get_role_permissions(self, role: str) -> Set[str]:
        return self.role_permissions.get(role, set())

    def list_all_permissions(self) -> List[str]:
        return sorted(self.rules.keys())

    def list_roles(self) -> List[str]:
        return sorted(self.role_permissions.keys())
