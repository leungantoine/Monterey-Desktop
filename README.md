# Monterey Desktop

A personal Codex plugin for this Intel Mac running macOS Monterey. It bundles a local stdio MCP server and a skill for observing the primary screen and using native mouse and keyboard input. The server runs only while connected; it has no listening network port or continuous recording.

## Quick start from GitHub

This repository contains version 1.8.0 of the local Codex plugin. It was tested on an Intel Mac running macOS Monterey 12.7.6 with Python 3.12 and a Codex CLI that supports plugins. Background input uses Monterey-specific private APIs; newer macOS versions and other apps have not been verified.

Create the shared Python environment and install the pinned dependencies:

```sh
git clone https://github.com/leungantoine/Monterey-Desktop.git
cd Monterey-Desktop
mkdir -p "$HOME/.local/share/monterey-desktop"
python3.12 -m venv "$HOME/.local/share/monterey-desktop/.venv"
"$HOME/.local/share/monterey-desktop/.venv/bin/python" -m pip install --only-binary=:all: -r requirements.txt
codex plugin marketplace add leungantoine/Monterey-Desktop
codex plugin add monterey-desktop@monterey-desktop
```

Start a new Codex session. Grant Screen Recording and Accessibility to the process hosting the helper when macOS requests them. Safari page scripting also needs **Allow JavaScript from Apple Events** in Safari's developer settings and macOS Automation permission. Enabling that preference can require local Touch ID or password authentication.

The GitHub marketplace is named `monterey-desktop`; its installed copy is under `~/.codex/plugins/cache/monterey-desktop/monterey-desktop/1.8.0/`. The launcher uses the shared environment above. The locations and `install.command` below describe the original author's `personal-local` installation. That installer expects the personal catalog and source location; use the quick start above for a fresh GitHub installation.

Run the offline checks from the cloned repository:

```sh
"$HOME/.local/share/monterey-desktop/.venv/bin/python" verify_browser.py
"$HOME/.local/share/monterey-desktop/.venv/bin/python" verify_feedback.py
"$HOME/.local/share/monterey-desktop/.venv/bin/python" verify_native_wait.py
"$HOME/.local/share/monterey-desktop/.venv/bin/python" verify_focus_guards.py
"$HOME/.local/share/monterey-desktop/.venv/bin/python" verify_workflows.py
```

Live desktop tests create temporary fixtures and interact with Safari. Their details and limitations are documented below.

## Locations

- Editable plugin source: `~/.codex/plugins/monterey-desktop/`.
- Bundled instructions: `skills/monterey-desktop/SKILL.md`.
- Personal marketplace: `~/.agents/plugins/marketplace.json`, named `personal-local`.
- Installed copy: `~/.codex/plugins/cache/personal-local/monterey-desktop/1.8.0/`.
- Python environment and shared pause state: `~/.local/share/monterey-desktop/`.

Codex loads its installed copy. Edit the source, then run the plugin add command below to refresh that copy. The virtual environment stays outside the plugin cache so reinstalls do not relocate Python or duplicate dependencies.

## Use

Start a new Codex session after installation. Ask: “Use Monterey Desktop to open Safari and go to Google Classroom.” The skill can be selected explicitly or discovered for local desktop tasks.

Version 1.8.0 reduces agent round trips for complete tasks. `desktop_browser(operation="navigate_read")` navigates once, waits for an explicit visible selector/text condition, and returns scoped text in one call. New `desktop_plan` resolves unique native selectors afresh between UI changes, allowing newly revealed controls to be filled or pressed without intermediate observations. Plans require an exact pinned window, stop on guard/ambiguity/deadline errors, report partial completion, and never replay input. They support foreground and explicit background modes, with one final reading/control response. Use only already authorized steps and finish with an expected outcome condition. See [workflow examples](skills/monterey-desktop/references/workflows.md).

The complete fixture benchmarks reduced median browser calls from 3 to 1 and native calls from 2 to 1 against the previous release’s best batching/combined reading; native response text fell by 54%. Browser local time was about 0.61 seconds. Native plans took about 1.4 seconds longer locally, so their benefit depends on removing agent round trips and repeated context. Prefer ordinary ID batches for already exposed controls. End-to-end model time was not measured. Detailed samples and limitations appear in [workflow-results.json](workflow-results.json) and below.

