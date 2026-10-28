"""Real HA config flows with synthetic phones and mocked SMS/cloud clients."""
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

import test_ha


class SMSFlows(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_ha.HACompatibility.asyncSetUp
    asyncTearDown = test_ha.HACompatibility.asyncTearDown

    async def begin(self, phone='13800000000', **kwargs):
        menu = await self.hass.config_entries.flow.async_init('aupu_cloud', context={'source': 'user'})
        self.assertEqual(menu['type'], 'menu')
        form = await self.hass.config_entries.flow.async_configure(menu['flow_id'], {'next_step_id': 'sms'})
        self.assertEqual(form['step_id'], 'sms')
        if phone is None:
            return form
        return await self.hass.config_entries.flow.async_configure(form['flow_id'], {'phone': phone, **kwargs})

    async def test_opening_sms_form_does_not_send(self):
        with patch.object(self.flow_module, 'SMSAuthClient') as auth:
            await self.begin(phone=None)
            auth.assert_not_called()

    async def test_sms_to_mqtt_persists_no_phone_code_or_account_token(self):
        auth = Mock(); auth.login.return_value = 'synthetic-account-token'
        device = {'did': '1001', 'name': 'Test heater', 'product_name': 'Synthetic product'}
        mqtt = {'x-amz-customauthorizer-name': 'test-authorizer',
                'x-amz-customauthorizer-signature': 'test-signature', 'tokenKeyName': 'test-token'}
        with patch.object(self.flow_module, 'SMSAuthClient', return_value=auth), \
             patch.object(self.flow_module, '_discover', return_value=[device]), \
             patch.object(self.flow_module, '_prepare_mqtt', return_value=mqtt):
            result = await self.begin()
            self.assertEqual(result['step_id'], 'sms_code')
            auth.request_code.assert_called_once_with('13800000000')
            result = await self.hass.config_entries.flow.async_configure(result['flow_id'],
                {'verification_code': '123456'})
            self.assertEqual(result['step_id'], 'mqtt')
            result = await self.hass.config_entries.flow.async_configure(result['flow_id'],
                {'local_host': '10.23.45.67', 'expected_mac': '02:00:00:00:00:01'})
            self.assertEqual(result['type'], 'create_entry')
            await self.hass.async_block_till_done()
            entry = result['result']
            self.assertEqual(entry.data['mqtt_credentials'], mqtt)
            self.assertNotIn('access_token', entry.data)
            text = repr(entry.data)
            self.assertNotIn('13800000000', text)
            self.assertNotIn('123456', text)
            self.assertNotIn('synthetic-account-token', text)

    async def test_parallel_flows_share_sms_cooldown(self):
        with patch.object(self.flow_module, 'SMSAuthClient') as auth:
            first = await self.begin()
            self.assertEqual(first['step_id'], 'sms_code')
            second = await self.begin()
            self.assertEqual(second['errors']['base'], 'sms_cooldown')
            self.assertEqual(auth.return_value.request_code.call_count, 1)

    async def test_uncertain_send_allows_code_but_does_not_resend(self):
        from custom_components.aupu_cloud.auth import SMSDeliveryUncertain
        auth = Mock(); auth.request_code.side_effect = SMSDeliveryUncertain('safe failure')
        with patch.object(self.flow_module, 'SMSAuthClient', return_value=auth):
            result = await self.begin()
            self.assertEqual(result['step_id'], 'sms_code')
            self.assertEqual(result['errors']['base'], 'sms_delivery_uncertain')
            auth.request_code.assert_called_once()

    async def test_bad_code_format_and_attempt_limit(self):
        from custom_components.aupu_cloud.client import AuthenticationError
        auth = Mock(); auth.login.side_effect = AuthenticationError('safe rejection')
        with patch.object(self.flow_module, 'SMSAuthClient', return_value=auth):
            result = await self.begin()
            result = await self.hass.config_entries.flow.async_configure(result['flow_id'],
                {'verification_code': 'bad'})
            self.assertEqual(result['errors']['base'], 'invalid_sms_code')
            auth.login.assert_not_called()
            for _ in range(5):
                result = await self.hass.config_entries.flow.async_configure(result['flow_id'],
                    {'verification_code': '123456'})
                self.assertEqual(result['errors']['base'], 'sms_login_rejected')
            result = await self.hass.config_entries.flow.async_configure(result['flow_id'],
                {'verification_code': '123456'})
            self.assertEqual(result['reason'], 'sms_attempts_exhausted')
            self.assertEqual(auth.login.call_count, 5)

    async def test_expired_local_flow_does_not_submit_code(self):
        with patch.object(self.flow_module, 'SMSAuthClient') as auth:
            result = await self.begin()
            with patch.object(self.flow_module, 'monotonic', return_value=10**12):
                result = await self.hass.config_entries.flow.async_configure(result['flow_id'],
                    {'verification_code': '123456'})
            self.assertEqual(result['reason'], 'sms_flow_expired')
            auth.return_value.login.assert_not_called()

    async def test_reauth_wrong_account_preserves_existing_entry(self):
        device = {'did': '1001', 'name': 'Test heater', 'product_name': 'Test model'}
        auth = Mock(); auth.login.return_value = 'new-synthetic-token'
        with patch.object(self.flow_module, '_discover', return_value=[device]):
            result = await self.hass.config_entries.flow.async_init('aupu_cloud',
                context={'source': 'user'}, data={'access_token': 'old-synthetic-token'})
            await self.hass.async_block_till_done()
        entry = result['result']; previous = deepcopy(dict(entry.data))
        with patch.object(self.flow_module, 'SMSAuthClient', return_value=auth), \
             patch.object(self.flow_module, '_discover', return_value=[dict(device, did='9999')]):
            menu = await self.hass.config_entries.flow.async_init('aupu_cloud',
                context={'source': 'reauth', 'entry_id': entry.entry_id}, data=dict(entry.data))
            form = await self.hass.config_entries.flow.async_configure(menu['flow_id'],
                {'next_step_id': 'reauth_sms'})
            result = await self.hass.config_entries.flow.async_configure(form['flow_id'], {'phone': '13800000000'})
            result = await self.hass.config_entries.flow.async_configure(result['flow_id'],
                {'verification_code': '123456'})
            self.assertEqual(result['errors']['base'], 'wrong_account')
            self.assertEqual(dict(entry.data), previous)

    async def test_sms_reauth_replaces_mqtt_credentials_preserving_identity(self):
        device = {'did': '1001', 'name': 'Test heater', 'product_name': 'Test model'}
        old = {'x-amz-customauthorizer-name': 'test-authorizer',
               'x-amz-customauthorizer-signature': 'old-test-signature', 'tokenKeyName': 'old-test-token'}
        new = dict(old, tokenKeyName='new-test-token')
        auth = Mock(); auth.login.return_value = 'new-synthetic-token'
        with patch.object(self.flow_module, '_discover', return_value=[device]), \
             patch.object(self.flow_module, '_prepare_mqtt', return_value=old):
            result = await self.hass.config_entries.flow.async_init('aupu_cloud', context={'source': 'user'},
                data={'access_token': 'old-synthetic-token', 'transport': 'mqtt'})
            result = await self.hass.config_entries.flow.async_configure(result['flow_id'],
                {'local_host': '10.23.45.67', 'expected_mac': '02:00:00:00:00:01'})
            await self.hass.async_block_till_done()
        entry = result['result']; identity = (entry.entry_id, entry.unique_id)
        with patch.object(self.flow_module, 'SMSAuthClient', return_value=auth), \
             patch.object(self.flow_module, '_discover', return_value=[device]), \
             patch.object(self.flow_module, '_prepare_mqtt', return_value=new):
            menu = await self.hass.config_entries.flow.async_init('aupu_cloud',
                context={'source': 'reauth', 'entry_id': entry.entry_id}, data=dict(entry.data))
            form = await self.hass.config_entries.flow.async_configure(menu['flow_id'], {'next_step_id': 'reauth_sms'})
            result = await self.hass.config_entries.flow.async_configure(form['flow_id'], {'phone': '13800000000'})
            result = await self.hass.config_entries.flow.async_configure(result['flow_id'], {'verification_code': '123456'})
            self.assertEqual(result['step_id'], 'mqtt')
            result = await self.hass.config_entries.flow.async_configure(result['flow_id'],
                {'local_host': '10.23.45.67', 'expected_mac': '02:00:00:00:00:01'})
            self.assertEqual(result['reason'], 'reauth_successful')
            self.assertEqual((entry.entry_id, entry.unique_id), identity)
            self.assertEqual(entry.data['mqtt_credentials'], new)
            for value in ('13800000000', '123456', 'new-synthetic-token'):
                self.assertNotIn(value, repr(entry.data))
