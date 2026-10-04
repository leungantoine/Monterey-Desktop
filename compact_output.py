"""Presentation only: full native snapshots and safety checks stay internal."""
import json


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def bounded(node):
    result = dict(node)
    for key in ('name', 'description', 'help', 'value', 'identifier'):
        value = result.get(key)
        if isinstance(value, str) and len(value) > 500:
            result[key] = value[:500]
            result[key + '_omitted_chars'] = len(value) - 500 + result.get(key + '_omitted_chars', 0)
    return result


def present(metadata, detail='compact', ui_query=None, action=False, controls_only=False):
    """Keep fresh IDs, capabilities, uncertainty, and target context in every reply."""
    if detail == 'full' and ui_query is None and not controls_only:
        return metadata
    result = dict(metadata)
    ui = dict(metadata['ui'])
    nodes = ui['elements']
    query = ui_query.casefold() if ui_query else None
    filtered = [n for n in nodes if query is None or any(
        query in str(n.get(k, '')).casefold()
        for k in ('role', 'name', 'description', 'help', 'value', 'identifier', 'url'))]
    if controls_only:
        filtered=[n for n in filtered if n.get('role')=='AXWindow' or 'AXPress' in n.get('actions', [])
                  or n.get('role') in ('AXTextField','AXTextArea','AXComboBox','AXCheckBox','AXSlider')]
    if detail == 'compact':
        # Anonymous layout containers carry no task information. Keep controls,
        # window roots, labels, values, and all advertised actions.
        useful = [n for n in filtered if n.get('role') == 'AXWindow' or any(
            n.get(k) for k in ('name', 'description', 'help', 'value', 'actions', 'value_settable', 'focused'))
            or n.get('role') not in ('AXGroup', 'AXScrollArea', 'AXSplitGroup', 'AXLayoutArea', 'AXUnknown')]
        output = []
        retained = {n['id'] for n in useful}
        for node in useful:
            item = bounded(node)
            item.pop('pid', None)
            item.pop('bounds', None)
            for key in ('focused', 'selected', 'expanded'):
                if item.get(key) is False:
                    item.pop(key)
            for key in ('actions', 'name', 'description', 'help', 'identifier'):
                if not item.get(key):
                    item.pop(key, None)
            if item.get('parent') not in retained:
                item.pop('parent', None)
            if item.get('screenshot_bounds'):
                item['rect'] = [round(item['screenshot_bounds'][k], 1) for k in ('x', 'y', 'width', 'height')]
                item.pop('screenshot_bounds')
            output.append(item)
        ui['elements'] = output
        for key in ('transport', 'coordinates', 'display', 'background_input', 'dispatch'):
            result.pop(key, None)
        if action:
            for key in ('apps', 'windows', 'space_info'):
                result.pop(key, None)
        if 'state_changes' in result:
            result['state_changes'] = {k:v for k,v in result['state_changes'].items() if k != 'meaning'}
        if 'background_safety' in result:
            result['background_safety'] = {k:v for k,v in result['background_safety'].items() if k != 'human_activity'}
    else:
        ui['elements'] = filtered
    ui.update(scanned_count=len(nodes), returned_count=len(ui['elements']),
              omitted_count=len(nodes)-len(ui['elements']))
    if query is not None:
        ui['query'] = ui_query
    result['ui'] = ui
    result['detail'] = detail
    return result
