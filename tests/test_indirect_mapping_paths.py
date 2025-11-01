import os
import sys
import unittest

pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if pkg_root not in sys.path:
    sys.path.insert(0, pkg_root)
sys.modules.pop('odoosync', None)
sys.modules.pop('odoosync.ModelSyncer', None)

from odoosync.ModelSyncer import OdooModel  # noqa: E402


class DummyModel:
    def __init__(self, records, metadata=None):
        self.records = {record_id: dict(values) for record_id, values in records.items()}
        self.metadata = metadata or {}

    def read(self, ids, fields):
        result = []
        for record_id in ids:
            values = self.records.get(record_id, {})
            result.append({field: values.get(field) for field in fields})
        return result

    def fields_get(self, fields=None, attributes=None):
        if fields is None:
            fields = list(self.metadata.keys())
        return {field: dict(self.metadata.get(field, {})) for field in fields}


class MockEnv:
    def __init__(self, models):
        self._models = dict(models)

    def __getitem__(self, model_name):
        return self._models[model_name]


class MockOdoo:
    def __init__(self, models):
        self.env = MockEnv(models)


class IndirectTargetFieldPathTests(unittest.TestCase):

    def setUp(self):
        source_records = {
            24409: {'product_tmpl_id': [24439, 'Source Template']},
        }
        source_metadata = {
            'product_tmpl_id': {'type': 'many2one', 'relation': 'product.template'},
        }
        dest_template_records = {
            555: {'product_variant_id': [34818, 'Destination Variant']},
            777: {'product_variant_id': [34818, 'Destination Variant']},
        }
        dest_template_metadata = {
            'product_variant_id': {'type': 'many2one', 'relation': 'product.product'},
        }
        dest_product_records = {
            34818: {'product_tmpl_id': [777, 'Destination Template']},
        }
        dest_product_metadata = {
            'product_tmpl_id': {'type': 'many2one', 'relation': 'product.template'},
        }

        self.source_odoo = MockOdoo({'product.product': DummyModel(source_records, source_metadata)})
        self.dest_odoo = MockOdoo({
            'product.template': DummyModel(dest_template_records, dest_template_metadata),
            'product.product': DummyModel(dest_product_records, dest_product_metadata),
        })

    @staticmethod
    def _find_dest_id(model_name, source_id):
        if model_name == 'product.template' and source_id == 24439:
            return 555
        return None

    def _build_model(self, target_field):
        model_config = {
            'model': 'mrp.bom.line',
            'field_mappings': {
                'product_id': {
                    'indirect_via': 'product_tmpl_id',
                    'target_model': 'product.template',
                    'target_field': target_field,
                },
            },
        }
        model = OdooModel(model_config)
        model._source_odoo = self.source_odoo
        model._dest_odoo = self.dest_odoo
        return model

    def test_simple_target_field_returns_variant_id(self):
        model = self._build_model('product_variant_id')
        indirect_id = model._try_indirect_mapping(
            'product_id',
            'product_id',
            24409,
            'product.product',
            self._find_dest_id,
        )
        self.assertEqual(indirect_id, 34818)

    def test_dotted_target_field_traverses_relations(self):
        model = self._build_model('product_variant_id.product_tmpl_id.product_variant_id')
        indirect_id = model._try_indirect_mapping(
            'product_id',
            'product_id',
            24409,
            'product.product',
            self._find_dest_id,
        )
        self.assertEqual(indirect_id, 34818)


if __name__ == '__main__':
    unittest.main()
