import copy
from collections import OrderedDict

from django.db import models
from django.core.exceptions import FieldError

from shortuuid import ShortUUID
from rest_framework import serializers
from rest_framework.reverse import reverse
# from jsonbfield.fields import JSONField as JSONBField
from rest_framework.pagination import LimitOffsetPagination

import os
import requests
import logging

from kpi.widgets import ReCaptchaWidget
from django import forms
from django.conf import settings

from django.core.exceptions import ValidationError
from django.utils.translation import ugettext_lazy as _



class ReCaptchaField(forms.CharField):
    def __init__(self, attrs={}, *args, **kwargs):
        self._private_key = kwargs.pop('private_key', None)
        public_key = kwargs.pop('public_key', None)

        if 'widget' not in kwargs:
            kwargs['widget'] = ReCaptchaWidget(public_key=public_key)

        super(ReCaptchaField, self).__init__(*args, **kwargs)

    def clean(self, values):

        # Disable the check if we run a test unit
        if os.environ.get('RECAPTCHA_DISABLE', None) is not None:
            return values[0]

        super(ReCaptchaField, self).clean(values[0])
        response_token = values[0]

        try:
            r = requests.post(
                'https://www.google.com/recaptcha/api/siteverify',
                {
                    'secret': self._private_key or settings.RECAPTCHA_SECRET_KEY,
                    'response': response_token
                },
                timeout=15
            )
            r.raise_for_status()
        except requests.RequestException as e:
            raise ValidationError(
                _('Connection to reCaptcha server failed')
            )

        json_response = r.json()

        if bool(json_response['success']):
            return values[0]
        else:
            if 'error-codes' in json_response:
                if 'missing-input-secret' in json_response['error-codes'] or \
                        'invalid-input-secret' in json_response['error-codes']:
                    raise ValidationError(
                        _('Connection to reCaptcha server failed')
                    )
                else:
                    raise ValidationError(
                        _('reCaptcha invalid or expired, try again')
                    )
            else:
                raise ValidationError(
                    _('reCaptcha response from Google not valid, try again')
                )
