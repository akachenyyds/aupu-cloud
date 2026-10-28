"""SMS authentication from observed API field contracts; no bundled app secrets."""
from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

from .client import AuthenticationError, CloudError, ORIGIN, _NoRedirect

SMS_PATH = '/applicationApi/authserver/auth/user/terminal/smscode'
LOGIN_PATH = '/applicationApi/authserver/auth/user/terminal/loginByPhone'
SMS_COOLDOWN_SECONDS = 60
SMS_FLOW_TTL_SECONDS = 600


class SMSDeliveryUncertain(CloudError):
    """The one-shot send may have reached the server; never auto-resend."""


def normalize_phone(value):
    if not isinstance(value, str) or not re.fullmatch(r'1[3-9][0-9]{9}', value):
        raise ValueError('A mainland China mobile number is required')
    return value


def normalize_code(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{6}', value):
        raise ValueError('A six-digit verification code is required')
    return value


class SMSAuthClient:
    """One-shot requests; retains neither phone, code, user profile nor token."""

    def __init__(self):
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def _request(self, method, path, payload):
        if (method, path) not in (('GET', SMS_PATH), ('POST', LOGIN_PATH)):
            raise CloudError('Unsupported authentication operation')
        url = ORIGIN + path
        data = None
        if method == 'GET':
            url += '?' + urllib.parse.urlencode(payload)
        else:
            data = json.dumps(payload, separators=(',', ':')).encode()
        request = urllib.request.Request(url, data=data, method=method,
            headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
        try:
            with self._opener.open(request, timeout=15) as response:
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise ValueError
                body = json.loads(raw)
        except urllib.error.HTTPError:
            # Do not include URL/query, headers, response body or exception text.
            raise CloudError('Authentication request rejected') from None
        except (urllib.error.URLError, OSError, ValueError):
            if path == SMS_PATH:
                raise SMSDeliveryUncertain('SMS delivery was not confirmed') from None
            raise CloudError('Authentication connection or response failed') from None
        if not isinstance(body, dict) or type(body.get('status')) is not int:
            if path == SMS_PATH:
                raise SMSDeliveryUncertain('SMS delivery was not confirmed')
            raise CloudError('Unrecognized authentication response')
        if body['status'] != 0:
            if path == LOGIN_PATH:
                raise AuthenticationError('SMS verification was rejected')
            raise CloudError('SMS request was rejected')
        return body.get('result')

    def request_code(self, phone):
        phone = normalize_phone(phone)
        # AP is a fixed public client selector, not an account key or signing key.
        self._request('GET', SMS_PATH,
            {'areaCode': 86, 'appKey': 'AP', 'phoneNum': phone, 'type': 'LOG'})

    def login(self, phone, code):
        result = self._request('POST', LOGIN_PATH,
            {'areaCode': 86, 'appKey': 'AP', 'phone': normalize_phone(phone),
             'randomCode': normalize_code(code)})
        if not isinstance(result, dict) or not isinstance(result.get('token'), str):
            raise AuthenticationError('Login returned no usable account token')
        token = result['token'].strip().removeprefix('Bearer ').strip()
        if not token or len(token) > 8192 or any(ch.isspace() for ch in token):
            raise AuthenticationError('Login returned no usable account token')
        return token
