import json
import lzma
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shapely.geometry import Point

from appCommon import ProjectFile
from appCommon.ProjectFile import ZSTD_MAGIC, load_project_file, open_project_stream, save_project_file
from camlib import to_dict, dict2obj, geometry_wkt_cache, project_json_default
from appHandlers.appIO import appIO
from types import SimpleNamespace
from unittest.mock import Mock
from tests.helpers_objects import (  # noqa: F401,E402
    app, assert_coords_close, assert_poly_equiv, assert_same_tools, _headless_qt, make_app, make_excellon,
    make_gerber, save_and_load,
)

try:
    import zstandard
except ImportError:
    zstandard = None


def project():
    return {"objs": [{"options": {"name": "a"}, "tools": {1: {"solid_geometry": [Point(1, 2).buffer(1)]}}}],
            "options": {"units": "MM"}, "version": 9.0}


class TestProjectFile(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.filename = os.path.join(self.tmp.name, 'p.FlatPrj')

    def check_round_trip(self, compression_level):
        save_project_file(self.filename, project(), default=to_dict, compression_level=compression_level)
        self.assertFalse(os.path.exists(self.filename + '.tmp'))
        loaded = load_project_file(self.filename, object_hook=dict2obj)
        geo = loaded['objs'][0]['tools']['1']['solid_geometry'][0]
        self.assertTrue(geo.equals_exact(Point(1, 2).buffer(1), 1e-9))
        self.assertEqual(loaded['options'], {"units": "MM"})
        return loaded


    def test_zstd_round_trip(self):
        self.check_round_trip(3)
        with open(self.filename, 'rb') as f:
            self.assertEqual(f.read(4), ZSTD_MAGIC)


    def test_legacy_lzma_project_loads(self):
        data = json.dumps(project(), default=to_dict, indent=2, sort_keys=True).encode('utf-8')
        with open(self.filename, 'wb') as f:
            f.write(lzma.compress(data, preset=3))
        loaded = load_project_file(self.filename, object_hook=dict2obj)
        self.assertEqual(loaded['options'], {"units": "MM"})

    def test_equal_geometries_are_saved_once_and_loaded_shared(self):
        circle = Point(1, 2).buffer(1)
        data = {"objs": [{"solid_geometry": [circle], "tools": {1: {"solid_geometry": [circle, Point(5, 5).buffer(1)]}},
                          "follow": [Point(1, 2).buffer(1)]}], "version": 9.0}
        for level in (None, 3):
            with self.subTest(level=level):
                save_project_file(self.filename, data, default=project_json_default(geometry_wkt_cache(data)),
                                  compression_level=level)
                with open_project_stream(self.filename) as stream:
                    raw = json.load(stream)
                self.assertEqual(len(raw['geometry_table']), 2)
                obj = load_project_file(self.filename, object_hook=dict2obj)['objs'][0]
                self.assertNotIn('geometry_table', obj)
                self.assertIs(obj['solid_geometry'][0], obj['tools']['1']['solid_geometry'][0])
                self.assertIs(obj['solid_geometry'][0], obj['follow'][0])
                self.assertTrue(obj['solid_geometry'][0].equals_exact(circle, 0))

    def test_project_over_the_decompressed_size_cap_is_refused(self):
        save_project_file(self.filename, project(), default=to_dict, compression_level=3)
        with patch.object(ProjectFile, 'MAX_DECOMPRESSED_BYTES', 50):
            with self.assertRaises(ProjectFile.ProjectTooLargeError):
                load_project_file(self.filename, object_hook=dict2obj)
        self.assertEqual(load_project_file(self.filename, object_hook=dict2obj)['version'], 9.0)

    def test_failed_save_keeps_the_old_file(self):
        save_project_file(self.filename, project(), default=to_dict, compression_level=3)
        with open(self.filename, 'rb') as f:
            old = f.read()
        for level in (None, 3):
            with self.subTest(level=level):
                # to_dict() returns the unknown object, so json reports a circular reference
                with self.assertRaises(ValueError):
                    save_project_file(self.filename, {"objs": [object()]}, default=to_dict, compression_level=level)
                with open(self.filename, 'rb') as f:
                    self.assertEqual(f.read(), old)
                self.assertFalse(os.path.exists(self.filename + '.tmp'))

    def test_bad_content_raises(self):
        with open(self.filename, 'wb') as f:
            f.write(b'not JSON')
        with self.assertRaises(ValueError):
            load_project_file(self.filename)
        with open(self.filename, 'wb') as f:
            f.write(ZSTD_MAGIC + b'broken')
        with self.assertRaises(Exception):
            load_project_file(self.filename)


if __name__ == '__main__':
    unittest.main()


def test_saved_project_has_only_project_options(tmp_path):
    from defaults import is_project_key
    app = make_app()
    proj, _restored = save_and_load(app, [], tmp_path)
    assert 'units' in proj['options']
    assert 'global_save_compressed' not in proj['options']
    assert all(is_project_key(key) for key in proj['options'])


def test_save_project_reports_success_and_failure(tmp_path):
    app = make_app()
    io = appIO(app)
    app.collection.get_list.return_value = []
    app.collection.get_active.return_value = None
    # a real failure: the folder does not exist, so open() fails
    assert io.save_project(str(tmp_path / 'missing' / 'bad.FlatPrj'), silent=True) is False
    assert app.save_in_progress is False
    assert io.save_project(str(tmp_path / 'good.FlatPrj'), silent=True) is True


def test_save_project_object_serialization_error_is_a_failure(tmp_path):
    app = make_app()
    io = appIO(app)
    app.collection.get_list.return_value = [SimpleNamespace(to_dict=Mock(side_effect=ValueError('bad object')))]
    app.collection.get_active.return_value = None
    path = tmp_path / 'bad_object.FlatPrj'
    assert io.save_project(str(path), silent=True) is False
    assert app.save_in_progress is False
    assert not path.exists()


class TestProjectRoundTrip:
    def test_gerber_and_excellon_survive_save_and_restore(self, app, tmp_path):
        grb = make_gerber(app, name='grb')
        exc = make_excellon(app, name='exc')
        for t, data in ((1, {'cutz': -1.7, 'tag': 'a'}), (2, {'cutz': -2.0}), (3, {'cutz': -0.5, 'multidepth': True})):
            exc.tools[t]['data'] = data
        proj, restored = save_and_load(app, [grb, exc], tmp_path)
        assert [o['kind'] for o in proj['objs']] == ['gerber', 'excellon']
        assert len(restored) == 2
        grb2, exc2 = restored
        assert (grb2.kind, exc2.kind) == ('gerber', 'excellon')

        # Gerber
        assert grb2.units == grb.units == 'MM'
        assert (grb2.int_digits, grb2.frac_digits) == (4, 6)
        assert grb2.source_file == grb.source_file
        assert grb2.obj_options['name'] == 'grb'
        assert_poly_equiv(grb2.solid_geometry, grb.solid_geometry, rel=1e-6, abs_tol=1e-4, msg='gerber project')
        assert_coords_close(grb2.follow_geometry, grb.follow_geometry, atol=2e-6)
        assert set(grb2.aperture_macros) == {'OUTL', 'RING'}
        for name, macro in grb.aperture_macros.items():
            assert (grb2.aperture_macros[name].name, grb2.aperture_macros[name].raw) == (macro.name, macro.raw)
        assert_same_tools(grb.tools, grb2.tools, 'gerber')
        assert all(isinstance(k, int) for k in grb2.tools)

        # Excellon
        assert exc2.units == exc.units == 'MM'
        assert exc2.zeros == exc.zeros
        assert exc2.obj_options['name'] == 'exc'
        assert_same_tools(exc.tools, exc2.tools, 'excellon')
        assert exc2.tools[3]['data'] == {'cutz': -0.5, 'multidepth': True}
        assert exc2.tools[1]['data'] == {'cutz': -1.7, 'tag': 'a'}
        assert_poly_equiv(exc2.solid_geometry, exc.solid_geometry, rel=1e-6, abs_tol=1e-4, msg='excellon project')


