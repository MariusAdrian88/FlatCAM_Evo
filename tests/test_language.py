"""
The language names and the fall back to English (appTranslation.languages_dict, apply_language()).

    python -m pytest tests/test_language.py
"""

import builtins
import unittest
import unittest.mock

import appTranslation as fcTranslate


class TestLanguageNames(unittest.TestCase):
    def test_each_name_is_written_in_its_own_language(self):
        self.assertEqual('Deutsch', fcTranslate.languages_dict['de'])
        self.assertEqual('Português do Brasil', fcTranslate.languages_dict['pt_BR'])
        self.assertEqual('Türkçe', fcTranslate.languages_dict['tr'])
        # the Russian name is all Cyrillic (it began with a Latin 'P')
        self.assertEqual('Русский', fcTranslate.languages_dict['ru'])
        self.assertTrue(all('Ѐ' <= ch <= 'ӿ' for ch in fcTranslate.languages_dict['ru']))

class TestApplyLanguage(unittest.TestCase):
    def setUp(self):
        self._saved = builtins.__dict__.get('_')
        self.addCleanup(self._restore)

    def _restore(self):
        fcTranslate.apply_language('strings', lang='English')
        if self._saved is not None:
            builtins.__dict__['_'] = self._saved

    def test_an_unknown_name_falls_back_to_english(self):
        # names written by older versions and names of languages without a translation
        self.assertEqual('Deutsch', fcTranslate.apply_language('strings', lang='Deutsch'))
        for name in ('Deutsche', 'Türk', 'Portugues do Brasil', 'Pусский', 'Klingon'):
            with self.subTest(name=name):
                self.assertEqual('English', fcTranslate.apply_language('strings', lang=name))
                self.assertEqual('Preferences', builtins.__dict__['_']('Preferences'))

    def _fake_settings(self, value):
        store = {'language': value}

        class FakeSettings:
            def __init__(self, *args):
                pass

            def contains(self, key):
                return key in store

            def value(self, key, *args, **kwargs):
                return store[key]

            def setValue(self, key, val):
                store[key] = val

        patcher = unittest.mock.patch.object(fcTranslate, 'QSettings', FakeSettings)
        patcher.start()
        self.addCleanup(patcher.stop)
        return store

    def test_an_unknown_name_in_the_settings_is_written_back_as_english(self):
        # else Preferences -> Apply of English compares with the old name and asks for a restart
        store = self._fake_settings('Deutsche')
        self.assertEqual('English', fcTranslate.apply_language('strings'))
        self.assertEqual('English', store['language'])

if __name__ == '__main__':
    unittest.main()
