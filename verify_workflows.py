"""Offline browser/native workflow failure and fresh-target regressions."""
import json
import threading
import time
import unittest
from unittest.mock import Mock,patch
from pydantic import ValidationError

from desktop import Desktop, PlanStep
from safari_browser import SafariBrowser, SafariBridgeError
from verify_browser import FakeRunner, completed


def navigation():
    return completed(json.dumps({'window_id':91,'tab_index':1,'url':'https://example.test/two'}))


def page(value):
    return completed(json.dumps({'ok':True,'value':value}))


class BrowserWorkflowTests(unittest.TestCase):
    def call(self,browser,**kwargs):
        return browser.navigate_read(91,1,'https://example.test/two','https://example.test/one',
            ready_selector='#heading',**kwargs)

    def test_navigation_runs_once_then_waits_and_returns_scoped_reading(self):
        runner=FakeRunner(navigation(),page({'ready':False}),page({'ready':True,'text':'Ready body'}))
        guard=Mock()
        result=self.call(SafariBrowser(runner=runner),read_selector='#body',check=guard)
        self.assertEqual(result['text'],'Ready body')
        self.assertEqual(result['workflow']['completed_steps'],['navigate','wait','read'])
        self.assertEqual(result['workflow']['polls'],2)
        self.assertEqual(len(runner.calls),3)
        self.assertIn('#body',runner.calls[1][0][7])
        self.assertEqual(json.loads(runner.calls[1][0][8]),'https://example.test/two')
        self.assertEqual(guard.call_count,4)

    def test_missing_explicit_readiness_or_source_url_sends_no_events(self):
        runner=FakeRunner()
        browser=SafariBrowser(runner=runner)
        for kwargs in ({'expected_url':None,'ready_selector':'#heading'},
                       {'expected_url':'https://example.test/one'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                browser.navigate_read(91,1,'https://example.test/two',**kwargs)
        self.assertEqual(runner.calls,[])

    def test_ambiguity_stops_without_replaying_navigation(self):
        runner=FakeRunner(navigation(),page({'ambiguous':True}))
        with self.assertRaisesRegex(SafariBridgeError,'ambiguous'):
            self.call(SafariBrowser(runner=runner))
        self.assertEqual(len(runner.calls),2)

    def test_wrong_destination_or_redirect_stops_after_navigation(self):
        runner=FakeRunner(navigation(),completed(stderr='Safari tab URL changed',returncode=1))
        with self.assertRaisesRegex(SafariBridgeError,'navigation may have run.*URL changed'):
            self.call(SafariBrowser(runner=runner))
        self.assertEqual(len(runner.calls),2)

    def test_pause_between_navigation_and_poll_prevents_read(self):
        runner=FakeRunner(navigation())
        guard=Mock(side_effect=[None,RuntimeError('Computer use is paused')])
        with self.assertRaisesRegex(SafariBridgeError,'paused'):
            self.call(SafariBrowser(runner=runner),check=guard)
        self.assertEqual(len(runner.calls),1)

    def test_changed_window_before_navigation_sends_no_events(self):
        runner=FakeRunner()
        with self.assertRaisesRegex(SafariBridgeError,'Space scope'):
            self.call(SafariBrowser(runner=runner),check=Mock(side_effect=RuntimeError('Window left Space scope')))
        self.assertEqual(runner.calls,[])

    def test_deadline_bounds_polling_and_each_apple_event(self):
        runner=FakeRunner(navigation(),page({'ready':False}))
        with self.assertRaisesRegex(SafariBridgeError,'deadline'):
            self.call(SafariBrowser(runner=runner),timeout=.015)
        self.assertEqual(len(runner.calls),2)
        self.assertTrue(all(0<kwargs['timeout']<=.015 for _,kwargs in runner.calls))

    def test_script_failure_reports_partial_navigation_without_retry(self):
        runner=FakeRunner(navigation(),completed(json.dumps({'ok':False,'error':'Invalid CSS selector'})))
        with self.assertRaisesRegex(SafariBridgeError,'Invalid CSS selector'):
            self.call(SafariBrowser(runner=runner))
        self.assertEqual(len(runner.calls),2)


class BrowserWindowScopeTests(unittest.TestCase):
    def setUp(self):
        self.d=Desktop.__new__(Desktop)
        self.d.spaces=Mock(available=True)
        self.d.spaces.active_id.return_value=7
        self.d.spaces.window_spaces.return_value=[7]
        self.d.spaces.window_ordered.return_value=True
        self.window={'kCGWindowNumber':191,'kCGWindowOwnerPID':123,'kCGWindowLayer':0,
                     'kCGWindowBounds':{'Width':100,'Height':100},'kCGWindowIsOnscreen':True}
        self.d._window_metadata=Mock(return_value=[self.window])

    def test_exact_live_window_is_checked_without_inventory(self):
        self.assertTrue(self.d.window_in_scope(191,123,'active'))
        self.d._window_metadata.assert_called_once()
        self.assertEqual(self.d._window_metadata.call_args.args[1],191)

    def test_changed_owner_or_closed_window_is_refused(self):
        self.assertFalse(self.d.window_in_scope(191,124,'active'))
        self.d._window_metadata.return_value=[]
        self.assertFalse(self.d.window_in_scope(191,123,'active'))

    def test_other_space_requires_explicit_all_scope(self):
        self.d.spaces.window_spaces.return_value=[9]
        self.window['kCGWindowIsOnscreen']=False
        self.assertFalse(self.d.window_in_scope(191,123,'active'))
        self.assertTrue(self.d.window_in_scope(191,123,'all'))
        self.d.spaces.require.assert_called_once()

    def test_minimized_or_unmapped_window_is_refused(self):
        self.d.spaces.window_ordered.return_value=False
        self.assertFalse(self.d.window_in_scope(191,123,'all'))
        self.d.spaces.window_ordered.return_value=True
        self.d.spaces.window_spaces.return_value=[]
        self.assertFalse(self.d.window_in_scope(191,123,'all'))

    def test_unavailable_spaces_require_onscreen_window(self):
        self.d.spaces.available=False
        self.assertTrue(self.d.window_in_scope(191,123,'active'))
        self.window['kCGWindowIsOnscreen']=False
        self.assertFalse(self.d.window_in_scope(191,123,'active'))

class NativePlanTests(unittest.TestCase):
    def setUp(self):
        d=self.d=Desktop.__new__(Desktop)
        d.lock=threading.RLock()
        d.plan_deadline=None
        d.screen_allowed=lambda:True
        d.input_allowed=lambda:True
        d.frame={'id':'initial','window_id':191,'target_pid':123,
                 'viewport':{'x':0,'y':0,'width':100,'height':100},'display':{'width':100},
                 'ui':{'elements':[],'truncated':False},'config':{'window_id':191}}
        d.references={}
        d.read_cache=None
        d.display_geometry=lambda:{'width':100}
        d.check_focus=Mock()
        d.check_background=Mock()
        d.perform=Mock()
        d.perform_background=Mock()
        d.ui=Mock()
        self.count=0
        def find(*args,**kwargs):
            self.count+=1
            node={'id':f'fresh{self.count}','role':'AXButton','name':'Next','pid':123,
                  'actions':['AXPress'],'bounds':{'x':10,'y':10,'width':20,'height':20}}
            return {'elements':[node],'truncated':False},{node['id']:(object(),node)},node
        d.ui.find_unique.side_effect=find
        d.ui.resolve.side_effect=lambda refs,key:refs[key]
        def observe(**kwargs):
            d.frame={'id':'after'}
            d.references={}
            return {'frame_id':'after','ui':{'elements':[],'truncated':False}},None
        d.observe=Mock(side_effect=observe)

    def test_new_controls_are_resolved_between_actions(self):
        result,_=self.d.plan([{'kind':'press','name':'Next'},{'kind':'press','name':'Newly revealed'}],'initial')
        self.assertEqual([call.args[0].element_id for call in self.d.perform.call_args_list],['fresh1','fresh2'])
        self.assertEqual(result['plan']['completed_steps'],2)
        self.d.observe.assert_called_once()
        self.assertEqual(self.d.plan_deadline,None)

    def test_whole_plan_shape_is_validated_before_first_input(self):
        with self.assertRaises(ValidationError):
            self.d.plan([{'kind':'press','name':'Next'},{'kind':'replace_text','role':'AXTextField'}],'initial')
        self.d.perform.assert_not_called()
        self.d.ui.find_unique.assert_not_called()

    def test_selectorless_and_unexpected_text_steps_are_refused(self):
        for data in ({'kind':'press'},{'kind':'press','name':'Next','text':'bad'}):
            with self.subTest(data=data),self.assertRaises(ValidationError):
                PlanStep.model_validate(data)

    def test_later_ambiguity_reports_partial_completion_and_clears_frame(self):
        original=self.d.ui.find_unique.side_effect
        def find(*args,**kwargs):
            if self.count:
                raise ValueError('Readiness selector is ambiguous')
            return original(*args,**kwargs)
        self.d.ui.find_unique.side_effect=find
        with self.assertRaisesRegex(RuntimeError,'after 1 completed steps.*ambiguous'):
            self.d.plan([{'kind':'press','name':'Next'},{'kind':'press','name':'Duplicate'}],'initial')
        self.d.perform.assert_called_once()
        self.assertEqual(self.d.frame,None)
        self.assertEqual(self.d.references,{})
        self.assertEqual(self.d.plan_deadline,None)

    def test_foreground_change_stops_before_input(self):
        self.d.check_focus.side_effect=RuntimeError('Foreground app changed')
        with self.assertRaisesRegex(RuntimeError,'after 0 completed steps.*Foreground app changed'):
            self.d.plan([{'kind':'press','name':'Next'}],'initial')
        self.d.perform.assert_not_called()

    def test_background_uses_only_background_transport(self):
        self.d.validate_background=Mock()
        self.d.plan([{'kind':'press','name':'Next'}],'initial',mode='background')
        self.d.perform.assert_not_called()
        self.d.perform_background.assert_called_once()
        self.d.check_background.assert_called()

    def test_background_secure_refusal_prevents_dispatch(self):
        self.d.validate_background=Mock(side_effect=ValueError('Secure fields require explicit foreground interaction'))
        with self.assertRaisesRegex(RuntimeError,'Secure fields'):
            self.d.plan([{'kind':'press','name':'Next'}],'initial',mode='background')
        self.d.perform_background.assert_not_called()

    def test_wait_step_resolves_condition_without_input(self):
        result,_=self.d.plan([{'kind':'wait_for','name':'Ready'}],'initial')
        self.d.perform.assert_not_called()
        self.assertEqual(result['plan']['steps'][0]['outcome'],'condition_observed')

    def test_total_deadline_stops_slow_action_and_invalidates_frame(self):
        self.d.perform.side_effect=lambda action:(time.sleep(.02),self.d.check(inputs=True))
        with self.assertRaisesRegex(RuntimeError,'deadline expired'):
            self.d.plan([{'kind':'press','name':'Next'}],'initial',timeout=.005)
        self.assertEqual(self.d.frame,None)
        self.assertEqual(self.d.plan_deadline,None)

    def test_stale_or_unpinned_frame_sends_no_input(self):
        with self.assertRaisesRegex(ValueError,'selected-window'):
            self.d.plan([{'kind':'press','name':'Next'}],'old')
        self.d.frame['window_id']=None
        with self.assertRaisesRegex(ValueError,'selected-window'):
            self.d.plan([{'kind':'press','name':'Next'}],'initial')
        self.d.perform.assert_not_called()

    def test_summary_skips_final_inventory_and_expires_frame(self):
        self.d.ui.read_text.return_value={'text':'Final result','truncated':False}
        result,_=self.d.plan([{'kind':'wait_for','name':'Ready'}],'initial',summary=True)
        self.assertEqual(result['reading']['text'],'Final result')
        self.assertTrue(result['frame_expired'])
        self.assertNotIn('frame_id',result)
        self.d.observe.assert_not_called()
        self.assertIsNone(self.d.frame)
        self.assertEqual(self.d.references,{})

    def test_summary_read_scope_is_resolved_fresh_and_text_limit_is_explicit(self):
        self.d.ui.read_text.return_value={'text':'abcdefgh','truncated':False}
        result,_=self.d.plan([{'kind':'wait_for','name':'Ready'}],'initial',summary=True,
                              read_selector={'identifier':'result'},max_read_chars=4)
        self.assertEqual(self.d.ui.find_unique.call_count,2)
        self.assertEqual(self.d.ui.find_unique.call_args.kwargs['identifier'],'result')
        self.assertEqual(result['reading']['text'],'abcd')
        self.assertTrue(result['reading']['page_truncated'])
        self.assertEqual(result['reading']['total_chars'],8)

    def test_summary_read_failure_reports_completed_steps_and_clears_frame(self):
        self.d.ui.read_text.side_effect=RuntimeError('Read target disappeared')
        with self.assertRaisesRegex(RuntimeError,'after 1 completed steps.*disappeared'):
            self.d.plan([{'kind':'press','name':'Next'}],'initial',summary=True)
        self.assertIsNone(self.d.frame)
        self.d.observe.assert_not_called()

    def test_read_scope_shape_is_checked_before_input(self):
        with self.assertRaises(ValidationError):
            self.d.plan([{'kind':'press','name':'Next'}],'initial',summary=True,read_selector={})
        self.d.perform.assert_not_called()

    def test_locked_native_plan_sends_no_input(self):
        with patch('desktop.Q.CGSessionCopyCurrentDictionary',return_value={'CGSSessionScreenIsLocked':True}):
            with self.assertRaisesRegex(RuntimeError,'Unlock'):
                self.d.plan([{'kind':'press','name':'Next'}],'initial')
        self.d.perform.assert_not_called()


class BrowserActionWorkflowTests(unittest.TestCase):
    def call(self,browser,steps=None,**kwargs):
        return browser.act_read(91,1,steps or [{'kind':'click','selector':'#open'},{'kind':'click','selector':'#save'}],
                                'https://example.test/one',ready_selector='#ready',**kwargs)

    def test_completed_actions_are_not_repeated_while_waiting(self):
        runner=FakeRunner(page({'next':1,'ready':False}),page({'next':2,'ready':True,'text':'Done'}))
        result=self.call(SafariBrowser(runner=runner),read_selector='#result')
        self.assertEqual(result['workflow']['completed_steps'],2)
        self.assertEqual(result['text'],'Done')
        self.assertIn('"start": 0',runner.calls[0][0][7])
        self.assertIn('"start": 1',runner.calls[1][0][7])
        self.assertEqual(len(runner.calls),2)

    def test_later_ambiguity_reports_partial_completion_and_stops(self):
        runner=FakeRunner(page({'next':1,'failure':'Ambiguous visible selector'}))
        with self.assertRaisesRegex(SafariBridgeError,'after 1 completed steps.*Ambiguous'):
            self.call(SafariBrowser(runner=runner))
        self.assertEqual(len(runner.calls),1)

    def test_partial_transport_failure_never_replays(self):
        runner=FakeRunner(completed(stderr='Safari failed after click',returncode=1))
        with self.assertRaisesRegex(SafariBridgeError,'current step may also have partially run'):
            self.call(SafariBrowser(runner=runner))
        self.assertEqual(len(runner.calls),1)

    def test_all_step_shapes_are_checked_before_any_events(self):
        runner=FakeRunner()
        with self.assertRaisesRegex(ValueError,'requires text'):
            self.call(SafariBrowser(runner=runner),steps=[{'kind':'click','selector':'#open'},
                                                        {'kind':'replace_text','selector':'#field'}])
        self.assertEqual(runner.calls,[])

    def test_pause_after_first_action_stops_before_following_actions(self):
        runner=FakeRunner(page({'next':1,'ready':False}))
        with self.assertRaisesRegex(SafariBridgeError,'after 1 completed steps.*paused'):
            self.call(SafariBrowser(runner=runner),check=Mock(side_effect=[None,RuntimeError('paused')]))
        self.assertEqual(len(runner.calls),1)

    def test_deadline_stops_polls_without_action_replay(self):
        runner=FakeRunner(page({'next':2,'ready':False}))
        with self.assertRaisesRegex(SafariBridgeError,'after 2 completed steps.*deadline'):
            self.call(SafariBrowser(runner=runner),timeout=.01)
        self.assertEqual(len(runner.calls),1)

    def test_invalid_progress_acknowledgement_is_refused(self):
        runner=FakeRunner(page({'next':25,'ready':True}))
        with self.assertRaisesRegex(SafariBridgeError,'Unexpected DOM workflow result'):
            self.call(SafariBrowser(runner=runner))

    def test_bad_script_result_contains_actionable_iife_hint(self):
        with self.assertRaisesRegex(SafariBridgeError,'wrap statements in an IIFE'):
            SafariBrowser(runner=FakeRunner(completed('null'))).evaluate(91,1,"click();'done'")


if __name__=='__main__':
    unittest.main(verbosity=2)
