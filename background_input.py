"""Monterey window-targeted input. Never posts to the global desktop.

WindowServer record layouts are adapted from actuallyepic/background-computer-use
(MIT); see THIRD_PARTY_NOTICES.md. These private interfaces are version-bound.
"""
from __future__ import annotations

import ctypes as C
import platform
import struct
import time

import objc
import Quartz as Q

from native_ui import ProcessSerialNumber
from input_timing import TEXT_KEY_HOLD_SECONDS, TEXT_CHUNK_INTERVAL_SECONDS


class BackgroundInput:
    def __init__(self):
        self.available = False
        self.reason = 'Background WindowServer input is supported only on macOS Monterey.'
        if platform.mac_ver()[0].split('.')[0] != '12':
            return
        try:
            self.sky = C.CDLL('/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight')
            self.processes = C.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
            self.post = self.sky.SLEventPostToPid
            self.post.argtypes, self.post.restype = [C.c_int32, C.c_void_p], None
            self.local = self.sky.SLEventSetWindowLocation
            self.local.argtypes, self.local.restype = [C.c_void_p, C.c_double, C.c_double], None
            self.record_post = self.sky.SLPSPostEventRecordTo
            self.record_post.argtypes, self.record_post.restype = [C.POINTER(ProcessSerialNumber), C.c_void_p], C.c_int32
            self.get_psn = self.processes.GetProcessForPID
            self.get_psn.argtypes, self.get_psn.restype = [C.c_int32, C.POINTER(ProcessSerialNumber)], C.c_int32
            self.source = Q.CGEventSourceCreate(Q.kCGEventSourceStatePrivate)
            if self.source is None:
                raise RuntimeError('Could not create a private event source.')
            self.available, self.reason = True, None
        except (AttributeError, OSError, RuntimeError) as error:
            self.reason = str(error)

    def require(self):
        if not self.available:
            raise RuntimeError('Background input is unavailable: ' + self.reason)

    def prepare(self, pid, window_id, keyboard, check):
        self.require()
        check()
        psn = ProcessSerialNumber()
        if self.get_psn(pid, C.byref(psn)):
            raise RuntimeError('The background target process no longer exists.')
        focus = bytearray(0xF8)
        focus[4], focus[8], focus[0x8A] = 0xF8, 0x0D, 1
        struct.pack_into('<I', focus, 0x3C, window_id)
        records = [focus]
        if keyboard:
            for phase in (1, 2):
                record = bytearray(0x100)
                record[4], record[8], record[0x3A] = 0xF8, phase, 0x10
                record[0x20:0x30] = b'\xff'*16
                struct.pack_into('<I', record, 0x3C, window_id)
                records.append(record)
        for record in records:
            check()
            buffer = (C.c_ubyte*len(record)).from_buffer_copy(record)
            if self.record_post(C.byref(psn), buffer):
                raise RuntimeError('Background target-window preparation failed. No foreground fallback was attempted.')
            check()
        time.sleep(0.05)
        check()

    def pointer_event(self, pid, window_id, bounds, kind, x, y, button=0, count=1):
        event = Q.CGEventCreateMouseEvent(self.source, kind, (x, y), button)
        if event is None:
            raise RuntimeError('Could not create background pointer input.')
        Q.CGEventSetFlags(event, 0)
        for field, value in ((1, count), (3, button), (7, 3), (40, pid),
                             (51, window_id), (91, window_id), (92, window_id)):
            Q.CGEventSetIntegerValueField(event, field, value)
        # SkyLight's location is window-local with a top-left origin on Monterey.
        self.local(objc.pyobjc_id(event), x-bounds['x'], y-bounds['y'])
        return event

    def send_pointer(self, pid, window_id, bounds, kind, x, y, check, button=0, count=1):
        check()
        event = self.pointer_event(pid, window_id, bounds, kind, x, y, button, count)
        self.post(pid, objc.pyobjc_id(event))
        time.sleep(0.025)
        check()

    def click(self, pid, window_id, bounds, x, y, check, button=0, clicks=1):
        self.prepare(pid, window_id, False, check)
        self.send_pointer(pid, window_id, bounds, Q.kCGEventMouseMoved, x, y, check, button, 0)
        down, up = (Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp) if button == 0 else (Q.kCGEventRightMouseDown, Q.kCGEventRightMouseUp)
        for count in range(1, clicks+1):
            held = False
            try:
                check()
                event = self.pointer_event(pid, window_id, bounds, down, x, y, button, count)
                self.post(pid, objc.pyobjc_id(event))
                held = True
                time.sleep(0.025)
                check()
            finally:
                if held:
                    event = self.pointer_event(pid, window_id, bounds, up, x, y, button, count)
                    self.post(pid, objc.pyobjc_id(event))
            time.sleep(0.025)
            check()

    def drag(self, pid, window_id, bounds, start, end, duration, check):
        self.prepare(pid, window_id, False, check)
        self.send_pointer(pid, window_id, bounds, Q.kCGEventMouseMoved, *start, check, count=0)
        current, held = start, False
        try:
            check()
            event = self.pointer_event(pid, window_id, bounds, Q.kCGEventLeftMouseDown, *start)
            self.post(pid, objc.pyobjc_id(event))
            held = True
            steps = max(2, round(duration/0.025))
            for index in range(1, steps+1):
                fraction = index/steps
                current = (start[0]+(end[0]-start[0])*fraction, start[1]+(end[1]-start[1])*fraction)
                self.send_pointer(pid, window_id, bounds, Q.kCGEventLeftMouseDragged, *current, check)
        finally:
            if held:
                event = self.pointer_event(pid, window_id, bounds, Q.kCGEventLeftMouseUp, *current)
                self.post(pid, objc.pyobjc_id(event))

    def text(self, pid, window_id, text, check):
        self.prepare(pid, window_id, True, check)
        for offset in range(0, len(text), 16):
            check()
            chunk = text[offset:offset+16]
            units = len(chunk.encode('utf-16-le'))//2
            down = Q.CGEventCreateKeyboardEvent(self.source, 0, True)
            up = Q.CGEventCreateKeyboardEvent(self.source, 0, False)
            if down is None or up is None:
                raise RuntimeError('Could not create background keyboard input.')
            for event in (down, up):
                Q.CGEventSetFlags(event, 0)
                Q.CGEventKeyboardSetUnicodeString(event, units, chunk)
            try:
                Q.CGEventPostToPid(pid, down)
                time.sleep(TEXT_KEY_HOLD_SECONDS)
                check()
            finally:
                Q.CGEventPostToPid(pid, up)
            time.sleep(TEXT_CHUNK_INTERVAL_SECONDS)
            check()

    def key(self, pid, window_id, code, flags, check):
        self.prepare(pid, window_id, True, check)
        down = Q.CGEventCreateKeyboardEvent(self.source, code, True)
        up = Q.CGEventCreateKeyboardEvent(self.source, code, False)
        if down is None or up is None:
            raise RuntimeError('Could not create background shortcut input.')
        Q.CGEventSetFlags(down, flags)
        Q.CGEventSetFlags(up, flags)
        try:
            check()
            Q.CGEventPostToPid(pid, down)
            time.sleep(0.04)
            check()
        finally:
            Q.CGEventPostToPid(pid, up)
        check()

    def scroll(self, pid, window_id, bounds, delta_x, delta_y, check):
        self.prepare(pid, window_id, False, check)
        steps = max(1, max(abs(delta_x), abs(delta_y))//40)
        previous_x = previous_y = 0
        for index in range(1, steps+2):
            check()
            total_x, total_y = round(delta_x*min(index,steps)/steps), round(delta_y*min(index,steps)/steps)
            dx, dy = previous_x-total_x, previous_y-total_y
            previous_x, previous_y = total_x, total_y
            event = Q.CGEventCreateScrollWheelEvent(self.source, Q.kCGScrollEventUnitPixel, 2, dy, dx)
            if event is None:
                raise RuntimeError('Could not create background wheel input.')
            x, y = bounds['x']+bounds['width']/2, bounds['y']+bounds['height']/2
            Q.CGEventSetLocation(event, (x,y))
            for field, value in ((40,pid), (51,window_id), (91,window_id), (92,window_id),
                                 (88,1), (99,4 if index > steps else 1 if index == 1 else 2),
                                 (123,0), (96,dy), (97,dx), (93,dy*65536), (94,dx*65536)):
                Q.CGEventSetIntegerValueField(event, field, value)
            self.local(objc.pyobjc_id(event), bounds['width']/2, bounds['height']/2)
            self.post(pid, objc.pyobjc_id(event))
            time.sleep(0.01)
        check()
