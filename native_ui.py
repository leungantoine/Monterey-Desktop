"""Bounded macOS Accessibility queries and in-memory CoreGraphics capture."""
from __future__ import annotations
import time
import ctypes as C
from collections import deque

import AppKit
import ApplicationServices as AX
import Quartz as Q
import objc
from PIL import Image

ATTRIBUTES = ['AXRole', 'AXSubrole', 'AXTitle', 'AXDescription', 'AXHelp',
              'AXValue', 'AXEnabled', 'AXFocused', 'AXPosition', 'AXSize',
              'AXChildren', 'AXIdentifier', 'AXSelected', 'AXExpanded', 'AXURL']


class ProcessSerialNumber(C.Structure):
    _fields_ = [('high', C.c_uint32), ('low', C.c_uint32)]


# NSWorkspace's frontmostApplication can stay cached without an AppKit main run
# loop (our MCP tools execute on worker threads). Monterey's Process Manager
# queries the live foreground process instead; never trust a stale cached PID.
_processes = C.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
_front_process = _processes.GetFrontProcess
_front_process.argtypes, _front_process.restype = [C.POINTER(ProcessSerialNumber)], C.c_int32
_process_pid = _processes.GetProcessPID
_process_pid.argtypes, _process_pid.restype = [C.POINTER(ProcessSerialNumber), C.POINTER(C.c_int32)], C.c_int32
_window_identifier = getattr(_processes, '_AXUIElementGetWindow', None)
if _window_identifier is not None:
    _window_identifier.argtypes, _window_identifier.restype = [C.c_void_p, C.POINTER(C.c_uint32)], C.c_int32


def window_id_of(element):
    if _window_identifier is None or element is None:
        return None
    identifier = C.c_uint32()
    error = _window_identifier(objc.pyobjc_id(element), C.byref(identifier))
    return int(identifier.value) if error == 0 and identifier.value else None


def front_app():
    serial, pid = ProcessSerialNumber(), C.c_int32()
    if _front_process(C.byref(serial)) or _process_pid(C.byref(serial), C.byref(pid)):
        return None
    app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid.value)
    return app_info(app) if app else None


def app_info(app):
    return {'pid': int(app.processIdentifier()), 'name': str(app.localizedName() or ''),
            'bundle_id': str(app.bundleIdentifier() or '')}


def running_apps():
    return [app_info(app) for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications()
            if app.activationPolicy() == 0 and not app.isTerminated()]


def get_attr(element, name):
    error, value = AX.AXUIElementCopyAttributeValue(element, name, None)
    return value if error == 0 else None


def bounds_of(attributes):
    position, size = attributes.get('AXPosition'), attributes.get('AXSize')
    if position is None or size is None:
        return None
    try:
        p_ok, point = AX.AXValueGetValue(position, AX.kAXValueCGPointType, None)
        s_ok, size = AX.AXValueGetValue(size, AX.kAXValueCGSizeType, None)
        if p_ok and s_ok and size.width > 0 and size.height > 0:
            return {'x': float(point.x), 'y': float(point.y),
                    'width': float(size.width), 'height': float(size.height)}
    except (TypeError, ValueError):
        pass
    return None


def add_capabilities(element, node):
    error, actions = AX.AXUIElementCopyActionNames(element, None)
    if not error and actions is not None:
        node['actions'] = [str(action) for action in actions][:30]
    if node['role'] in ('AXTextField', 'AXTextArea', 'AXComboBox', 'AXSlider', 'AXCheckBox'):
        error, settable = AX.AXUIElementIsAttributeSettable(element, 'AXValue', None)
        if not error:
            node['value_settable'] = bool(settable)


