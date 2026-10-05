"""Measure MCP latency on owned localhost Safari fixtures; no external content.

Run with --root pointing at a baseline or installed plugin directory. The
fixture is closed and the previous foreground app/window is restored.
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

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from native_ui import NativeUI, front_app, get_attr
import ApplicationServices as AX
from safari_browser import SafariBrowser


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'''<!doctype html><meta charset="utf-8"><title>Monterey speed fixture</title>
        <h1>Owned speed fixture</h1><input id="field" aria-label="Speed text" autocomplete="off">
        <p id="result">Ready</p><script>
        document.querySelector('#field').oninput=e=>document.querySelector('#result').textContent=e.target.value;
        </script>'''
        self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self,*_):
        pass


def decode(response):
    if response.isError:
        raise AssertionError(str(response.content))
    return json.loads(next(item.text for item in response.content if item.type=='text'))


async def measure(root, window_id, url, samples):
    params=StdioServerParameters(command=str(root/'scripts/start.sh'),args=[],cwd=str(root))
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as client:
            initialized=await client.initialize()
            listed=await client.list_tools()
            metrics={'samples':samples,'tool_schema_chars':len(json.dumps(listed.model_dump(),ensure_ascii=False)),
                     'shared_instruction_chars':len(initialized.instructions or '')}
            windows=decode(await client.call_tool('desktop_browser',{'operation':'tabs'}))['result']['windows']
            window=next(w for w in windows if w['window_id']==window_id)
            tab=next(t for t in window['tabs'] if t['url']==url)
            target={'window_id':window_id,'tab_index':tab['tab_index'],'expected_url':url}
            for operation in ('read','evaluate'):
                timings=[]
                for _ in range(samples+1):
                    start=time.perf_counter()
                    response=decode(await client.call_tool('desktop_browser',{
                        'operation':operation,**target,**({'script':'document.title'} if operation=='evaluate' else {})}))
                    timings.append((time.perf_counter()-start)*1000)
                    assert not response['truncated']
                metrics[f'browser_{operation}_ms']=round(statistics.median(timings[1:]),1)
                metrics[f'browser_{operation}_samples_ms']=[round(t,1) for t in timings[1:]]
            observe=[]
            metadata=None
            for _ in range(samples+1):
                start=time.perf_counter()
                metadata=decode(await client.call_tool('desktop_observe',{'window_id':window['cg_window_id'],
                    'include_image':False,'ui_query':'Speed text'}))
                observe.append((time.perf_counter()-start)*1000)
            metrics['native_observe_ms']=round(statistics.median(observe[1:]),1)
            metrics['native_observe_response_chars']=len(json.dumps(metadata,ensure_ascii=False))
            text=('café ✓ 😀 '+ 'x'*23)*25  # 800 characters, Unicode crosses chunks.
            timings=[]
            for _ in range(3):
                field=next(n for n in metadata['ui']['elements'] if n['role']=='AXTextField')
                start=time.perf_counter()
                metadata=decode(await client.call_tool('desktop_act',{'frame_id':metadata['frame_id'],
                    'actions':[{'kind':'replace_text','element_id':field['id'],'text':text}],
                    'include_image':False,'ui_query':'Speed text'}))
                timings.append((time.perf_counter()-start)*1000)
                observed=next(n for n in metadata['ui']['elements'] if n['role']=='AXTextField')
                assert observed['value']==text[:500]
                exact=decode(await client.call_tool('desktop_browser',{'operation':'evaluate',**target,
                    'script':'({value:document.querySelector("#field").value,input:document.querySelector("#result").textContent})'}))['result']
                assert exact=={'value':text,'input':text}, 'Input events or full Unicode value were lost'
                metadata=decode(await client.call_tool('desktop_observe',{'window_id':window['cg_window_id'],
                    'include_image':False,'ui_query':'Speed text'}))
            metrics['replace_text_chars']=len(text)
            metrics['replace_text_ms']=round(statistics.median(timings),1)
            metrics['replace_text_samples_ms']=[round(t,1) for t in timings]
            return metrics


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--samples',type=int,default=5)
    args=parser.parse_args()
    assert args.samples>=1
    previous=front_app()
    ui=NativeUI()
    previous_window=ui.window(previous['pid']) if previous else None
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url=f'http://127.0.0.1:{server.server_address[1]}/'
    browser=SafariBrowser()
    owned=None
    try:
        subprocess.run(['/usr/bin/osascript','-l','JavaScript','-e',
            'function run(a){Application("Safari").Document({url:a[0]}).make();}',url],
            capture_output=True,text=True,check=True,timeout=10)
        for _ in range(30):
            matches=[w for w in browser.tabs() if any(t['url']==url for t in w['tabs'])]
            if len(matches)==1:
                owned=matches[0]['window_id']
                break
            time.sleep(.1)
        assert owned is not None
        # Input is confined to the owned fixture's observed field.
        metadata=asyncio.run(measure(args.root.resolve(),owned,url,args.samples))
        metadata['scope']='Actual local MCP round trips on this Monterey Mac; excludes model and network latency. Text values and input callbacks verified in owned Safari fixture.'
        if args.output:
            args.output.write_text(json.dumps(metadata,indent=2)+'\n')
        print(json.dumps(metadata,indent=2),flush=True)
    finally:
        if owned is not None:
            subprocess.run(['/usr/bin/osascript','-l','JavaScript','-e',
                '''function run(a){Application("Safari").windows().forEach(function(w){
                if(String(w.id())===a[0] && w.tabs().every(function(t){return t.url()===a[1];}))w.close();});}''',
                str(owned),url],capture_output=True,text=True,check=True,timeout=10)
        server.shutdown()
        server.server_close()
        if previous:
            if previous_window is not None and get_attr(previous_window,'AXRole')=='AXWindow':
                AX.AXUIElementPerformAction(previous_window,'AXRaise')
            ui.activate(previous['pid'],lambda:None)


if __name__=='__main__':
    main()
