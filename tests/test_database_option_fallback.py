"""Regression tests for mutable factory fallbacks in the tools database."""

from types import SimpleNamespace
import unittest

from appDatabase import _database_option


class Defaults:
    def __init__(self, values, factory_defaults):
        self.values = values
        self.factory_defaults = factory_defaults

    def get(self, key, default=None):
        return self.values.get(key, default)


class TestDatabaseOptionFallback(unittest.TestCase):
    def test_missing_mutable_values_are_independent_factory_copies(self):
        factory_value = {"colors": ["red"]}
        app = SimpleNamespace(
            defaults=Defaults({}, {"mutable": factory_value}),
            options={},
        )

        first = _database_option(app, "mutable")
        second = _database_option(app, "mutable")

        self.assertEqual(first, factory_value)
        self.assertIsNot(first, factory_value)
        self.assertIsNot(first, second)
        first["colors"].append("blue")
        self.assertEqual(factory_value, {"colors": ["red"]})
        self.assertEqual(second, {"colors": ["red"]})


    def test_present_falsy_default_overrides_factory_value(self):
        factory_value = ["factory"]
        default_value = []
        app = SimpleNamespace(
            defaults=Defaults({"mutable": default_value}, {"mutable": factory_value}),
            options={},
        )

        result = _database_option(app, "mutable")

        self.assertIs(result, default_value)
        self.assertIsNot(result, factory_value)


if __name__ == "__main__":
    unittest.main()
