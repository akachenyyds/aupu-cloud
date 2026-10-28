"""Cached MQTT transport and LAN failure/race coverage; no real network."""
from copy import deepcopy
import importlib
from itertools import chain, repeat
import json
from pathlib import Path
import sys
import types
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scene_fixtures import HUMAN_SCENES_OFF, NIGHT_ON, WARM_OFF, SCHEDULES_OFF

directory = Path(__file__).resolve().parents[1] / 'custom_components/aupu_cloud'
package = types.ModuleType('aupu_mqtt_transport_tests')
package.__path__ = [str(directory)]
sys.modules[package.__name__] = package
c = importlib.import_module(package.__name__ + '.client')
m = importlib.import_module(c.__package__ + '.mqtt_api')
lan = importlib.import_module(c.__package__ + '.lan')
AUTH = {'x-amz-customauthorizer-name': 'test-authorizer',
        'x-amz-customauthorizer-signature': 'test-signature', 'tokenKeyName': 'test-token'}
DEVICE = {'did': '1001', 'name': 'Test heater', 'product_name': 'Test product'}


class MQTTControls(unittest.TestCase):
    def setUp(self):
        self.probe = Mock(check=Mock(return_value=True))
        self.api = m.CachedMQTTAPI(AUTH, DEVICE, '10.23.45.67', '02:00:00:00:00:01', probe=self.probe)
        self.session = c.ShadowSession(self.api, '1001')
        self.session._connected = True
        self.session._client = Mock()
        self.updates = []
        self.ack = 'matching'
        self.apply_report = True
        self.push(20, {'2': {'properties': {'1': False}}, '3': {'properties': {'2': 0, '3': 35}},
            '5': {'properties': {'2': 5}}, '6': {'properties': {'3': False, '4': False, '5': 5,
            '6': HUMAN_SCENES_OFF, '7': 18, '9': False, '10': WARM_OFF, '11': SCHEDULES_OFF,
            '12': False, '14': NIGHT_ON}}})
        self.session._client.publish.side_effect = self.publish
        self.api.bind_command_sender(self.session._publish_mqtt_control)

    def deliver(self, suffix, body):
        self.session._on_message(None, None, SimpleNamespace(
            topic=self.session._prefix + suffix, payload=json.dumps(body).encode()))

    def push(self, version, reported):
        self.deliver('/update/documents', {'current': {'version': version,
            'state': {'reported': {'1001': deepcopy(reported)}}}})

    def publish(self, topic, payload, **options):
        body = json.loads(payload)
        if topic.endswith('/get'):
            self.deliver('/get/accepted', {'clientToken': body['clientToken'], 'version': self.session._version,
                'state': {'reported': {'1001': deepcopy(self.session._reported)}}})
        else:
            self.updates.append(body)
            if self.ack != 'none':
                self.deliver('/update/rejected' if self.ack == 'rejected' else '/update/accepted', {
                    'clientToken': body['clientToken'] if self.ack != 'foreign' else 'other-client-marker',
                    'version': self.session._version + 1, 'code': 403 if self.ack == 'rejected' else None})
            if self.apply_report:
                reported = deepcopy(self.session._reported)
                for fn, value in body['state']['desired']['1001'].items():
                    reported.setdefault(fn, {}).setdefault('properties', {}).update(value['properties'])
                self.push(self.session._version + 2, reported)
        return SimpleNamespace(rc=0)

    def test_no_account_token_and_http_path_forbidden(self):
        self.assertFalse(hasattr(self.api, '_token'))
        with self.assertRaises(c.CloudError):
            self.api._request('GET', c.FAMILY)
        self.assertEqual(self.api.discover(), [DEVICE])
        credentials = self.api.state_credentials()
        credentials['tokenKeyName'] = 'changed'
        self.assertEqual(self.api.state_credentials(), AUTH)

    def test_matching_acceptance_and_new_report_confirm(self):
        state = self.session.set_external_light(True)
        self.assertTrue(state['reported']['6']['properties']['3'])
        self.assertEqual(state['version'], 22)
        self.assertEqual(set(self.updates[0]), {'state', 'clientToken'})
        self.assertEqual(self.updates[0]['state'], {'desired': {'1001': {'6': {'properties': {'3': True}}}}})

    def test_all_existing_control_functions_use_same_mqtt_transport(self):
        self.session.set_light(True)
        self.session.set_night_light(True)
        self.session.set_external_light(True)
        self.session.set_ai_enabled(True)
        self.session.set_target_temperature(39)
        self.session.set_ai_enabled(False)
        self.session.set_mode(7)
        self.session.set_ventilation_level(1)
        self.session.set_mode(2)
        self.session.set_blow_speed(3)
        for mode in (21, 9, 4, 0):
            self.session.set_mode(mode)
        self.session.set_holiday_mode(True)
        self.session.set_unoccupied_standby(True)
        self.session.set_presence_parameter(('distance_meters', 1))
        paths = {tuple((fn, prop)) for update in self.updates
                 for fn, values in update['state']['desired']['1001'].items() for prop in values['properties']}
        self.assertTrue({('2', '1'), ('6', '3'), ('6', '4'), ('3', '2'), ('3', '3'),
                         ('6', '5'), ('5', '2'), ('6', '9'), ('6', '12'), ('6', '7')} <= paths)

    def test_configuration_merges_preserve_omitted_fields(self):
        self.session.configure_motion_night_light({'end': '05:01'})
        self.session.configure_schedule((False, 7, {'start': '07:03', 'fan_speed': 2}))
        self.session.configure_human_scene((0, {'presence_delay_seconds': 31}))
        self.assertEqual(len(self.updates), 3)
        self.assertEqual([set(x['state']['desired']['1001']) for x in self.updates], [{'6'}] * 3)

    def test_already_matching_output_not_republished(self):
        self.session.set_external_light(False)
        self.assertEqual(self.updates, [])

    def test_foreign_acceptance_cannot_complete_command_even_with_new_report(self):
        self.ack = 'foreign'
        with patch.object(self.session._command_done, 'wait', return_value=False):
            with self.assertRaisesRegex(c.CloudError, 'acceptance timed out'):
                self.session.set_external_light(True)
        self.assertEqual(len(self.updates), 1)
        self.assertIsNone(self.session._pending_command_token)

    def test_own_rejection_does_not_retry_or_assume_success(self):
        self.ack = 'rejected'
        with self.assertRaisesRegex(c.CloudError, 'command rejected'):
            self.session.set_external_light(True)
        self.assertEqual(len(self.updates), 1)

    def test_acceptance_without_device_report_is_not_success(self):
        self.apply_report = False
        with patch.object(c.time, 'monotonic', side_effect=chain((100, 100), repeat(113))):
            with self.assertRaisesRegex(c.CloudError, 'report not confirmed'):
                self.session.set_external_light(True)
        self.assertEqual(len(self.updates), 1)

    def test_offline_or_unknown_local_identity_blocks_control(self):
        for state in (False, None):
            with self.subTest(state=state):
                self.probe.check.return_value = state
                with self.assertRaises(c.CloudError):
                    self.session.set_external_light(True)
        self.assertEqual(self.updates, [])

    def test_identity_lost_between_query_and_publish_blocks_control(self):
        self.probe.check.side_effect = [True, False]
        with self.assertRaises(c.CloudError):
            self.session.set_external_light(True)
        self.assertEqual(self.updates, [])

    def test_foreign_device_and_unknown_property_rejected(self):
        with self.assertRaises(c.CloudError):
            self.api.set_light('1002', True)
        with self.assertRaises(c.CloudError):
            self.api._set_property('1001', '6', '999', True)
        self.assertEqual(self.updates, [])

    def test_publish_failure_is_safe_error_and_no_retry(self):
        self.session._client.publish.side_effect = OSError('unsafe low-level detail')
        with self.assertRaises(c.CloudError) as caught:
            self.session._publish_mqtt_control('1001', '6', '3', True)
        self.assertNotIn('unsafe', str(caught.exception))
        self.session._client.publish.assert_called_once()

    def test_disconnect_releases_pending_command_and_old_close_keeps_new_sender(self):
        self.session._pending_command_token = 'pending'
        self.session._on_disconnect(None, None, None, None, None)
        self.assertTrue(self.session._command_done.is_set())
        self.assertIsInstance(self.session._command_error, c.CloudError)
        new_sender = Mock()
        self.api.bind_command_sender(new_sender)
        self.session.close()
        self.api.set_external_light('1001', True)
        new_sender.assert_called_once_with('1001', '6', '3', True)

    def test_close_removes_old_sender(self):
        self.session.close()
        with self.assertRaises(c.CloudError):
            self.api.set_external_light('1001', True)

    def test_extra_header_or_linebreak_credentials_rejected(self):
        for auth in (dict(AUTH, Authorization='Bearer test'), dict(AUTH, tokenKeyName='bad\nvalue')):
            with self.assertRaises(c.AuthenticationError):
                m.CachedMQTTAPI(auth, DEVICE, '10.23.45.67', '02:00:00:00:00:01')


