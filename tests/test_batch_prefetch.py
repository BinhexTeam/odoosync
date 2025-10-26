import os
import sys
import types
import unittest
from collections import defaultdict

pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if pkg_root not in sys.path:
    sys.path.insert(0, pkg_root)
sys.modules.pop('odoosync', None)
sys.modules.pop('odoosync.sync', None)
sys.modules.pop('odoosync.models', None)

from odoosync.models import OdooModel
from odoosync.sync import ModelSyncer


class StubModelData:
    def __init__(self, records):
        self.records = list(records)
        self.search_calls = []
        self.read_calls = []

    def search(self, domain):
        self.search_calls.append(domain)
        res_ids = None
        module = None
        names = None
        model = None
        for item in domain:
            field, operator, value = item
            if field == 'res_id' and operator == 'in':
                res_ids = set(value)
            elif field == 'module' and operator == '=':
                module = value
            elif field == 'name' and operator == 'in':
                names = set(value)
            elif field == 'model' and operator == '=':
                model = value
        matched = []
        for record in self.records:
            if res_ids is not None and record.get('res_id') not in res_ids:
                continue
            if module is not None and record.get('module') != module:
                continue
            if names is not None and record.get('name') not in names:
                continue
            if model is not None and record.get('model') != model:
                continue
            matched.append(record['id'])
        return matched

    def read(self, ids, fields):
        self.read_calls.append((list(ids), list(fields)))
        result = []
        for record in self.records:
            if record['id'] not in ids:
                continue
            result.append({field: record.get(field) for field in fields})
        return result


class BatchedLookupTests(unittest.TestCase):
    def _build_syncer(self, batch_size=2):
        syncer = ModelSyncer.__new__(ModelSyncer)
        syncer.record_id_map_forward = {}
        syncer.record_id_map_reverse = {}
        syncer._external_translations = defaultdict(dict)
        syncer._source_xmlid_cache = defaultdict(dict)
        syncer._dest_xmlid_cache = defaultdict(dict)
        syncer.batch_size = batch_size
        syncer.auto_xmlid_lookup = True
        return syncer

    def test_prefetch_destination_ids_batches_source_queries(self):
        source_records = [
            {'id': 1, 'model': 'res.partner', 'res_id': 10, 'module': 'base', 'name': 'partner10'},
            {'id': 2, 'model': 'res.partner', 'res_id': 11, 'module': 'base', 'name': 'partner11'},
            {'id': 3, 'model': 'res.partner', 'res_id': 13, 'module': 'base', 'name': 'partner13'},
        ]
        dest_records = [
            {'id': 10, 'model': 'res.partner', 'res_id': 1000, 'module': 'base', 'name': 'partner10'},
            {'id': 11, 'model': 'res.partner', 'res_id': 1100, 'module': 'base', 'name': 'partner11'},
            {'id': 12, 'model': 'res.partner', 'res_id': 1300, 'module': 'base', 'name': 'partner13'},
        ]
        source_model_data = StubModelData(source_records)
        dest_model_data = StubModelData(dest_records)

        syncer = self._build_syncer(batch_size=2)
        syncer.source = types.SimpleNamespace(odoo=types.SimpleNamespace(env={'ir.model.data': source_model_data}))
        syncer.dest = types.SimpleNamespace(ir_model_obj=dest_model_data)

        model = OdooModel({'model': 'res.partner'})
        syncer.models_by_name = {model.name: model}

        records = [{'id': 10}, {'id': 11}, {'id': 12}, {'id': 13}, {'id': 14}]
        syncer._prefetch_destination_ids(model, records)

        self.assertEqual(len(source_model_data.search_calls), 3)
        self.assertEqual(len(dest_model_data.search_calls), 1)
        self.assertEqual(model.trans.get(10), 1000)
        self.assertEqual(model.trans.get(11), 1100)
        self.assertEqual(model.trans.get(13), 1300)
        self.assertNotIn(12, model.trans)
        self.assertNotIn(14, model.trans)

        cache = syncer._source_xmlid_cache[model.name]
        self.assertIsNone(cache.get(12))
        self.assertIsNone(cache.get(14))

    def test_prefetch_destination_ids_uses_cache_on_repeated_calls(self):
        source_records = [
            {'id': 1, 'model': 'res.partner', 'res_id': 21, 'module': 'base', 'name': 'partner21'},
        ]
        dest_records = [
            {'id': 20, 'model': 'res.partner', 'res_id': 2100, 'module': 'base', 'name': 'partner21'},
        ]
        source_model_data = StubModelData(source_records)
        dest_model_data = StubModelData(dest_records)

        syncer = self._build_syncer(batch_size=5)
        syncer.source = types.SimpleNamespace(odoo=types.SimpleNamespace(env={'ir.model.data': source_model_data}))
        syncer.dest = types.SimpleNamespace(ir_model_obj=dest_model_data)

        model = OdooModel({'model': 'res.partner'})
        syncer.models_by_name = {model.name: model}

        records = [{'id': 21}]
        syncer._prefetch_destination_ids(model, records)
        first_source_calls = len(source_model_data.search_calls)
        first_dest_calls = len(dest_model_data.search_calls)

        syncer._prefetch_destination_ids(model, records)
        self.assertEqual(len(source_model_data.search_calls), first_source_calls)
        self.assertEqual(len(dest_model_data.search_calls), first_dest_calls)
        self.assertEqual(model.trans.get(21), 2100)


if __name__ == '__main__':
    unittest.main()
