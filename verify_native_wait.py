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
