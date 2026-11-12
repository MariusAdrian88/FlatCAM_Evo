"""
scripts/i18n_tool.py: extraction of the strings, .po read / write / merge, .mo compilation, placeholder checks.

    python tests/test_i18n_tool.py
"""

import contextlib
import functools
import gettext
import io
import os
import shutil
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import i18n_tool as t       # noqa: E402


@functools.lru_cache(maxsize=None)
def extract_program_strings():
    """1 extraction of the strings of the program (about 4 seconds) for the tests that only read the result."""
    return t.extract()


PO = textwrap.dedent('''\
    msgid ""
    msgstr ""
    "Content-Type: text/plain; charset=UTF-8\\n"
    "Language: tr_TR\\n"

    #: a.py:1
    msgid "Title"
    msgstr "Başlık"

    #: a.py:2
    msgid ""
    "Two lines.\\n"
    "Second \\"quoted\\" line"
    msgstr ""
    "İki satır.\\n"
    "İkinci \\"tırnaklı\\" satır"

    #: a.py:3
    #, fuzzy
    #| msgid "Fuzzy"
    msgid "Fuzzy one"
    msgstr "Belirsiz"

    #: a.py:4
    msgid "Gone"
    msgstr "Gitti"

    # a translator comment
    #~| msgid "Older"
    #~ msgid "Old"
    #~ msgstr "Eski"
    ''')


