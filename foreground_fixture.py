"""Disposable native app used only to verify foreground input isolation."""
import json
import os
import sys
import threading
import urllib.request

import AppKit
from Foundation import NSObject


def report(kind, value):
    def send():
        try:
            request = urllib.request.Request(sys.argv[1]+'event', data=json.dumps({'kind':kind,'value':value}).encode(), method='POST')
            urllib.request.urlopen(request,timeout=3).close()
        except OSError:
            pass
    threading.Thread(target=send,daemon=True).start()


class Delegate(NSObject):
    def applicationDidFinishLaunching_(self, notification):
        window.makeKeyAndOrderFront_(None)
        app.activateIgnoringOtherApps_(True)

    def applicationDidBecomeActive_(self, notification):
        window.makeKeyWindow()
        window.makeFirstResponder_(field)
        field.selectText_(None)
        report('foreground_ready',os.getpid())

    def controlTextDidChange_(self, notification):
        report('foreground_input', str(notification.object().stringValue()))


app = AppKit.NSApplication.sharedApplication()
app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
    ((200,250),(650,180)), AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable,
    AppKit.NSBackingStoreBuffered, False)
window.setTitle_('Monterey foreground input verification')
field = AppKit.NSTextField.alloc().initWithFrame_(((25,75),(600,50)))
field.setAccessibilityLabel_('Foreground input sentinel')
delegate = Delegate.alloc().init()
field.setDelegate_(delegate)
app.setDelegate_(delegate)
window.contentView().addSubview_(field)
app.run()
