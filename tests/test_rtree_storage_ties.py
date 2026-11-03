"""AppRTreeStorage.nearest(): on ties the first inserted point wins, whatever way the index was built."""
import unittest

from shapely.geometry import LineString, Point

from camlib import AppRTreeStorage


def make_storage(objs, bulk):
    storage = AppRTreeStorage()
    storage.get_points = lambda g: [g.coords[0]]
    if bulk:
        storage.insert_many(objs)
    else:
        for o in objs:
            storage.insert(o)
    return storage


def walk(storage, start):
    order = []
    pt = start
    while True:
        try:
            pt, obj = storage.nearest(pt)
        except StopIteration:
            return order
        order.append(pt)
        storage.remove(obj)


class TestRTreeStorageTies(unittest.TestCase):

    def test_ties_give_lowest_id(self):
        # 4 points at distance 1 from the origin
        pts = [(1, 0), (0, 1), (-1, 0), (0, -1)]
        for bulk in (False, True):
            objs = [Point(p) for p in pts]
            storage = make_storage(objs, bulk)
            pt, obj = storage.nearest((0, 0))
            self.assertIs(obj, objs[0], 'bulk=%s' % bulk)
            self.assertEqual(pt, (1, 0))
            objs = [LineString([p, (p[0] + 5, p[1] + 5)]) for p in pts]
            storage = make_storage(objs, bulk)
            self.assertIs(storage.nearest((0, 0))[1], objs[0])

    def test_grid_walk_same_for_both_builds(self):
        pts = [(x * 1.0, y * 1.0) for y in range(30) for x in range(30)]
        orders = [walk(make_storage([Point(p) for p in pts], bulk), (0.0, 0.0)) for bulk in (False, True)]
        self.assertEqual(len(orders[0]), 900)
        self.assertEqual(orders[0], orders[1])


if __name__ == '__main__':
    unittest.main()