Version 1.7.0 speeds up agent workflows on Monterey. After tab discovery, supply `expected_url` to use one Apple Event per browser operation rather than rediscovering all Safari tabs. Live Space/window, URL, tab-range and duplicate-URL checks still run; permissions, pause/lock state and page content are not cached. Native Unicode typing uses shorter event delays, and per-chunk foreground guards query only the selected window while checking fresh focus/ownership/geometry. Tool descriptions and the skill entrypoint are shorter and recommend batching, filtered native feedback, and one-call open/read. Benchmarks and test scope are recorded in `performance-results.json`; they measure local MCP latency, not total model/task latency.

Version 1.6.2 fixes two Monterey reliability defects. Browser operations refuse a target whose URL is shared by another tab in the same window, both before dispatch and inside the Apple Event, because an index/URL pair cannot distinguish those tabs after reordering. Use native controls for duplicate-URL tabs, or give the intended tab a distinct URL using an authorized task. Native `wait_for(value_contains=...)` searches the full live value rather than its 2,000-character observation preview. Secure fields are excluded from value matching, and returned previews remain bounded. These fixes do not add support for other macOS versions.

Version 1.6.1 reports page JavaScript exceptions explicitly, including partial script failures. Live browser checks verify DOM text, links, exact-tab navigation, Unicode input/clicks, wrong-target guards, and output truncation in an owned localhost fixture.

Version 1.6 adds `desktop_browser`: list exact Safari tabs, navigate within the same tab, read DOM text/links, and evaluate caller JavaScript expressions (including IIFEs for statement sequences) for queries, fills, clicks, or batched page operations. It uses the existing signed-in profile and structured Apple Event arguments, without opening another browser profile or activating Safari. The tool defaults to the active Space; explicit all-Space scope supports other Spaces. Optional `expected_url` rejects a changed or reordered tab; the enumerated URL is also checked inside the Apple Event immediately before execution. Arbitrary evaluation/navigation can mutate pages and invalidates desktop frames; obtain a new frame before native/coordinate input. Scripts run synchronously; start and poll an in-page job for async work. Output is bounded with explicit truncation, and larger limits/queries are available. Safari must allow JavaScript from Apple Events; macOS may require local Touch ID/password authentication to enable it. Browser operation choices remain available alongside native and visual control.

Version 1.5 reduces model context through compact responses by default, whitespace-free Unicode JSON, bounded control text, and omission of repeated inventories after actions. `detail="full"` restores the original output. Compact control `rect` is `[x,y,width,height]` in screenshot pixels; retained IDs remain fresh and actionable. `ui_query` filters this snapshot by a case-insensitive label/role/value substring. Counts expose omitted nodes; `ui.truncated` still exposes incomplete native traversal. Full internal native state is retained for safety guards and state comparisons.

Observation is native-first by default: useful AX controls/text produce no screenshot; absent native access triggers visual fallback. Explicit `include_image=true` requests visual output and `false` forces native-only output. Native `press`/focus/value/readiness compact batches use the same native-first feedback and skip visual settling by default. Explicit image/timing settings are honored. Other actions retain the observed policy and .25-second default settling. Coordinate input in either mode requires a fresh screenshot. `replace_text` focuses an observed editable control, selects all, types the new value (or deletes it for empty text), and verifies the live nonsecure value. It sends input events, unlike direct `set_value`. Already-focused text controls preserve selection during explicit `type`/`key` targeting. Partial replacement failures are reported and never automatically replayed.

`desktop_read` extracts selected-window native text without screenshots, inventories, geometry or capability queries. It automatically scopes to a unique Safari web area, includes long exposed text and image descriptions, omits secure values, and supports pagination over the captured text. `desktop_act(feedback="text")` combines an opening action/readiness condition with reading and fresh actionable controls in one call. This is intended for workflows such as Safari Gmail/Outlook; it cannot read collapsed/unexposed content and reports incomplete traversal. Use explicit `element_id` to scope a read to an observed message container. No browser automation preference or DOM-script permission is required. Workflow guidance is advisory; both native and visual control, full feedback, batching and explicit timings remain accessible.

The skill entrypoint is shorter; mode-specific background instructions load only when needed. These changes reduce text and avoid some image inputs, but actual billed tokens depend on the model and chosen workflow. Raw metadata size and live fixture results are recorded in `verification-results.json`.

Version 1.4 supports background work in another macOS Space. `desktop_observe` defaults to `space_scope="active"`, which discovers and targets only the currently active Space. Set `space_scope="all"` explicitly to discover other Spaces, then observe the intended `window_id` with that same scope and use `desktop_act(mode="background")`. Each action result preserves the observation's scope. No Space switch is requested. `space_info` exposes active/visible Space IDs, and each window exposes `space_ids` and `on_active_space`. IDs and window membership are authoritative; a Space ID or its generated label is not a Mission Control “Desktop N” number.

