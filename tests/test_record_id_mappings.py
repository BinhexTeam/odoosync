import os
import sys
import unittest

pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if pkg_root not in sys.path:
    sys.path.insert(0, pkg_root)
sys.modules.pop('odoosync', None)
sys.modules.pop('odoosync.sync', None)

from odoosync.sync import ModelSyncer
from odoosync.sync.syncer import logger


class RecordIdMappingParsingTests(unittest.TestCase):

    def test_new_schema_parses_forward_and_reverse(self):
        syncer = ModelSyncer.__new__(ModelSyncer)
        forward, reverse, xmlid_overrides = syncer._parse_record_id_mappings({
            'record_id_mappings': {
                'forward': {
                    'res.company': {2: 1},
                },
                'reverse': {
                    'res.partner': {10: 99},
                },
                'xmlid_overrides': {
                    'uom.uom': {'product.product_uom_unit': 'uom.product_uom_unit'},
                },
            }
        })
        self.assertEqual(forward['res.company'][2], 1)
        self.assertEqual(reverse['res.partner'][10], 99)
        self.assertEqual(xmlid_overrides['uom.uom']['product.product_uom_unit'], 'uom.product_uom_unit')

    def test_legacy_keys_emit_deprecation_warning(self):
        syncer = ModelSyncer.__new__(ModelSyncer)
        with self.assertLogs(logger, level='WARNING') as captured:
            forward, reverse, xmlid_overrides = syncer._parse_record_id_mappings({
                'manual_mapping': {'res.company': {3: 5}},
                'reverse_manual_mapping': {'res.partner': {7: 9}},
            })
        log_output = '\n'.join(captured.output)
        self.assertIn('deprecated', log_output)
        self.assertEqual(forward['res.company'][3], 5)
        self.assertEqual(reverse['res.partner'][7], 9)
        self.assertEqual(xmlid_overrides, {})

    def test_xmlid_override_applied(self):
        syncer = ModelSyncer.__new__(ModelSyncer)
        syncer.record_id_map_forward = {}
        syncer.record_id_map_reverse = {}
        syncer.record_id_xmlid_overrides = {'uom.uom': {'product.product_uom_unit': 'uom.product_uom_unit'}}
        syncer.models_by_name = {}
        syncer._external_translations = {}
        syncer.auto_xmlid_lookup = True
        syncer._lookup_source_xmlid = lambda model, sid: 'product.product_uom_unit'
        syncer._lookup_dest_by_xmlid = lambda model, xmlid: 42 if xmlid == 'uom.product_uom_unit' else None
        syncer._external_translations = {}
        syncer.models_by_name = {}
        result = syncer._find_dest_id('uom.uom', 1)
        self.assertEqual(result, 42)

    def test_bulk_xmlid_override_applied(self):
        syncer = ModelSyncer.__new__(ModelSyncer)
        syncer.record_id_xmlid_overrides = {
            'uom.uom': {'product.product_uom_unit': 'uom.product_uom_unit'}
        }
        overrides = syncer._apply_xmlid_overrides_bulk('uom.uom', {
            1: 'product.product_uom_unit',
            2: 'custom.other',
            3: None,
        })
        self.assertEqual(overrides[1], 'uom.product_uom_unit')
        self.assertEqual(overrides[2], 'custom.other')
        self.assertIsNone(overrides[3])


if __name__ == '__main__':
    unittest.main()
