"""
Per-import settings and visibility rules shared by all converters.
"""
import logging

import gettext
import builtins

from ezdxf import units as ezunits
from shapely import affinity

if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')

TEXT_MODES = ('stroke', 'outline', 'none')
# Maximum distance between a curve and its flattened version, in millimetres.
TOLERANCE_MM = 0.01
# Drawing unit code of millimetres in $INSUNITS.
UNIT_MM = 4
# Drawing unit code of inches in $INSUNITS.
UNIT_INCH = 1


def unit_tolerance(doc):
    """
    :param doc:     ezdxf Document (or None)
    :return:        0.01 mm expressed in the drawing units of the document ($INSUNITS);
                    unitless or unknown units are taken as millimetres
    """
    try:
        code = int(doc.header.get('$INSUNITS', 0))
        if code in (0, UNIT_MM):
            return TOLERANCE_MM
        factor = ezunits.conversion_factor(UNIT_MM, code)
        return TOLERANCE_MM * factor if factor and factor > 0 else TOLERANCE_MM
    except Exception as err:
        log.debug("DXF $INSUNITS not usable, assuming mm: %s" % str(err))
        return TOLERANCE_MM


def unit_scale(doc, app_units):
    """
    :param doc:         ezdxf Document (or None)
    :param app_units:   application units, 'MM' or 'IN'
    :return:            (factor, name): the factor that converts the drawing units of the document ($INSUNITS)
                        to the application units, and the name of the drawing units; unitless, absent or unknown
                        units give factor 1 and a warning in the log
    """
    mm_app = str(app_units).upper() == 'MM'
    try:
        code = int(doc.header.get('$INSUNITS', 0))
        name = ezunits.unit_name(code) if code else ''
        factor = ezunits.conversion_factor(code, UNIT_MM if mm_app else UNIT_INCH) if name else None
    except Exception as err:
        log.debug("DXF $INSUNITS not usable: %s" % str(err))
        name, factor = '', None
    if not factor or factor <= 0:
        log.warning("DXF file declares no units, taken as application units.")
        return 1.0, ''
    # the ezdxf table is not exact (inch to mm gives 25.4000000001): 10 significant digits remove the noise
    return float('%.10g' % factor), name


def scale_to_app_units(geos, doc, app_units, app=None):
    """
    Scales imported geometry from the drawing units of the document to the application units.

    :param geos:        flat list of Shapely geometries in drawing units
    :param doc:         ezdxf Document
    :param app_units:   application units, 'MM' or 'IN'
    :param app:         the App, if any, to report the scale used in the status bar
    :return:            the list scaled about (0, 0); the same list when the factor is 1
    """
    factor, name = unit_scale(doc, app_units)
    if factor == 1.0 or not geos:
        return geos

    def report(text):
        if app is not None and hasattr(app, 'inform'):
            app.inform.emit('[WARNING_NOTCL] %s' % text)
        else:
            log.warning(text)

    # a scaled drawing over 1 m while the unscaled one fits in 1 m: the declared units are most likely wrong
    limit = 1000.0 if str(app_units).upper() == 'MM' else 1000.0 / 25.4
    bounds = [g.bounds for g in geos if not g.is_empty]
    if not bounds:
        return geos
    minx, miny = min(b[0] for b in bounds), min(b[1] for b in bounds)
    maxx, maxy = max(b[2] for b in bounds), max(b[3] for b in bounds)
    width, height = maxx - minx, maxy - miny
    size = max(width, height)
    # the message tells the user the sizes and the Transformation scale that reverses the choice
    fmt = '%.6g'
    app_name = str(app_units).lower()
    if factor > 1 and size <= limit < size * factor:
        report(_("DXF drawing units '{units}' ignored: scaled x{factor} the drawing would be {sw} x {sh} {app}, "
                 "larger than 1 m. It is imported unscaled: {w} x {h} {app}. If '{units}' is right, scale the "
                 "object by {factor} in the Transformation plugin (reference: Origin).").format(
            units=name, factor=fmt % factor, sw=fmt % (width * factor), sh=fmt % (height * factor),
            w=fmt % width, h=fmt % height, app=app_name))
        return geos

    report(_("DXF drawing units '{units}': scaled x{factor} to {w} x {h} {app}. If the file was drawn in {app}, "
             "scale the object by {inverse} in the Transformation plugin (reference: Origin).").format(
        units=name, factor=fmt % factor, w=fmt % (width * factor), h=fmt % (height * factor),
        app=app_name, inverse=fmt % (1.0 / factor)))
    return [affinity.scale(g, xfact=factor, yfact=factor, origin=(0, 0)) for g in geos]


