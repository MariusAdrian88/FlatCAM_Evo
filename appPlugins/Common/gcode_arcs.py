"""Pure shared arc splitting for the Sender and Photo Carve simulation."""

from appPlugins.Common.levelling_interp import new_levelling_state, level_gcode_line


def split_arcs(lines, units, arc_segment, decimals=4):
    """
    Split the G2/G3 arcs into G1 lines without a height change: level_gcode_line()
    with a 0 offset splits the arcs and keeps every Z. The modal state of
    level_gcode_line() finds also a bare modal arc line (X20 Y0 I5 J0 after G2).

    :param lines: list of str (clean lines).
    :param units: 'MM' or 'IN'.
    :param arc_segment: the longest chord of 1 piece of a split arc, in `units`.
    :param decimals: decimals of the new words.
    :return: (new_lines, count of split arcs, source_index, unsplit).
        source_index[i] is the index in `lines` of the source line of new_lines[i].
        unsplit is the list of the indexes of the arc lines that could not be
        split (plane, frame or position not known); they stay in new_lines as they are.
    """
    state = new_levelling_state(arc_segment=arc_segment, units=units)
    out = []
    source_index = []
    unsplit = []
    for index, line in enumerate(lines):
        split_before = state['arcs_split']
        arcs_before = state['arcs']
        text = level_gcode_line(line, state, lambda x, y: 0.0, decimals)
        if state['arcs_split'] > split_before:
            pieces = text.split('\n')
            out.extend(pieces)
            source_index.extend([index] * len(pieces))
            continue
        if state['arcs'] > arcs_before:
            unsplit.append(index)
        out.append(line)
        source_index.append(index)
    return out, state['arcs_split'], source_index, unsplit
