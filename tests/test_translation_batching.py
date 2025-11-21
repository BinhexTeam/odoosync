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


class RecordingModelData:
    def __init__(self, records):
        self.records = list(records)
        self.search_calls = []
        self.read_calls = []

    def search(self, domain):
        self.search_calls.append(domain)
        module_filter = None
        name_filter = None
        res_id_filter = None
        model_filter = None
        for field, operator, value in domain:
            if field == 'module' and operator == '=':
                module_filter = value
            elif field == 'name' and operator == 'in':
                name_filter = set(value)
            elif field == 'res_id' and operator == 'in':
                res_id_filter = set(value)
            elif field == 'model' and operator == '=':
                model_filter = value
        matched = []
        for record in self.records:
            if module_filter is not None and record.get('module') != module_filter:
                continue
            if name_filter is not None and record.get('name') not in name_filter:
                continue
            if res_id_filter is not None and record.get('res_id') not in res_id_filter:
                continue
            if model_filter is not None and record.get('model') != model_filter:
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


class TranslationBatchingTests(unittest.TestCase):
    def _build_syncer(self, translation_batch_size=2):
        syncer = ModelSyncer.__new__(ModelSyncer)
        syncer.record_id_map_forward = {}
        syncer.record_id_map_reverse = {}
        syncer._external_translations = defaultdict(dict)
        syncer._source_xmlid_cache = defaultdict(dict)
        syncer._dest_xmlid_cache = defaultdict(dict)
        syncer.translation_batch_size = translation_batch_size
        syncer.batch_size = translation_batch_size
        syncer.auto_xmlid_lookup = True
        syncer.prefix = '__export_sfit__'
        syncer._execute_with_retry = lambda func, context=None: func()
        syncer._get_xmlid = lambda model_name, source_id: f"{syncer.prefix}.{model_name.replace('.', '_')}_{source_id}"
        return syncer

    def test_add_translations_batches_xmlid_searches(self):
        records = []
        for idx, source_id in enumerate(range(1, 6), start=1):
            records.append({
                'id': idx,
                'module': '__export_sfit__',
                'name': f'__export_sfit__.res_partner_{source_id}',
                'model': 'res.partner',
                'res_id': 100 + source_id,
            })
        model_data = RecordingModelData(records)

        syncer = self._build_syncer(translation_batch_size=2)
        syncer.dest = types.SimpleNamespace(ir_model_obj=model_data)
        model = OdooModel({'model': 'res.partner'})
        syncer.models = [model]
        syncer.models_by_name = {model.name: model}
        syncer.reverse_models = []
        syncer.reverse_models_by_name = {}

        loaded = {'res.partner': {1, 2, 3, 4, 5}}
        syncer._add_translations(loaded)

        self.assertEqual(len(model_data.search_calls), 3)
        self.assertEqual(len(model_data.read_calls), 3)
        self.assertEqual(model.trans.get(1), 101)
        self.assertEqual(model.trans.get(5), 105)

    def test_add_reverse_translations_batches_res_id_searches(self):
        records = []
        for idx, source_id in enumerate(range(1, 6), start=1):
            records.append({
                'id': idx,
                'module': '__export_sfit__',
                'name': f'__export_sfit__.res_partner_{source_id}',
                'model': 'res.partner',
                'res_id': 200 + source_id,
            })
        model_data = RecordingModelData(records)

        syncer = self._build_syncer(translation_batch_size=2)
        syncer.dest = types.SimpleNamespace(ir_model_obj=model_data)
        reverse_model = OdooModel({'model': 'res.partner'})
        syncer.reverse_models = [reverse_model]
        syncer.reverse_models_by_name = {reverse_model.name: reverse_model}
        syncer.models = []
        syncer.models_by_name = {}

        loaded = {'res.partner': {201, 202, 203, 204, 205}}
        syncer._add_reverse_translations(loaded)

        self.assertEqual(len(model_data.search_calls), 3)
        self.assertEqual(len(model_data.read_calls), 3)
        self.assertEqual(reverse_model.trans.get(201), 1)
        self.assertEqual(reverse_model.trans.get(205), 5)

    def test_lookup_dest_ids_uses_translation_batch_size(self):
        records = []
        for idx, source_id in enumerate(range(1, 6), start=1):
            records.append({
                'id': idx,
                'module': '__export_sfit__',
                'name': f'partner_{source_id}',
                'model': 'res.partner',
                'res_id': 1000 + source_id,
            })
        model_data = RecordingModelData(records)

        syncer = self._build_syncer(translation_batch_size=2)
        syncer.dest = types.SimpleNamespace(ir_model_obj=model_data)
        syncer.models = []
        syncer.models_by_name = {}
        syncer.reverse_models = []
        syncer.reverse_models_by_name = {}

        xmlids = {source_id: f"__export_sfit__.partner_{source_id}" for source_id in range(1, 6)}
        result = syncer._lookup_dest_ids_by_xmlid_bulk('res.partner', xmlids)

        self.assertEqual(len(model_data.search_calls), 3)
        self.assertEqual(len(model_data.read_calls), 3)
        self.assertEqual(result[1], 1001)
        self.assertEqual(result[5], 1005)


if __name__ == '__main__':
    unittest.main()
