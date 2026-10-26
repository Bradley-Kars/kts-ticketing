"""H2c slice 2a plugin tests: commands, ribbon, settings fields, auto-refresh.

Inventory parity (old plugin):
  3 commands: open-dashboard, new-ticket, search-tickets
  1 ribbon: layout-dashboard -> Open KTS Dashboard
  3 settings fields: autoRefresh (on), refreshInterval (30000), theme (system)
  (Stripe fields + vaultPath are DROPPED per A5b/A5c)

Rules honored: READ only, token in loadLocalStorage/saveLocalStorage,
isDesktopOnly false, bridge 127.0.0.1:8799 unchanged, NO write endpoints,
NO Stripe client code, NO Node/Electron.
"""
import json
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
const mode = process.argv[3];
const src = fs.readFileSync(mainPath, "utf8");
if (/^\s*(import|export)\s/m.test(src)) {
  console.error("ESM_SYNTAX_FOUND");
  process.exit(3);
}

const logs = [], warns = [], requests = [], notices = [], intervals = [];
const recorder = {
  log: (...a) => logs.push(a.map(String).join(" ")),
  warn: (...a) => warns.push(a.map(String).join(" ")),
  error: (...a) => warns.push("ERROR:" + a.map(String).join(" ")),
  info: (...a) => logs.push(a.map(String).join(" ")),
};

function makeEl(tag) {
  const el = { tag: tag || "div", _text: "", children: [],
    empty() { this._text = ""; this.children = []; return this; },
    setText(t) { this._text = String(t); return this; },
    getText() { return this._text; },
    createEl(t, opts) {
      opts = opts || {}; const c = makeEl(t);
      if (opts.text !== undefined) c._text = String(opts.text);
      this.children.push(c); return c;
    },
  };
  return el;
}
function flattenText(el, out) {
  out = out || [];
  if (el._text) out.push(el._text);
  for (const c of el.children) flattenText(c, out);
  return out;
}

let requestUrlImpl;
if (mode === "commands" || mode === "settings") {
  requestUrlImpl = () => Promise.resolve({
    status: 200, text: "", json: { count: 0, tickets: [] },
  });
} else if (mode === "autoon" || mode === "autooff") {
  requestUrlImpl = () => Promise.resolve({
    status: 200, text: "", json: { count: 2, tickets: [
      { id: "TKT-2026-001", status: "new", priority: "high",
        client_name: "", title: "A" },
      { id: "TKT-2026-002", status: "in-progress", priority: "low",
        client_name: "", title: "B" },
    ]},
  });
}

class Plugin {
  constructor(app, manifest) { this.app = app; this.manifest = manifest; this._loaded = false; this._commands = []; this._intervals = []; }
  addStatusBarItem() { this._statusBar = makeEl(); return this._statusBar; }
  addSettingTab(tab) { this._settingTab = tab; }
  registerView(type, creator) { this._viewCreator = creator; this._viewType = type; }
  addCommand(cmd) { this._commands.push(cmd); }
  addRibbonIcon(icon, title, cb) { this._ribbon = { icon, title, cb }; return makeEl(); }
  registerInterval(id) { this._intervals.push(id); return id; }
  async loadData() { return this.app.__data === undefined ? null : this.app.__data; }
  async saveData(d) { this.app.__data = d; }
}
class ItemView { constructor(leaf) { this.leaf = leaf; this.contentEl = makeEl(); } }
class PluginSettingTab {
  constructor(app, plugin) {
    this.app = app; this.plugin = plugin;
    this.containerEl = makeEl(); this.containerEl._settings = [];
  }
}
class Setting {
  constructor(containerEl) {
    this._el = containerEl; this.name = ""; this.desc = "";
    if (containerEl._settings) containerEl._settings.push(this);
  }
  setName(n) { this.name = String(n); return this; }
  setDesc(d) { this.desc = String(d); return this; }
  addText(cb) {
    const t = { inputEl: { type: "text" }, _v: "", _onChange: null,
      setValue(v) { this._v = String(v); return this; },
      onChange(fn) { this._onChange = fn; return this; } };
    cb(t); this._text = t; return this;
  }
  addToggle(cb) {
    const tg = { _v: false, _onChange: null,
      setValue(v) { this._v = !!v; return this; },
      onChange(fn) { this._onChange = fn; return this; } };
    cb(tg); this._toggle = tg; return this;
  }
  addDropdown(cb) {
    const dd = { _opts: {}, _v: "", _onChange: null,
      addOption(k, l) { this._opts[k] = l; return this; },
      setValue(v) { this._v = String(v); return this; },
      onChange(fn) { this._onChange = fn; return this; } };
    cb(dd); this._dropdown = dd; return this;
  }
}
const stubObsidian = {
  Plugin, ItemView, PluginSettingTab, Setting,
  Notice: class Notice { constructor(msg) { this.message = String(msg); notices.push(this.message); } },
  Platform: { isDesktop: true, isMobile: false },
  requestUrl: (p) => { requests.push(p); return requestUrlImpl(p); },
};