def describe(element, capabilities=False):
    error, values = AX.AXUIElementCopyMultipleAttributeValues(element, ATTRIBUTES, 0, None)
    if error or values is None:
        return None, []
    a = dict(zip(ATTRIBUTES, values))
    role = str(a['AXRole']) if isinstance(a.get('AXRole'), str) else ''
    if not role:
        return None, []
    node = {'role': role}
    url = a.get('AXURL')
    if isinstance(url, str):
        node['url'] = url
    elif hasattr(url, 'absoluteString'):
        node['url'] = str(url.absoluteString())
    for key, attribute in [('name','AXTitle'), ('description','AXDescription'),
                           ('help','AXHelp'), ('identifier','AXIdentifier'), ('subrole','AXSubrole')]:
        value = a.get(attribute)
        if isinstance(value, str) and value:
            node[key] = str(value)[:500]
            if len(value) > 500:
                node[key + '_omitted_chars'] = len(value)-500
    value = a.get('AXValue')
    if 'Secure' in node.get('subrole', ''):
        node['value'] = '<redacted>'
    elif isinstance(value, (str, int, float, bool)):
        node['value'] = str(value)[:2000]
        if len(str(value)) > 2000:
            node['value_omitted_chars'] = len(str(value))-2000
    for key in ('Enabled','Focused','Selected','Expanded'):
        if isinstance(a.get('AX'+key), (bool, int)):
            node[key.lower()] = bool(a['AX'+key])
    bounds = bounds_of(a)
    if bounds:
        node['bounds'] = bounds
    if capabilities:
        add_capabilities(element,node)
    children = a.get('AXChildren')
    return node, list(children)[:400] if isinstance(children, (list, tuple)) or hasattr(children, 'count') and hasattr(children, '__iter__') else []


