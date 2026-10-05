"""Offline foreground guard regressions; no desktop capture or input."""
import copy
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

import desktop


class ForegroundGuardTests(unittest.TestCase):
    def setUp(self):
        self.d=desktop.Desktop.__new__(desktop.Desktop)
        self.bounds={'x':1.0,'y':2.0,'width':100.0,'height':200.0}
        self.d.frame={'active_space_id':1,'target_pid':123,'window_id':191,
                      'viewport':self.bounds,'config':{'app_pid':123}}
        self.d.expected_pid=123
        self.d.spaces=SimpleNamespace(available=True,active_id=Mock(return_value=1))
        self.d.ui=Mock()
        self.window={'kCGWindowNumber':191,'kCGWindowLayer':0,'kCGWindowIsOnscreen':True,
                     'kCGWindowOwnerPID':123,'kCGWindowBounds':{'X':1,'Y':2,'Width':100,'Height':200}}
        self.d._window_metadata=Mock(side_effect=lambda *args:[self.window])
        mocked=patch.object(desktop,'front_app',return_value={'pid':123})
        self.front=mocked.start()
        self.addCleanup(mocked.stop)

    def test_fresh_target_metadata_is_read_for_every_guard(self):
        self.d.check_focus()
        self.d.check_focus()
        self.assertEqual(self.d._window_metadata.call_count,2)
        self.d._window_metadata.assert_called_with(int(desktop.Q.kCGWindowListOptionIncludingWindow)|16,191)
        self.d.ui.check_window_focus.assert_called_with(123,self.bounds,191)

    def test_moved_or_resized_window_stops_input(self):
        for attribute in ('X','Y','Width','Height'):
            with self.subTest(attribute=attribute):
                bounds=copy.copy(self.window['kCGWindowBounds'])
                self.window['kCGWindowBounds'][attribute]+=10
                with self.assertRaisesRegex(RuntimeError,'moved, resized'):
                    self.d.check_focus()
                self.window['kCGWindowBounds']=bounds

    def test_hidden_or_off_space_window_stops_input(self):
        self.window['kCGWindowIsOnscreen']=False
        with self.assertRaisesRegex(RuntimeError,'disappeared'):
            self.d.check_focus()

    def test_disappeared_or_ambiguous_window_stops_input(self):
        for items in ([],[self.window,self.window]):
            with self.subTest(items=items):
                self.d._window_metadata=Mock(return_value=items)
                with self.assertRaisesRegex(RuntimeError,'disappeared'):
                    self.d.check_focus()

    def test_changed_owner_stops_input(self):
        self.window['kCGWindowOwnerPID']=124
        with self.assertRaises(RuntimeError):
            self.d.check_focus()

    def test_foreground_takeover_and_unknown_foreground_stop_before_metadata(self):
        for front in ({'pid':124},None):
            with self.subTest(front=front):
                self.front.return_value=front
                with self.assertRaisesRegex(RuntimeError,'Foreground app changed'):
                    self.d.check_focus()
        self.d._window_metadata.assert_not_called()

    def test_space_change_stops_before_metadata(self):
        self.d.spaces.active_id.return_value=2
        with self.assertRaisesRegex(RuntimeError,'Space changed'):
            self.d.check_focus()
        self.d._window_metadata.assert_not_called()

    def test_another_window_of_same_app_still_stops_input(self):
        self.d.ui.check_window_focus.side_effect=RuntimeError('Selected window is not the focused window')
        with self.assertRaisesRegex(RuntimeError,'not the focused window'):
            self.d.check_focus()
        self.d._window_metadata.assert_not_called()


if __name__=='__main__':
    unittest.main(verbosity=2)
