# Background and Spaces


Observe an exact selected window and require `background_input.available`. The user must be using a different foreground app. Background batches use native actions where supported, window-targeted pointer events otherwise, and process-targeted keyboard input with exact-window preparation. They do not send global input or request activation, and never fall back to foreground input. The user may switch between other apps; bringing the target app forward makes the helper yield. Locking the Mac or moving/resizing/disappearing target windows also stops input.

The active Space is the default. An observation with explicit `space_scope: "all"` can select a window on another Space; background actions preserve that scope without switching Spaces. Active-scope batches stop when the active Space changes. All-scope batches allow the user to switch Spaces while remaining pinned to the original window; moving that window between Spaces requires a new observation. Minimized/hidden windows and locked operation are unsupported. The private WindowServer backend is enabled only on Monterey; app compatibility varies. Safari across ordinary desktop Spaces was tested; fullscreen/multiple-display configurations have not been verified.

For text, supply the observed editable field's `element_id` to `type` or `key`. Background `focus` clicks that field without raising the app; `set_value` verifies the write without activation fallback. Already-focused fields preserve their selection; prefer `replace_text` to replace the whole value with verified keyboard input. Implicit typing follows the currently focused observed field, including changes caused by Tab. Observed secure controls are refused. `activate` and `open_url` require foreground mode; do not change modes to overcome a background failure while the user is working without their instruction.

Native window handles are retained while the MCP connection lasts, so controls observed on one Space can remain available after the user switches. For an unseen other-Space window, macOS may omit its AX window: `ui.available: false` means use the screenshot rather than increasing traversal budgets. A coordinate click on the intended text field followed by `type` in the same batch provides visual input; an optional intervening `cmd+a` replaces existing text. Its effect stays `not_verified` until confirmed from returned state/screenshots. Other keys that change focus require another field click before implicit visual typing. Do not invent element IDs or assume an absent native tree is empty content.

Inspect `action_results` for delivery and effect, `background_safety` for foreground telemetry, and the returned UI for the specific goal. `effect: "not_verified"` is inconclusive. Actions can partially run before an error; observe before retrying. Readiness stays within the selected window. Run only one assistant workflow, even while the human works in another app.

```json
{"frame_id":"<latest selected-window frame_id>","mode":"background","actions":[{"kind":"type","element_id":"<observed field id>","text":"example text"}],"settle_seconds":0}
```

For another Space, first call `desktop_observe(space_scope="all")`, then `desktop_observe(window_id=<selected ID>, space_scope="all")`. Use `mode="background"` with the returned frame. Without the explicit scope, observations remain on the active Space; without background mode, an off-Space target is refused.
