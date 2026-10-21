"""Offline Chrome adapter, JXA targeting, cache isolation and workflow checks."""
import json
import unittest
from unittest.mock import Mock, patch

import chrome_browser as chrome
import verify_browser
import verify_workflows


class ChromeBridgeTests(verify_browser.SafariBrowserTests):
    def setUp(self):
        # Exercise the existing transport/result/argument contract for both adapters.
        adapter = Mock(SafariBrowser=chrome.ChromeBrowser,
                       SafariBridgeError=chrome.ChromeBridgeError,
                       _TABS_SCRIPT=chrome._TABS_SCRIPT,
                       _EVALUATE_SCRIPT=chrome._EVALUATE_SCRIPT,
                       _NAVIGATE_SCRIPT=chrome._NAVIGATE_SCRIPT)
        self.adapter_patch = patch.object(verify_browser, 'bridge', adapter)
        self.adapter_patch.start()
        self.addCleanup(self.adapter_patch.stop)

    def mock_app(self, duplicate=False):
        url = 'https://example.test/current'
        other = url if duplicate else 'https://example.test/other'
        return '''var executed=0;
        var target={url:function(){return URL;},execute:function(options){
            executed++;return eval(options.javascript);}};
        var other={url:function(){return OTHER;},execute:function(){throw Error("WRONG TAB");}};
        function Application(name){if(name!=="Google Chrome")throw Error("WRONG APP");return {
            windows:function(){return [{id:function(){return 91;},tabs:function(){return [target,other];}}];}
        };}\n'''.replace('URL', json.dumps(url)).replace('OTHER', json.dumps(other))

    def test_duplicate_url_tabs_are_refused_before_script_or_navigation(self):
        browser = chrome.ChromeBrowser()
        for source in (chrome._EVALUATE_SCRIPT, chrome._NAVIGATE_SCRIPT):
            for expected in (None, 'https://example.test/current'):
                with self.subTest(source=source, expected=expected):
                    with self.assertRaisesRegex(chrome.ChromeBridgeError, 'URL is ambiguous'):
                        browser._run(self.mock_app(True)+source, '91', '1',
                                     'https://example.test/new', json.dumps(expected))

    def test_duplicate_urls_elsewhere_do_not_block_a_unique_target(self):
        mock = self.mock_app().replace('return [target,other]', 'return [target,other,other]')
        raw = chrome.ChromeBrowser()._run(mock+chrome._EVALUATE_SCRIPT, '91', '1', '42', 'null')
        self.assertEqual(json.loads(raw), {'ok': True, 'value': 42})

    def test_real_jxa_expression_serialization_and_empty_url_guard(self):
        browser = chrome.ChromeBrowser()
        for script, expected in [('({text:"é 📬",nested:[true,null,42]})',
                                  {'text':'é 📬','nested':[True,None,42]}),
                                 ('(() => { return 6*7; })();', 42), ('undefined', None)]:
            raw = browser._run(self.mock_app()+chrome._EVALUATE_SCRIPT, '91', '1', script,
                               json.dumps('https://example.test/current'))
            self.assertEqual(json.loads(raw), {'ok':True,'value':expected})
        for window, tab, expected, error in [('92','1',None,'window id'),
                                            ('91','3',None,'out of range'),
                                            ('91','1','','URL changed'),
                                            ('91','1','https://wrong.test','URL changed')]:
            for source in (chrome._EVALUATE_SCRIPT, chrome._NAVIGATE_SCRIPT):
                with self.assertRaisesRegex(chrome.ChromeBridgeError, error):
                    browser._run(self.mock_app()+source,window,tab,'42',json.dumps(expected))
        raw = browser._run(self.mock_app()+chrome._EVALUATE_SCRIPT, '91', '1',
                           '(() => { throw Error("page failed"); })()', 'null')
        self.assertFalse(json.loads(raw)['ok'])

    def test_tab_metadata_uses_active_index_and_chrome_titles(self):
        mock = '''function Application(name){return {windows:function(){return [{
            id:function(){return "91";},name:function(){return "Chrome";},
            bounds:function(){return {x:10,y:20,width:900,height:700};},
            activeTabIndex:function(){return 2;},tabs:function(){return [
                {title:function(){return "First";},url:function(){return "https://a.test";}},
                {title:function(){return "Second";},url:function(){return "https://b.test";}}
            ];}}];}};}\n'''
        result = json.loads(chrome.ChromeBrowser()._run(mock+chrome._TABS_SCRIPT))
        self.assertEqual(result[0]['window_id'], 91)
        self.assertEqual(result[0]['tabs'], [
            {'tab_index':1,'title':'First','url':'https://a.test','selected':False},
            {'tab_index':2,'title':'Second','url':'https://b.test','selected':True}])

    def test_chrome_disabled_scripting_error_identifies_the_developer_menu(self):
        runner = verify_browser.FakeRunner(verify_browser.completed(returncode=1,
            stderr='Executing JavaScript through AppleScript is turned off.'))
        with self.assertRaisesRegex(chrome.ChromeBridgeError, 'View > Developer'):
            chrome.ChromeBrowser(runner=runner).evaluate(91,1,'42')


class ChromeCacheTests(verify_browser.BrowserTargetCacheTests):
    browser_choice = 'chrome'

    def test_browser_namespaces_do_not_invalidate_or_reuse_each_others_cache(self):
        safari = Mock()
        safari.tabs.return_value = self.browser.tabs.return_value
        safari.read_dom.return_value = {'text':'Safari fixture'}
        self.apps.append({'pid':456,'bundle_id':'com.apple.Safari'})
        self.windows.append({**self.windows[0], 'pid':456, 'window_id':291})
        with patch.object(self.module, 'SafariBrowser', return_value=safari):
            self.module.mcp_server(self.companion)
        self.call('tabs')
        self.registered['desktop_browser'](operation='tabs',browser='safari',max_output_chars=30000)
        self.call('read',expected_url=self.url)
        self.registered['desktop_browser'](operation='read',browser='safari',window_id=91,
                                          tab_index=1,expected_url=self.url,max_output_chars=30000)
        self.assertEqual(self.browser.tabs.call_count, 1)
        self.assertEqual(safari.tabs.call_count, 1)
        self.browser.read_dom.assert_called_once_with(91,1,self.url)
        safari.read_dom.assert_called_once_with(91,1,self.url)

    def test_invalid_browser_does_not_touch_the_desktop(self):
        with self.assertRaisesRegex(ValueError, 'browser must be'):
            self.registered['desktop_browser'](browser='unknown')
        self.companion.check.assert_not_called()
        self.browser.tabs.assert_not_called()


class ChromeNavigateReadTests(verify_workflows.BrowserWorkflowTests):
    def setUp(self):
        patcher = patch.object(verify_workflows, 'SafariBrowser', chrome.ChromeBrowser)
        patcher.start()
        self.addCleanup(patcher.stop)


class ChromeActionReadTests(verify_workflows.BrowserActionWorkflowTests):
    def setUp(self):
        patcher = patch.object(verify_workflows, 'SafariBrowser', chrome.ChromeBrowser)
        patcher.start()
        self.addCleanup(patcher.stop)


if __name__ == '__main__':
    unittest.main(verbosity=2)
