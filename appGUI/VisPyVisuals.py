# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# http://flatcam.org                                       #
# File Author: Dennis Hayrullin                            #
# Date: 2/5/2016                                           #
# MIT Licence                                              #
# ##########################################################

from vispy.visuals import CompoundVisual, LineVisual, MeshVisual, TextVisual, MarkersVisual
from vispy.visuals.gridlines import GridLinesVisual as VisPyGridLinesVisual, _GRID_COLOR
from vispy.visuals.shaders import Function
from vispy.scene.visuals import VisualNode, generate_docstring, visuals
from vispy.gloo import set_state
from vispy.color import Color
from shapely import Polygon, LineString, LinearRing
import itertools
import threading
import time
import numpy as np
from functools import lru_cache
from appGUI.VisPyTesselators import GLUTess

# set_gl_state() replaces the whole state and set_state() sends only the given keys, so a visual without these
# draws with the blend of the previous visual (the grid uses 'additive')
_BLEND = dict(blend=True, blend_func=('src_alpha', 'one_minus_src_alpha'))


# class FlatCAMLineVisual(LineVisual):
#     def __init__(self, pos=None, color=(0.5, 0.5, 0.5, 1), width=1, connect='strip', method='gl', antialias=False):
#         LineVisual.__init__(self, pos=pos, color=color, width=width, connect=connect,
#                             method=method, antialias=True)
#
#     def clear_data(self):
#         self._bounds = None
#         self._pos = None
#         self._changed['pos'] = True
#         self.update()


@lru_cache(maxsize=128)
def _cached_color_rgba(color):
    return Color(color).rgba


def _update_shape_buffers(data, triangulation='glu'):
    """
    Translates Shapely geometry to internal buffers for speedup redraws
    :param data: dict
        Input shape data
    :param triangulation: str
        Triangulation engine
    """
    mesh_vertices = []                                              # Vertices for mesh
    mesh_tris = []                                                  # Faces for mesh
    mesh_rgba = np.zeros(4, dtype=np.float32)                       # One face color for the whole shape
    line_pts = []                                                   # Vertices for line
    line_rgba = np.zeros(4, dtype=np.float32)                       # One line color for the whole shape

    geo, color, face_color, tolerance = data['geometry'], data['color'], data['face_color'], data['tolerance']

    if geo is not None and not geo.is_empty:
        simplified_geo = geo.simplify(tolerance) if tolerance else geo      # Simplified shape
        pts = []                                                            # Shape line points
        tri_pts = []                                                        # Mesh vertices
        tri_tris = []                                                       # Mesh faces

        if type(geo) == LineString:
            # Prepare lines
            pts = _linestring_to_segments(simplified_geo.coords)

        elif type(geo) == LinearRing:
            # Prepare lines
            pts = _linearring_to_segments(simplified_geo.coords)

        elif type(geo) == Polygon:
            # Prepare polygon faces
            if face_color is not None:
                if triangulation == 'glu':
                    gt = GLUTess()
                    tri_tris, tri_pts = gt.triangulate(simplified_geo)
                else:
                    print("Triangulation type '%s' isn't implemented. Drawing only edges." % triangulation)

            # Prepare polygon edges
            if color is not None:
                pts = _linearring_to_segments(simplified_geo.exterior.coords)
                if len(simplified_geo.interiors):
                    pts = np.concatenate([pts] + [
                        _linearring_to_segments(ints.coords) for ints in simplified_geo.interiors])

        # Appending data for mesh
        if len(tri_pts) > 0 and len(tri_tris) > 0:
            mesh_tris += tri_tris
            mesh_vertices += tri_pts
            face_color_rgba = (_cached_color_rgba(face_color) if isinstance(face_color, str)
                               else Color(face_color).rgba)
            mesh_rgba = face_color_rgba

        # Appending data for line
        if len(pts) > 0:
            line_pts = pts
            line_rgba = _cached_color_rgba(color) if isinstance(color, str) else Color(color).rgba

    # Store buffers as numpy arrays for faster concatenation in __update()
    data['line_pts'] = np.asarray(line_pts, dtype=np.float32) if len(line_pts) else np.empty((0, 2), dtype=np.float32)
    # The color is stored once per shape. __update() repeats it for every vertex.
    data['line_rgba'] = np.asarray(line_rgba, dtype=np.float32).reshape(4)
    data['mesh_vertices'] = np.array(mesh_vertices, dtype=np.float32) if mesh_vertices else np.empty((0, 2), dtype=np.float32)
    data['mesh_tris'] = np.array(mesh_tris, dtype=np.uint32) if mesh_tris else np.empty(0, dtype=np.uint32)
    data['mesh_rgba'] = np.asarray(mesh_rgba, dtype=np.float32).reshape(4)

    # Clear shapely geometry
    del data['geometry']

    return data


def _linearring_to_segments(arr):
    # Close linear ring
    """
    Translates linear ring to line segments
    :param arr: numpy.array
        Array of linear ring vertices
    :return: numpy.array
        Line segments
    """
    arr = np.asarray(arr)
    if len(arr) and not np.array_equal(arr[0], arr[-1]):
        arr = np.concatenate((arr, arr[:1]))
    return _linestring_to_segments(arr)


def _linestring_to_segments(arr):
    """
    Translates line strip to segments
    :param arr: numpy.array
        Array of line strip vertices
    :return: numpy.array
        Line segments
    """
    arr = np.asarray(arr)
    if len(arr) < 2:
        return np.empty((0, 2), dtype=np.float32)
    segments = np.empty(((len(arr) - 1) * 2, arr.shape[1]), dtype=np.float32)
    segments[0::2] = arr[:-1]
    segments[1::2] = arr[1:]
    return segments


@lru_cache(maxsize=128)
def _cached_hover_buffers(bounds, units, color, face_color):
    xmin, ymin, xmax, ymax = bounds
    shape = Polygon([(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)])
    shape = shape.buffer(-0.1).buffer(0.2) if units == 'MM' else shape.buffer(-0.00393).buffer(0.00787)
    return _update_shape_buffers({
        'geometry': shape, 'color': color, 'face_color': face_color,
        'alpha': None, 'visible': True, 'layer': 0, 'tolerance': None
    })


