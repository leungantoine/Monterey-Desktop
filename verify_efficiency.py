"""Compact MCP output and real Safari replacement, isolated localhost fixtures."""
import asyncio
import copy
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from compact_output import present, encode
from desktop import Desktop
from native_ui import front_app
from verify import Handler, ThreadingHTTPServer, decode, named, await_event
from verify_background import cleanup_safari

ROOT=Path(__file__).resolve().parent
MESSAGE_ONE='School notice café ✓. '*300
MESSAGE_TWO='Second school message 😀. '*300
MAIL_HTML=('''<!doctype html><meta charset="utf-8"><title>Monterey Desktop verification</title>
<h1 id="subject">Message one</h1><button id="next">Open next email</button>
<p id="body">'''+MESSAGE_ONE+'''</p><img src="/missing-image" alt="School attachment overview">
<input type="password" aria-label="Private test" value="never-read-this-secret">
<script>document.querySelector('#next').onclick=()=>{
document.querySelector('#subject').textContent='Message two';
document.querySelector('#body').textContent='''+json.dumps(MESSAGE_TWO)+''';
fetch('/event',{method:'POST',body:JSON.stringify({kind:'reader_next',value:'ok'})});};
fetch('/event',{method:'POST',body:JSON.stringify({kind:'reader_loaded',value:'ok'})});</script>''').encode()


class EfficiencyHandler(Handler):
    def do_GET(self):
        if self.path.startswith('/messages'):
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(MAIL_HTML)))
            self.end_headers();self.wfile.write(MAIL_HTML)
        elif self.path.startswith('/missing-image'):
            self.send_error(404)
        else:
            super().do_GET()


def pure_checks():
    nodes=[{'id':'e0','pid':7,'role':'AXWindow','name':'Window','enabled':True},
           {'id':'e1','pid':7,'role':'AXGroup','parent':'e0','focused':False},
           {'id':'e2','pid':7,'role':'AXTextField','parent':'e1','description':'Find café',
            'enabled':False,'value':'x'*2000,'value_omitted_chars':30,'value_settable':True,
            'bounds':{'x':2,'y':3,'width':4,'height':5},
            'screenshot_bounds':{'x':4,'y':6,'width':8,'height':10}},
           {'id':'e3','role':'AXTextField','subrole':'AXSecureTextField','value':'<redacted>'}]
    metadata={'frame_id':'fresh','ui':{'elements':nodes,'truncated':True},'apps':[],
              'windows':[],'target_app_pid':7,'space_scope':'active','target_space_ids':[1]}
    original=copy.deepcopy(metadata)
    compact=present(metadata)
    field=compact['ui']['elements'][1]
    assert field['id']=='e2' and field['enabled'] is False and field['value_settable']
    assert field['rect']==[4,6,8,10] and 'bounds' not in field and 'parent' not in field
    assert field['value_omitted_chars']==1530 and len(field['value'])==500
    assert compact['ui']['truncated'] and compact['ui']['omitted_count']==1
    assert present(metadata,'full') is metadata and metadata==original
    assert len(present(metadata,ui_query='CAFÉ')['ui']['elements'])==1
    assert present(metadata,ui_query='missing')['ui']['returned_count']==0
    assert present(metadata,ui_query='missing')['ui']['truncated']
    assert present(metadata)['ui']['elements'][-1]['value']=='<redacted>'
    assert 'apps' not in present(metadata,action=True)
    assert 'café' in encode(compact)
    print('PASS compact presentation preserves uncertainty, capabilities, secure redaction, Unicode and internal state',flush=True)


