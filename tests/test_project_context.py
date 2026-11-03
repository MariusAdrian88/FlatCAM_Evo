"""Tests for ProjectContext and its helper functions."""

import os
import unittest

from appObjects.ProjectContext import ProjectContext, unique_name, same_path, name_from_filename, \
    is_valid_name


class TestProjectContext(unittest.TestCase):

    def test_unique_name_adds_number(self):
        self.assertEqual(unique_name('Board', ['Board']), 'Board_1')

    def test_same_path(self):
        self.assertTrue(same_path('a/b/../c.FlatPrj', 'a/c.FlatPrj'))
        self.assertFalse(same_path('a/c.FlatPrj', 'a/d.FlatPrj'))
        self.assertFalse(same_path(None, 'a/d.FlatPrj'))


    def test_is_valid_name(self):
        self.assertTrue(is_valid_name('Board A'))
        self.assertFalse(is_valid_name(''))
        self.assertFalse(is_valid_name('   '))
        for char in '\\/:*?"<>|':
            self.assertFalse(is_valid_name('Bo%sard' % char))


if __name__ == '__main__':
    unittest.main()
