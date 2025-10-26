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
        forward, reverse = syncer._parse_record_id_mappings({
            'record_id_mappings': {
                'forward': {
                    'res.company': {2: 1},
                },
                'reverse': {
                    'res.partner': {10: 99},
                },
            }
        })
        self.assertEqual(forward['res.company'][2], 1)
        self.assertEqual(reverse['res.partner'][10], 99)

    def test_legacy_keys_emit_deprecation_warning(self):
        syncer = ModelSyncer.__new__(ModelSyncer)
        with self.assertLogs(logger, level='WARNING') as captured:
            forward, reverse = syncer._parse_record_id_mappings({
                'manual_mapping': {'res.company': {3: 5}},
                'reverse_manual_mapping': {'res.partner': {7: 9}},
            })
        log_output = '\n'.join(captured.output)
        self.assertIn('deprecated', log_output)
        self.assertEqual(forward['res.company'][3], 5)
        self.assertEqual(reverse['res.partner'][7], 9)


if __name__ == '__main__':
    unittest.main()
