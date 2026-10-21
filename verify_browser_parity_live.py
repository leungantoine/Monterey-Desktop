"""Verify all seven browser MCP operations on owned localhost windows only.

Run with --browser chrome or safari and optional --root <plugin directory>.
Closes only owned windows and restores the original foreground app.
"""
import argparse
import asyncio
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = '''<!doctype html><meta charset="utf-8"><title>Browser parity fixture</title>
        <h1>Browser parity fixture</h1><a href="/two">Next page</a>
        <div id="controls"><button id="open" onclick="openCount++;document.documentElement.dataset.openCount=openCount;this.disabled=true;
        setTimeout(()=>document.querySelector('#controls').insertAdjacentHTML('beforeend',
        '<input id=field><button id=save onclick=save()>Save</button>'),80)">Open</button></div>
        <p id="result">Waiting</p><button class="duplicate">A</button><button class="duplicate">B</button>
        <script>
        var openCount=0,saveCount=0,inputCount=0;
        document.addEventListener('input',()=>{inputCount++;document.documentElement.dataset.inputCount=inputCount;});
        function save(){saveCount++;document.documentElement.dataset.saveCount=saveCount;let v=document.querySelector('#field').value;
        setTimeout(()=>document.querySelector('#result').textContent='Saved '+v,80);}
        </script>'''.encode()
        self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def jxa(source, *args):
    result = subprocess.run(['/usr/bin/osascript','-l','JavaScript','-e',source,*args],
                            check=True,capture_output=True,text=True,timeout=15)
    return result.stdout.strip()


def decode(result):
    if result.isError:
        raise AssertionError(str(result.content))
    return json.loads(next(c.text for c in result.content if c.type=='text'))


