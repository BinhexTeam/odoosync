import os
import sys
import types
import unittest
from collections import defaultdict

if 'odoorpc' not in sys.modules:
    odoorpc_stub = types.ModuleType('odoorpc')
    odoorpc_stub.error = types.SimpleNamespace(RPCError=Exception)
    odoorpc_stub.ODOO = object
    sys.modules['odoorpc'] = odoorpc_stub

pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if pkg_root not in sys.path:
    sys.path.insert(0, pkg_root)
sys.modules.pop('odoosync', None)
sys.modules.pop('odoosync.ModelSyncer', None)

from odoosync.ModelSyncer import ModelSyncer, OdooModel


class FakeOdooModel:
    def __init__(self, existing=None):
        self.created = []
        self.read_calls = []
        self.write_calls = []
        self.data = existing or {}

    def create(self, vals):
        self.created.append(dict(vals))
        new_id = len(self.data) + len(self.created)
        return new_id

    def read(self, ids, fields):
        self.read_calls.append((ids, fields))
        return [self.data.get(_id, {'id': _id}) for _id in ids]

    def write(self, dest_id, vals):
        self.write_calls.append((dest_id, vals))
        self.data[dest_id] = dict({'id': dest_id, **vals})
        return True


class FakeIrModelData:
    def __init__(self, entries=None):
        self.entries = entries or []

    def search(self, domain):
        results = []
        for entry in self.entries:
            matched = True
            for field, op, value in domain:
                entry_value = entry.get(field)
                if op == '=':
                    if entry_value != value:
                        matched = False
                        break
                elif op == 'in':
                    if entry_value not in value:
                        matched = False
                        break
                else:
                    raise NotImplementedError('Unsupported operator in test stub')
            if matched:
                results.append(entry['id'])
        return results

    def read(self, ids, fields):
        records = []
        for entry in self.entries:
            if entry['id'] in ids:
                record = {field: entry.get(field) for field in fields}
                records.append(record)
        return records

    def create(self, vals):
        entry = dict(vals)
        entry['id'] = len(self.entries) + 1
        self.entries.append(entry)
        return entry['id']

    def unlink(self, record_id):
        self.entries = [entry for entry in self.entries if entry['id'] != record_id]


class FakeEnv(dict):
    def __getitem__(self, key):
        return dict.__getitem__(self, key)


class XmlIdLookupTests(unittest.TestCase):

    def _build_syncer(self, source_imd, dest_models):
        syncer = ModelSyncer.__new__(ModelSyncer)
        syncer.sync_dependencies = True
        syncer.dry_run = False
        syncer.auto_xmlid_lookup = True
        syncer.manual_mapping = {}
        syncer.reverse_manual_mapping = {}
        syncer._external_translations = defaultdict(dict)
        syncer._source_xmlid_cache = defaultdict(dict)
        syncer._dest_xmlid_cache = defaultdict(dict)
        syncer.prefix = '__test__'
        syncer.dest = types.SimpleNamespace()
        syncer.dest.odoo = types.SimpleNamespace(env=FakeEnv(dest_models))
        syncer.dest.ir_model_obj = dest_models['ir.model.data']
        syncer.source = types.SimpleNamespace()
        syncer.source.odoo = types.SimpleNamespace(env=FakeEnv({'ir.model.data': source_imd}))
        return syncer

    def test_many2one_relations_resolve_via_xmlid(self):
        source_imd = FakeIrModelData([
            {'id': 1, 'model': 'res.country', 'res_id': 5, 'module': 'base', 'name': 'es'},
        ])
        dest_partner_model = FakeOdooModel()
        dest_imd = FakeIrModelData([
            {'id': 10, 'model': 'res.country', 'res_id': 205, 'module': 'base', 'name': 'es'},
        ])
        dest_models = {
            'res.partner': dest_partner_model,
            'ir.model.data': dest_imd,
        }

        syncer = self._build_syncer(source_imd, dest_models)
        model = OdooModel({'model': 'res.partner'})
        model.fields = ['id', 'name', 'country_id']
        model.dest_fields = ['id', 'name', 'country_id']
        model.many2onefields = {'country_id': 'res.country'}
        model.field_specs = {
            'name': {
                'dest_field': 'name',
                'source_type': 'char',
                'source_relation': None,
                'dest_type': 'char',
                'dest_relation': None,
            },
            'country_id': {
                'dest_field': 'country_id',
                'source_type': 'many2one',
                'source_relation': 'res.country',
                'dest_type': 'many2one',
                'dest_relation': 'res.country',
            },
        }
        model.records = [
            {'id': 1, 'name': 'Ada Lovelace', 'country_id': [5, 'Spain']},
        ]
        syncer.models_by_name = {'res.partner': model}
        syncer._external_translations = defaultdict(dict)

        syncer._sync_one_model(model)
        self.assertEqual(dest_partner_model.created[0]['country_id'], 205)

    def test_existing_records_reused_when_xmlid_matches(self):
        source_imd = FakeIrModelData([
            {'id': 1, 'model': 'res.country', 'res_id': 5, 'module': 'base', 'name': 'es'},
        ])
        dest_country_model = FakeOdooModel(existing={205: {'id': 205, 'name': 'Spain'}})
        dest_imd = FakeIrModelData([
            {'id': 10, 'model': 'res.country', 'res_id': 205, 'module': 'base', 'name': 'es'},
        ])
        dest_models = {
            'res.country': dest_country_model,
            'ir.model.data': dest_imd,
        }

        syncer = self._build_syncer(source_imd, dest_models)
        model = OdooModel({'model': 'res.country'})
        model.fields = ['id', 'name']
        model.dest_fields = ['id', 'name']
        model.records = [
            {'id': 5, 'name': 'Spain'},
        ]
        model.field_specs = {
            'name': {
                'dest_field': 'name',
                'source_type': 'char',
                'source_relation': None,
                'dest_type': 'char',
                'dest_relation': None,
            }
        }
        syncer.models_by_name = {'res.country': model}
        syncer._external_translations = defaultdict(dict)

        syncer._sync_one_model(model)
        self.assertFalse(dest_country_model.created)
        self.assertEqual(syncer._external_translations['res.country'][5], 205)


if __name__ == '__main__':
    unittest.main()
