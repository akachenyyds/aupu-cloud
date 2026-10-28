"""Run config/reauth and entity tests in isolated real Home Assistant 2026.9.4."""
import asyncio
from copy import deepcopy
import importlib
from pathlib import Path
import sys
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from scene_fixtures import HUMAN_SCENES_OFF, NIGHT_ON, WARM_OFF, SCHEDULES_OFF

from homeassistant import config_entries, loader
from homeassistant.core import HomeAssistant

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class HACompatibility(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime_root = Path(tempfile.mkdtemp(prefix="ha-fixture-"))
        shutil.copytree(ROOT / 'custom_components', self.runtime_root / 'custom_components',
                        ignore=shutil.ignore_patterns('__pycache__'))
        self.hass = HomeAssistant(str(self.runtime_root))
        loader.async_setup(self.hass)
        self.hass.config_entries = config_entries.ConfigEntries(self.hass, {})
        await self.hass.config_entries.async_initialize()
        self.integration = importlib.import_module('custom_components.aupu_cloud')
        self.flow_module = importlib.import_module('custom_components.aupu_cloud.config_flow')
        self.original_setup_entry = self.integration.async_setup_entry
        self.original_unload_entry = self.integration.async_unload_entry
        self.setup_patch = patch.object(self.integration, 'async_setup_entry', AsyncMock(return_value=True))
        self.unload_patch = patch.object(self.integration, 'async_unload_entry', AsyncMock(return_value=True))
        self.setup_patch.start()
        self.unload_patch.start()

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.setup_patch.stop()
        self.unload_patch.stop()
        shutil.rmtree(self.runtime_root)

    async def test_config_reauth_entities_and_shutdown(self):
        from custom_components.aupu_cloud.client import AuthenticationError, CloudError
        from custom_components.aupu_cloud.coordinator import AupuCoordinator
        from custom_components.aupu_cloud.light import AupuBuiltInLight, AupuExternalLight, ColorMode
        from custom_components.aupu_cloud.climate import AupuAIHeating, HVACMode
        from custom_components.aupu_cloud.sensor import AupuTargetTemperature, AupuOperatingFunction
        from custom_components.aupu_cloud.select import AupuOperatingFunctionSelect, AupuVentilationLevelSelect
        from homeassistant.exceptions import ConfigEntryAuthFailed
        from homeassistant.helpers.update_coordinator import UpdateFailed

        device = {'did': '1001', 'name': 'Test heater', 'product_name': 'Verified product'}

        def discover(token):
            if token == 'invalid-test-token':
                raise AuthenticationError('Rejected')
            if token == 'other-account-test-token':
                return [dict(device, did='9999')]
            return [device]

        with patch.object(self.flow_module, '_discover', discover):
            result = await self.hass.config_entries.flow.async_init('aupu_cloud', context={'source': 'user'})
            self.assertEqual(result['type'], 'form')
            flow_id = result['flow_id']
            result = await self.hass.config_entries.flow.async_configure(flow_id, {'access_token': 'invalid-test-token'})
            self.assertEqual(result['errors']['base'], 'invalid_auth')
            result = await self.hass.config_entries.flow.async_configure(flow_id, {'access_token': 'valid-test-token'})
            self.assertEqual(result['type'], 'create_entry')
            await self.hass.async_block_till_done()
            entry = result['result']
            original_entry_id = entry.entry_id
            self.assertEqual(entry.data['device_id'], '1001')

            duplicate = await self.hass.config_entries.flow.async_init('aupu_cloud', context={'source': 'user'}, data={'access_token': 'valid-test-token'})
            self.assertEqual(duplicate['reason'], 'already_configured')

            reauth = await self.hass.config_entries.flow.async_init('aupu_cloud', context={'source': 'reauth', 'entry_id': entry.entry_id}, data=dict(entry.data))
            self.assertEqual(reauth['step_id'], 'reauth_confirm')
            result = await self.hass.config_entries.flow.async_configure(reauth['flow_id'], {'access_token': 'other-account-test-token'})
            self.assertEqual(result['errors']['base'], 'wrong_account')
            self.assertEqual(entry.data['access_token'], 'valid-test-token')
            result = await self.hass.config_entries.flow.async_configure(reauth['flow_id'], {'access_token': 'replacement-test-token'})
            self.assertEqual(result['reason'], 'reauth_successful')
            await self.hass.async_block_till_done()
            self.assertEqual(entry.entry_id, original_entry_id)
            self.assertEqual(entry.data['access_token'], 'replacement-test-token')
            self.assertEqual(len(self.hass.config_entries.async_entries('aupu_cloud')), 1)

            coordinator = AupuCoordinator(self.hass, entry)
            coordinator.device = device
            coordinator.async_set_light = AsyncMock()
            coordinator.data = {'connected': True, 'device_online': True, 'reported': {'2': {'properties': {'1': True}}, '3': {'properties': {'1': 0, '2': 18, '3': 40}}, '4': {'properties': {'1': 26}}}}
            light = AupuBuiltInLight(coordinator)
            target = AupuTargetTemperature(coordinator)
            function = AupuOperatingFunction(coordinator)
            self.assertTrue(light.is_on)
            self.assertEqual(light.supported_color_modes, {ColorMode.ONOFF})
            self.assertEqual(target.native_value, 40)
            self.assertEqual(function.native_value, 'AI恒温暖')
            await light.async_turn_off()
            await light.async_turn_on()
            self.assertEqual([c.args[0] for c in coordinator.async_set_light.await_args_list], [False, True])
            coordinator.data['reported']['3']['properties']['2'] = 99
            self.assertIsNone(function.native_value)
            coordinator.async_set_mode = AsyncMock()
            coordinator.async_set_ventilation_level = AsyncMock()
            mode_select = AupuOperatingFunctionSelect(coordinator)
            ventilation_select = AupuVentilationLevelSelect(coordinator)
            coordinator.data['reported']['3']['properties']['2'] = 7
            coordinator.data['reported']['6'] = {'properties': {'5': 5}}
            self.assertEqual(mode_select.current_option, '换气')
            self.assertTrue(ventilation_select.available)
            self.assertEqual(ventilation_select.current_option, '5')
            await mode_select.async_select_option('换气')
            coordinator.async_set_mode.assert_awaited_once_with(7)
            await ventilation_select.async_select_option('3')
            coordinator.async_set_ventilation_level.assert_awaited_once_with(3)
            coordinator.data['reported']['3']['properties']['2'] = 18
            self.assertFalse(ventilation_select.available)
            coordinator.async_set_ai_enabled = AsyncMock()
            coordinator.async_set_target_temperature = AsyncMock()
            coordinator.async_set_external_light = AsyncMock()
            heating = AupuAIHeating(coordinator)
            external = AupuExternalLight(coordinator)
            self.assertEqual(heating.hvac_mode, HVACMode.HEAT)
            self.assertEqual((heating.min_temp, heating.max_temp, heating.target_temperature_step), (30, 42, 1))
            self.assertEqual(heating.current_temperature,26)
            await heating.async_set_temperature(temperature=39.0)
            coordinator.async_set_target_temperature.assert_awaited_once_with(39)
            coordinator.async_set_ai_enabled.assert_not_awaited()
            from homeassistant.exceptions import HomeAssistantError
            with self.assertRaises(HomeAssistantError):
                await heating.async_set_temperature(temperature=39.5, hvac_mode=HVACMode.HEAT)
            coordinator.async_set_ai_enabled.assert_not_awaited()
            await heating.async_turn_off()
            coordinator.async_set_ai_enabled.assert_awaited_once_with(False)
            await external.async_turn_on()
            coordinator.async_set_external_light.assert_awaited_once_with(True)
            self.assertFalse(external.entity_registry_enabled_default)
            coordinator.data['connected'] = False
            self.assertFalse(light.available)
            session = Mock()
            coordinator.session = session
            await coordinator.async_shutdown()
            await coordinator.async_shutdown()
            session.close.assert_called_once()

            coordinator = AupuCoordinator(self.hass, entry)
            snapshot = {'connected': True, 'device_online': True, 'reported': {'2': {'properties': {'1': True}}}}
            await self.hass.async_add_executor_job(coordinator._push_from_thread, snapshot)
            await asyncio.sleep(0)
            self.assertEqual(coordinator.data, snapshot)

            coordinator._session_generation = 2
            coordinator._apply_push({'connected': True, 'version': 30}, 2)
            coordinator._apply_push({'connected': True, 'version': 29}, 2)
            coordinator._apply_push({'connected': False, 'version': 99}, 1)
            self.assertEqual(coordinator.data, {'connected': True, 'version': 30})
            # Reset this fixture before exercising reconnect/shutdown below.
            coordinator.data = snapshot

            with patch.object(coordinator, '_read', side_effect=AuthenticationError('Expired')):
                with self.assertRaises(ConfigEntryAuthFailed):
                    await coordinator._async_update_data()
            with patch.object(coordinator, '_read', side_effect=CloudError('Offline')):
                with self.assertRaises(UpdateFailed):
                    await coordinator._async_update_data()

            stale_session = Mock(connected=False)
            new_session = Mock(connected=True)
            new_session.start.return_value = snapshot
            coordinator.api, coordinator.session = Mock(), stale_session
            with patch('custom_components.aupu_cloud.coordinator.ShadowSession', return_value=new_session):
                self.assertEqual(await coordinator._async_update_data(), snapshot)
            stale_session.close.assert_called_once()
            new_session.start.assert_called_once()
            await coordinator.async_shutdown()
            coordinator._apply_push({'connected': False})
            self.assertEqual(coordinator.data, snapshot)


    async def test_real_platform_setup_state_and_unload(self):
        await self._exercise_real_platform_setup(False)

    async def test_real_mqtt_platform_setup_state_and_unload(self):
        await self._exercise_real_platform_setup(True)

    async def _exercise_real_platform_setup(self, mqtt_mode):
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers import device_registry as dr
        if dr.DATA_REGISTRY not in self.hass.data:
            dr.async_setup(self.hass)
        await dr.async_get(self.hass).async_load(load_empty=True)
        await er.async_get(self.hass).async_load(load_empty=True)
        device = {'did': '1001', 'name': 'Test heater', 'product_name': 'Verified product'}
        snapshot = {'connected': True, 'device_online': True, 'version': 20, 'reported_updated_at': 1792512650, 'reported': {
            '2': {'properties': {'1': True}},
            '3': {'properties': {'1': 0, '2': 7, '3': 40}},
            '4': {'properties': {'1': 26}},
            '5': {'properties': {'2': 5}},
            '6': {'properties': {'5': 5, '4': True, '3': False, '6': HUMAN_SCENES_OFF,
                                 '14': NIGHT_ON, '12': False, '23': 0, '9': False, '10': WARM_OFF, '11': SCHEDULES_OFF, '7': 18}}}}
        api = Mock()
        api.discover.return_value = [device]
        session = Mock(connected=True)
        session.start.return_value = snapshot
        session.refresh.return_value = snapshot
        def set_property(function, prop, value):
            snapshot['version'] += 1
            snapshot['reported'][function]['properties'][prop] = value
            return deepcopy(snapshot)
        session.set_mode.side_effect = lambda value: set_property('3', '2', value)
        session.set_ventilation_level.side_effect = lambda value: set_property('6', '5', value)
        session.set_blow_speed.side_effect = lambda value: set_property('5', '2', value)
        session.set_light.side_effect = lambda value: set_property('2', '1', value)
        session.set_night_light.side_effect = lambda value: set_property('6', '4', value)
        session.set_external_light.side_effect = lambda value: set_property('6', '3', value)
        session.set_ai_enabled.side_effect = lambda value: set_property('3', '2', 18 if value else 0)
        session.set_target_temperature.side_effect = lambda value: set_property('3', '3', int(value))
        from custom_components.aupu_cloud.scene_settings import patch_human_scene_enabled, patch_motion_night_light_enabled, patch_schedule_enabled, patch_presence_parameter
        session.set_human_scene_enabled.side_effect = lambda setting: set_property('6', '6',
            patch_human_scene_enabled(snapshot['reported']['6']['properties']['6'], *setting))
        session.set_motion_night_light_enabled.side_effect = lambda enabled: set_property('6', '14',
            patch_motion_night_light_enabled(snapshot['reported']['6']['properties']['14'], enabled))
        session.set_unoccupied_standby.side_effect = lambda enabled: set_property('6', '12', enabled)
        session.set_holiday_mode.side_effect = lambda enabled: set_property('6', '9', enabled)
        session.set_presence_parameter.side_effect = lambda setting: set_property('6','7',patch_presence_parameter(snapshot['reported']['6']['properties']['7'],*setting))
        def set_schedule(setting):
            warm, mode, enabled = setting
            prop = '10' if warm else '11'
            return set_property('6', prop, patch_schedule_enabled(snapshot['reported']['6']['properties'][prop], mode, enabled, warm=warm))
        session.set_schedule_enabled.side_effect = set_schedule
        from custom_components.aupu_cloud.scene_settings import patch_human_scene, patch_motion_night_light, patch_schedule
        session.configure_human_scene.side_effect = lambda setting: set_property('6','6',
            patch_human_scene(snapshot['reported']['6']['properties']['6'], *setting))
        session.configure_motion_night_light.side_effect = lambda changes: set_property('6','14',
            patch_motion_night_light(snapshot['reported']['6']['properties']['14'], changes))
        def configure_schedule(setting):
            warm, mode, changes = setting
            prop = '10' if warm else '11'
            return set_property('6',prop,patch_schedule(snapshot['reported']['6']['properties'][prop],mode,changes,warm=warm))
        session.configure_schedule.side_effect = configure_schedule
        with patch.object(self.flow_module, '_discover', return_value=[device]):
            result = await self.hass.config_entries.flow.async_init('aupu_cloud',
                context={'source': 'user'}, data={'access_token': 'valid-test-token'})
            await self.hass.async_block_till_done()
        entry = result['result']
        self.assertTrue(await self.hass.config_entries.async_unload(entry.entry_id))
        if mqtt_mode:
            self.hass.config_entries.async_update_entry(entry, data={
                'device_id': device['did'], 'device_name': device['name'], 'transport': 'mqtt',
                'local_host': '10.23.45.67', 'expected_mac': '02:00:00:00:00:01',
                'mqtt_credentials': {'x-amz-customauthorizer-name': 'a',
                    'x-amz-customauthorizer-signature': 's', 'tokenKeyName': 't'}})
        with patch('custom_components.aupu_cloud.coordinator.CloudAPI', return_value=api) as account_factory, \
             patch('custom_components.aupu_cloud.mqtt_api.LANProbe', return_value=Mock(check=Mock(return_value=True))), \
             patch('custom_components.aupu_cloud.coordinator.ShadowSession', return_value=session), \
             patch.object(self.integration, 'async_setup_entry', self.original_setup_entry), \
             patch.object(self.integration, 'async_unload_entry', self.original_unload_entry):
            self.assertTrue(await self.hass.config_entries.async_setup(entry.entry_id))
            await self.hass.async_block_till_done()
            if mqtt_mode:
                account_factory.assert_not_called()
                self.assertNotIn('access_token', entry.data)
            registry = er.async_get(self.hass)
            entities = er.async_entries_for_config_entry(registry, entry.entry_id)
            self.assertEqual(len(entities), 25)
            external_entry = next(e for e in entities if e.unique_id == '1001_external_light')
            self.assertEqual(external_entry.disabled_by, er.RegistryEntryDisabler.INTEGRATION)
            actual = {e.unique_id: self.hass.states.get(e.entity_id) for e in entities}
            self.assertEqual(actual['1001_built_in_light'].state, 'on')
            self.assertEqual(actual['1001_night_light'].state, 'on')
            self.assertIsNone(actual['1001_external_light'])
            self.assertEqual(actual['1001_ai_heating'].state, 'off')
            self.assertEqual(actual['1001_device_online'].state, 'on')
            self.assertEqual(actual['1001_ai_heating'].attributes.get('current_temperature'),26)
            self.assertEqual(actual['1001_device_temperature'].state,'26')
            self.assertEqual(actual['1001_last_reported'].state, '2026-10-20T16:10:50+00:00')
            self.assertEqual(actual['1001_target_temperature'].state, '40')
            self.assertEqual(actual['1001_operating_function_control'].state, '换气')
            self.assertEqual(actual['1001_ventilation_level'].state, '5')
            self.assertEqual(actual['1001_human_detection'].state, 'off')
            self.assertEqual(actual['1001_motion_night_light'].state, 'on')
            self.assertEqual(actual['1001_unoccupied_standby'].state, 'off')
            self.assertEqual(actual['1001_human_scene_0'].attributes['生效开始'], '09:10')
            entity_ids = {e.unique_id: e.entity_id for e in entities}
            for mode in (0, 2, 7, 21):
                entity_id = entity_ids[f'1001_human_scene_{mode}']
                await self.hass.services.async_call('switch', 'turn_on', {'entity_id': entity_id}, blocking=True)
                self.assertEqual(self.hass.states.get(entity_id).state, 'on')
                session.set_human_scene_enabled.assert_called_with((mode, True))
                await self.hass.services.async_call('switch', 'turn_off', {'entity_id': entity_id}, blocking=True)
                self.assertEqual(self.hass.states.get(entity_id).state, 'off')
            self.assertEqual(snapshot['reported']['6']['properties']['6'], HUMAN_SCENES_OFF)
            for suffix in ('motion_night_light', 'unoccupied_standby'):
                entity_id = entity_ids[f'1001_{suffix}']
                await self.hass.services.async_call('switch', 'turn_off', {'entity_id': entity_id}, blocking=True)
                self.assertEqual(self.hass.states.get(entity_id).state, 'off')
                await self.hass.services.async_call('switch', 'turn_on', {'entity_id': entity_id}, blocking=True)
                self.assertEqual(self.hass.states.get(entity_id).state, 'on')
            self.assertEqual(snapshot['reported']['6']['properties']['14'], NIGHT_ON)
            self.assertTrue(snapshot['reported']['6']['properties']['4'])
            original_mode = snapshot['reported']['3']['properties']['2']
            for suffix in ('holiday_mode', 'warm_schedule', 'schedule_7', 'schedule_4', 'schedule_21'):
                entity_id = entity_ids[f'1001_{suffix}']
                self.assertEqual(self.hass.states.get(entity_id).state, 'off')
                await self.hass.services.async_call('switch', 'turn_on', {'entity_id': entity_id}, blocking=True)
                self.assertEqual(self.hass.states.get(entity_id).state, 'on')
                await self.hass.services.async_call('switch', 'turn_off', {'entity_id': entity_id}, blocking=True)
                self.assertEqual(self.hass.states.get(entity_id).state, 'off')
            self.assertEqual(snapshot['reported']['3']['properties']['2'], original_mode)
            self.assertEqual(snapshot['reported']['6']['properties']['10'], WARM_OFF)
            self.assertEqual(snapshot['reported']['6']['properties']['11'], SCHEDULES_OFF)
            self.assertEqual(self.hass.states.get(entity_ids['1001_warm_schedule']).attributes['开始时间'], '11:40')
            human_id = entity_ids['1001_human_scene_0']
            await self.hass.services.async_call('aupu_cloud','configure_human_scene',{
                'entity_id':human_id,'start':'19:01:00','end':'20:58:00',
                'presence_delay_seconds':61,'absence_delay_seconds':31},blocking=True)
            self.assertEqual(self.hass.states.get(human_id).state,'off')
            self.assertEqual(self.hass.states.get(human_id).attributes['人在后开启_秒'],61)
            self.assertEqual(self.hass.states.get(human_id).attributes['生效开始'],'19:01')
            await self.hass.services.async_call('aupu_cloud','configure_human_scene',{
                'entity_id':human_id,'start':'09:10','end':'16:40',
                'presence_delay_seconds':0,'absence_delay_seconds':30},blocking=True)
            self.assertEqual(snapshot['reported']['6']['properties']['6'],HUMAN_SCENES_OFF)
            night_id=entity_ids['1001_motion_night_light']
            await self.hass.services.async_call('aupu_cloud','configure_motion_night_light',{
                'entity_id':night_id,'start':'23:01','end':'05:01',
                'repeat_days':['mon','sun'],'absence_delay_seconds':31},blocking=True)
            self.assertEqual(self.hass.states.get(night_id).attributes['重复日期'],['mon','sun'])
            self.assertTrue(snapshot['reported']['6']['properties']['4'])
            await self.hass.services.async_call('aupu_cloud','configure_motion_night_light',{
                'entity_id':night_id,'start':'20:10','end':'08:20','absence_delay_seconds':30,
                'repeat_days':['mon','tue','wed','thu','fri','sat','sun']},blocking=True)
            self.assertEqual(snapshot['reported']['6']['properties']['14'],NIGHT_ON)
            for suffix, changes, restore in (
                ('schedule_7',{'start':'06:01','fan_speed':4,'repeat_days':['sun'],'duration_minutes':20},
                              {'start':'08:20','fan_speed':5,'repeat_days':[],'duration_minutes':30}),
                ('warm_schedule',{'start':'08:01','target_temperature':34,'repeat_days':['sun'],'duration_minutes':20},
                                 {'start':'11:40','target_temperature':35,'repeat_days':[],'duration_minutes':30})):
                entity_id=entity_ids['1001_'+suffix]
                await self.hass.services.async_call('aupu_cloud','configure_schedule',
                    {'entity_id':entity_id,**changes},blocking=True)
                self.assertEqual(self.hass.states.get(entity_id).state,'off')
                self.assertEqual(self.hass.states.get(entity_id).attributes['开始时间'],changes['start'])
                self.assertEqual(self.hass.states.get(entity_id).attributes['预约时长_分钟'],20)
                await self.hass.services.async_call('aupu_cloud','configure_schedule',
                    {'entity_id':entity_id,**restore},blocking=True)
                self.assertEqual(self.hass.states.get(entity_id).attributes['预约时长_分钟'],30)
            self.assertEqual(snapshot['reported']['6']['properties']['10'],WARM_OFF)
            self.assertEqual(snapshot['reported']['6']['properties']['11'],SCHEDULES_OFF)
            from homeassistant.helpers.service import _load_services_file
            integration = await loader.async_get_integration(self.hass,'aupu_cloud')
            # Validate this integration's real UI schema; unrelated optional HA
            # platforms are intentionally not installed in this isolated runtime.
            descriptions = await self.hass.async_add_executor_job(_load_services_file,integration)
            self.assertIn('presence_delay_seconds',descriptions['configure_human_scene']['fields'])
            self.assertIn('repeat_days',descriptions['configure_motion_night_light']['fields'])
            self.assertIn('target_temperature',descriptions['configure_schedule']['fields'])
            self.assertIn('duration_minutes',descriptions['configure_schedule']['fields'])
            from homeassistant.exceptions import HomeAssistantError
            import voluptuous as vol
            before=session.configure_human_scene.call_count
            for data in ({'start':'19:00'}, {'entity_id':'all','start':'19:00'},
                         {'entity_id':[human_id,entity_ids['1001_human_scene_7']],'start':'19:00'},
                         {'entity_id':entity_ids['1001_holiday_mode'],'start':'19:00'},
                         {'entity_id':human_id,'device_id':'unknown','start':'19:00'}):
                with self.assertRaises((HomeAssistantError,vol.Invalid)):
                    await self.hass.services.async_call('aupu_cloud','configure_human_scene',data,blocking=True)
            self.assertEqual(session.configure_human_scene.call_count,before)
            self.assertEqual(snapshot['reported']['3']['properties']['2'],original_mode)
            for suffix, option, expected in (('presence_sensitivity','低',50),('presence_distance_meters','4 米',52),('presence_sensitivity','高',20),('presence_distance_meters','2 米',18)):
                entity_id=entity_ids[f'1001_{suffix}']
                await self.hass.services.async_call('select','select_option',{'entity_id':entity_id,'option':option},blocking=True)
                self.assertEqual(self.hass.states.get(entity_id).state,option)
                self.assertEqual(snapshot['reported']['6']['properties']['7'],expected)
            await self.hass.services.async_call('select', 'select_option', {
                'entity_id': entity_ids['1001_ventilation_level'], 'option': '3'}, blocking=True)
            self.assertEqual(self.hass.states.get(entity_ids['1001_ventilation_level']).state, '3')
            session.set_ventilation_level.assert_called_once_with(3)
            await self.hass.services.async_call('select', 'select_option', {
                'entity_id': entity_ids['1001_operating_function_control'], 'option': '吹风'}, blocking=True)
            self.assertEqual(self.hass.states.get(entity_ids['1001_ventilation_level']).state, 'unavailable')
            await self.hass.services.async_call('select', 'select_option', {
                'entity_id': entity_ids['1001_blow_speed'], 'option': '3'}, blocking=True)
            session.set_blow_speed.assert_called_once_with(3)
            self.assertEqual(self.hass.states.get(entity_ids['1001_blow_speed']).state, '3')
            await self.hass.services.async_call('select', 'select_option', {
                'entity_id': entity_ids['1001_operating_function_control'], 'option': '关闭'}, blocking=True)
            self.assertEqual(self.hass.states.get(entity_ids['1001_operating_function_control']).state, '关闭')
            self.assertEqual(self.hass.states.get(entity_ids['1001_ventilation_level']).state, 'unavailable')
            for mode in ('除臭除菌', '普通干燥', '恒温干燥'):
                await self.hass.services.async_call('select', 'select_option', {
                    'entity_id': entity_ids['1001_operating_function_control'], 'option': mode}, blocking=True)
                self.assertEqual(self.hass.states.get(entity_ids['1001_operating_function_control']).state, mode)
            await self.hass.services.async_call('climate', 'set_hvac_mode', {
                'entity_id': entity_ids['1001_ai_heating'], 'hvac_mode': 'heat'}, blocking=True)
            self.assertEqual(self.hass.states.get(entity_ids['1001_ai_heating']).state, 'heat')
            self.assertEqual(self.hass.states.get(entity_ids['1001_operating_function_control']).state, 'AI恒温暖')
            await self.hass.services.async_call('climate', 'set_temperature', {
                'entity_id': entity_ids['1001_ai_heating'], 'temperature': 39}, blocking=True)
            session.set_target_temperature.assert_called_once_with(39)
            self.assertEqual(self.hass.states.get(entity_ids['1001_ai_heating']).attributes['temperature'], 39)
            self.assertEqual(self.hass.states.get(entity_ids['1001_target_temperature']).state, '39')
            await self.hass.services.async_call('climate', 'turn_off', {
                'entity_id': entity_ids['1001_ai_heating']}, blocking=True)
            self.assertEqual(self.hass.states.get(entity_ids['1001_ai_heating']).state, 'off')
            self.assertEqual(self.hass.states.get(entity_ids['1001_operating_function_control']).state, '关闭')
            await self.hass.services.async_call('light', 'turn_off', {
                'entity_id': entity_ids['1001_built_in_light']}, blocking=True)
            self.assertEqual(self.hass.states.get(entity_ids['1001_built_in_light']).state, 'off')
            session.set_light.assert_called_once_with(False)
            await self.hass.services.async_call('light', 'turn_off', {
                'entity_id': entity_ids['1001_night_light']}, blocking=True)
            self.assertEqual(self.hass.states.get(entity_ids['1001_night_light']).state, 'off')
            session.set_night_light.assert_called_once_with(False)
            coordinator = entry.runtime_data
            detected = deepcopy(coordinator.data)
            detected['version'] += 1
            detected['reported']['6']['properties']['23'] = 1
            coordinator._apply_push(detected)
            await self.hass.async_block_till_done()
            self.assertEqual(self.hass.states.get(entity_ids['1001_human_detection']).state, 'on')
            detected['version'] += 1
            detected['reported']['6']['properties']['23'] = 0
            coordinator._apply_push(deepcopy(detected))
            await self.hass.async_block_till_done()
            self.assertEqual(self.hass.states.get(entity_ids['1001_human_detection']).state, 'off')
            measured = deepcopy(coordinator.data)
            measured['version'] += 1
            measured['reported']['4']['properties']['1'] = 25
            coordinator._apply_push(measured)
            await self.hass.async_block_till_done()
            self.assertEqual(self.hass.states.get(entity_ids['1001_device_temperature']).state,'25')
            self.assertEqual(self.hass.states.get(entity_ids['1001_ai_heating']).attributes['current_temperature'],25)
            faulty = deepcopy(measured)
            faulty['version'] += 1
            faulty['reported']['3']['properties']['1'] = 1
            coordinator._apply_push(faulty)
            await self.hass.async_block_till_done()
            self.assertEqual(self.hass.states.get(entity_ids['1001_device_temperature']).state,'unavailable')
            self.assertIsNone(self.hass.states.get(entity_ids['1001_ai_heating']).attributes['current_temperature'])
            recovered = deepcopy(faulty)
            recovered['version'] += 1
            recovered['reported']['3']['properties']['1'] = 0
            recovered['reported']['4']['properties']['1'] = 26
            coordinator._apply_push(recovered)
            await self.hass.async_block_till_done()
            self.assertEqual(self.hass.states.get(entity_ids['1001_device_temperature']).state,'26')
            session.set_target_temperature.assert_called_once_with(39)
            unknown = deepcopy(detected)
            unknown['version'] = recovered['version']
            unknown['version'] += 1
            unknown['reported']['6']['properties']['23'] = 2
            unknown['reported']['6']['properties']['7'] = 33
            coordinator._apply_push(unknown)
            await self.hass.async_block_till_done()
            self.assertEqual(self.hass.states.get(entity_ids['1001_human_detection']).state, 'unavailable')
            for suffix in ('presence_sensitivity','presence_distance_meters'):
                self.assertEqual(self.hass.states.get(entity_ids[f'1001_{suffix}']).state,'unavailable')
            offline = deepcopy(coordinator.data)
            offline['device_online'] = False
            coordinator._apply_push(offline)
            await self.hass.async_block_till_done()
            self.assertEqual(self.hass.states.get(entity_ids['1001_device_online']).state, 'off')
            self.assertEqual(self.hass.states.get(entity_ids['1001_ai_heating']).state, 'unavailable')
            self.assertEqual(self.hass.states.get(entity_ids['1001_device_temperature']).state, 'unavailable')
            self.assertEqual(self.hass.states.get(entity_ids['1001_built_in_light']).state, 'unavailable')
            self.assertEqual(self.hass.states.get(entity_ids['1001_motion_night_light']).state, 'unavailable')
            self.assertEqual(self.hass.states.get(entity_ids['1001_human_scene_0']).state, 'unavailable')
            self.assertEqual(self.hass.states.get(entity_ids['1001_human_detection']).state, 'unavailable')
            for suffix in ('holiday_mode', 'warm_schedule', 'schedule_7', 'schedule_4', 'schedule_21'):
                self.assertEqual(self.hass.states.get(entity_ids[f'1001_{suffix}']).state, 'unavailable')
            self.assertEqual(self.hass.states.get(entity_ids['1001_last_reported']).state, '2026-10-20T16:10:50+00:00')
            online = deepcopy(offline)
            online['device_online'] = True
            online['version'] += 1
            online['reported']['3']['properties']['3'] = 35
            online['reported']['6']['properties']['4'] = False
            coordinator._apply_push(online)
            await self.hass.async_block_till_done()
            self.assertEqual(self.hass.states.get(entity_ids['1001_device_online']).state, 'on')
            self.assertEqual(self.hass.states.get(entity_ids['1001_ai_heating']).state, 'off')
            self.assertEqual(self.hass.states.get(entity_ids['1001_ai_heating']).attributes['temperature'], 35)
            self.assertEqual(self.hass.states.get(entity_ids['1001_night_light']).state, 'off')
            session.set_target_temperature.assert_called_once_with(39)
            self.assertTrue(await self.hass.config_entries.async_unload(entry.entry_id))
            session.close.assert_called_once()

    async def test_two_identical_models_have_isolated_names_states_commands_and_unload(self):
        from homeassistant.helpers import entity_registry as er, device_registry as dr
        if dr.DATA_REGISTRY not in self.hass.data:
            dr.async_setup(self.hass)
        await dr.async_get(self.hass).async_load(load_empty=True)
        await er.async_get(self.hass).async_load(load_empty=True)
        devices = [{'did': did, 'name': 'Same model', 'product_name': 'T5-Pro'} for did in ('1001', '1002')]
        entries = []
        with patch.object(self.flow_module, '_discover', return_value=devices):
            for did, name in (('1001', 'Test heater A'), ('1002', 'Test heater B')):
                flow = await self.hass.config_entries.flow.async_init('aupu_cloud', context={'source': 'user'},
                    data={'access_token': 'valid-test-token', 'device_name': name})
                self.assertEqual(flow['step_id'], 'device')
                result = await self.hass.config_entries.flow.async_configure(flow['flow_id'], {'device_id': did})
                entries.append(result['result'])
                self.assertEqual(result['result'].unique_id, did)
                self.assertEqual(result['result'].title, name)
                await self.hass.async_block_till_done()
            duplicate = await self.hass.config_entries.flow.async_init('aupu_cloud', context={'source': 'user'},
                data={'access_token': 'valid-test-token'})
            duplicate = await self.hass.config_entries.flow.async_configure(duplicate['flow_id'], {'device_id': '1001'})
            self.assertEqual(duplicate['reason'], 'already_configured')
        for entry in entries:
            self.assertTrue(await self.hass.config_entries.async_unload(entry.entry_id))
        sessions = {}
        for did in ('1001', '1002'):
            snapshot = {'connected': True, 'device_online': True, 'version': 20, 'reported_updated_at': 1792512650,
                'reported': {'2': {'properties': {'1': False}}, '3': {'properties': {'1': 0, '2': 0, '3': 35}},
                             '4': {'properties': {'1': 26 if did=='1001' else 25}},
                             '5': {'properties': {'2': 5}}, '6': {'properties': {'3': False, '4': False, '5': 5,
                                 '6': HUMAN_SCENES_OFF, '14': NIGHT_ON, '12': False, '23': 0, '9': False, '10': WARM_OFF, '11': SCHEDULES_OFF, '7': 18}}}}
            session = Mock(connected=True)
            session.start.return_value = snapshot
            session.refresh.return_value = snapshot
            def set_light(value, state=snapshot):
                state['version'] += 1
                state['reported']['2']['properties']['1'] = value
                return deepcopy(state)
            session.set_light.side_effect = set_light
            from custom_components.aupu_cloud.scene_settings import patch_motion_night_light
            def configure_night(changes,state=snapshot):
                state['version']+=1
                state['reported']['6']['properties']['14']=patch_motion_night_light(state['reported']['6']['properties']['14'],changes)
                return deepcopy(state)
            session.configure_motion_night_light.side_effect=configure_night
            sessions[did] = session
        api = Mock()
        api.discover.return_value = devices
        with patch('custom_components.aupu_cloud.coordinator.CloudAPI', return_value=api), \
             patch('custom_components.aupu_cloud.coordinator.ShadowSession', side_effect=lambda _api, did, listener: sessions[did]), \
             patch.object(self.integration, 'async_setup_entry', self.original_setup_entry), \
             patch.object(self.integration, 'async_unload_entry', self.original_unload_entry):
            for entry in entries:
                self.assertTrue(await self.hass.config_entries.async_setup(entry.entry_id))
            await self.hass.async_block_till_done()
            registry = er.async_get(self.hass)
            grouped = [er.async_entries_for_config_entry(registry, entry.entry_id) for entry in entries]
            self.assertEqual([len(group) for group in grouped], [25, 25])
            self.assertEqual(len({item.unique_id for group in grouped for item in group}), 50)
            self.assertEqual(entries[0].runtime_data.device['name'], 'Test heater A')
            self.assertEqual(entries[1].runtime_data.device['name'], 'Test heater B')
            for group,expected in zip(grouped,(26,25)):
                temperature = next(item.entity_id for item in group if item.unique_id.endswith('_device_temperature'))
                self.assertEqual(self.hass.states.get(temperature).state,str(expected))
            first = next(item.entity_id for item in grouped[0] if item.unique_id == '1001_built_in_light')
            second = next(item.entity_id for item in grouped[1] if item.unique_id == '1002_built_in_light')
            await self.hass.services.async_call('light', 'turn_on', {'entity_id': second}, blocking=True)
            sessions['1001'].set_light.assert_not_called()
            sessions['1002'].set_light.assert_called_once_with(True)
            self.assertEqual(self.hass.states.get(first).state, 'off')
            self.assertEqual(self.hass.states.get(second).state, 'on')
            night_first=next(item.entity_id for item in grouped[0] if item.unique_id=='1001_motion_night_light')
            night_second=next(item.entity_id for item in grouped[1] if item.unique_id=='1002_motion_night_light')
            await self.hass.services.async_call('aupu_cloud','configure_motion_night_light',{
                'entity_id':night_second,'end':'05:01'},blocking=True)
            sessions['1001'].configure_motion_night_light.assert_not_called()
            sessions['1002'].configure_motion_night_light.assert_called_once_with({'end':'05:01'})
            self.assertEqual(self.hass.states.get(night_first).attributes['生效结束'],'08:20')
            self.assertEqual(self.hass.states.get(night_second).attributes['生效结束'],'05:01')
            self.assertTrue(await self.hass.config_entries.async_unload(entries[0].entry_id))
            sessions['1001'].close.assert_called_once()
            sessions['1002'].close.assert_not_called()
            self.assertEqual(self.hass.states.get(second).state, 'on')
            self.assertTrue(await self.hass.config_entries.async_unload(entries[1].entry_id))
            sessions['1002'].close.assert_called_once()



    async def test_mqtt_config_reauth_and_rest_switch_preserve_entry_without_jwt(self):
        from custom_components.aupu_cloud.client import CloudError
        device = {'did': '3001', 'name': 'MQTT heater', 'product_name': 'Verified model'}
        auth = {'x-amz-customauthorizer-name': 'test-authorizer',
                'x-amz-customauthorizer-signature': 'test-signature', 'tokenKeyName': 'test-value'}
        prepare = Mock(return_value=auth)
        with patch.object(self.flow_module, '_discover', return_value=[device]), \
             patch.object(self.flow_module, '_prepare_mqtt', prepare):
            result = await self.hass.config_entries.flow.async_init('aupu_cloud', context={'source': 'user'},
                data={'access_token': 'setup-test-token', 'transport': 'mqtt', 'device_name': 'Test heater configuration'})
            self.assertEqual(result['step_id'], 'mqtt')
            flow_id = result['flow_id']
            invalid = await self.hass.config_entries.flow.async_configure(flow_id,
                {'local_host': '8.8.8.8', 'expected_mac': '02:00:00:00:00:01'})
            self.assertEqual(invalid['errors']['base'], 'invalid_local_identity')
            prepare.assert_not_called()
            prepare.side_effect = CloudError('unavailable')
            invalid = await self.hass.config_entries.flow.async_configure(flow_id,
                {'local_host': '10.23.45.67', 'expected_mac': '02:00:00:00:00:01'})
            self.assertEqual(invalid['errors']['base'], 'cannot_verify_mqtt')
            prepare.side_effect = None
            result = await self.hass.config_entries.flow.async_configure(flow_id,
                {'local_host': '10.23.45.67', 'expected_mac': '02:00:00:00:00:01'})
            self.assertEqual(result['type'], 'create_entry')
            await self.hass.async_block_till_done()
            entry = result['result']
            entry_id = entry.entry_id
            self.assertEqual(entry.data['transport'], 'mqtt')
            self.assertNotIn('access_token', entry.data)
            self.assertEqual(entry.data['expected_mac'], '02:00:00:00:00:01')
            self.assertEqual(entry.data['mqtt_credentials'], auth)

            reauth = await self.hass.config_entries.flow.async_init('aupu_cloud',
                context={'source': 'reauth', 'entry_id': entry_id}, data=dict(entry.data))
            result = await self.hass.config_entries.flow.async_configure(reauth['flow_id'],
                {'access_token': 'replacement-test-token'})
            self.assertEqual(result['step_id'], 'mqtt')
            result = await self.hass.config_entries.flow.async_configure(result['flow_id'],
                {'local_host': '10.23.45.67', 'expected_mac': '02:00:00:00:00:01'})
            self.assertEqual(result['reason'], 'reauth_successful')
            await self.hass.async_block_till_done()
            self.assertEqual(entry.entry_id, entry_id)
            self.assertNotIn('access_token', entry.data)

            switch = await self.hass.config_entries.flow.async_init('aupu_cloud',
                context={'source': 'reconfigure', 'entry_id': entry_id})
            result = await self.hass.config_entries.flow.async_configure(switch['flow_id'],
                {'access_token': 'rest-test-token', 'transport': 'rest'})
            self.assertEqual(result['reason'], 'reconfigure_successful')
            await self.hass.async_block_till_done()
            self.assertEqual(entry.entry_id, entry_id)
            self.assertEqual(entry.data['access_token'], 'rest-test-token')
            self.assertNotIn('mqtt_credentials', entry.data)

            switch = await self.hass.config_entries.flow.async_init('aupu_cloud',
                context={'source': 'reconfigure', 'entry_id': entry_id})
            result = await self.hass.config_entries.flow.async_configure(switch['flow_id'],
                {'access_token': 'mqtt-setup-token', 'transport': 'mqtt'})
            self.assertEqual(result['step_id'], 'mqtt')
            result = await self.hass.config_entries.flow.async_configure(result['flow_id'],
                {'local_host': '10.23.45.67', 'expected_mac': '02:00:00:00:00:01'})
            self.assertEqual(result['reason'], 'reconfigure_successful')
            await self.hass.async_block_till_done()
            self.assertEqual(entry.entry_id, entry_id)
            self.assertNotIn('access_token', entry.data)
            self.assertEqual(entry.unique_id, '3001')

    async def test_mqtt_runtime_constructs_no_account_client_and_reconnects(self):
        from custom_components.aupu_cloud.coordinator import AupuCoordinator
        from custom_components.aupu_cloud import mqtt_api
        device = {'did': '3002', 'name': 'MQTT runtime', 'product_name': 'Verified model'}
        auth = {'x-amz-customauthorizer-name': 'a', 'x-amz-customauthorizer-signature': 's', 'tokenKeyName': 't'}
        with patch.object(self.flow_module, '_discover', return_value=[device]), \
             patch.object(self.flow_module, '_prepare_mqtt', return_value=auth):
            result = await self.hass.config_entries.flow.async_init('aupu_cloud', context={'source': 'user'},
                data={'access_token': 'setup-only-test-token', 'transport': 'mqtt'})
            result = await self.hass.config_entries.flow.async_configure(result['flow_id'],
                {'local_host': '10.23.45.67', 'expected_mac': '02:00:00:00:00:01'})
            await self.hass.async_block_till_done()
            entry = result['result']
        snapshot = {'connected': True, 'device_online': True, 'version': 1,
                    'reported': {'2': {'properties': {'1': False}}}}
        session = Mock(connected=True)
        session.start.return_value = snapshot
        session.refresh.return_value = snapshot
        session.set_light.return_value = snapshot
        coordinator = AupuCoordinator(self.hass, entry)
        with patch('custom_components.aupu_cloud.coordinator.CloudAPI', side_effect=AssertionError('Account HTTP forbidden')), \
             patch.object(mqtt_api, 'LANProbe', return_value=Mock(check=Mock(return_value=True))), \
             patch('custom_components.aupu_cloud.coordinator.ShadowSession', return_value=session) as factory:
            self.assertEqual(await coordinator._async_update_data(), snapshot)
            self.assertFalse(hasattr(coordinator.api, '_token'))
            self.assertEqual(coordinator.api.state_credentials(), auth)
            await coordinator.async_set_light(True)
            session.set_light.assert_called_once_with(True)
            session.connected = False
            await coordinator._async_update_data()
            self.assertEqual(factory.call_count, 2)
            from custom_components.aupu_cloud.client import StateChannelDisconnected, AuthenticationError, CloudError
            from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
            from homeassistant.helpers.update_coordinator import UpdateFailed
            session.connected = True
            session.refresh.side_effect = StateChannelDisconnected('lost during GET')
            replacement = Mock(connected=True)
            replacement.start.return_value = snapshot
            replacement.refresh.return_value = snapshot
            factory.return_value = replacement
            self.assertEqual(await coordinator._async_update_data(),snapshot)
            self.assertEqual(factory.call_count,3)
            replacement.set_light.assert_not_called()
            replacement.refresh.side_effect = StateChannelDisconnected('second read disconnected')
            failed = Mock(connected=False)
            failed.start.side_effect = StateChannelDisconnected('reconnect failed')
            factory.return_value = failed
            with self.assertRaises(UpdateFailed):
                await coordinator._async_update_data()
            self.assertEqual(factory.call_count,4)
            # Neither authorization failures nor generic query timeouts are
            # classified as a disconnect that can repeat a read.
            coordinator.session = replacement
            replacement.refresh.side_effect = AuthenticationError('rejected')
            with self.assertRaises(ConfigEntryAuthFailed):
                await coordinator._async_update_data()
            replacement.refresh.side_effect = CloudError('Timed out')
            with self.assertRaises(UpdateFailed):
                await coordinator._async_update_data()
            self.assertEqual(factory.call_count,4)
            replacement.set_light.side_effect = StateChannelDisconnected('uncertain control')
            with self.assertRaises(HomeAssistantError):
                await coordinator.async_set_light(True)
            replacement.set_light.assert_called_once_with(True)
            self.assertEqual(factory.call_count,4)
            # Only loss of an established, current-generation session asks
            # for one background read. Initial failures cannot spin a loop.
            coordinator.data = snapshot
            coordinator._data_generation = coordinator._session_generation
            disconnected = deepcopy(snapshot)
            disconnected['connected'] = False
            with patch.object(coordinator,'async_request_refresh',new_callable=AsyncMock) as request:
                coordinator._apply_push(disconnected,coordinator._session_generation)
                await self.hass.async_block_till_done()
                request.assert_awaited_once()
                coordinator._apply_push(disconnected,coordinator._session_generation)
                await self.hass.async_block_till_done()
                request.assert_awaited_once()
                coordinator.data = snapshot
                coordinator._data_generation = coordinator._session_generation-1
                coordinator._apply_push(disconnected,coordinator._session_generation)
                await self.hass.async_block_till_done()
                request.assert_awaited_once()
                coordinator.data = snapshot
                coordinator._data_generation = coordinator._session_generation
                coordinator._apply_push(disconnected,coordinator._session_generation-1)
                await self.hass.async_block_till_done()
                self.assertIs(coordinator.data,snapshot)
                request.assert_awaited_once()
            await coordinator.async_shutdown()
            self.assertGreaterEqual(session.close.call_count, 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
