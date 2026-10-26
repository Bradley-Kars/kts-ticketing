"""H2c slice 1 plugin tests: token storage, data.json warning, offline state.

Runs main.js in a node vm with a stub `obsidian` module that fakes just
enough of the API (Plugin base, ItemView, PluginSettingTab, Setting,
requestUrl, app.loadLocalStorage/saveLocalStorage) to drive the real
plugin code. Four modes:

  token  - onload reads the token from localStorage (never data.json);
           settings-tab Device token onChange writes localStorage only;
           saveData (data.json) never receives the token.
  warn   - data.json containing a token-like value triggers a startup
           WARNING that names the KEY but never the value.
  clean  - ordinary data.json produces no warning.
  offline- bridge unreachable -> status bar "KTS: offline" and the view
           renders the clear OFF-1 offline message (no writes).
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

const logs = [], warns = [], requests = [], notices = [];
const recorder = {
  log: (...a) => logs.push(a.map(String).join(" ")),
  warn: (...a) => warns.push(a.map(String).join(" ")),
  error: (...a) => warns.push("ERROR:" + a.map(String).join(" ")),
  info: (...a) => logs.push(a.map(String).join(" ")),
};

function makeEl(tag) {
  const el = {
    tag: tag || "div", _text: "", children: [],
    empty() { this._text = ""; this.children = []; return this; },
    setText(t) { this._text = String(t); return this; },
    getText() { return this._text; },
    createEl(t, opts) {
      opts = opts || {};
      const c = makeEl(t);
      if (opts.text !== undefined) c._text = String(opts.text);
      this.children.push(c);
      return c;
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
if (mode === "offline") {
  requestUrlImpl = () =>
    Promise.reject(new Error("connect ECONNREFUSED 127.0.0.1:8799"));
} else if (mode === "token") {
  requestUrlImpl = () => Promise.resolve({
    status: 200, text: "",
    json: { count: 2, tickets: [
      { id: "TKT-2026-001", status: "new", priority: "high",
        client_name: "Acme", title: "Fix thing" },
      { id: "TKT-2026-002", status: "in-progress", priority: "low",
        client_name: "", title: "Other" },
    ]},
  });
} else {
  requestUrlImpl = () => Promise.resolve({
    status: 200, text: "", json: { count: 0, tickets: [] },
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
  addText(cb) {
    const t = {
      inputEl: { type: "text" },
      _v: "", _onChange: null,
      setValue(v) { this._v = String(v); return this; },
      onChange(fn) { this._onChange = fn; return this; },
    };
    cb(t);
    this._text = t;
    return this;
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
    setInterval: (fn, ms) => { (sandbox.__intervals = sandbox.__intervals || []).push({ ms }); return (sandbox.__intervals.length); },
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
    if (mode === "token") {
      const app = makeApp(
        { bridgeUrl: "http://127.0.0.1:8799" },
        { "kts-rebuild.token": "tok_SEEDSEED01" }
      );
      const plugin = new Main.default(app, { id: "kts-rebuild" });
      await plugin.onload();
      out.tokenFromLoad = plugin.token;
      out.statusAfterLoad = plugin._statusBar ? plugin._statusBar._text : null;
      const tab = plugin._settingTab;
      tab.display();
      out.settingNames = tab.containerEl._settings.map((s) => s.name);
      const tokenSetting = tab.containerEl._settings.find(
        (s) => s.name === "Device token"
      );
      await tokenSetting._text._onChange("tok_NEWNEWNEW01");
      out.lsAfter = JSON.stringify(app.__ls);
      out.dataAfter = JSON.stringify(app.__data);
      out.tokenBack = Main.loadToken(app);
      Main.saveToken(app, "tok_DIRECTDIR9");
      out.tokenDirect = Main.loadToken(app);
      out.bridgeUrlKept =
        JSON.parse(out.dataAfter || "{}").bridgeUrl || null;
    } else if (mode === "warn") {
      const app = makeApp({
        bridgeUrl: "http://127.0.0.1:8799",
        apiKey: "rk_live_ABCDEFG123456",
        nested: { note: "x" },
      }, {});
      const plugin = new Main.default(app, { id: "kts-rebuild" });
      await plugin.onload();
      out.warnText = warns.join(" || ");
      out.valueLeaked = warns.join(" ").includes("rk_live_");
      out.tokenKeys = Main.findTokenLikeKeys(
        JSON.parse(JSON.stringify(app.__data))
      );
    } else if (mode === "clean") {
      const app = makeApp({ bridgeUrl: "http://127.0.0.1:8799" }, {});
      const plugin = new Main.default(app, { id: "kts-rebuild" });
      await plugin.onload();
      out.warns = warns;
    } else if (mode === "offline") {
      const app = makeApp({ bridgeUrl: "http://127.0.0.1:8799" }, {});
      const plugin = new Main.default(app, { id: "kts-rebuild" });
      await plugin.onload();
      out.statusAfterLoad = plugin._statusBar ? plugin._statusBar._text : null;
      const view = new Main.TicketListView({}, plugin);
      await view.onOpen();
      out.viewTexts = flattenText(view.contentEl);
      out.offlineConst = Main.OFFLINE_MSG;
      out.statusAfterView = plugin._statusBar ? plugin._statusBar._text : null;
    }
    out.logs = logs;
    out.warns = warns;
    out.requests = requests;
    console.log("JSON:" + JSON.stringify(out));
    process.exit(0);
  } catch (e) {
    console.error("MODE_FAILED: " + ((e && e.stack) || e));
    process.exit(6);
  }
})();
"""