class NativeUI:
    def __init__(self):
        self.system = AX.AXUIElementCreateSystemWide()
        AX.AXUIElementSetMessagingTimeout(self.system, 0.08)
        self.window_cache = {}

    def window(self, pid, bounds=None, window_id=None):
        app = AX.AXUIElementCreateApplication(pid)
        AX.AXUIElementSetMessagingTimeout(app, 0.08)
        windows = list(get_attr(app, 'AXWindows') or [])
        for attribute in ('AXFocusedWindow', 'AXMainWindow'):
            candidate = get_attr(app, attribute)
            if candidate is not None and candidate not in windows:
                windows.append(candidate)
        for candidate in windows:
            identifier = window_id_of(candidate)
            if identifier is not None:
                self.window_cache[(pid, identifier)] = candidate
        while len(self.window_cache) > 200:
            self.window_cache.pop(next(iter(self.window_cache)))
        cached = self.window_cache.get((pid, window_id))
        if cached is not None and cached not in windows:
            if window_id_of(cached) == window_id and get_attr(cached, 'AXRole') == 'AXWindow':
                windows.append(cached)
            else:
                self.window_cache.pop((pid, window_id), None)
        if bounds is not None:
            matches = []
            if window_id is not None:
                identified = [(window, window_id_of(window)) for window in windows]
                if any(identifier is not None for _,identifier in identified):
                    exact = [window for window,identifier in identified if identifier == window_id]
                    if len(exact) != 1:
                        raise ValueError('Selected window has no unique native Accessibility ID match. Observe again.')
                    return exact[0]
            for window in windows:
                node, _ = describe(window)
                actual = (node or {}).get('bounds')
                if actual and all(abs(actual[k]-bounds[k]) <= 1 for k in bounds):
                    matches.append(window)
            if len(matches) != 1:
                raise ValueError('Selected window has no unique Accessibility window match. Observe again or use the primary display.')
            return matches[0]
        root = get_attr(app, 'AXFocusedWindow')
        if root is None:
            windows = get_attr(app, 'AXWindows')
            root = windows[0] if windows else app
        return root

    def ensure_window_available(self, pid, bounds, window_id, allow_missing=False):
        app = AX.AXUIElementCreateApplication(pid)
        AX.AXUIElementSetMessagingTimeout(app, 0.08)
        if get_attr(app, 'AXHidden') is True:
            raise RuntimeError('The selected app/window is hidden or minimized. Unhide it locally and observe again.')
        try:
            root = self.window(pid, bounds, window_id)
        except ValueError:
            if allow_missing:return
            raise
        if get_attr(root, 'AXMinimized') is True:
            raise RuntimeError('The selected app/window is hidden or minimized. Unhide it locally and observe again.')

    def check_window_focus(self, pid, bounds, window_id=None):
        app = AX.AXUIElementCreateApplication(pid)
        AX.AXUIElementSetMessagingTimeout(app, 0.08)
        root = get_attr(app, 'AXFocusedWindow')
        actual_id = window_id_of(root)
        if window_id is not None and actual_id is not None and actual_id != window_id:
            raise RuntimeError('Selected window is not the focused window. Observe and focus its control before keyboard or positional input.')
        node, _ = describe(root) if root is not None else (None, [])
        actual = (node or {}).get('bounds')
        if not actual or any(abs(actual[k]-bounds[k]) > 1 for k in bounds):
            raise RuntimeError('Selected window is not the focused window. Observe and focus its control before keyboard or positional input.')

    def snapshot(self, pid, check, limit=400, seconds=2, window_bounds=None, window_id=None, capabilities=True):
        root = self.window(pid, window_bounds, window_id)
        queue = deque([(root, None, 0)])
        nodes, references, visited = [], {}, set()
        incomplete = False
        deadline = time.monotonic()+seconds
        while queue and len(nodes) < limit and time.monotonic() < deadline:
            check()
            element, parent, depth = queue.popleft()
            identity = hash(element)
            if identity in visited:
                continue
            visited.add(identity)
            node, children = describe(element, capabilities=capabilities)
            if node is None:
                incomplete = True
                continue
            element_id = f'e{len(nodes)}'
            node.update({'id': element_id, 'pid': pid})
            if parent is not None:
                node['parent'] = parent
            nodes.append(node)
            references[element_id] = (element, node)
            if depth < 24:
                queue.extend((child, element_id, depth+1) for child in children)
            elif children:
                incomplete = True
            if len(children) == 400:
                incomplete = True
        return {'pid': pid, 'window_id': window_id_of(root), 'elements': nodes, 'truncated': bool(queue) or incomplete}, references

    def activate(self, pid, check):
        app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None or app.isTerminated():
            raise ValueError('The selected app is no longer running. Observe again.')
        if not app.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps):
            raise RuntimeError('macOS could not activate the selected app.')
        deadline = time.monotonic()+2
        while time.monotonic() < deadline:
            check()
            if (front_app() or {}).get('pid') == pid:
                return
            time.sleep(0.04)
        raise RuntimeError('The selected app did not become foreground.')

    def read_text(self, root, check, max_nodes=5000, seconds=2):
        """Read document-order native text without geometry, capabilities or images."""
        attributes=['AXRole','AXSubrole','AXTitle','AXDescription','AXValue','AXChildren']
        stack=[root]
        visited=set()
        chunks=[]
        chars=0
        incomplete=False
        start=time.monotonic()
        deadline=start+seconds
        while stack and len(visited)<max_nodes and time.monotonic()<deadline and chars<100000:
            check()
            element=stack.pop()
            identity=hash(element)
            if identity in visited:continue
            visited.add(identity)
            error,values=AX.AXUIElementCopyMultipleAttributeValues(element,attributes,0,None)
            if error or values is None:
                incomplete=True
                continue
            data=dict(zip(attributes,values))
            role=data.get('AXRole')
            if not isinstance(role,str):
                incomplete=True
                continue
            if 'Secure' in str(data.get('AXSubrole','')):
                continue  # Never read secure values or their children.
            children=data.get('AXChildren')
            children=list(children) if hasattr(children,'__iter__') else []
            # Container titles duplicate descendants and browser chrome. Leaf
            # text, links, image alt descriptions, and editable values matter.
            value=data.get('AXValue')
            if role in ('AXStaticText','AXTextField','AXTextArea','AXComboBox'):
                value=value if isinstance(value,str) else data.get('AXTitle')
            elif not children and role not in ('AXWindow','AXApplication','AXToolbar','AXGroup','AXWebArea'):
                value=data.get('AXTitle') or data.get('AXDescription') or value
            else:
                value=None
            if isinstance(value,str) and value.strip():
                chunk=value.strip()
                remaining=100000-chars
                if len(chunk)>remaining:
                    chunk=chunk[:remaining]
                    incomplete=True
                chunks.append(chunk)
                chars+=len(chunk)+1
            stack.extend(reversed(children))
        return {'text':'\n'.join(chunks),'truncated':bool(stack) or incomplete,
                'scanned_nodes':len(visited),'read_ms':round((time.monotonic()-start)*1000),
                'source':'accessibility','scope':'native exposed text; excludes unexposed/collapsed content'}

    def resolve(self, references, element_id):
        if element_id not in references:
            raise ValueError('Unknown element_id. Observe the target app again.')
        element, before = references[element_id]
        current, _ = describe(element)
        if current is None or any(current.get(k) != before.get(k) for k in ('role','name','description','identifier')):
            raise ValueError('Accessibility target changed or disappeared. Observe again.')
        if current.get('enabled') is False:
            raise ValueError('The target control is disabled.')
        return element, before

    def press(self, references, element_id):
        element, _ = self.resolve(references, element_id)
        error, actions = AX.AXUIElementCopyActionNames(element, None)
        if not error and 'AXPress' not in (actions or []):
            raise ValueError('This control does not advertise AXPress. Inspect its actions or use observed screenshot coordinates.')
        error = AX.AXUIElementPerformAction(element, 'AXPress')
        if error:
            raise RuntimeError(f'This control does not support native press (AX error {error}). Observe and use its screenshot coordinates instead.')

    def set_value(self, references, element_id, value, check, allow_activation=True):
        element, before = self.resolve(references, element_id)
        error, settable = AX.AXUIElementIsAttributeSettable(element, 'AXValue', None)
        if error or not settable:
            raise ValueError('This control cannot set its value directly. Focus it and type instead.')
        activated = False
        # WebKit sometimes acknowledges a value write but ignores it unless focused.
        # Verify the actual value before claiming success; retry only this idempotent write.
        for attempt in range(2):
            check()
            error = AX.AXUIElementSetAttributeValue(element, 'AXValue', value)
            if error:
                raise RuntimeError(f'Native value change failed (AX error {error}).')
            deadline = time.monotonic()+0.2
            while time.monotonic() < deadline:
                check()
                if get_attr(element, 'AXValue') == value:
                    return activated
                time.sleep(0.03)
            if not allow_activation:
                raise RuntimeError('The app did not reflect the background value write. No activation or foreground fallback was attempted; observe before retrying.')
            if attempt == 0:
                self.focus(references, element_id, check)
                activated = True
        raise RuntimeError('The app acknowledged the value write but the control did not reflect it. Observe before retrying; use focus and typing if necessary.')

    def focus(self, references, element_id, check):
        element, before = self.resolve(references, element_id)
        window = get_attr(element, 'AXWindow')
        if window is not None:
            error = AX.AXUIElementPerformAction(window, 'AXRaise')
            if error:
                raise RuntimeError(f'Could not raise the control’s window (AX error {error}).')
        error = AX.AXUIElementSetAttributeValue(element, 'AXFocused', True)
        if error:
            raise RuntimeError(f'Native focus failed (AX error {error}).')
        if get_attr(element, 'AXFocused') is not True:
            raise RuntimeError('The selected control did not receive focus.')
        # Focus the selected window before activation, so an app's previous
        # focused window on another Space cannot redirect the activation.
        self.activate(before['pid'], check)

    def find_unique(self, pid, name, role, value_contains, enabled, timeout, check, window_bounds=None, window_id=None, identifier=None, selector_only=False):
        deadline = time.monotonic()+timeout
        def exact_label(node,element,key,attribute,expected):
            value=node.get(key)
            if node.get(key+'_omitted_chars'):
                check()
                if time.monotonic()>=deadline:
                    raise TimeoutError('The requested UI condition did not become ready before timeout.')
                value=get_attr(element,attribute)
            return value==expected
        while True:
            check()
            snapshot, references = self.snapshot(pid, check, limit=1000, seconds=min(2, max(0.01, deadline-time.monotonic())), window_bounds=window_bounds, window_id=window_id, capabilities=not selector_only)
            matches = []
            values_complete = True
            for node in snapshot['elements']:
                if ((role is not None and node['role'] != role)
                        or (enabled is not None and node.get('enabled') != enabled)):
                    continue
                element, _ = references[node['id']]
                if name is not None and not any(exact_label(node,element,key,attribute,name)
                        for key,attribute in (('name','AXTitle'),('description','AXDescription'),('help','AXHelp'))):
                    continue
                if identifier is not None and not exact_label(node,element,'identifier','AXIdentifier',identifier):
                    continue
                if value_contains is not None:
                    if time.monotonic() >= deadline:
                        values_complete = False
                        break
                    # Observation previews are bounded; readiness must inspect
                    # the complete live value without returning it to the caller.
                    subrole = get_attr(element, 'AXSubrole')
                    if 'Secure' in node.get('subrole', '') or 'Secure' in str(subrole or ''):
                        continue
                    check()
                    value = get_attr(element, 'AXValue')
                    if not isinstance(value, (str, int, float, bool)) or value_contains not in str(value):
                        continue
                matches.append(node)
            if len(matches) == 1 and not snapshot['truncated'] and values_complete:
                if selector_only:
                    check()
                    # Scan labels/state for uniqueness, then query capabilities
                    # only on the matched live handle needed by the next action.
                    add_capabilities(references[matches[0]['id']][0],matches[0])
                    check()
                return snapshot, references, matches[0]
            if len(matches) > 1:
                raise ValueError('Readiness selector is ambiguous. Supply a more specific name/role/value.')
            if time.monotonic() >= deadline:
                raise TimeoutError('The requested UI condition did not become ready before timeout.')
            time.sleep(min(0.1, max(0, deadline-time.monotonic())))

    def wait_for(self, pid, name, role, value_contains, enabled, timeout, check, window_bounds=None, window_id=None):
        _, _, node = self.find_unique(pid,name,role,value_contains,enabled,timeout,check,window_bounds,window_id)
        return node