Active-scope frames stop if you switch Spaces. All-scope background batches remain pinned to their selected window while you use another Space/app; they stop if that window moves to another Space, changes geometry, is hidden/minimized, or disappears. The helper retains verified native AX window handles across Space changes. macOS sometimes omits unseen other-Space windows from Accessibility: in that case `ui.available=false` explains the limit and the screenshot remains available. Visual typing requires an explicit field click earlier in the same batch; its effect remains unverified until checked through returned state or another observation. Native controls remain preferred whenever available. Default foreground activation refuses apps whose windows are only on another Space; opening a URL creates a regular Safari document on the active Space.

Version 1.3 added explicit `desktop_act(mode="background")`: selected-window clicks, movement, dragging, scrolling, field focus, Unicode typing, and shortcuts while you use another app. The backend targets the app process and window without global input or requesting app activation. It yields when you bring the target app forward, the Mac locks, or the selected window moves, resizes, or disappears. `desktop_status.background_input` reports availability. Background transport uses private WindowServer APIs and is enabled only on macOS 12; individual apps can reject it. Each action reports its delivery and observed effect. Unverified input is never automatically retried through the foreground.

Background work can target a window behind another app or on another Space with explicit all-Space scope. Minimized/hidden windows and locked operation remain unsupported. This release has no live picture-in-picture viewer or per-app permission list. Cross-Space input and fresh screenshots were verified in Safari on this Mac; other apps and fullscreen/multiple-display configurations have not been verified.

Version 1.2 added exact `window_id` targeting, supported AX actions and writable-value hints, a same-app window focus guard, and bounded `state_changes` in action results. Native element identity is preserved across snapshots, so recycled IDs and duplicate labels do not imply the same target. Changes are evidence rather than proof of task completion; an accepted action without an observed change must not be blindly repeated. The result reports incomplete snapshot coverage and caps detailed changes at 20. [Research notes](references/open-source-inspiration.md) explain the open-source projects that informed these versions.

Version 1.1 added native Accessibility control IDs, verified field writes, explicit app activation, readiness polling, and selected-window screenshots. `desktop_observe(app_pid=..., max_width=2000)` captures a target window in greater detail; `include_image=false` returns structured controls for semantic workflows. In-memory CoreGraphics capture replaces temporary screenshot files. Read and action tools have explicit MCP annotations.

The plugin exposes `desktop_status`, `desktop_observe`, `desktop_act`, `desktop_plan`, `desktop_read`, `desktop_browser`, and `desktop_pause`. Observe, use fresh native IDs or explicit screenshot coordinates with the returned `frame_id`, and inspect the resulting state. It supports clicks, mouse movement, dragging, Unicode typing, shortcuts, scrolling, waits, and opening http/https URLs in regular Safari with its existing profile. Screenshot width defaults to 1440 pixels and can be increased to 2880. Window offsets and Retina scaling are handled automatically.

The default `mode="foreground"` operates the real foreground desktop and stops when the expected app loses focus, another window of the same app gains focus, or a selected capture window moves/resizes. In that mode, `focus` raises the control's window and `set_value` may activate it if necessary. Use `mode="background"` explicitly for every batch while the user works in another app. Observe an exact `window_id` first; supply the observed field's `element_id` to `type` or `key` for precise text targeting. Background `focus` uses a targeted click; observed secure controls, `activate`, and `open_url` are refused. Background value writes cannot activate the app as a fallback. A window on another Space requires background mode.

A selected app window can be captured independently of the primary-display image. The helper matches native AX window IDs to CoreGraphics IDs, including overlapping windows. If native IDs are unavailable, matching falls back to unique bounds and refuses ambiguity. Run one assistant desktop workflow at a time. Screen Recording and Accessibility must be granted to the hosting process. In this SSH setup they were granted to `sshd-keygen-wrapper`; another launch context may need its own permissions.

## Pause

Run `Pause.command` in this source folder to block screenshots and input, or ask the assistant to call `desktop_pause`. Run `Resume.command` locally to resume. Both scripts and all installed copies use `~/.local/share/monterey-desktop/.paused`. Pausing is checked between actions and during waits; an in-progress drag releases the mouse button.

The optional `MONTEREY_DESKTOP_DATA_DIR` environment variable changes the data directory for the launcher and local controls. Use the same value for both if customizing it.

## Install, refresh, and remove

Python 3.12 and Codex are already installed on this Mac. `install.command` installs the pinned dependencies into the stable virtual environment, registers the personal marketplace, installs or refreshes the plugin, saves tool approval settings for this explicitly installed local helper, and removes the old standalone MCP connection if present. It expects this source folder and the existing `personal-local` catalog entry to remain in their documented locations.

