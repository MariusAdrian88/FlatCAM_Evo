"""
appCommon.SafeEval.safe_eval(): the values that eval() was used for, without running code.

A Gerber aperture macro, a project or preferences option, the clipboard and the entry fields were read with eval():
opening a Gerber made by someone else or pasting a text could run any Python code.

    python tests/test_safe_eval.py
"""

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6 import QtWidgets     # noqa: E402

from appCommon.SafeEval import safe_eval      # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# when it runs, this code puts MARKER in sys.modules; it has no '*', ',' or 'x' (the Gerber macro parser splits the
# text on '*' and ',' and changes 'x' to '*')
MARKER = 'flatcam_safe_eval_marker'
CODE = "__import__('sys').modules.setdefault('%s')" % MARKER

def parse_gerber(macro):
    """Gerber.parse_file() of a file with the aperture macro M1 flashed 1 time at (1, 2): (the Gerber, the result)"""
    from appParsers.ParseGerber import Gerber

    options = {
        'gerber_def_units': 'MM', 'gerber_def_zeros': 'L', 'gerber_circle_steps': 64,
        'gerber_simp_tolerance': 0.001, 'gerber_simplification': True, 'gerber_buffering': True,
        'gerber_extra_buffering': 0.0, 'gerber_clean_apertures': True, 'gerber_use_buffer_for_union': True,
        'global_tolerance': 0.01,
    }
    app = SimpleNamespace(
        options=options, decimals=4, abort_flag=False, log=Mock(), inform=Mock(), app_units='MM',
        plotcanvas=SimpleNamespace(new_shape_collection=Mock()), proc_container=Mock(),
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'macro.gbr')
        with open(path, 'w') as f:
            f.write('%FSLAX46Y46*%\n%MOMM*%\n' + macro + '\n%ADD10M1,0.5*%\nD10*\nX1000000Y2000000D03*\nM02*\n')
        gbr = Gerber(app)
        return gbr, gbr.parse_file(path)


class MarkerCase(unittest.TestCase):
    def setUp(self):
        sys.modules.pop(MARKER, None)
        self.addCleanup(sys.modules.pop, MARKER, None)

    def assertCodeDidNotRun(self):
        self.assertFalse(MARKER in sys.modules, 'the code in the text was run')


class TestValues(unittest.TestCase):
    def test_literals(self):
        self.assertEqual(0.1, safe_eval('0.1'))
        self.assertEqual(-3, safe_eval(' -3 '))
        self.assertEqual((0.1, 0.2), safe_eval('0.1, 0.2'))
        self.assertEqual((1.0, 2.5), safe_eval('(1.0, 2.5)'))
        self.assertEqual([1, 2], safe_eval('[1, 2]'))
        self.assertEqual({'a': 1}, safe_eval("{'a': 1}"))
        self.assertIs(True, safe_eval('True'))
        self.assertIsNone(safe_eval('None'))
        self.assertEqual('text', safe_eval("'text'"))
        self.assertEqual(1e-3, safe_eval('1e-3'))
        self.assertEqual(2.5, safe_eval(2.5))

        self.assertAlmostEqual(10 / 3, safe_eval('10/3'))
        self.assertAlmostEqual(0.6, safe_eval('0.5*1+0.1'))
        self.assertEqual(-0.5, safe_eval('-(0.25+0.25)'))
        self.assertEqual(0.5, safe_eval('--0.5'))
        self.assertEqual(8, safe_eval('2**3'))
        self.assertEqual(1, safe_eval('7 % 3'))
        self.assertEqual(3, safe_eval('7 // 2'))
        self.assertTrue(bool(safe_eval('1')))
        self.assertFalse(bool(safe_eval('0')))

    def test_code_is_refused(self):
        for text in ("__import__('os').system('echo hacked')", 'open("x")', 'x', 'a.b', '[1][0]',
                     '(lambda: 1)()', '[i for i in range(3)]', "'a' * 3", '"a" + "b"', '9**9**9', '1/0',
                     '{**{}}', 'True + 1', '1 if 1 else 2', '1 < 2', '2 ^ 3', '1j // 2', ''):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    safe_eval(text)

    def test_too_long_or_too_deep_is_refused(self):
        for text in ('1+' * 49999 + '1', '-' * 99990 + '1', '(' * 300 + '1' + ')' * 300, '1' * 100001):
            with self.subTest(text=text[:10]):
                with self.assertRaises(ValueError):
                    safe_eval(text)