class ShapeGroup(object):
    def __init__(self, collection):
        """
        Represents group of shapes in collection
        :param collection: ShapeCollection
            Collection to work with
        """
        self._collection = collection
        self._indexes = []
        self._visible = True
        self._enabled = True
        self._color = None

    def add(self, **kwargs):
        """
        Adds shape to collection and store index in group
        :param kwargs: keyword arguments.
            Arguments for ShapeCollection.add function
        """
        key = self._collection.add(**kwargs)
        self._indexes.append(key)
        return key

    def add_batch(self, shapes_data, **kwargs):
        """
        Batch adds shapes to collection and stores indexes in group.
        """
        keys = self._collection.add_batch(shapes_data, **kwargs)
        self._indexes.extend(keys)
        return keys
    
    def remove(self, idx, update=False):
        self._indexes.remove(idx)
        self._collection.remove(idx, False)
        if update:
            self._collection.redraw([])             # Skip waiting results

    def clear(self, update=False):
        """
        Removes group shapes from collection, clear indexes
        :param update: bool
            Set True to redraw collection
        """
        for i in self._indexes:
            self._collection.remove(i, False)

        del self._indexes[:]

        if update:
            self._collection.redraw([])             # Skip waiting results

    def transform(self, matrix, update=True):
        """
        Applies an affine transform (shapely 6-tuple) to the shapes of the group without a new triangulation.

        :return: True if done. False if the caller has to plot the shapes again.
        """
        return self._collection.transform_keys(list(self._indexes), matrix, update)

    def redraw(self, update_colors=None):
        """
        Redraws shape collection
        """
        if update_colors:
            self._collection.redraw(self._indexes, update_colors=update_colors)
        else:
            self._collection.redraw(self._indexes)

    @property
    def visible(self):
        """
        Visibility of group
        :return: bool
        """
        return self._visible

    @visible.setter
    def visible(self, value):
        """
        Visibility of group
        :param value: bool
        """
        self._visible = value
        data = self._collection.data
        for i in self._indexes:
            if i in data:
                data[i]['visible'] = value

        # the collection skips the update if it is not marked dirty
        self._collection._dirty = True
        self._collection.redraw([])

    @property
    def enabled(self):
        """
        Another way to toggle visibility on canvas
        :return:
        :rtype:
        """
        return self._enabled

    @enabled.setter
    def enabled(self, value):
        """
        Another way to toggle visibility on canvas
        :param value: bool
        """
        self._collection.enabled = value

    def move_to_top(self):
        """
        Draw the shapes of the group over all the other shapes of the collection
        """
        if self._collection.move_to_top(self._indexes):
            self._collection.redraw([])

    def update_visibility(self, state, indexes=None):
        data = self._collection.data
        if indexes:
            # a set: a membership test on the list is O(n) for each index
            group_indexes = set(self._indexes)
            for i in indexes:
                if i in group_indexes and i in data:
                    data[i]['visible'] = state
        else:
            for i in self._indexes:
                if i in data:
                    data[i]['visible'] = state

        # the collection skips the update if it is not marked dirty
        self._collection._dirty = True
        self._collection.redraw([])


class RectangleVisual(CompoundVisual):
    """Keep rubber-band topology and styling; dragging updates only vertex positions."""

    def __init__(self, linewidth):
        self._vertices = np.array([(0, 0), (1, 0), (1, 1), (0, 1)], dtype=np.float32)
        self._mesh = MeshVisual(vertices=self._vertices, faces=np.array([(0, 1, 2), (0, 2, 3)]))
        self._line = LineVisual(pos=self._vertices[[0, 1, 1, 2, 2, 3, 3, 0]],
                                connect='segments', width=linewidth, antialias=True)
        # Reversing a drag mirrors the unit square; both windings must remain visible.
        self._mesh.set_gl_state(polygon_offset_fill=True, polygon_offset=(1, 1), cull_face=False, **_BLEND)
        self._line.set_gl_state(**_BLEND)
        self._rect = None
        self._colors = None
        super().__init__([self._mesh, self._line])

    def set_data(self, start, end, color, face_color):
        x0, y0 = start
        x1, y1 = end
        rect = (x0, y0, x1, y1)
        if rect != self._rect:
            self._vertices[:] = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
            self._mesh.mesh_data.set_vertices(self._vertices)
            self._mesh.set_data(meshdata=self._mesh.mesh_data)
            self._line.set_data(pos=self._vertices[[0, 1, 1, 2, 2, 3, 3, 0]])
            self._rect = rect
        colors = (color, face_color)
        if colors != self._colors:
            if color is not None:
                self._line.set_data(color=color)
            if face_color is not None:
                self._mesh.color = face_color
            self._colors = colors
        self._line.visible = color is not None
        self._mesh.visible = face_color is not None and x1 != x0 and y1 != y0


class _DeferredUpdater(object):
    """
    One daemon thread shared by all deferred collections. A collection asks for an update; the update runs when
    no new request came for QUIET seconds, but at most MAX_WAIT seconds after the first pending request.
    A burst of redraws (plot_all) gives one rebuild. One thread for all: the updates of the collections run one
    after another, so two updates of one collection never overlap and only one extra thread exists.
    """
    QUIET = 0.03
    MAX_WAIT = 0.15

    def __init__(self):
        self._cond = threading.Condition()
        self._pending = {}      # id(collection) -> [collection, first request time, last request time]
        self._thread = None
        self._stopped = False
        self._run_lock = threading.Lock()   # held while an update runs

    def request(self, collection):
        now = time.monotonic()
        with self._cond:
            if self._stopped:
                return
            entry = self._pending.get(id(collection))
            if entry is None:
                self._pending[id(collection)] = [collection, now, now]
            else:
                entry[2] = now
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="ShapeCollectionUpdater", daemon=True)
                self._thread.start()
            self._cond.notify()

    def cancel(self, collection):
        with self._cond:
            return self._pending.pop(id(collection), None) is not None

    def stop(self):
        """
        Drops the pending updates, refuses new requests and waits for an update that is running.
        Call it before the canvas closes: an update after that fails because Gloo has no canvas.
        """
        with self._cond:
            self._stopped = True
            self._pending.clear()
        with self._run_lock:
            pass

    def _run(self):
        while True:
            with self._cond:
                while True:
                    if not self._pending:
                        self._cond.wait()
                        continue
                    now = time.monotonic()
                    ready = None
                    wait = None
                    for key, (collection, first, last) in self._pending.items():
                        due = min(last + self.QUIET, first + self.MAX_WAIT)
                        if due <= now:
                            ready = key
                            break
                        wait = due - now if wait is None else min(wait, due - now)
                    if ready is not None:
                        collection = self._pending.pop(ready)[0]
                        self._run_lock.acquire()
                        break
                    self._cond.wait(wait)
            try:
                collection._update_deferred()
            except Exception as e:
                print("VisPyVisuals deferred update error = %s" % str(e))
            finally:
                self._run_lock.release()
            collection = None


_deferred_updater = _DeferredUpdater()


def stop_deferred_updates():
    """
    Stops the deferred updates of all collections. The app calls it at quit, before it closes the canvas.
    """
    _deferred_updater.stop()


