"""Keep the legacy image plugin imports and monkeypatch targets available."""

from appPlugins.ToolImage.Image import ToolImage, QtWidgets  # noqa: F401
from appCommon.imagetrace import trace  # noqa: F401
from rasterio import open as rasterio_open  # noqa: F401
from appParsers.SVG import svg_to_shapes  # noqa: F401
from appPlugins.ToolImage.ImageUI import (  # noqa: F401
    ImageUI, FCLabel, FCButton, FCFrame, GLay, FCComboBox, FCComboBox2, RadioSet, FCDoubleSpinner, FCSpinner
)
