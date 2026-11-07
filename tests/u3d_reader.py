"""Independent, test-only ECMA-363 third-edition no-compression scene reader.

Derived from https://ecma-international.org/wp-content/uploads/
ECMA-363_3rd_edition_june_2006.pdf, not from the application exporter.
Only static, untextured base meshes and ambient/directional lights are supported.
No rendering, arithmetic decoding, progressive meshes, or skeletons are implemented.

read_u3d(bytes) returns a dict with header, nodes, meshes, shaders, materials,
lights (palettes keyed by case-sensitive name), and chains keyed by (type, name).
Matrices are flat tuples in the standard's column-major wire order. Mesh faces
and normal_indices are separate lists of triples; shading_ids selects a shading
description for each face. Chain shading entries expose shader_lists by name.
Links are names, not object references; empty names refer to default palettes.
Undefined references are preserved as required by section 9.3.
"""

import math
import struct


class U3DError(ValueError):
    """Malformed or truncated U3D input."""


class UnsupportedU3D(U3DError):
    """Valid U3D features outside this test reader's subset."""


def _require(condition, message):
    if not condition:
        raise U3DError(message)


def _flags(value, mask, field):
    _require(not value & ~mask, 'Invalid reserved bits in ' + field)
    return value


class _Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def take(self, size):
        _require(0 <= size <= len(self.data) - self.pos, 'Truncated U3D payload')
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result

    def values(self, fmt):
        result = struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))
        _require(all(not isinstance(v, float) or math.isfinite(v) for v in result),
                 'Invalid non-finite floating point value')
        return result

    def u32(self):
        return self.values('I')[0]

    def floats(self, count):
        return self.values('f' * count)

    def string(self):
        try:
            return self.take(self.values('H')[0]).decode('utf-8')
        except UnicodeDecodeError as exc:
            raise U3DError('Invalid UTF-8 string') from exc

    def count(self, minimum_size):
        count = self.u32()
        _require(count <= (len(self.data) - self.pos) // minimum_size,
                 'Truncated U3D counted array')
        return count

    def padding(self):
        _require(not any(self.take(-self.pos % 4)), 'Invalid nonzero padding')

    def end(self):
        _require(self.pos == len(self.data), 'Unconsumed bytes in U3D payload')


def _block(reader):
    # Section 9.2: each payload and metadata section is independently padded.
    kind, size, metadata_size = reader.values('III')
    payload = _Reader(reader.take(size))
    reader.padding()
    metadata = _Reader(reader.take(metadata_size))
    reader.padding()
    if metadata_size:
        for _ in range(metadata.count(8)):
            attributes = _flags(metadata.u32(), 0x773, 'metadata attributes')
            metadata.string()
            if attributes & 1:
                metadata.take(metadata.u32())
            else:
                metadata.string()
        metadata.end()
    return kind, payload


def _store(palette, item):
    name = item['name']
    _require(bool(name), 'Empty object definition name')
    _require(name not in palette, 'Duplicate object definition: ' + name)
    palette[name] = item


def _parents(reader):
    # Section 9.5.1.2: one transform per parent, including the default world.
    return [{'name': reader.string(), 'matrix': reader.floats(16)}
            for _ in range(reader.count(66))]


def _mesh_declaration(reader, name):
    # Section 9.6.1.1. No face data occurs in the declaration.
    _require(reader.u32() == 0, 'Invalid mesh chain index')
    attributes = _flags(reader.u32(), 1, 'mesh attributes')
    counts = reader.values('IIIIII')
    shadings = []
    for _ in range(reader.count(12)):
        shading_attributes = _flags(reader.u32(), 3, 'shading description attributes')
        dimensions = [reader.u32() for _ in range(reader.count(4))]
        _require(all(1 <= n <= 4 for n in dimensions), 'Invalid texture coordinate dimensions')
        original_id = reader.u32()
        if shading_attributes or dimensions:
            raise UnsupportedU3D('Vertex colors and texture coordinates are unsupported')
        shadings.append({'attributes': shading_attributes, 'texture_dimensions': dimensions,
                         'original_id': original_id})
    minimum, maximum = reader.values('II')
    _require(minimum <= maximum == counts[1], 'Invalid mesh resolution range')
    quality = reader.values('III')
    inverse_quantization = reader.floats(5)
    parameters = reader.floats(3)
    if reader.u32():
        raise UnsupportedU3D('Mesh skeletons are unsupported')
    if minimum != maximum:
        raise UnsupportedU3D('Progressive meshes are unsupported')
    if any(counts[3:]):
        raise UnsupportedU3D('Vertex colors and texture coordinates are unsupported')
    _require(not attributes & 1 or counts[2] == 0, 'Excluded normals have a nonzero count')
    return {'name': name, 'attributes': attributes, 'counts': counts, 'shadings': shadings,
            'minimum_resolution': minimum, 'maximum_resolution': maximum,
            'quality': quality, 'inverse_quantization': inverse_quantization,
            'parameters': parameters}


