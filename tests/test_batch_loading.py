
import os
import sys
import types
import unittest

pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if pkg_root not in sys.path:
    sys.path.insert(0, pkg_root)
sys.modules.pop('odoosync', None)
sys.modules.pop('odoosync.models', None)

from odoosync.models import OdooModel


class DummySource:
    def __init__(self):
        self.read_calls = []

    def read(self, ids, fields):
        self.read_calls.append(list(ids))
        return [{"id": record_id} for record_id in ids]


class BatchLoadingTests(unittest.TestCase):
    def test_load_recs_splits_batches(self):
        model = OdooModel({"model": "res.partner"})
        model.fields = ["id"]
        dummy_source = DummySource()
        dummy_odoo = types.SimpleNamespace(env={"res.partner": dummy_source})

        records = model.load_recs(dummy_odoo, list(range(1, 6)), chunk_size=2)

        self.assertEqual(dummy_source.read_calls, [[1, 2], [3, 4], [5]])
        self.assertEqual({rec["id"] for rec in records}, {1, 2, 3, 4, 5})
        self.assertEqual(model.record_ids, {1, 2, 3, 4, 5})


if __name__ == "__main__":
    unittest.main()
