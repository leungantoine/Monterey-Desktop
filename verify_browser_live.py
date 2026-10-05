"""Live browser MCP checks using an owned localhost Safari window.

No mail content is read. The fixture window is closed in finally.
"""
import asyncio
import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from safari_browser import SafariBrowser


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        page = 'two' if self.path.startswith('/two') else 'one'
        long_value = 'a' * 2500 + 'LIVE_READY_AT_END'
        body = f'''<!doctype html><meta charset="utf-8">
        <title>Monterey Desktop browser verification</title>
        <h1>Fixture {page}</h1><p>Complete browser test text.</p>
        <a href="/two">Second fixture page</a>
        <input id="field" aria-label="Browser test field">
        <textarea aria-label="Long readiness field">{long_value}</textarea>
        <button onclick="document.getElementById('result').textContent='Clicked '+document.getElementById('field').value">Test DOM click</button>
        <p id="result">Waiting</p>'''.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def decode(result):
    if result.isError:
        raise AssertionError(str(result.content))
    return json.loads(next(c.text for c in result.content if c.type == 'text'))


async def verify(launcher, window_id, base):
    params = StdioServerParameters(command=str(launcher), args=[], cwd=str(launcher.parent.parent))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            target = {'window_id': window_id, 'tab_index': 1}
            async def browser(operation, **kwargs):
                return decode(await client.call_tool('desktop_browser', {'operation': operation, **target, **kwargs}))
            listing = decode(await client.call_tool('desktop_browser', {'operation': 'tabs'}))['result']
            window = next(w for w in listing['windows'] if w['window_id'] == window_id)
            assert window['tabs'][0]['url'] == base + '/one'
            text = (await browser('read'))['result']
            assert 'Fixture one' in text['text'] and not text['truncated']
            links = (await browser('links'))['result']
            assert any(l['url'] == base + '/two' for l in links['links'])
            print('PASS live tab discovery, DOM text, and link URLs', flush=True)
            frame = decode(await client.call_tool('desktop_observe', {'window_id': window['cg_window_id'], 'include_image': False}))
            ready = decode(await client.call_tool('desktop_act', {'frame_id':frame['frame_id'],
                'actions':[{'kind':'wait_for','role':'AXTextArea','value_contains':'LIVE_READY_AT_END','timeout':2}],
                'include_image':False}))
            assert ready['completed_actions'] == 1
            print('PASS live native readiness matches beyond character 2000', flush=True)
            frame = ready
            result = await browser('evaluate', expected_url=base + '/one', script='''(() => {
                document.querySelector('#field').value='Monterey ✓ 📬';
                document.querySelector('button').click();
                return {text:document.querySelector('#result').textContent, unicode:document.querySelector('#field').value};
            })()''')
            assert result['result'] == {'text':'Clicked Monterey ✓ 📬', 'unicode':'Monterey ✓ 📬'}
            stale = await client.call_tool('desktop_act', {'frame_id': frame['frame_id'], 'actions':[{'kind':'wait','seconds':0}]})
            assert stale.isError
            bad = await client.call_tool('desktop_browser', {'operation':'evaluate', **target, 'expected_url':base+'/wrong', 'script':'document.body.remove()'})
            assert bad.isError
            assert 'Clicked Monterey ✓ 📬' in (await browser('read'))['result']['text']
            bad = await client.call_tool('desktop_browser', {'operation':'evaluate', **target,
                'script':'(() => { document.querySelector("#result").textContent="Changed before failure"; throw new Error("live script failure"); })()'})
            assert bad.isError and 'live script failure' in str(bad.content)
            assert 'Changed before failure' in (await browser('read'))['result']['text']
            bad = await client.call_tool('desktop_browser', {'operation':'read', 'window_id':2147483647,'tab_index':1})
            assert bad.isError
            large = await browser('evaluate', script='"x".repeat(5000)', max_output_chars=100)
            assert large['truncated'] and len(large['preview']) == 100 and large['total_chars'] == 5002
            print('PASS live DOM input/clicks, Unicode, stale frame/target/URL refusal, partial script errors, and truncation', flush=True)
            await browser('navigate', expected_url=base+'/one', url=base+'/two')
            for _ in range(30):
                text = (await browser('read'))['result']
                if text['url']==base+'/two' and 'Fixture two' in text['text']:
                    break
                await asyncio.sleep(.1)
            else:
                raise AssertionError('Navigation did not expose the second fixture body')
            assert text['ready_state']=='complete'
            print('PASS exact-tab navigation and new-body readiness', flush=True)
            subprocess.run(['/usr/bin/osascript','-e',
                '''on run argv
                tell application "Safari"
                    set targetWindow to first window whose id is (item 1 of argv as integer)
                    make new tab at end of tabs of targetWindow with properties {URL:item 2 of argv}
                end tell
                end run''',str(window_id),base+'/two'],
                capture_output=True, text=True, check=True, timeout=10)
            for _ in range(30):
                listing = decode(await client.call_tool('desktop_browser', {'operation':'tabs'}))['result']
                owned = next(w for w in listing['windows'] if w['window_id']==window_id)
                if sum(t['url']==base+'/two' for t in owned['tabs'])==2:
                    break
                await asyncio.sleep(.1)
            else:
                raise AssertionError('Duplicate fixture tab did not become ready')
            for operation in ('read','links','evaluate','navigate'):
                bad = await client.call_tool('desktop_browser', {'operation':operation, **target,
                    'expected_url':base+'/two','script':'document.body.remove()','url':base+'/one'})
                assert bad.isError and 'URL is ambiguous' in str(bad.content)
            listing = decode(await client.call_tool('desktop_browser', {'operation':'tabs'}))['result']
            owned = next(w for w in listing['windows'] if w['window_id']==window_id)
            assert len(owned['tabs'])==2 and all(t['url']==base+'/two' for t in owned['tabs'])
            print('PASS live duplicate-URL refusal for reading, links, scripts and navigation', flush=True)


def main():
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    browser = SafariBrowser()
    window_id = None
    try:
        subprocess.run(['/usr/bin/osascript','-l','JavaScript','-e',
            'function run(a){var s=Application("Safari");s.Document({url:a[0]}).make();}',base+'/one'],
            capture_output=True, text=True, check=True, timeout=10)
        for _ in range(30):
            matches = [w for w in browser.tabs() if any(t['url']==base+'/one' for t in w['tabs'])]
            if len(matches)==1:
                window_id = matches[0]['window_id']
                break
            time.sleep(.1)
        assert window_id is not None, 'Owned fixture window not found'
        version = json.loads((Path(__file__).parent/'.codex-plugin/plugin.json').read_text())['version']
        launcher = Path.home()/f'.codex/plugins/cache/personal-local/monterey-desktop/{version}/scripts/start.sh'
        asyncio.run(verify(launcher, window_id, base))
    finally:
        if window_id is not None:
            subprocess.run(['/usr/bin/osascript','-l','JavaScript','-e',
                '''function run(a){var s=Application("Safari");s.windows().forEach(function(w){
                if(String(w.id())===a[0] && w.tabs().every(function(t){return t.url().indexOf(a[1])===0;})) w.close();});}''',
                str(window_id),base+'/'], capture_output=True, text=True, check=True, timeout=10)
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