async def verify(root, browser, window_id, base):
    params = StdioServerParameters(command=str(root/'scripts/start.sh'),args=[],cwd=str(root))
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as client:
            await client.initialize()
            tools = await client.list_tools()
            schema = next(t.inputSchema for t in tools.tools if t.name=='desktop_browser')
            assert schema['properties']['browser']['enum']==['safari','chrome']
            async def call(operation, **kwargs):
                return await client.call_tool('desktop_browser',dict(browser=browser,
                    operation=operation,window_id=window_id,tab_index=1,**kwargs))
            async def result(operation, **kwargs):
                return decode(await call(operation,**kwargs))['result']
            listing=await result('tabs')
            window=next(w for w in listing['windows'] if w['window_id']==window_id)
            assert window['tabs'][0]['url']==base+'/one', listing
            text=await result('read',expected_url=base+'/one')
            assert 'Browser parity fixture' in text['text']
            links=await result('links',expected_url=base+'/one')
            assert any(l['url']==base+'/two' for l in links['links'])
            value=await result('evaluate',expected_url=base+'/one',script='({unicode:"é 📬",nested:[42,true,null]})')
            assert value=={'unicode':'é 📬','nested':[42,True,None]}
            context=await result('evaluate',expected_url=base+'/one',script='typeof openCount')
            assert context==('undefined' if browser=='chrome' else 'number'),context
            print('PASS tab discovery, scoped window mapping, DOM text, links, JSON and Unicode',flush=True)

            await result('navigate',expected_url=base+'/one',url=base+'/two')
            text=await result('navigate_read',expected_url=base+'/two',url=base+'/workflow',
                              ready_selector='#open',read_selector='h1',text_contains='Browser parity',timeout=5)
            assert text['text']=='Browser parity fixture'
            value='Monterey ✓ é 📬'
            text=await result('act_read',expected_url=base+'/workflow',steps=[
                {'kind':'click','selector':'#open'},
                {'kind':'replace_text','selector':'#field','text':value},
                {'kind':'click','selector':'#save'},
                {'kind':'wait_for','selector':'#result','text_contains':'Saved'}],
                text_contains='Saved '+value,read_selector='#result',timeout=5)
            assert text['text']=='Saved '+value and text['workflow']['completed_steps']==4
            counts=await result('evaluate',expected_url=base+'/workflow',script='Object.fromEntries(["openCount","saveCount","inputCount"].map(k=>[k,Number(document.documentElement.dataset[k])]))')
            assert counts=={'openCount':1,'saveCount':1,'inputCount':1},counts
            print('PASS exact navigation, navigate/wait/read and delayed action/input/wait/read without replay',flush=True)

            frame=decode(await client.call_tool('desktop_observe',
                         {'window_id':window['cg_window_id'],'include_image':False}))
            bad=await call('evaluate',expected_url=base+'/workflow',script='(() => {window.partial=1;throw Error("partial failure");})()')
            assert bad.isError and 'partial failure' in str(bad.content)
            stale=await client.call_tool('desktop_act',{'frame_id':frame['frame_id'],
                          'actions':[{'kind':'wait','seconds':0}],'include_image':False})
            assert stale.isError
            assert await result('evaluate',expected_url=base+'/workflow',script='window.partial')==1
            bad=await call('evaluate',expected_url=base+'/wrong',script='document.body.remove()')
            assert bad.isError and 'URL changed' in str(bad.content)
            bad=await call('act_read',expected_url=base+'/workflow',
                           steps=[{'kind':'click','selector':'.duplicate'}],text_contains='Saved')
            assert bad.isError and 'Ambiguous' in str(bad.content)
            bad=await call('act_read',expected_url=base+'/workflow',steps=[
                {'kind':'click','selector':'#save'}, {'kind':'click','selector':'.duplicate'}],text_contains='Saved')
            assert bad.isError and 'after 1 completed steps' in str(bad.content)
            assert await result('evaluate',expected_url=base+'/workflow',script='Number(document.documentElement.dataset.saveCount)')==2
            clipped=decode(await call('evaluate',expected_url=base+'/workflow',script='"x".repeat(5000)',max_output_chars=100))
            assert clipped['truncated'] and len(clipped['preview'])==100
            print('PASS stale frames, changed URLs, ambiguity, partial execution and bounded output',flush=True)

            app='Google Chrome' if browser=='chrome' else 'Safari'
            jxa('''function run(a){var s=Application(a[0]);var w=s.windows().filter(w=>String(w.id())===a[1])[0];
                w.tabs.push(s.Tab({url:a[2]}));}''',app,str(window_id),base+'/workflow')
            # Keep the cached target: the live Apple Event must detect the new duplicate.
            for operation in ('read','links','evaluate','navigate','navigate_read','act_read'):
                kwargs={'expected_url':base+'/workflow'}
                if operation=='evaluate':kwargs['script']='document.body.remove()'
                if operation in ('navigate','navigate_read'):kwargs['url']=base+'/other'
                if operation in ('navigate_read','act_read'):kwargs['text_contains']='Saved'
                if operation=='act_read':kwargs['steps']=[{'kind':'click','selector':'#save'}]
                bad=await call(operation,**kwargs)
                assert bad.isError and 'URL is ambiguous' in str(bad.content),(operation,bad)
            print('PASS live duplicate-URL refusal across all six targeted operations',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser',choices=['safari','chrome'],default='chrome')
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    args=parser.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_address[1]}'
    app='Google Chrome' if args.browser=='chrome' else 'Safari'
    previous=jxa('ObjC.import("AppKit");$.NSWorkspace.sharedWorkspace.frontmostApplication.bundleIdentifier.js')
    window=None
    try:
        window=int(jxa('''function run(a){var s=Application(a[0]);var w;
            if(a[0]==="Safari"){s.Document({url:a[1]}).make();w=s.windows()[0];}
            else{w=s.Window().make();w.tabs()[0].url=a[1];}return Number(w.id());}''',app,base+'/one'))
        asyncio.run(verify(args.root.resolve(),args.browser,window,base))
    finally:
        if window is not None:
            jxa('''function run(a){var s=Application(a[0]);s.windows().forEach(w=>{
                if(String(w.id())===a[1] && w.tabs().every(t=>String(t.url()).indexOf(a[2])===0))w.close();});}''',
                app,str(window),base+'/')
        if previous:
            jxa('function run(a){Application(a[0]).activate();}',previous)
        server.shutdown()
        server.server_close()


if __name__=='__main__':
    main()
