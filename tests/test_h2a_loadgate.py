"""H2a load gate: main.js must load as a classic CommonJS script.

The 2026-10-09 sandbox load check failed with
  Plugin failure: kts-rebuild SyntaxError: Cannot use import statement outside a module
because tsc emitted ESM (tsconfig module: ESNext) while Obsidian evals plugin
main.js as a classic script. This gate catches that WITHOUT opening Obsidian:

1. no top-level import/export in main.js (regex),
2. main.js evaluated in a CommonJS sandbox (node vm) with a stub `obsidian`
   module — any ESM syntax is a SyntaxError at compile time,
3. `require("obsidian")` must actually happen (external require, no bundling).
"""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PLUGIN_DIR = Path.home() / ".ticketing" / "plugins" / "kts-rebuild"
MAIN_JS = PLUGIN_DIR / "main.js"

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const mainPath = process.argv[2];
const src = fs.readFileSync(mainPath, "utf8");
if (/^\s*(import|export)\s/m.test(src)) {
  console.error("ESM_SYNTAX_FOUND");
  process.exit(3);
}
const stubObsidian = {
  Plugin: class Plugin {
    constructor() { this.app = null; this._loaded = false; }
    async onload() {}
    async onunload() {}
  },
  // classes the plugin source extends/references at module-eval time
  // (H2c slice 1): stubs only - load gate still checks ESM-free eval +
  // exact external require list; behavior lives in test_h2c1_plugin.
  ItemView: class ItemView { constructor(leaf) { this.leaf = leaf; } },
  PluginSettingTab: class PluginSettingTab {},
  Setting: class Setting {},
  requestUrl: () => Promise.reject(new Error("load-gate stub")),
  Platform: { isDesktop: true, isMobile: false },
};
const required = [];
const mod = { exports: {} };
const sandbox = {
  require(m) {
    required.push(m);
    if (m === "obsidian") return stubObsidian;
    throw new Error("unexpected require: " + m);
  },
  module: mod,
  exports: mod.exports,
  console,
};
vm.createContext(sandbox);
try {
  new vm.Script(src, { filename: mainPath }).runInContext(sandbox);
} catch (e) {
  console.error("EVAL_FAILED: " + e.name + ": " + e.message);
  process.exit(4);
}
if (!required.includes("obsidian")) {
  console.error("NO_OBSIDIAN_REQUIRE");
  process.exit(5);
}
console.log("OK requires=" + required.join(","));
"""


class TestH2aLoadGate(unittest.TestCase):
    def test_main_js_loads_as_commonjs(self):
        """main.js: no ESM syntax, evaluates in a CommonJS sandbox,
        requires obsidian as an external."""
        self.assertTrue(MAIN_JS.exists(), f"missing {MAIN_JS} - run build.sh")
        src = MAIN_JS.read_text()

        self.assertIsNone(
            re.search(r"^\s*(import|export)\s", src, re.M),
            "ESM import/export statement in main.js - Obsidian loads plugins "
            "as classic scripts (SyntaxError at runtime)",
        )

        node = shutil.which("node")
        self.assertIsNotNone(node, "node not on PATH - load gate cannot run")
        with tempfile.NamedTemporaryFile(
            "w", suffix=".js", delete=False
        ) as f:
            f.write(HARNESS)
            harness_path = f.name
        try:
            proc = subprocess.run(
                [node, harness_path, str(MAIN_JS)],
                capture_output=True, text=True, timeout=30,
            )
        finally:
            Path(harness_path).unlink(missing_ok=True)

        self.assertEqual(
            proc.returncode, 0,
            f"main.js failed CommonJS sandbox load: {proc.stderr.strip()} "
            f"(exit {proc.returncode})",
        )
        self.assertIn("obsidian", proc.stdout)
        # require list must be exactly the external obsidian module
        self.assertEqual(proc.stdout.strip(), "OK requires=obsidian")


if __name__ == "__main__":
    unittest.main()
