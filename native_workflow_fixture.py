"""Disposable AppKit-only form for native workflow benchmarks. No web view."""
import json
import os
import sys
from pathlib import Path
import AppKit
from Foundation import NSObject,NSTimer

STATE=Path(sys.argv[1])
events=[]
def record(kind):
    events.append(kind)
    data={'pid':os.getpid(),'first':str(first.stringValue()),'second':str(second.stringValue()),
          'result':str(result.stringValue()),'events':events,'secondary':str(sentinel.stringValue())}
    temporary=STATE.with_suffix('.tmp');temporary.write_text(json.dumps(data,ensure_ascii=False));temporary.replace(STATE)

class Delegate(NSObject):
    def applicationDidFinishLaunching_(self,notification):
        secondary.orderFront_(None);window.makeKeyAndOrderFront_(None)
        app.activateIgnoringOtherApps_(True);record('ready')
    def show_(self,sender):
        record('show')
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(.15,self,'reveal:',None,False)
    def reveal_(self,timer):
        second.setHidden_(False);save.setHidden_(False);record('revealed')
    def save_(self,sender):
        record('save')
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(.15,self,'finish:',None,False)
    def finish_(self,timer):
        result.setStringValue_('Done: '+str(first.stringValue())+' / '+str(second.stringValue()));record('done')
    def reset_(self,sender):
        first.setStringValue_('');second.setStringValue_('');second.setHidden_(True);save.setHidden_(True)
        result.setStringValue_('Waiting');window.makeFirstResponder_(first);record('reset')
    def controlTextDidChange_(self,notification):record('input')

app=AppKit.NSApplication.sharedApplication()
app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
style=AppKit.NSWindowStyleMaskTitled|AppKit.NSWindowStyleMaskClosable
window=AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(((80,100),(1000,690)),style,AppKit.NSBackingStoreBuffered,False)
window.setTitle_('Monterey native workflow benchmark')
secondary=AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(((400,200),(400,160)),style,AppKit.NSBackingStoreBuffered,False)
secondary.setTitle_('Monterey native isolation sentinel')
sentinel=AppKit.NSTextField.alloc().initWithFrame_(((20,60),(340,40)))
sentinel.setStringValue_('Unchanged secondary window');secondary.contentView().addSubview_(sentinel)
delegate=Delegate.alloc().init();app.setDelegate_(delegate)

def field(label,identifier,rect,editable=True):
    f=AppKit.NSTextField.alloc().initWithFrame_(rect)
    f.setAccessibilityLabel_(label);f.setAccessibilityIdentifier_(identifier);f.setEditable_(editable)
    if not editable:f.setBezeled_(False);f.setDrawsBackground_(False)
    f.setDelegate_(delegate);window.contentView().addSubview_(f);return f
def button(title,identifier,rect,selector):
    b=AppKit.NSButton.alloc().initWithFrame_(rect);b.setTitle_(title);b.setAccessibilityIdentifier_(identifier)
    b.setTarget_(delegate);b.setAction_(selector);window.contentView().addSubview_(b);return b
first=field('First name','first',((25,615),(380,36)))
show=button('Show details','show',((440,615),(180,36)),'show:')
second=field('Detail text','second',((25,560),(380,36)));second.setHidden_(True)
save=button('Save native fixture','save',((440,560),(200,36)),'save:');save.setHidden_(True)
result=field('Result','result',((25,505),(920,34)),False);result.setStringValue_('Waiting')
reset=button('Reset native fixture','reset',((720,615),(230,36)),'reset:')
secret=AppKit.NSSecureTextField.alloc().initWithFrame_(((25,465),(320,28)))
secret.setAccessibilityLabel_('Protected fixture');secret.setStringValue_('native-secret-not-returned');window.contentView().addSubview_(secret)
for i in range(48):
    x=25+(i%3)*320;y=435-(i//3)*25
    f=field(f'Context {i+1}',f'context-{i}',((x,y),(305,22)),False)
    f.setStringValue_(f'Background context {i+1}: already reviewed')
app.run()
