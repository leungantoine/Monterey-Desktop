"""Complete owned Safari workflows: compare split calls with guarded workflows.

Measures local task latency and call/response counts, not model reasoning or
network time. Fixtures are closed and the prior foreground window restored.
"""
import argparse
import asyncio
import json
import statistics
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

import ApplicationServices as AX
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from native_ui import NativeUI,front_app,get_attr
from safari_browser import SafariBrowser


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/native'):
            body='''<!doctype html><meta charset="utf-8"><title>Owned native workflow fixture</title>
            <h1>Native workflow</h1><input id="first" aria-label="First name">
            <button id="open">Show details</button><div id="details" style="display:none">
            <input id="second" aria-label="Detail text"><button id="save">Save fixture</button></div>
            <p id="result">Waiting</p><input type="password" aria-label="Private fixture" value="do-not-return-this">
            <script>
            window.opens=0;
            document.querySelector('#open').onclick=()=>{window.opens++;document.querySelector('#details').style.display='block';};
            document.querySelector('#save').onclick=()=>document.querySelector('#result').textContent='Done: '+document.querySelector('#first').value+' / '+document.querySelector('#second').value;
            </script>'''
        else:
            body='''<!doctype html><meta charset="utf-8"><title>Owned browser workflow fixture</title>
            <h1 id="heading">Loading fixture</h1><main id="body">Old content</main>
            <script>setTimeout(()=>{document.querySelector('#heading').id='ready';
            document.querySelector('h1').textContent='Ready fixture';
            document.querySelector('#body').textContent='Complete fixture body café ✓ 😀';},200);</script>'''
        payload=body.encode()
        self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    def log_message(self,*_):
        pass


def decode(response):
    if response.isError:
        raise AssertionError(str(response.content))
    return json.loads(next(c.text for c in response.content if c.type=='text'))


