"""
The translations are found without a failed first try: from the source the locale folder is next to appTranslation.py,
frozen with cx_Freeze appTranslation is inside lib/library.zip and the folder is lib/locale.
"""

import gettext
import os

import appTranslation


def test_frozen_the_folder_next_to_library_zip(tmp_path, monkeypatch):
    lib = tmp_path / 'lib'
    (lib / 'locale' / 'en' / 'LC_MESSAGES').mkdir(parents=True)
    monkeypatch.setattr(appTranslation, '__file__', str(lib / 'library.zip' / 'appTranslation.pyc'))
    assert appTranslation.locale_dir() == str(lib / 'locale')
    assert 'en' in appTranslation.load_languages()


def test_the_english_translation_is_found_at_the_first_try():
    assert gettext.find('strings', localedir=appTranslation.locale_dir(), languages=['en'])
