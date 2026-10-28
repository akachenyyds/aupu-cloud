"""Synthetic SMS contract, privacy and ambiguous-delivery regressions; no network."""
import importlib
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock
import urllib.error
from urllib.parse import urlsplit, parse_qs

package = types.ModuleType('aupu_auth_tests')
package.__path__ = [str(Path(__file__).resolve().parents[1] / 'custom_components/aupu_cloud')]
sys.modules[package.__name__] = package
a = importlib.import_module(package.__name__ + '.auth')


class SMSAuthentication(unittest.TestCase):
    def setUp(self):
        self.client = a.SMSAuthClient()
        self.opener = self.client._opener = MagicMock()
        self.response = self.opener.open.return_value.__enter__.return_value
        self.response.read.return_value = b'{"status":0,"result":null}'
        self.phone = '13800000000'  # Synthetic input; never sent by these tests.

    def test_send_has_the_observed_query_contract(self):
        self.client.request_code(self.phone)
        req = self.opener.open.call_args.args[0]
        self.assertEqual(req.get_method(), 'GET')
        self.assertEqual(urlsplit(req.full_url).scheme, 'https')
        self.assertEqual(urlsplit(req.full_url).path, a.SMS_PATH)
        self.assertEqual(parse_qs(urlsplit(req.full_url).query),
            {'areaCode': ['86'], 'appKey': ['AP'], 'phoneNum': [self.phone], 'type': ['LOG']})
        self.assertNotIn('Authorization', req.headers)
        self.assertEqual(self.opener.open.call_count, 1)

    def test_login_discards_profile_and_normalizes_token(self):
        self.response.read.return_value = b'{"status":0,"result":{"token":"Bearer test-account-token","user":{"phone":"not retained"}}}'
        self.assertEqual(self.client.login(self.phone, '123456'), 'test-account-token')
        req = self.opener.open.call_args.args[0]
        self.assertEqual(urlsplit(req.full_url).path, a.LOGIN_PATH)
        self.assertEqual(json.loads(req.data),
            {'areaCode': 86, 'appKey': 'AP', 'phone': self.phone, 'randomCode': '123456'})
        self.assertEqual(set(vars(self.client)), {'_opener'})

    def test_invalid_phone_never_requests_network(self):
        for value in ('', None, True, 13800000000, '+8613800000000', '13800000000\n', '12800000000'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.client.request_code(value)
        self.opener.open.assert_not_called()

    def test_invalid_code_never_requests_network(self):
        for value in ('', '12345', '1234567', 123456, True, None, '12345\n', '１２３４５６'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.client.login(self.phone, value)
        self.opener.open.assert_not_called()

    def test_send_timeout_is_uncertain_and_never_retried(self):
        self.opener.open.side_effect = TimeoutError('private upstream text')
        with self.assertRaises(a.SMSDeliveryUncertain) as caught:
            self.client.request_code(self.phone)
        self.assertNotIn(self.phone, str(caught.exception))
        self.assertNotIn('private upstream', str(caught.exception))
        self.assertEqual(self.opener.open.call_count, 1)

    def test_http_error_never_exposes_query_or_retries(self):
        self.opener.open.side_effect = urllib.error.HTTPError(
            'https://example.invalid/?phone=' + self.phone, 429, 'private upstream text', {}, None)
        with self.assertRaises(a.CloudError) as caught:
            self.client.request_code(self.phone)
        self.assertNotIn(self.phone, str(caught.exception))
        self.assertEqual(self.opener.open.call_count, 1)

    def test_business_rejection_has_no_raw_message(self):
        self.response.read.return_value = json.dumps({'status': 1311, 'msg': self.phone}).encode()
        with self.assertRaises(a.AuthenticationError) as caught:
            self.client.login(self.phone, '123456')
        self.assertNotIn(self.phone, str(caught.exception))

    def test_missing_malformed_and_large_login_responses_fail_closed(self):
        for raw in (b'{}', b'[]', b'{"status":true}', b'{"status":0,"result":{}}',
                    b'{"status":0,"result":{"token":""}}',
                    b'{"status":0,"result":{"token":"bad token"}}', b'x' * 65537):
            with self.subTest(length=len(raw)), self.assertRaises(a.CloudError):
                self.response.read.return_value = raw
                self.client.login(self.phone, '123456')

    def test_malformed_send_response_is_uncertain(self):
        self.response.read.return_value = b'not JSON'
        with self.assertRaises(a.SMSDeliveryUncertain):
            self.client.request_code(self.phone)

    def test_only_two_auth_operations_are_allowed(self):
        with self.assertRaises(a.CloudError):
            self.client._request('POST', '/unobserved-refresh', {})
        self.opener.open.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