class ShapeCollectionVisual(CompoundVisual):

    def __init__(self, linewidth=1, triangulation='vispy', layers=3, pool=None, fcoptions=None, deferred=False,
                 **kwargs):
        """
        Represents collection of shapes to draw on VisPy scene
        :param linewidth: float
            Width of lines/edges
        :param triangulation: str
            Triangulation method used for polygons translation
            'vispy' - VisPy lib triangulation
            'gpc' - Polygon2 lib
        :param layers: int
            Layers count
            Each layer adds 2 visuals on VisPy scene. Be careful: more layers cause less fps
        :param deferred: bool
            True: redraw() only asks for a rebuild, a shared thread joins a burst of redraws into one rebuild.
            Use flush() to get the buffers at once. Only for the big collection of the plotted objects.
            False (default): redraw() rebuilds at once, for interactive shapes.
        :param kwargs:
        """
        self.fc_options = fcoptions
        self._deferred = deferred

        self.data = {}
        self.last_key = -1
        self._top_keys = set()
        self._rectangle = None
        
        # Dirty flag to track whether buffers need rebuilding
        self._dirty = True

        # Thread locks
        self.key_lock = threading.Lock()
        self.results_lock = threading.Lock()
        self.update_lock = threading.Lock()

        # Process pool
        self.pool = pool
        self.results = {}
        self._batch_results = []  # NEW: list of (keys, AsyncResult) tuples for batch processing

        self._meshes = [MeshVisual() for _ in range(0, layers)]
        # self._lines = [LineVisual(antialias=True) for _ in range(0, layers)]
        self._lines = [LineVisual(antialias=True) for _ in range(0, layers)]
        self._top_mesh = MeshVisual()
        self._top_line = LineVisual(antialias=True)

        self._line_width = linewidth
        self._triangulation = triangulation

        visuals_ = [self._lines[i // 2] if i % 2 else self._meshes[i // 2] for i in range(0, layers * 2)]
        visuals_.extend([self._top_mesh, self._top_line])

        CompoundVisual.__init__(self, visuals_, **kwargs)

        for m in self._meshes:
            # Backface culling - safe with CCW winding enforcement in tessellator
            cull = True
            if self.fc_options:
                cull = self.fc_options.get("global_backface_culling", True)
            m.set_gl_state(polygon_offset_fill=True, polygon_offset=(1, 1), cull_face=cull, **_BLEND)

        for lne in self._lines:
            lne.set_gl_state(**_BLEND)

        # Selected objects are an overlay, independent of their normal plotting layer.
        self._top_mesh.set_gl_state(depth_test=False,
                                    cull_face=(self.fc_options or {}).get("global_backface_culling", True), **_BLEND)
        self._top_line.set_gl_state(depth_test=False, **_BLEND)

        self.freeze()

    def _check_layer(self, layer):
        """
        Raise ValueError unless layer is an integer in 0..len(self._meshes)-1.
        """
        if isinstance(layer, bool) or not isinstance(layer, (int, np.integer)) or not 0 <= layer < len(self._meshes):
            raise ValueError("Layer %r is out of range 0..%d." % (layer, len(self._meshes) - 1))

    def set_rectangle(self, start, end, color, face_color):
        """Replace selection contents without rebuilding collection buffers on each move."""
        if self.data:
            self.clear(update=True)
        if self._rectangle is None:
            if isinstance(self, VisualNode):
                self._rectangle = Rectangle(self._line_width, parent=self)
            else:
                self._rectangle = RectangleVisual(self._line_width)
                self.add_subvisual(self._rectangle)
        self._rectangle.set_data(start, end, color, face_color)
        if not self._rectangle.visible:
            self._rectangle.visible = True

    def add_hover(self, bounds, units, color, face_color):
        """Reuse rounded hover geometry and triangulation, bounded to 128 styles/bounds."""
        buffers = _cached_hover_buffers(tuple(bounds), units.upper(), color, face_color)
        with self.key_lock:
            self.last_key += 1
            key = self.last_key
        self.data[key] = buffers.copy()
        self._dirty = True
        self.redraw()
        return key

    def add(self, shape=None, color=None, face_color=None, alpha=None, visible=True,
            update=False, layer=1, tolerance=0.001, linewidth=None):
        """
        Adds shape to collection
        :return:
        :param shape: shapely.geometry
            Shapely geometry object
        :param color: str, tuple
            Line/edge color
        :param face_color: str, tuple
            Polygon face color
        :param alpha: str
            Polygon transparency
        :param visible: bool
            Shape visibility
        :param update: bool
            Set True to redraw collection
        :param layer: int
            Layer number. 0 - lowest.
        :param tolerance: float
            Geometry simplifying tolerance
        :param linewidth: int
            Width of the line
        :return: int
            Index of shape
        """
        self._check_layer(layer)

        # Get new key
        self.key_lock.acquire(True)
        self.last_key += 1
        key = self.last_key
        self.key_lock.release()

        # Prepare data for translation
        self.data[key] = {
            'geometry': shape,
            'color': color,
            'alpha': alpha,
            'face_color': face_color,
            'visible': visible,
            'layer': layer,
            'tolerance': tolerance,
            # the following keys are updated in the _update_shape_buffers() method
            'mesh_vertices': np.empty((0, 2), dtype=np.float32),    # Vertices for mesh
            'mesh_tris': np.empty(0, dtype=np.uint32),              # Faces for mesh
            'mesh_rgba': np.zeros(4, dtype=np.float32),             # Face color of the shape
            'line_pts': np.empty((0, 2), dtype=np.float32),         # Vertices for line
            'line_rgba': np.zeros(4, dtype=np.float32)              # Line color of the shape
        }

        if linewidth:
            self._line_width = linewidth

        # Always process synchronously for individual adds (pool IPC overhead > compute)
        self.data[key] = _update_shape_buffers(self.data[key])

        # Mark buffers as dirty
        self._dirty = True

        if update:
            self.redraw()   # redraw() waits for pool process end

        return key

    def add_batch(self, shapes_data, layer=1, tolerance=0.001, linewidth=None, visible=True):
        """
        Add multiple shapes in one pool call. Dramatically reduces IPC overhead.

        :param shapes_data: list of dicts, each with keys:
            'shape' (required), 'color', 'face_color', 'alpha', 'layer', 'tolerance'
        :param layer: int - default layer for shapes that don't specify one
        :param tolerance: float - default tolerance for shapes that don't specify one
        :param linewidth: int - line width override
        :param visible: bool - visibility for all shapes in the batch
        :return: list of int - keys for all added shapes (in same order as shapes_data)
        """
        if not shapes_data:
            return []

        # Validate every effective layer before any state changes (lock, last_key, data, line width)
        for item in shapes_data:
            self._check_layer(item.get('layer', layer))

        if linewidth:
            self._line_width = linewidth

        keys = []
        data_list = []

        self.key_lock.acquire(True)
        for item in shapes_data:
            self.last_key += 1
            key = self.last_key
            keys.append(key)
            self.data[key] = {
                'geometry': item.get('shape'),
                'color': item.get('color'),
                'alpha': item.get('alpha'),
                'face_color': item.get('face_color'),
                'visible': visible,
                'layer': item.get('layer', layer),
                'tolerance': item.get('tolerance', tolerance),
                'mesh_vertices': np.empty((0, 2), dtype=np.float32),
                'mesh_tris': np.empty(0, dtype=np.uint32),
                'mesh_rgba': np.zeros(4, dtype=np.float32),
                'line_pts': np.empty((0, 2), dtype=np.float32),
                'line_rgba': np.zeros(4, dtype=np.float32)
            }
            data_list.append(self.data[key])
        self.key_lock.release()

        if self.fc_options and self.fc_options.get("global_graphic_engine_3d_no_mp") is True:
            # Synchronous mode
            for k, d in zip(keys, data_list):
                self.data[k] = _update_shape_buffers(d)
        else:
            try:
                # ONE pool.map_async call for ALL shapes
                # Pool distributes items across workers automatically
                result = self.pool.map_async(_update_shape_buffers, data_list)
                self._batch_results.append((keys, result))
            except Exception:
                # Fallback: process synchronously
                for k, d in zip(keys, data_list):
                    self.data[k] = _update_shape_buffers(d)

        self._dirty = True
        return keys

    def add_copy(self, source, source_key, color=None, face_color=None, linewidth=None):
        """
        Adds a shape that is already in a collection (this one or another one) with new colors, without a new
        simplification and triangulation. The position buffers are shared: no code changes them after
        _update_shape_buffers(). Only the color buffers are new.

        :param source:      ShapeCollectionVisual that holds the shape
        :param source_key:  key of the shape in the source
        :param color:       line color of the copy
        :param face_color:  face color of the copy
        :param linewidth:   width of the lines of this collection
        :return:            key of the copy, or None if the source has no buffers for this key (yet)
        """
        src = source.data.get(source_key)
        if src is None or 'line_pts' not in src or source_key in source.results:
            return None

        if linewidth:
            self._line_width = linewidth

        def rgba_of(col, rows):
            # a color is parsed only if the shape has vertices to paint
            if not rows:
                return np.zeros(4, dtype=np.float32)
            rgba = _cached_color_rgba(col) if isinstance(col, str) else Color(col).rgba
            return np.asarray(rgba, dtype=np.float32)

        self.key_lock.acquire(True)
        self.last_key += 1
        key = self.last_key
        self.key_lock.release()

        self.data[key] = {
            'color': color,
            'alpha': src.get('alpha'),
            'face_color': face_color,
            'visible': True,
            'layer': src['layer'],
            'tolerance': src.get('tolerance'),
            'mesh_vertices': src['mesh_vertices'],
            'mesh_tris': src['mesh_tris'],
            'mesh_rgba': rgba_of(face_color, len(src['mesh_tris'])),
            'line_pts': src['line_pts'],
            'line_rgba': rgba_of(color, len(src['line_pts']))
        }

        self._dirty = True
        return key

    def remove(self, key, update=False):
        """
        Removes shape from collection
        :param key: int
            Shape index to remove
        :param update:
            Set True to redraw collection
        """
        # Remove process result
        self.results_lock.acquire(True)
        if key in list(self.results.copy().keys()):
            del self.results[key]
        self.results_lock.release()

        # Remove data
        if key in self.data:
            del self.data[key]
        self._top_keys.discard(key)

        # Mark buffers as dirty
        self._dirty = True

        if update:
            self.__update()

    def clear(self, update=False):
        """
        Removes all shapes from collection
        :param update: bool
            Set True to redraw collection
        """
        if self._rectangle is not None and self._rectangle.visible:
            self._rectangle.visible = False
        self.last_key = -1
        self.data.clear()
        self._top_keys.clear()
        
        # Mark buffers as dirty
        self._dirty = True
        
        if update:
            self.__update()

    def move_to_top(self, keys):
        """
        Move the shapes to the foreground, above every normal plotting layer.
        The shapes keep their keys and their buffers; there is no new tessellation.

        :param keys:    list of shape keys, in the order to draw them
        :return:        True if the order changed, False if the shapes are already on top
        """
        keys = [k for k in keys if k in self.data]
        if not keys:
            return False

        # Foreground membership matters even when these are already the last shapes.
        key_set = set(keys)
        if self._top_keys == key_set and set(itertools.islice(reversed(self.data), len(keys))) == key_set:
            return False

        # Lock sub-visuals updates; a key that is deleted and set again goes to the end of the dict
        self.update_lock.acquire(True)
        try:
            moved = [(k, self.data.pop(k)) for k in keys]
            self.data.update(moved)
            self._top_keys = key_set
        finally:
            self.update_lock.release()

        # Mark buffers as dirty
        self._dirty = True
        return True

    def update_visibility(self, state: bool, indexes=None) -> None:
        if indexes is not None:
            # a set: the keys of a group come as a list, and 'k in list' for each shape of the collection is O(N*M)
            indexes = set(indexes)
        # Lock sub-visuals updates
        self.update_lock.acquire(True)
        if indexes is None:
            for k, data in list(self.data.items()):
                self.data[k]['visible'] = state
        else:
            for k, data in list(self.data.items()):
                if k in indexes:
                    self.data[k]['visible'] = state

        self.update_lock.release()
        
        # Mark buffers as dirty
        self._dirty = True

    def update_color(self, new_mesh_color=None, new_line_color=None, indexes=None):
        if new_mesh_color is None and new_line_color is None:
            return

        if not self.data:
            return

        if indexes is not None:
            # a set: ShapeGroup.redraw() gives its keys as a list, and 'k in list' for each shape of the shared
            # collection is O(N*M), seconds for a large project
            indexes = set(indexes)

        # Mark buffers as dirty (color changes require visual update)
        self._dirty = True
        
        # Rest of the method...

        # if a new color is empty string then make it None so it will not be updated
        # if a new color is valid then transform it here in a format palatable
        mesh_color_rgba = None
        line_color_rgba = None
        if new_mesh_color:
            if new_mesh_color != '':
                mesh_color_rgba = Color(new_mesh_color).rgba
            else:
                new_mesh_color = None
        if new_line_color:
            if new_line_color != '':
                line_color_rgba = Color(new_line_color).rgba
            else:
                new_line_color = None

        meshes = self._meshes + [self._top_mesh]
        lines = self._lines + [self._top_line]
        mesh_colors_chunks = [[] for _ in meshes]     # Face colors chunks
        line_colors_chunks = [[] for _ in lines]     # Line colors chunks
        line_pts_chunks = [[] for _ in lines]         # Vertices for line chunks

        # Lock sub-visuals updates
        self.update_lock.acquire(True)
        # Merge shapes buffers

        if indexes is None:
            for k, data in list(self.data.items()):
                if data['visible'] and 'line_pts' in data:
                    layer = len(self._meshes) if k in self._top_keys else data['layer']
                    if new_mesh_color and new_mesh_color != '':
                        dim_mesh_tris = (len(data['mesh_tris']) // 3)
                        if dim_mesh_tris != 0:
                            try:
                                mesh_colors_chunks[layer].append(np.tile(mesh_color_rgba, (dim_mesh_tris, 1)))
                                self.data[k]['face_color'] = new_mesh_color
                                # Invalidate cached rgba
                                data['face_color_rgba'] = mesh_color_rgba
                                data['mesh_rgba'] = np.asarray(mesh_color_rgba, dtype=np.float32)
                            except Exception as e:
                                print("VisPyVisuals.ShapeCollectionVisual.update_color(). "
                                      "Create mesh colors --> Data error. %s" % str(e))

                    if new_line_color and new_line_color != '':
                        dim_line_pts = (len(data['line_pts']))
                        if dim_line_pts != 0:
                            try:
                                line_pts_chunks[layer].append(data['line_pts'])
                                line_colors_chunks[layer].append(np.tile(line_color_rgba, (dim_line_pts, 1)))
                                self.data[k]['color'] = new_line_color
                                # Invalidate cached rgba
                                data['color_rgba'] = line_color_rgba
                                data['line_rgba'] = np.asarray(line_color_rgba, dtype=np.float32)
                            except Exception as e:
                                print("VisPyVisuals.ShapeCollectionVisual.update_color(). "
                                      "Create line colors --> Data error. %s" % str(e))
        else:
            for k, data in list(self.data.items()):
                if data['visible'] and 'line_pts' in data:
                    layer = len(self._meshes) if k in self._top_keys else data['layer']
                    dim_mesh_tris = (len(data['mesh_tris']) // 3)
                    dim_line_pts = (len(data['line_pts']))

                    if k in indexes:
                        if new_mesh_color and new_mesh_color != '':
                            if dim_mesh_tris != 0:
                                try:
                                    mesh_colors_chunks[layer].append(np.tile(mesh_color_rgba, (dim_mesh_tris, 1)))
                                    self.data[k]['face_color'] = new_mesh_color
                                    # Invalidate cached rgba
                                    data['face_color_rgba'] = mesh_color_rgba
                                    data['mesh_rgba'] = np.asarray(mesh_color_rgba, dtype=np.float32)
                                except Exception as e:
                                    print("VisPyVisuals.ShapeCollectionVisual.update_color(). "
                                          "Create mesh colors --> Data error. %s" % str(e))
                        if new_line_color and new_line_color != '':
                            if dim_line_pts != 0:
                                try:
                                    line_pts_chunks[layer].append(data['line_pts'])
                                    line_colors_chunks[layer].append(np.tile(line_color_rgba, (dim_line_pts, 1)))
                                    self.data[k]['color'] = new_line_color
                                    # Invalidate cached rgba
                                    data['color_rgba'] = line_color_rgba
                                    data['line_rgba'] = np.asarray(line_color_rgba, dtype=np.float32)
                                except Exception as e:
                                    print("VisPyVisuals.ShapeCollectionVisual.update_color(). "
                                          "Create line colors --> Data error. %s" % str(e))
                    else:
                        if dim_mesh_tris != 0:
                            try:
                                # Use cached rgba value if available
                                if 'face_color_rgba' not in data:
                                    data['face_color_rgba'] = Color(data['face_color']).rgba
                                mesh_colors_chunks[layer].append(np.tile(data['face_color_rgba'], (dim_mesh_tris, 1)))
                            except Exception as e:
                                print("VisPyVisuals.ShapeCollectionVisual.update_color(). "
                                      "Create mesh colors --> Data error. %s" % str(e))

                        if dim_line_pts != 0:
                            try:
                                line_pts_chunks[layer].append(data['line_pts'])
                                # Use cached rgba value if available
                                if 'color_rgba' not in data:
                                    data['color_rgba'] = Color(data['color']).rgba
                                line_colors_chunks[layer].append(np.tile(data['color_rgba'], (dim_line_pts, 1)))
                            except Exception as e:
                                print("VisPyVisuals.ShapeCollectionVisual.update_color(). "
                                      "Create line colors --> Data error. %s" % str(e))

        # Concatenate chunks for GPU upload
        mesh_colors = [np.concatenate(c) if c else np.empty((0, 4), dtype=np.float32) for c in mesh_colors_chunks]
        line_pts = [np.concatenate(c) if c else np.empty((0, 2), dtype=np.float32) for c in line_pts_chunks]
        line_colors = [np.concatenate(c) if c else np.empty((0, 4), dtype=np.float32) for c in line_colors_chunks]

        # Updating meshes
        if new_mesh_color and new_mesh_color != '':
            for i, mesh in enumerate(meshes):
                if len(mesh_colors[i]) > 0:
                    try:
                        mesh._meshdata.set_face_colors(colors=mesh_colors[i])
                        mesh.mesh_data_changed()
                    except Exception as e:
                        print("VisPyVisuals.ShapeCollectionVisual.update_color(). "
                              "Apply mesh colors --> Data error. %s" % str(e))

        # Updating lines
        if new_line_color and new_line_color != '':
            for i, line in enumerate(lines):
                if len(line_pts[i]) > 0:
                    line.visible = True
                    try:
                        line._color = line_colors[i]
                        line._changed['color'] = True
                        line.update()
                    except Exception as e:
                        print("VisPyVisuals.ShapeCollectionVisual.update_color(). "
                              "Apply line colors --> Data error. %s" % str(e))
                else:
                    # line.clear_data()
                    line.visible = False

        self.update_lock.release()

    def __update(self):
        """
        Merges internal buffers, sets data to visuals, redraws collection on scene
        """
        # Skip if nothing has changed
        if not self._dirty:
            return

        with self.update_lock:
            # another thread may have done the work while this one waited for the lock
            if not self._dirty:
                return
            # reset before the work: a change during the work marks the collection dirty again
            self._dirty = False
            try:
                self._rebuild_visuals()
            except Exception:
                self._dirty = True
                raise

    def _rebuild_visuals(self):
        """
        Body of __update(), runs with update_lock held.
        """
        meshes = self._meshes + [self._top_mesh]
        lines = self._lines + [self._top_line]
        # Optimized: Phase 1 - Collect references (O(1) per shape)
        layer_line_pts_chunks = [[] for _ in lines]
        layer_mesh_verts_chunks = [[] for _ in meshes]
        layer_mesh_tris_chunks = [[] for _ in meshes]
        layer_mesh_colors_chunks = [[] for _ in meshes]
        layer_line_colors_chunks = [[] for _ in lines]
        
        # Track cumulative vertex counts per layer for mesh_tris offsets
        # mesh_tris contains triangle indices into mesh_vertices.
        # When merging multiple shapes, indices must be offset by the
        # cumulative vertex count of all previously merged shapes.
        layer_vert_counts = [0] * len(meshes)

        # Collect shape buffers
        for key, data in list(self.data.items()):
            if data['visible'] and 'line_pts' in data:
                layer = len(self._meshes) if key in self._top_keys else data['layer']
                layer_line_pts_chunks[layer].append(data['line_pts'])
                # one color per shape, repeated for every vertex (a view, copied by np.concatenate)
                layer_line_colors_chunks[layer].append(np.broadcast_to(data['line_rgba'], (len(data['line_pts']), 4)))

                if len(data['mesh_tris']) > 0 and len(data['mesh_vertices']) > 0:
                    offset = layer_vert_counts[layer]
                    layer_mesh_tris_chunks[layer].append(
                        data['mesh_tris'] + offset
                    )
                    layer_mesh_verts_chunks[layer].append(data['mesh_vertices'])
                    layer_mesh_colors_chunks[layer].append(
                        np.broadcast_to(data['mesh_rgba'], (len(data['mesh_tris']) // 3, 4)))
                    layer_vert_counts[layer] += len(data['mesh_vertices'])

        # Phase 2: Single flatten per layer using np.concatenate
        def _concat(chunks, empty_shape, dtype):
            return np.concatenate(chunks) if chunks else np.empty(empty_shape, dtype=dtype)

        line_pts = [_concat(layer_line_pts_chunks[i], (0, 2), np.float32) for i in range(len(lines))]
        line_colors = [_concat(layer_line_colors_chunks[i], (0, 4), np.float32) for i in range(len(lines))]
        mesh_vertices = [_concat(layer_mesh_verts_chunks[i], (0, 2), np.float32) for i in range(len(meshes))]
        mesh_tris = [_concat(layer_mesh_tris_chunks[i], (0,), np.uint32) for i in range(len(meshes))]
        mesh_colors = [_concat(layer_mesh_colors_chunks[i], (0, 4), np.float32) for i in range(len(meshes))]

        # Set GPU state once, outside the mesh loop (Step 1a optimization)
        set_state(polygon_offset_fill=False)

        # Updating meshes
        for i, mesh in enumerate(meshes):
            mesh.visible = len(mesh_vertices[i]) > 0
            if len(mesh_vertices[i]) > 0:
                mesh.set_data(
                    vertices=mesh_vertices[i],
                    faces=mesh_tris[i].reshape((-1, 3)),
                    face_colors=mesh_colors[i]
                )
            else:
                mesh.set_data()

            mesh._bounds_changed()

        # Updating lines
        for i, line in enumerate(lines):
            if len(line_pts[i]) > 0:
                line.visible = True
                line.set_data(
                    pos=line_pts[i],
                    color=line_colors[i],
                    width=self._line_width,
                    connect='segments')
            else:
                # line.clear_data()
                line.visible = False

            line._bounds_changed()

        self._bounds_changed()

    def _collect_results(self, indexes=None):
        """
        Stores the finished (or waits for the pending) process pool results in self.data.

        :param indexes: shape keys to get the single-shape (add()) results for; all if empty
        :return:        True if any data was replaced
        """
        # Only one thread can update data
        self.results_lock.acquire(True)

        results_collected = False

        # --- Collect batch results (Step 3c optimization) ---
        for batch_keys, batch_result in self._batch_results:
            try:
                batch_result.wait()
                batch_data = batch_result.get()
                for k, r in zip(batch_keys, batch_data):
                    if k in self.data:
                        self.data[k] = r
                        results_collected = True
            except Exception as e:
                print("VisPyVisuals.ShapeCollectionVisual.redraw() --> Batch error = %s" % str(e))

        self._batch_results.clear()
        # --- End batch results collection ---

        # Existing per-shape result collection (backward compat with add())
        for i in list(self.data.keys()) if not indexes else indexes:
            if i in list(self.results.keys()):
                try:
                    self.results[i].wait()                                  # Wait for process results
                    if i in self.data:
                        self.data[i] = self.results[i].get()[0]             # Store translated data
                        del self.results[i]
                        results_collected = True
                except Exception as e:
                    print("VisPyVisuals.ShapeCollectionVisual.redraw() --> Data error = %s. Indexes = %s" %
                          (str(e), str(indexes)))

        self.results_lock.release()
        return results_collected

    def transform_keys(self, keys, matrix, update=True):
        """
        Applies an affine transform to the already triangulated shapes, by mapping the stored vertex arrays.
        There is no new simplification and triangulation.

        :param keys:    shape keys to transform
        :param matrix:  shapely 6-tuple (a, b, d, e, xoff, yoff): x' = a*x + b*y + xoff; y' = d*x + e*y + yoff
        :param update:  True to redraw the collection
        :return:        True if done. False if a shape has no buffers (yet) or is not a plain buffer shape; nothing
                        is changed then and the caller has to plot again.
        """
        a, b, d, e, xoff, yoff = (float(v) for v in matrix)

        # pool results of the keys (also of the other keys: same wait as in redraw())
        if self._collect_results(keys):
            self._dirty = True

        data = self.data
        keys = [k for k in keys if k in data]
        for k in keys:
            if 'geometry' in data[k] or k in self.results:
                return False

        # The position arrays can be shared with other keys (add_copy()): they are never changed in place.
        # Every key gets new arrays; the buffers of all the keys are mapped by ONE matrix multiplication.
        flip = (a * e - b * d) < 0          # mirror: the winding of the triangles is reversed (backface culling)

        def _map(field):
            arrays = [data[k][field] for k in keys]
            lens = np.fromiter((len(x) for x in arrays), dtype=np.int64, count=len(arrays))
            total = int(lens.sum())
            if total == 0:
                return None, lens
            pts = np.concatenate(arrays).astype(np.float64, copy=False)
            out = np.empty((total, 2), dtype=np.float32)
            out[:, 0] = pts[:, 0] * a + pts[:, 1] * b + xoff
            out[:, 1] = pts[:, 0] * d + pts[:, 1] * e + yoff
            return out, lens

        new_line, line_lens = _map('line_pts')
        new_mesh, mesh_lens = _map('mesh_vertices')

        new_tris = None
        tri_lens = None
        if flip:
            tri_arrays = [data[k]['mesh_tris'] for k in keys]
            tri_lens = np.fromiter((len(x) for x in tri_arrays), dtype=np.int64, count=len(tri_arrays))
            if int(tri_lens.sum()):
                all_tris = np.concatenate(tri_arrays).reshape(-1, 3)
                new_tris = np.ascontiguousarray(all_tris[:, (0, 2, 1)]).reshape(-1)

        def _split(arr, lens):
            if arr is None:
                return None
            return np.split(arr, np.cumsum(lens)[:-1])

        line_parts = _split(new_line, line_lens)
        mesh_parts = _split(new_mesh, mesh_lens)
        tri_parts = _split(new_tris, tri_lens) if flip else None

        self.update_lock.acquire(True)
        try:
            for i, k in enumerate(keys):
                dk = data[k]
                if line_parts is not None and line_lens[i]:
                    dk['line_pts'] = line_parts[i]
                if mesh_parts is not None and mesh_lens[i]:
                    dk['mesh_vertices'] = mesh_parts[i]
                if tri_parts is not None and tri_lens[i]:
                    dk['mesh_tris'] = tri_parts[i]
            self._dirty = True
        finally:
            self.update_lock.release()

        if update:
            self.__update()
        return True

    def redraw(self, indexes=None, update_colors=None):
        """
        Redraws collection
        :param indexes:     list
            Shape indexes to get from process pool
        :param update_colors:
        """
        # Mark dirty if pool results were collected (data changed)
        if self._collect_results(indexes):
            self._dirty = True

        if update_colors is None or update_colors is False:
            if self._deferred:
                _deferred_updater.request(self)
            else:
                self.__update()
        else:
            try:
                self.update_color(
                    new_mesh_color=update_colors[0],
                    new_line_color=update_colors[1],
                    indexes=indexes
                )
            except Exception as e:
                print("VisPyVisuals.ShapeCollectionVisual.redraw() --> Update colors error = %s." % str(e))

    def _update_deferred(self):
        """
        Called by the deferred updater thread.
        """
        self.__update()

    def flush(self):
        """
        Runs a pending (deferred) update at once in the calling thread, and waits for one that is running.
        Call it before reading the buffers or the bounds of a deferred collection.
        Do not call it while holding update_lock.
        """
        if self._deferred:
            _deferred_updater.cancel(self)
        self.__update()
        # an update running in the updater thread: wait for it
        with self.update_lock:
            pass

    def set_deferred(self, value):
        """
        Turns the joined (deferred) redraws on or off. Turned off, a pending update is applied at once.
        """
        if self._deferred and not value:
            self.flush()
        self._deferred = bool(value)

    def lock_updates(self):
        self.update_lock.acquire(True)

    def unlock_updates(self):
        self.update_lock.release()


class GridLinesVisual(VisPyGridLinesVisual):
    """Grid with a stable ray-plane intersection at high zoom and oblique angles."""

    _shaders = dict(VisPyGridLinesVisual._shaders, fragment="""
        uniform vec2 image_size;
        uniform int method;
        varying vec2 v_texcoord;

        void main() {
            vec2 texcoord = v_texcoord;
            if (method == 1) {
                vec4 origin = $transform(vec4(v_texcoord, 0, 1));
                // Transform a direction, not a second point half a depth range away.
                // Subtracting those distant points loses the small grid coordinates.
                vec4 direction = $transform(vec4(0, 0, 1, 0));
                if (abs(direction.z) <= 1e-6 * length(direction.xyz)) {
                    discard;
                }
                vec4 point = origin - direction * (origin.z / direction.z);
                texcoord = point.xy / (point.w * image_size);
            }
            gl_FragColor = $color_transform($get_data(texcoord));
        }
    """)

    def __init__(self, scale=(1, 1), color='w', **kwargs):
        super(GridLinesVisual, self).__init__(scale=scale, color=color, **kwargs)
        # Use the actual projected plane footprint, not a constant-depth slice.
        # With the axis lines on, skip the grid lines at X=0 and Y=0; they show beside the axis at some zooms.
        self._grid_color_fn = Function(_GRID_COLOR.replace(
            'vec2 px = vec2(abs(dx.x) + abs(dy.x), abs(dx.y) + abs(dy.y));',
            'vec2 px = fwidth(pos) * $pixel_scale;'
        ).replace(
            '    float alpha = (((log(max(x_alpha, y_alpha))/log(10.)) + 2.) / 3.);',
            '    if ($hide_axes > 0.5) {\n'
            '        if (abs(local_pos.x) < 2. * px.x) { x_alpha = 0.; }\n'
            '        if (abs(local_pos.y) < 2. * px.y) { y_alpha = 0.; }\n'
            '    }\n'
            '    float alpha = (((log(max(x_alpha, y_alpha))/log(10.)) + 2.) / 3.);'
        ))
        self._grid_color_fn['color'] = Color(color).rgba
        self._grid_color_fn['scale'] = scale
        self._grid_color_fn['hide_axes'] = 0.0
        self.shared_program.frag['get_data'] = self._grid_color_fn

    def set_hide_axes(self, state):
        self._grid_color_fn['hide_axes'] = 1.0 if state else 0.0
        self.update()

    def _prepare_transforms(self, view):
        if view.transforms.canvas is not None:
            self._grid_color_fn['pixel_scale'] = view.transforms.canvas.pixel_scale
        super(GridLinesVisual, self)._prepare_transforms(view)


class TextGroup(object):
    def __init__(self, collection):
        self._collection = collection
        self._index = None
        self._visible = None

    def set(self, **kwargs):
        """
        Adds text to collection and store index
        :param kwargs: keyword arguments
            Arguments for TextCollection.add function
        """
        self._index = self._collection.add(**kwargs)

    def clear(self, update=False):
        """
        Removes text from collection, clear index
        :param update: bool
            Set True to redraw collection
        """

        if self._index is not None:
            self._collection.remove(self._index, False)
            self._index = None

        if update:
            self._collection.redraw()

    def redraw(self):
        """
        Redraws text collection
        """
        self._collection.redraw()

    @property
    def visible(self):
        """
        Visibility of group
        :return: bool
        """
        return self._visible

    @visible.setter
    def visible(self, value):
        """
        Visibility of group
        :param value: bool
        """
        self._visible = value
        # 0 is a valid key: the first text group of a collection (each CNCJob has its own collection) has key 0
        if self._index is not None:
            try:
                self._collection.data[self._index]['visible'] = value
            except KeyError as e:
                print("VisPyVisuals.TextGroup.visible --> KeyError --> %s" % str(e))
                pass
            self._collection.redraw()


class TextCollectionVisual(TextVisual):

    def __init__(self, **kwargs):
        """
        Represents collection of shapes to draw on VisPy scene
        :param kwargs: keyword arguments
            Arguments to pass for TextVisual
        """
        self.data = {}
        self.last_key = -1
        self.lock = threading.Lock()
        self.method = 'gpu'
        self._annotation_scale = None
        super(TextCollectionVisual, self).__init__(**kwargs)

        self.freeze()

    def _prepare_draw(self, view):
        result = super(TextCollectionVisual, self)._prepare_draw(view)
        if result is False:
            return False

        transform = view.transforms.get_transform('visual', 'document')
        points = transform.map([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)])
        points = points[:, :2] / points[:, 3:4]
        # The largest projected scale follows zoom without depending on camera rotation.
        scale = np.linalg.norm(points[1:] - points[0], ord=2)
        if np.isfinite(scale) and scale > 0:
            if self._annotation_scale is None:
                self._annotation_scale = scale
            zoom = min(1.0, scale / self._annotation_scale)
            self.shared_program['u_npix'] = self.font_size / 72. * self.transforms.dpi * zoom

        return result

    def add(self, text, pos, visible=True, update=True, font_size=9, color='black'):
        """
        Adds array of text to collection
        :param text: list
            Array of strings ['str1', 'str2', ... ]
        :param pos: list
            Array of string positions   [(0, 0), (10, 10), ... ]
        :param visible: bool
        |   Set True to make it visible
        :param update: bool
            Set True to redraw collection
        :param font_size: int
            Set font size to redraw collection
        :param color: string
            Set font color to redraw collection
        :return: int
            Index of array
        """
        # Get new key
        self.lock.acquire(True)
        self.last_key += 1
        key = self.last_key
        self.lock.release()

        # Prepare data for translation
        self.data[key] = {'text': text, 'pos': pos, 'visible': visible, 'font_size': font_size, 'color': color}

        if update:
            self.redraw()

        return key

    def remove(self, key, update=False):
        """
        Removes shape from collection
        :param key: int
            Shape index to remove
        :param update:
            Set True to redraw collection
        """
        del self.data[key]

        if update:
            self.__update()

    def clear(self, update=False):
        """
        Removes all shapes from collection
        :param update: bool
            Set True to redraw collection
        """
        self.data.clear()
        if update:
            self.__update()

    def __update(self):
        """
        Merges internal buffers, sets data to visuals, redraws collection on scene
        """
        labels = []
        pos = []
        font_s = 9
        color = 'black'

        # Merge buffers
        for data in list(self.data.values()):
            if data['visible']:
                try:
                    labels += data['text']
                    pos += data['pos']
                    font_s = data['font_size']
                    color = data['color']
                except Exception as e:
                    print("VisPyVisuals.TextCollectionVisual._update() --> Data error. %s" % str(e))

        # Updating text
        if len(labels) > 0:
            self.text = labels
            self.pos = pos
            self.font_size = font_s
            self.color = color
        else:
            self.text = None
            self.pos = (0, 0)

        self._bounds_changed()

    def redraw(self):
        """
        Redraws collection
        """
        self.__update()


class CursorTextVisual(TextVisual):
    """Avoid rebuilding editor cursor glyphs when text and anchors haven't changed."""

    @TextVisual.text.setter
    def text(self, value):
        value = [] if value is None else value
        if getattr(self, '_text', None) != value:
            TextVisual.text.fset(self, value)

    @TextVisual.anchors.setter
    def anchors(self, value):
        if getattr(self, '_anchors', None) != value:
            TextVisual.anchors.fset(self, value)


class CursorVisual(MarkersVisual):
    """Keep marker styling and its buffer bindings when only the cursor moves."""

    def __init__(self, *args, **kwargs):
        self._cursor_style = None
        super().__init__(*args, **kwargs)

    def set_data(self, pos=None, size=10., edge_width=None, edge_width_rel=None,
                 edge_color='black', face_color='white', symbol='o'):
        style = []
        for value in (size, edge_width, edge_width_rel,
                      getattr(edge_color, 'rgba', edge_color),
                      getattr(face_color, 'rgba', face_color), symbol):
            if isinstance(value, (np.ndarray, list, tuple)):
                array = np.asarray(value)
                value = (array.shape, array.dtype, array.tobytes())
            style.append(value)
        style = tuple(style)
        if isinstance(pos, np.ndarray) and pos.shape == (1, 2) and \
                self._data is not None and len(self._data) == 1 and \
                style == self._cursor_style:
            position = pos.astype(np.float32, copy=False)[0]
            current = self._data['a_position'][0]
            if current[0] != position[0] or current[1] != position[1] or current[2] != 0:
                current[:2] = position
                current[2] = 0
                self._vbo.set_data(self._data)
                self.events.data_updated()
                self.update()
            return

        super().set_data(pos=pos, size=size, edge_width=edge_width, edge_width_rel=edge_width_rel,
                         edge_color=edge_color, face_color=face_color, symbol=symbol)
        self._cursor_style = style


# Add 'enabled' property to visual nodes
def create_fast_node(subclass):
    # Create a new subclass of Node.

    # Decide on new class name
    clsname = subclass.__name__
    if not (clsname.endswith('Visual') and issubclass(subclass, visuals.BaseVisual)):
        raise RuntimeError('Class "%s" must end with Visual, and must '
                           'subclass BaseVisual' % clsname)
    clsname = clsname[:-6]

    # Generate new docstring based on visual docstring
    try:
        doc = generate_docstring(subclass, clsname)
    except Exception:
        # If parsing fails, just return the original Visual docstring
        doc = subclass.__doc__

    # New __init__ method
    def __init__(self, *args, **kwargs):
        parent = kwargs.pop('parent', None)
        name = kwargs.pop('name', None)
        self.name = name  # to allow __str__ before Node.__init__
        self._visual_superclass = subclass

        # parent: property,
        # _parent: attribute of Node class
        # __parent: attribute of fast_node class
        self.__parent = parent
        self._enabled = False

        subclass.__init__(self, *args, **kwargs)
        self.unfreeze()
        VisualNode.__init__(self, parent=parent, name=name)
        self.freeze()

    # Create new class
    cls = type(
        clsname, (VisualNode, subclass), {'__init__': __init__, '__doc__': doc}
    )

    # 'Enabled' property clears/restores 'parent' property of Node class
    # Scene will be painted quicker than when using 'visible' property
    def get_enabled(self):
        return self._enabled

    def set_enabled(self, enabled):
        if enabled:
            self.parent = self.__parent                 # Restore parent
        else:
            if self.parent:                             # Store parent
                self.__parent = self.parent
            self.parent = None

    cls.enabled = property(get_enabled, set_enabled)

    return cls


ShapeCollection = create_fast_node(ShapeCollectionVisual)
Rectangle = create_fast_node(RectangleVisual)
GridLines = create_fast_node(GridLinesVisual)
TextCollection = create_fast_node(TextCollectionVisual)
Cursor = create_fast_node(CursorVisual)
CursorText = create_fast_node(CursorTextVisual)
