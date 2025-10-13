import os
import sys
import unittest

pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if pkg_root not in sys.path:
    sys.path.insert(0, pkg_root)
sys.modules.pop('odoosync', None)
sys.modules.pop('odoosync.ModelSyncer', None)

from odoosync.ModelSyncer import INTERNAL_RUNTIME_FIELDS, ModelSyncer, OdooModel


class OdooModelFieldMappingTests(unittest.TestCase):

    def test_internal_flags_are_removed_before_mapping(self):
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
        data = {
            'id': 10,
            'name': 'Parent Partner',
        }
        for internal_key in INTERNAL_RUNTIME_FIELDS:
            data[internal_key] = True
        mapped = model._map_fields(data, lambda *args, **kwargs: None)
        for internal_key in INTERNAL_RUNTIME_FIELDS:
            self.assertNotIn(internal_key, mapped)

    def test_make_hash_ignores_internal_flags(self):
        base = {'id': 1, 'name': 'Partner'}
        with_flag = base.copy()
        for internal_key in INTERNAL_RUNTIME_FIELDS:
            with_flag[internal_key] = True
        syncer = ModelSyncer.__new__(ModelSyncer)
        self.assertEqual(ModelSyncer._make_hash(syncer, base),
                         ModelSyncer._make_hash(syncer, with_flag))


if __name__ == '__main__':
    unittest.main()
