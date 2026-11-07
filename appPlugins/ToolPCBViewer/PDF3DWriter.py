"""Single-page PDF 1.7 wrapper for static U3D scenes and lossless RGB posters."""

import itertools
import zlib

import numpy as np


def _number(value):
    # PDF real numbers don't support exponent notation.
    return np.format_float_positional(float(value), unique=True, trim='-')


def _array(values):
    return '[' + ' '.join(_number(value) for value in values) + ']'


def _stream(entries, data):
    return ('<< %s /Length %d >>\nstream\n' % (entries, len(data))).encode('ascii') + data + b'\nendstream'


def export_frame(bounds, page_size=None):
    """Shared ISO orthographic frame with five percent total projected padding."""
    bounds = np.asarray(bounds, dtype=float)
    if (bounds.shape != (2, 3) or not np.isfinite(bounds).all()
            or np.any(bounds[0] > bounds[1]) or not np.any(bounds[0] < bounds[1])):
        raise ValueError('bounds must be finite ordered minimum/maximum XYZ triples with nonzero extent')
    # Camera_3D ISO quaternion, including Arcball's (x,z,y) axis mapping.
    # PDF 13.6.5 uses +Z into the page, opposite VisPy's camera backward vector.
    right = np.array([1, 1, 0]) / np.sqrt(2)
    up = np.array([-1, 1, 2]) / np.sqrt(6)
    forward = np.array([-1, 1, -1]) / np.sqrt(3)
    rotation = np.column_stack((right, up, forward))
    center = bounds[0] / 2 + bounds[1] / 2
    corners = np.array(list(itertools.product(*zip(*bounds))))
    projected = (corners - center) @ rotation
    extent = np.ptp(projected, axis=0)
    if page_size is None:
        size = extent[:2] / max(extent[:2]) * (297 * 72 / 25.4)
    else:
        size = np.asarray(page_size, dtype=float)
    if size.shape != (2,) or not np.isfinite(size).all() or np.any(size <= 0):
        raise ValueError('page_size must be two finite positive dimensions')
    distance = max(float(np.linalg.norm(bounds[1] - bounds[0])), 1.0) * 2
    eye = center - distance * forward
    scale = min(size / np.maximum(extent[:2], np.finfo(float).tiny)) / 1.05
    if not np.isfinite(np.r_[eye, distance, scale]).all() or scale <= 0:
        raise ValueError('bounds/page_size exceed the supported camera range')
    return dict(bounds=bounds, center=center, rotation=rotation, page_size=size,
                scale=scale, distance=distance, eye=eye)


def write(path, u3d, bounds, page_size=None, poster=None):
    """Build a complete PDF before opening the destination; atomic saving belongs to the caller."""
    frame = export_frame(bounds, page_size)
    if not isinstance(u3d, bytes) or not u3d.startswith(b'U3D\x00'):
        raise ValueError('u3d must be a complete U3D byte stream')
    if poster is not None and (not isinstance(poster, np.ndarray) or poster.dtype != np.uint8
                               or poster.ndim != 3 or poster.shape[2] != 3
                               or not poster.shape[0] or not poster.shape[1]):
        raise ValueError('poster must be a nonempty HxWx3 uint8 RGB image')
    size = frame['page_size']
    matrix = np.concatenate((frame['rotation'].T.ravel(), frame['eye']))
    rectangle = _array((0, 0, *size))
    view = ('<< /Type /3DView /XN (ISO) /IN (ISO) /MS /M /C2W %s /CO %s '
            '/P << /Subtype /O /CS /ANF /OS %s /OB /Absolute >> '
            '/LS << /Type /3DLightingScheme /Subtype /Headlamp >> '
            '/BG << /Type /3DBG /CS /DeviceRGB /C [0.75 0.75 0.75] >> >>'
            % (_array(matrix), _number(frame['distance']), _number(frame['scale']))).encode('ascii')
    if poster is None:
        appearance = ('q 0.75 g 0 0 %s %s re f Q\n' % tuple(_number(value) for value in size)).encode('ascii')
        resources = '<< >>'
    else:
        appearance = ('q %s 0 0 %s 0 0 cm /Image Do Q\n' % tuple(_number(value) for value in size)).encode('ascii')
        resources = '<< /XObject << /Image 9 0 R >> >>'
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        ('<< /Type /Page /Parent 2 0 R /MediaBox %s '
         '/Resources << /XObject << /Poster 7 0 R >> >> /Contents 8 0 R /Annots [4 0 R] >>'
         % rectangle).encode('ascii'),
        ('<< /Type /Annot /Subtype /3D /Rect %s /P 3 0 R /F 4 /Border [0 0 0] '
         '/3DD 5 0 R /3DV 6 0 R /3DA << /A /PO /AIS /I /D /PC /DIS /U >> '
         '/AP << /N 7 0 R >> /Contents (Static 3D scene) >>' % rectangle).encode('ascii'),
        _stream('/Type /3D /Subtype /U3D /Filter /FlateDecode /VA [6 0 R] /DV 6 0 R', zlib.compress(u3d)),
        view,
        _stream('/Type /XObject /Subtype /Form /FormType 1 /BBox %s /Resources %s'
                % (rectangle, resources), appearance),
        _stream('', b'q /Poster Do Q\n'),
    ]
    if poster is not None:
        objects.append(_stream('/Type /XObject /Subtype /Image /Width %d /Height %d '
                               '/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode'
                               % (poster.shape[1], poster.shape[0]), zlib.compress(poster.tobytes())))
    data = bytearray(b'%PDF-1.7\n%\xe2\xe3\xcf\xd3\n')
    offsets = []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(('%d 0 obj\n' % number).encode('ascii') + obj + b'\nendobj\n')
    xref = len(data)
    data.extend(('xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)).encode('ascii'))
    for offset in offsets:
        data.extend(('%010d 00000 n \n' % offset).encode('ascii'))
    data.extend(('trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n'
                 % (len(objects) + 1, xref)).encode('ascii'))
    with open(path, 'wb') as output:
        output.write(data)
