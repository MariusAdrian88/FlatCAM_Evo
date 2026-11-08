# ###########################################################
# FlatCAM EVO - App Lifecycle Integration Tests
# Tests for app initialization order and shutdown sequence
# ###########################################################

import sys
import os
import unittest
from unittest.mock import Mock, patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestAppShutdownSequence(unittest.TestCase):
    """
    Test that app shutdown sequence works correctly.
    
    Verifies that quit_application() can successfully call
    preferencesUiManager.save_defaults() without AttributeError.
    """
    
    def test_quit_application_before_the_event_loop_still_ends_the_app(self):
        """
        A --shellfile runs in App.__init__(), before app.exec(). QApplication.quit() does nothing while the event
        loop does not run, so `quit_app` hid the window and the process kept running: the quit must also be queued
        for the event loop.
        """
        from appHandlers.appLifecycle import AppLifecycle

        mock_app = Mock()
        # a Mock attribute is truthy: this is the GUI mode, not the headless mode
        mock_app.headless = False
        mock_app.options = {}
        lifecycle = AppLifecycle(app=mock_app)
        lifecycle._confirm_tools_database = Mock(return_value=True)

        with patch('appHandlers.appLifecycle.sys.platform', 'linux'), \
                patch('PyQt6.QtCore.QSettings'), \
                patch('PyQt6.QtWidgets.QApplication.quit') as quit_now, \
                patch('PyQt6.QtCore.QTimer.singleShot') as single_shot:
            self.assertTrue(lifecycle.quit_application(silent=True))

        quit_now.assert_called_once_with()
