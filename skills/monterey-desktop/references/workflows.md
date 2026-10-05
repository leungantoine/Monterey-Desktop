# Complete workflows

Composite tools avoid intermediate agent calls and repeated response context. They execute only caller-specified steps; they do not choose new actions or replay failures.

## Safari navigate, wait, read

Discover the intended tab once using `desktop_browser(operation="tabs")`. Use its Safari window ID and current source URL:

```json
{
  "operation": "navigate_read",
  "window_id": 91,
  "tab_index": 1,
  "expected_url": "https://example.test/inbox",
  "url": "https://example.test/messages/42",
  "ready_selector": "#message-heading",
  "read_selector": "#message-body",
  "text_contains": "Expected message content",
  "timeout": 10
}
```

The examples use illustrative IDs/URLs; use actual discovery evidence. Readiness requires document completion, the exact destination URL, and every supplied content condition. CSS selectors must match exactly one visible element; duplicate matches fail. `text_contains` checks the scoped text, or body text if no scope is supplied. Only the final scoped text (up to 50,000 characters) is returned. Outer output limits may truncate that result.

Navigation runs once. Each subsequent poll rechecks pause, permissions, lock, exact live window/Space, tab range, destination URL and duplicate URLs. Redirects or URL normalization can fail the exact destination guard. On failure, inspect the current tab before choosing a new operation. Use split operations for a task that needs intermediate decisions or an unknown redirected destination.

## Native plans with newly revealed controls

Observe an exact desktop capture `window_id` and retain the latest `frame_id`. Foreground plans require that same window already focused; use an authorized focus action and its resulting frame if needed. Safari IDs above are not desktop capture IDs.

```json
{
  "frame_id": "latest-observed-frame",
  "steps": [
    {"kind": "replace_text", "name": "First name", "role": "AXTextField", "text": "Example"},
    {"kind": "press", "name": "Show details", "role": "AXButton"},
    {"kind": "replace_text", "name": "Detail", "role": "AXTextField", "text": "Authorized value"},
    {"kind": "press", "name": "Save fixture", "role": "AXButton"},
    {"kind": "wait_for", "role": "AXStaticText", "value_contains": "Done:", "enabled": null}
  ],
  "feedback": "text",
  "timeout": 20
}
```

Use 1–20 steps: `press`, `replace_text`, `set_value`, or `wait_for`. Each selector must uniquely match a complete fresh Accessibility traversal. Name/role/identifier use exact matches; `value_contains` reads the full live nonsecure value. Enabled defaults to true. Each step has a 3-second timeout by default (up to 10); the whole plan has a 20-second deadline by default (up to 30). Ambiguity stops immediately. Large or incomplete app trees may fail uniqueness.

The entire input shape is validated before input; target/capability checks happen again as each control appears. Focus, display/window geometry, Space, permissions and pause guards remain active. Foreground native writes may focus the already selected window; background plans explicitly require `mode="background"` and the same background restrictions as action batches. See [background and Spaces](background.md).

Successful output includes completed steps, delivery/value/readiness evidence, state changes, and a fresh frame. `feedback="text"` adds native reading plus actionable controls; `feedback="controls"` skips the read. Secure values remain omitted; a secure replacement reports delivery without value verification. A press reporting delivery is not evidence that saving or submitting succeeded: end with an expected outcome condition.

Failure invalidates native frames and reports completed steps; the current step may also have partially run. Observe actual state before continuing. Never replay a failed plan blindly. Do not put steps needing a new user decision or authorization into a plan.

## What the measurements mean

The repository's `workflow-results.json` records complete local fixture tasks, MCP calls, and response characters. Initial discovery/focus setup is excluded consistently. Composite operations reduce model round trips and repeated context; local execution can still be slower because they resolve fresh selectors and check explicit outcomes. Model/network latency is not measured. Use the call reduction and local overhead together when assessing overall workflow speed.
