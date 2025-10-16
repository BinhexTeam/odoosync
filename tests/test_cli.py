import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if pkg_root not in sys.path:
    sys.path.insert(0, pkg_root)
sys.modules.pop('odoosync', None)
sys.modules.pop('odoosync.ModelSyncer', None)

SCRIPT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'bash-scripts', 'odoosync'))


def load_cli_module():
    loader = importlib.machinery.SourceFileLoader("odoosync_cli", SCRIPT_PATH)
    spec = importlib.util.spec_from_loader("odoosync_cli", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class OdooSyncCLITests(unittest.TestCase):

    def setUp(self):
        if 'yaml' not in sys.modules:
            yaml_stub = types.ModuleType('yaml')

            def _load(stream, Loader=None):
                if hasattr(stream, 'read'):
                    stream = stream.read()
                return json.loads(stream) if stream else None

            def _dump(data, stream):
                json.dump(data, stream)

            yaml_stub.load = _load
            yaml_stub.dump = _dump
            yaml_stub.SafeLoader = object
            sys.modules['yaml'] = yaml_stub
        if 'odoorpc' not in sys.modules:
            odoorpc_stub = types.ModuleType('odoorpc')
            odoorpc_stub.error = types.SimpleNamespace(RPCError=Exception)
            odoorpc_stub.ODOO = object
            sys.modules['odoorpc'] = odoorpc_stub

    def _write_yaml(self, content):
        tmp = tempfile.NamedTemporaryFile('w', delete=False, suffix='.yaml')
        tmp.write(content)
        tmp.flush()
        tmp.close()
        self.addCleanup(lambda: os.path.exists(tmp.name) and os.unlink(tmp.name))
        timestamp_path = os.path.splitext(tmp.name)[0] + '.timestamp'
        self.addCleanup(lambda: os.path.exists(timestamp_path) and os.unlink(timestamp_path))
        return tmp.name

    def test_cli_overrides_netrc_path(self):
        module = load_cli_module()
        yaml_path = self._write_yaml('{"options": {"netrc_path": "/existing"}, "source": {}, "target": {}, "models": []}')

        mock_instance = MagicMock()
        mock_instance.get_new_timestamps.return_value = {}

        with patch.object(module, 'ModelSyncer', return_value=mock_instance) as mock_syncer:
            with patch.object(sys, 'argv', ['odoosync', yaml_path, '--netrc-file', '/tmp/custom.netrc']):
                module.main()

        args, kwargs = mock_syncer.call_args
        self.assertEqual(args[0]['options']['netrc_path'], '/tmp/custom.netrc')
        self.assertNotIn('sync_dependencies', args[0]['options'])

    def test_cli_enables_sync_dependencies_flag(self):
        module = load_cli_module()
        yaml_path = self._write_yaml('{"source": {}, "target": {}, "models": []}')

        mock_instance = MagicMock()
        mock_instance.get_new_timestamps.return_value = {}

        with patch.object(module, 'ModelSyncer', return_value=mock_instance) as mock_syncer:
            with patch.object(sys, 'argv', ['odoosync', yaml_path, '--sync-dependencies']):
                module.main()

        args, kwargs = mock_syncer.call_args
        self.assertTrue(args[0]['options']['sync_dependencies'])

    def test_cli_respects_absence_of_override(self):
        module = load_cli_module()
        yaml_path = self._write_yaml('{"source": {}, "target": {}, "models": []}')

        mock_instance = MagicMock()
        mock_instance.get_new_timestamps.return_value = {}

        with patch.object(module, 'ModelSyncer', return_value=mock_instance) as mock_syncer:
            with patch.object(sys, 'argv', ['odoosync', yaml_path]):
                module.main()

        args, kwargs = mock_syncer.call_args
        self.assertIsNone(args[0].get('options'))

    def test_help_includes_examples_section(self):
        module = load_cli_module()
        parser = module.build_parser()
        help_output = parser.format_help()

        self.assertIn('Examples:', help_output)
        self.assertIn('odoosync projects.yaml', help_output)

    def test_cli_accepts_batch_size(self):
        module = load_cli_module()
        yaml_path = self._write_yaml('{"source": {}, "target": {}, "models": []}')

        mock_instance = MagicMock()
        mock_instance.get_new_timestamps.return_value = {}

        with patch.object(module, 'ModelSyncer', return_value=mock_instance) as mock_syncer:
            with patch.object(sys, 'argv', ['odoosync', yaml_path, '--batch-size', '250']):
                module.main()

        args, kwargs = mock_syncer.call_args
        self.assertEqual(args[0]['options']['batch_size'], 250)


if __name__ == '__main__':
    unittest.main()
