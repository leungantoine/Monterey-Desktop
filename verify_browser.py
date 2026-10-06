"""Offline tests for the Safari bridge and desktop_browser MCP contract.

Run with the companion Python environment. Apple Events are mocked; one test
executes the real JXA interpreter with a fake Application implementation. This
script never launches Safari or sends desktop events.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import unittest
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import safari_browser as bridge  # noqa: E402


class FakeRunner:
    def __init__(self, *responses: subprocess.CompletedProcess[str] | Exception):
        self.responses = list(responses)
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, kwargs))
        if not self.responses:
            raise AssertionError("unexpected osascript invocation")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class SafariBrowserTests(unittest.TestCase):
    @unittest.skipUnless(Path('/usr/bin/osascript').exists(), 'Requires macOS JXA')
    def test_duplicate_url_tabs_are_refused_before_script_or_navigation(self) -> None:
        mock = '''var tabA={url:function(){return "https://example.test/same";}};
        var tabB={url:function(){return "https://example.test/same";}};
        function Application(name) { return {
          windows:function(){return [{id:function(){return 91;},tabs:function(){return [tabB,tabA];}}];},
          doJavaScript:function(){throw new Error("UNEXPECTED PAGE EXECUTION");}
        }; }\n'''
        browser = bridge.SafariBrowser()
        for source in (bridge._EVALUATE_SCRIPT, bridge._NAVIGATE_SCRIPT):
            for expected_url in (None, 'https://example.test/same'):
                with self.subTest(source=source, expected_url=expected_url):
                    with self.assertRaisesRegex(bridge.SafariBridgeError, 'URL is ambiguous'):
                        browser._run(mock + source, '91', '1', 'https://example.test/new', json.dumps(expected_url))

    @unittest.skipUnless(Path('/usr/bin/osascript').exists(), 'Requires macOS JXA')
    def test_duplicate_urls_elsewhere_do_not_block_a_unique_target(self) -> None:
        mock = '''var target={tag:"unique",url:function(){return "https://example.test/unique";}};
        var duplicate={url:function(){return "https://example.test/same";}};
        function Application(name) { return {
          windows:function(){return [{id:function(){return 91;},tabs:function(){return [target,duplicate,duplicate];}}];},
          doJavaScript:function(source,target){return JSON.stringify({ok:true,value:target.in.tag});}
        }; }\n'''
        raw = bridge.SafariBrowser()._run(mock + bridge._EVALUATE_SCRIPT, '91', '1', '42',
                                        json.dumps('https://example.test/unique'))
        self.assertEqual(json.loads(raw), {'ok':True,'value':'unique'})

    @unittest.skipUnless(Path('/usr/bin/osascript').exists(), 'Requires macOS JXA')
    def test_real_jxa_expression_serialization_and_empty_url_guard(self) -> None:
        mock = '''var fakeTab={url:function(){return "https://example.test/current";}};
        function Application(name) { return {
          windows:function(){return [{id:function(){return 91;},tabs:function(){return [fakeTab];}}];},
          doJavaScript:function(source,target){return eval(source);}
        }; }\n'''
        browser = bridge.SafariBrowser()
        cases = [
            ('({text:"quotes \\\" é 📬", nested:[1,true,null]})', {'text':'quotes " é 📬','nested':[1,True,None]}),
            ('(() => { const value=6; return value*7; })();', 42),
            ('undefined', None), ('"scalar"', 'scalar'),
        ]
        for expression, expected in cases:
            raw = browser._run(mock + bridge._EVALUATE_SCRIPT, '91', '1', expression,
                               json.dumps('https://example.test/current'))
            self.assertEqual(json.loads(raw), {"ok":True,"value":expected})
        for expected_url in ('', 'https://example.test/changed'):
            with self.assertRaisesRegex(bridge.SafariBridgeError, 'URL changed'):
                browser._run(mock + bridge._EVALUATE_SCRIPT, '91', '1', '42', json.dumps(expected_url))
        self.assertEqual(json.loads(browser._run(mock + bridge._EVALUATE_SCRIPT, '91', '1', '42', 'null')), {'ok':True,'value':42})
        result = json.loads(browser._run(mock + bridge._EVALUATE_SCRIPT, '91', '1',
                            '(() => { throw new Error("visible failure"); })()', 'null'))
        self.assertFalse(result['ok'])
        self.assertIn('visible failure', result['error'])

    def test_page_exceptions_and_empty_results_are_reported(self) -> None:
        for raw in ('null', '', '{}', '{"ok":false,"error":"Error: page failed"}'):
            browser = bridge.SafariBrowser(runner=FakeRunner(completed(raw)))
            with self.subTest(raw=raw), self.assertRaises(bridge.SafariBridgeError):
                browser.evaluate(91, 1, '42')

    def test_tabs_reads_json_without_page_script_and_retains_metadata(self) -> None:
        expected = [{
            "window_id": 91,
            "title": "Mail | Example",
            "bounds": {"x": 0, "y": 20, "width": 1440, "height": 900},
            "tabs": [{"tab_index": 2, "url": "https://example.test/#inbox",
                      "title": "Inbox", "selected": True}],
        }]
        runner = FakeRunner(completed(json.dumps(expected)))
        browser = bridge.SafariBrowser(runner=runner)

        self.assertEqual(browser.tabs(), expected)
        command, kwargs = runner.calls[0]
        self.assertEqual(command[:4], ["/usr/bin/osascript", "-l", "JavaScript", "-e"])
        self.assertEqual(command[4], bridge._TABS_SCRIPT)
        self.assertNotIn("doJavaScript", command[4])
        self.assertEqual(len(command), 5)
        self.assertEqual(kwargs["timeout"], 20.0)
        self.assertTrue(kwargs["capture_output"])

    def test_evaluate_passes_caller_code_as_separate_argv_and_decodes_json(self) -> None:
        script = '({text: "quote \\"; )\\n throw new Error(1) //", unicode: "é 📬"})'
        runner = FakeRunner(completed(json.dumps({'ok':True,'value':{'text':'ok','unicode':'é 📬'}})))
        browser = bridge.SafariBrowser(runner=runner)

        expected_url = 'https://mail.example.test/#search/quote%22'
        self.assertEqual(browser.evaluate(202, 3, script, expected_url), {"text": "ok", "unicode": "é 📬"})
        command, _ = runner.calls[0]
        self.assertEqual(command[4], bridge._EVALUATE_SCRIPT)
        self.assertEqual(command[5:], ["202", "3", script, json.dumps(expected_url)])
        self.assertIn("Number(w.id()) === windowId", command[4])
        self.assertIn("tabs[tabIndex - 1]", command[4])
        self.assertIn("expectedURL", command[4])
        self.assertIn("String(tabs[tabIndex - 1].url()) !== expectedURL", command[4])
        self.assertNotIn(script, command[4])

    def test_navigate_passes_hostile_looking_url_as_data_and_checks_returned_target(self) -> None:
        url = 'https://mail.example.test/x?quote=%22%3Bthrow%20new%20Error(1)&emoji=📬#part'
        runner = FakeRunner(completed(json.dumps({"window_id": 77, "tab_index": 4, "url": url})))
        browser = bridge.SafariBrowser(runner=runner)

        previous_url = 'https://mail.example.test/#search/old-id'
        self.assertEqual(browser.navigate(77, 4, url, previous_url), {"window_id": 77, "tab_index": 4, "url": url})
        command, _ = runner.calls[0]
        self.assertEqual(command[4], bridge._NAVIGATE_SCRIPT)
        self.assertEqual(command[5:], ["77", "4", url, json.dumps(previous_url)])
        self.assertIn("Number(w.id()) === windowId", command[4])
        self.assertIn("tabs[tabIndex - 1].url = destination", command[4])
        self.assertIn("expectedURL", command[4])
        self.assertIn("String(tabs[tabIndex - 1].url()) !== expectedURL", command[4])
        self.assertNotIn(url, command[4])

    def test_rejects_invalid_targets_scripts_and_urls_before_osascript(self) -> None:
        runner = FakeRunner()
        browser = bridge.SafariBrowser(runner=runner)
        invalid_calls = [
            lambda: browser.evaluate(True, 1, "1"),
            lambda: browser.evaluate(1, 0, "1"),
            lambda: browser.evaluate(1, 1, "  "),
            lambda: browser.navigate(1, 1, "javascript:alert(1)"),
            lambda: browser.navigate(1, 1, "file:///tmp/a"),
            lambda: browser.navigate(1, 1, "https:///missing-host"),
        ]
        for call in invalid_calls:
            with self.subTest(call=call), self.assertRaises(ValueError):
                call()
        self.assertEqual(runner.calls, [])

    def test_reports_mismatched_navigation_target(self) -> None:
        runner = FakeRunner(completed(json.dumps({"window_id": 77, "tab_index": 5, "url": "https://x.test"})))
        browser = bridge.SafariBrowser(runner=runner)
        with self.assertRaisesRegex(bridge.SafariBridgeError, "did not match"):
            browser.navigate(77, 4, "https://x.test")

    def test_timeout_and_process_start_errors_are_clear(self) -> None:
        runner = FakeRunner(subprocess.TimeoutExpired("osascript", 2))
        browser = bridge.SafariBrowser(timeout=2, runner=runner)
        with self.assertRaisesRegex(bridge.SafariBridgeError, "timed out after 2s"):
            browser.tabs()

        runner = FakeRunner(OSError("osascript missing"))
        browser = bridge.SafariBrowser(runner=runner)
        with self.assertRaisesRegex(bridge.SafariBridgeError, "Could not start osascript: osascript missing"):
            browser.tabs()

    def test_permission_and_javascript_preference_errors_are_actionable(self) -> None:
        cases = [
            ("Not authorized to send Apple events to Safari", "Automation settings"),
            ("JavaScript is disabled for Apple Events", "JavaScript from Apple Events may be disabled"),
            ("operation failed", "operation failed"),
        ]
        for detail, expected in cases:
            runner = FakeRunner(completed(stderr=detail, returncode=-1743))
            browser = bridge.SafariBrowser(runner=runner)
            with self.subTest(detail=detail), self.assertRaisesRegex(bridge.SafariBridgeError, expected):
                browser.tabs()

    def test_invalid_and_unexpected_json_shapes_are_reported(self) -> None:
        cases = [
            (completed("not json"), "invalid tab metadata JSON"),
            (completed(json.dumps({"not": "a list"})), "unexpected tab metadata value"),
        ]
        for response, expected in cases:
            browser = bridge.SafariBrowser(runner=FakeRunner(response))
            with self.subTest(expected=expected), self.assertRaisesRegex(bridge.SafariBridgeError, expected):
                browser.tabs()

        browser = bridge.SafariBrowser(runner=FakeRunner(completed("not json")))
        with self.assertRaisesRegex(bridge.SafariBridgeError, "invalid navigation JSON"):
            browser.navigate(1, 1, "https://x.test")

    def test_read_dom_and_collect_links_decode_bounded_results(self) -> None:
        dom = {"url": "https://x.test", "title": "X", "ready_state": "complete",
               "text": "read", "total_chars": 4, "truncated": False}
        links = {"links": [{"url": "https://x.test/a", "text": "A", "title": ""}],
                 "total_links": 1, "truncated": False}
        runner = FakeRunner(completed(json.dumps({'ok':True,'value':dom})), completed(json.dumps({'ok':True,'value':links})))
        browser = bridge.SafariBrowser(runner=runner)
        self.assertEqual(browser.read_dom(3, 1), dom)
        self.assertEqual(browser.collect_links(3, 1), links)
        self.assertIn("text.slice(0, limit)", runner.calls[0][0][7])
        self.assertIn("all.slice(0, 1000)", runner.calls[1][0][7])

        browser = bridge.SafariBrowser(runner=FakeRunner(completed(json.dumps({'ok':True,'value':[]}))))
        with self.assertRaisesRegex(bridge.SafariBridgeError, "unexpected DOM text result"):
            browser.read_dom(3, 1)
        browser = bridge.SafariBrowser(runner=FakeRunner(completed(json.dumps({'ok':True,'value':{'links':'bad'}}))))
        with self.assertRaisesRegex(bridge.SafariBridgeError, "unexpected link collection"):
            browser.collect_links(3, 1)


class BrowserMcpSourceContractTests(unittest.TestCase):
    @unittest.skipUnless(Path('/System/Library/Frameworks/AppKit.framework').exists(), 'Requires macOS desktop modules')
    def test_mcp_refuses_duplicate_url_before_mutation_or_frame_invalidation(self) -> None:
        import desktop as module
        registered = {}
        class Server:
            def tool(self, **kwargs):
                def decorate(function):
                    registered[function.__name__] = function
                    return function
                return decorate
            def run(self, **kwargs):
                pass
        target_url = 'https://example.test/same'
        browser = Mock()
        browser.tabs.return_value = [{'window_id':91, 'tabs':[
            {'tab_index':1,'url':target_url}, {'tab_index':2,'url':target_url}]}]
        companion = SimpleNamespace(lock=threading.RLock(), check=Mock(), frame='preserved',
                                    references={'preserved':True}, read_cache='preserved')
        companion.windows = lambda scope: [{'pid':123,'window_id':91,'space_ids':[1]}]
        with patch('mcp.server.fastmcp.FastMCP', return_value=Server()), \
             patch.object(module, 'SafariBrowser', return_value=browser), \
             patch.object(module, 'running_apps', return_value=[{'pid':123,'bundle_id':'com.apple.Safari'}]), \
             patch.object(module.Q, 'CGSessionCopyCurrentDictionary', return_value={}):
            module.mcp_server(companion)
            for operation in ('read','links','evaluate','navigate'):
                with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, 'URL is ambiguous'):
                    registered['desktop_browser'](operation=operation, window_id=91, tab_index=1,
                        expected_url=target_url, script='42', url='https://example.test/new')
        self.assertEqual(companion.frame, 'preserved')
        browser.evaluate.assert_not_called()
        browser.navigate.assert_not_called()
        browser.read_dom.assert_not_called()
        browser.collect_links.assert_not_called()

    def test_browser_tool_schema_contract_and_action_annotations(self) -> None:
        """Check the MCP signature/decorator offline without starting desktop.py."""
        tree = ast.parse((HERE / "desktop.py").read_text())
        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "desktop_browser"
        )
        self.assertTrue(any(isinstance(d, ast.Call) and ast.unparse(d.func) == "server.tool"
                            for d in function.decorator_list))
        signature = ast.unparse(function.args)
        compact_signature = "".join(signature.split())
        self.assertIn("Literal['tabs','read','links','evaluate','navigate','navigate_read','act_read']", compact_signature)
        for parameter in ("window_id", "tab_index", "script", "url", "space_scope", "max_output_chars"):
            self.assertIn(parameter, signature)
        self.assertIn("max_length=200000", compact_signature)
        self.assertIn("max_output_chars:int=Field(default=30000,ge=1,le=1000000)", compact_signature)
        self.assertIn("expected_url", signature)
        decorator = next(d for d in function.decorator_list if isinstance(d, ast.Call))
        annotations = ast.unparse(decorator)
        self.assertIn("readOnlyHint=False", annotations)
        self.assertIn("destructiveHint=True", annotations)
        self.assertIn("openWorldHint=True", annotations)


@unittest.skipUnless(Path('/System/Library/Frameworks/AppKit.framework').exists(), 'Requires macOS desktop modules')
class BrowserTargetCacheTests(unittest.TestCase):
    def setUp(self):
        import desktop as module
        self.module=module
        self.registered={}
        registered=self.registered
        class Server:
            def tool(self,**kwargs):
                def decorate(function):
                    registered[function.__name__]=function
                    return function
                return decorate
            def run(self,**kwargs):
                pass
        self.url='https://example.test/one'
        self.browser=Mock()
        self.browser.tabs.return_value=[{'window_id':91,'bounds':{'x':0,'y':0,'width':100,'height':100},
            'tabs':[{'tab_index':1,'url':self.url}]}]
        self.browser.read_dom.return_value={'text':'fixture'}
        self.browser.collect_links.return_value={'links':[]}
        self.browser.evaluate.return_value=42
        self.browser.navigate.return_value={'url':'https://example.test/two'}
        self.windows=[{'pid':123,'window_id':191,'space_ids':[1],
                       'bounds':{'X':0,'Y':0,'Width':100,'Height':100}}]
        self.apps=[{'pid':123,'bundle_id':'com.apple.Safari'}]
        self.companion=SimpleNamespace(lock=threading.RLock(),check=Mock(),frame='before',
            references={'before':True},read_cache='before',windows=lambda scope:self.windows)
        self.locked={}
        patches=[patch('mcp.server.fastmcp.FastMCP',return_value=Server()),
            patch.object(module,'SafariBrowser',return_value=self.browser),
            patch.object(module,'running_apps',side_effect=lambda:self.apps),
            patch.object(module.Q,'CGSessionCopyCurrentDictionary',side_effect=lambda:self.locked)]
        for mocked in patches:
            mocked.start()
            self.addCleanup(mocked.stop)
        module.mcp_server(self.companion)

    def call(self,operation,**kwargs):
        return self.registered['desktop_browser'](operation=operation,window_id=91,tab_index=1,
            max_output_chars=30000,**kwargs)

    def test_explicit_url_reuses_window_mapping_without_relisting_tabs(self):
        self.call('tabs')
        self.call('read',expected_url=self.url)
        self.call('links',expected_url=self.url)
        self.call('evaluate',expected_url=self.url,script='42')
        self.assertEqual(self.browser.tabs.call_count,1)
        self.browser.evaluate.assert_called_once_with(91,1,'42',self.url)
        self.assertEqual(self.companion.frame,None)
        self.assertEqual(self.companion.references,{})
        self.assertEqual(self.companion.read_cache,None)

    def test_without_expected_url_rediscovers_live_tabs(self):
        self.call('tabs')
        self.call('read')
        self.assertEqual(self.browser.tabs.call_count,2)
        self.browser.read_dom.assert_called_once_with(91,1,self.url)

    def test_navigation_next_url_is_verified_live_instead_of_using_stale_metadata(self):
        self.call('tabs')
        new_url='https://example.test/two'
        self.call('navigate',expected_url=self.url,url=new_url)
        self.call('read',expected_url=new_url)
        self.assertEqual(self.browser.tabs.call_count,1)
        self.browser.navigate.assert_called_once_with(91,1,new_url,self.url)
        self.browser.read_dom.assert_called_once_with(91,1,new_url)

    def test_missing_or_other_space_window_is_refused_before_page_code(self):
        self.call('tabs')
        self.windows=[]
        with self.assertRaisesRegex(ValueError,'Space scope'):
            self.call('evaluate',expected_url=self.url,script='42')
        self.browser.evaluate.assert_not_called()
        self.assertEqual(self.companion.frame,'before')

    def test_changed_process_is_refused_even_if_window_number_is_reused(self):
        self.call('tabs')
        self.apps=[{'pid':124,'bundle_id':'com.apple.Safari'}]
        self.windows[0]['pid']=124
        with self.assertRaisesRegex(ValueError,'unavailable'):
            self.call('evaluate',expected_url=self.url,script='42')
        self.browser.evaluate.assert_not_called()

    def test_live_url_error_propagates_and_mutation_frame_remains_invalid(self):
        self.call('tabs')
        self.browser.evaluate.side_effect=bridge.SafariBridgeError('Safari tab URL changed')
        with self.assertRaisesRegex(bridge.SafariBridgeError,'URL changed'):
            self.call('evaluate',expected_url=self.url,script='42')
        self.assertEqual(self.companion.frame,None)
        self.assertEqual(self.companion.references,{})
        self.assertEqual(self.companion.read_cache,None)

    def test_lock_and_pause_guards_still_run_after_discovery(self):
        self.call('tabs')
        self.locked={'CGSSessionScreenIsLocked':True}
        with self.assertRaisesRegex(RuntimeError,'Unlock'):
            self.call('read',expected_url=self.url)
        self.locked={}
        self.companion.check.side_effect=RuntimeError('Computer use is paused')
        with self.assertRaisesRegex(RuntimeError,'paused'):
            self.call('read',expected_url=self.url)
        self.browser.read_dom.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
