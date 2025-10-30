import os
import sys
import types
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if pkg_root not in sys.path:
    sys.path.insert(0, pkg_root)
sys.modules.pop('odoosync', None)
sys.modules.pop('odoosync.ModelSyncer', None)

from odoosync.ModelSyncer import ModelSyncer


class RpcRetryTests(unittest.TestCase):
    def setUp(self):
        self.syncer = ModelSyncer.__new__(ModelSyncer)
        self.syncer.rpc_retry_attempts = 3
        self.syncer.rpc_retry_delay = 0.1
        self.syncer.rpc_retry_backoff = 1.0

    def _http_error(self):
        return HTTPError(url='http://example.com', code=502, msg='Bad Gateway', hdrs=None, fp=None)

    def test_retry_succeeds_after_transient_errors(self):
        call_count = {'value': 0}

        def flaky_call():
            call_count['value'] += 1
            if call_count['value'] < 3:
                raise self._http_error()
            return 'ok'

        with patch('odoosync.sync.syncer.time.sleep') as sleep_mock:
            result = self.syncer._execute_with_retry(flaky_call, 'transient test')
        self.assertEqual(result, 'ok')
        self.assertEqual(call_count['value'], 3)
        self.assertEqual(sleep_mock.call_count, 2)

    def test_retry_exhaustion_raises(self):
        def always_fail():
            raise self._http_error()

        with patch('odoosync.sync.syncer.time.sleep'):
            with self.assertRaises(HTTPError):
                self.syncer._execute_with_retry(always_fail, 'permanent failure')

    def test_non_retryable_error_propagates_immediately(self):
        def bad_call():
            raise ValueError('boom')

        with patch('odoosync.sync.syncer.time.sleep') as sleep_mock:
            with self.assertRaises(ValueError):
                self.syncer._execute_with_retry(bad_call, 'non retryable')
        sleep_mock.assert_not_called()


if __name__ == '__main__':
    unittest.main()
