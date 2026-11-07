"""Static U3D scenes with material opacity: ECMA-363 third edition, no-compression profile.

Only triangle base meshes, one material per model, and two lights are emitted.
No arithmetic codec, SDK code, progressive geometry, textures, or UI dependencies.
"""

import struct

import numpy as np


class BlockWriter:
    """Little-endian primitive payload builder; methods can be chained."""

    def __init__(self):
        self.data = bytearray()

    def u8(self, value):
        self.data.extend(struct.pack('<B', value))
        return self

    def u16(self, value):
        self.data.extend(struct.pack('<H', value))
        return self

    def u32(self, value):
        self.data.extend(struct.pack('<I', value))
        return self

    def i32(self, value):
        self.data.extend(struct.pack('<i', value))
        return self

    def f32(self, value):
        self.data.extend(struct.pack('<f', value))
        return self

    def u64(self, value):
        self.data.extend(struct.pack('<Q', value))
        return self

    def string(self, value):
        encoded = value.encode('utf-8')
        self.u16(len(encoded))
        self.data.extend(encoded)
        return self


def block(kind, data, metadata=b''):
    """Frame a block; sizes exclude the independently padded payload/metadata."""
    return (struct.pack('<III', kind, len(data), len(metadata)) + bytes(data) + bytes(-len(data) % 4)
            + bytes(metadata) + bytes(-len(metadata) % 4))


def _chain(name, kind, *modifiers):
    writer = BlockWriter().string(name).u32(kind).u32(0)
    writer.data.extend(bytes(-len(writer.data) % 4))
    writer.u32(len(modifiers))
    return block(0xFFFFFF14, writer.data + b''.join(modifiers))


def _node(name, parent):
    writer = BlockWriter().string(name).u32(1).string(parent)
    writer.data.extend(np.eye(4, dtype='<f4').tobytes())
    return writer


def _vectors(values, field):
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != 3 or not len(array):
        raise ValueError('%s must be a nonempty array of triples' % field)
    if not np.isfinite(array).all() or np.any(np.abs(array) > np.finfo(np.float32).max):
        raise ValueError('%s must contain finite F32 values' % field)
    return array.astype('<f4')


def _indices(values, count, field):
    array = np.asarray(values)
    if array.ndim != 2 or array.shape[1] != 3 or not len(array) or array.dtype.kind not in 'iu':
        raise ValueError('%s must be a nonempty array of integer triples' % field)
    if np.any(array < 0) or np.any(array >= count):
        raise ValueError('%s contains an out-of-range index' % field)
    return array.astype('<u4')


def _mesh(mesh):
    name = mesh['name']
    if not isinstance(name, str) or not name or len(name.encode('utf-8')) > 65535:
        raise ValueError('Mesh name must be nonempty UTF-8 fitting a U16 byte length')
    positions = _vectors(mesh['positions'], 'positions')
    faces = _indices(mesh['faces'], len(positions), 'faces')
    triangles = positions[faces].astype(np.float64)
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    if np.any(lengths == 0):
        raise ValueError('Faces must be nondegenerate triangles at F32 precision')
    if 'normals' in mesh or 'normal_indices' in mesh:
        if 'normals' not in mesh or 'normal_indices' not in mesh:
            raise ValueError('Provide both normals and normal_indices')
        normals = _vectors(mesh['normals'], 'normals').astype(np.float64)
        lengths = np.linalg.norm(normals, axis=1)
        if np.any(lengths == 0):
            raise ValueError('Normals must be nonzero')
        normal_indices = _indices(mesh['normal_indices'], len(normals), 'normal_indices')
        if normal_indices.shape != faces.shape:
            raise ValueError('normal_indices must match faces')
    else:
        normal_indices = np.repeat(np.arange(len(faces), dtype='<u4')[:, None], 3, axis=1)
    normals = (normals / lengths[:, None]).astype('<f4')
    color = np.asarray(mesh['color'], dtype=float)
    if color.shape != (3,) or not np.isfinite(color).all() or np.any((color < 0) | (color > 1)):
        raise ValueError('color must be three finite RGB values in [0, 1]')
    opacity = np.asarray(mesh.get('opacity', 1))
    if opacity.ndim != 0:
        raise ValueError('opacity must be a finite scalar in [0, 1]')
    try:
        opacity = float(opacity.item())
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError('opacity must be a finite scalar in [0, 1]') from error
    if not np.isfinite(opacity) or not 0 <= opacity <= 1:
        raise ValueError('opacity must be a finite scalar in [0, 1]')
    return name, positions, faces, normals, normal_indices, color, opacity