For a source-only refresh, use:

```sh
~/.local/bin/codex plugin add monterey-desktop@personal-local
```

Then start a new session. Do not edit the generated installed cache.

The installer saves `default_tools_approval_mode = "approve"` under `[plugins."monterey-desktop@personal-local".mcp_servers.monterey-desktop]`. This allows this trusted local helper to run without interactive tool prompts, including under a session that cannot show approval prompts. Actions still follow the user’s task authorization and macOS permissions. A one-time config backup is kept beside `config.toml`; change this plugin setting to `prompt` to require individual tool approvals.

Remove the plugin connection with:

```sh
~/.local/bin/codex plugin remove monterey-desktop@personal-local
```

Removal leaves the source and shared Python environment in place. macOS permissions can be changed separately in System Preferences → Security & Privacy → Privacy.

## Local diagnostics

```sh
~/.local/share/monterey-desktop/.venv/bin/python ~/.codex/plugins/monterey-desktop/desktop.py status
~/.local/share/monterey-desktop/.venv/bin/python ~/.codex/plugins/monterey-desktop/desktop.py observe --output /tmp/desktop.jpg
```

`verify_workflows.py` runs offline workflow failure, fresh-target, complete-shape validation and exact-window/Space regression tests. `verify_workflows_live.py` compares split operations with the composite browser/native workflows through actual MCP launchers on owned localhost fixtures. It verifies delayed content, newly revealed fields, Unicode input callbacks, completed outcome text, secure omission, and partial-plan no-replay/frame invalidation. It closes its fixture and restores the prior foreground app/window. Use `--baseline-root <v1.7.0-directory> --root <installed-directory> --output <results.json>` to reproduce the comparison.

`verify_speed.py` benchmarks guarded Safari reads/evaluations and 800-character Unicode replacement through the actual MCP launcher. It verifies complete field values and input callbacks, closes its owned fixture, and restores the prior foreground app/window. Use `--root` to compare another plugin directory and `--output` to save results. `verify_focus_guards.py` checks fresh selected-window geometry/owner/focus and Space/takeover refusal without input.

`verify_efficiency.py` checks compact/full compatibility, fresh filtered IDs, Unicode/empty replacement, selection preservation, native-first screenshots, full long message text and image descriptions, secure omission, pagination, and one-call open/read against disposable localhost Safari fixtures. It restores the foreground and closes its fixtures afterward. Raw text-size measurements compare the same native metadata; local action timings exclude model/network latency.

`verify_browser.py` tests browser argument handling, target validation, duplicate-URL refusal at both the MCP and Apple Event boundaries, JSON results, timeouts and errors without sending Safari events. `verify_native_wait.py` tests long-value readiness, suffix disambiguation, incomplete-tree refusal and secure-field exclusion using mocked Accessibility calls without input. Its runtime check executes JXA with a mocked application. `verify_browser_live.py` checks the installed browser tools against an owned localhost Safari window and closes it afterward. It requires Safari scripting permission.

The optional `verify.py` is a live end-to-end test using a temporary localhost Safari fixture. It exercises clicks, Unicode typing, shortcuts, dragging, scrolling, stale-frame rejection, batch validation, pause/resume, named control presses, verified field edits, delayed readiness, ambiguous selectors, secure-field redaction, window coordinates, background presses, app activation, focus guards, supported-action rejection, observed-change evidence, and two-window isolation checks. It changes Safari’s foreground UI and leaves its test page open. Run it only when desktop interaction testing is desired:

```sh
~/.local/share/monterey-desktop/.venv/bin/python ~/.codex/plugins/monterey-desktop/verify.py
```

`verify_background.py` creates two disposable Safari windows and a native foreground text field. Independent app callbacks verify background Unicode, shortcuts, value edits, drag/drop, and scrolling. It also simulates user typing into the real foreground while background typing is in progress and checks that the two text streams stay isolated, the pointer and foreground app stay unchanged, another Safari window stays untouched, secure fields are refused, and foreground takeover stops input. Its fixtures are closed afterward. Close Mission Control and leave the physical pointer stationary during this test; user pointer movement invalidates its unchanged-pointer assertion. It exercises the desktop and should be run only when interaction testing is desired:

```sh
~/.local/share/monterey-desktop/.venv/bin/python ~/.codex/plugins/monterey-desktop/verify_background.py
```

Original and background input checks passed on this Mac. Plugin installation is separately checked through its installed launcher, real MCP protocol, permission status, screenshot, and shared pause controls.

For the cross-Space variant, keep at least two existing desktop Spaces and run:

