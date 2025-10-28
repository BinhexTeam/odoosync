import os
import sys
import types
import unittest
from collections import defaultdict

pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if pkg_root not in sys.path:
    sys.path.insert(0, pkg_root)
sys.modules.pop('odoosync', None)
sys.modules.pop('odoosync.ModelSyncer', None)

from odoosync.ModelSyncer import ModelSyncer


class ModelStub:
    def __init__(self, name, dependency_rel_fields, records):
        self.name = name
        self.dependency_rel_fields = dependency_rel_fields
        self.records = list(records)
        self.record_ids = {rec['id'] for rec in self.records}
        self.translatable_ids = set()
        self.many2onefields = {}
        self.load_calls = []

    def load_recs(self, odoo_instance, ids, dep=False, chunk_size=None):
        ids = list(ids)
        self.load_calls.append((tuple(sorted(ids)), dep))
        records = [{"id": _id} for _id in ids]
        if dep:
            for record in records:
                record['__sfit_dep'] = True
        self.records.extend(records)
        self.record_ids.update(ids)
        return records


class DependencyRelationTraversalTests(unittest.TestCase):

    def setUp(self):
        if 'odoorpc' not in sys.modules:
            odoorpc_stub = types.ModuleType('odoorpc')
            odoorpc_stub.error = types.SimpleNamespace(RPCError=Exception)
            odoorpc_stub.ODOO = object
            sys.modules['odoorpc'] = odoorpc_stub

    def test_one2many_and_many2many_dependencies_loaded(self):
        syncer = ModelSyncer.__new__(ModelSyncer)
        syncer.batch_size = None

        parent_model = ModelStub(
            'res.partner',
            {
                'child_ids': {'relation': 'res.partner', 'type': 'one2many'},
                'category_id': {'relation': 'res.partner.category', 'type': 'many2many'},
            },
            [{'id': 1, 'child_ids': [2, 3], 'category_id': [4, 5]}],
        )
        category_model = ModelStub('res.partner.category', {}, [])

        other_models = {
            'res.partner': parent_model,
            'res.partner.category': category_model,
        }
        loaded = {'res.partner': parent_model.records}

        captured = defaultdict(set)

        def add_translations(dep_struct):
            for model_name, ids in dep_struct.items():
                captured[model_name].update(ids)

        syncer._load_dependencies_of_records(object(), loaded, other_models, add_translations)

        self.assertIn('res.partner', captured)
        self.assertIn('res.partner.category', captured)
        self.assertEqual(captured['res.partner'], {2, 3})
        self.assertEqual(captured['res.partner.category'], {4, 5})
        self.assertIn(((2, 3), True), parent_model.load_calls)
        self.assertIn(((4, 5), True), category_model.load_calls)


if __name__ == '__main__':
    unittest.main()