class TestPo(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.po = os.path.join(self.dir, 'strings.po')
        with open(self.po, 'w', encoding='utf-8') as f:
            f.write(PO)

    def test_write_read_round_trip(self):
        header, entries = t.read_po(self.po)
        entries.append(t.Entry('A long string ' * 12 + 'with\ttab and back\\slash', 'Uzun ' * 30, ['b.py:9']))
        out = os.path.join(self.dir, 'out.po')
        t.write_po(out, header, entries)
        h2, e2 = t.read_po(out)
        self.assertEqual(header.msgstr, h2.msgstr)
        self.assertEqual([(e.msgid, e.msgstr, e.obsolete, e.flags, e.previous) for e in entries],
                         [(e.msgid, e.msgstr, e.obsolete, e.flags, e.previous) for e in e2])
        with open(out, encoding='utf-8') as f:
            text = f.read()
        self.assertTrue(all(len(line) <= 81 for line in text.splitlines() if not line.startswith('#')))
        self.assertIn('#, fuzzy\n#| msgid "Fuzzy"\nmsgid "Fuzzy one"\n', text)
        self.assertIn('#~| msgid "Older"\n#~ msgid "Old"\n', text)

    def test_compile_skips_fuzzy_and_obsolete(self):
        mo = os.path.join(self.dir, 'strings.mo')
        self.assertEqual(3, t.compile_mo(self.po, mo))
        with open(mo, 'rb') as f:
            catalog = gettext.GNUTranslations(f)
        self.assertEqual('Başlık', catalog.gettext('Title'))
        self.assertEqual('İki satır.\nİkinci "tırnaklı" satır', catalog.gettext('Two lines.\nSecond "quoted" line'))
        self.assertEqual('Fuzzy one', catalog.gettext('Fuzzy one'))
        self.assertEqual('Old', catalog.gettext('Old'))

    def test_compile_does_not_overwrite_a_catalog_when_po_syntax_is_invalid(self):
        mo = os.path.join(self.dir, 'strings.mo')
        t.compile_mo(self.po, mo)
        with open(mo, 'rb') as f:
            original = f.read()
        with open(self.po, 'w', encoding='utf-8') as f:
            f.write('msgid "Title"\nmsgstr "Titel\n')
        with self.assertRaises(ValueError):
            t.compile_mo(self.po, mo)
        with open(mo, 'rb') as f:
            self.assertEqual(original, f.read())

    def test_merge(self):
        lang_dir = os.path.join(self.dir, 'locale', 'xx', 'LC_MESSAGES')
        os.makedirs(lang_dir)
        shutil.copy(self.po, os.path.join(lang_dir, 'strings.po'))
        old_root = t.ROOT
        t.ROOT = self.dir
        try:
            strings = {'Title': ['a.py:10'], 'Fuzzy one': ['a.py:11'], 'Old': ['a.py:12'], 'New text': ['a.py:13']}
            counts = t.merge('xx', strings)
            header, entries = t.read_po(os.path.join(lang_dir, 'strings.po'))
        finally:
            t.ROOT = old_root
        self.assertEqual({'kept': 2, 'revived': 1, 'new': 1, 'obsolete': 2}, counts)
        active = {e.msgid: e for e in entries if not e.obsolete}
        self.assertEqual(['Title', 'Fuzzy one', 'Old', 'New text'], list(active))
        self.assertEqual('Eski', active['Old'].msgstr)
        self.assertEqual(['a.py:10'], active['Title'].refs)
        self.assertEqual('', active['New text'].msgstr)
        # the fuzzy entry keeps its flag and its previous English text, the revived one too
        self.assertTrue(active['Fuzzy one'].fuzzy)
        self.assertEqual(['msgid "Fuzzy"'], active['Fuzzy one'].previous)
        self.assertEqual(['msgid "Older"'], active['Old'].previous)
        self.assertEqual({'Two lines.\nSecond "quoted" line', 'Gone'}, {e.msgid for e in entries if e.obsolete})


class TestTranslatorTools(unittest.TestCase):
    """init, stats, glossary and the .mo check, in a throw-away locale folder."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.old_root = t.ROOT
        t.ROOT = self.dir
        self.addCleanup(setattr, t, 'ROOT', self.old_root)
        self.lang_dir = os.path.join(self.dir, 'locale', 'xx', 'LC_MESSAGES')
        os.makedirs(self.lang_dir)
        with open(os.path.join(self.lang_dir, 'strings.po'), 'w', encoding='utf-8') as f:
            f.write(PO)
        self.template = os.path.join(self.dir, 'strings.pot')
        t.write_template({'Title': ['a.py:1'], 'Tools Table:': ['a.py:2']}, self.template)

    def test_check_fails_on_errors_only(self):
        def run_check():
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = t.main(['check', 'xx'])
            return code, out.getvalue()

        with open(t.po_path('xx'), encoding='utf-8') as f:
            text = f.read()
        self.assertEqual(0, run_check()[0])
        # a warning: the program shows the added tag as it is
        with open(t.po_path('xx'), 'w', encoding='utf-8') as f:
            f.write(text.replace('msgstr "Gitti"', 'msgstr "<i>Gitti</i>"'))
        code, out = run_check()
        self.assertEqual(0, code)
        self.assertIn("warning: 'Gone': HTML tags differ", out)
        self.assertEqual((0, 1), (t.stats('xx')['errors'], t.stats('xx')['warnings']))
        # an error: the translation lost the %s, formatting it raises "not all arguments converted"
        with open(t.po_path('xx'), 'w', encoding='utf-8') as f:
            f.write(text.replace('msgid "Gone"', 'msgid "Gone %s"'))
        code, out = run_check()
        self.assertEqual(1, code)
        self.assertIn("error: 'Gone %s': formatting with % fails", out)
        self.assertEqual((1, 0), (t.stats('xx')['errors'], t.stats('xx')['warnings']))


class TestExtract(unittest.TestCase):
    def test_literals_and_problems(self):
        src = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, src, True)
        with open(os.path.join(src, 'mod.py'), 'w', encoding='utf-8') as f:
            f.write(textwrap.dedent('''\
                a = _("First")
                b = _("Implicit " "concatenation")
                c = _("First")
                d = _(name)
                e = _("Bad %s" % 3)
                g = _("")
                '''))
        os.makedirs(os.path.join(src, 'tests'))
        with open(os.path.join(src, 'tests', 'skip.py'), 'w', encoding='utf-8') as f:
            f.write('x = _("Not extracted")\n')
        strings, problems = t.extract(src)
        self.assertEqual(['First', 'Implicit concatenation'], list(strings))
        self.assertEqual(['mod.py:1', 'mod.py:3'], strings['First'])
        self.assertEqual(2, len(problems))

if __name__ == '__main__':
    unittest.main()