def write_scene(meshes):
    """Return U3D bytes for a nonempty iterable of mesh dictionaries.

    Required: name (unique string), positions (N x 3), faces (M x 3 integer
    indices), color (RGB in [0, 1]). Optional: normals (K x 3) together with
    normal_indices (M x 3), and opacity (finite scalar in [0, 1], default 1).
    Normals are normalized; otherwise flat face normals
    are generated. Positions are world-space, unitless, and converted to F32.
    Winding is preserved; front faces are counterclockwise. Inputs aren't changed.
    """
    meshes = [_mesh(mesh) for mesh in meshes]
    names = [mesh[0] for mesh in meshes]
    if not meshes or len(set(names)) != len(names):
        raise ValueError('Scene needs meshes with unique names')
    root = '__Scene'
    while any(name.startswith(root) for name in names):
        root = '_' + root
    group = _node(root, '')
    declarations = [_chain(root, 0, block(0xFFFFFF21, group.data))]
    continuations = []
    for name, positions, faces, normals, normal_indices, color, opacity in meshes:
        model = _node(name, root).string(name).u32(3)
        shading = BlockWriter().string(name).u32(1).u32(1).u32(1).u32(1).string(name)
        declarations.append(_chain(name, 0, block(0xFFFFFF22, model.data), block(0xFFFFFF45, shading.data)))

        declaration = BlockWriter().string(name).u32(0).u32(0)
        counts = (len(faces), len(positions), len(normals), 0, 0, 0)
        for value in counts:
            declaration.u32(value)
        # One shading description, no vertex colors or texture layers, original ID zero.
        declaration.u32(1).u32(0).u32(0).u32(0).u32(len(positions)).u32(len(positions))
        for _ in range(3):
            declaration.u32(1000)
        for _ in range(5):
            declaration.f32(1)
        # Preserve sharp edges; progressive normal prediction isn't used by base meshes.
        declaration.f32(1).f32(1).f32(1).u32(0)
        declarations.append(_chain(name, 1, block(0xFFFFFF31, declaration.data)))

        shader = BlockWriter().string(name).u32(1).f32(0).u32(0x617).u32(0x606).u32(1).u32(0).u32(0)
        shader.string(name)
        declarations.append(block(0xFFFFFF53, shader.data))
        material = BlockWriter().string(name).u32(0x3F)
        for rgb in ((0.2, 0.2, 0.2), color, (0, 0, 0), (0, 0, 0)):
            for value in rgb:
                material.f32(value)
        material.f32(0).f32(opacity)
        declarations.append(block(0xFFFFFF54, material.data))

        base = BlockWriter().string(name).u32(0)
        for value in counts:
            base.u32(value)
        base.data.extend(positions.tobytes())
        base.data.extend(normals.tobytes())
        corners = np.zeros((len(faces), 7), dtype='<u4')
        corners[:, 1::2] = faces
        corners[:, 2::2] = normal_indices
        base.data.extend(corners.tobytes())
        base.u32(0)  # ECMA 10.4.10 flush word, also retained by SDK no-compression output.
        continuations.append(block(0xFFFFFF3B, base.data))

    for suffix, kind, intensity in (('Ambient', 0, 0.35), ('Directional', 1, 0.85)):
        name = root + suffix
        node = _node(name, root).string(name)
        declarations.append(_chain(name, 0, block(0xFFFFFF23, node.data)))
        light = BlockWriter().string(name).u32(1).u8(kind)
        for value in (1, 1, 1, 1, 1, 0, 0, 45, intensity):
            light.f32(value)
        declarations.append(block(0xFFFFFF51, light.data))

    declarations = b''.join(declarations)
    continuations = block(0xFFFFFF15, BlockWriter().u32(1).data) + b''.join(continuations)
    # ECMA 9.4.1.1.1 requires a negative major version until encoder compliance is validated.
    header = BlockWriter().u16(0xFFFF).u16(0).u32(4).u32(36 + len(declarations))
    header.u64(36 + len(declarations) + len(continuations)).u32(106)
    return block(0x00443355, header.data) + declarations + continuations
