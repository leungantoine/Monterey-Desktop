"""A small, explicitly targeted Safari bridge using structured osascript argv.

This module does not enable Safari's JavaScript-from-Apple-Events preference,
activate Safari, create tabs, or select a different target when an identifier
is stale. Callers must choose a window and 1-based tab index from ``tabs()``.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any, Callable
from urllib.parse import urlsplit


class SafariBridgeError(RuntimeError):
    """Safari could not perform a requested, explicitly targeted operation."""


_TABS_SCRIPT = r'''function run(argv) {
    var safari = Application("Safari");
    var result = [];
    var windows = safari.windows();
    for (var wi = 0; wi < windows.length; wi++) {
        var w = windows[wi];
        var tabs = w.tabs();
        var currentIndex = null;
        try { currentIndex = Number(w.currentTab().index()); } catch (_) {}
        var windowResult = {window_id: Number(w.id()), title: String(w.name()),
                            bounds: w.bounds(), tabs: []};
        for (var ti = 0; ti < tabs.length; ti++) {
            var t = tabs[ti];
            var index = Number(t.index());
            windowResult.tabs.push({
                tab_index: index,
                url: String(t.url()),
                title: String(t.name()),
                selected: currentIndex === index
            });
        }
        result.push(windowResult);
    }
    return JSON.stringify(result);
}'''

_EVALUATE_SCRIPT = r'''function run(argv) {
    var windowId = Number(argv[0]);
    var tabIndex = Number(argv[1]);
    var source = String(argv[2]);
    var expectedURL = JSON.parse(String(argv[3]));
    var safari = Application("Safari");
    var windows = safari.windows().filter(function (w) {
        return Number(w.id()) === windowId;
    });
    if (windows.length !== 1) throw new Error("Safari window id is missing or ambiguous: " + windowId);
    var tabs = windows[0].tabs();
    if (!Number.isInteger(tabIndex) || tabIndex < 1 || tabIndex > tabs.length)
        throw new Error("Safari tab index is out of range for window " + windowId + ": " + tabIndex);
    if (expectedURL !== null && String(tabs[tabIndex - 1].url()) !== expectedURL)
        throw new Error("Safari tab URL changed; list tabs before continuing.");
    // Serialize inside the page: Apple Events do not reliably transport page
    // objects. Execute the expression directly, without CSP-sensitive eval.
    // Wrap statement sequences in an IIFE: (() => { ...; return result; })().
    source = source.trim().replace(/;+\s*$/, "");
    var wrapped = "(function(){try{var value=(\n" + source +
                  "\n);return JSON.stringify({ok:true,value:value===undefined?null:value});}" +
                  "catch(error){return JSON.stringify({ok:false,error:String(error),name:error.name});}})()";
    var value = safari.doJavaScript(wrapped, {in: tabs[tabIndex - 1]});
    return value === undefined ? "null" : String(value);
}'''

_NAVIGATE_SCRIPT = r'''function run(argv) {
    var windowId = Number(argv[0]);
    var tabIndex = Number(argv[1]);
    var destination = String(argv[2]);
    var expectedURL = JSON.parse(String(argv[3]));
    var safari = Application("Safari");
    var windows = safari.windows().filter(function (w) {
        return Number(w.id()) === windowId;
    });
    if (windows.length !== 1) throw new Error("Safari window id is missing or ambiguous: " + windowId);
    var tabs = windows[0].tabs();
    if (!Number.isInteger(tabIndex) || tabIndex < 1 || tabIndex > tabs.length)
        throw new Error("Safari tab index is out of range for window " + windowId + ": " + tabIndex);
    if (expectedURL !== null && String(tabs[tabIndex - 1].url()) !== expectedURL)
        throw new Error("Safari tab URL changed; list tabs before continuing.");
    if (!/^https?:\/\//i.test(destination))
        throw new Error("Only http and https URLs can be opened in the selected Safari tab.");
    tabs[tabIndex - 1].url = destination;
    return JSON.stringify({window_id: windowId, tab_index: tabIndex,
                           url: String(tabs[tabIndex - 1].url())});
}'''


class SafariBrowser:
    """List tabs and navigate/evaluate JavaScript in an exact Safari tab.

    Dynamic values are always passed as ``osascript`` arguments rather than
    interpolated into AppleScript/JXA source. ``runner`` is injectable for
    offline tests; production callers should use the default subprocess runner.
    """

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        osascript: str = "/usr/bin/osascript",
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.timeout = timeout
        self.osascript = osascript
        self._runner = runner or subprocess.run

    def _run(self, source: str, *argv: str) -> str:
        command = [self.osascript, "-l", "JavaScript", "-e", source, *argv]
        try:
            completed = self._runner(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise SafariBridgeError(
                f"Safari Apple Event timed out after {self.timeout:g}s."
            ) from error
        except OSError as error:
            raise SafariBridgeError(f"Could not start osascript: {error}") from error
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode:
            detail = (stderr or stdout).strip()
            hint = ""
            lowered = detail.lower()
            if "javascript" in lowered and ("disabled" in lowered or "not allowed" in lowered):
                hint = " Safari JavaScript from Apple Events may be disabled."
            elif "not authorized" in lowered or "not permitted" in lowered:
                hint = " Allow the requesting app to control Safari in macOS Automation settings."
            raise SafariBridgeError(
                f"Safari Apple Event failed (exit {completed.returncode}): "
                f"{detail or 'no error details'}{hint}"
            )
        return stdout.strip()

    @staticmethod
    def _target(window_id: int, tab_index: int) -> tuple[str, str]:
        if isinstance(window_id, bool) or not isinstance(window_id, int) or window_id <= 0:
            raise ValueError("window_id must be a positive Safari window id from tabs()")
        if isinstance(tab_index, bool) or not isinstance(tab_index, int) or tab_index <= 0:
            raise ValueError("tab_index must be a 1-based index from tabs()")
        return str(window_id), str(tab_index)

    def tabs(self) -> list[dict[str, Any]]:
        """Return Safari windows and tabs without requiring page JavaScript."""
        raw = self._run(_TABS_SCRIPT)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise SafariBridgeError("Safari returned invalid tab metadata JSON.") from error
        if not isinstance(value, list):
            raise SafariBridgeError("Safari returned an unexpected tab metadata value.")
        return value

    def evaluate(self, window_id: int, tab_index: int, script: str, expected_url: str | None = None) -> Any:
        """Evaluate a JavaScript expression in the tab; return a JSON-safe result.

        This executes caller-provided page code, which can read or change that
        page. Wrap statement sequences in an IIFE. Callers must limit it to the
        user's authorized task. expected_url guards against reordered tabs.
        """
        if not isinstance(script, str) or not script.strip():
            raise ValueError("script must be a non-empty JavaScript string")
        args = self._target(window_id, tab_index)
        raw = self._run(_EVALUATE_SCRIPT, *args, script, json.dumps(expected_url))
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as error:
            raise SafariBridgeError('Safari returned no valid script result; inspect the page before retrying.') from error
        if not isinstance(result, dict) or not isinstance(result.get('ok'), bool):
            raise SafariBridgeError('Safari returned no valid script result; inspect the page before retrying.')
        if not result['ok']:
            raise SafariBridgeError(f"Page JavaScript failed: {result.get('error', 'unknown script error')}. The script may have partially run; inspect before retrying.")
        return result.get('value')

    def read_dom(self, window_id: int, tab_index: int, expected_url: str | None = None) -> dict[str, Any]:
        """Read bounded visible page text and document metadata from one tab."""
        result = self.evaluate(
            window_id,
            tab_index,
            """(() => {
                const text = document.body ? (document.body.innerText || "") : "";
                const limit = 50000;
                return {
                    url: location.href,
                    title: document.title,
                    ready_state: document.readyState,
                    text: text.slice(0, limit),
                    total_chars: text.length,
                    truncated: text.length > limit
                };
            })()""",
            expected_url,
        )
        if not isinstance(result, dict):
            raise SafariBridgeError("Safari returned an unexpected DOM text result.")
        return result

    def collect_links(self, window_id: int, tab_index: int, expected_url: str | None = None) -> dict[str, Any]:
        """Collect up to 1,000 page anchors with URL and link text."""
        result = self.evaluate(
            window_id,
            tab_index,
            """(() => {
                const all = Array.from(document.querySelectorAll("a[href]"));
                const links = all.slice(0, 1000).map(a => ({
                    url: a.href,
                    text: (a.innerText || a.getAttribute("aria-label") || a.title || "").trim(),
                    title: a.title || ""
                }));
                return {links, total_links: all.length, truncated: all.length > links.length};
            })()""",
            expected_url,
        )
        if not isinstance(result, dict) or not isinstance(result.get("links"), list):
            raise SafariBridgeError("Safari returned an unexpected link collection result.")
        return result

    def navigate(self, window_id: int, tab_index: int, url: str, expected_url: str | None = None) -> dict[str, Any]:
        """Navigate only the selected existing tab; never create or activate one."""
        parts = urlsplit(url) if isinstance(url, str) else None
        if parts is None or parts.scheme.lower() not in ("http", "https") or not parts.netloc:
            raise ValueError("url must be an http or https URL")
        args = self._target(window_id, tab_index)
        raw = self._run(_NAVIGATE_SCRIPT, *args, url, json.dumps(expected_url))
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as error:
            raise SafariBridgeError("Safari returned invalid navigation JSON.") from error
        if not isinstance(result, dict) or result.get("window_id") != window_id or result.get("tab_index") != tab_index:
            raise SafariBridgeError("Safari navigation response did not match the selected window and tab.")
        return result
