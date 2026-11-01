"""
Colors of the text editor (code mode). Each palette has its own colors: the dark one is not an inversion of the
light one.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class EditorPalette:
    # gutter
    gutter_bg: str
    gutter_fg: str
    gutter_current_fg: str
    # text area
    current_line_bg: str
    bracket_match_bg: str
    find_match_bg: str
    find_current_bg: str
    whitespace_fg: str
    fold_marker_fg: str
    # minimap
    minimap_bg: str
    minimap_fg: str
    minimap_viewport: str
    # tokens
    comment: str
    gcode_g: str
    gcode_m: str
    tool: str
    feed: str
    axis: str
    z_axis: str
    string: str
    keyword: str
    number: str
    delimiter: str
    param: str
    gerber_dcode: str
    gerber_extended: str
    attribute: str
    line_number: str
    tcl_command: str
    variable: str


LIGHT = EditorPalette(
    gutter_bg='#e6e6e6', gutter_fg='#8a8a8a', gutter_current_fg='#0000cc',
    current_line_bg='#f0f4ff', bracket_match_bg='#b4d7ff', find_match_bg='#ffe680', find_current_bg='#ffb347',
    whitespace_fg='#b0b0b0', fold_marker_fg='#707070',
    minimap_bg='#f4f4f4', minimap_fg='#909090', minimap_viewport='#4a90d9',
    comment='#808080', gcode_g='#0000ff', gcode_m='#ff7f50', tool='#0000ff', feed='#008080', axis='#228b22',
    z_axis='#800080', string='#ff6347', keyword='#ff0000', number='#008000', delimiter='#00008b', param='#00008b',
    gerber_dcode='#c71585', gerber_extended='#6a3d9a', attribute='#b8860b', line_number='#8a8a8a',
    tcl_command='#0000cd', variable='#b35900',
)

DARK = EditorPalette(
    gutter_bg='#2b2d30', gutter_fg='#6e7378', gutter_current_fg='#9cc2ff',
    current_line_bg='#2f3238', bracket_match_bg='#3d5a80', find_match_bg='#6b5a1e', find_current_bg='#a8741a',
    whitespace_fg='#4e5257', fold_marker_fg='#8a8f95',
    minimap_bg='#25272a', minimap_fg='#6e7378', minimap_viewport='#5b8fd0',
    comment='#7f8c8d', gcode_g='#6cb6ff', gcode_m='#ffa07a', tool='#8ab4f8', feed='#4ec9b0', axis='#7fd17f',
    z_axis='#d291e8', string='#f08a70', keyword='#ff6b6b', number='#89d185', delimiter='#9aa7d6', param='#8fa8d8',
    gerber_dcode='#e58acb', gerber_extended='#b39ddb', attribute='#e0b84d', line_number='#6e7378',
    tcl_command='#6cb6ff', variable='#f0a860',
)


def palette_for(theme, background=None):
    """
    The dark palette for the 'dark' theme, the light one for the other themes. A dark background (QColor) also takes
    the dark palette: the 'default' theme follows the Qt palette, which is dark on a system in dark mode.
    """
    if theme == 'dark' or (background is not None and background.lightness() < 128):
        return DARK
    return LIGHT
