import os
import sys
import unittest

pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if pkg_root not in sys.path:
    sys.path.insert(0, pkg_root)
sys.modules.pop('odoosync', None)
sys.modules.pop('odoosync.models', None)

from odoosync.models import OdooModel


class LookupReadModel:
    def __init__(self, records):
        self._records = dict(records)

    def read(self, ids, fields):
        return [self._records.get(record_id, {}) for record_id in ids]


class LookupSearchModel:
    def __init__(self, results):
        self._results = list(results)

    def search(self, domain, limit=None):
        if limit:
            return self._results[:limit]
        return list(self._results)


class MockEnv:
    def __init__(self, models):
        self._models = dict(models)

    def __getitem__(self, model_name):
        return self._models[model_name]


class MockOdoo:
    def __init__(self, models):
        self.env = MockEnv(models)


class ValueMappingTests(unittest.TestCase):
    def test_selection_value_mapping_applied(self):
        model = OdooModel({
            'model': 'product.template',
            'value_mappings': {
                'type': {
                    'product': 'combo',
                    '__default__': 'service',
                }
            },
        })
        model.fields = ['type']
        model.dest_fields = ['type']
        model.field_specs = {
            'type': {
                'dest_field': 'type',
                'source_type': 'selection',
                'source_relation': None,
                'dest_type': 'selection',
                'dest_relation': None,
            }
        }
        data = {'type': 'product'}
        mapped = model._map_fields(data, lambda *args, **kwargs: None)
        self.assertEqual(mapped['type'], 'combo')

    def test_value_mapping_default_used_when_missing(self):
        model = OdooModel({
            'model': 'product.template',
            'value_mappings': {
                'type': {
                    'product': 'combo',
                    '__default__': 'service',
                }
            },
        })
        model.fields = ['type']
        model.dest_fields = ['type']
        model.field_specs = {
            'type': {
                'dest_field': 'type',
                'source_type': 'selection',
                'source_relation': None,
                'dest_type': 'selection',
                'dest_relation': None,
            }
        }
        data = {'type': 'consu'}
        mapped = model._map_fields(data, lambda *args, **kwargs: None)
        self.assertEqual(mapped['type'], 'service')

    def test_case_insensitive_mapping(self):
        model = OdooModel({
            'model': 'res.partner',
            'value_mappings': {
                'category': {
                    'case_insensitive': True,
                    'values': {
                        'vip': 'VIP',
                    },
                }
            },
        })
        model.fields = ['category']
        model.dest_fields = ['category']
        model.field_specs = {
            'category': {
                'dest_field': 'category',
                'source_type': 'char',
                'source_relation': None,
                'dest_type': 'char',
                'dest_relation': None,
            }
        }
        data = {'category': 'VIP'}
        mapped = model._map_fields(data, lambda *args, **kwargs: None)
        self.assertEqual(mapped['category'], 'VIP')

    def test_selection_to_boolean_mapping(self):
        model = OdooModel({
            'model': 'product.template',
            'field_mappings': {
                'type': 'is_storable',
            },
            'value_mappings': {
                'type': {
                    'product': True,
                    '__default__': False,
                }
            },
        })
        model.fields = ['type']
        model.dest_fields = ['is_storable']
        model.field_specs = {
            'type': {
                'dest_field': 'is_storable',
                'source_type': 'selection',
                'source_relation': None,
                'dest_type': 'boolean',
                'dest_relation': None,
            }
        }
        mapped_product = model._map_fields({'type': 'product'}, lambda *args, **kwargs: None)
        self.assertTrue(mapped_product['is_storable'])

        mapped_service = model._map_fields({'type': 'service'}, lambda *args, **kwargs: None)
        self.assertFalse(mapped_service['is_storable'])

    def test_constant_mapping_overrides_all_values(self):
        model = OdooModel({
            'model': 'res.partner',
            'value_mappings': {
                'comment': 'Migrated from legacy system',
            },
        })
        model.fields = ['comment']
        model.dest_fields = ['comment']
        model.field_specs = {
            'comment': {
                'dest_field': 'comment',
                'source_type': 'text',
                'source_relation': None,
                'dest_type': 'text',
                'dest_relation': None,
            }
        }
        data = {'comment': 'Original Note'}
        mapped = model._map_fields(data, lambda *args, **kwargs: None)
        self.assertEqual(mapped['comment'], 'Migrated from legacy system')

    def test_forced_values_override_incoming_data(self):
        model = OdooModel({
            'model': 'res.partner',
            'forced_values': {
                'comment': 'Always set me',
            },
        })
        model.fields = ['comment']
        model.dest_fields = ['comment']
        model.field_specs = {
            'comment': {
                'dest_field': 'comment',
                'source_type': 'text',
                'source_relation': None,
                'dest_type': 'text',
                'dest_relation': None,
            }
        }
        data = {'comment': 'Original Note'}
        mapped = model._map_fields(data, lambda *args, **kwargs: None)
        self.assertEqual(mapped['comment'], 'Always set me')

    def test_forced_values_applied_without_source_field(self):
        model = OdooModel({
            'model': 'product.template',
            'forced_values': {
                'available_in_pos': True,
            },
        })
        model.fields = []
        model.dest_fields = []
        model.field_specs = {}
        mapped = model._map_fields({}, lambda *args, **kwargs: None)
        self.assertEqual(mapped['available_in_pos'], True)

    def test_lookup_mapping_matches_cross_model_code(self):
        model = OdooModel({
            'model': 'product.template',
            'field_mappings': {
                'intrasat_id': 'hs_code_id',
            },
            'value_mappings': {
                'intrasat_id': {
                    'lookup': {
                        'source_model': 'report.intrastat.code',
                        'source_field': 'code',
                        'dest_model': 'hs.code',
                        'dest_field': 'code',
                    },
                },
            },
        })
        model.fields = ['intrasat_id']
        model.dest_fields = ['hs_code_id']
        model.field_specs = {
            'intrasat_id': {
                'dest_field': 'hs_code_id',
                'source_type': 'many2one',
                'source_relation': 'report.intrastat.code',
                'dest_type': 'many2one',
                'dest_relation': 'hs.code',
            },
        }
        model._source_odoo = MockOdoo({
            'report.intrastat.code': LookupReadModel({123: {'code': '95030010'}}),
        })
        model._dest_odoo = MockOdoo({
            'hs.code': LookupSearchModel([456]),
        })

        data = {'intrasat_id': [123, 'Old Code']}
        mapped = model._map_fields(data, lambda *args, **kwargs: None)
        self.assertEqual(mapped['hs_code_id'], 456)


if __name__ == '__main__':
    unittest.main()
