import os
import sys
import types
import unittest

pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if pkg_root not in sys.path:
    sys.path.insert(0, pkg_root)
sys.modules.pop('odoosync', None)
sys.modules.pop('odoosync.ModelSyncer', None)

from odoosync.ModelSyncer import ModelSyncer


class TestModel:
    def __init__(self):
        self.name = 'res.partner'
        self.domain = []
        self.no_domain = False
        self.context = {}


class ForceSyncFlagTests(unittest.TestCase):

    def setUp(self):
        if 'odoorpc' not in sys.modules:
            odoorpc_stub = types.ModuleType('odoorpc')
            odoorpc_stub.error = types.SimpleNamespace(RPCError=Exception)
            odoorpc_stub.ODOO = object
            sys.modules['odoorpc'] = odoorpc_stub

    def _build_syncer(self, force_sync=False):
        syncer = ModelSyncer.__new__(ModelSyncer)
        syncer.options = {'force_sync': force_sync}
        syncer.force_sync = force_sync
        syncer.debug = False
        return syncer

    def test_force_sync_disabled_by_default(self):
        """Test that force_sync is disabled by default"""
        syncer = self._build_syncer()
        self.assertFalse(syncer.force_sync)

    def test_force_sync_enabled_from_options(self):
        """Test that force_sync can be enabled through options"""
        syncer = self._build_syncer(force_sync=True)
        self.assertTrue(syncer.force_sync)
        
    def test_domain_includes_timestamp_when_force_sync_disabled(self):
        """Test that domain includes timestamp filter when force_sync is disabled"""
        syncer = self._build_syncer(force_sync=False)
        model = TestModel()
        since = '2023-01-01 00:00:00'
        
        domain = syncer._prepare_model_domain(model, since)
        
        self.assertEqual(len(domain), 1)
        self.assertEqual(domain[0], ('write_date', '>', since))

    def test_domain_excludes_timestamp_when_force_sync_enabled(self):
        """Test that domain excludes timestamp filter when force_sync is enabled"""
        syncer = self._build_syncer(force_sync=True)
        model = TestModel()
        since = '2023-01-01 00:00:00'
        
        domain = syncer._prepare_model_domain(model, since)
        
        self.assertEqual(domain, [])