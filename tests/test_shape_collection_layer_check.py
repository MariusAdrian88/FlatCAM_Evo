"""ShapeCollectionVisual.add / add_batch reject a layer outside 0..len(meshes)-1 with ValueError.

A rejected call changes nothing: data, last_key and the line width stay as they were and key_lock stays free.
"""

import pytest
from shapely.geometry import Polygon

from appGUI.VisPyVisuals import ShapeCollectionVisual

SQUARE = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
OPTIONS = {"global_graphic_engine_3d_no_mp": True, "global_backface_culling": True}


@pytest.fixture
def coll():
    return ShapeCollectionVisual(linewidth=1, layers=3, pool=None, fcoptions=OPTIONS)


def _state(c):
    return dict(c.data), c.last_key, c._line_width


def _assert_lock_free(c):
    assert c.key_lock.acquire(False), 'key_lock is still held'
    c.key_lock.release()


@pytest.mark.parametrize('layer', [3, None])
def test_add_rejects_bad_layer(coll, layer):
    before = _state(coll)
    with pytest.raises(ValueError):
        coll.add(shape=SQUARE, layer=layer)
    assert _state(coll) == before
    _assert_lock_free(coll)
    key = coll.add(shape=SQUARE, layer=2)
    assert coll.data[key]['layer'] == 2