def capture(window_id=None):
    if window_id is None:
        image = Q.CGDisplayCreateImage(Q.CGMainDisplayID())
    else:
        image = Q.CGWindowListCreateImage(Q.CGRectNull, Q.kCGWindowListOptionIncludingWindow,
                                         window_id, Q.kCGWindowImageBoundsIgnoreFraming)
    if image is None:
        raise RuntimeError('macOS did not return a screen image.')
    width, height = Q.CGImageGetWidth(image), Q.CGImageGetHeight(image)
    if Q.CGImageGetBitsPerComponent(image) != 8 or Q.CGImageGetBitsPerPixel(image) != 32:
        raise RuntimeError('Unsupported macOS capture pixel format.')
    info = Q.CGImageGetBitmapInfo(image)
    little = (info & Q.kCGBitmapByteOrderMask) == Q.kCGBitmapByteOrder32Little
    alpha_first = (info & Q.kCGBitmapAlphaInfoMask) in (Q.kCGImageAlphaPremultipliedFirst, Q.kCGImageAlphaFirst, Q.kCGImageAlphaNoneSkipFirst)
    raw_format = ('BGRA' if alpha_first else 'ABGR') if little else ('ARGB' if alpha_first else 'RGBA')
    data = bytes(Q.CGDataProviderCopyData(Q.CGImageGetDataProvider(image)))
    result = Image.frombytes('RGBA', (width,height), data, 'raw', raw_format, Q.CGImageGetBytesPerRow(image)).convert('RGB')
    space = Q.CGImageGetColorSpace(image)
    profile = Q.CGColorSpaceCopyICCData(space) if space else None
    return result, bytes(profile) if profile else None
