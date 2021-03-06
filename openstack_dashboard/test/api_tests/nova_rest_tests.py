# Copyright 2014, Rackspace, US, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import mock
import unittest2

from django.conf import settings

from openstack_dashboard.api.rest import nova

from rest_test_utils import construct_request   # noqa


class NovaRestTestCase(unittest2.TestCase):
    def assertStatusCode(self, response, expected_code):
        if response.status_code == expected_code:
            return
        self.fail('status code %r != %r: %s' % (response.status_code,
                                                expected_code,
                                                response.content))

    #
    # Keypairs
    #
    @mock.patch.object(nova.api, 'nova')
    def test_keypair_get(self, nc):
        request = construct_request()
        nc.keypair_list.return_value = [
            mock.Mock(**{'to_dict.return_value': {'id': 'one'}}),
            mock.Mock(**{'to_dict.return_value': {'id': 'two'}}),
        ]
        response = nova.Keypairs().get(request)
        self.assertStatusCode(response, 200)
        self.assertEqual(response.content,
                         '{"items": [{"id": "one"}, {"id": "two"}]}')
        nc.keypair_list.assert_called_once_with(request)

    @mock.patch.object(nova.api, 'nova')
    def test_keypair_create(self, nc):
        request = construct_request(body='''{"name": "Ni!"}''')
        new = nc.keypair_create.return_value
        new.to_dict.return_value = {'name': 'Ni!', 'public_key': 'sekrit'}
        new.name = 'Ni!'
        with mock.patch.object(settings, 'DEBUG', True):
            response = nova.Keypairs().post(request)
        self.assertStatusCode(response, 201)
        self.assertEqual(response.content,
                         '{"name": "Ni!", "public_key": "sekrit"}')
        self.assertEqual(response['location'], '/api/nova/keypairs/Ni%21')
        nc.keypair_create.assert_called_once_with(request, 'Ni!')

    @mock.patch.object(nova.api, 'nova')
    def test_keypair_import(self, nc):
        request = construct_request(body='''
            {"name": "Ni!", "public_key": "hi"}
        ''')
        new = nc.keypair_import.return_value
        new.to_dict.return_value = {'name': 'Ni!', 'public_key': 'hi'}
        new.name = 'Ni!'
        with mock.patch.object(settings, 'DEBUG', True):
            response = nova.Keypairs().post(request)
        self.assertStatusCode(response, 201)
        self.assertEqual(response.content,
                         '{"name": "Ni!", "public_key": "hi"}')
        self.assertEqual(response['location'], '/api/nova/keypairs/Ni%21')
        nc.keypair_import.assert_called_once_with(request, 'Ni!', 'hi')

    #
    # Availability Zones
    #
    def test_availzone_get_brief(self):
        self._test_availzone_get(False)

    def test_availzone_get_detailed(self):
        self._test_availzone_get(True)

    @mock.patch.object(nova.api, 'nova')
    def _test_availzone_get(self, detail, nc):
        if detail:
            request = construct_request(GET={'detailed': 'true'})
        else:
            request = construct_request(GET={})
        nc.availability_zone_list.return_value = [
            mock.Mock(**{'to_dict.return_value': {'id': 'one'}}),
            mock.Mock(**{'to_dict.return_value': {'id': 'two'}}),
        ]
        response = nova.AvailabilityZones().get(request)
        self.assertStatusCode(response, 200)
        self.assertEqual(response.content,
                         '{"items": [{"id": "one"}, {"id": "two"}]}')
        nc.availability_zone_list.assert_called_once_with(request, detail)

    #
    # Limits
    #
    def test_limits_get_not_reserved(self):
        self._test_limits_get(False)

    def test_limits_get_reserved(self):
        self._test_limits_get(True)

    @mock.patch.object(nova.api, 'nova')
    def _test_limits_get(self, reserved, nc):
        if reserved:
            request = construct_request(GET={'reserved': 'true'})
        else:
            request = construct_request(GET={})
        nc.tenant_absolute_limits.return_value = {'id': 'one'}
        response = nova.Limits().get(request)
        self.assertStatusCode(response, 200)
        nc.tenant_absolute_limits.assert_called_once_with(request, reserved)
        self.assertEqual(response.content, '{"id": "one"}')

    #
    # Servers
    #
    @mock.patch.object(nova.api, 'nova')
    def test_server_create_missing(self, nc):
        request = construct_request(body='''{"name": "hi"}''')
        response = nova.Servers().post(request)
        self.assertStatusCode(response, 400)
        self.assertEqual(response.content,
                         '"missing required parameter \'source_id\'"')
        nc.server_create.assert_not_called()

    @mock.patch.object(nova.api, 'nova')
    def test_server_create_basic(self, nc):
        request = construct_request(body='''{"name": "Ni!",
            "source_id": "image123", "flavor_id": "flavor123",
            "key_name": "sekrit", "user_data": "base64 yes",
            "security_groups": [{"name": "root"}]}
        ''')
        new = nc.server_create.return_value
        new.to_dict.return_value = {'id': 'server123'}
        new.id = 'server123'
        response = nova.Servers().post(request)
        self.assertStatusCode(response, 201)
        self.assertEqual(response.content, '{"id": "server123"}')
        self.assertEqual(response['location'], '/api/nova/servers/server123')
        nc.server_create.assert_called_once_with(
            request, 'Ni!', 'image123', 'flavor123', 'sekrit', 'base64 yes',
            [{'name': 'root'}]
        )
