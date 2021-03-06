# -*- coding: utf-8 -*-

import copy
from hashlib import md5
import json
import requests
import StringIO

from django.conf import settings
from django.contrib.auth import get_user
from django.contrib.auth.models import User
from django.core.urlresolvers import reverse
from lxml import etree
from rest_framework import status
from rest_framework.test import APITestCase
from private_storage.storage.files import PrivateFileSystemStorage

from kpi.models import Asset
from kpi.models import AssetFile
from kpi.models import AssetVersion
from kpi.models import Collection
from kpi.models import ExportTask
from kpi.serializers import AssetListSerializer
from .kpi_test_case import KpiTestCase
from formpack.utils.expand_content import SCHEMA_VERSION

EMPTY_SURVEY = {'survey': [], 'schema': SCHEMA_VERSION, 'settings': {}}


class AssetsListApiTests(APITestCase):
    fixtures = ['test_data']

    def setUp(self):
        self.client.login(username='someuser', password='someuser')
        self.list_url = reverse('asset-list')

    def test_login_as_other_users(self):
        self.client.logout()
        self.client.login(username='admin', password='pass')
        self.client.logout()
        self.client.login(username='anotheruser', password='anotheruser')
        self.client.logout()

    def test_create_asset(self):
        """
        Ensure we can create a new asset
        """
        data = {
            'content': '{}',
            'asset_type': 'survey',
        }
        response = self.client.post(self.list_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED,
                         msg=response.data)
        sa = Asset.objects.order_by('date_created').last()
        self.assertEqual(sa.content, EMPTY_SURVEY)
        return response

    def test_delete_asset(self):
        self.client.logout()
        self.client.login(username='anotheruser', password='anotheruser')
        creation_response = self.test_create_asset()
        asset_url = creation_response.data['url']
        response = self.client.delete(asset_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK,
                         msg=response.data)

    def test_asset_list_matches_detail(self):
        detail_response = self.test_create_asset()
        list_response = self.client.get(self.list_url)
        self.assertEqual(list_response.status_code, status.HTTP_200_OK,
                         msg=list_response.data)
        expected_list_data = {
            field: detail_response.data[field]
                for field in AssetListSerializer.Meta.fields
        }
        list_result_detail = None
        for result in list_response.data['results']:
            if result['uid'] == expected_list_data['uid']:
                list_result_detail = result
                break
        self.assertIsNotNone(list_result_detail)
        self.assertDictEqual(expected_list_data, dict(list_result_detail))

    def test_assets_hash(self):
        another_user = User.objects.get(username="anotheruser")
        user_asset = Asset.objects.first()
        user_asset.save()
        user_asset.assign_perm(another_user, "view_asset")

        self.client.logout()
        self.client.login(username="anotheruser", password="anotheruser")
        creation_response = self.test_create_asset()

        another_user_asset = another_user.assets.last()
        another_user_asset.save()

        versions_ids = [
            user_asset.version_id,
            another_user_asset.version_id
        ]
        versions_ids.sort()
        expected_hash = md5("".join(versions_ids)).hexdigest()
        hash_url = reverse("asset-hash-list")
        hash_response = self.client.get(hash_url)
        self.assertEqual(hash_response.data.get("hash"), expected_hash)


