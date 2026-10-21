"""A small, explicitly targeted Safari bridge using structured osascript argv.

This module does not enable Safari's JavaScript-from-Apple-Events preference,
activate Safari, create tabs, or select a different target when an identifier
is stale. Callers must choose a window and 1-based tab index from ``tabs()``.
"""
from __future__ import annotations

from browser_bridge import AppleEventBrowser, BrowserBridgeError

# Retain the public error type for existing callers.
SafariBridgeError = BrowserBridgeError


_TABS_SCRIPT = r'''function run(argv) {
    var safari = Application("Safari");
    var result = [];
    var windows = safari.windows();
    for (var wi = 0; wi < windows.length; wi++) {
        var w = windows[wi];
        var tabs = w.tabs();
        if (!tabs || tabs.length === undefined) continue;
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


class SafariBrowser(AppleEventBrowser):
    """Target existing Safari tabs using the shared guarded browser workflows."""

    name = "Safari"
    javascript_setting = "Safari’s Develop menu"
    tabs_script = _TABS_SCRIPT
    evaluate_script = _EVALUATE_SCRIPT
    navigate_script = _NAVIGATE_SCRIPT
