import os
import sys
import unittest

pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if pkg_root not in sys.path:
    sys.path.insert(0, pkg_root)
sys.modules.pop('odoosync', None)
sys.modules.pop('odoosync.models', None)

from odoosync.models import OdooModel


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


if __name__ == '__main__':
    unittest.main()