class TestIntegerSize(unittest.TestCase):
    """A product or a power of integers above MAX_INTEGER_BITS (4096) is refused before it is calculated."""

    def test_long_multiplication_is_refused(self):
        # 2**1000 has 1001 bits: a product of 4 fits in 4096 bits, a product of 5 does not
        self.assertEqual(2 ** 4000, safe_eval('*'.join(['2**1000'] * 4)))
        for text in ('*'.join(['2**1000'] * 5), '*'.join(['4294967295'] * 200), '(2**1000)**1000'):
            with self.subTest(text=text[:20]):
                with self.assertRaises(ValueError):
                    safe_eval(text)

    def test_exponent_limit_is_kept(self):
        for text in ('2**1001', '1**1001', '0.5**-1001'):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    safe_eval(text)


class TestGerberMacro(MarkerCase):
    """Opening a Gerber file: the aperture macro expressions are values, not code."""

    parse = staticmethod(parse_gerber)

    def test_macro_arithmetic_still_works(self):
        # $2 = $1 x 2 + 0.25 = 1.25: a circle of 1.25 mm flashed at (1, 2)
        gbr, result = self.parse('%AMM1*$2=$1x2+0.25*1,1,$2,0,0*%')
        self.assertIsNone(result)
        circle = gbr.tools[10]['geometry'][0]['solid']
        self.assertAlmostEqual(1.25, circle.bounds[2] - circle.bounds[0], places=3)
        self.assertAlmostEqual(1.0, circle.centroid.x, places=6)
        self.assertAlmostEqual(2.0, circle.centroid.y, places=6)

    def test_code_in_a_macro_primitive_does_not_run(self):
        gbr, result = self.parse('%%AMM1*1,1,$1,0,%s or 0*%%' % CODE)
        self.assertCodeDidNotRun()
        self.assertEqual('fail', result)

    def test_huge_integer_in_a_macro_fails_quickly(self):
        # ((2**1000)**1000)**1000 has 10**9 bits: without the integer size limit this parse takes 8 s (0.5 GB) on a
        # desktop PC, with 3 instead of 2 more than 5 minutes. With the limit it takes about 0.02 s.
        # A big integer calculation cannot be interrupted: run it in a daemon thread and fail at the timeout.
        outcome = []
        worker = threading.Thread(
            target=lambda: outcome.append(self.parse('%AMM1*1,1,((2xx1000)xx1000)xx1000,0,0*%')[1]), daemon=True)
        start = time.perf_counter()
        worker.start()
        worker.join(20)
        self.assertFalse(worker.is_alive(), 'the parse did not end in 20 s')
        self.assertEqual(['fail'], outcome)
        self.assertLess(time.perf_counter() - start, 2)
class TestUserText(MarkerCase):
    """The entry fields and the clipboard."""

    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_entries_evaluate_arithmetic_and_refuse_code(self):
        from appGUI.GUIElements import FloatEntry, EvalEntry, NumericalEvalTupleEntry

        for cls, text, value in ((FloatEntry, '10/4', 2.5), (EvalEntry, '0.1, 0.2', (0.1, 0.2)),
                                 (NumericalEvalTupleEntry, '(1.5, -2.0)', (1.5, -2.0))):
            with self.subTest(entry=cls.__name__):
                entry = cls()
                self.addCleanup(entry.deleteLater)
                entry.setText(text)
                self.assertEqual(value, entry.get_value())
                entry.setText(CODE)
                self.assertIsNone(entry.get_value())
        self.assertCodeDidNotRun()

if __name__ == '__main__':
    unittest.main()