class TestH2c1Plugin(unittest.TestCase):
    def setUp(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node not on PATH")
        self.assertTrue(MAIN_JS.exists(), f"missing {MAIN_JS} - run build.sh")
        with tempfile.NamedTemporaryFile(
            "w", suffix=".js", delete=False
        ) as f:
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

    def test_token_storage_path(self):
        """Token round-trips through app.load/saveLocalStorage only;
        data.json (saveData) never receives it."""
        out = self._run("token")
        # read at onload from localStorage, not data.json
        self.assertEqual(out["tokenFromLoad"], "tok_SEEDSEED01")
        # settings tab structure: exactly the slice-1 two fields
        self.assertEqual(out["settingNames"], ["Bridge URL", "Device token", "Auto-refresh", "Refresh interval (ms)", "Theme"])
        # onChange wrote to localStorage
        self.assertEqual(out["tokenBack"], "tok_NEWNEWNEW01")
        self.assertIn("kts-rebuild.token", out["lsAfter"])
        self.assertIn("tok_NEWNEWNEW01", out["lsAfter"])
        # data.json kept bridgeUrl, contains no token at all
        self.assertNotIn("tok_", out["dataAfter"])
        self.assertEqual(out["bridgeUrlKept"], "http://127.0.0.1:8799")
        # direct helper round-trip
        self.assertEqual(out["tokenDirect"], "tok_DIRECTDIR9")
        # connected status bar after successful fetch
        self.assertIn("connected", out["statusAfterLoad"])
        # the fetch is GET /tickets with the token from localStorage
        reqs = out.get("requests") or []
        self.assertTrue(reqs, "expected the plugin to have issued a request")
        r0 = reqs[0]
        self.assertEqual(r0.get("method"), "GET")
        self.assertIn("/tickets", r0.get("url", ""))
        self.assertIn("all=true", r0.get("url", ""))
        self.assertEqual(
            (r0.get("headers") or {}).get("X-API-Token"), "tok_SEEDSEED01"
        )

    def test_data_json_token_warning(self):
        """Startup warning when a token-like value lands in data.json:
        names the key, never the value; clean data.json warns nothing."""
        out = self._run("warn")
        self.assertTrue(out["warnText"], "expected a startup warning")
        self.assertIn("data.json", out["warnText"])
        self.assertIn("apiKey", out["warnText"])
        self.assertFalse(
            out["valueLeaked"],
            "warning leaked the secret value: " + out["warnText"],
        )
        self.assertNotIn("rk_live_", out["warnText"])
        # the key-finder itself also returns keys, not values
        self.assertEqual(out["tokenKeys"], ["apiKey"])
        # negative control
        clean = self._run("clean")
        self.assertEqual(clean["warns"], [])

    def test_offline_state(self):
        """Bridge unreachable -> status bar offline + clear view message
        (OFF-1), no exception, no writes."""
        out = self._run("offline")
        self.assertIn("offline", out["statusAfterLoad"].lower())
        joined = " ".join(out["viewTexts"])
        self.assertIn("unreachable", joined.lower())
        self.assertIn("offline", joined.lower())
        self.assertIn("Read-only slice", joined)
        # status bar still offline after the view render
        self.assertIn("offline", out["statusAfterView"].lower())
        # the view logged its offline state
        self.assertTrue(
            any("view offline" in line for line in out["logs"]),
            "expected 'view offline' log, got: " + str(out["logs"]),
        )
        # source discipline: GET only, no write verbs anywhere
        src = MAIN_JS.read_text()
        self.assertIn('"GET"', src)
        for verb in ("POST", "PUT", "PATCH", "DELETE"):
            self.assertNotIn(f'"{verb}"', src)


if __name__ == "__main__":
    unittest.main()
