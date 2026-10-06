"""Owned Safari action/read regression and local MCP workflow benchmark.

Discovery/reset is excluded. Model latency is not measured. Restores prior focus.
"""
import argparse
import asyncio
import json
import statistics
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import ApplicationServices as AX
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from native_ui import NativeUI, front_app, get_attr
from safari_browser import SafariBrowser
from verify_workflows_live import decode


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        payload='''<!doctype html><meta charset="utf-8"><title>Owned DOM action fixture</title>
        <input id="first"><button id="show">Show details</button>
        <div id="details" style="display:none"><input id="second"><button id="save">Save</button></div>
        <p id="result">Waiting</p><button class="duplicate">A</button><button class="duplicate">B</button>
        <input id="readonly" readonly value="unchanged"><button id="move">Change owned URL</button>
        <script>window.opens=0;window.saves=0;window.inputs=0;
        document.addEventListener('input',()=>window.inputs++);
        show.onclick=()=>{window.opens++;setTimeout(()=>details.style.display='block',200);};
        save.onclick=()=>{window.saves++;setTimeout(()=>result.textContent='Done: '+first.value+' / '+second.value,200);};
        move.onclick=()=>history.pushState({},'', '/changed');</script>'''.encode()
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
    def log_message(self,*_):pass


async def measure(root,owned,base,samples,optimized):
    async with stdio_client(StdioServerParameters(command=str(root/'scripts/start.sh'),args=[],cwd=str(root))) as streams:
        async with ClientSession(*streams) as client:
            await client.initialize()
            target={'window_id':owned,'tab_index':1};url=base+'/form'
            listing=decode(await client.call_tool('desktop_browser',{'operation':'tabs'}))['result']
            window=next(w for w in listing['windows'] if w['window_id']==owned)
            async def browser(operation,**kwargs):
                return decode(await client.call_tool('desktop_browser',{'operation':operation,**target,**kwargs}))
            async def reset():
                current=(await browser('read'))['result']['url']
                await browser('navigate',expected_url=current,url=url)
                for _ in range(40):
                    ready=await browser('evaluate',expected_url=url,script='document.readyState==="complete" && typeof window.opens==="number"')
                    if ready['result']:return
                    await asyncio.sleep(.05)
                raise AssertionError('Fixture did not load')
            steps=[{'kind':'replace_text','selector':'#first','text':'First café ✓'},
                   {'kind':'click','selector':'#show'},
                   {'kind':'replace_text','selector':'#second','text':'Second 😀 ✓'},
                   {'kind':'click','selector':'#save'}]
            expected='Done: First café ✓ / Second 😀 ✓';runs=[]
            for _ in range(samples):
                await reset();calls=0;chars=0
                async def call(operation,**kwargs):
                    nonlocal calls,chars
                    response=await client.call_tool('desktop_browser',{'operation':operation,**target,'expected_url':url,**kwargs})
                    calls+=1;chars+=sum(len(c.text) for c in response.content if c.type=='text')
                    return decode(response)
                start=time.perf_counter()
                if optimized:
                    result=await call('act_read',steps=steps,ready_selector='#result',text_contains=expected,read_selector='#result',timeout=5)
                    assert result['result']['text']==expected and result['result']['workflow']['completed_steps']==4
                else:
                    await call('evaluate',script="(() => {first.value='First café ✓';first.dispatchEvent(new Event('input',{bubbles:true}));show.click();return true;})()")
                    for _ in range(40):
                        if (await call('evaluate',script="getComputedStyle(details).display!=='none'"))['result']:break
                        await asyncio.sleep(.05)
                    else:raise AssertionError('Details never appeared')
                    await call('evaluate',script="(() => {second.value='Second 😀 ✓';second.dispatchEvent(new Event('input',{bubbles:true}));save.click();return true;})()")
                    for _ in range(40):
                        result=await call('evaluate',script="document.querySelector('#result').innerText")
                        if result['result']==expected:break
                        await asyncio.sleep(.05)
                    else:raise AssertionError('Result never appeared')
                runs.append({'elapsed_ms':round((time.perf_counter()-start)*1000,1),'tool_calls':calls,'response_chars':chars})
                oracle=(await browser('evaluate',expected_url=url,script='({opens:window.opens,saves:window.saves,inputs:window.inputs,first:first.value,second:second.value})'))['result']
                assert oracle=={'opens':1,'saves':1,'inputs':2,'first':'First café ✓','second':'Second 😀 ✓'},oracle
            if optimized:
                await reset()
                frame=decode(await client.call_tool('desktop_observe',{'window_id':window['cg_window_id'],'include_image':False}))
                async def failure(test_steps,completed,substring):
                    response=await client.call_tool('desktop_browser',{'operation':'act_read',**target,'expected_url':url,
                        'steps':test_steps,'ready_selector':'#result','timeout':2})
                    assert response.isError and f'after {completed} completed steps' in str(response.content) and substring in str(response.content),response
                await failure([{'kind':'click','selector':'#show'},{'kind':'click','selector':'.duplicate'}],1,'Ambiguous')
                assert (await browser('evaluate',expected_url=url,script='window.opens'))['result']==1
                stale=await client.call_tool('desktop_act',{'frame_id':frame['frame_id'],'actions':[{'kind':'wait','seconds':0}]})
                assert stale.isError
                await failure([{'kind':'replace_text','selector':'#readonly','text':'changed'}],0,'read-only')
                assert (await browser('evaluate',expected_url=url,script='document.querySelector("#readonly").value'))['result']=='unchanged'
                await failure([{'kind':'click','selector':'#move'},{'kind':'click','selector':'#show'}],1,'URL changed')
                assert (await browser('evaluate',expected_url=base+'/changed',script='window.opens'))['result']==1
                print('PASS partial completion, no replay, ambiguity/read-only/URL guards, and frame invalidation',flush=True)
            return {'runs':runs,'median_ms':round(statistics.median(r['elapsed_ms'] for r in runs),1),
                    'median_tool_calls':statistics.median(r['tool_calls'] for r in runs),
                    'median_response_chars':statistics.median(r['response_chars'] for r in runs)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--baseline-root',type=Path,required=True)
    parser.add_argument('--samples',type=int,default=3);parser.add_argument('--output',type=Path)
    args=parser.parse_args();assert args.samples>=1
    previous=front_app();ui=NativeUI();prior=ui.window(previous['pid']) if previous else None
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_address[1]}';owned=None;browser=SafariBrowser()
    try:
        subprocess.run(['/usr/bin/osascript','-l','JavaScript','-e',
            'function run(a){Application("Safari").Document({url:a[0]}).make();}',base+'/form'],capture_output=True,text=True,check=True,timeout=10)
        for _ in range(30):
            matches=[w for w in browser.tabs() if any(t['url']==base+'/form' for t in w['tabs'])]
            if len(matches)==1:owned=matches[0]['window_id'];break
            time.sleep(.1)
        assert owned is not None
        results={'date':'2026-10-05','baseline_version':'1.8.0','optimized_version':'1.9.0',
            'baseline':asyncio.run(measure(args.baseline_root.resolve(),owned,base,args.samples,False)),
            'optimized':asyncio.run(measure(args.root.resolve(),owned,base,args.samples,True)),
            'scope':'Installed MCP launchers, owned Safari asynchronous form. Baseline batches each available group into one IIFE and polls expected readiness/result. Discovery/reset excluded; model/network time not measured. Unicode input events, exact single click counts and partial/ambiguity/read-only/URL/frame guards verified.'}
        if args.output:args.output.write_text(json.dumps(results,indent=2)+'\n')
        print(json.dumps(results,indent=2),flush=True)
    finally:
        if owned is not None:
            subprocess.run(['/usr/bin/osascript','-l','JavaScript','-e',
                'function run(a){Application("Safari").windows().forEach(function(w){if(String(w.id())===a[0] && w.tabs().every(function(t){return t.url().indexOf(a[1])===0;}))w.close();});}',
                str(owned),base+'/'],capture_output=True,text=True,check=True,timeout=10)
        server.shutdown();server.server_close()
        if previous:
            if prior is not None and get_attr(prior,'AXRole')=='AXWindow':AX.AXUIElementPerformAction(prior,'AXRaise')
            ui.activate(previous['pid'],lambda:None)

if __name__=='__main__':main()
