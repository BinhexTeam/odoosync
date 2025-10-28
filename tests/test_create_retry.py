import os
import sys
import unittest
from collections import defaultdict

import odoorpc

pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if pkg_root not in sys.path:
    sys.path.insert(0, pkg_root)
sys.modules.pop('odoosync', None)
sys.modules.pop('odoosync.sync', None)
sys.modules.pop('odoosync.models', None)

from odoosync.models import OdooModel
from odoosync.sync import ModelSyncer


class DummyEnv:
    def __init__(self, mapping):
        self._mapping = mapping

    def __getitem__(self, item):
        return self._mapping[item]


class DummyLogModel:
    def __init__(self):
        self.records = []

    def create(self, vals):
        self.records.append(vals)
        return len(self.records)


class SequencedRPCModel:
    def __init__(self, side_effects):
        self.side_effects = side_effects
        self.calls = []

    def create(self, payload):
        self.calls.append(dict(payload))
        effect = self.side_effects[min(len(self.calls), len(self.side_effects)) - 1]
        if isinstance(effect, Exception):
            raise effect
        return effect


class ConditionalRPCModel:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def create(self, payload):
        self.calls.append(dict(payload))
        return self.handler(dict(payload), len(self.calls))


class DummyOdoo:
    def __init__(self, env):
        self.env = env


class DummyInstance:
    def __init__(self, env, database='testdb'):
        self.odoo = DummyOdoo(env)
        self.database = database


class CreateRetryTests(unittest.TestCase):

    def setUp(self):
        self.syncer = ModelSyncer.__new__(ModelSyncer)
        self.syncer.dry_run = False
        self.syncer._external_translations = defaultdict(dict)

    def test_retry_removes_single_field(self):
        model = OdooModel({
            'model': 'res.partner',
            'retry_on_create': {
                'fields': ['vat'],
            },
        })
        vat_error = odoorpc.error.RPCError('Invalid VAT')
        rpc_model = SequencedRPCModel([vat_error, 99])
        log_model = DummyLogModel()
        env = DummyEnv({
            'res.partner': rpc_model,
            'ir.logging': log_model,
        })
        instance = DummyInstance(env)

        stored = {}
        dest_ids = []

        def add_dest(model_name, source_id, dest_id):
            stored[source_id] = dest_id

        def create_xmlid(model_name, source_id, dest_id):
            dest_ids.append(dest_id)

        dest_id = self.syncer._create_record_with_retry(
            instance,
            model,
            {'name': 'Test Partner', 'vat': 'BE0474107887'},
            source_id=54847,
            add_dest_id_function=add_dest,
            create_xmlid_function=create_xmlid,
        )
        self.assertEqual(dest_id, 99)
        self.assertEqual(stored[54847], 99)
        self.assertIn({'name': 'Test Partner', 'vat': 'BE0474107887'}, rpc_model.calls)
        self.assertIn({'name': 'Test Partner'}, rpc_model.calls)
        self.assertTrue(any('drop' in rec['message'] for rec in log_model.records))

    def test_retry_combination_requires_multiple_fields(self):
        model = OdooModel({
            'model': 'res.partner',
            'retry_on_create': {
                'fields': ['vat', 'field_x'],
                'max_subset': 2,
            },
        })

        def handler(payload, _attempt):
            if payload.get('vat') or payload.get('field_x'):
                raise odoorpc.error.RPCError('invalid data')
            return 77

        rpc_model = ConditionalRPCModel(handler)
        log_model = DummyLogModel()
        env = DummyEnv({
            'res.partner': rpc_model,
            'ir.logging': log_model,
        })
        instance = DummyInstance(env)

        dest_id = self.syncer._create_record_with_retry(
            instance,
            model,
            {'name': 'Needs combinations', 'vat': 'bad', 'field_x': True},
            source_id=99,
            add_dest_id_function=lambda *args: None,
            create_xmlid_function=lambda *args: None,
        )
        self.assertEqual(dest_id, 77)
        self.assertEqual(len(rpc_model.calls), 4)
        self.assertTrue(any('success' in rec['message'] for rec in log_model.records))

    def test_retry_respects_max_subset_limit(self):
        model = OdooModel({
            'model': 'res.partner',
            'retry_on_create': {
                'fields': ['vat', 'field_x'],
                'max_subset': 1,
            },
        })

        def handler(payload, _attempt):
            raise odoorpc.error.RPCError('still invalid')

        rpc_model = ConditionalRPCModel(handler)
        log_model = DummyLogModel()
        env = DummyEnv({
            'res.partner': rpc_model,
            'ir.logging': log_model,
        })
        instance = DummyInstance(env)

        dest_id = self.syncer._create_record_with_retry(
            instance,
            model,
            {'name': 'Failing partner', 'vat': 'bad', 'field_x': True},
            source_id=42,
            add_dest_id_function=lambda *args: None,
            create_xmlid_function=lambda *args: None,
        )
        self.assertIsNone(dest_id)
        self.assertEqual(len(rpc_model.calls), 3)  # original + two single-field retries
        self.assertTrue(any('exhausted retry combinations' in rec['message'] for rec in log_model.records))


if __name__ == '__main__':
    unittest.main()
