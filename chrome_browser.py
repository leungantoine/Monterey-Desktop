"""Google Chrome Apple Events adapter for the existing, regular browser profile.

Chrome executes Apple Events JavaScript in an isolated context with DOM access;
page-defined globals belong to the page’s separate main context.
Never enables scripting, activates the app, creates tabs, or falls back to a
different target. The shared bridge supplies serialization and DOM workflows.
"""
from browser_bridge import AppleEventBrowser, BrowserBridgeError

ChromeBridgeError = BrowserBridgeError

_TABS_SCRIPT = r'''function run(argv) {
    var chrome = Application("Google Chrome");
    var result = [];
    var windows = chrome.windows();
    for (var wi = 0; wi < windows.length; wi++) {
        var w = windows[wi];
        var tabs = w.tabs();
        if (!tabs || tabs.length === undefined) continue;
        var selected = Number(w.activeTabIndex());
        var windowResult = {window_id: Number(w.id()), title: String(w.name()),
                            bounds: w.bounds(), tabs: []};
        for (var ti = 0; ti < tabs.length; ti++) {
            var t = tabs[ti];
            windowResult.tabs.push({tab_index: ti + 1, url: String(t.url()),
                title: String(t.title()), selected: selected === ti + 1});
        }
        result.push(windowResult);
    }
    return JSON.stringify(result);
}'''

# Used by both mutation scripts, so the target checks cannot drift apart.
_TARGET_GUARD = r'''
    var windowId = Number(argv[0]);
    var tabIndex = Number(argv[1]);
    var expectedURL = JSON.parse(String(argv[3]));
    var chrome = Application("Google Chrome");
    var windows = chrome.windows().filter(function (w) {
        return Number(w.id()) === windowId;
    });
    if (windows.length !== 1) throw new Error("Chrome window id is missing or ambiguous: " + windowId);
    var tabs = windows[0].tabs();
    if (!Number.isInteger(tabIndex) || tabIndex < 1 || tabIndex > tabs.length)
        throw new Error("Chrome tab index is out of range for window " + windowId + ": " + tabIndex);
    if (expectedURL !== null && String(tabs[tabIndex - 1].url()) !== expectedURL)
        throw new Error("Chrome tab URL changed; list tabs before continuing.");
    var targetURL = String(tabs[tabIndex - 1].url());
    if (tabs.filter(function(t){return String(t.url()) === targetURL;}).length !== 1)
        throw new Error("Chrome tab URL is ambiguous within this window. Use native controls or give the intended tab a distinct URL before browser scripting.");
'''

_EVALUATE_SCRIPT = 'function run(argv) {' + _TARGET_GUARD + r'''
    var source = String(argv[2]).trim().replace(/;+\s*$/, "");
    var wrapped = "(function(){try{var value=(\n" + source +
                  "\n);return JSON.stringify({ok:true,value:value===undefined?null:value});}" +
                  "catch(error){return JSON.stringify({ok:false,error:String(error),name:error.name});}})()";
    var value = tabs[tabIndex - 1].execute({javascript: wrapped});
    return value === undefined ? "null" : String(value);
}'''

_NAVIGATE_SCRIPT = 'function run(argv) {' + _TARGET_GUARD + r'''
    var destination = String(argv[2]);
    if (!/^https?:\/\//i.test(destination))
        throw new Error("Only http and https URLs can be opened in the selected Chrome tab.");
    tabs[tabIndex - 1].url = destination;
    return JSON.stringify({window_id: windowId, tab_index: tabIndex,
                           url: String(tabs[tabIndex - 1].url())});
}'''


class ChromeBrowser(AppleEventBrowser):
    """Target existing Google Chrome tabs with the same workflows as Safari."""

    name = "Google Chrome"
    javascript_setting = "Chrome’s View > Developer menu"
    tabs_script = _TABS_SCRIPT
    evaluate_script = _EVALUATE_SCRIPT
    navigate_script = _NAVIGATE_SCRIPT
