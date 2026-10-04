# Reading efficiently

For Safari Gmail/Outlook, first obtain a selected-window frame and identify native message links/rows. Use `press` when AXPress is advertised, or a screenshot/coordinate click when it is not. Request images explicitly when they help; avoid repeated screenshots for native-readable messages.

`desktop_act(feedback="text")` returns the newly opened document text and fresh actionable IDs together. Where navigation is asynchronous, batch the opening action with `wait_for` for the expected subject/heading or another unique readiness condition. Visual stability alone does not establish message readiness. If text output is unavailable, inspect the controls/image and choose another method.

`desktop_read(element_id=...)` narrows extraction to an observed message/body container, useful for avoiding mailbox sidebar/navigation text. Its pagination continues the captured text snapshot, not a new message; keep the same frame, element and traversal limits. `truncated=true` signals a limit or native read failure, while `page_truncated=true` signals more captured text. Do not describe an incomplete extraction as all contents. Native reading excludes collapsed/unexposed content; expand quoted text or conversation sections when the user asks for those contents.

Batch independent native actions where targets remain valid. Use returned frames/controls rather than an extra observation after every step. Choose `detail="full"` for missing capabilities/hierarchy or `include_image=true` for visual confirmation. A partially completed action error requires observing current state before continuing; repeating an entire batch can repeat successful input.
