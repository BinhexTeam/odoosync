import os
import sys
import types
import unittest

pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if pkg_root not in sys.path:
    sys.path.insert(0, pkg_root)
sys.modules.pop('odoosync', None)
sys.modules.pop('odoosync.ModelSyncer', None)

from odoosync.ModelSyncer import OdooModel, logger


class FakeIrModelFields:
    def __init__(self, field_definitions):
        self.field_definitions = list(field_definitions)
        self._by_id = {rec['id']: rec for rec in self.field_definitions}

    def search(self, domain):
        results = []
        for record in self.field_definitions:
            if self._matches(record, domain):
                results.append(record['id'])
        return results

    def read(self, ids, fields):
        records = []
        for _id in ids:
            record = self._by_id.get(_id, {})
            if not fields:
                records.append(dict(record))
            else:
                records.append({field: record.get(field) for field in fields})
        return records

    @staticmethod
    def _matches(record, domain):
        for key, op, value in domain:
            if key == 'model' and record.get('model') != value:
                return False
            if key == 'name' and op == 'in' and record.get('name') not in value:
                return False
        return True


class FakeOdoo:
    def __init__(self, fields_env):
        self.env = {'ir.model.fields': fields_env}


class FieldMappingTests(unittest.TestCase):

    def _prepare_model(self, model_config, source_fields, dest_fields):
        model = OdooModel(model_config)
        source_ir = FakeIrModelFields(source_fields)
        dest_ir = FakeIrModelFields(dest_fields)
        odoo = FakeOdoo(source_ir)
        dest_odoo = FakeOdoo(dest_ir)
        model.determine_fields(odoo, dest_odoo, [model], {}, allow_external_m2o=True)
        return model

    def test_field_rename_char_to_char(self):
        model = self._prepare_model(
            {'model': 'res.partner', 'field_mapping': {'x_field': 'y_field'}},
            [
                {'id': 1, 'model': 'res.partner', 'name': 'x_field', 'ttype': 'char', 'relation': False, 'readonly': False},
            ],
            [
                {'id': 1, 'model': 'res.partner', 'name': 'y_field', 'ttype': 'char', 'relation': False},
            ],
        )
        mapped = model._map_fields({'id': 10, 'x_field': 'value'}, lambda *args, **kwargs: None)
        self.assertEqual(mapped['y_field'], 'value')
        self.assertNotIn('x_field', mapped)

    def test_boolean_to_integer_conversion(self):
        model = self._prepare_model(
            {'model': 'res.partner', 'field_mapping': {'flag': 'counter'}},
            [
                {'id': 1, 'model': 'res.partner', 'name': 'flag', 'ttype': 'boolean', 'relation': False, 'readonly': False},
            ],
            [
                {'id': 1, 'model': 'res.partner', 'name': 'counter', 'ttype': 'integer', 'relation': False},
            ],
        )
        mapped = model._map_fields({'id': 42, 'flag': True}, lambda *args, **kwargs: None)
        self.assertEqual(mapped['counter'], 1)

    def test_many2one_to_char_conversion(self):
        model = self._prepare_model(
            {'model': 'res.partner', 'field_mapping': {'country_id': 'country_name'}},
            [
                {'id': 1, 'model': 'res.partner', 'name': 'country_id', 'ttype': 'many2one', 'relation': 'res.country', 'readonly': False},
            ],
            [
                {'id': 1, 'model': 'res.partner', 'name': 'country_name', 'ttype': 'char', 'relation': False},
            ],
        )
        mapped = model._map_fields({'id': 7, 'country_id': [5, 'Spain']}, lambda *args, **kwargs: None)
        self.assertEqual(mapped['country_name'], 'Spain')

    def test_incompatible_mapping_is_skipped_with_log(self):
        model = self._prepare_model(
            {'model': 'res.partner', 'field_mapping': {'child_ids': 'child_text'}},
            [
                {'id': 1, 'model': 'res.partner', 'name': 'child_ids', 'ttype': 'one2many', 'relation': 'res.partner.child', 'readonly': False},
            ],
            [
                {'id': 1, 'model': 'res.partner', 'name': 'child_text', 'ttype': 'char', 'relation': False},
            ],
        )
        with self.assertLogs(logger, level='WARNING') as captured:
            mapped = model._map_fields({'id': 99, 'child_ids': [1, 2, 3]}, lambda *args, **kwargs: None)
        self.assertNotIn('child_text', mapped)
        log_messages = '\n'.join(captured.output)
        self.assertIn('cannot convert one2many to char', log_messages)


if __name__ == '__main__':
    unittest.main()