async def measure(root,optimized,window_id,base,samples):
    params=StdioServerParameters(command=str(root/'scripts/start.sh'),args=[],cwd=str(root))
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as client:
            await client.initialize()
            listing=decode(await client.call_tool('desktop_browser',{'operation':'tabs'}))['result']
            window=next(w for w in listing['windows'] if w['window_id']==window_id)
            target={'window_id':window_id,'tab_index':1}
            current=window['tabs'][0]['url']
            async def browser(operation,**kwargs):
                return decode(await client.call_tool('desktop_browser',{'operation':operation,**target,**kwargs}))
            output={}
            for task in ('browser','native'):
                runs=[]
                for index in range(samples):
                    calls=0
                    chars=0
                    async def call(tool,arguments):
                        nonlocal calls,chars
                        calls+=1
                        response=await client.call_tool(tool,arguments)
                        chars+=sum(len(c.text) for c in response.content if c.type=='text')
                        return decode(response)
                    if task=='browser':
                        destination=base+f'/message?variant={int(optimized)}&sample={index}'
                        start=time.perf_counter()
                        if optimized:
                            result=await call('desktop_browser',{'operation':'navigate_read',**target,'expected_url':current,
                                'url':destination,'ready_selector':'#ready','read_selector':'#body','text_contains':'Complete fixture body','timeout':5})
                            assert result['result']['text']=='Complete fixture body café ✓ 😀'
                            assert result['result']['workflow']['completed_steps']==['navigate','wait','read']
                        else:
                            await call('desktop_browser',{'operation':'navigate',**target,'expected_url':current,'url':destination})
                            for _ in range(30):
                                ready=await call('desktop_browser',{'operation':'evaluate',**target,'expected_url':destination,
                                    'script':'document.readyState==="complete" && !!document.querySelector("#ready")'})
                                if ready['result']:
                                    break
                                await asyncio.sleep(.1)
                            else:
                                raise AssertionError('Split workflow did not become ready')
                            result=await call('desktop_browser',{'operation':'read',**target,'expected_url':destination})
                            assert 'Complete fixture body café ✓ 😀' in result['result']['text']
                        current=destination
                    else:
                        native=base+'/native'
                        await browser('navigate',expected_url=current,url=native)
                        current=native
                        for _ in range(30):
                            ready=await browser('evaluate',expected_url=current,
                                script='document.readyState==="complete" && !!document.querySelector("#first")')
                            if ready['result']:
                                break
                            await asyncio.sleep(.1)
                        metadata=decode(await client.call_tool('desktop_observe',{'window_id':window['cg_window_id'],'include_image':False}))
                        first=next(n for n in metadata['ui']['elements'] if n.get('description')=='First name')
                        metadata=decode(await client.call_tool('desktop_act',{'frame_id':metadata['frame_id'],
                            'actions':[{'kind':'focus','element_id':first['id']}],'include_image':False}))
                        assert not any(n.get('description')=='Detail text' for n in metadata['ui']['elements'])
                        expected='Done: First café ✓ / Second 😀 ✓'
                        steps=[{'kind':'replace_text','name':'First name','role':'AXTextField','text':'First café ✓'},
                               {'kind':'press','name':'Show details','role':'AXButton'},
                               {'kind':'replace_text','name':'Detail text','role':'AXTextField','text':'Second 😀 ✓'},
                               {'kind':'press','name':'Save fixture','role':'AXButton'},
                               {'kind':'wait_for','role':'AXStaticText','value_contains':expected,'enabled':None}]
                        start=time.perf_counter()
                        if optimized:
                            result=await call('desktop_plan',{'frame_id':metadata['frame_id'],'steps':steps,'feedback':'text','timeout':15})
                            assert result['plan']['completed_steps']==5
                            assert expected in result['reading']['text']
                        else:
                            # Give the previous release its best supported batching:
                            # known controls together, then newly revealed controls
                            # plus readiness and embedded reading in the second call.
                            for group in (steps[:2],steps[2:4]):
                                actions=[]
                                for step in group:
                                    node=next(n for n in metadata['ui']['elements'] if step['name'] in (n.get('name'),n.get('description')) and n['role']==step['role'])
                                    action={'kind':step['kind'],'element_id':node['id']}
                                    if 'text' in step:action['text']=step['text']
                                    actions.append(action)
                                final=group[-1]['name']=='Save fixture'
                                if final:actions.append({k:v for k,v in steps[-1].items()})
                                metadata=await call('desktop_act',{'frame_id':metadata['frame_id'],'actions':actions,
                                    'feedback':'text' if final else 'controls','include_image':False})
                            result=metadata
                            assert expected in result['reading']['text']
                        assert 'do-not-return-this' not in json.dumps(result)
                    runs.append({'elapsed_ms':round((time.perf_counter()-start)*1000,1),
                                 'tool_calls':calls,'response_chars':chars})
                output[task]={'runs':runs,'median_ms':round(statistics.median(r['elapsed_ms'] for r in runs),1),
                    'median_tool_calls':statistics.median(r['tool_calls'] for r in runs),
                    'median_response_chars':statistics.median(r['response_chars'] for r in runs)}
            if optimized:
                # One input can complete before a later step fails; never replay it.
                frame=decode(await client.call_tool('desktop_observe',{'window_id':window['cg_window_id'],'include_image':False}))
                error=await client.call_tool('desktop_plan',{'frame_id':frame['frame_id'],'steps':[
                    {'kind':'press','name':'Show details','role':'AXButton'},
                    {'kind':'wait_for','name':'Never exists','timeout':.1}],'feedback':'controls'})
                assert error.isError and 'after 1 completed steps' in str(error.content)
                observed=(await browser('evaluate',expected_url=current,script='window.opens'))['result']
                assert observed==2, 'Failed plan replayed its completed press'
                stale=await client.call_tool('desktop_act',{'frame_id':frame['frame_id'],'actions':[{'kind':'wait','seconds':0}]})
                assert stale.isError
                print('PASS native plan stops after partial execution, does not replay, invalidates old frames, and omits secure values',flush=True)
            return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--baseline-root',type=Path)
    parser.add_argument('--samples',type=int,default=3)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    assert args.samples>=1
    previous=front_app();ui=NativeUI()
    previous_window=ui.window(previous['pid']) if previous else None
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_address[1]}'
    owned=None;browser=SafariBrowser()
    try:
        subprocess.run(['/usr/bin/osascript','-l','JavaScript','-e',
            'function run(a){Application("Safari").Document({url:a[0]}).make();}',base+'/message?start=1'],
            capture_output=True,text=True,check=True,timeout=10)
        for _ in range(30):
            matches=[w for w in browser.tabs() if any(t['url']==base+'/message?start=1' for t in w['tabs'])]
            if len(matches)==1:owned=matches[0]['window_id'];break
            time.sleep(.1)
        assert owned is not None
        baseline=asyncio.run(measure((args.baseline_root or args.root).resolve(),False,owned,base,args.samples))
        optimized=asyncio.run(measure(args.root.resolve(),True,owned,base,args.samples))
        metrics={'date':'2026-10-05','baseline_version':'1.7.0' if args.baseline_root else 'split calls',
                 'optimized_version':'1.8.0','baseline':baseline,'optimized':optimized,
                 'baseline_native_strategy':'Two desktop_act batches: fill/reveal, then fill/save/wait with feedback=text. All initially exposed controls batched; no separate read call.',
                 'optimized_native_strategy':'One desktop_plan resolving five fresh selectors and returning final reading.',
                 'scope':'Complete local task latency and MCP call/response counts on owned Safari fixtures. Initial discovery/navigation/focus setup shared and excluded. Model reasoning/network time not measured.'}
        if args.output:args.output.write_text(json.dumps(metrics,indent=2)+'\n')
        print(json.dumps(metrics,indent=2),flush=True)
    finally:
        if owned is not None:
            subprocess.run(['/usr/bin/osascript','-l','JavaScript','-e',
                '''function run(a){Application("Safari").windows().forEach(function(w){
                if(String(w.id())===a[0] && w.tabs().every(function(t){return t.url().indexOf(a[1])===0;}))w.close();});}''',
                str(owned),base+'/'],capture_output=True,text=True,check=True,timeout=10)
        server.shutdown();server.server_close()
        if previous:
            if previous_window is not None and get_attr(previous_window,'AXRole')=='AXWindow':AX.AXUIElementPerformAction(previous_window,'AXRaise')
            ui.activate(previous['pid'],lambda:None)


if __name__=='__main__':main()
