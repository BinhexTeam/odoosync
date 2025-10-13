import sys
import types
import unittest
from unittest.mock import patch

if 'odoorpc' not in sys.modules:
    odoorpc_stub = types.ModuleType('odoorpc')
    odoorpc_stub.error = types.SimpleNamespace(RPCError=Exception)
    odoorpc_stub.ODOO = object
    sys.modules['odoorpc'] = odoorpc_stub

import os
pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if pkg_root not in sys.path:
    sys.path.insert(0, pkg_root)
sys.modules.pop('odoosync', None)
sys.modules.pop('odoosync.ModelSyncer', None)

from odoosync.ModelSyncer import OdooInstance


class DummyODOO:
    def __init__(self, *args, **kwargs):
        self.calls = []
        self.env = DummyEnv({'ir.model.data': DummyIrModelData()})
        self.config = {}

    def login(self, database, username, password):
        self.calls.append((database, username, password))


class DummyEnv(dict):
    def __init__(self, data):
        super().__init__(data)
        self.uid = 1

    def __getitem__(self, key):
        return dict.__getitem__(self, key)


class DummyIrModelData:
    def __init__(self):
        self.created = []

    def create(self, vals):
        self.created.append(vals)
        return 1

    def read(self, ids, fields=None):
        return [{'create_date': '1970-01-01 00:00:00'}]

    def unlink(self, record_id):
        return True


class FakeNetrc:
    def __init__(self, expected_host, username, password):
        self.expected_host = expected_host
        self.username = username
        self.password = password

    def authenticators(self, host):
        if host != self.expected_host:
            return None
        return (self.username, None, self.password)


class OdooInstanceNetrcTests(unittest.TestCase):

    @patch('odoosync.ModelSyncer.OdooInstance._get_timestamp', autospec=True, return_value=None)
    @patch('odoosync.ModelSyncer.odoorpc.ODOO')
    @patch('odoosync.ModelSyncer.netrc.netrc')
    def test_instance_prefers_explicit_netrc(self, mock_netrc, mock_odoo_cls, mock_get_timestamp):
        dummy_odoo = DummyODOO()
        mock_odoo_cls.return_value = dummy_odoo
        mock_netrc.return_value = FakeNetrc('example.com', 'user@example.com', 'secret')

        with patch.dict('odoosync.ModelSyncer.os.environ', {}, clear=True):
            OdooInstance({'host': 'example.com', 'database': 'db', 'netrc_path': '/tmp/custom.netrc'})

        mock_netrc.assert_called_once_with('/tmp/custom.netrc')
        self.assertIn(('db', 'user@example.com', 'secret'), dummy_odoo.calls)

    @patch('odoosync.ModelSyncer.OdooInstance._get_timestamp', autospec=True, return_value=None)
    @patch('odoosync.ModelSyncer.odoorpc.ODOO')
    @patch('odoosync.ModelSyncer.netrc.netrc')
    def test_instance_uses_default_option_netrc(self, mock_netrc, mock_odoo_cls, mock_get_timestamp):
        dummy_odoo = DummyODOO()
        mock_odoo_cls.return_value = dummy_odoo
        mock_netrc.return_value = FakeNetrc('example.org', 'other@example.com', 'topsecret')

        with patch.dict('odoosync.ModelSyncer.os.environ', {}, clear=True):
            OdooInstance({'host': 'example.org', 'database': 'db'}, default_netrc_path='/tmp/shared.netrc')

        mock_netrc.assert_called_once_with('/tmp/shared.netrc')
        self.assertIn(('db', 'other@example.com', 'topsecret'), dummy_odoo.calls)


if __name__ == '__main__':
    unittest.main()
