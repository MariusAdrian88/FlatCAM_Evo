"""Raster polygonization and image geometry transforms. No Qt here."""

import numpy as np
from itertools import islice
from rasterio import open as rasterio_open
from rasterio.features import shapes
from rasterio.transform import Affine
from shapely import transform, from_ragged_array, GeometryType
from PIL import ImageOps


def scale_geometry(geometry, units='MM', dpi=96):
    """Scale pixel coordinates to application units without changing the source."""
    scale_factor = 25.4 / dpi if units.lower() == 'mm' else 1 / dpi
    return list(transform(geometry, lambda coordinates: coordinates * scale_factor))


def adjust_image(image, invert=False, mirror_x=False, mirror_y=False):
    """Photo Carve orientation: X is left/right, Y is top/bottom; invert before interpretation."""
    if invert:
        rgba = image.convert('RGBA')
        image = ImageOps.invert(rgba.convert('RGB'))
        image.putalpha(rgba.getchannel('A'))
    if mirror_x:
        image = ImageOps.mirror(image)
    if mirror_y:
        image = ImageOps.flip(image)
    return image


def raster_values(red, green, blue, mode, mask):
    if mode == 'black':
        return red, red <= mask[0]
    total = np.zeros(red.shape, dtype=np.float32)
    for band in red, green, blue:
        total += band
    return total / 3, (red <= mask[1]) | (green <= mask[2]) | (blue <= mask[3])


def import_raster(filename, flip=True, units='MM', dpi=96, mode='black', mask=None, raster_opener=None,
                  invert=False, mirror_x=False, mirror_y=False):
    """Read image bands, apply the masks, and polygonize in application units."""
    if mask is None:
        mask = [128, 128, 128, 128]

    if raster_opener is None:
        raster_opener = rasterio_open
    with raster_opener(filename) as src:
        red = green = blue = src.read(1)
        try:
            green = src.read(2)
        except Exception:
            pass
        try:
            blue = src.read(3)
        except Exception:
            pass

    if invert:
        red, green, blue = (255 - band for band in (red, green, blue))
    total, mask_setting = raster_values(red, green, blue, mode, mask)
    if mirror_x:
        total, mask_setting = total[:, ::-1], mask_setting[:, ::-1]
    if mirror_y:
        total, mask_setting = total[::-1], mask_setting[::-1]
    factor = 25.4 / dpi if units.lower() == 'mm' else 1 / dpi
    # Transform once in GDAL, not four Python/Shapely transforms per color region.
    return raster_geometries(total, mask_setting, factor, flip)


def raster_geometries(total, mask, factor=1.0, flip=True):
    affine = Affine.scale(factor, -factor if flip else factor)
    regions = shapes(total, mask=mask, transform=affine)
    result = []
    # Batch GEOS construction without merging tone regions or rounding pixel edges.
    while batch := list(islice(regions, 4096)):
        coordinates, ring_offsets, polygon_offsets = [], [0], [0]
        for geometry, value in batch:
            for ring in geometry['coordinates']:
                coordinates.extend(ring)
                ring_offsets.append(len(coordinates))
            polygon_offsets.append(len(ring_offsets) - 1)
        result.extend(from_ragged_array(GeometryType.POLYGON, np.asarray(coordinates),
                                        (np.asarray(ring_offsets), np.asarray(polygon_offsets))))
    return result


def flatten_list(obj_list):
    """Yield the leaves of nested image geometry lists."""
    for item in obj_list:
        if hasattr(item, '__iter__') and not isinstance(item, (str, bytes)):
            yield from flatten_list(item)
        else:
            yield item
