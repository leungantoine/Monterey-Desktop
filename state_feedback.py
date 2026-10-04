"""Bounded evidence from two AX snapshots; no inference of task success."""
FIELDS = ('role', 'name', 'description', 'value', 'enabled', 'focused', 'selected', 'expanded')


def preview(node):
    result = {k: (v[:180] if isinstance(v, str) else v)
              for k in FIELDS if (v := node.get(k)) is not None}
    if 'Secure' in node.get('subrole', ''):
        result['value'] = '<redacted>'
    for key in FIELDS:
        value = node.get(key)
        if key == 'value' and 'Secure' in node.get('subrole', ''):
            continue
        omitted = max(0, len(value)-180) if isinstance(value, str) else 0
        omitted += node.get(key+'_omitted_chars', 0)
        if omitted:
            result[key+'_omitted_chars'] = omitted
    if node.get('id'):
        result['element_id'] = node['id']
    return result


def compared(node, key):
    if key == 'value' and 'Secure' in node.get('subrole', ''):
        return '<redacted>', 0
    return node.get(key), node.get(key+'_omitted_chars', 0)


def state_changes(before, after, before_ui, after_ui, limit=20):
    """Match retained native elements, never recycled e0/e1 IDs or similar labels."""
    result = {'status': 'unavailable', 'changes': [], 'change_count': 0,
              'coverage_complete': bool(before and after) and not (before_ui.get('truncated') or after_ui.get('truncated')),
              'truncated': False,
              'meaning': 'Observed AX state only. Changes do not prove task success; no change does not prove failure. Observe before retrying an accepted action.'}
    if not before or not after:
        return result
    before_root = next(iter(before.values()))[0]
    after_root = next(iter(after.values()))[0]
    if before_ui.get('pid') != after_ui.get('pid') or before_root != after_root:
        result['status'] = 'target_changed'
        return result
    # AXUIElement equality/hash identifies the same native object across snapshots.
    old = {element: node for element, node in before.values()}
    new = {element: node for element, node in after.values()}
    changes = []
    for element, node in new.items():
        current = preview(node)
        if element not in old:
            changes.append({'kind': 'appeared', 'after': current})
        else:
            prior = preview(old[element])
            prior.pop('element_id', None)
            # Compare captured state before shortening the returned evidence.
            # A changed suffix must not become an inconclusive no-change result.
            fields = [k for k in FIELDS if compared(old[element], k) != compared(node, k)]
            if fields:
                changes.append({'kind': 'changed', 'fields': fields, 'before': prior, 'after': current})
    for element, node in old.items():
        if element not in new:
            prior = preview(node)
            prior.pop('element_id', None)  # removed IDs cannot be acted on
            changes.append({'kind': 'disappeared', 'before': prior})
    # Focus/value changes are most useful; bound the evidence returned to the model.
    changes.sort(key=lambda c: (c['kind'] != 'changed', not any(k in c.get('fields', []) for k in ('value', 'focused', 'enabled', 'selected'))))
    result.update(status='observed_change' if changes else 'no_change_observed',
                  changes=changes[:limit], change_count=len(changes), truncated=len(changes) > limit)
    return result
