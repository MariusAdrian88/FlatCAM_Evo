"""
The canvas of the headless mode accepts every call of the application and draws nothing. A number taken from it
raises TypeError, so canvas data can never reach a calculation (G-code) unnoticed.
"""

from appGUI.NullCanvas import NullCanvas, NullObject


def test_any_attribute_and_any_call_is_accepted():
    canvas = NullCanvas(app=None)
    canvas.view.camera.zoom(2, (0, 0))
    canvas.graph_event_connect('mouse_move', print)
    canvas.native.setFocus()
    canvas.some_attribute = 5            # ignored
    assert isinstance(canvas.view.scene, NullObject)