def _base_mesh(reader, mesh):
    # Section 9.6.1.2: F32 arrays, then shading ID and three interleaved corners.
    _require(reader.u32() == 0, 'Invalid base mesh chain index')
    counts = reader.values('IIIIII')
    _require(counts == mesh['counts'], 'Base mesh counts differ from static declaration')
    _require('positions' not in mesh, 'Duplicate base mesh continuation')
    faces, positions, normals = counts[:3]
    _require(12 * (positions + normals) <= len(reader.data) - reader.pos,
             'Truncated base mesh arrays')
    mesh['positions'] = [reader.floats(3) for _ in range(positions)]
    mesh['normals'] = [reader.floats(3) for _ in range(normals)]
    mesh['faces'], mesh['normal_indices'], mesh['shading_ids'] = [], [], []
    has_normals = not mesh['attributes'] & 1
    _require(faces * (16 + 12 * has_normals) <= len(reader.data) - reader.pos,
             'Truncated base mesh faces')
    for _ in range(faces):
        shading = reader.u32()
        _require(shading < len(mesh['shadings']), 'Invalid face shading index')
        position_indices, normal_indices = [], []
        for _ in range(3):
            index = reader.u32()
            _require(index < positions, 'Invalid face position index')
            position_indices.append(index)
            if has_normals:
                index = reader.u32()
                _require(index < normals, 'Invalid face normal index')
                normal_indices.append(index)
        mesh['faces'].append(tuple(position_indices))
        if has_normals:
            mesh['normal_indices'].append(tuple(normal_indices))
        mesh['shading_ids'].append(shading)
    # Section 10.4.10 flush word: retained by the SDK even in no-compression mode.
    if len(reader.data) - reader.pos == 4:
        _require(reader.u32() == 0, 'Invalid base mesh flush word')


def _declaration(kind, reader, scene, chain=None, index=0):
    name = reader.string()
    if chain is not None:
        _require(name == chain['name'], 'Modifier name differs from chain name')
    item = {'name': name}
    if kind in (0xFFFFFF21, 0xFFFFFF22, 0xFFFFFF23):
        item.update(kind={0xFFFFFF21: 'group', 0xFFFFFF22: 'model', 0xFFFFFF23: 'light'}[kind],
                    parents=_parents(reader))
        if kind != 0xFFFFFF21:
            item['resource'] = reader.string()
        if kind == 0xFFFFFF22:
            item['visibility'] = _flags(reader.u32(), 3, 'model visibility')
        if chain is not None:
            _require(chain['type'] == 0 and index == 0, 'Invalid node modifier placement')
        _store(scene['nodes'], item)
    elif kind == 0xFFFFFF31:
        if chain is not None:
            _require(chain['type'] == 1 and index == 0, 'Invalid mesh modifier placement')
        _store(scene['meshes'], _mesh_declaration(reader, name))
    elif kind == 0xFFFFFF45:
        if chain is None:
            raise U3DError('Shading modifier requires a modifier chain')
        _require(reader.u32() == index, 'Invalid shading modifier chain index')
        item['attributes'] = _flags(reader.u32(), 15, 'shading attributes')
        item['shader_lists'] = [[reader.string() for _ in range(reader.count(2))]
                                for _ in range(reader.count(4))]
        chain['shading'].append(item)
    elif kind == 0xFFFFFF53:
        item['attributes'] = _flags(reader.u32(), 7, 'shader attributes')
        item['alpha_reference'] = reader.floats(1)[0]
        item['alpha_function'], item['blend_function'], item['render_passes'] = reader.values('III')
        _require(0x610 <= item['alpha_function'] <= 0x617, 'Invalid alpha test function')
        _require(0x604 <= item['blend_function'] <= 0x607, 'Invalid color blend function')
        channels, alpha_channels = reader.values('II')
        _flags(channels, 255, 'shader channels')
        _flags(alpha_channels, channels, 'alpha texture channels')
        item['material'] = reader.string()
        if channels:
            raise UnsupportedU3D('Textured shaders are unsupported')
        _store(scene['shaders'], item)
    elif kind == 0xFFFFFF54:
        item['attributes'] = _flags(reader.u32(), 63, 'material attributes')
        for field in ('ambient', 'diffuse', 'specular', 'emissive'):
            item[field] = reader.floats(3)
        item['reflectivity'], item['opacity'] = reader.floats(2)
        _store(scene['materials'], item)
    elif kind == 0xFFFFFF51:
        item['attributes'] = _flags(reader.u32(), 7, 'light attributes')
        item['type'] = reader.values('B')[0]
        if item['type'] not in (0, 1):
            raise UnsupportedU3D('Only ambient and directional lights are supported')
        color = reader.floats(4)
        _require(color[3] == 1, 'Invalid light reserved parameter')
        item['color'] = color[:3]
        item['attenuation'] = reader.floats(3)
        item['spot_angle'], item['intensity'] = reader.floats(2)
        _store(scene['lights'], item)
    else:
        raise UnsupportedU3D('Unsupported U3D block type: %#010x' % kind)
    reader.end()