class ImportContext:
    """
    Settings of one dxf_to_shapes() call; passed down to every converter (no global state).
    """

    def __init__(self, doc=None, text_mode='stroke', tolerance=None, options=None, kind='geometry'):
        """
        :param doc:         ezdxf Document, used for the layer table and the units
        :param text_mode:   'stroke', 'outline' or 'none'; anything else falls back to 'stroke'
        :param tolerance:   flattening tolerance in drawing units; None = derived from $INSUNITS
        :param options:     the application options for the circle steps; None = the factory defaults
        :param kind:        'gerber' or 'geometry': whose circle preferences set the steps of the circles
        """
        if text_mode not in TEXT_MODES:
            log.warning("DXF import: unknown text mode %r, using 'stroke'." % (text_mode,))
            text_mode = 'stroke'
        self.doc = doc
        self.text_mode = text_mode
        self.tolerance = unit_tolerance(doc) if tolerance is None else tolerance
        self.options = options
        self.kind = kind

    def circle_steps(self, radius):
        """
        :param radius:  radius of a circle in drawing units
        :return:        segments of the full circle by the circle preferences of the object kind; the drawing units
                        come from the flattening tolerance (0.01 mm in drawing units)
        """
        from camlib import object_circle_steps
        mm_per_unit = TOLERANCE_MM / self.tolerance if self.tolerance > 0 else 1.0
        return object_circle_steps(self.options, self.kind, abs(radius) * mm_per_unit, 'MM')

    def layer_of(self, entity):
        """
        :param entity:  ezdxf entity
        :return:        layer name; entities of a block on layer '0' take the layer of their INSERT
        """
        layer = entity.dxf.get('layer', '0')
        if layer == '0':
            source = getattr(entity, 'source_block_reference', None)
            if source is not None and source is not entity:
                return self.layer_of(source)
        return layer

    def layer_hidden(self, entity):
        """
        :param entity:  ezdxf entity
        :return:        True when its layer is switched off or frozen
        """
        if self.doc is None:
            return False
        name = self.layer_of(entity)
        if name not in self.doc.layers:
            return False
        layer = self.doc.layers.get(name)
        return bool(layer.is_off() or layer.is_frozen())

    def is_visible(self, entity):
        """
        :param entity:  ezdxf entity
        :return:        False for invisible entities (also invisible ATTRIBs) and for entities
                        on an off or frozen layer
        """
        if entity.dxf.get('invisible', 0) == 1:
            return False
        if entity.dxftype() == 'ATTRIB' and getattr(entity, 'is_invisible', False):
            return False
        return not self.layer_hidden(entity)


# A CIRCLE copied out of a block with non-uniform scale comes back as an ELLIPSE without any
# trace of its origin. blocks.py tags such copies with this (otherwise unused) LTSCALE value.
CIRCLE_MARK = 0.0123456789


def is_circle_ellipse(entity):
    """
    :param entity:  ezdxf entity
    :return:        True for an ELLIPSE that blocks.py made out of a CIRCLE
    """
    return (entity.dxftype() == 'ELLIPSE'
            and abs(entity.dxf.get('ltscale', 1.0) - CIRCLE_MARK) < 1e-12)