```sh
~/.local/share/monterey-desktop/.venv/bin/python ~/.codex/plugins/monterey-desktop/verify_background.py --across-spaces
```

This optional test uses observed Mission Control controls to switch Spaces for setup and restore the original Space afterward. It checks default exclusion, stale active-Space frames, a previously unseen window's visual typing and freshly rendered pixels, cached native controls, input isolation, drag/scroll, and unchanged active Space during every background batch. Its Space-switching code is confined to the test; the MCP server's Space backend is read-only.

## Privacy

Screenshots and visible window titles are returned to the connected assistant and can enter chat context. Capture images stay in memory unless an explicit local `--output` path is requested. Accessibility control labels and values can also enter chat context; native secure-field values are redacted. Browser evaluations return the page data chosen by the caller, including DOM values if requested. The helper does not read stored passwords, collect clipboard contents, or log typed text. Its native tools can interact with apps allowed by macOS permissions, without per-app restrictions.

Packaging follows [OpenAI’s local plugin layout](https://developers.openai.com/plugins/build/plugins). The server uses the [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/v1/).

## Readiness and app support

`wait_for` polls a unique Accessibility selector (name, role, value substring, enabled state) within a bounded timeout. `settle_seconds` checks visual stability; a stable image can still show a loading page. Prefer a specific readiness condition. UI traversal is bounded; increase `max_elements` or `ui_timeout` when the response says `ui.truncated`.

Native control support varies by app. A native press that fails returns an error so the assistant can inspect the screen before choosing another method. Value writes are read back and verified. Background writes focus the selected field through window-targeted input and fail if the app ignores the write. No automatic coordinate clicking follows a failed native press. Actions can partially run before an error; observe the actual state before continuing. App behavior itself can trigger navigation, dialogs, or app switching, so verify the requested outcome. These improvements do not provide the official app's full desktop isolation, live viewer, or browser/Office integrations.

## Version 1.8.0 complete workflow measurements

Three verified local samples per task through the actual MCP launchers, comparing 1.7.0 split calls with installed 1.8.0. Initial tab/window discovery and native focus setup are excluded consistently. The browser task navigates, waits for delayed message content, and reads its body. The native task fills one field, reveals another, fills it, saves, waits for the result, and reads final text.

| Task | Calls before → after | Response characters before → after | Local median before → after |
| --- | ---: | ---: | ---: |
| Navigate/wait/read | 3 → 1 | 607 → 404 | 641.1 → 608.0 ms |
| Native form/save/read | 2 → 1 | 14,465 → 6,587 | 1,868.2 → 3,257.9 ms |

The native baseline batches all initially exposed controls in the first call, then fills/saves/waits/reads in the second call. Composite execution has extra local work for fresh selectors and explicit outcome guards. Its measured native overhead was 1,390 ms while removing one agent round trip. As an inference from these samples, it can save total time when that removed round trip and reduced context processing cost more than the overhead; model reasoning/network time was not measured. Keep using ordinary ID batches for controls already exposed. Selector plans help when later controls appear and another model decision is unnecessary. Response sizes are characters rather than billed tokens. These fixtures do not establish performance on real mail pages or other apps. Full samples and scope are in [workflow-results.json](workflow-results.json).

## Version 1.7.0 local performance

Measured through the actual MCP launchers on this Monterey Mac. Five warm samples per browser operation and three complete Unicode replacements; values and browser input callbacks were verified.

| Operation | 1.6.2 median | Installed 1.7.0 median | Speedup |
| --- | ---: | ---: | ---: |
| Safari read with expected URL | 374.1 ms | 180.5 ms | 2.07× |
| Safari evaluate with expected URL | 391.6 ms | 183.9 ms | 2.13× |
| Replace 800 Unicode characters | 9,066.3 ms | 2,676.3 ms | 3.39× |

Tool schemas/descriptions contain 16.1% fewer characters, shared server instructions 51.8% fewer, and the skill entrypoint 30.2% fewer. These are character counts, not billed tokens. Native observation was roughly 0.36–0.40 seconds and did not improve. Measurements exclude model/network latency; apps and page complexity can differ. Full samples, methodology and validation scope are in [performance-results.json](performance-results.json). Run `verify_speed.py --root <plugin-directory> --output <results.json>` to reproduce a comparison.

## Local verification measurements

On this Mac, three alternating captures of the same display gave median image times of 734 ms for v1.0 and 263 ms for v1.1. Median v1.1 full observation, including structured Accessibility data, was 311 ms. These measurements exclude model reasoning, network transit, and chat rendering; complex app trees can take longer. The samples and check status are in `verification-results.json`.
