"""Read-only Monterey Space discovery. Never switches Spaces or moves windows."""
from __future__ import annotations

import ctypes as C
import platform
import plistlib

import objc
from Foundation import NSArray


class NativeSpaces:
    def __init__(self):
        self.available = False
        self.reason = 'Space discovery is supported only on macOS Monterey.'
        if platform.mac_ver()[0].split('.')[0] != '12':
            return
        try:
            self.sky = C.CDLL('/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight')
            self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
            self.connection = self.bind(self.sky, 'SLSMainConnectionID', C.c_int32, [])()
            self.active = self.bind(self.sky, 'SLSGetActiveSpace', C.c_uint64, [C.c_int32])
            self.displays = self.bind(self.sky, 'SLSCopyManagedDisplaySpaces', C.c_void_p, [C.c_int32])
            self.memberships = self.bind(self.sky, 'SLSCopySpacesForWindows', C.c_void_p,
                                         [C.c_int32, C.c_int32, C.c_void_p])
            self.ordered = self.bind(self.sky, 'SLSWindowIsOrderedIn', C.c_int32,
                                     [C.c_int32, C.c_uint32, C.POINTER(C.c_uint8)])
            self.serialize = self.bind(self.cf, 'CFPropertyListCreateData', C.c_void_p,
                                       [C.c_void_p, C.c_void_p, C.c_long, C.c_ulong, C.c_void_p])
            self.bytes = self.bind(self.cf, 'CFDataGetBytePtr', C.c_void_p, [C.c_void_p])
            self.length = self.bind(self.cf, 'CFDataGetLength', C.c_long, [C.c_void_p])
            self.release = self.bind(self.cf, 'CFRelease', None, [C.c_void_p])
            self.available, self.reason = True, None
        except (AttributeError, OSError) as error:
            self.reason = str(error)

    @staticmethod
    def bind(library, name, result, args):
        function = getattr(library, name)
        function.restype, function.argtypes = result, args
        return function

    def require(self):
        if not self.available:
            raise RuntimeError('Space discovery is unavailable: ' + self.reason)

    def decode_owned(self, pointer):
        if not pointer:
            raise RuntimeError('WindowServer did not return Space metadata.')
        data = None
        try:
            data = self.serialize(None, pointer, 100, 0, None)
            if not data:
                raise RuntimeError('Could not decode Space metadata.')
            return plistlib.loads(C.string_at(self.bytes(data), self.length(data)))
        finally:
            if data:
                self.release(data)
            self.release(pointer)

    def active_id(self):
        self.require()
        identifier = int(self.active(self.connection))
        if not identifier:
            raise RuntimeError('Could not determine the active Space.')
        return identifier

    def window_spaces(self, window_id):
        self.require()
        windows = NSArray.arrayWithArray_([int(window_id)])
        identifiers = self.decode_owned(self.memberships(self.connection, 7, objc.pyobjc_id(windows)))
        return sorted(set(int(identifier) for identifier in identifiers if int(identifier) > 0))

    def snapshot(self):
        if not self.available:
            return {'available': False, 'reason': self.reason, 'active_space_id': None, 'spaces': []}
        active = self.active_id()
        displays = self.decode_owned(self.displays(self.connection))
        spaces, visible = [], []
        for display in displays:
            current = display.get('Current Space', {})
            current_id = int(current.get('ManagedSpaceID', current.get('id64', 0)))
            if current_id:
                visible.append(current_id)
            for space in display.get('Spaces', []):
                identifier = int(space.get('ManagedSpaceID', space.get('id64', 0)))
                kind = int(space.get('type', -1))
                if not identifier or space.get('uuid') == 'dashboard':
                    continue
                spaces.append({'space_id': identifier,
                               # Managed Space ordering can differ from Mission Control names.
                               # IDs and window membership are authoritative; do not invent Desktop N.
                               'label': f'Space {identifier}',
                               'type': 'desktop' if kind == 0 else 'fullscreen' if kind == 4 else 'system',
                               'display_id': str(display.get('Display Identifier', '')),
                               'active': identifier == active, 'visible': identifier == current_id})
        return {'available': True, 'reason': None, 'active_space_id': active,
                'visible_space_ids': visible, 'spaces': spaces}

    def window_ordered(self, window_id):
        # An off-Space window remains ordered; hidden/minimized windows do not.
        self.require()
        ordered = C.c_uint8()
        error = self.ordered(self.connection, window_id, C.byref(ordered))
        return not error and bool(ordered.value)
