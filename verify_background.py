"""Live background input proof against Safari and a disposable foreground app."""
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import ctypes as C
from collections import deque
from pathlib import Path

import Quartz as Q
import AppKit
import ApplicationServices as AX
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client

from desktop import Desktop, PAUSED, Key, Wait
from native_ui import front_app, running_apps, get_attr
from verify import ClientSession, Handler, ThreadingHTTPServer, EVENTS, HTML, decode, named, await_event, find_color

ROOT = Path(__file__).resolve().parent


class BackgroundHandler(Handler):
    def do_GET(self):
        html = HTML.replace(b"document.querySelector('#text').oninput=e=>report('input',e.target.value);",
                            b"document.querySelector('#text').oninput=e=>{e.target.style.backgroundColor='#13db51';report('input',e.target.value)};")
        if self.path.startswith('/secondary'):
            html = html.replace(b'Monterey Desktop verification</title>',b'Monterey Desktop verification secondary</title>').replace(b'Verification text',b'Secondary verification text')
        self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(html)))
        self.end_headers();self.wfile.write(html)


def cleanup_safari(url=None):
    prefix = url or 'http://127.0.0.1:'
    script = f'''tell application "Safari"
repeat with i from (count of windows) to 1 by -1
    try
        set w to window i
        if (name of current tab of w is "Monterey Desktop verification" or name of current tab of w is "Monterey Desktop verification secondary") and (URL of current tab of w starts with "{prefix}") then close w
    end try
end repeat
end tell'''
    subprocess.run(['/usr/bin/osascript','-e',script],check=True,capture_output=True,timeout=10)