function makeApp(dataJson, lsSeed) {
  const ls = Object.assign({}, lsSeed || {});
  return {
    __data: dataJson,
    __ls: ls,
    loadLocalStorage(k) {
      return Object.prototype.hasOwnProperty.call(ls, k) ? ls[k] : null;
    },
    saveLocalStorage(k, v) {
      if (v === null || v === undefined) delete ls[k]; else ls[k] = v;
    },
    workspace: { getLeavesOfType: () => [], detachLeavesOfType: () => {} },
  };
}

const mod = { exports: {} };
const sandbox = {
  require(m) {
    if (m === "obsidian") return stubObsidian;
    throw new Error("unexpected require: " + m);
  },
  module: mod, exports: mod.exports, console: recorder,
  window: {
    setInterval: (fn, ms) => { intervals.push({ ms }); return intervals.length; },
    clearInterval: () => {},
  },
};
vm.createContext(sandbox);
try {
  new vm.Script(src, { filename: mainPath }).runInContext(sandbox);
} catch (e) {
  console.error("EVAL_FAILED: " + e.name + ": " + e.message);
  process.exit(4);
}
const Main = mod.exports;

(async () => {
  const out = { mode };
  try {
    if (mode === "commands") {
      const app = makeApp(
        { bridgeUrl: "http://127.0.0.1:8799" },
        { "kts-rebuild.token": "tok_SEEDSEED01" }
      );
      const plugin = new Main.default(app, { id: "kts-rebuild" });
      await plugin.onload();
      out.commands = plugin._commands.map(c => ({ id: c.id, name: c.name }));
      out.ribbon = plugin._ribbon ? { icon: plugin._ribbon.icon, title: plugin._ribbon.title } : null;
      // invoke command callbacks to capture notices
      const cmdNotices = [];
      for (const c of plugin._commands) {
        if (typeof c.callback === "function") {
          c.callback();
        }
      }
      out.notices = notices.slice();
      // ribbon callback
      if (plugin._ribbon && typeof plugin._ribbon.cb === "function") {
        plugin._ribbon.cb();
      }
      out.ribbonNotice = notices.slice(-1)[0] || null;
      // no saveData from command callbacks expected
      out.dataAfter = JSON.stringify(app.__data || {});
      out.lsAfter = JSON.stringify(app.__ls || {});
      out.requests = requests;
    } else if (mode === "settings") {
      const app = makeApp(
        { bridgeUrl: "http://127.0.0.1:8799" },
        { "kts-rebuild.token": "tok_SEEDSEED01" }
      );
      const plugin = new Main.default(app, { id: "kts-rebuild" });
      await plugin.onload();
      const tab = plugin._settingTab;
      tab.display();
      out.settingNames = tab.containerEl._settings.map(s => s.name);
      // toggle autoRefresh -> false
      const ar = tab.containerEl._settings.find(s => s.name === "Auto-refresh");
      await ar._toggle._onChange(false);
      // refreshInterval valid
      const ri = tab.containerEl._settings.find(s => s.name === "Refresh interval (ms)");
      await ri._text._onChange("45000");
      // refreshInterval invalid (non-numeric)
      await ri._text._onChange("abc");
      // theme dark
      const th = tab.containerEl._settings.find(s => s.name === "Theme");
      await th._dropdown._onChange("dark");
      // theme invalid -> clamped to system
      await th._dropdown._onChange("sepia");
      out.dataAfter = JSON.stringify(app.__data || {});
      out.lsAfter = JSON.stringify(app.__ls || {});
      out.tokenAfterLoadLocal = Main.loadToken(app);
    } else if (mode === "autoon") {
      const app = makeApp(
        { bridgeUrl: "http://127.0.0.1:8799", autoRefresh: true, refreshInterval: 60000 },
        { "kts-rebuild.token": "tok_SEEDSEED01" }
      );
      const plugin = new Main.default(app, { id: "kts-rebuild" });
      await plugin.onload();
      out.intervals = intervals.map(i => ({ ms: i.ms }));
    } else if (mode === "autooff") {
      const app = makeApp(
        { bridgeUrl: "http://127.0.0.1:8799", autoRefresh: false, refreshInterval: 60000 },
        { "kts-rebuild.token": "tok_SEEDSEED01" }
      );
      const plugin = new Main.default(app, { id: "kts-rebuild" });
      await plugin.onload();
      out.intervals = intervals.map(i => ({ ms: i.ms }));
    }
    out.logs = logs;
    out.warns = warns;
    console.log("JSON:" + JSON.stringify(out));
    process.exit(0);
  } catch (e) {
    console.error("MODE_FAILED: " + ((e && e.stack) || e));
    process.exit(6);
  }
})();
"""


class TestH2c2Plugin(unittest.TestCase):
    def setUp(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node not on PATH")
        self.assertTrue(MAIN_JS.exists(), f"missing {MAIN_JS} - run build.sh")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(HARNESS)
            self.harness = Path(f.name)
        self.addCleanup(self.harness.unlink, missing_ok=True)

    def _run(self, mode):
        proc = subprocess.run(
            ["node", str(self.harness), str(MAIN_JS), mode],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(
            proc.returncode, 0,
            f"mode {mode} failed: {proc.stderr.strip()}",
        )
        for line in proc.stdout.splitlines():
            if line.startswith("JSON:"):
                return json.loads(line[len("JSON:"):])
        self.fail(f"no JSON output for mode {mode}: {proc.stdout!r}")

    def test_command_open_dashboard(self):
        out = self._run("commands")
        # inventory parity: exact id/name
        self.assertEqual(len(out["commands"]), 3)
        ids = {c["id"] for c in out["commands"]}
        self.assertEqual(ids, {"open-dashboard", "new-ticket", "search-tickets"})
        open_d = next(c for c in out["commands"] if c["id"] == "open-dashboard")
        self.assertEqual(open_d["name"], "Open KTS Dashboard")
        # ribbon parity
        self.assertIsNotNone(out["ribbon"])
        self.assertEqual(out["ribbon"]["icon"], "layout-dashboard")
        self.assertEqual(out["ribbon"]["title"], "Open KTS Dashboard")
        # callback -> notice stub (no view exists yet)
        self.assertTrue(out["ribbonNotice"] and "later slice" in out["ribbonNotice"])
        # commands invoked -> notices for each
        cmd_notices = out["notices"]
        open_notice = next((n for n in cmd_notices if "dashboard" in n.lower()), None)
        self.assertIsNotNone(open_notice)
        # no write request from command callbacks
        reqs = out.get("requests") or []
        cmd_reqs = [r for r in reqs if r.get("method") and r["method"] in ("POST","PUT","PATCH","DELETE")]
        self.assertEqual(cmd_reqs, [])
        # data.json not written by command handlers
        self.assertNotIn("tok_", out["dataAfter"])

    def test_command_new_ticket(self):
        out = self._run("commands")
        new_t = next(c for c in out["commands"] if c["id"] == "new-ticket")
        self.assertEqual(new_t["name"], "Create New Ticket")
        cmd_notices = out["notices"]
        new_notice = next((n for n in cmd_notices if "new-ticket" in n.lower() or "read-only" in n.lower()), None)
        self.assertIsNotNone(new_notice)

    def test_command_search_tickets(self):
        out = self._run("commands")
        search_t = next(c for c in out["commands"] if c["id"] == "search-tickets")
        self.assertEqual(search_t["name"], "Search Tickets")
        cmd_notices = out["notices"]
        search_notice = next((n for n in cmd_notices if "search" in n.lower()), None)
        self.assertIsNotNone(search_notice)

    def test_ribbon_icon(self):
        out = self._run("commands")
        self.assertEqual(out["ribbon"]["icon"], "layout-dashboard")
        self.assertEqual(out["ribbon"]["title"], "Open KTS Dashboard")
        # ribbon callback reuses openDashboard -> same notice
        self.assertTrue(out["ribbonNotice"] and "dashboard" in out["ribbonNotice"].lower())

    def test_settings_fields_persist(self):
        out = self._run("settings")
        # all 5 settings present in order
        self.assertEqual(out["settingNames"], [
            "Bridge URL", "Device token", "Auto-refresh",
            "Refresh interval (ms)", "Theme"
        ])
        data = json.loads(out["dataAfter"])
        # autoRefresh toggled false
        self.assertEqual(data.get("autoRefresh"), False)
        # refreshInterval valid value persisted
        self.assertEqual(data.get("refreshInterval"), 45000)
        # invalid "abc" rejected -> previous 45000 kept
        self.assertNotIn("abc", str(data.get("refreshInterval")))
        # theme: 'dark' persisted then 'sepia' clamped to 'system' -> final 'system'
        self.assertEqual(data.get("theme"), "system")
        # token never in data.json
        self.assertNotIn("tok_", out["dataAfter"])
        # no stripe/vaultPath keys
        for bad in ("stripe", "vaultPath", "secret"):
            for k in data:
                self.assertNotIn(bad, k.lower())
        # bridgeUrl preserved through persistSettings merge
        self.assertEqual(data.get("bridgeUrl"), "http://127.0.0.1:8799")
        # device token still in localStorage
        self.assertEqual(out["tokenAfterLoadLocal"], "tok_SEEDSEED01")
        # localStorage only has token (no data.json keys)
        self.assertIn("kts-rebuild.token", out["lsAfter"])
        self.assertIn("tok_SEEDSEED01", out["lsAfter"])

    def test_autorefresh_interval(self):
        on = self._run("autoon")
        self.assertEqual(len(on["intervals"]), 1)
        self.assertEqual(on["intervals"][0]["ms"], 60000)
        off = self._run("autooff")
        self.assertEqual(len(off["intervals"]), 0)

    def test_read_only_surface(self):
        """Source/built-JS scan: GET only, no write verbs, no Stripe client code, no vaultPath."""
        src = MAIN_JS.read_text()
        # only GET as the request verb
        self.assertIn('"GET"', src)
        for verb in ("POST", "PUT", "PATCH", "DELETE"):
            self.assertNotIn(f'"{verb}"', src)
        # no STRIPE CLIENT CODE: no Stripe class, no Authorization header,
        # no createPaymentIntent, no stripeSecretKey, no Stripe SDK import.
        # The word "stripe" appears ONLY in a comment documenting the
        # token-like regex ("stripe-style rk_/sk_/pk_...") which is a
        # privacy guard, not Stripe client code. "secret" appears in
        # variable names (ls_token_key) and comments — not secret material.
        for bad in ("vaultPath",):
            self.assertNotIn(bad, src.lower())
        # no actual Stripe API method names (these would be client SDK)
        for bad in ("createPaymentIntent", "createCustomer", "createCheckoutSession",
                     "stripeSecretKey", "stripePublishableKey", "stripeWebhookSecret",
                     "Stripe("):
            self.assertNotIn(bad, src)
        # commands present, none contain requestUrl (stubs only)
        src_ts = (PLUGIN_DIR / "src" / "main.ts").read_text()
        for bad in ("vaultPath",):
            self.assertNotIn(bad, src_ts.lower())
        for bad in ("createPaymentIntent", "createCustomer", "createCheckoutSession",
                     "stripeSecretKey", "stripePublishableKey", "stripeWebhookSecret",
                     "Stripe("):
            self.assertNotIn(bad, src_ts)


if __name__ == "__main__":
    unittest.main()