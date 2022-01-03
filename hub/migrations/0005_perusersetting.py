# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
import jsonbfield.fields


class Migration(migrations.Migration):

    dependencies = [
        ('hub', '0004_configurationfile'),
    ]

    operations = [
        migrations.CreateModel(
            name='PerUserSetting',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('user_queries', jsonbfield.fields.JSONField(help_text='A JSON representation of a *list* of Django queries, e.g. `[{"email__endswith": "@kobotoolbox.org"}, {"email__endswith": "@kbtdev.org"}]`. A matching user is one who would be returned by ANY of the queries in the list.')),
                ('name', models.CharField(unique=True, max_length=255)),
                ('value_when_matched', models.CharField(max_length=2048, blank=True)),
                ('value_when_not_matched', models.CharField(max_length=2048, blank=True)),
            ],
        ),
    ]