def user_typing(text):
    """Simulated user input to the real foreground, independent of the MCP server."""
    source = Q.CGEventSourceCreate(Q.kCGEventSourceStatePrivate)
    for offset in range(0,len(text),16):
        chunk = text[offset:offset+16]
        for down in (True,False):
            event = Q.CGEventCreateKeyboardEvent(source,0,down)
            Q.CGEventSetFlags(event,0)
            Q.CGEventKeyboardSetUnicodeString(event,len(chunk.encode('utf-16-le'))//2,chunk)
            Q.CGEventPost(Q.kCGHIDEventTap,event)
            time.sleep(0.04)
        time.sleep(0.06)


def switch_space(desktop, target):
    """Test setup/cleanup only: select an observed Mission Control desktop button."""
    info = desktop.spaces.snapshot()
    if info['active_space_id'] == target:
        return
    dock = next(app for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications() if app.bundleIdentifier()=='com.apple.dock')
    root = AX.AXUIElementCreateApplication(dock.processIdentifier())
    tried=set()
    for _ in range(30):
        if not any(get_attr(child,'AXIdentifier')=='mc' for child in get_attr(root,'AXChildren') or []):
            services=C.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
            toggle=desktop.spaces.bind(services,'CoreDockSendNotification',C.c_int32,[C.c_void_p,C.c_int32])
            make_string=desktop.spaces.bind(desktop.spaces.cf,'CFStringCreateWithCString',C.c_void_p,[C.c_void_p,C.c_char_p,C.c_uint32])
            message=make_string(None,b'com.apple.expose.awake',0x08000100)
            try:assert toggle(message,0)==0
            finally:desktop.spaces.release(message)
            time.sleep(.4)
        queue = deque([root]);matches=[]
        for _ in range(200):
            if not queue:break
            element=queue.popleft()
            description=get_attr(element,'AXDescription') or ''
            if get_attr(element,'AXRole')=='AXButton' and description.startswith('exit to Desktop ') and description not in tried:
                matches.append((element,description))
            queue.extend(get_attr(element,'AXChildren') or [])
        if matches:
            element,description=matches[0]
            tried.add(description)
            error=AX.AXUIElementPerformAction(element,'AXPress')
            assert error==0,error
            time.sleep(.8)
            if desktop.spaces.active_id()==target:return
        time.sleep(.1)
    raise AssertionError('Could not find the target desktop button in Mission Control.')


async def verify(url, across_spaces=False):
    desktop = Desktop()
    subprocess.run(['/usr/bin/osascript','-e',f'tell application "Safari"\nmake new document with properties {{URL:"{url}"}}\nset bounds of front window to {{50,70,1050,820}}\nend tell'],check=True,capture_output=True,timeout=10)
    await await_event('loaded','ok')
    safari = next(a for a in running_apps() if a['bundle_id']=='com.apple.Safari')
    async def window_named(title):
        for _ in range(40):
            matches = [w['window_id'] for w in desktop.windows() if w['pid']==safari['pid'] and w['title']==title]
            if len(matches)==1:return matches[0]
            await asyncio.sleep(.1)
        raise AssertionError('Fixture window unavailable: '+title+' '+str([(w['title'],w['window_id']) for w in desktop.windows() if w['pid']==safari['pid']]))
    first_id = await window_named('Monterey Desktop verification')
    subprocess.run(['/usr/bin/osascript','-e',f'tell application "Safari"\nmake new document with properties {{URL:"{url}secondary"}}\nset bounds of front window to {{350,110,1350,860}}\nend tell'],check=True,capture_output=True,timeout=10)
    await asyncio.sleep(0.5)
    second_id = await window_named('Monterey Desktop verification secondary')
    assert first_id != second_id
    if across_spaces:
        target_space = desktop.spaces.active_id()
        previous_frame,_ = desktop.observe(window_id=first_id,include_image=False)
        other = next(space['space_id'] for space in desktop.spaces.snapshot()['spaces']
                     if space['type']=='desktop' and space['space_id']!=target_space)
        switch_space(desktop,other)
        assert first_id not in [w['window_id'] for w in desktop.windows()]
        assert first_id in [w['window_id'] for w in desktop.windows('all')]
        try:
            desktop.act([Wait(kind='wait',seconds=0)],previous_frame['frame_id'],mode='background')
        except ValueError as error:
            assert 'Space changed' in str(error)
        else:
            raise AssertionError('An active-scope frame survived a Space switch.')
        try:
            desktop.observe(window_id=first_id)
        except ValueError as error:
            assert 'space_scope=all' in str(error)
        else:
            raise AssertionError('Default observation reached another Space.')
        print('PASS active default excludes other Spaces and a Space switch invalidates its frame',flush=True)
    active_space = desktop.spaces.active_id()
    fixture = subprocess.Popen([sys.executable,str(ROOT/'foreground_fixture.py'),url],env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        await await_event('foreground_ready',fixture.pid)
        assert front_app()['pid'] == fixture.pid
        pointer = Q.CGEventGetLocation(Q.CGEventCreate(None))
        parameters = StdioServerParameters(command=sys.executable,args=[str(ROOT/'desktop.py'),'serve'],env={'PYTHONDONTWRITEBYTECODE':'1'})
        async with stdio_client(parameters) as (read,write):
            async with ClientSession(read,write) as client:
                await client.initialize()
                if across_spaces:
                    unseen,picture = decode(await client.call_tool('desktop_observe',{'window_id':first_id,'space_scope':'all','include_image':None,'max_width':2000}))
                    if unseen['ui'].get('available') is False:
                        assert picture is not None  # Native-unavailable visual fallback.
                    elif picture is None:
                        unseen,picture=decode(await client.call_tool('desktop_observe',{'window_id':first_id,'space_scope':'all','include_image':True,'max_width':2000}))
                    assert picture is not None
                    assert unseen['space_info']['active_space_id'] not in unseen['target_space_ids']
                    point=find_color(picture,(0,212,204))
                    forbidden=await client.call_tool('desktop_act',{'frame_id':unseen['frame_id'],'mode':'foreground','actions':[{'kind':'key','key':'cmd+a'}]})
                    assert forbidden.isError and 'requires mode=background' in str(forbidden.content)
                    result=await client.call_tool('desktop_act',{'frame_id':unseen['frame_id'],'mode':'background','actions':[
                        {'kind':'click',**point},{'kind':'key','key':'cmd+a'},{'kind':'type','text':'Cross-Space visual text ✓'}],
                        'include_image':True,'settle_seconds':0})
                    evidence,picture=decode(result)
                    await await_event('input','Cross-Space visual text ✓')
                    assert desktop.spaces.active_id()==active_space and front_app()['pid']==fixture.pid
                    assert Q.CGEventGetLocation(Q.CGEventCreate(None))==pointer
                    find_color(picture,(19,219,81))
                    if unseen['ui'].get('available') is False:
                        assert evidence['action_results'][-1]['effect']=='not_verified'
                    print('PASS unseen other-Space window: screenshot click/typing, fresh rendered pixels, foreground refusal, and no Space switch',flush=True)
                    # Retain native AX handles while visible, then exercise those
                    # exact controls after the user changes Spaces.
                    switch_space(desktop,target_space)
                    decode(await client.call_tool('desktop_observe',{'window_id':first_id,'include_image':False}))
                    switch_space(desktop,active_space)
                    desktop.ui.activate(fixture.pid,lambda:None)
                    pointer = Q.CGEventGetLocation(Q.CGEventCreate(None))
                async def observe(image=False,window_id=first_id):
                    result = await client.call_tool('desktop_observe',{'window_id':window_id,'include_image':image,'max_width':2000,
                                                                     'space_scope':'all' if across_spaces else 'active'})
                    return decode(result)[0]
                metadata = await observe()
                async def act(actions,image=False):
                    nonlocal metadata
                    result = await client.call_tool('desktop_act',{'frame_id':metadata['frame_id'],'actions':actions,'mode':'background','settle_seconds':0,'include_image':image})
                    metadata = decode(result)[0]
                    assert metadata['front_app']['pid'] == fixture.pid
                    assert not metadata['background_safety']['global_input_sent']
                    assert not metadata['background_safety']['space_switch_requested']
                    assert desktop.spaces.active_id() == active_space
                    assert Q.CGEventGetLocation(Q.CGEventCreate(None)) == pointer
                    return metadata
                field = named(metadata,'Verification text','AXTextField')
                EVENTS.clear()
                initial=[{'kind':'key','element_id':field['id'],'key':'cmd+a'}] if across_spaces else []
                typed={'kind':'type','text':'Background ✓ 😀'}
                if not across_spaces:typed['element_id']=field['id']
                await act(initial+[typed])
                await await_event('input','Background ✓ 😀')
                assert metadata['action_results'][-1]['effect']=='text_observed'
                print('PASS background field focus and Unicode with foreground/pointer preserved',flush=True)
                field = named(metadata,'Verification text','AXTextField')
                await act([{'kind':'key','element_id':field['id'],'key':'cmd+a'},{'kind':'type','element_id':field['id'],'text':'Replaced in background ✓'}])
                await await_event('input','Replaced in background ✓')
                print('PASS background shortcut and replacement typing',flush=True)
                for replacement in ('', 'Replacement action ✓ 😀'):
                    field = named(metadata,'Verification text','AXTextField')
                    metadata, picture = decode(await client.call_tool('desktop_act', {
                        'detail':'compact','frame_id':metadata['frame_id'],'mode':'background',
                        'actions':[{'kind':'replace_text','element_id':field['id'],'text':replacement}]}))
                    assert picture is None and metadata['settle']['elapsed_ms']==0
                    assert named(metadata,'Verification text','AXTextField')['value']==replacement
                    assert metadata['action_results'][0]['effect']=='value_observed'
                    assert metadata['front_app']['pid']==fixture.pid
                    assert not metadata['background_safety']['space_switch_requested']
                    assert desktop.spaces.active_id()==active_space
                    await await_event('input',replacement)
                print('PASS compact verified background replacement, empty text, auto image/settle omission',flush=True)
                field = named(metadata,'Verification text','AXTextField')
                await act([{'kind':'set_value','element_id':field['id'],'value':'Background native value ✓'}])
                await await_event('input','Background native value ✓')
                print('PASS verified background field value without activation fallback',flush=True)
                await act([{'kind':'press','element_id':named(metadata,'Test click','AXButton')['id']},
                           {'kind':'wait_for','name':'Delayed test','role':'AXButton','timeout':4}])
                await await_event('ready','ok')
                print('PASS background native press and readiness',flush=True)
                metadata = await observe(True)
                # A visual-only div exercises raw targeted clicks and dragging.
                drag = next(n for n in metadata['ui']['elements'] if n.get('value')=='Drag me' or n.get('name')=='Drag me')
                target = next(n for n in metadata['ui']['elements'] if n.get('value')=='Drop here' or n.get('name')=='Drop here')
                def center(node):
                    b=node['screenshot_bounds'];return {'x':b['x']+b['width']/2,'y':b['y']+b['height']/2}
                start,end = center(drag),center(target)
                EVENTS.clear()
                await act([{'kind':'move',**start},{'kind':'drag',**start,'to_x':end['x'],'to_y':end['y'],'duration':0.4}],image=True)
                await await_event('drag','ok')
                print('PASS background hover and drag with physical pointer preserved',flush=True)
                await act([{'kind':'scroll','delta_y':350}],image=True)
                await await_event('scroll')
                assert any(e['kind']=='scroll' and e['value']>0 for e in EVENTS)
                await act([{'kind':'scroll','delta_y':-5000}],image=True)
                await asyncio.sleep(.2)
                metadata = await observe()
                print('PASS background window scrolling',flush=True)
                # Global foreground typing must still go to the user app while background text is flowing.
                field = named(metadata,'Verification text','AXTextField')
                await act([{'kind':'key','element_id':field['id'],'key':'cmd+a'}])
                EVENTS.clear()
                payload = 'Agent background text ✓ 😀 '*14
                task = asyncio.create_task(act([{'kind':'type','text':payload}]))
                await await_event('input')
                await asyncio.to_thread(user_typing,'Human foreground typing ✓')
                await task
                await await_event('foreground_input','Human foreground typing ✓')
                await await_event('input',payload)
                assert named(metadata,'Verification text','AXTextField')['value']==payload
                print('PASS simultaneous user foreground typing and background agent typing stay isolated',flush=True)
                field = named(metadata,'Verification text','AXTextField')
                bad = await client.call_tool('desktop_act',{'frame_id':metadata['frame_id'],'mode':'background','actions':[
                    {'kind':'key','element_id':field['id'],'key':'tab'},
                    {'kind':'type','text':'Must not refocus the old field'}]})
                assert bad.isError and 'Secure' in str(bad.content)
                metadata = await observe()
                assert named(metadata,'Verification text','AXTextField')['value']==payload
                print('PASS typing follows live focus after Tab and refuses a secure field',flush=True)
                other = await observe(window_id=second_id)
                assert named(other,'Secondary verification text','AXTextField').get('value','')==''
                print('PASS exact Safari window targeting leaves second window unchanged',flush=True)
                metadata = await observe()
                clicks = sum(e['kind']=='click' for e in EVENTS)
                bad = await client.call_tool('desktop_act',{'frame_id':metadata['frame_id'],'mode':'background','actions':[
                    {'kind':'press','element_id':named(metadata,'Test click','AXButton')['id']},
                    {'kind':'activate','pid':safari['pid']}]})
                assert bad.isError and 'foreground mode' in str(bad.content)
                assert sum(e['kind']=='click' for e in EVENTS)==clicks
                secret = named(metadata,'Protected test','AXTextField')
                bad = await client.call_tool('desktop_act',{'frame_id':metadata['frame_id'],'mode':'background','actions':[{'kind':'focus','element_id':secret['id']}]})
                assert bad.isError and 'Secure' in str(bad.content)
                print('PASS unsupported/activation/secure-field batches rejected before input',flush=True)
                metadata = await observe()
                replacement='Plan background café ✓'
                metadata,picture=decode(await client.call_tool('desktop_plan',{
                    'frame_id':metadata['frame_id'],'mode':'background','feedback':'controls','steps':[
                        {'kind':'replace_text','name':'Verification text','role':'AXTextField','text':replacement},
                        {'kind':'wait_for','name':'Verification text','role':'AXTextField','value_contains':replacement}]}))
                assert picture is None and metadata['plan']['completed_steps']==2
                assert named(metadata,'Verification text','AXTextField')['value']==replacement
                await await_event('input',replacement)
                assert metadata['front_app']['pid']==fixture.pid and front_app()['pid']==fixture.pid
                assert desktop.spaces.active_id()==active_space
                assert Q.CGEventGetLocation(Q.CGEventCreate(None))==pointer
                print('PASS native background plan resolves fresh controls and preserves foreground/pointer/Space',flush=True)
                desktop.ui.activate(safari['pid'],lambda:None)
                bad = await client.call_tool('desktop_act',{'frame_id':metadata['frame_id'],'mode':'background','actions':[{'kind':'scroll','delta_y':1}]})
                assert bad.isError and 'yields' in str(bad.content)
                print('PASS background mode yields when the user brings the target app forward',flush=True)
    finally:
        fixture.terminate()
        fixture.wait(timeout=5)
        errors=fixture.stderr.read().decode()
        if 'Traceback' in errors:print(errors,flush=True)


def main(across_spaces=False):
    if PAUSED.exists():raise RuntimeError('Resume the helper locally before testing.')
    previous = front_app()
    original_space = Desktop().spaces.active_id()
    cleanup_safari()
    server = ThreadingHTTPServer(('127.0.0.1',0),BackgroundHandler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url = f'http://127.0.0.1:{server.server_address[1]}/'
    try:
        asyncio.run(verify(url,across_spaces))
        print('ALL CROSS-SPACE CHECKS PASSED' if across_spaces else 'ALL BACKGROUND CHECKS PASSED',flush=True)
    finally:
        cleanup_safari(url)
        server.shutdown();server.server_close()
        if across_spaces:
            switch_space(Desktop(),original_space)
        if previous:
            try:Desktop().ui.activate(previous['pid'],lambda:None)
            except (ValueError,RuntimeError):pass


if __name__=='__main__':main(across_spaces='--across-spaces' in sys.argv)
