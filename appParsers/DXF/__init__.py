"""
Clean DXF importer: ezdxf document -> Shapely geometries.
"""
from .reader import dxf_to_shapes
from .text import text_entity_to_shapes, TEXT_RENDERING_AVAILABLE
from .context import unit_scale, scale_to_app_units

__all__ = ['dxf_to_shapes', 'unit_scale', 'scale_to_app_units','text_entity_to_shapes', 'TEXT_RENDERING_AVAILABLE']
