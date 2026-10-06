"""Offline readiness regressions using mocked Accessibility, with no UI input."""
import unittest
import time
from unittest.mock import patch

import native_ui


class NativeReadinessTests(unittest.TestCase):
    def setUp(self):
        self.fields = {
            'field': {'AXRole': 'AXTextArea', 'AXValue': 'a' * 2500 + 'READY_AT_END', 'AXChildren': []},
        }
        self.ui = native_ui.NativeUI.__new__(native_ui.NativeUI)
        self.ui.window = lambda *args: 'field'
        def attributes(element, names, flags, output):
            return 0, [self.fields[element].get(name) for name in names]
        def attribute(element, name, output):
            return 0, self.fields[element].get(name)
        patches = [
            patch.object(native_ui.AX, 'AXUIElementCopyMultipleAttributeValues', attributes),
            patch.object(native_ui.AX, 'AXUIElementCopyAttributeValue', attribute),
            patch.object(native_ui.AX, 'AXUIElementCopyActionNames', return_value=(0, [])),
            patch.object(native_ui.AX, 'AXUIElementIsAttributeSettable', return_value=(0, True)),
            patch.object(native_ui, 'window_id_of', return_value=91),
        ]
        for mocked in patches:
            mocked.start()
            self.addCleanup(mocked.stop)

    def wait(self, value):
        return self.ui.wait_for(123, None, 'AXTextArea', value, None, .03, lambda: None)

    def test_full_value_matches_while_returned_preview_stays_bounded(self):
        node = self.wait('READY_AT_END')
        self.assertEqual(len(node['value']), 2000)
        self.assertNotIn('READY_AT_END', node['value'])
        self.assertEqual(node['value_omitted_chars'], 512)

    def test_long_suffix_distinguishes_identical_previews(self):
        self.fields['root'] = {'AXRole': 'AXGroup', 'AXChildren': ['field', 'other']}
        self.fields['other'] = {'AXRole': 'AXTextArea', 'AXValue': 'a' * 2500 + 'DIFFERENT_END', 'AXChildren': []}
        self.ui.window = lambda *args: 'root'
        node = self.wait('READY_AT_END')
        self.assertEqual(node['id'], 'e1')

    def test_selector_scan_queries_only_unique_target_capabilities(self):
        self.fields['root']={'AXRole':'AXGroup','AXChildren':['field','other']}
        self.fields['other']={'AXRole':'AXButton','AXTitle':'Other','AXChildren':[]}
        self.ui.window=lambda *args:'root'
        with patch.object(native_ui.AX,'AXUIElementCopyActionNames',return_value=(0,['AXPress'])) as actions:
            snapshot,refs,node=self.ui.find_unique(123,None,'AXTextArea','READY_AT_END',None,.03,
                                                   lambda:None,selector_only=True)
        actions.assert_called_once_with('field',None)
        self.assertEqual(node['actions'],['AXPress'])
        self.assertTrue(node['value_settable'])
        self.assertNotIn('actions',snapshot['elements'][0])
        self.assertIs(refs[node['id']][1],node)

    def test_long_live_labels_disambiguate_identical_previews(self):
        prefix='x'*500
        self.fields['field']['AXTitle']=prefix+'first'
        self.fields['root']={'AXRole':'AXGroup','AXChildren':['field','other']}
        self.fields['other']=dict(self.fields['field'],AXTitle=prefix+'second')
        self.ui.window=lambda *args:'root'
        _,_,node=self.ui.find_unique(123,prefix+'first','AXTextArea',None,None,.03,lambda:None)
        self.assertEqual(node['id'],'e1')

    def test_truncated_label_or_identifier_is_not_an_exact_match(self):
        prefix='x'*500
        self.fields['field'].update(AXTitle=prefix+'suffix',AXIdentifier=prefix+'suffix')
        for kwargs in ({'name':prefix},{'identifier':prefix}):
            with self.subTest(kwargs=kwargs),self.assertRaises(TimeoutError):
                self.ui.find_unique(123,kwargs.get('name'),'AXTextArea',None,None,.01,
                                    lambda:None,identifier=kwargs.get('identifier'))

    def test_ambiguous_selector_scan_never_queries_action_capabilities(self):
        self.fields['root']={'AXRole':'AXGroup','AXChildren':['field','other']}
        self.fields['other']=dict(self.fields['field'])
        self.ui.window=lambda *args:'root'
        with patch.object(native_ui.AX,'AXUIElementCopyActionNames') as actions:
            with self.assertRaisesRegex(ValueError,'ambiguous'):
                self.ui.find_unique(123,None,'AXTextArea','READY_AT_END',None,.03,
                                    lambda:None,selector_only=True)
        actions.assert_not_called()

    def test_fast_selector_scan_omits_unrelated_values_geometry_and_capabilities(self):
        self.fields['field']['AXIdentifier']='target'
        self.fields['root']={'AXRole':'AXGroup','AXChildren':['field','other']}
        self.fields['other']={'AXRole':'AXStaticText','AXIdentifier':'other','AXValue':'Unrelated long text','AXChildren':[]}
        self.ui.window=lambda *args:'root'
        reader=native_ui.AX.AXUIElementCopyMultipleAttributeValues
        with patch.object(native_ui.AX,'AXUIElementCopyMultipleAttributeValues',wraps=reader) as reads:
            _,_,node=self.ui.find_unique(123,None,'AXTextArea',None,None,.03,lambda:None,
                                         identifier='target',selector_only=True)
        self.assertEqual(node['identifier'],'target')
        for call in reads.call_args_list:
            if call.args[0]!='field':
                self.assertNotIn('AXValue',call.args[1])
                self.assertNotIn('AXPosition',call.args[1])
                self.assertNotIn('AXSize',call.args[1])

    def test_incomplete_fast_selector_scan_does_not_establish_uniqueness(self):
        self.fields['field']['AXIdentifier']='target'
        self.fields['root']={'AXRole':'AXGroup','AXChildren':['field']+['other']*400}
        self.fields['other']={'AXRole':'AXStaticText','AXIdentifier':'other','AXChildren':[]}
        self.ui.window=lambda *args:'root'
        with self.assertRaises(TimeoutError):
            self.ui.find_unique(123,None,'AXTextArea',None,None,.01,lambda:None,
                                identifier='target',selector_only=True)

    def test_changed_target_during_hydration_stops_before_action(self):
        self.fields['field']['AXIdentifier']='target'
        original=native_ui.describe
        def changed(element,capabilities=False):
            self.fields['field']['AXIdentifier']='changed'
            return original(element,capabilities)
        with patch.object(native_ui,'describe',side_effect=changed):
            with self.assertRaisesRegex(RuntimeError,'changed during resolution'):
                self.ui.find_unique(123,None,'AXTextArea',None,None,.03,lambda:None,
                                    identifier='target',selector_only=True)

    def test_per_attribute_failure_cannot_establish_complete_uniqueness(self):
        self.fields['field']['AXIdentifier']='target'
        self.fields['field']['AXChildren']=native_ui.AX.AXValueCreate(
            native_ui.AX.kAXValueAXErrorType,native_ui.AX.kAXErrorCannotComplete)
        with self.assertRaises(TimeoutError):
            self.ui.find_unique(123,None,'AXTextArea',None,None,.01,lambda:None,
                                identifier='target',selector_only=True)

    def test_unsupported_leaf_children_does_not_hide_a_query_failure(self):
        self.fields['field']['AXIdentifier']='target'
        self.fields['field']['AXChildren']=native_ui.AX.AXValueCreate(
            native_ui.AX.kAXValueAXErrorType,native_ui.AX.kAXErrorAttributeUnsupported)
        _,_,node=self.ui.find_unique(123,None,'AXTextArea',None,None,.03,lambda:None,
                                    identifier='target',selector_only=True)
        self.assertEqual(node['identifier'],'target')

    def test_native_press_restores_query_timeout_without_replaying_error(self):
        node={'role':'AXButton','name':'Save'}
        self.ui.resolve=lambda refs,key:('field',node)
        with patch.object(native_ui.AX,'AXUIElementCopyActionNames',return_value=(0,['AXPress'])), \
             patch.object(native_ui.AX,'AXUIElementSetMessagingTimeout') as timeout, \
             patch.object(native_ui.AX,'AXUIElementPerformAction',return_value=-25204) as perform:
            with self.assertRaisesRegex(RuntimeError,'AX error -25204'):
                self.ui.press({},'field',timeout=.2)
        self.assertEqual([call.args[1] for call in timeout.call_args_list],[.2,.08])
        perform.assert_called_once_with('field','AXPress')

    def test_expired_native_press_deadline_sends_no_input(self):
        self.ui.resolve=lambda refs,key:('field',{'role':'AXButton'})
        with patch.object(native_ui.AX,'AXUIElementCopyActionNames',return_value=(0,['AXPress'])), \
             patch.object(native_ui.AX,'AXUIElementPerformAction') as perform:
            with self.assertRaisesRegex(TimeoutError,'no input'):
                self.ui.press({},'field',timeout=0)
        perform.assert_not_called()

    def test_duplicate_full_values_remain_ambiguous(self):
        self.fields['root'] = {'AXRole': 'AXGroup', 'AXChildren': ['field', 'other']}
        self.fields['other'] = dict(self.fields['field'])
        self.ui.window = lambda *args: 'root'
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            self.wait('READY_AT_END')

    def test_secure_fields_are_never_value_matched(self):
        self.fields['field']['AXSubrole'] = 'AXSecureTextField'
        with self.assertRaises(TimeoutError):
            self.wait('READY_AT_END')
        with self.assertRaises(TimeoutError):
            self.wait('<redacted>')

    def test_field_becoming_secure_after_snapshot_is_not_read(self):
        original = self.ui.snapshot
        def snapshot(*args, **kwargs):
            result = original(*args, **kwargs)
            self.fields['field']['AXSubrole'] = 'AXSecureTextField'
            return result
        self.ui.snapshot = snapshot
        with patch.object(native_ui, 'get_attr', wraps=native_ui.get_attr) as reader:
            with self.assertRaises(TimeoutError):
                self.wait('READY_AT_END')
            self.assertFalse(any(call.args[1] == 'AXValue' for call in reader.call_args_list))

    def test_incomplete_tree_does_not_establish_unique_match(self):
        original = self.ui.snapshot
        def snapshot(*args, **kwargs):
            data, references = original(*args, **kwargs)
            data['truncated'] = True
            return data, references
        self.ui.snapshot = snapshot
        with self.assertRaises(TimeoutError):
            self.wait('READY_AT_END')

    def test_slow_value_reads_do_not_extend_wait_across_the_whole_tree(self):
        self.fields['root'] = {'AXRole':'AXGroup', 'AXChildren':['field'] + [f'other{i}' for i in range(10)]}
        for i in range(10):
            self.fields[f'other{i}'] = dict(self.fields['field'])
        self.ui.window = lambda *args: 'root'
        original = native_ui.get_attr
        def slow_read(element, attribute):
            if attribute == 'AXValue':
                time.sleep(.02)
            return original(element, attribute)
        with patch.object(native_ui, 'get_attr', side_effect=slow_read) as reader:
            with self.assertRaises(TimeoutError):
                self.wait('MISSING')
        reads = [call for call in reader.call_args_list if call.args[1]=='AXValue']
        self.assertLess(len(reads), len(self.fields)-1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