class AssetVersionApiTests(APITestCase):
    fixtures = ['test_data']

    def setUp(self):
        self.client.login(username='someuser', password='someuser')
        self.asset = Asset.objects.first()
        self.asset.save()
        self.version = self.asset.asset_versions.first()
        self.version_list_url = reverse('asset-version-list',
                                        args=(self.asset.uid,))

    def test_asset_version(self):
        self.assertEqual(Asset.objects.count(), 2)
        self.assertEqual(AssetVersion.objects.count(), 1)
        resp = self.client.get(self.version_list_url, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        _version_detail_url = resp.data['results'][0].get('url')
        resp2 = self.client.get(_version_detail_url, format='json')
        self.assertTrue('survey' in resp2.data['content'])
        self.assertEqual(len(resp2.data['content']['survey']), 2)

    def test_asset_version_content_hash(self):
        resp = self.client.get(self.version_list_url, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        first_version = resp.data['results'][0]
        asset = AssetVersion.objects.get(uid=first_version['uid']).asset
        self.assertEqual(first_version['content_hash'],
                         asset.latest_version.content_hash)
        resp2 = self.client.get(first_version['url'], format='json')
        self.assertEqual(resp2.data['content_hash'],
                         asset.latest_version.content_hash)

    def test_restricted_access_to_version(self):
        self.client.logout()
        self.client.login(username='anotheruser', password='anotheruser')
        resp = self.client.get(self.version_list_url, format='json')
        self.assertEqual(resp.data['count'], 0)
        _version_detail_url = reverse('asset-version-detail',
                                      args=(self.asset.uid, self.version.uid))
        resp2 = self.client.get(_version_detail_url)
        self.assertEqual(resp2.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(resp2.data['detail'], 'Not found.')


class AssetsDetailApiTests(APITestCase):
    fixtures = ['test_data']

    def setUp(self):
        self.client.login(username='someuser', password='someuser')
        url = reverse('asset-list')
        data = {'content': '{}', 'asset_type': 'survey'}
        self.r = self.client.post(url, data, format='json')
        self.asset = Asset.objects.get(uid=self.r.data.get('uid'))
        self.asset_url = self.r.data['url']
        self.assertEqual(self.r.status_code, status.HTTP_201_CREATED)
        self.asset_uid = self.r.data['uid']

    def test_asset_exists(self):
        resp = self.client.get(self.asset_url, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_can_update_asset_settings(self):
        data = {
            'settings': json.dumps({
                'mysetting': 'value'
            }),
        }
        resp = self.client.patch(self.asset_url, data, format='json')
        self.assertEqual(resp.data['settings'], {'mysetting': "value"})

    def test_asset_has_deployment_data(self):
        response = self.client.get(self.asset_url, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get('deployment__active'), False)
        self.assertEqual(response.data.get('has_deployment'), False)

    def test_asset_deployment_data_updates(self):
        deployment_url = reverse('asset-deployment',
                                 kwargs={'uid': self.asset_uid})

        response1 = self.client.post(deployment_url, {
                'backend': 'mock',
                'active': True,
            })

        self.assertEqual(response1.data.get("asset").get('deployment__active'), True)
        self.assertEqual(response1.data.get("asset").get('has_deployment'), True)

        response2 = self.client.get(self.asset_url, format='json')
        self.assertEqual(response2.data.get('deployment__active'), True)
        self.assertEqual(response2.data['has_deployment'], True)

    def test_can_clone_asset(self):
        response = self.client.post(reverse('asset-list'),
                                    format='json',
                                    data={
                                       'clone_from': self.r.data.get('uid'),
                                       'name': 'clones_name',
                                    })
        self.assertEqual(response.status_code, 201)
        new_asset = Asset.objects.get(uid=response.data.get('uid'))
        self.assertEqual(new_asset.content, EMPTY_SURVEY)
        self.assertEqual(new_asset.name, 'clones_name')

    def test_can_clone_version_of_asset(self):
        v1_uid = self.asset.asset_versions.first().uid
        self.asset.content = {'survey': [{'type': 'note', 'label': 'v2'}]}
        self.asset.save()
        self.assertEqual(self.asset.asset_versions.count(), 2)
        v2_uid = self.asset.asset_versions.first().uid
        self.assertNotEqual(v1_uid, v2_uid)

        self.asset.content = {'survey': [{'type': 'note', 'label': 'v3'}]}
        self.asset.save()
        self.assertEqual(self.asset.asset_versions.count(), 3)
        response = self.client.post(reverse('asset-list'),
                                    format='json',
                                    data={
                                       'clone_from_version_id': v2_uid,
                                       'clone_from': self.r.data.get('uid'),
                                       'name': 'clones_name',
                                    })

        self.assertEqual(response.status_code, 201)
        new_asset = Asset.objects.get(uid=response.data.get('uid'))
        self.assertEqual(new_asset.content['survey'][0]['label'], ['v2'])
        self.assertEqual(new_asset.content['translations'], [None])

    def test_deployed_version_pagination(self):
        PAGE_LENGTH = 100
        version = self.asset.latest_version
        preexisting_count = self.asset.deployed_versions.count()
        version.deployed = True
        for i in range(PAGE_LENGTH + 11):
            version.uid = ''
            version.pk = None
            version.save()
        self.assertEqual(
            preexisting_count + PAGE_LENGTH + 11,
            self.asset.deployed_versions.count()
        )
        response = self.client.get(self.asset_url, format='json')
        self.assertEqual(
            response.data['deployed_versions']['count'],
            self.asset.deployed_versions.count()
        )
        self.assertEqual(
            len(response.data['deployed_versions']['results']),
            PAGE_LENGTH
        )

    def check_asset_writable_json_field(self, field_name, **kwargs):
        expected_default = kwargs.get('expected_default', {})
        test_data = kwargs.get(
            'test_data',
            {'test_field': 'test value for {}'.format(field_name)}
        )
        # Check the default value
        response = self.client.get(self.asset_url, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[field_name], expected_default)
        # Update
        response = self.client.patch(
            self.asset_url, format='json',
            data={field_name: json.dumps(test_data)}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Verify
        response = self.client.get(self.asset_url, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[field_name], test_data)

    def test_report_custom_field(self):
        self.check_asset_writable_json_field('report_custom')

    def test_report_styles_field(self):
        test_data = copy.deepcopy(self.asset.report_styles)
        test_data['default'] = {'report_type': 'vertical'}
        self.check_asset_writable_json_field(
            'report_styles',
            expected_default=self.asset.report_styles,
            test_data=test_data
        )

    def test_map_styles_field(self):
        self.check_asset_writable_json_field('map_styles')

    def test_map_custom_field(self):
        self.check_asset_writable_json_field('map_custom')

    def test_asset_version_id_and_content_hash(self):
        response = self.client.get(self.asset_url, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.asset.version_id, self.asset.latest_version.uid)
        self.assertEqual(response.data['version_id'],
                         self.asset.version_id)
        self.assertEqual(response.data['version__content_hash'],
                         self.asset.latest_version.content_hash)


class AssetsXmlExportApiTests(KpiTestCase):
    fixtures = ['test_data']

    def test_xml_export_title_retained(self):
        asset_title= 'XML Export Test Asset Title'
        content= {'settings': [{'id_string': 'titled_asset'}],
                 'survey': [{'label': 'Q1 Label.', 'type': 'decimal'}]}
        self.login('someuser', 'someuser')
        asset= self.create_asset(asset_title, json.dumps(content), format='json')
        response= self.client.get(reverse('asset-detail',
                                          kwargs={'uid':asset.uid, 'format': 'xml'}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        xml= etree.fromstring(response.content)
        title_elts= xml.xpath('./*[local-name()="head"]/*[local-name()="title"]')
        self.assertEqual(len(title_elts), 1)
        self.assertEqual(title_elts[0].text, asset_title)

    def test_xml_export_name_as_title(self):
        asset_name= 'XML Export Test Asset Name'
        content= {'settings': [{'form_id': 'named_asset'}],
                 'survey': [{'label': 'Q1 Label.', 'type': 'decimal'}]}
        self.login('someuser', 'someuser')
        asset= self.create_asset(asset_name, json.dumps(content), format='json')
        response= self.client.get(reverse('asset-detail',
                                          kwargs={'uid':asset.uid, 'format': 'xml'}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        xml= etree.fromstring(response.content)
        title_elts= xml.xpath('./*[local-name()="head"]/*[local-name()="title"]')
        self.assertEqual(len(title_elts), 1)
        self.assertEqual(title_elts[0].text, asset_name)

    def test_api_xml_export_auto_title(self):
        content = {'settings': [{'form_id': 'no_title_asset'}],
                   'survey': [{'label': 'Q1 Label.', 'type': 'decimal'}]}
        self.login('someuser', 'someuser')
        asset = self.create_asset('', json.dumps(content), format='json')
        response = self.client.get(reverse('asset-detail',
                                           kwargs={'uid': asset.uid, 'format': 'xml'}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        xml = etree.fromstring(response.content)
        title_elts = xml.xpath('./*[local-name()="head"]/*[local-name()="title"]')
        self.assertEqual(len(title_elts), 1)
        self.assertNotEqual(title_elts[0].text, '')

    def test_xml_export_group(self):
        example_formbuilder_output= {'survey': [{"type": "begin_group",
                                                 "relevant": "",
                                                 "appearance": "",
                                                 "name": "group_hl3hw45",
                                                 "label": "Group 1 Label"},
                                                {"required": "true",
                                                 "type": "decimal",
                                                 "label": "Question 1 Label"},
                                                {"type": "end_group"}],
                                     "settings": [{"form_title": "",
                                                   "form_id": "group_form"}]}

        self.login('someuser', 'someuser')
        asset= self.create_asset('', json.dumps(example_formbuilder_output), format='json')
        response= self.client.get(reverse('asset-detail',
                                          kwargs={'uid':asset.uid, 'format': 'xml'}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        xml= etree.fromstring(response.content)
        group_elts= xml.xpath('./*[local-name()="body"]/*[local-name()="group"]')
        self.assertEqual(len(group_elts), 1)
        self.assertNotIn('relevant', group_elts[0].attrib)


class ObjectRelationshipsTests(APITestCase):
    fixtures = ['test_data']

    def setUp(self):
        self.client.login(username='someuser', password='someuser')
        self.user = User.objects.get(username='someuser')
        self.surv = Asset.objects.create(content={'survey': [{"type": "text", "name": "q1"}]},
                                         owner=self.user,
                                         asset_type='survey')
        self.coll = Collection.objects.create(name='sample collection', owner=self.user)

    def _count_children_by_kind(self, children, kind):
        count = 0
        # TODO: Request all pages of children
        for child in children['results']:
            if child['kind'] == kind:
                count += 1
        return count

    def test_list_asset(self):
        pass

    def test_collection_can_have_asset(self):
        '''
        * after assigning a asset, self.surv, to a collection (self.coll) [via the ORM]
            the asset is now listed in the collection's list of assets.
        '''
        _ = self.client.get(reverse('asset-detail', args=[self.surv.uid]))
        coll_req1 = self.client.get(reverse('collection-detail', args=[self.coll.uid]))
        self.assertEqual(self._count_children_by_kind(
            coll_req1.data['children'], self.surv.kind), 0)

        self.surv.parent = self.coll
        self.surv.save()

        surv_req2 = self.client.get(reverse('asset-detail', args=[self.surv.uid]))
        self.assertIn('parent', surv_req2.data)
        self.assertIn(self.coll.uid, surv_req2.data['parent'])

        coll_req2 = self.client.get(reverse('collection-detail', args=[self.coll.uid]))
        self.assertEqual(self._count_children_by_kind(
            coll_req2.data['children'], self.surv.kind), 1)
        self.assertEqual(
            self.surv.uid, coll_req2.data['children']['results'][0]['uid'])

    def test_add_asset_to_collection(self):
        '''
        * a survey starts out with no collection.
        * assigning a collection to the survey returns a HTTP 200 code.
        * a follow up query on the asset shows that the collection is now set
        '''
        self.assertEqual(self.surv.parent, None)
        surv_url = reverse('asset-detail', args=[self.surv.uid])
        patch_req = self.client.patch(
            surv_url, data={'parent': reverse('collection-detail', args=[self.coll.uid])})
        self.assertEqual(patch_req.status_code, status.HTTP_200_OK)
        req = self.client.get(surv_url)
        self.assertIn('/collections/%s' % (self.coll.uid), req.data['parent'])

    def test_remove_asset_from_collection(self):
        '''
        * a survey starts out with no collection.
        * assigning a collection to the survey returns a HTTP 200 code.
        * a follow up query on the asset shows that the collection is now set
        * removing the collection assignment returns a HTTP 200 code.
        * a follow up query on the asset shows the collection unassigned
        '''
        self.assertEqual(self.surv.parent, None)
        surv_url = reverse('asset-detail', args=[self.surv.uid])
        patch_req = self.client.patch(
            surv_url, data={'parent': reverse('collection-detail', args=[self.coll.uid])})
        self.assertEqual(patch_req.status_code, status.HTTP_200_OK)
        req = self.client.get(surv_url)
        self.assertIn('/collections/%s' % (self.coll.uid), req.data['parent'])
        # Assigned asset to collection successfully; now remove it
        patch_req = self.client.patch(surv_url, data={'parent': ''})
        self.assertEqual(patch_req.status_code, status.HTTP_200_OK)
        req = self.client.get(surv_url)
        self.assertIsNone(req.data['parent'])

    def test_move_asset_between_collections(self):
        '''
        * a survey starts out with no collection.
        * assigning a collection to the survey returns a HTTP 200 code.
        * a follow up query on the asset shows that the collection is now set
        * assigning a new collection to the survey returns a HTTP 200 code.
        * a follow up query on the asset shows the new collection now set
        '''
        self.assertEqual(self.surv.parent, None)
        surv_url = reverse('asset-detail', args=[self.surv.uid])
        patch_req = self.client.patch(surv_url, data={'parent': reverse(
            'collection-detail', args=[self.coll.uid])})
        self.assertEqual(patch_req.status_code, status.HTTP_200_OK)
        req = self.client.get(surv_url)
        self.assertIn('/collections/%s' % (self.coll.uid), req.data['parent'])
        # Assigned asset to collection successfully; now move it to another
        other_coll = Collection.objects.create(
            name='another collection', owner=self.user)
        patch_req = self.client.patch(surv_url, data={'parent': reverse(
            'collection-detail', args=[other_coll.uid])})
        self.assertEqual(patch_req.status_code, status.HTTP_200_OK)
        req = self.client.get(surv_url)
        self.assertIn('/collections/%s' % (other_coll.uid), req.data['parent'])


class AssetsSettingsFieldTest(KpiTestCase):
    fixtures = ['test_data']

    def test_query_settings(self):
        asset_title = 'asset_title'
        content = {'settings': [{'id_string': 'titled_asset'}],
                 'survey': [{'label': 'Q1 Label.', 'type': 'decimal'}]}
        self.login('someuser', 'someuser')
        asset = self.create_asset(None, json.dumps(content), format='json')
        self.assert_object_in_object_list(asset)
        # Note: This is not an API method, but an ORM one.
        self.assertFalse(Asset.objects.filter(settings__id_string='titled_asset'))


class AssetExportTaskTest(APITestCase):
    fixtures = ['test_data']

    def setUp(self):
        self.client.login(username='someuser', password='someuser')
        self.user = User.objects.get(username='someuser')
        self.asset = Asset.objects.create(
            content={'survey': [{"type": "text", "name": "q1"}]},
            owner=self.user,
            asset_type='survey',
            name=u'???????? ??????????'
        )
        self.asset.deploy(backend='mock', active=True)
        self.asset.save()
        v_uid = self.asset.latest_deployed_version.uid
        submission = {
            '__version__': v_uid,
            'q1': u'??Qu?? tal?'
        }
        self.asset.deployment.mock_submissions([submission])
        settings.CELERY_TASK_ALWAYS_EAGER = True

    def result_stored_locally(self, detail_response):
        '''
        Return `True` if the result is stored locally, or `False` if it's
        housed externally (e.g. on Amazon S3)
        '''
        export_task = ExportTask.objects.get(uid=detail_response.data['uid'])
        return isinstance(export_task.result.storage, PrivateFileSystemStorage)

    def test_owner_can_create_export(self):
        post_url = reverse('exporttask-list')
        asset_url = reverse('asset-detail', args=[self.asset.uid])
        task_data = {
            'source': asset_url,
            'type': 'csv',
        }
        # Create the export task
        response = self.client.post(post_url, task_data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # Task should complete right away due to `CELERY_TASK_ALWAYS_EAGER`
        detail_response = self.client.get(response.data['url'])
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_response.data['status'], 'complete')
        self.assertEqual(detail_response.data['messages'], {})
        # Get the result file
        if self.result_stored_locally(detail_response):
            result_response = self.client.get(detail_response.data['result'])
            result_content = ''.join(result_response.streaming_content)
        else:
            result_response = requests.get(detail_response.data['result'])
            result_content = result_response.content
        self.assertEqual(result_response.status_code, status.HTTP_200_OK)
        expected_content = ''.join([
            '"q1";"_id";"_uuid";"_submission_time";"_validation_status";"_index"\r\n',
            '"??Qu?? tal?";"";"";"";"";"1"\r\n',
        ])
        self.assertEqual(result_content, expected_content)
        return detail_response

    def test_other_user_cannot_access_export(self):
        detail_response = self.test_owner_can_create_export()
        self.client.logout()
        self.client.login(username='otheruser', password='otheruser')
        response = self.client.get(detail_response.data['url'])
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        if self.result_stored_locally(detail_response):
            # This check only makes sense for locally-stored results, since S3
            # uses query parameters in the URL for access control
            response = self.client.get(detail_response.data['result'])
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_anon_cannot_access_export(self):
        detail_response = self.test_owner_can_create_export()
        self.client.logout()
        response = self.client.get(detail_response.data['url'])
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        if self.result_stored_locally(detail_response):
            # This check only makes sense for locally-stored results, since S3
            # uses query parameters in the URL for access control
            response = self.client.get(detail_response.data['result'])
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class AssetFileTest(APITestCase):
    fixtures = ['test_data']

    def setUp(self):
        self.client.login(username='someuser', password='someuser')
        self.current_username = 'someuser'
        self.asset = Asset.objects.filter(owner__username='someuser').first()
        self.list_url = reverse('asset-file-list', args=[self.asset.uid])
        # TODO: change the fixture so every asset's owner has all expected
        # permissions?  For now, call `save()` to recalculate permissions and
        # verify the result
        self.asset.save()
        self.assertListEqual(
            sorted(list(self.asset.get_perms(self.asset.owner))),
            sorted(list(Asset.ASSIGNABLE_PERMISSIONS +
                        Asset.CALCULATED_PERMISSIONS))
        )

    @staticmethod
    def absolute_reverse(*args, **kwargs):
        return 'http://testserver/' + reverse(*args, **kwargs).lstrip('/')

    def get_asset_file_content(self, url):
        response = self.client.get(url)
        return ''.join(response.streaming_content)

    @property
    def asset_file_payload(self):
        return {
            'file_type': 'map_layer',
            'name': 'Dinagat Islands',
            'content':
                StringIO.StringIO(json.dumps(
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [125.6, 10.1]
                        },
                        "properties": {
                            "name": "Dinagat Islands"
                        }
                    }
                )),
            'metadata': json.dumps({'source': 'http://geojson.org/'}),
        }

    def switch_user(self, *args, **kwargs):
        self.client.logout()
        self.client.login(*args, **kwargs)
        self.current_username = kwargs['username']

    def create_asset_file(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(json.loads(response.content)['count'], 0)
        response = self.client.post(self.list_url, self.asset_file_payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        return response

    def verify_asset_file(self, response):
        posted_payload = self.asset_file_payload
        response_dict = json.loads(response.content)
        self.assertEqual(
            response_dict['asset'],
            self.absolute_reverse('asset-detail', args=[self.asset.uid])
        )
        self.assertEqual(
            response_dict['user'],
            self.absolute_reverse('user-detail',
                                  args=[self.current_username])
        )
        self.assertEqual(
            response_dict['user__username'],
            self.current_username,
        )
        self.assertEqual(
            json.dumps(response_dict['metadata']),
            posted_payload['metadata']
        )
        for field in 'file_type', 'name':
            self.assertEqual(response_dict[field], posted_payload[field])
        # Content via the direct URL to the file
        posted_payload['content'].seek(0)
        expected_content = posted_payload['content'].read()
        self.assertEqual(
            self.get_asset_file_content(response_dict['content']),
            expected_content
        )
        return response_dict['uid']

    def test_owner_can_create_file(self):
        self.verify_asset_file(self.create_asset_file())

    def test_owner_can_delete_file(self):
        af_uid = self.verify_asset_file(self.create_asset_file())
        detail_url = reverse('asset-file-detail',
                             args=(self.asset.uid, af_uid))
        response = self.client.delete(detail_url)
        self.assertTrue(response.status_code, status.HTTP_204_NO_CONTENT)
        # TODO: test that the file itself is removed

    def test_editor_can_create_file(self):
        anotheruser = User.objects.get(username='anotheruser')
        self.assertListEqual(list(self.asset.get_perms(anotheruser)), [])
        self.asset.assign_perm(anotheruser, 'change_asset')
        self.assertTrue(self.asset.has_perm(anotheruser, 'change_asset'))
        self.switch_user(username='anotheruser', password='anotheruser')
        self.verify_asset_file(self.create_asset_file())

    def test_editor_can_delete_file(self):
        anotheruser = User.objects.get(username='anotheruser')
        self.assertListEqual(list(self.asset.get_perms(anotheruser)), [])
        self.asset.assign_perm(anotheruser, 'change_asset')
        self.assertTrue(self.asset.has_perm(anotheruser, 'change_asset'))
        self.switch_user(username='anotheruser', password='anotheruser')
        af_uid = self.verify_asset_file(self.create_asset_file())
        detail_url = reverse('asset-file-detail',
                             args=(self.asset.uid, af_uid))
        response = self.client.delete(detail_url)
        self.assertTrue(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_viewer_can_access_file(self):
        af_uid = self.verify_asset_file(self.create_asset_file())
        detail_url = reverse('asset-file-detail',
                             args=(self.asset.uid, af_uid))
        anotheruser = User.objects.get(username='anotheruser')
        self.assertListEqual(list(self.asset.get_perms(anotheruser)), [])
        self.asset.assign_perm(anotheruser, 'view_asset')
        self.assertTrue(self.asset.has_perm(anotheruser, 'view_asset'))
        self.switch_user(username='anotheruser', password='anotheruser')
        response = self.client.get(detail_url)
        self.assertTrue(response.status_code, status.HTTP_200_OK)

    def test_viewer_cannot_create_file(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(json.loads(response.content)['count'], 0)

        self.switch_user(username='anotheruser', password='anotheruser')
        anotheruser = User.objects.get(username='anotheruser')
        self.assertListEqual(list(self.asset.get_perms(anotheruser)), [])
        self.asset.assign_perm(anotheruser, 'view_asset')
        self.assertTrue(self.asset.has_perm(anotheruser, 'view_asset'))
        response = self.client.post(self.list_url, self.asset_file_payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.switch_user(username='someuser', password='someuser')
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(json.loads(response.content)['count'], 0)

    def test_viewer_cannot_delete_file(self):
        af_uid = self.verify_asset_file(self.create_asset_file())
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(json.loads(response.content)['count'], 1)
        detail_url = reverse('asset-file-detail',
                             args=(self.asset.uid, af_uid))

        self.switch_user(username='anotheruser', password='anotheruser')
        anotheruser = User.objects.get(username='anotheruser')
        self.assertListEqual(list(self.asset.get_perms(anotheruser)), [])
        self.asset.assign_perm(anotheruser, 'view_asset')
        self.assertTrue(self.asset.has_perm(anotheruser, 'view_asset'))
        response = self.client.delete(detail_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.switch_user(username='someuser', password='someuser')
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(json.loads(response.content)['count'], 1)

    def test_unprivileged_user_cannot_access_file(self):
        af_uid = self.verify_asset_file(self.create_asset_file())
        detail_url = reverse('asset-file-detail',
                             args=(self.asset.uid, af_uid))
        anotheruser = User.objects.get(username='anotheruser')
        self.assertListEqual(list(self.asset.get_perms(anotheruser)), [])
        self.switch_user(username='anotheruser', password='anotheruser')
        response = self.client.get(detail_url)
        self.assertTrue(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_anon_cannot_access_file(self):
        af_uid = self.verify_asset_file(self.create_asset_file())
        detail_url = reverse('asset-file-detail',
                             args=(self.asset.uid, af_uid))

        self.client.logout()
        response = self.client.get(detail_url)
        self.assertTrue(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_files_are_filtered_by_parent_asset(self):
        af1_uid = self.verify_asset_file(self.create_asset_file())
        af1 = AssetFile.objects.get(uid=af1_uid)
        af1.asset = self.asset.clone()
        af1.asset.owner = self.asset.owner
        af1.asset.save()
        af1.save()
        af2_uid = self.verify_asset_file(self.create_asset_file())
        af2 = AssetFile.objects.get(uid=af2_uid)

        for af in af1, af2:
            response = self.client.get(
                reverse('asset-file-list', args=[af.asset.uid]))
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            asset_files = json.loads(response.content)['results']
            self.assertEqual(len(asset_files), 1)
            self.assertEqual(asset_files[0]['uid'], af.uid)
