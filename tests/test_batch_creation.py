import os
import sys
import types
import unittest
from collections import defaultdict

pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if pkg_root not in sys.path:
    sys.path.insert(0, pkg_root)
if 'odoorpc' not in sys.modules:
    odoorpc_stub = types.ModuleType('odoorpc')
    odoorpc_stub.error = types.SimpleNamespace(RPCError=Exception)
    odoorpc_stub.ODOO = object
    sys.modules['odoorpc'] = odoorpc_stub
sys.modules.pop('odoosync', None)
sys.modules.pop('odoosync.sync', None)
sys.modules.pop('odoosync.models', None)

from odoosync.models import OdooModel
from odoosync.sync import ModelSyncer


class DummyEnv(dict):
    def __getitem__(self, item):
        return dict.__getitem__(self, item)


class BatchCreateModel:
    def __init__(self):
        self.payloads = []
        self.batch_lengths = []
        self._next_id = 1000

    def _reserve_id(self):
        new_id = self._next_id
        self._next_id += 1
        return new_id

    def create(self, vals):
        if isinstance(vals, list):
            self.batch_lengths.append(len(vals))
            created_ids = []
            for item in vals:
                self.payloads.append(dict(item))
                created_ids.append(self._reserve_id())
            return created_ids
        self.batch_lengths.append(1)
        self.payloads.append(dict(vals))
        return self._reserve_id()

    def read(self, ids, fields):
        return []

    def write(self, dest_id, vals):
        return True


class BatchCreationTests(unittest.TestCase):
    def _build_syncer(self, batch_size):
        syncer = ModelSyncer.__new__(ModelSyncer)
        syncer.batch_size = batch_size
        syncer.dry_run = False
        syncer.sync_dependencies = True
        syncer.auto_xmlid_lookup = False
        syncer.record_id_map_forward = {}
        syncer.record_id_map_reverse = {}
        syncer._external_translations = defaultdict(dict)
        syncer._source_xmlid_cache = defaultdict(dict)
        syncer._dest_xmlid_cache = defaultdict(dict)
        syncer.models_by_name = {}
        syncer.reverse_models_by_name = {}
        syncer.create_xmlid = lambda *args, **kwargs: None
        syncer._add_dest_id = ModelSyncer._add_dest_id.__get__(syncer, ModelSyncer)
        syncer._find_dest_id = ModelSyncer._find_dest_id.__get__(syncer, ModelSyncer)
        return syncer

    def test_records_created_in_batches(self):
        syncer = self._build_syncer(batch_size=2)
        model = OdooModel({'model': 'res.partner'})
        model.fields = ['id', 'name']
        model.dest_fields = ['id', 'name']
        model.field_specs = {
            'name': {
                'dest_field': 'name',
                'source_type': 'char',
                'source_relation': None,
                'dest_type': 'char',
                'dest_relation': None,
            }
        }
        model.records = [
            {'id': 1, 'name': 'Alpha'},
            {'id': 2, 'name': 'Beta'},
            {'id': 3, 'name': 'Gamma'},
            {'id': 4, 'name': 'Delta'},
            {'id': 5, 'name': 'Epsilon'},
        ]
        syncer.models_by_name = {model.name: model}
        fake_model = BatchCreateModel()
        syncer.dest = types.SimpleNamespace(
            odoo=types.SimpleNamespace(env=DummyEnv({'res.partner': fake_model}))
        )
        syncer.source = syncer.dest
        syncer.dest.ir_model_obj = types.SimpleNamespace(
            search=lambda *args, **kwargs: [],
            read=lambda *args, **kwargs: [],
        )

        syncer._sync_one_model(model)

        self.assertEqual(fake_model.batch_lengths, [2, 2, 1])
        self.assertEqual(len(model.trans), len(model.records))

    def test_disable_batch_processes_in_single_call(self):
        syncer = self._build_syncer(batch_size=None)
        model = OdooModel({'model': 'res.partner'})
        model.fields = ['id', 'name']
        model.dest_fields = ['id', 'name']
        model.field_specs = {
            'name': {
                'dest_field': 'name',
                'source_type': 'char',
                'source_relation': None,
                'dest_type': 'char',
                'dest_relation': None,
            }
        }
        model.records = [
            {'id': 1, 'name': 'Alpha'},
            {'id': 2, 'name': 'Beta'},
            {'id': 3, 'name': 'Gamma'},
        ]
        syncer.models_by_name = {model.name: model}
        fake_model = BatchCreateModel()
        syncer.dest = types.SimpleNamespace(
            odoo=types.SimpleNamespace(env=DummyEnv({'res.partner': fake_model}))
        )
        syncer.source = syncer.dest
        syncer.dest.ir_model_obj = types.SimpleNamespace(
            search=lambda *args, **kwargs: [],
            read=lambda *args, **kwargs: [],
        )

        syncer._sync_one_model(model)

        # With batching disabled the create should receive all records in one call
        self.assertEqual(fake_model.batch_lengths, [3])


if __name__ == '__main__':
    unittest.main()
