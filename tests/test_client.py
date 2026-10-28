"""Security, stale-state and physical-light behavior boundaries; no network."""
import importlib.util
from copy import deepcopy
from itertools import chain, repeat
import json
from pathlib import Path
import sys
import types
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from scene_fixtures import HUMAN_SCENES_OFF, NIGHT_ON, WARM_OFF, SCHEDULES_OFF

path = Path(__file__).resolve().parents[1] / 'custom_components/aupu_cloud/client.py'
package = types.ModuleType('aupu_standalone_test')
package.__path__ = [str(path.parent)]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location('aupu_standalone_test.client', path)
client = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = client
spec.loader.exec_module(client)


class Boundaries(unittest.TestCase):
    def setUp(self):
        self.api = client.CloudAPI('test-token')
        self.api._owned_devices = {'1001'}
        self.api.device_online = Mock(return_value=True)
        self.session = client.ShadowSession(self.api, '1001')
        self.session._connected = True
        self.session._device_online = True
        self.session._client = Mock()
        def answer_query(topic, payload, **kwargs):
            body = {'clientToken': json.loads(payload)['clientToken'],
                    'version': self.session._version,
                    'state': {'reported': {'1001': deepcopy(self.session._reported)}}}
            self.session._on_message(None, None, SimpleNamespace(
                topic=self.session._prefix + '/get/accepted', payload=json.dumps(body).encode()))
            return SimpleNamespace(rc=0)
        self.session._client.publish.side_effect = answer_query

    def message(self, version, light, did='1001', suffix='/update/documents'):
        body = {'version': version, 'state': {'reported': {did: {'2': {'properties': {'1': light}}}}}}
        if suffix == '/update/documents':
            body = {'current': body}
        return SimpleNamespace(topic=self.session._prefix + suffix, payload=json.dumps(body).encode())

    def test_old_shadow_cannot_roll_back_state(self):
        self.session._on_message(None, None, self.message(20, True))
        self.session._on_message(None, None, self.message(19, False, suffix='/update/documents'))
        self.assertTrue(self.session.snapshot()['reported']['2']['properties']['1'])
        self.assertEqual(self.session.snapshot()['version'], 20)

    def test_wrong_device_and_unsubscribed_topic_ignored(self):
        self.session._on_message(None, None, self.message(20, True, did='9999'))
        self.session._on_message(None, None, self.message(21, True, suffix='/unsubscribed'))
        self.assertIsNone(self.session.snapshot()['reported'])

    def test_already_on_does_not_cycle_fixture(self):
        self.session._on_message(None, None, self.message(20, True))
        self.api.set_light = Mock()
        self.session.set_light(True)
        self.api.set_light.assert_not_called()

    def test_disconnect_marks_state_unavailable(self):
        self.session._on_message(None, None, self.message(20, True))
        self.session._on_disconnect(None, None, None, None, None)
        self.assertFalse(self.session.connected)
        self.assertTrue(self.session._query_done.is_set())
        with self.assertRaises(client.CloudError):
            self.session.set_light(False)

    def test_other_device_control_blocked(self):
        self.api._request = Mock()
        with self.assertRaises(client.CloudError):
            self.api.set_light('9999', True)
        self.api._request.assert_not_called()

    def test_light_rejects_numeric_pseudo_boolean(self):
        self.api._request = Mock()
        with self.assertRaises(client.CloudError):
            self.api.set_light('1001', 1)
        self.api._request.assert_not_called()

    def test_no_credential_redirect(self):
        self.assertIsNone(client._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.com'))

    def test_unsupported_operation_and_query_injection_blocked(self):
        with self.assertRaises(client.CloudError):
            self.api._request('POST', '/unobserved/control')
        with self.assertRaises(client.CloudError):
            self.api._request('GET', client.DEVICES, family_id='1&extra=2')

    def test_same_version_report_is_not_command_confirmation(self):
        self.session._on_message(None, None, self.message(20, False))
        self.api.set_light = Mock(side_effect=lambda *_: self.session._on_message(None, None, self.message(20, True)))
        with patch.object(client.time, 'monotonic', side_effect=chain((100, 100), repeat(113))):
            with self.assertRaises(client.CloudError):
                self.session.set_light(True)

    def test_newer_report_confirms_command(self):
        self.session._on_message(None, None, self.message(20, False))
        self.api.set_light = Mock(side_effect=lambda *_: self.session._on_message(None, None, self.message(21, True)))
        self.assertEqual(self.session.set_light(True)['version'], 21)

    def test_stale_cached_identical_value_is_rechecked(self):
        self.session._on_message(None, None, self.message(20, True))
        self.session._received_monotonic = 0
        self.session._refresh_locked = Mock(side_effect=lambda: self.session._on_message(None, None, self.message(21, False)))
        self.api.set_light = Mock(side_effect=lambda *_: self.session._on_message(None, None, self.message(22, True)))
        self.session.set_light(True)
        self.session._refresh_locked.assert_called_once()
        self.api.set_light.assert_called_once_with('1001', True)

    def test_recent_cached_value_is_rechecked_after_lost_wall_push(self):
        self.session._on_message(None, None, self.message(20, True))
        self.session._refresh_locked = Mock(side_effect=lambda:
            self.session._on_message(None, None, self.message(21, False)))
        self.api.set_light = Mock(side_effect=lambda *_:
            self.session._on_message(None, None, self.message(22, True)))
        self.session.set_light(True)
        self.session._refresh_locked.assert_called_once()
        self.api.set_light.assert_called_once_with('1001', True)

    def test_closed_session_ignores_late_callbacks_and_wakes_waiters(self):
        self.session._on_message(None, None, self.message(20, True))
        self.session.listener = Mock()
        self.session.close()
        self.session._on_message(None, None, self.message(21, False))
        self.session._on_disconnect(None, None, None, None, None)
        self.assertEqual(self.session.snapshot()['version'], 20)
        self.assertFalse(self.session.connected)
        self.assertTrue(self.session._query_done.is_set())
        self.session.listener.assert_not_called()
        with self.assertRaises(client.CloudError):
            self.session.start()

    def test_state_server_unavailable_is_not_account_reauth(self):
        self.session._on_connect(None, None, None, SimpleNamespace(is_failure=True, value=136), None)
        self.assertIsInstance(self.session._error, client.CloudError)
        self.assertNotIsInstance(self.session._error, client.AuthenticationError)

    def test_query_timeout_marks_session_disconnected(self):
        self.session._on_message(None, None, self.message(20, True))
        self.session._client = Mock()
        self.session._client.publish.return_value.rc = 0
        with patch.object(self.session._query_done, 'wait', return_value=False):
            with self.assertRaises(client.CloudError):
                self.session.refresh()
        self.assertFalse(self.session.connected)

    def query_response(self, token, version=20, light=True, rejected=False):
        body = {'version': version, 'state': {'reported': {'1001': {'2': {'properties': {'1': light}}}}}}
        if token is not None:
            body['clientToken'] = token
        if rejected:
            body = {'code': 404, 'clientToken': token}
        suffix = '/get/rejected' if rejected else '/get/accepted'
        return SimpleNamespace(topic=self.session._prefix + suffix, payload=json.dumps(body).encode())

    def test_foreign_and_tokenless_query_replies_cannot_complete_our_refresh(self):
        self.session._on_message(None, None, self.message(20, False))
        self.session._client.publish.side_effect = None
        self.session._client.publish.return_value.rc = 0
        self.session._publish_query()
        for token in ('another-client', None):
            self.session._on_message(None, None, self.query_response(token, 999, True))
            self.assertFalse(self.session._query_done.is_set())
            self.assertEqual(self.session.snapshot()['version'], 20)
            self.assertFalse(self.session.snapshot()['reported']['2']['properties']['1'])
        self.assertFalse(self.session._ready.is_set())

    def test_unrelated_rejected_query_does_not_disconnect_our_session(self):
        self.session._on_message(None, None, self.message(20, False))
        self.session._pending_query_token = 'our-query'
        self.session._on_message(None, None, self.query_response('other-query', rejected=True))
        self.assertTrue(self.session.connected)
        self.assertIsNone(self.session._error)
        self.assertFalse(self.session._query_done.is_set())
        self.session._on_message(None, None, self.query_response('our-query', rejected=True))
        self.assertFalse(self.session.connected)
        self.assertIsInstance(self.session._error, client.CloudError)
        self.assertIsNone(self.session._pending_query_token)

    def test_unsolicited_query_reply_without_pending_request_is_ignored(self):
        self.session._on_message(None, None, self.query_response('another-query'))
        self.assertIsNone(self.session.snapshot()['reported'])
        self.assertFalse(self.session._ready.is_set())
        self.assertFalse(self.session._query_done.is_set())

    def test_newer_push_is_preserved_when_matching_query_reply_arrives_later(self):
        self.session._pending_query_token = 'our-query'
        self.session._on_message(None, None, self.message(21, True))
        self.assertFalse(self.session._ready.is_set())
        self.assertFalse(self.session._query_done.is_set())
        self.session._on_message(None, None, self.query_response('our-query', 20, False))
        self.assertEqual(self.session.snapshot()['version'], 21)
        self.assertTrue(self.session.snapshot()['reported']['2']['properties']['1'])
        self.assertTrue(self.session._query_done.is_set())
        self.assertTrue(self.session._ready.is_set())
        self.assertIsNone(self.session._pending_query_token)

    def test_each_refresh_uses_distinct_marker_and_only_publishes_read_requests(self):
        self.session._on_message(None, None, self.message(20, False))
        self.session.refresh()
        self.session.refresh()
        calls = self.session._client.publish.call_args_list
        markers = [json.loads(call.args[1])['clientToken'] for call in calls]
        self.assertEqual(len(set(markers)), 2)
        self.assertTrue(all(len(marker.encode()) <= 64 for marker in markers))
        self.assertTrue(all(call.args[0] == self.session._prefix + '/get' for call in calls))
        self.assertIsNone(self.session._pending_query_token)

    def test_foreign_get_reply_cannot_allow_control_after_failed_own_query(self):
        self.session._on_message(None, None, self.message(20, False))
        def foreign_reply(*args, **kwargs):
            self.session._on_message(None, None, self.query_response('another-client', 21, False))
            self.assertFalse(self.session._query_done.is_set())
            return SimpleNamespace(rc=0)
        self.session._client.publish.side_effect = foreign_reply
        self.api.set_light = Mock()
        with patch.object(self.session._query_done, 'wait', return_value=False):
            with self.assertRaises(client.CloudError):
                self.session.set_light(True)
        self.api.set_light.assert_not_called()

    def test_failed_query_publish_marks_session_unavailable_and_discards_marker(self):
        self.session._on_message(None, None, self.message(20, False))
        self.session._client.publish.side_effect = None
        self.session._client.publish.return_value.rc = client.mqtt.MQTT_ERR_NO_CONN
        with self.assertRaises(client.CloudError):
            self.session.refresh()
        self.assertFalse(self.session.connected)
        self.assertIsNone(self.session._pending_query_token)
        self.assertTrue(self.session._query_done.is_set())

    def test_close_discards_query_marker_and_ignores_its_late_reply(self):
        self.session._on_message(None, None, self.message(20, False))
        self.session._pending_query_token = 'our-query'
        self.session.close()
        self.session._on_message(None, None, self.query_response('our-query', 21, True))
        self.assertIsNone(self.session._pending_query_token)
        self.assertEqual(self.session.snapshot()['version'], 20)

    def test_verified_mode_and_level_build_exact_single_property_commands(self):
        self.api._request = Mock()
        self.api.set_mode('1001', 7)
        payload = self.api._request.call_args.args[2]
        self.assertEqual(payload['sendBody']['state']['desired'], {'1001': {'3': {'properties': {'2': 7}}}})
        self.api.set_ventilation_level('1001', 3)
        payload = self.api._request.call_args.args[2]
        self.assertEqual(payload['sendBody']['state']['desired'], {'1001': {'6': {'properties': {'5': 3}}}})

    def test_unknown_modes_and_noninteger_levels_never_reach_network(self):
        self.api._request = Mock()
        for mode in (True, 99, '7', 7.0):
            with self.assertRaises(client.CloudError):
                self.api.set_mode('1001', mode)
        for level in (True, 0, 6, '3', 3.0):
            with self.assertRaises(client.CloudError):
                self.api.set_ventilation_level('1001', level)
        self.api._request.assert_not_called()

    def test_ventilation_level_cannot_start_or_change_another_function(self):
        message = self.message(20, True)
        body = json.loads(message.payload)['current']
        body['state']['reported']['1001']['3'] = {'properties': {'2': 18}}
        message.payload = json.dumps({'current': body}).encode()
        self.session._on_message(None, None, message)
        self.api.set_ventilation_level = Mock()
        with self.assertRaises(client.CloudError):
            self.session.set_ventilation_level(3)
        self.api.set_ventilation_level.assert_not_called()

    def test_copy_pasted_bearer_token_is_normalized_once(self):
        self.assertEqual(client.CloudAPI('  Bearer test-token  ')._token, 'test-token')

    def test_malformed_family_is_a_handled_cloud_error(self):
        self.api._request = Mock(return_value={'list': [None]})
        with self.assertRaises(client.CloudError):
            self.api.discover()

    def test_equal_version_cannot_change_reported_document(self):
        self.session._on_message(None, None, self.message(20, False))
        self.session._on_message(None, None, self.message(20, True))
        self.assertFalse(self.session.snapshot()['reported']['2']['properties']['1'])

    def test_night_light_command_does_not_target_builtin_or_external_lighting(self):
        self.api._request = Mock()
        self.api.set_night_light('1001', False)
        payload = self.api._request.call_args.args[2]
        self.assertEqual(payload['sendBody']['state']['desired'], {'1001': {'6': {'properties': {'4': False}}}})
        with self.assertRaises(client.CloudError):
            self.api.set_night_light('1001', 0)

    def test_cloud_query_timestamp_does_not_refresh_device_report_timestamp(self):
        message = self.message(20, True)
        body = json.loads(message.payload)['current']
        body['timestamp'] = 2000
        body['metadata'] = {'reported': {'1001': {'2': {'properties': {'1': {'timestamp': 1000}}}}}}
        message.payload = json.dumps({'current': body}).encode()
        self.session._on_message(None, None, message)
        body['timestamp'] = 3000
        message.payload = json.dumps({'current': body}).encode()
        self.session._on_message(None, None, message)
        self.assertEqual(self.session.snapshot()['reported_updated_at'], 1000)

    def test_blow_speed_uses_separate_field_and_requires_blow_mode(self):
        self.api._request = Mock()
        self.api.set_blow_speed('1001', 3)
        self.assertEqual(self.api._request.call_args.args[2]['sendBody']['state']['desired'],
                         {'1001': {'5': {'properties': {'2': 3}}}})
        message=self.message(20, True)
        body=json.loads(message.payload)['current']
        body['state']['reported']['1001']['3']={'properties':{'2':7}}
        message.payload=json.dumps({'current': body}).encode()
        self.session._on_message(None,None,message)
        self.api.set_blow_speed=Mock()
        with self.assertRaises(client.CloudError):self.session.set_blow_speed(3)
        self.api.set_blow_speed.assert_not_called()

    def mode_message(self, version, mode, temperature=40):
        message = self.message(version, True)
        body = json.loads(message.payload)['current']
        body['state']['reported']['1001']['3'] = {'properties': {'2': mode, '3': temperature}}
        message.payload = json.dumps({'current': body}).encode()
        return message

    def test_temperature_bounds_types_and_exact_command(self):
        self.api._request = Mock()
        for invalid in (True, False, 29, 43, 10**1000, 30.5, '40', None, float('nan'), float('inf')):
            with self.assertRaises(client.CloudError):
                self.api.set_target_temperature('1001', invalid)
        self.api._request.assert_not_called()
        for temperature in (30, 39.0, 42):
            self.api.set_target_temperature('1001', temperature)
            self.assertEqual(self.api._request.call_args.args[2]['sendBody']['state']['desired'],
                             {'1001': {'3': {'properties': {'3': int(temperature)}}}})

    def test_temperature_never_starts_heating_as_side_effect(self):
        for mode in (0, 7, 2, 4, 9, 21):
            self.session._on_message(None, None, self.mode_message(20 + mode, mode))
            self.api.set_target_temperature = Mock()
            with self.assertRaises(client.CloudError):
                self.session.set_target_temperature(39)
            self.api.set_target_temperature.assert_not_called()
            self.session._version = -1

    def test_temperature_needs_new_report_in_ai_mode(self):
        self.session._on_message(None, None, self.mode_message(20, 18))
        self.api.set_target_temperature = Mock(side_effect=lambda *_:
            self.session._on_message(None, None, self.mode_message(21, 18, 39)))
        self.assertEqual(self.session.set_target_temperature(39.0)['version'], 21)
        self.api.set_target_temperature.assert_called_once_with('1001', 39)

    def test_climate_off_does_not_stop_other_functions(self):
        self.api.set_mode = Mock()
        for version, mode in enumerate((0, 7, 2, 4, 9, 21), 20):
            self.session._on_message(None, None, self.mode_message(version, mode))
            self.session.set_ai_enabled(False)
        self.api.set_mode.assert_not_called()

    def test_climate_off_stops_ai_and_on_uses_verified_mode(self):
        self.session._on_message(None, None, self.mode_message(20, 18))
        self.api.set_mode = Mock(side_effect=lambda _, mode:
            self.session._on_message(None, None, self.mode_message(self.session._version + 1, mode)))
        self.session.set_ai_enabled(False)
        self.session.set_ai_enabled(True)
        self.assertEqual(self.api.set_mode.call_args_list[0].args, ('1001', 0))
        self.assertEqual(self.api.set_mode.call_args_list[1].args, ('1001', 18))

    def test_external_output_is_independent_and_boolean_only(self):
        self.api._request = Mock()
        self.api.set_external_light('1001', True)
        self.assertEqual(self.api._request.call_args.args[2]['sendBody']['state']['desired'],
                         {'1001': {'6': {'properties': {'3': True}}}})
        with self.assertRaises(client.CloudError):
            self.api.set_external_light('1001', 1)

    def test_physical_offline_blocks_commands_even_if_shadow_query_works(self):
        self.session._on_message(None, None, self.message(20, False))
        self.api.device_online.return_value = False
        self.api.set_light = Mock()
        with self.assertRaises(client.CloudError):
            self.session.set_light(True)
        self.api.set_light.assert_not_called()
        self.assertTrue(self.session.snapshot()['connected'])
        self.assertFalse(self.session.snapshot()['device_online'])

    def test_unknown_online_status_blocks_commands(self):
        self.session._on_message(None, None, self.message(20, False))
        self.api.device_online.return_value = None
        self.api.set_mode = Mock()
        with self.assertRaises(client.CloudError):
            self.session.set_mode(18)
        self.api.set_mode.assert_not_called()

    def test_device_list_status_uses_own_family_and_strict_calibrated_values(self):
        del self.api.device_online
        self.api._device_families = {'1001': 77}
        for status, expected in ((4, True), (5, False), (0, None), (True, None), ('4', None)):
            self.api._request = Mock(return_value={'device':[{'did':1001,'status':status},
                                                          {'did':9999,'status':4}]})
            self.assertIs(self.api.device_online('1001'), expected)
            self.api._request.assert_called_once_with('GET', client.DEVICES, family_id=77)

    def settings_message(self, version, props):
        body = {'version': version, 'state': {'reported': {'1001': {'6': {'properties': props}}}}}
        return SimpleNamespace(topic=self.session._prefix + '/update/documents', payload=json.dumps({'current': body}).encode())

    def test_scene_merge_uses_fresh_blob_and_preserves_other_scene(self):
        self.session._on_message(None, None, self.settings_message(20, {'6': HUMAN_SCENES_OFF}))
        fresh = client.patch_human_scene_enabled(HUMAN_SCENES_OFF, 7, True)
        self.session._refresh_locked = Mock(side_effect=lambda:
            self.session._on_message(None, None, self.settings_message(21, {'6': fresh})))
        self.api._set_human_scenes = Mock(side_effect=lambda _, value:
            self.session._on_message(None, None, self.settings_message(22, {'6': value})))
        result = self.session.set_human_scene_enabled((0, True))
        sent = self.api._set_human_scenes.call_args.args[1]
        self.assertEqual(sent, client.patch_human_scene_enabled(fresh, 0, True))
        scenes = client.decode_human_scenes(sent)
        self.assertTrue(scenes[0]['enabled'])
        self.assertTrue(scenes[2]['enabled'])
        self.assertEqual(result['version'], 22)

    def test_night_automation_noop_never_toggles_actual_light(self):
        self.session._on_message(None, None, self.settings_message(20, {'14': NIGHT_ON, '4': False}))
        self.api._set_motion_night_light = Mock()
        self.api.set_night_light = Mock()
        self.session.set_motion_night_light_enabled(True)
        self.api._set_motion_night_light.assert_not_called()
        self.api.set_night_light.assert_not_called()
        self.assertFalse(self.session.snapshot()['reported']['6']['properties']['4'])

    def test_motion_night_patch_keeps_schedule_and_output_separate(self):
        self.session._on_message(None, None, self.settings_message(20, {'14': NIGHT_ON, '4': True}))
        self.api._set_motion_night_light = Mock(side_effect=lambda _, value:
            self.session._on_message(None, None, self.settings_message(21, {'14': value, '4': True})))
        result = self.session.set_motion_night_light_enabled(False)
        sent = self.api._set_motion_night_light.call_args.args[1]
        self.assertEqual(sent, '00' + NIGHT_ON[2:])
        self.assertTrue(result['reported']['6']['properties']['4'])

    def test_missing_or_unknown_scene_format_never_sends_configuration(self):
        self.api._set_human_scenes = Mock()
        self.api._set_motion_night_light = Mock()
        self.session._on_message(None, None, self.settings_message(20, {'6': '00', '14': None}))
        with self.assertRaises(client.CloudError):
            self.session.set_human_scene_enabled((0, True))
        with self.assertRaises(client.CloudError):
            self.session.set_motion_night_light_enabled(True)
        self.api._set_human_scenes.assert_not_called()
        self.api._set_motion_night_light.assert_not_called()

    def test_invalid_scene_and_standby_arguments_do_not_reach_network(self):
        self.api._request = Mock()
        for setting in ((True, True), (99, True), (0, 1), (0,), None):
            with self.assertRaises(client.CloudError):
                self.session.set_human_scene_enabled(setting)
        for value in (1, 'true', None):
            with self.assertRaises(client.CloudError):
                self.session.set_motion_night_light_enabled(value)
            with self.assertRaises(client.CloudError):
                self.api.set_unoccupied_standby('1001', value)
        with self.assertRaises(client.CloudError):
            self.api._set_human_scenes('1001', '00')
        with self.assertRaises(client.CloudError):
            self.api._set_motion_night_light('9999', NIGHT_ON)
        self.api._request.assert_not_called()

    def test_offline_scene_control_blocked_before_blob_write(self):
        self.session._on_message(None, None, self.settings_message(20, {'6': HUMAN_SCENES_OFF}))
        self.api.device_online.return_value = False
        self.api._set_human_scenes = Mock()
        with self.assertRaises(client.CloudError):
            self.session.set_human_scene_enabled((0, True))
        self.api._set_human_scenes.assert_not_called()


    def test_schedule_fresh_merge_preserves_other_enabled_record_and_unknown_bytes(self):
        self.session._on_message(None, None, self.settings_message(20, {'11': SCHEDULES_OFF}))
        fresh = bytearray.fromhex(client.patch_schedule_enabled(SCHEDULES_OFF, 4, True))
        fresh[-1] = 0xA5
        fresh = fresh.hex()
        self.session._refresh_locked = Mock(side_effect=lambda:
            self.session._on_message(None, None, self.settings_message(21, {'11': fresh})))
        self.api._set_schedules = Mock(side_effect=lambda _, value, warm=False:
            self.session._on_message(None, None, self.settings_message(22, {'11': value})))
        result = self.session.set_schedule_enabled((False, 7, True))
        target = self.api._set_schedules.call_args.args[1]
        self.assertEqual(target, client.patch_schedule_enabled(fresh, 7, True))
        self.assertEqual(target[-2:], 'a5')
        self.assertEqual([r['enabled'] for r in client.decode_schedules(target)], [True, True, False])
        self.assertEqual(result['version'], 22)

    def test_warm_schedule_preserves_padded_and_compact_layout_without_starting_heat(self):
        for value in (WARM_OFF, WARM_OFF[:24]):
            with self.subTest(length=len(value)):
                self.session._on_message(None, None, self.settings_message(self.session._version + 1, {'10': value}))
                self.api._set_schedules = Mock(side_effect=lambda _, target, warm=False:
                    self.session._on_message(None, None, self.settings_message(self.session._version + 1, {'10': target})))
                self.api.set_mode = Mock()
                self.session.set_schedule_enabled((True, 18, True))
                target = self.api._set_schedules.call_args.args[1]
                self.assertEqual(target, value[:2] + '01' + value[4:])
                self.api._set_schedules.assert_called_once_with('1001', target, warm=True)
                self.api.set_mode.assert_not_called()

    def test_schedule_unknown_count_clock_marker_and_duplicate_mode_rejected(self):
        self.api._set_schedules = Mock()
        variants = ['00', SCHEDULES_OFF[:12], '04'+SCHEDULES_OFF[2:]]
        for index, value in ((2, 9), (3, 24), (4, 60), (22, 7)):
            raw = bytearray.fromhex(SCHEDULES_OFF)
            raw[index] = value
            variants.append(raw.hex())
        for value in variants:
            self.session._on_message(None, None, self.settings_message(self.session._version+1, {'11': value}))
            with self.assertRaises(client.CloudError):
                self.session.set_schedule_enabled((False, 7, True))
        self.api._set_schedules.assert_not_called()

    def test_schedule_and_holiday_invalid_args_and_foreign_device_never_reach_network(self):
        self.api._request = Mock()
        for setting in ((1,18,True),(True,7,True),(False,18,True),(False,7,1),None,(False,)):
            with self.assertRaises(client.CloudError):self.session.set_schedule_enabled(setting)
        for value in (1, 'true', None):
            with self.assertRaises(client.CloudError):self.session.set_holiday_mode(value)
            with self.assertRaises(client.CloudError):self.api.set_holiday_mode('1001', value)
        with self.assertRaises(client.CloudError):self.api._set_schedules('9999', WARM_OFF, warm=True)
        with self.assertRaises(client.CloudError):self.api._set_schedules('1001', '00')
        self.api._request.assert_not_called()

    def test_offline_blocks_all_added_schedule_and_holiday_controls(self):
        self.session._on_message(None, None, self.settings_message(20, {'10':WARM_OFF,'11':SCHEDULES_OFF,'9':False}))
        self.api.device_online.return_value=False
        self.api._set_schedules=Mock();self.api.set_holiday_mode=Mock()
        for setting in ((True,18,True),(False,7,True),(False,4,True),(False,21,True)):
            with self.assertRaises(client.CloudError):self.session.set_schedule_enabled(setting)
        with self.assertRaises(client.CloudError):self.session.set_holiday_mode(True)
        self.api._set_schedules.assert_not_called();self.api.set_holiday_mode.assert_not_called()


    def test_presence_parameters_all_eight_captured_combinations(self):
        for sensitivity, prefix in (('高',16),('低',48)):
            for distance in range(1,5):
                self.assertEqual(client.decode_presence_parameters(prefix+distance),
                    {'sensitivity':sensitivity,'distance_meters':distance})
        for value in (True,17.0,'18',None,0,16,21,33):
            with self.assertRaises(ValueError):client.decode_presence_parameters(value)

    def test_presence_patch_preserves_the_other_subfield(self):
        for original in (17,18,19,20,49,50,51,52):
            parsed=client.decode_presence_parameters(original)
            self.assertEqual(client.decode_presence_parameters(client.patch_presence_parameter(original,'distance_meters',4))['sensitivity'],parsed['sensitivity'])
            self.assertEqual(client.decode_presence_parameters(client.patch_presence_parameter(original,'sensitivity','低'))['distance_meters'],parsed['distance_meters'])

    def test_presence_patch_merges_fresh_distance_not_stale_cache(self):
        self.session._on_message(None,None,self.settings_message(20,{'7':18}))
        self.session._refresh_locked=Mock(side_effect=lambda:
            self.session._on_message(None,None,self.settings_message(21,{'7':51})))
        self.api._set_presence_parameters=Mock(side_effect=lambda _,value:
            self.session._on_message(None,None,self.settings_message(22,{'7':value})))
        result=self.session.set_presence_parameter(('sensitivity','高'))
        self.api._set_presence_parameters.assert_called_once_with('1001',19)
        self.assertEqual(result['reported']['6']['properties']['7'],19)

    def test_presence_noop_never_controls_lights_or_mode(self):
        self.session._on_message(None,None,self.settings_message(20,{'7':18}))
        self.api._request=Mock()
        self.session.set_presence_parameter(('distance_meters',2))
        self.session.set_presence_parameter(('sensitivity','高'))
        self.api._request.assert_not_called()

    def test_presence_invalid_types_foreign_devices_and_unknown_format_never_write(self):
        self.api._request=Mock()
        for setting in (None,('distance_meters',True),('distance_meters',0),('distance_meters',5),('distance_meters',2.0),('sensitivity','中'),('unknown',2)):
            with self.assertRaises(client.CloudError):self.session.set_presence_parameter(setting)
        for value in (True,18.0,21,None):
            with self.assertRaises(client.CloudError):self.api._set_presence_parameters('1001',value)
        with self.assertRaises(client.CloudError):self.api._set_presence_parameters('9999',18)
        self.session._on_message(None,None,self.settings_message(20,{'7':33}))
        with self.assertRaises(client.CloudError):self.session.set_presence_parameter(('distance_meters',2))
        self.api._request.assert_not_called()

    def test_offline_blocks_presence_parameters_before_write(self):
        self.session._on_message(None,None,self.settings_message(20,{'7':18}))
        self.api.device_online.return_value=False;self.api._set_presence_parameters=Mock()
        with self.assertRaises(client.CloudError):self.session.set_presence_parameter(('distance_meters',4))
        self.api._set_presence_parameters.assert_not_called()

    def test_advanced_parameters_match_independent_wire_examples(self):
        self.assertEqual(client.patch_human_scene(HUMAN_SCENES_OFF, 0,
            {'presence_delay_seconds': 61, 'absence_delay_seconds': 31, 'start': '19:01', 'end': '20:58'}),
            '0400003d001f0000020000000000001301143a' + HUMAN_SCENES_OFF[38:])
        self.assertEqual(client.patch_motion_night_light(NIGHT_ON,
            {'start': '23:01', 'end': '05:01', 'repeat_days': ['tue','wed','thu','fri','sat','sun']}),
            '010817010501001e7e')
        self.assertEqual(client.patch_schedule(SCHEDULES_OFF, 7, {'start': '06:01'}),
            '030008060180070000050000020000000000080c1980040000000000000000000000080e32801500000000000000000000')
        compact = WARM_OFF[:24]
        self.assertEqual(client.patch_schedule(compact, 18,
            {'start': '08:01:00', 'repeat_days': ['sun'], 'target_temperature': 34}, warm=True),
            '010008080140123c00220000')

    def test_all_weekday_bits_match_individual_calibration(self):
        masks = [('mon',1),('tue',2),('wed',4),('thu',8),('fri',16),('sat',32),('sun',64)]
        for day, mask in masks:
            night = client.patch_motion_night_light(NIGHT_ON, {'repeat_days': [day]})
            schedule = client.patch_schedule(SCHEDULES_OFF, 7, {'repeat_days': [day]})
            self.assertEqual(bytes.fromhex(night)[8], mask)
            self.assertEqual(bytes.fromhex(schedule)[5], mask)
        self.assertEqual(client.patch_schedule(SCHEDULES_OFF, 7, {'repeat_days': []}), SCHEDULES_OFF)

    def test_parameter_merge_uses_fresh_other_scene_and_preserves_action_bytes(self):
        self.session._on_message(None,None,self.settings_message(20,{'6':HUMAN_SCENES_OFF}))
        fresh = bytearray.fromhex(client.patch_human_scene_enabled(HUMAN_SCENES_OFF,7,True))
        fresh[8] = 0xA5
        fresh = fresh.hex()
        self.session._refresh_locked = Mock(side_effect=lambda:
            self.session._on_message(None,None,self.settings_message(21,{'6':fresh})))
        self.api._set_human_scenes = Mock(side_effect=lambda _,value:
            self.session._on_message(None,None,self.settings_message(22,{'6':value})))
        result = self.session.configure_human_scene((0,{'absence_delay_seconds':301}))
        raw = bytes.fromhex(self.api._set_human_scenes.call_args.args[1])
        self.assertEqual(raw[4:6], b'\x01\x2d')
        self.assertEqual(raw[8], 0xA5)
        self.assertEqual(raw[37], 1)  # concurrently enabled ventilation scene
        self.assertEqual(raw[1], 0)
        self.assertEqual(result['version'],22)

    def test_parameter_noop_does_not_toggle_any_lamp_or_heat(self):
        self.session._on_message(None,None,self.settings_message(20,{
            '6':HUMAN_SCENES_OFF,'14':NIGHT_ON,'10':WARM_OFF,'11':SCHEDULES_OFF}))
        self.api._request=Mock()
        self.session.configure_human_scene((0,{'start':'09:10','presence_delay_seconds':0}))
        self.session.configure_motion_night_light({'start':'20:10','absence_delay_seconds':30})
        self.session.configure_schedule((True,18,{'target_temperature':35}))
        self.session.configure_schedule((False,7,{'fan_speed':5}))
        self.api._request.assert_not_called()

    def test_parameter_invalid_or_uncalibrated_fields_never_reach_network(self):
        self.api._request=Mock()
        for changes in ({},None,[],{'start':'24:00'},{'start':'1:00'},{'start':'01:00:01'},
                {'presence_delay_seconds':True},{'presence_delay_seconds':3601},
                {'absence_delay_seconds':1201},{'enabled':True}):
            with self.assertRaises(client.CloudError):self.session.configure_human_scene((0,changes))
        for days in ([],['bad'],['mon','mon'],'mon',[True]):
            with self.assertRaises(client.CloudError):self.session.configure_motion_night_light({'repeat_days':days})
        for warm, mode, changes in ((False,4,{'fan_speed':3}),(False,7,{'target_temperature':35}),
                (True,18,{'fan_speed':3}),(True,18,{'target_temperature':29}),
                (False,7,{'duration_minutes':5}),(1,18,{'start':'08:00'})):
            with self.assertRaises(client.CloudError):self.session.configure_schedule((warm,mode,changes))
        self.api._request.assert_not_called()

    def test_distinct_delay_limits_match_ui_without_overwriting_enabled_state(self):
        human = client.patch_human_scene(HUMAN_SCENES_OFF,0,{'presence_delay_seconds':3600,'absence_delay_seconds':1200})
        parsed = client.decode_human_scenes(human)[0]
        self.assertEqual(parsed['presence_delay_seconds'],3600)
        self.assertEqual(parsed['absence_delay_seconds'],1200)
        self.assertFalse(parsed['enabled'])
        night = client.patch_motion_night_light(NIGHT_ON,{'absence_delay_seconds':1200})
        self.assertEqual(client.decode_motion_night_light(night)['absence_delay_seconds'],1200)
        with self.assertRaises(ValueError):client.patch_motion_night_light(NIGHT_ON,{'absence_delay_seconds':1201})

    def test_offline_and_unknown_repeat_block_parameter_writes(self):
        self.session._on_message(None,None,self.settings_message(20,{'6':HUMAN_SCENES_OFF,'14':NIGHT_ON,'11':SCHEDULES_OFF}))
        self.api._request=Mock();self.api.device_online.return_value=False
        for fn,arg in ((self.session.configure_human_scene,(0,{'start':'19:00'})),
                       (self.session.configure_motion_night_light,{'end':'05:00'}),
                       (self.session.configure_schedule,(False,7,{'start':'11:40'}))):
            with self.assertRaises(client.CloudError):fn(arg)
        self.api.device_online.return_value=True
        self.session._on_message(None,None,self.settings_message(21,{'14':NIGHT_ON[:-2]+'80'}))
        with self.assertRaises(client.CloudError):self.session.configure_motion_night_light({'end':'05:00'})
        self.api._request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
