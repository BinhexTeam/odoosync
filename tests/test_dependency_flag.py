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


class FakeOdooModel:
    def __init__(self):
        self.created = []
        self.read_calls = []
        self.write_calls = []

    def create(self, vals):
        stored = dict(vals)
        self.created.append(stored)
        return len(self.created)

    def read(self, ids, fields):
        self.read_calls.append((ids, fields))
        return []

    def write(self, dest_id, vals):
        self.write_calls.append((dest_id, vals))
        return True


class FakeEnv(dict):
    def __getitem__(self, key):
        return dict.__getitem__(self, key)


class DummyModel:
    def __init__(self):
        self.name = 'res.partner'
        self.reverse = False
        self.fields = ['name', 'parent_id']
        self.dest_fields = ['id', 'name', 'parent_id']
        self.many2onefields = {'parent_id': 'res.partner'}
        self.translatable_ids = set()
        self.records = []

    def _map_fields(self, data, find_dest_id_function):
        mapped = {k: v for k, v in data.items() if not k.startswith('__sfit_')}
        for field, rel_model_name in self.many2onefields.items():
            source_rel = data.get(field)
            source_rel_id = source_rel and source_rel[0]
            if source_rel_id:
                dest_id = find_dest_id_function(rel_model_name, source_rel_id)
                mapped[field] = dest_id or None
        return mapped


class DependencyFlagTests(unittest.TestCase):

    def setUp(self):
        if 'odoorpc' not in sys.modules:
            odoorpc_stub = types.ModuleType('odoorpc')
            odoorpc_stub.error = types.SimpleNamespace(RPCError=Exception)
            odoorpc_stub.ODOO = object
            sys.modules['odoorpc'] = odoorpc_stub

    def _build_syncer(self, sync_dependencies):
        syncer = ModelSyncer.__new__(ModelSyncer)
        syncer.sync_dependencies = sync_dependencies
        syncer.dry_run = False
        translations = {}
        syncer.auto_xmlid_lookup = False
        syncer._external_translations = defaultdict(dict)
        syncer._source_xmlid_cache = defaultdict(dict)
        syncer._dest_xmlid_cache = defaultdict(dict)

        def _find_dest_id(model_name, source_id):
            return translations.get((model_name, source_id))

        def _add_dest_id(model_name, source_id, dest_id):
            translations[(model_name, source_id)] = dest_id

        syncer._find_dest_id = _find_dest_id
        syncer._add_dest_id = _add_dest_id
        syncer.create_xmlid = lambda *args, **kwargs: None
        syncer._translate_to_dest_id = lambda *args, **kwargs: None
        syncer.dest = types.SimpleNamespace()
        fake_model = FakeOdooModel()
        syncer.dest.odoo = types.SimpleNamespace(env=FakeEnv({'res.partner': fake_model}))
        syncer.source = syncer.dest
        syncer.dest.ir_model_obj = types.SimpleNamespace(
            search=lambda *args, **kwargs: [],
            read=lambda *args, **kwargs: []
        )
        syncer.manual_mapping = {}
        syncer.reverse_manual_mapping = {}
        return syncer, fake_model

    def test_dependencies_skipped_when_flag_disabled(self):
        syncer, fake_model = self._build_syncer(sync_dependencies=False)
        model = DummyModel()
        model.records = [
            {'id': 1, 'name': 'Primary'},
            {'id': 2, 'name': 'Dependent', '__sfit_dep': True},
        ]
        syncer._sync_one_model(model)
        self.assertEqual(len(fake_model.created), 1)
        self.assertEqual(fake_model.created[0]['name'], 'Primary')

    def test_dependencies_created_when_flag_enabled(self):
        syncer, fake_model = self._build_syncer(sync_dependencies=True)
        model = DummyModel()
        model.records = [
            {'id': 1, 'name': 'Primary'},
            {'id': 2, 'name': 'Dependent', '__sfit_dep': True, 'parent_id': [1, 'Primary']},
        ]
        syncer._sync_one_model(model)
        self.assertEqual([rec['name'] for rec in fake_model.created], ['Primary', 'Dependent'])
        # Ensure parent mapping used the created dependency id (1)
        self.assertEqual(fake_model.created[1]['parent_id'], 1)

    def test_parent_relationship_preserved_for_chains(self):
        syncer, fake_model = self._build_syncer(sync_dependencies=True)
        model = DummyModel()
        model.records = [
            {'id': 10, 'name': 'Root', '__sfit_dep': True},
            {'id': 11, 'name': 'Parent', '__sfit_dep': True, 'parent_id': [10, 'Root']},
            {'id': 12, 'name': 'Child', 'parent_id': [11, 'Parent']},
        ]
        syncer._sync_one_model(model)
        # Expect records created in dependency order: Root -> Parent -> Child
        self.assertEqual([rec['name'] for rec in fake_model.created], ['Root', 'Parent', 'Child'])
        self.assertEqual(fake_model.created[1]['parent_id'], 1)  # Parent points to Root
        self.assertEqual(fake_model.created[2]['parent_id'], 2)  # Child points to Parent

    def test_siblings_share_same_parent(self):
        syncer, fake_model = self._build_syncer(sync_dependencies=True)
        model = DummyModel()
        model.records = [
            {'id': 20, 'name': 'Parent', '__sfit_dep': True},
            {'id': 30, 'name': 'Child B', '__sfit_dep': True, 'parent_id': [20, 'Parent']},
            {'id': 40, 'name': 'Child A', 'parent_id': [20, 'Parent']},
        ]
        syncer._sync_one_model(model)
        self.assertEqual([rec['name'] for rec in fake_model.created], ['Parent', 'Child B', 'Child A'])
        self.assertIsNone(fake_model.created[0].get('parent_id'))
        self.assertEqual(fake_model.created[1].get('parent_id'), 1)
        self.assertEqual(fake_model.created[2].get('parent_id'), 1)


if __name__ == '__main__':
    unittest.main()