async def live(url):
    desktop=Desktop()
    subprocess.run(['/usr/bin/osascript','-e',
        'on run argv\ntell application "Safari"\nmake new document with properties {URL:item 1 of argv}\nend tell\nend run',url],
        check=True,capture_output=True,timeout=10)
    await await_event('loaded','ok')
    window=None
    for _ in range(40):
        window=next((w for w in desktop.windows() if w['title']=='Monterey Desktop verification'),None)
        if window:break
        await asyncio.sleep(.1)
    assert window is not None, 'The fixture window did not become visible.'
    async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(ROOT/'desktop.py'),'serve'],
                            env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})) as (read,write):
        async with ClientSession(read,write) as client:
            await client.initialize()
            tools={t.name:t for t in (await client.list_tools()).tools}
            assert tools['desktop_observe'].inputSchema['properties']['detail']['default']=='compact'
            full,_=decode(await client.call_tool('desktop_observe',{'window_id':window['window_id'],'include_image':False,'detail':'full'}))
            benchmark={
                'legacy_text_chars':len(json.dumps(full)),
                'compact_observe_chars':len(encode(present(full))),
                'compact_action_chars':len(encode(present(full,action=True))),
                'scanned_controls':len(full['ui']['elements'])}
            meta,picture=decode(await client.call_tool('desktop_observe',{
                'window_id':window['window_id'],'include_image':False,'ui_query':'Verification text'}))
            assert picture is None and meta['ui']['returned_count']==1 and meta['ui']['omitted_count']>0
            benchmark['filtered_observe_chars']=len(encode(meta))
            field=named(meta,'Verification text','AXTextField')
            assert 'rect' in field and 'bounds' not in field
            stale=meta['frame_id']
            for text in ('Replacement café ✓ 😀', '', 'Second replacement ✓'):
                meta,picture=decode(await client.call_tool('desktop_act',{
                    'frame_id':meta['frame_id'],'actions':[{'kind':'replace_text','element_id':field['id'],'text':text}],
                    'ui_query':'Verification text'}))
                assert picture is None and not meta['image_included'] and meta['settle']['elapsed_ms']==0
                assert 'apps' not in meta and 'windows' not in meta and meta['completed_actions']==1
                field=named(meta,'Verification text','AXTextField')
                assert field['value']==text
                await await_event('input',text)
            print('PASS compact filtered IDs, foreground Unicode/empty replacement, input callbacks and semantic image/settle omission',flush=True)
            meta,_=decode(await client.call_tool('desktop_act',{'frame_id':meta['frame_id'],'actions':[
                {'kind':'key','element_id':field['id'],'key':'cmd+a'},
                {'kind':'type','element_id':field['id'],'text':'Selection preserved ✓'}],'include_image':False,'settle_seconds':0}))
            assert named(meta,'Verification text','AXTextField')['value']=='Selection preserved ✓'
            await await_event('input','Selection preserved ✓')
            print('PASS repeated explicit targeting preserves select-all',flush=True)
            wrong=await client.call_tool('desktop_act',{'frame_id':stale,'actions':[{'kind':'wait','seconds':0}]})
            assert wrong.isError
            wrong=await client.call_tool('desktop_act',{'frame_id':meta['frame_id'],'actions':[{'kind':'click','x':10,'y':10}]})
            assert wrong.isError and 'fresh observed screenshot' in str(wrong.content)
            # Whole-batch prevalidation rejects an invalid replacement before a press.
            button=named(meta,'Test click','AXButton')
            wrong=await client.call_tool('desktop_act',{'frame_id':meta['frame_id'],'actions':[
                {'kind':'press','element_id':button['id']},
                {'kind':'replace_text','element_id':button['id'],'text':'invalid'}]})
            assert wrong.isError
            field=named(meta,'Verification text','AXTextField')
            meta,picture=decode(await client.call_tool('desktop_act',{'frame_id':meta['frame_id'],
                'actions':[{'kind':'replace_text','element_id':field['id'],'text':'Visual confirmation'}],'include_image':True}))
            assert picture is not None and meta['image_included']
            meta,_=decode(await client.call_tool('desktop_observe',{'window_id':window['window_id'],'detail':'full','include_image':False}))
            assert meta['apps'] and meta['windows'] and 'screenshot_bounds' in named(meta,'Verification text','AXTextField')
            full_action,_=decode(await client.call_tool('desktop_act',{'frame_id':meta['frame_id'],
                'actions':[{'kind':'wait','seconds':0}],'settle_seconds':0,'include_image':False,'detail':'full'}))
            benchmark['legacy_action_chars']=len(json.dumps(full_action))
            benchmark['compact_action_chars']=len(encode(present(full_action,action=True)))
            print('PASS full compatibility, explicit image override, stale/imageless/invalid-control batch guards',flush=True)
            subprocess.run(['/usr/bin/osascript','-e',
                'on run argv\ntell application "Safari"\nmake new document with properties {URL:item 1 of argv}\nend tell\nend run',url+'messages'],
                check=True,capture_output=True,timeout=10)
            await await_event('reader_loaded','ok')
            message_window=None
            for _ in range(40):
                message_window=next((w for w in desktop.windows() if w['title']=='Monterey Desktop verification' and w['window_id']!=window['window_id']),None)
                if message_window:break
                await asyncio.sleep(.1)
            assert message_window
            meta,picture=decode(await client.call_tool('desktop_observe',{'window_id':message_window['window_id']}))
            assert picture is None and not meta['image_included']
            old_frame=meta['frame_id']
            first,_=decode(await client.call_tool('desktop_read',{'frame_id':old_frame,'max_chars':200}))
            assert first['page_truncated'] and first['next_offset']==200 and not first['truncated']
            rest,_=decode(await client.call_tool('desktop_read',{'frame_id':old_frame,'offset':first['next_offset'],'max_chars':100000}))
            combined=first['text']+rest['text']
            assert MESSAGE_ONE.strip() in combined and 'School attachment overview' in combined
            assert 'never-read-this-secret' not in combined and rest['next_offset'] is None
            benchmark['native_read_ms']=first['read_ms']
            benchmark['native_read_chars']=first['total_chars']
            start=time.monotonic()
            meta,picture=decode(await client.call_tool('desktop_act',{
                'frame_id':old_frame,'feedback':'text','actions':[
                    {'kind':'press','element_id':named(meta,'Open next email','AXButton')['id']},
                    {'kind':'wait_for','role':'AXStaticText','value_contains':'Message two','enabled':None,'timeout':3}]}))
            benchmark['open_and_read_ms']=round((time.monotonic()-start)*1000)
            assert picture is None and meta['settle']['elapsed_ms']==0
            assert MESSAGE_TWO.strip() in meta['reading']['text'] and 'Message two' in meta['reading']['text']
            assert not meta['reading']['truncated'] and 'never-read-this-secret' not in encode(meta)
            assert not any(n['role']=='AXStaticText' for n in meta['ui']['elements'])
            named(meta,'Open next email','AXButton')
            expired=await client.call_tool('desktop_read',{'frame_id':old_frame,'offset':200})
            assert expired.isError
            print('PASS native-first Safari message reading: full long text, alt text, secure omission, pagination, and one-call open/read',flush=True)
            return benchmark


def main():
    pure_checks()
    previous=front_app()
    server=ThreadingHTTPServer(('127.0.0.1',0),EfficiencyHandler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url=f'http://127.0.0.1:{server.server_address[1]}/'
    try:
        metrics=asyncio.run(live(url))
        print('MEASUREMENTS '+json.dumps(metrics),flush=True)
        return metrics
    finally:
        cleanup_safari(url)
        server.shutdown();server.server_close()
        if previous:
            try:Desktop().ui.activate(previous['pid'],lambda:None)
            except (ValueError,RuntimeError):pass


if __name__=='__main__':main()