def _chain(reader, scene):
    # Section 9.4.3: embedded blocks start after absolute 32-bit alignment.
    name, kind = reader.string(), reader.u32()
    if kind not in (0, 1):
        raise UnsupportedU3D('Only node and model resource modifier chains are supported')
    attributes = _flags(reader.u32(), 3, 'modifier chain attributes')
    chain = {'name': name, 'type': kind, 'attributes': attributes, 'shading': []}
    if attributes & 1:
        chain['bounding_sphere'] = reader.floats(4)
        _require(chain['bounding_sphere'][3] >= 0, 'Invalid bounding sphere radius')
    if attributes & 2:
        bounds = reader.floats(6)
        _require(all(bounds[i] <= bounds[i + 3] for i in range(3)), 'Invalid bounding box')
        chain['bounding_box'] = bounds
    reader.padding()
    key = (kind, name)
    _require(name and key not in scene['chains'], 'Invalid or duplicate modifier chain name')
    for index in range(reader.count(12)):
        block_type, payload = _block(reader)
        if block_type not in (0xFFFFFF21, 0xFFFFFF22, 0xFFFFFF23, 0xFFFFFF31, 0xFFFFFF45):
            raise UnsupportedU3D('Unsupported modifier declaration: %#010x' % block_type)
        _declaration(block_type, payload, scene, chain, index)
    reader.end()
    palette = 'nodes' if kind == 0 else 'meshes'
    _require(name in scene[palette], 'Modifier chain has no node or mesh generator')
    scene['chains'][key] = chain


def read_u3d(data):
    """Decode bytes; raise U3DError (including UnsupportedU3D) on failure.

    header: major, minor, profile, declaration_size, file_size, encoding,
    units_scale (None for unitless files). Mesh declarations expose counts in
    face/position/normal/diffuse/specular/texture order. Every supported field
    is consumed; unknown blocks are rejected, never silently skipped.
    """
    reader = _Reader(data)
    kind, header = _block(reader)
    _require(kind == 0x00443355, 'Missing U3D File Header')
    major, minor, profile, declaration_size, file_size, encoding = header.values('hhIIQI')
    if major > 0:
        raise UnsupportedU3D('Unsupported U3D major version')
    if not profile & 4:
        raise UnsupportedU3D('Unsupported compression: no-compression profile bit is required')
    if profile & ~12:
        raise UnsupportedU3D('Unsupported U3D profile flags')
    _require(encoding == 106, 'Invalid character encoding: UTF-8 (106) is required')
    units_scale = header.values('d')[0] if profile & 8 else None
    _require(units_scale is None or units_scale > 0, 'Invalid units scaling factor')
    header.end()
    _require(file_size == len(data), 'Declared file size differs from actual file length')
    _require(reader.pos <= declaration_size <= file_size and declaration_size % 4 == 0,
             'Invalid declaration size')
    scene = {palette: {} for palette in ('nodes', 'meshes', 'shaders', 'materials', 'lights', 'chains')}
    scene['header'] = dict(major=major, minor=minor, profile=profile, declaration_size=declaration_size,
                           file_size=file_size, encoding=encoding, units_scale=units_scale)
    boundaries = {reader.pos}
    priority = 0
    while reader.pos < file_size:
        kind, payload = _block(reader)
        if kind == 0xFFFFFF14:
            _chain(payload, scene)
        elif kind == 0xFFFFFF15:
            new_priority = payload.u32()
            _require(new_priority >= priority, 'Invalid decreasing continuation priority')
            priority = new_priority
            payload.end()
        elif kind == 0xFFFFFF3B:
            name = payload.string()
            _require(name in scene['meshes'], 'Base mesh has no declaration: ' + name)
            _base_mesh(payload, scene['meshes'][name])
            payload.end()
        else:
            _declaration(kind, payload, scene)
        boundaries.add(reader.pos)
    _require(declaration_size in boundaries, 'Declaration size is not a block boundary')
    for mesh in scene['meshes'].values():
        if mesh['counts'][1]:
            _require('positions' in mesh, 'Missing base mesh continuation')
    # Section 9.5.1.2 forbids cycles, but section 9.3 permits undefined links.
    pending = dict(scene['nodes'])
    while pending:
        roots = [name for name, node in pending.items()
                 if not any(parent['name'] in pending for parent in node['parents'])]
        _require(bool(roots), 'Cyclic node parent links')
        for name in roots:
            del pending[name]
    return scene
