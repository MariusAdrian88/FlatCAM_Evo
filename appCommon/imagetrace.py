# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# MIT Licence                                              #
# ##########################################################
"""
Raster -> SVG tracing for the Image import plugin, with the `vtracer` package (Rust, no browser, no SciPy).

The image is flattened on white first. In B/W mode it is also thresholded here, because the binary mode of vtracer
looks only at the red channel of a pixel: red artwork on white would come out empty.
"""

import io
import os
import re

from PIL import Image

try:
    import vtracer
except ImportError as err:
    # the rest of the program must still start without it, see trace()
    vtracer = None
    _VTRACER_IMPORT_ERROR = err

# Curve fitting modes, in the order of the UI combo box
CURVE_MODES = ['polygon', 'spline', 'none']

# Option sets. 'polygon' keeps the pixel edges as straight segments (no rounded corners, no bridged gaps), which is
# what PCB artwork needs; 'smooth' fits splines, for logos and drawings. A color precision below 8 bits merges the
# colors of a photograph into a few large regions (PSNR 13.8 dB against 27 dB at 8 bits).
PRESETS = {
    'default': {'mode': 'polygon', 'filter_speckle': 4, 'color_precision': 8, 'layer_difference': 16,
                'corner_threshold': 60, 'length_threshold': 4.0, 'splice_threshold': 45},
    'detailed': {'mode': 'polygon', 'filter_speckle': 1, 'color_precision': 8, 'layer_difference': 8,
                 'corner_threshold': 60, 'length_threshold': 4.0, 'splice_threshold': 45},
    'smooth': {'mode': 'spline', 'filter_speckle': 4, 'color_precision': 8, 'layer_difference': 16,
               'corner_threshold': 60, 'length_threshold': 4.0, 'splice_threshold': 45},
    'photo': {'mode': 'polygon', 'filter_speckle': 10, 'color_precision': 8, 'layer_difference': 48,
              'corner_threshold': 180, 'length_threshold': 4.0, 'splice_threshold': 45},
}
PRESET_NAMES = list(PRESETS)

MAX_ITERATIONS = 10
PATH_PRECISION = 3


def trace(filename, black_white=True, threshold=128, options=None):
    """
    Trace an image file and return the SVG as a string.

    :param filename:    path of the image
    :param black_white: True: trace the dark pixels only. False: trace every color region
    :param threshold:   B/W only: a pixel darker than this (0-255) is traced
    :param options:     a dict with the keys of a PRESETS entry; None means PRESETS['default']
    :return:            the SVG text
    """
    if not os.path.exists(filename):
        raise FileNotFoundError("%s does not exist!" % filename)
    with Image.open(filename) as img:
        return trace_image(img, black_white, threshold, options)


def trace_image(image, black_white=True, threshold=128, options=None):
    """
    Trace an image that is already in memory and return the SVG as a string.

    :param image:       a PIL.Image.Image
    :param black_white: True: trace the dark pixels only. False: trace every color region
    :param threshold:   B/W only: a pixel darker than this (0-255) is traced
    :param options:     a dict with the keys of a PRESETS entry; None means PRESETS['default']
    :return:            the SVG text
    """
    if vtracer is None:
        raise ImportError("Image tracing needs the 'vtracer' package, which could not be imported (%s). "
                          "Install it with: pip install vtracer" % _VTRACER_IMPORT_ERROR)

    opt = dict(PRESETS['default'])
    if options:
        opt.update(options)

    img = image.convert('RGBA')
    flat = Image.new('RGBA', img.size, (255, 255, 255, 255))
    flat.alpha_composite(img)
    if black_white:
        gray = flat.convert('L')
        flat = gray.point(lambda v: 0 if v < threshold else 255).convert('RGB')
    else:
        flat = flat.convert('RGB')

    buf = io.BytesIO()
    flat.save(buf, 'PNG')

    # Positional arguments only: vtracer 0.6.15 segfaults on CPython 3.14 when it gets keyword arguments.
    # 'cutout' makes color regions that do not overlap, so each one becomes its own shape on import
    try:
        svg = vtracer.convert_raw_image_to_svg(
            buf.getvalue(), 'png', 'binary' if black_white else 'color', 'cutout', opt['mode'],
            int(opt['filter_speckle']), int(opt['color_precision']), int(opt['layer_difference']),
            int(opt['corner_threshold']), float(opt['length_threshold']), MAX_ITERATIONS,
            int(opt['splice_threshold']), PATH_PRECISION)
    except BaseException as err:
        # PyO3 panics inherit BaseException, not Exception; let workers report them and release their progress UI.
        if type(err).__name__ != 'PanicException':
            raise
        raise ValueError('Image tracing failed: %s' % str(err)) from err
    # lxml rejects a str that has an encoding declaration (ToolImage parses the text with ET.fromstring())
    return re.sub(r'^\s*<\?xml[^>]*\?>\s*', '', svg)