class LocalIdentity(unittest.TestCase):
    def test_private_ipv4_only_and_no_command_injection(self):
        for address in ('8.8.8.8', '127.0.0.1', '::1', '10.23.45.67;id', None):
            with self.assertRaises(ValueError):
                lan.normalize_host(address)
        self.assertEqual(lan.normalize_host('10.23.45.67'), '10.23.45.67')

    def test_valid_unicast_mac_only(self):
        for mac in ('ff:ff:ff:ff:ff:ff', '00:00:00:00:00:00', '01:00:00:00:00:01', 'x'):
            with self.assertRaises(ValueError):
                lan.normalize_mac(mac)
        self.assertEqual(lan.normalize_mac('02:00:00:00:00:0A'), '02:00:00:00:00:0a')

    def test_both_new_icmp_and_exact_mac_required(self):
        probe = lan.LANProbe('10.23.45.67', '02:00:00:00:00:01')
        with patch.object(lan, '_ping', return_value=True), patch.object(lan, '_neighbor_mac', return_value='02:00:00:00:00:01'):
            self.assertIs(probe.check(), True)
            self.assertIsNone(probe.last_failure)
        with patch.object(lan, '_ping', return_value=True), patch.object(lan, '_neighbor_mac', return_value='02:00:00:00:00:02'):
            self.assertIs(probe.check(), False)
            self.assertEqual(probe.last_failure, 'neighbor_mismatch')
        with patch.object(lan, '_ping', return_value=False), patch.object(lan, '_neighbor_mac') as neighbor:
            self.assertIs(probe.check(), False)
            neighbor.assert_not_called()
            self.assertEqual(probe.last_failure, 'icmp_unreachable')

    def test_permission_and_missing_neighbor_source_are_unknown(self):
        probe = lan.LANProbe('10.23.45.67', '02:00:00:00:00:01')
        with patch.object(lan, '_ping', side_effect=PermissionError):
            self.assertIsNone(probe.check())
            self.assertEqual(probe.last_error_type, 'PermissionError')
        with patch.object(lan, '_ping', return_value=True), patch.object(lan, '_neighbor_mac', side_effect=FileNotFoundError):
            self.assertIsNone(probe.check())

    def test_linux_arp_requires_completed_entry_and_exact_ip(self):
        header = 'IP address HW type Flags HW address Mask Device\n'
        for flags, expected in (('0x2', '02:00:00:00:00:01'), ('0x0', None)):
            text = header + f'10.23.45.67 0x1 {flags} 02:00:00:00:00:01 * eth0\n'
            with patch.object(lan.sys, 'platform', 'linux'), patch.object(lan.Path, 'read_text', return_value=text):
                self.assertEqual(lan._neighbor_mac('10.23.45.67'), expected)
                self.assertIsNone(lan._neighbor_mac('10.23.45.68'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
