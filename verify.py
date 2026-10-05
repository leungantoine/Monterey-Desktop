"""End-to-end verification against a local Safari fixture. No external website."""
import asyncio
import base64
import io
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from desktop import PAUSED
from PIL import Image
from mcp import ClientSession as MCPClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent


class ClientSession(MCPClientSession):
    """Exercise the complete legacy response contract in the regression suite."""
    async def call_tool(self, name, arguments=None, **kwargs):
        if name in ('desktop_observe', 'desktop_act'):
            arguments = {'detail':'full', **(arguments or {})}
            if name=='desktop_observe':
                arguments={'include_image':True, **arguments}
        return await super().call_tool(name, arguments, **kwargs)


EVENTS = []
HTML = b'''<!doctype html><meta charset="utf-8"><title>Monterey Desktop verification</title>
<style>
body{margin:0;background:#f4f7fa;font:18px system-ui;height:2400px}
h1{position:absolute;left:40px;top:15px;font-size:28px}
button{position:absolute;left:40px;top:90px;width:220px;height:55px;background:#ff2147;color:white;border:0;border-radius:8px;font:18px system-ui}
input{position:absolute;left:40px;top:170px;width:420px;height:50px;box-sizing:border-box;background:#00d4cc;border:0;padding:10px;font:18px system-ui}
#drag{position:absolute;left:40px;top:270px;width:100px;height:80px;background:#7c32ee;border-radius:8px;color:white;display:grid;place-items:center;user-select:none}
#target{position:absolute;left:350px;top:270px;width:120px;height:80px;background:#ffcd00;border-radius:8px;display:grid;place-items:center}
#result{position:absolute;left:40px;top:390px;white-space:pre-wrap;color:#174a33}
</style><h1>Local desktop companion test</h1><button id="click">Test click</button>
<input id="text" aria-label="Verification text" placeholder="Type here" autocomplete="off"><div id="drag">Drag me</div><div id="target">Drop here</div><div id="result">Waiting for test...</div>
<button id="ready" disabled style="position:absolute;left:520px;top:90px;background:#2244ff">Delayed test</button>
<input id="secret" type="password" aria-label="Protected test" value="never-return-this" style="position:absolute;left:520px;top:170px;width:220px;height:50px;background:#cccccc">
<button style="position:absolute;left:520px;top:270px;background:#888888">Duplicate test</button>
<button style="position:absolute;left:520px;top:340px;background:#888888">Duplicate test</button>
<script>
const report=(kind,value)=>{document.querySelector('#result').textContent+=`\\n${kind}: ${value}`;fetch('/event',{method:'POST',body:JSON.stringify({kind,value})})};
document.querySelector('#click').onclick=()=>{report('click','ok');document.querySelector('#ready').disabled=true;setTimeout(()=>{document.querySelector('#ready').disabled=false;report('ready','ok')},700)};
document.querySelector('#text').oninput=e=>report('input',e.target.value);
let start=null;document.querySelector('#drag').onmousedown=e=>{start={x:e.clientX,y:e.clientY}};
document.onmouseup=e=>{if(start){if(Math.hypot(e.clientX-start.x,e.clientY-start.y)>100)report('drag','ok');start=null}};
window.onscroll=()=>report('scroll',window.scrollY);
report('loaded','ok');
</script>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        html = HTML
        if self.path.startswith('/secondary'):
            html = html.replace(b'Monterey Desktop verification</title>', b'Monterey Desktop verification secondary</title>').replace(b'Verification text', b'Secondary verification text')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        if length > 16384:
            self.send_error(413)
            return
        EVENTS.append(json.loads(self.rfile.read(length)))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_):
        pass


async def await_event(kind, value=None):
    for _ in range(60):
        if any(e['kind'] == kind and (value is None or e['value'] == value) for e in EVENTS):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f'No browser event: {kind}, {value!r}. Received: {EVENTS!r}')


def decode(result):
    if result.isError:
        raise AssertionError(str(result.content))
    text = next(item.text for item in result.content if item.type == 'text')
    metadata = json.loads(text)
    images = [item for item in result.content if item.type == 'image']
    image = Image.open(io.BytesIO(base64.b64decode(images[0].data))).convert('RGB') if images else None
    return metadata, image


def find_color(image, color):
    # Find a large solid control in the returned JPEG rather than hard-code
    # Safari toolbar height or Retina geometry.
    points = []
    for y in range(60, min(image.height, 650), 3):
        for x in range(10, min(image.width, 850), 3):
            pixel = image.getpixel((x, y))
            if sum(abs(a - b) for a, b in zip(pixel, color)) < 35:
                points.append((x, y))
    if len(points) < 150:
        raise AssertionError(f'Control not found: {color}')
    xs, ys = zip(*points)
    return {'x': (min(xs) + max(xs)) / 2, 'y': (min(ys) + max(ys)) / 2}


async def verify(server):
    parameters = StdioServerParameters(command=sys.executable, args=[str(ROOT / 'desktop.py'), 'serve'], env={'PYTHONDONTWRITEBYTECODE':'1'})
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            names = {t.name for t in (await client.list_tools()).tools}
            assert names == {'desktop_status', 'desktop_observe', 'desktop_act', 'desktop_read', 'desktop_browser', 'desktop_plan', 'desktop_pause'}, names
            print('PASS MCP initialization and tool discovery', flush=True)
            status, _ = decode(await client.call_tool('desktop_status'))
            assert status['screen_recording'] and status['accessibility'] and not status['paused'], status
            metadata, image = decode(await client.call_tool('desktop_observe'))
            assert image is not None
            assert image.size == (metadata['image_width'], metadata['image_height'])
            print(f"PASS capture and scaling ({image.width} x {image.height}, {metadata['capture_ms']} ms)", flush=True)

            url = f'http://127.0.0.1:{server.server_address[1]}/'
            # Use the actual tool to open a URL in Safari.
            metadata, image = decode(await client.call_tool('desktop_act', {
                'frame_id': metadata['frame_id'],
                'actions': [{'kind': 'open_url', 'url': url}], 'settle_seconds': 1,
            }))
            await await_event('loaded', 'ok')
            # Ensure an ordinary Safari window with known size, without modifying
            # existing document contents or using WebDriver's input-blocking window.
            script = 'tell application "Safari"\nactivate\nset bounds of front window to {0, 25, 1000, 760}\nend tell'
            subprocess.run(['/usr/bin/osascript', '-e', script], check=True, capture_output=True, timeout=10)
            metadata, image = decode(await client.call_tool('desktop_observe'))
            button = find_color(image, (255, 33, 71))
            field = find_color(image, (0, 212, 204))
            drag = find_color(image, (124, 50, 238))
            target = find_color(image, (255, 205, 0))
            old_frame = metadata['frame_id']
            metadata, image = decode(await client.call_tool('desktop_act', {
                'frame_id': old_frame, 'actions': [
                    {'kind': 'click', **button}, {'kind': 'click', **field},
                    {'kind': 'type', 'text': 'Monterey ✓ 😀'},
                ],
            }))
            await await_event('click', 'ok')
            await await_event('input', 'Monterey ✓ 😀')
            assert metadata['completed_actions'] == 3
            print('PASS native clicking, Unicode typing, and action batch', flush=True)

            metadata, image = decode(await client.call_tool('desktop_act', {
                'frame_id': metadata['frame_id'], 'actions': [
                    {'kind': 'key', 'key': 'cmd+a'},
                    {'kind': 'type', 'text': 'Desktop helper works'},
                    {'kind': 'drag', **drag, 'to_x': target['x'], 'to_y': target['y']},
                ],
            }))
            await await_event('input', 'Desktop helper works')
            await await_event('drag', 'ok')
            print('PASS shortcut and dragging', flush=True)

            bad = await client.call_tool('desktop_act', {
                'frame_id': old_frame, 'actions': [{'kind': 'click', **button}],
            })
            assert bad.isError
            clicks = sum(e['kind'] == 'click' for e in EVENTS)
            bad = await client.call_tool('desktop_act', {
                'frame_id': metadata['frame_id'], 'actions': [
                    {'kind': 'click', **button}, {'kind': 'click', 'x': -1, 'y': 0},
                ],
            })
            assert bad.isError
            await asyncio.sleep(0.2)
            assert clicks == sum(e['kind'] == 'click' for e in EVENTS)
            print('PASS stale frame and invalid batch rejection before input', flush=True)

            metadata, image = decode(await client.call_tool('desktop_act', {
                'frame_id': metadata['frame_id'], 'actions': [{'kind': 'scroll', 'delta_y': 400}],
            }))
            await await_event('scroll')
            assert any(e['kind'] == 'scroll' and e['value'] > 0 for e in EVENTS)
            print('PASS native scrolling', flush=True)

            result = await client.call_tool('desktop_pause')
            paused, _ = decode(result)
            assert paused['paused']
            assert (await client.call_tool('desktop_observe')).isError
            assert (await client.call_tool('desktop_act', {
                'frame_id': metadata['frame_id'], 'actions': [{'kind': 'key', 'key': 'tab'}],
            })).isError
            print('PASS pause blocks observation and input', flush=True)
            PAUSED.unlink()
            metadata, _ = decode(await client.call_tool('desktop_observe'))
            assert not metadata['paused']
            print('PASS resume and final observation', flush=True)
            # Return to the visible results at the top of the local fixture.
            metadata, _ = decode(await client.call_tool('desktop_act', {
                'frame_id': metadata['frame_id'], 'actions': [{'kind': 'scroll', 'delta_y': -5000}],
            }))
            assert metadata['completed_actions'] == 1
            await verify_native(client, url)


def named(metadata, name, role=None):
    matches = [n for n in metadata['ui']['elements']
               if name in (n.get('name'),n.get('description'),n.get('help'))
               and (role is None or role == n['role'])]
    assert len(matches) == 1, (name, [(n['id'],n['role']) for n in matches], metadata['ui']['truncated'])
    return matches[0]


async def verify_native(client, url):
    metadata, _ = decode(await client.call_tool('desktop_observe'))
    safari_pid = metadata['front_app']['pid']
    # Window capture magnifies the target while retaining native coordinate mapping.
    metadata, image = decode(await client.call_tool('desktop_observe', {
        'app_pid': safari_pid, 'max_width': 2000,
    }))
    assert image.width == 2000 and metadata['image_bounds']['width'] == 1000
    button, field = named(metadata,'Test click','AXButton'), named(metadata,'Verification text','AXTextField')
    secret = named(metadata,'Protected test','AXTextField')
    assert 'AXPress' in button['actions'] and field['value_settable'] is True
    assert secret['value'] == '<redacted>' and 'never-return-this' not in json.dumps(metadata)
    # Coordinate bounds from AX and independently observed control color must agree.
    button_pixels = find_color(image,(255,33,71))
    bounds = button['screenshot_bounds']
    assert bounds['x'] <= button_pixels['x'] <= bounds['x']+bounds['width']
    assert bounds['y'] <= button_pixels['y'] <= bounds['y']+bounds['height']
    print('PASS named controls, secure-field redaction, detailed window capture, and coordinate mapping',flush=True)
    EVENTS.clear()
    started = time.monotonic()
    metadata, image = decode(await client.call_tool('desktop_act', {
        'frame_id': metadata['frame_id'], 'actions': [
            {'kind':'press','element_id':button['id']},
            {'kind':'wait_for','name':'Delayed test','role':'AXButton','enabled':True,'timeout':4},
        ], 'settle_seconds':0, 'include_image':False,
    }))
    assert image is None and time.monotonic()-started >= 0.5
    await await_event('click','ok')
    await await_event('ready','ok')
    print('PASS native button press and readiness polling across delayed enable',flush=True)
    field = named(metadata,'Verification text','AXTextField')
    metadata, _ = decode(await client.call_tool('desktop_act', {
        'frame_id':metadata['frame_id'], 'actions':[
            {'kind':'set_value','element_id':field['id'],'value':'Native field value ✓'},
            {'kind':'wait_for','name':'Verification text','value_contains':'Native field value ✓'},
        ], 'settle_seconds':0,
    }))
    await await_event('input','Native field value ✓')
    assert metadata['state_changes']['status'] == 'observed_change'
    assert any(c['kind'] == 'changed' and 'value' in c['fields'] and c['after'].get('value') == 'Native field value ✓'
               for c in metadata['state_changes']['changes']), metadata['state_changes']
    assert 'never-return-this' not in json.dumps(metadata['state_changes'])
    print('PASS native field value with independent browser input event',flush=True)
    metadata, _ = decode(await client.call_tool('desktop_act', {
        'frame_id':metadata['frame_id'], 'actions':[{'kind':'wait','seconds':0}], 'settle_seconds':0,
    }))
    assert metadata['state_changes']['status'] == 'no_change_observed', metadata['state_changes']
    # An unsupported native control anywhere in the batch must stop an earlier press.
    before_clicks = sum(e['kind'] == 'click' for e in EVENTS)
    bad = await client.call_tool('desktop_act', {
        'frame_id':metadata['frame_id'], 'actions':[
            {'kind':'press','element_id':named(metadata,'Test click','AXButton')['id']},
            {'kind':'press','element_id':named(metadata,'Verification text','AXTextField')['id']},
        ],
    })
    assert bad.isError and 'AXPress' in str(bad.content)
    await asyncio.sleep(0.15)
    assert before_clicks == sum(e['kind'] == 'click' for e in EVENTS)
    print('PASS supported-action discovery, bounded state evidence, inconclusive no-change, and unsupported batch rejection', flush=True)
    bad = await client.call_tool('desktop_act',{
        'frame_id':metadata['frame_id'],'actions':[{'kind':'wait_for','name':'Duplicate test','timeout':1}],
    })
    assert bad.isError and 'ambiguous' in str(bad.content)
    metadata, _ = decode(await client.call_tool('desktop_observe',{'app_pid':safari_pid,'include_image':False}))
    bad = await client.call_tool('desktop_act',{
        'frame_id':metadata['frame_id'],'actions':[{'kind':'wait_for','name':'Never exists','timeout':0.2}],
    })
    assert bad.isError and 'timeout' in str(bad.content)
    print('PASS ambiguous and unavailable readiness selectors fail clearly',flush=True)
    metadata, _ = decode(await client.call_tool('desktop_observe',{'app_pid':safari_pid,'include_image':False}))
    finder_pid = next(a['pid'] for a in metadata['apps'] if a['bundle_id']=='com.apple.finder')
    metadata, _ = decode(await client.call_tool('desktop_act',{
        'frame_id':metadata['frame_id'],'actions':[{'kind':'activate','pid':finder_pid}], 'settle_seconds':0,
    }))
    assert metadata['front_app']['pid'] == finder_pid
    bad = await client.call_tool('desktop_act',{
        'frame_id':metadata['frame_id'],'actions':[{'kind':'type','text':'WRONG APP INPUT'}],
    })
    assert bad.isError and 'Foreground app changed' in str(bad.content)
    metadata, _ = decode(await client.call_tool('desktop_observe',{'app_pid':safari_pid,'include_image':False}))
    EVENTS.clear()
    button = named(metadata,'Test click','AXButton')
    metadata, _ = decode(await client.call_tool('desktop_act',{
        'frame_id':metadata['frame_id'],'actions':[{'kind':'press','element_id':button['id']}], 'settle_seconds':0,
    }))
    await await_event('click','ok')
    assert metadata['front_app']['pid'] == finder_pid
    print('PASS foreground input guard and native Safari press while Finder remains foreground',flush=True)
    field=named(metadata,'Verification text','AXTextField')
    metadata, _ = decode(await client.call_tool('desktop_act',{
        'frame_id':metadata['frame_id'],'actions':[
            {'kind':'focus','element_id':field['id']}, {'kind':'key','key':'cmd+a'},
            {'kind':'type','text':'Focused native control ✓ 😀'},
        ], 'settle_seconds':0,
    }))
    await await_event('input','Focused native control ✓ 😀')
    assert metadata['front_app']['pid'] == safari_pid
    print('PASS native control focus, app activation, and targeted Unicode typing',flush=True)
    metadata, _ = decode(await client.call_tool('desktop_observe', {'app_pid':safari_pid,'max_width':2000}))
    print('Native capture timings:',metadata['capture_ms'],'ms image;',metadata['accessibility_ms'],'ms AX;',metadata['observe_ms'],'ms total',flush=True)
    await verify_exact_window(client, safari_pid, metadata['window_id'], url)


async def verify_exact_window(client, safari_pid, first_id, url):
    script = f'tell application "Safari"\nmake new document with properties {{URL:"{url}secondary"}}\nactivate\nset bounds of front window to {{300, 100, 1300, 850}}\nend tell'
    subprocess.run(['/usr/bin/osascript', '-e', script], check=True, capture_output=True, timeout=10)
    await asyncio.sleep(0.6)
    metadata, _ = decode(await client.call_tool('desktop_observe', {'app_pid':safari_pid, 'include_image':False}))
    second_id = metadata['window_id']
    assert second_id != first_id
    named(metadata, 'Secondary verification text', 'AXTextField')
    metadata, _ = decode(await client.call_tool('desktop_observe', {'window_id':first_id,'include_image':False}))
    assert metadata['window_id'] == first_id and metadata['target_app_pid'] == safari_pid
    named(metadata, 'Verification text', 'AXTextField')
    assert not any(n.get('description') == 'Secondary verification text' for n in metadata['ui']['elements'])
    # PID is unchanged, but input must not leak to the other focused Safari window.
    bad = await client.call_tool('desktop_act', {
        'frame_id':metadata['frame_id'], 'actions':[{'kind':'type','text':'WRONG WINDOW INPUT'}],
    })
    assert bad.isError and 'focused window' in str(bad.content), bad.content
    metadata, _ = decode(await client.call_tool('desktop_observe', {'window_id':second_id,'include_image':False}))
    assert named(metadata,'Secondary verification text','AXTextField').get('value','') == ''
    finder_pid = next(a['pid'] for a in metadata['apps'] if a['bundle_id']=='com.apple.finder')
    bad = await client.call_tool('desktop_observe', {'window_id':first_id,'app_pid':finder_pid,'include_image':False})
    assert bad.isError and 'does not belong' in str(bad.content)
    metadata, _ = decode(await client.call_tool('desktop_observe', {'window_id':first_id,'include_image':False}))
    field = named(metadata,'Verification text','AXTextField')
    EVENTS.clear()
    metadata, _ = decode(await client.call_tool('desktop_act', {
        'frame_id':metadata['frame_id'], 'actions':[
            {'kind':'focus','element_id':field['id']}, {'kind':'key','key':'cmd+a'},
            {'kind':'type','text':'Exact window target ✓'},
        ], 'settle_seconds':0,
    }))
    await await_event('input','Exact window target ✓')
    assert metadata['window_id'] == first_id
    metadata, _ = decode(await client.call_tool('desktop_observe', {'window_id':second_id,'include_image':False}))
    assert named(metadata,'Secondary verification text','AXTextField').get('value','') == ''
    print('PASS exact background window selection, owner mismatch rejection, same-app wrong-window input guard, and explicit target focus', flush=True)


def main():
    if PAUSED.exists():
        raise RuntimeError('Companion is paused. Resume it locally before running verification.')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        asyncio.run(verify(server))
    finally:
        PAUSED.unlink(missing_ok=True)
        server.shutdown()
        server.server_close()
    print('ALL CHECKS PASSED', flush=True)


if __name__ == '__main__':
    main()
