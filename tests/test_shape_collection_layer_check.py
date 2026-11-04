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


class _FakeBatch:
    """Stands in for the pool AsyncResult. The worker runs when the batch is submitted, on copies, like a pickled call.

    forbid_block: wait() and get() fail the test, so a method that must not block shows up as an error.
    """

    def __init__(self, results, forbid_block=False):
        self._results = results
        self._forbid_block = forbid_block
        self._ready = False

    def ready(self):
        return self._ready

    def finish(self):
        self._ready = True

    def wait(self, timeout=None):
        assert not self._forbid_block, 'wait() blocks the GUI thread'
        self._ready = True

    def get(self, timeout=None):
        assert not self._forbid_block, 'get() blocks the GUI thread'
        return self._results


class _FakePool:
    def __init__(self, forbid_block=False):
        self.forbid_block = forbid_block
        self.batches = []

    def map_async(self, fn, items):
        batch = _FakeBatch([fn(dict(d)) for d in items], self.forbid_block)
        self.batches.append(batch)
        return batch


def _pooled(pool):
    return ShapeCollectionVisual(linewidth=1, layers=3, pool=pool, fcoptions={'global_backface_culling': True})


def test_pending_ready_is_false_while_batch_pending_and_true_after_it_finishes_without_blocking():
    pool = _FakePool(forbid_block=True)
    c = _pooled(pool)
    c.add_batch([{'shape': SQUARE, 'color': '#000000', 'face_color': '#ff0000'}])
    assert c.pending_ready() is False
    pool.batches[-1].finish()
    assert c.pending_ready() is True


def test_pending_ready_is_true_without_a_pending_batch():
    c = _pooled(_FakePool())
    assert c.pending_ready() is True


def test_shape_hidden_while_its_batch_is_pending_stays_hidden_after_collect():
    c = _pooled(_FakePool())
    keys = c.add_batch([{'shape': SQUARE, 'color': '#000000', 'face_color': '#ff0000'}], visible=True)
    c.update_visibility(False, indexes=keys)
    assert c.data[keys[0]]['visible'] is False

    c._collect_results()

    assert 'geometry' not in c.data[keys[0]], 'the batch result did not replace the record'
    assert c.data[keys[0]]['visible'] is False

