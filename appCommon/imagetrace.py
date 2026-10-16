"""Raster -> SVG tracing without a browser.

The tracing mode of the Image import plugin used the `svgtrace` package, which drives headless Chromium through
playwright (a Node.js driver and a Chromium download of about 300 MB on first use) only to run imagetracer.js.
imagetracer.js (by Andras Jankovics, public domain / Unlicense, see the header of the file) also has a data entry point,
ImageTracer.imagedataToSVG(), that needs no DOM, so it runs here in an embedded QuickJS engine (the `quickjs-ng`
package, or "quickjs" on Intel Macs): the pixels are loaded with Pillow and handed to the library.
"""
import base64
import json
import os
import re

from PIL import Image

try:
    import quickjs
except ImportError as err:
    # quickjs-ng has wheels for Windows, Linux and macOS arm64 only (Intel Macs use the older "quickjs" package, see
    # requirements.txt). The rest of the program must still start without it, see trace()
    quickjs = None
    _QUICKJS_IMPORT_ERROR = err

_JS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'imagetracer.js')

# QuickJS has no atob(): decode the base64 pixel data in JavaScript
_B64_JS = """
function __b64(s) {
    var T = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/', L = {}, i;
    for (i = 0; i < 64; i++) L[T.charAt(i)] = i;
    var n = s.length, pad = (s.charAt(n - 1) === '=') + (s.charAt(n - 2) === '='), out = new Uint8ClampedArray(n / 4 * 3 - pad), o = 0;
    for (i = 0; i < n; i += 4) {
        var v = (L[s.charAt(i)] << 18) | (L[s.charAt(i + 1)] << 12) | ((L[s.charAt(i + 2)] || 0) << 6) | (L[s.charAt(i + 3)] || 0);
        if (o < out.length) out[o++] = (v >> 16) & 255;
        if (o < out.length) out[o++] = (v >> 8) & 255;
        if (o < out.length) out[o++] = v & 255;
    }
    return out;
}
"""


def options_to_js(mode):
    """A preset name becomes a JavaScript string; an '{ key:value, ... }' option object (as built by
    ToolImage.get_tracing_options) is passed through, with Python's True/False turned into true/false."""
    mode = str(mode).strip()
    if mode.startswith('{'):
        mode = re.sub(r'\bTrue\b', 'true', mode)
        mode = re.sub(r'\bFalse\b', 'false', mode)
        return '(' + mode + ')'
    return '"%s"' % mode.replace('\\', '\\\\').replace('"', '\\"')


def trace(filename, blackAndWhite=False, mode="default"):
    """Trace an image file and return the SVG as a string (same signature as svgtrace.trace()).

    :param filename:        path of the image
    :param blackAndWhite:   trace with the 'posterized1' preset
    :param mode:            an imagetracer.js preset name or an option object written as a string
    """
    if quickjs is None:
        raise ImportError("Image tracing needs the 'quickjs-ng' package (the 'quickjs' package on Intel macOS), which "
                          "could not be imported (%s). Install it with: pip install -r requirements.txt"
                          % _QUICKJS_IMPORT_ERROR)
    if str(mode).find("black") >= 0 or blackAndWhite:
        mode = "posterized1"
    if not os.path.exists(filename):
        raise FileNotFoundError(f"{filename} does not exist!")

    img = Image.open(filename).convert('RGBA')
    width, height = img.size
    ctx = quickjs.Context()
    ctx.set_time_limit(600)
    ctx.set_memory_limit(2 * 1024 ** 3)
    ctx.eval("var self = this; var window = this;")
    with open(_JS_PATH, encoding='utf-8') as js_file:
        ctx.eval(js_file.read())
    ctx.eval(_B64_JS)
    ctx.set("__pixels", base64.b64encode(img.tobytes()).decode('ascii'))
    # The SVG is built by string concatenation and quickjs-ng hands such a result (a "rope") to Python as a value its
    # binding cannot convert (TypeError: Unknown quickjs tag: -6); JSON.stringify() makes a flat string
    svg = ctx.eval("JSON.stringify(ImageTracer.imagedataToSVG({width: %d, height: %d, data: __b64(__pixels)}, %s))"
                   % (width, height, options_to_js(mode)))
    return json.loads(svg)
