"""Measure actual AppKit-only workflows through installed MCP launchers."""
import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import ApplicationServices as AX
import Quartz as Q
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from native_ui import NativeUI,front_app,get_attr,window_id_of
from desktop import Desktop

def decode(response):
    if response.isError:raise AssertionError(str(response.content))
    return json.loads(next(c.text for c in response.content if c.type=='text'))
def find(metadata,identifier):
    return next(n for n in metadata['ui']['elements'] if n.get('identifier')==identifier)

async def measure(root,pid,window_id,state_path,samples,variant,previous):
    params=StdioServerParameters(command=str(root/'scripts/start.sh'),args=[],cwd=str(root))
    runs=[];expected='Done: First café ✓ / Second 😀 ✓'
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as client:
            await client.initialize()
            async def observe():
                return decode(await client.call_tool('desktop_observe',{'window_id':window_id,'include_image':False,'max_elements':1000,'ui_timeout':2,'detail':'full'}))
            for index in range(samples):
                frame=await observe()
                if variant=='press_probe':
                    before=json.loads(state_path.read_text())
                    started=time.perf_counter()
                    response=await client.call_tool('desktop_act',{'frame_id':frame['frame_id'],'actions':[
                        {'kind':'press','element_id':find(frame,'reset')['id']}],'include_image':False})
                    elapsed=round((time.perf_counter()-started)*1000,1)
                    await asyncio.sleep(.25)
                    after=json.loads(state_path.read_text())
                    return {'error':bool(response.isError),'response':str(response.content) if response.isError else 'passed',
                            'elapsed_ms':elapsed,'reset_event_observed':after['events'].count('reset')==before['events'].count('reset')+1}
                if variant=='wait_read':
                    start=time.perf_counter()
                    response=await client.call_tool('desktop_plan',{'frame_id':frame['frame_id'],'steps':[
                        {'kind':'wait_for','identifier':'first','role':'AXTextField'}],'feedback':'text'})
                    metadata=decode(response)
                    assert 'Background context 1' in metadata['reading']['text']
                    runs.append({'elapsed_ms':round((time.perf_counter()-start)*1000,1),'tool_calls':1,
                                 'response_chars':sum(len(c.text) for c in response.content if c.type=='text')})
                    continue
                frame=decode(await client.call_tool('desktop_act',{'frame_id':frame['frame_id'],'actions':[
                    {'kind':'press','element_id':find(frame,'reset')['id']}],'include_image':False,'detail':'full'}))
                frame=decode(await client.call_tool('desktop_act',{'frame_id':frame['frame_id'],'actions':[
                    {'kind':'focus','element_id':find(frame,'first')['id']}],'include_image':False,'detail':'full'}))
                background=variant=='summary_background'
                if background:
                    assert previous and previous['pid']!=pid
                    NativeUI().activate(previous['pid'],lambda:None)
                    frame=await observe()
                before=json.loads(state_path.read_text())
                pointer=Q.CGEventGetLocation(Q.CGEventCreate(None))
                call_count=0;chars=0
                async def call(tool,args):
                    nonlocal call_count,chars
                    response=await client.call_tool(tool,args);call_count+=1
                    chars+=sum(len(c.text) for c in response.content if c.type=='text')
                    return decode(response)
                steps=[{'kind':'replace_text','identifier':'first','role':'AXTextField','text':'First café ✓'},
                       {'kind':'press','identifier':'show','role':'AXButton'},
                       {'kind':'replace_text','identifier':'second','role':'AXTextField','text':'Second 😀 ✓'},
                       {'kind':'press','identifier':'save','role':'AXButton'},
                       {'kind':'wait_for','identifier':'result','role':'AXStaticText','value_contains':expected,'enabled':None}]
                start=time.perf_counter()
                if variant=='batches':
                    metadata=await call('desktop_act',{'frame_id':frame['frame_id'],'actions':[
                        {'kind':'replace_text','element_id':find(frame,'first')['id'],'text':'First café ✓'},
                        {'kind':'press','element_id':find(frame,'show')['id']},
                        {'kind':'wait_for','name':'Detail text','role':'AXTextField','timeout':3}],'include_image':False})
                    metadata=await call('desktop_act',{'frame_id':metadata['frame_id'],'actions':[
                        {'kind':'replace_text','element_id':find(metadata,'second')['id'],'text':'Second 😀 ✓'},
                        {'kind':'press','element_id':find(metadata,'save')['id']},
                        {'kind':'wait_for','role':'AXStaticText','value_contains':expected,'enabled':None}],'feedback':'text','include_image':False})
                else:
                    args={'frame_id':frame['frame_id'],'steps':steps,'feedback':'summary' if variant.startswith('summary') else 'text',
                          'mode':'background' if background else 'foreground','timeout':20}
                    if variant.startswith('summary'):args['read_selector']={'identifier':'result','role':'AXStaticText'}
                    metadata=await call('desktop_plan',args)
                elapsed=round((time.perf_counter()-start)*1000,1)
                assert expected in metadata['reading']['text']
                assert not metadata['reading']['truncated']
                assert 'native-secret-not-returned' not in json.dumps(metadata)
                after=json.loads(state_path.read_text())
                assert after['first']=='First café ✓' and after['second']=='Second 😀 ✓'
                assert after['result']==expected and after['secondary']=='Unchanged secondary window'
                assert after['events'].count('show')==before['events'].count('show')+1
                assert after['events'].count('save')==before['events'].count('save')+1
                assert after['events'].count('input')>=before['events'].count('input')+2
                if variant.startswith('summary'):
                    assert metadata['frame_expired'] and 'frame_id' not in metadata
                    stale=await client.call_tool('desktop_act',{'frame_id':frame['frame_id'],'actions':[{'kind':'wait','seconds':0}]})
                    assert stale.isError
                if background:
                    assert front_app()['pid']==previous['pid']
                    assert Q.CGEventGetLocation(Q.CGEventCreate(None))==pointer
                runs.append({'elapsed_ms':elapsed,'tool_calls':call_count,'response_chars':chars})
    return {'runs':runs,'median_ms':round(statistics.median(r['elapsed_ms'] for r in runs),1),
            'median_tool_calls':statistics.median(r['tool_calls'] for r in runs),
            'median_response_chars':statistics.median(r['response_chars'] for r in runs)}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--baseline-root',type=Path,required=True)
    parser.add_argument('--samples',type=int,default=3)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();assert args.samples>=1
    ui=NativeUI();previous=front_app();prior_window=ui.window(previous['pid']) if previous else None
    fixture=None
    with tempfile.TemporaryDirectory(prefix='monterey-native-workflow-') as directory:
        state=Path(directory)/'state.json'
        fixture=subprocess.Popen([sys.executable,str(Path(__file__).parent/'native_workflow_fixture.py'),str(state)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            for _ in range(100):
                if state.exists():break
                if fixture.poll() is not None:raise RuntimeError(fixture.stderr.read().decode())
                time.sleep(.1)
            assert state.exists()
            windows=[w for w in Desktop().windows() if w['pid']==fixture.pid and w['title']=='Monterey native workflow benchmark']
            assert len(windows)==1
            window_id=windows[0]['window_id'];measurements={}
            for root,variant in ((args.baseline_root,'press_probe'),(args.root,'press_probe'),
                                 (args.baseline_root,'wait_read'),(args.root,'wait_read'),
                                 (args.root,'batches'),(args.root,'plan'),(args.root,'summary'),(args.root,'summary_background')):
                ui.activate(fixture.pid,lambda:None)
                key=('baseline_' if root==args.baseline_root else 'optimized_')+variant
                measurements[key]=asyncio.run(measure(root.resolve(),fixture.pid,window_id,state,args.samples,variant,previous))
                print('PASS '+key,flush=True)
            result={'date':'2026-10-05','baseline_version':'1.8.0','optimized_version':'1.9.0','measurements':measurements,
                    'scope':'Actual AppKit-only app, no web view. Installed MCP launchers. Unmodified 1.8/1.9 button reliability and readiness/read comparison. Full form compares regular 1.9 batches versus 1.9 plans/summary because 1.8 may reject ordinary AppKit presses. Unicode input callbacks, delayed UI, separate-window isolation, secure omission, summary frame invalidation and background foreground/pointer preservation verified. Setup/reset/focus excluded consistently; model/network time not measured.'}
            if args.output:args.output.write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps(result,indent=2),flush=True)
        finally:
            fixture.terminate();fixture.wait(timeout=5)
            errors=fixture.stderr.read().decode()
            if 'Traceback' in errors:print(errors,flush=True)
            if previous:
                if prior_window is not None and get_attr(prior_window,'AXRole')=='AXWindow':AX.AXUIElementPerformAction(prior_window,'AXRaise')
                ui.activate(previous['pid'],lambda:None)

if __name__=='__main__':main()
