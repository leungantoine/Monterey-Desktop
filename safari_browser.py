"""A small, explicitly targeted Safari bridge using structured osascript argv.

This module does not enable Safari's JavaScript-from-Apple-Events preference,
activate Safari, create tabs, or select a different target when an identifier
is stale. Callers must choose a window and 1-based tab index from ``tabs()``.
"""
from __future__ import annotations

import json
import subprocess
import time
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
    var targetURL = String(tabs[tabIndex - 1].url());
    if (tabs.filter(function(t){return String(t.url()) === targetURL;}).length !== 1)
        throw new Error("Safari tab URL is ambiguous within this window. Use native controls or give the intended tab a distinct URL before browser scripting.");
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
    var targetURL = String(tabs[tabIndex - 1].url());
    if (tabs.filter(function(t){return String(t.url()) === targetURL;}).length !== 1)
        throw new Error("Safari tab URL is ambiguous within this window. Use native controls or give the intended tab a distinct URL before browser scripting.");
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

    def _run(self, source: str, *argv: str, timeout: float | None = None) -> str:
        command = [self.osascript, "-l", "JavaScript", "-e", source, *argv]
        limit = self.timeout if timeout is None else min(self.timeout, timeout)
        if limit <= 0:
            raise SafariBridgeError('Safari workflow deadline expired.')
        try:
            completed = self._runner(
                command,
                capture_output=True,
                text=True,
                timeout=limit,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise SafariBridgeError(
                f"Safari Apple Event timed out after {limit:g}s."
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

    def evaluate(self, window_id: int, tab_index: int, script: str, expected_url: str | None = None, *, timeout: float | None = None) -> Any:
        """Evaluate a JavaScript expression in the tab; return a JSON-safe result.

        This executes caller-provided page code, which can read or change that
        page. Wrap statement sequences in an IIFE. Callers must limit it to the
        user's authorized task. expected_url guards against reordered tabs.
        """
        if not isinstance(script, str) or not script.strip():
            raise ValueError("script must be a non-empty JavaScript string")
        args = self._target(window_id, tab_index)
        raw = self._run(_EVALUATE_SCRIPT, *args, script, json.dumps(expected_url), timeout=timeout)
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as error:
            raise SafariBridgeError('Safari returned no valid script result. Use a JavaScript expression; wrap statements in an IIFE: (() => { ...; return result; })(). Inspect the page before retrying; input may have partially run.') from error
        if not isinstance(result, dict) or not isinstance(result.get('ok'), bool):
            raise SafariBridgeError('Safari returned no valid script result. Use a JavaScript expression; wrap statements in an IIFE: (() => { ...; return result; })(). Inspect the page before retrying; input may have partially run.')
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

    def navigate(self, window_id: int, tab_index: int, url: str, expected_url: str | None = None, *, timeout: float | None = None) -> dict[str, Any]:
        """Navigate only the selected existing tab; never create or activate one."""
        parts = urlsplit(url) if isinstance(url, str) else None
        if parts is None or parts.scheme.lower() not in ("http", "https") or not parts.netloc:
            raise ValueError("url must be an http or https URL")
        args = self._target(window_id, tab_index)
        raw = self._run(_NAVIGATE_SCRIPT, *args, url, json.dumps(expected_url), timeout=timeout)
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as error:
            raise SafariBridgeError("Safari returned invalid navigation JSON.") from error
        if not isinstance(result, dict) or result.get("window_id") != window_id or result.get("tab_index") != tab_index:
            raise SafariBridgeError("Safari navigation response did not match the selected window and tab.")
        return result

    def navigate_read(self, window_id: int, tab_index: int, url: str, expected_url: str,
                      *, ready_selector: str | None = None, text_contains: str | None = None,
                      read_selector: str | None = None, timeout: float = 10,
                      check: Callable[[], None] = lambda: None) -> dict[str, Any]:
        """Navigate once, poll explicit readiness, then return scoped visible text.

        Each Apple Event rechecks the exact destination/duplicate URLs. Redirects
        or reordered targets fail; navigation is never retried after an error.
        The host's check callback revalidates pause/lock/window/Space every poll.
        """
        if not expected_url or not (ready_selector or text_contains):
            raise ValueError('navigate_read requires expected_url and a nonempty ready_selector or text_contains.')
        if not 0 < timeout <= 30:
            raise ValueError('navigate_read timeout must be >0 and ≤30 seconds.')
        self._target(window_id, tab_index)
        options=json.dumps({'url':url,'ready':ready_selector,'contains':text_contains,'scope':read_selector},ensure_ascii=False)
        script='''(() => {
            const o=OPTIONS;
            if(location.href!==o.url || document.readyState!=="complete" || !document.body)
                return {ready:false};
            const visible=selector=>Array.from(document.querySelectorAll(selector)).filter(e=>{
                const s=getComputedStyle(e);return e.getClientRects().length>0 && s.display!=="none" && s.visibility!=="hidden" && s.visibility!=="collapse";
            });
            if(o.ready){const nodes=visible(o.ready);if(nodes.length>1)return {ambiguous:true};if(nodes.length!==1)return {ready:false};}
            let root=document.body;
            if(o.scope){const nodes=visible(o.scope);if(nodes.length>1)return {ambiguous:true};if(nodes.length!==1)return {ready:false};root=nodes[0];}
            const text=root.innerText||"";
            if(o.contains && !text.includes(o.contains))return {ready:false};
            return {ready:true,url:location.href,title:document.title,ready_state:document.readyState,
                    text:text.slice(0,50000),total_chars:text.length,truncated:text.length>50000};
        })()'''.replace('OPTIONS',options)
        start=time.monotonic()
        deadline=start+timeout
        polls=0
        try:
            check()
            self.navigate(window_id,tab_index,url,expected_url,timeout=deadline-time.monotonic())
            while True:
                check()
                if time.monotonic()>=deadline:
                    raise TimeoutError('Expected page content did not become ready before the workflow deadline.')
                result=self.evaluate(window_id,tab_index,script,url,timeout=deadline-time.monotonic())
                polls+=1
                if not isinstance(result,dict):
                    raise SafariBridgeError('Unexpected navigate_read result.')
                if result.get('ambiguous'):
                    raise ValueError('Browser readiness/read selector is ambiguous; provide a unique visible selector.')
                if result.get('ready') is True:
                    check()
                    return {**result,'workflow':{'completed_steps':['navigate','wait','read'],
                            'polls':polls,'elapsed_ms':round((time.monotonic()-start)*1000)}}
                time.sleep(min(.02,max(0,deadline-time.monotonic())))
        except Exception as error:
            raise SafariBridgeError(f'Browser workflow stopped; navigation may have run. Inspect before retrying. {error}') from error

    def act_read(self, window_id, tab_index, steps, expected_url, *, ready_selector=None,
                 text_contains=None, read_selector=None, timeout=10, check=lambda:None):
        """Run explicit DOM actions once, wait for content, and read without agent polls."""
        if not expected_url or not (ready_selector or text_contains):
            raise ValueError('act_read requires expected_url and ready_selector or text_contains.')
        if not 0<timeout<=30 or not 1<=len(steps)<=20:
            raise ValueError('act_read requires 1–20 steps and a timeout >0 and ≤30s.')
        # Bridge callers receive the same whole-shape validation as MCP callers.
        for step in steps:
            if (step.get('kind') not in ('click','replace_text','wait_for')
                    or not isinstance(step.get('selector'),str) or not step['selector'].strip()
                    or set(step)-{'kind','selector','text','text_contains'}):
                raise ValueError('Invalid DOM step; use click/replace_text/wait_for and a nonempty CSS selector.')
            if step['kind']=='replace_text' and not isinstance(step.get('text'),str):
                raise ValueError('replace_text requires text, including empty text for clearing.')
            if step['kind']!='replace_text' and step.get('text') is not None:
                raise ValueError('Only replace_text accepts text.')
            if step['kind']!='wait_for' and step.get('text_contains') is not None:
                raise ValueError('Only wait_for accepts text_contains.')
        self._target(window_id,tab_index)
        start=time.monotonic();deadline=start+timeout;completed=0;polls=0
        source='''(() => {
            const o=OPTIONS;
            let next=o.start;
            const visible=selector=>Array.from(document.querySelectorAll(selector)).filter(e=>{
                const s=getComputedStyle(e);return e.getClientRects().length>0 && s.display!=="none" && s.visibility!=="hidden" && s.visibility!=="collapse";
            });
            const one=selector=>{const a=visible(selector);if(a.length>1)throw Error("Ambiguous visible selector: "+selector);return a[0];};
            try {
                for(;next<o.steps.length;next++){
                    if(location.href!==o.url)throw Error("Target URL changed during DOM workflow");
                    if(Date.now()>=o.deadline)throw Error("DOM workflow deadline expired");
                    const step=o.steps[next],el=one(step.selector);
                    if(!el || el.disabled || el.getAttribute("aria-disabled")==="true")return {next,ready:false};
                    if(step.kind==="wait_for"){
                        if(step.text_contains && !(el.innerText||el.value||"").includes(step.text_contains))return {next,ready:false};
                    }else if(step.kind==="click"){
                        el.click();
                    }else{
                        if(el.readOnly)throw Error("Target is read-only");
                        const proto=el instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:
                            el instanceof HTMLInputElement?HTMLInputElement.prototype:null;
                        if(!proto || (el instanceof HTMLInputElement && !["text","search","email","url","tel","password","number","date","datetime-local","time","month","week"].includes(el.type)))
                            throw Error("replace_text requires an editable input or textarea");
                        Object.getOwnPropertyDescriptor(proto,"value").set.call(el,step.text);
                        el.dispatchEvent(new Event("input",{bubbles:true}));
                        el.dispatchEvent(new Event("change",{bubbles:true}));
                        const current=one(step.selector);
                        if(!current || current.value!==step.text)throw Error("Replacement value was not observed; do not replay");
                    }
                }
                if(location.href!==o.url)throw Error("Target URL changed during DOM workflow");
                if(Date.now()>=o.deadline)throw Error("DOM workflow deadline expired");
                if(document.readyState!=="complete" || !document.body)return {next,ready:false};
                if(o.ready && !one(o.ready))return {next,ready:false};
                const root=o.scope?one(o.scope):document.body;
                if(!root)return {next,ready:false};
                const text=root.innerText||"";
                if(o.contains && !text.includes(o.contains))return {next,ready:false};
                return {next,ready:true,text:text.slice(0,50000),total_chars:text.length,truncated:text.length>50000,
                        url:location.href,title:document.title,ready_state:document.readyState};
            }catch(error){return {next,failure:String(error)};}
        })()'''
        try:
            while True:
                check()
                if time.monotonic()>=deadline:
                    raise TimeoutError('Expected DOM controls/content did not become ready before the workflow deadline.')
                options={'steps':steps,'start':completed,'ready':ready_selector,'contains':text_contains,'scope':read_selector,
                         'url':expected_url,'deadline':int(time.time()*1000+(deadline-time.monotonic())*1000)}
                script=source.replace('OPTIONS',json.dumps(options,ensure_ascii=False))
                result=self.evaluate(window_id,tab_index,script,expected_url,timeout=deadline-time.monotonic())
                polls+=1
                if not isinstance(result,dict) or not isinstance(result.get('next'),int) or not completed<=result['next']<=len(steps):
                    raise SafariBridgeError('Unexpected DOM workflow result; the current step may have run.')
                completed=result.pop('next')
                if result.get('failure'):
                    raise SafariBridgeError(result['failure'])
                if result.get('ready') is True:
                    check()
                    return {**result,'workflow':{'completed_steps':completed,'polls':polls,'elapsed_ms':round((time.monotonic()-start)*1000)}}
                time.sleep(min(.02,max(0,deadline-time.monotonic())))
        except Exception as error:
            raise SafariBridgeError(f'DOM workflow stopped after {completed} completed steps; the current step may also have partially run. Inspect before retrying; never replay the whole workflow. {error}') from error
