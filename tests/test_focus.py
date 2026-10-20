import unittest
from unittest.mock import Mock, patch

from visor.platforms import Windows, WindowUnavailable


class FocusCase(unittest.TestCase):
    def setUp(self):
        self.adapter = object.__new__(Windows)
        self.adapter.api = Mock()
        self.adapter.api.IsIconic.return_value = False
        self.adapter.find = Mock(return_value=123)

    def test_normal_or_maximized_window_is_not_restored(self):
        self.adapter.focus("token")
        self.adapter.api.ShowWindow.assert_not_called()
        self.adapter.api.SetForegroundWindow.assert_called_once_with(123)

    def test_minimized_window_is_restored(self):
        self.adapter.api.IsIconic.return_value = True
        self.adapter.focus("token")
        self.adapter.api.ShowWindow.assert_called_once_with(123, 9)

    def test_changed_identity_is_rejected_before_activation(self):
        with patch("visor.win_identity.window_identity", return_value={"hwnd": 456}), self.assertRaises(WindowUnavailable):
            self.adapter.focus("token", expected_identity={"hwnd": 123})
        self.adapter.api.SetForegroundWindow.assert_not_called()

    def test_foreground_denial_is_reported(self):
        self.adapter.api.SetForegroundWindow.return_value = False
        self.adapter.api.GetForegroundWindow.return_value = 456
        with self.assertRaises(WindowUnavailable):
            self.adapter.focus("token")

    def test_already_foreground_is_success(self):
        self.adapter.api.SetForegroundWindow.return_value = False
        self.adapter.api.GetForegroundWindow.return_value = 123
        self.adapter.focus("token")
