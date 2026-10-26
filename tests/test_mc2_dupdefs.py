"""MC-2 duplicate-definition guard — sandbox-only, real tree never written.

Fails if any class in core/ defines the same method name more than once.
Python's last-def-wins silently hides earlier copies, which is how Bug 1
(raw type('obj', ...) event objects) and Bug 2 (version excluded from
serialization) survived in knowledge_service.py, task_service.py, and
project_service.py for so long. This test makes the hazard visible.
"""

# ---- ISOLATION GUARD: must be FIRST, before ANY core.* imports ------------
try:
    from tests.test_isolation_guard import require_isolation, snapshot_real_tree
except ImportError:  # running as `python3 tests/test_mc2_dupdefs.py`
    from test_isolation_guard import require_isolation, snapshot_real_tree
require_isolation()
# ---------------------------------------------------------------------------

import ast
import collections
import pathlib
import unittest
from pathlib import Path

HOME = Path.home()
REAL_VAULT = HOME / "Documents" / "KTS Records"
REAL_HOME = HOME / ".ticketing"
CODE = REAL_HOME


def find_duplicate_methods(py_path: Path) -> list[tuple[str, str, list[str]]]:
    """Return [(file, class_name, [dup_method_names]), ...] for classes with
    duplicate method names."""
    src = py_path.read_text()
    tree = ast.parse(src)
    results = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            names = [
                n.name
                for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            dupes = [n for n, c in collections.Counter(names).items() if c > 1]
            if dupes:
                results.append((str(py_path), node.name, dupes))
    return results


class TestDupDefs(unittest.TestCase):
    """No class in core/ may define the same method twice."""

    def setUp(self):
        self.real_snap = snapshot_real_tree()

    def test_no_duplicate_methods_in_core(self):
        """ast scan of core/ finds zero duplicate method definitions."""
        dupes: list[tuple[str, str, list[str]]] = []
        for py in pathlib.Path(CODE / "core").rglob("*.py"):
            dupes.extend(find_duplicate_methods(py))

        if dupes:
            lines = []
            for path, cls, methods in dupes:
                lines.append(f"  {path} class {cls}: {methods}")
            self.fail(
                "Duplicate method definitions found in core/ (last-def-wins "
                "silently hides earlier copies):\n" + "\n".join(lines)
            )

    def test_real_tree_untouched(self):
        """Real vault + home unchanged."""
        import hashlib

        def sha(p: Path) -> str:
            return hashlib.sha256(p.read_bytes()).hexdigest()

        self.assertEqual(
            sha(REAL_HOME / "id-counters.json"),
            "22a89c711f1f08f74119fe77eea1e1436f218d035bcc16bdf444c4f4de6ae245",
        )
        self.assertEqual(
            sha(REAL_HOME / "config" / "users.yaml"),
            "93f7797d42c115d2ddd7e4e944088e17b721370859ceab49140e6b0a2ecaada4",
        )
        vault_count = sum(
            1 for p in REAL_VAULT.rglob("*") if p.is_file() and not p.is_symlink()
        )
        self.assertEqual(vault_count, 47)


if __name__ == "__main__":
    unittest.main(verbosity=2)
