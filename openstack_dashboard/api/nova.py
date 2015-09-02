# Copyright 2012 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# All Rights Reserved.
#
# Copyright 2012 OpenStack Foundation
# Copyright 2012 Nebula, Inc.
# Copyright (c) 2012 X.commerce, a business unit of eBay Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from __future__ import absolute_import

import logging

from django.conf import settings
from django.utils.functional import cached_property  # noqa
from django.utils.translation import ugettext_lazy as _

from novaclient import exceptions as nova_exceptions
from novaclient.v1_1 import client as nova_client
from novaclient.v1_1.contrib import instance_action as nova_instance_action
from novaclient.v1_1.contrib import list_extensions as nova_list_extensions
from novaclient.v1_1 import security_group_rules as nova_rules
from novaclient.v1_1 import security_groups as nova_security_groups
from novaclient.v1_1 import servers as nova_servers

from horizon import conf
from horizon.utils import functions as utils
from horizon.utils.memoized import memoized  # noqa

from openstack_dashboard.api import base
from openstack_dashboard.api import network_base


LOG = logging.getLogger(__name__)


# API static values
INSTANCE_ACTIVE_STATE = 'ACTIVE'
VOLUME_STATE_AVAILABLE = "available"
DEFAULT_QUOTA_NAME = 'default'


class VNCConsole(base.APIDictWrapper):
    """Wrapper for the "console" dictionary.

    Returned by the novaclient.servers.get_vnc_console method.
    """
    _attrs = ['url', 'type']


class SPICEConsole(base.APIDictWrapper):
    """Wrapper for the "console" dictionary.

    Returned by the novaclient.servers.get_spice_console method.
    """
    _attrs = ['url', 'type']


class RDPConsole(base.APIDictWrapper):
    """Wrapper for the "console" dictionary.

    Returned by the novaclient.servers.get_rdp_console method.
    """
    _attrs = ['url', 'type']


class Server(base.APIResourceWrapper):
    """Simple wrapper around novaclient.server.Server.

    Preserves the request info so image name can later be retrieved.
    """
    _attrs = ['addresses', 'attrs', 'id', 'image', 'links',
             'metadata', 'name', 'private_ip', 'public_ip', 'status', 'uuid',
             'image_name', 'VirtualInterfaces', 'flavor', 'key_name', 'fault',
             'tenant_id', 'user_id', 'created', 'OS-EXT-STS:power_state',
             'OS-EXT-STS:task_state', 'OS-EXT-SRV-ATTR:instance_name',
             'OS-EXT-SRV-ATTR:host', 'OS-EXT-AZ:availability_zone',
             'OS-DCF:diskConfig']

    def __init__(self, apiresource, request):
        super(Server, self).__init__(apiresource)
        self.request = request

    # TODO(gabriel): deprecate making a call to Glance as a fallback.
    @property
    def image_name(self):
        import glanceclient.exc as glance_exceptions  # noqa
        from openstack_dashboard.api import glance  # noqa

        if not self.image:
            return "-"
        if hasattr(self.image, 'name'):
            return self.image.name
        if 'name' in self.image:
            return self.image['name']
        else:
            try:
                image = glance.image_get(self.request, self.image['id'])
                return image.name
            except glance_exceptions.ClientException:
                return "-"

    @property
    def internal_name(self):
        return getattr(self, 'OS-EXT-SRV-ATTR:instance_name', "")

    @property
    def availability_zone(self):
        return getattr(self, 'OS-EXT-AZ:availability_zone', "")


class Hypervisor(base.APIDictWrapper):
    """Simple wrapper around novaclient.hypervisors.Hypervisor."""

    _attrs = ['manager', '_loaded', '_info', 'hypervisor_hostname', 'id',
              'servers']

    @property
    def servers(self):
        # if hypervisor doesn't have servers, the attribute is not present
        servers = []
        try:
            servers = self._apidict.servers
        except Exception:
            pass

        return servers


class NovaUsage(base.APIResourceWrapper):
    """Simple wrapper around contrib/simple_usage.py."""

    _attrs = ['start', 'server_usages', 'stop', 'tenant_id',
             'total_local_gb_usage', 'total_memory_mb_usage',
             'total_vcpus_usage', 'total_hours']

    def get_summary(self):
        return {'instances': self.total_active_instances,
                'memory_mb': self.memory_mb,
                'vcpus': getattr(self, "total_vcpus_usage", 0),
                'vcpu_hours': self.vcpu_hours,
                'local_gb': self.local_gb,
                'disk_gb_hours': self.disk_gb_hours,
                'ft_secondary_instances': self.ft_total_secondary_instances,
                'ft_secondary_memory_mb': self.ft_secondary_memory_mb}

    @property
    def total_active_instances(self):
        return sum(1 for s in self.server_usages if s['ended_at'] is None)

    @property
    def vcpus(self):
        return sum(s['vcpus'] for s in self.server_usages
                   if s['ended_at'] is None)

    @property
    def vcpu_hours(self):
        return getattr(self, "total_hours", 0)

    @property
    def local_gb(self):
        return sum(s['local_gb'] for s in self.server_usages
                   if s['ended_at'] is None)

    @property
    def memory_mb(self):
        return sum(s['memory_mb'] for s in self.server_usages
                   if s['ended_at'] is None)

    @property
    def disk_gb_hours(self):
        return getattr(self, "total_local_gb_usage", 0)

    @property
    def ft_total_secondary_instances(self):
        return sum(len(s.get('ft_secondary_usage', []))
                   for s in self.server_usages if s['ended_at'] is None)

    @property
    def ft_secondary_memory_mb(self):
        return sum(fts['memory_mb']
                   for s in self.server_usages
                   if s['ended_at'] is None and 'ft_secondary_usage' in s
                   for fts in s['ft_secondary_usage'])


class SecurityGroup(base.APIResourceWrapper):
    """Wrapper around novaclient.security_groups.SecurityGroup.

    Wraps its rules in SecurityGroupRule objects and allows access to them.
    """

    _attrs = ['id', 'name', 'description', 'tenant_id']

    @cached_property
    def rules(self):
        """Wraps transmitted rule info in the novaclient rule class."""
        manager = nova_rules.SecurityGroupRuleManager(None)
        rule_objs = [nova_rules.SecurityGroupRule(manager, rule)
                     for rule in self._apiresource.rules]
        return [SecurityGroupRule(rule) for rule in rule_objs]


class SecurityGroupRule(base.APIResourceWrapper):
    """Wrapper for individual rules in a SecurityGroup."""

    _attrs = ['id', 'ip_protocol', 'from_port', 'to_port', 'ip_range', 'group']

    def __unicode__(self):
        if 'name' in self.group:
            vals = {'from': self.from_port,
                    'to': self.to_port,
                    'group': self.group['name']}
            return _('ALLOW %(from)s:%(to)s from %(group)s') % vals
        else:
            vals = {'from': self.from_port,
                    'to': self.to_port,
                    'cidr': self.ip_range['cidr']}
            return _('ALLOW %(from)s:%(to)s from %(cidr)s') % vals

    # The following attributes are defined to keep compatibility with Neutron
    @property
    def ethertype(self):
        return None

    @property
    def direction(self):
        return 'ingress'


class SecurityGroupManager(network_base.SecurityGroupManager):
    backend = 'nova'

    def __init__(self, request):
        self.request = request
        self.client = novaclient(request)

    def list(self):
        return [SecurityGroup(g) for g
                in self.client.security_groups.list()]

    def get(self, sg_id):
        return SecurityGroup(self.client.security_groups.get(sg_id))

    def create(self, name, desc):
        return SecurityGroup(self.client.security_groups.create(name, desc))

    def update(self, sg_id, name, desc):
        return SecurityGroup(self.client.security_groups.update(sg_id,
                                                                name, desc))

    def delete(self, security_group_id):
        self.client.security_groups.delete(security_group_id)

    def rule_create(self, parent_group_id,
                    direction=None, ethertype=None,
                    ip_protocol=None, from_port=None, to_port=None,
                    cidr=None, group_id=None):
        # Nova Security Group API does not use direction and ethertype fields.
        sg = self.client.security_group_rules.create(parent_group_id,
                                                     ip_protocol,
                                                     from_port,
                                                     to_port,
                                                     cidr,
                                                     group_id)
        return SecurityGroupRule(sg)

    def rule_delete(self, security_group_rule_id):
        self.client.security_group_rules.delete(security_group_rule_id)

    def list_by_instance(self, instance_id):
        """Gets security groups of an instance."""
        # TODO(gabriel): This needs to be moved up to novaclient, and should
        # be removed once novaclient supports this call.
        security_groups = []
        nclient = self.client
        resp, body = nclient.client.get('/servers/%s/os-security-groups'
                                        % instance_id)
        if body:
            # Wrap data in SG objects as novaclient would.
            sg_objs = [
                nova_security_groups.SecurityGroup(
                    nclient.security_groups, sg, loaded=True)
                for sg in body.get('security_groups', [])]
            # Then wrap novaclient's object with our own. Yes, sadly wrapping
            # with two layers of objects is necessary.
            security_groups = [SecurityGroup(sg) for sg in sg_objs]
        return security_groups

    def update_instance_security_group(self, instance_id,
                                       new_security_group_ids):
        try:
            all_groups = self.list()
        except Exception:
            raise Exception(_("Couldn't get security group list."))
        wanted_groups = set([sg.name for sg in all_groups
                             if sg.id in new_security_group_ids])

        try:
            current_groups = self.list_by_instance(instance_id)
        except Exception:
            raise Exception(_("Couldn't get current security group "
                              "list for instance %s.")
                            % instance_id)
        current_group_names = set([sg.name for sg in current_groups])

        groups_to_add = wanted_groups - current_group_names
        groups_to_remove = current_group_names - wanted_groups

        num_groups_to_modify = len(groups_to_add | groups_to_remove)
        try:
            for group in groups_to_add:
                self.client.servers.add_security_group(instance_id, group)
                num_groups_to_modify -= 1
            for group in groups_to_remove:
                self.client.servers.remove_security_group(instance_id, group)
                num_groups_to_modify -= 1
        except nova_exceptions.ClientException as err:
            LOG.error(_("Failed to modify %(num_groups_to_modify)d instance "
                        "security groups: %(err)s") %
                      dict(num_groups_to_modify=num_groups_to_modify,
                           err=err))
            # reraise novaclient.exceptions.ClientException, but with
            # a sanitized error message so we don't risk exposing
            # sensitive information to the end user. This has to be
            # novaclient.exceptions.ClientException, not just
            # Exception, since the former is recognized as a
            # "recoverable" exception by horizon, and therefore the
            # error message is passed along to the end user, while
            # Exception is swallowed alive by horizon and a gneric
            # error message is given to the end user
            raise nova_exceptions.ClientException(
                err.code,
                _("Failed to modify %d instance security groups") %
                num_groups_to_modify)
        return True


class FlavorExtraSpec(object):
    def __init__(self, flavor_id, key, val):
        self.flavor_id = flavor_id
        self.id = key
        self.key = key
        self.value = val


class FloatingIp(base.APIResourceWrapper):
    _attrs = ['id', 'ip', 'fixed_ip', 'port_id', 'instance_id',
              'instance_type', 'pool']

    def __init__(self, fip):
        fip.__setattr__('port_id', fip.instance_id)
        fip.__setattr__('instance_type',
                        'compute' if fip.instance_id else None)
        super(FloatingIp, self).__init__(fip)


class FloatingIpPool(base.APIDictWrapper):
    def __init__(self, pool):
        pool_dict = {'id': pool.name,
                     'name': pool.name}
        super(FloatingIpPool, self).__init__(pool_dict)


class FloatingIpTarget(base.APIDictWrapper):
    def __init__(self, server):
        server_dict = {'name': '%s (%s)' % (server.name, server.id),
                       'id': server.id}
        super(FloatingIpTarget, self).__init__(server_dict)


class FloatingIpManager(network_base.FloatingIpManager):
    def __init__(self, request):
        self.request = request
        self.client = novaclient(request)

    def list_pools(self):
        return [FloatingIpPool(pool)
                for pool in self.client.floating_ip_pools.list()]

    def list(self):
        return [FloatingIp(fip)
                for fip in self.client.floating_ips.list()]

    def get(self, floating_ip_id):
        return FloatingIp(self.client.floating_ips.get(floating_ip_id))

    def allocate(self, pool):
        return FloatingIp(self.client.floating_ips.create(pool=pool))

    def release(self, floating_ip_id):
        self.client.floating_ips.delete(floating_ip_id)

    def associate(self, floating_ip_id, port_id):
        # In Nova implied port_id is instance_id
        server = self.client.servers.get(port_id)
        fip = self.client.floating_ips.get(floating_ip_id)
        self.client.servers.add_floating_ip(server.id, fip.ip)

    def disassociate(self, floating_ip_id, port_id):
        fip = self.client.floating_ips.get(floating_ip_id)
        server = self.client.servers.get(fip.instance_id)
        self.client.servers.remove_floating_ip(server.id, fip.ip)

    def list_targets(self):
        return [FloatingIpTarget(s) for s in self.client.servers.list()]

    def get_target_id_by_instance(self, instance_id, target_list=None):
        return instance_id

    def list_target_id_by_instance(self, instance_id, target_list=None):
        return [instance_id, ]

    def is_simple_associate_supported(self):
        return conf.HORIZON_CONFIG["simple_ip_management"]

    def is_supported(self):
        return True


@memoized
def novaclient(request):
    insecure = getattr(settings, 'OPENSTACK_SSL_NO_VERIFY', False)
    cacert = getattr(settings, 'OPENSTACK_SSL_CACERT', None)
    LOG.debug('novaclient connection created using token "%s" and url "%s"' %
              (request.user.token.id, base.url_for(request, 'compute')))
    c = nova_client.Client(request.user.username,
                           request.user.token.id,
                           project_id=request.user.tenant_id,
                           auth_url=base.url_for(request, 'compute'),
                           insecure=insecure,
                           cacert=cacert,
                           http_log_debug=settings.DEBUG)
    c.client.auth_token = request.user.token.id
    c.client.management_url = base.url_for(request, 'compute')
    return c


def server_vnc_console(request, instance_id, console_type='novnc'):
    return VNCConsole(novaclient(request).servers.get_vnc_console(instance_id,
                                                  console_type)['console'])


def server_spice_console(request, instance_id, console_type='spice-html5'):
    return SPICEConsole(novaclient(request).servers.get_spice_console(
        instance_id, console_type)['console'])


def server_rdp_console(request, instance_id, console_type='rdp-html5'):
    return RDPConsole(novaclient(request).servers.get_rdp_console(
        instance_id, console_type)['console'])


def flavor_create(request, name, memory, vcpu, disk, flavorid='auto',
                  ephemeral=0, swap=0, metadata=None, is_public=True):
    flavor = novaclient(request).flavors.create(name, memory, vcpu, disk,
                                                flavorid=flavorid,
                                                ephemeral=ephemeral,
                                                swap=swap, is_public=is_public)
    if (metadata):
        flavor_extra_set(request, flavor.id, metadata)
    return flavor


def flavor_delete(request, flavor_id):
    novaclient(request).flavors.delete(flavor_id)


def flavor_get(request, flavor_id):
    return novaclient(request).flavors.get(flavor_id)


@memoized
def flavor_list(request, is_public=True):
    """Get the list of available instance sizes (flavors)."""
    return novaclient(request).flavors.list(is_public=is_public)


@memoized
def flavor_access_list(request, flavor=None):
    """Get the list of access instance sizes (flavors)."""
    return novaclient(request).flavor_access.list(flavor=flavor)


def add_tenant_to_flavor(request, flavor, tenant):
    """Add a tenant to the given flavor access list."""
    return novaclient(request).flavor_access.add_tenant_access(
        flavor=flavor, tenant=tenant)


def remove_tenant_from_flavor(request, flavor, tenant):
    """Remove a tenant from the given flavor access list."""
    return novaclient(request).flavor_access.remove_tenant_access(
        flavor=flavor, tenant=tenant)


def flavor_get_extras(request, flavor_id, raw=False):
    """Get flavor extra specs."""
    flavor = novaclient(request).flavors.get(flavor_id)
    extras = flavor.get_keys()
    if raw:
        return extras
    return [FlavorExtraSpec(flavor_id, key, value) for
            key, value in extras.items()]


def flavor_extra_delete(request, flavor_id, keys):
    """Unset the flavor extra spec keys."""
    flavor = novaclient(request).flavors.get(flavor_id)
    return flavor.unset_keys(keys)


def flavor_extra_set(request, flavor_id, metadata):
    """Set the flavor extra spec keys."""
    flavor = novaclient(request).flavors.get(flavor_id)
    if (not metadata):  # not a way to delete keys
        return None
    return flavor.set_keys(metadata)


def snapshot_create(request, instance_id, name):
    return novaclient(request).servers.create_image(instance_id, name)


def keypair_create(request, name):
    return novaclient(request).keypairs.create(name)


def keypair_import(request, name, public_key):
    return novaclient(request).keypairs.create(name, public_key)


def keypair_delete(request, keypair_id):
    novaclient(request).keypairs.delete(keypair_id)


def keypair_list(request):
    return novaclient(request).keypairs.list()


def server_create(request, name, image, flavor, key_name, user_data,
                  security_groups, block_device_mapping=None,
                  block_device_mapping_v2=None, nics=None,
                  availability_zone=None, instance_count=1, admin_pass=None,
                  disk_config=None, config_drive=None, meta=None):
    return Server(novaclient(request).servers.create(
        name, image, flavor, userdata=user_data,
        security_groups=security_groups,
        key_name=key_name, block_device_mapping=block_device_mapping,
        block_device_mapping_v2=block_device_mapping_v2,
        nics=nics, availability_zone=availability_zone,
        min_count=instance_count, admin_pass=admin_pass,
        disk_config=disk_config, config_drive=config_drive,
        meta=meta), request)


def server_delete(request, instance):
    novaclient(request).servers.delete(instance)


def server_get(request, instance_id):
    return Server(novaclient(request).servers.get(instance_id), request)


def server_list(request, search_opts=None, all_tenants=False):
    page_size = utils.get_page_size(request)
    c = novaclient(request)
    paginate = False
    if search_opts is None:
        search_opts = {}
    elif 'paginate' in search_opts:
        paginate = search_opts.pop('paginate')
        if paginate:
            search_opts['limit'] = page_size + 1

    if all_tenants:
        search_opts['all_tenants'] = True
    else:
        search_opts['project_id'] = request.user.tenant_id
    servers = [Server(s, request)
                for s in c.servers.list(True, search_opts)]

    has_more_data = False
    if paginate and len(servers) > page_size:
        servers.pop(-1)
        has_more_data = True
    elif paginate and len(servers) == getattr(settings, 'API_RESULT_LIMIT',
                                              1000):
        has_more_data = True
    return (servers, has_more_data)


def server_console_output(request, instance_id, tail_length=None):
    """Gets console output of an instance."""
    return novaclient(request).servers.get_console_output(instance_id,
                                                          length=tail_length)


def server_pause(request, instance_id):
    novaclient(request).servers.pause(instance_id)


def server_unpause(request, instance_id):
    novaclient(request).servers.unpause(instance_id)


def server_suspend(request, instance_id):
    novaclient(request).servers.suspend(instance_id)


def server_resume(request, instance_id):
    novaclient(request).servers.resume(instance_id)


def server_reboot(request, instance_id, soft_reboot=False):
    hardness = nova_servers.REBOOT_HARD
    if soft_reboot:
        hardness = nova_servers.REBOOT_SOFT
    novaclient(request).servers.reboot(instance_id, hardness)


def server_rebuild(request, instance_id, image_id, password=None,
                   disk_config=None):
    return novaclient(request).servers.rebuild(instance_id, image_id,
                                               password, disk_config)


def server_update(request, instance_id, name):
    return novaclient(request).servers.update(instance_id, name=name)


def server_migrate(request, instance_id):
    novaclient(request).servers.migrate(instance_id)


def server_live_migrate(request, instance_id, host, block_migration=False,
                        disk_over_commit=False):
    novaclient(request).servers.live_migrate(instance_id, host,
                                             block_migration,
                                             disk_over_commit)


def server_resize(request, instance_id, flavor, disk_config=None, **kwargs):
    novaclient(request).servers.resize(instance_id, flavor,
                                       disk_config, **kwargs)


def server_confirm_resize(request, instance_id):
    novaclient(request).servers.confirm_resize(instance_id)


def server_revert_resize(request, instance_id):
    novaclient(request).servers.revert_resize(instance_id)


def server_start(request, instance_id):
    novaclient(request).servers.start(instance_id)


def server_stop(request, instance_id):
    novaclient(request).servers.stop(instance_id)


def tenant_quota_get(request, tenant_id):
    return base.QuotaSet(novaclient(request).quotas.get(tenant_id))


def tenant_quota_update(request, tenant_id, **kwargs):
    novaclient(request).quotas.update(tenant_id, **kwargs)


def default_quota_get(request, tenant_id):
    return base.QuotaSet(novaclient(request).quotas.defaults(tenant_id))


def default_quota_update(request, **kwargs):
    novaclient(request).quota_classes.update(DEFAULT_QUOTA_NAME, **kwargs)


def usage_get(request, tenant_id, start, end):
    return NovaUsage(novaclient(request).usage.get(tenant_id, start, end))


def usage_list(request, start, end):
    return [NovaUsage(u) for u in
            novaclient(request).usage.list(start, end, True)]


def virtual_interfaces_list(request, instance_id):
    return novaclient(request).virtual_interfaces.list(instance_id)


def get_x509_credentials(request):
    return novaclient(request).certs.create()


def get_x509_root_certificate(request):
    return novaclient(request).certs.get()


def get_password(request, instance_id, private_key=None):
    return novaclient(request).servers.get_password(instance_id, private_key)


def instance_volume_attach(request, volume_id, instance_id, device):
    return novaclient(request).volumes.create_server_volume(instance_id,
                                                              volume_id,
                                                              device)


def instance_volume_detach(request, instance_id, att_id):
    return novaclient(request).volumes.delete_server_volume(instance_id,
                                                              att_id)


def instance_volumes_list(request, instance_id):
    from openstack_dashboard.api import cinder

    volumes = novaclient(request).volumes.get_server_volumes(instance_id)

    for volume in volumes:
        volume_data = cinder.cinderclient(request).volumes.get(volume.id)
        volume.name = cinder.Volume(volume_data).name

    return volumes


def hypervisor_list(request):
    return novaclient(request).hypervisors.list()


def hypervisor_stats(request):
    return novaclient(request).hypervisors.statistics()


def hypervisor_search(request, query, servers=True):
    return novaclient(request).hypervisors.search(query, servers)


def evacuate_host(request, host, target=None, on_shared_storage=False):
    # TODO(jmolle) This should be change for nova atomic api host_evacuate
    hypervisors = novaclient(request).hypervisors.search(host, True)
    response = []
    err_code = None
    for hypervisor in hypervisors:
        hyper = Hypervisor(hypervisor)
        # if hypervisor doesn't have servers, the attribute is not present
        for server in hyper.servers:
            try:
                novaclient(request).servers.evacuate(server['uuid'],
                                                     target,
                                                     on_shared_storage)
            except nova_exceptions.ClientException as err:
                err_code = err.code
                msg = _("Name: %(name)s ID: %(uuid)s")
                msg = msg % {'name': server['name'], 'uuid': server['uuid']}
                response.append(msg)

    if err_code:
        msg = _('Failed to evacuate instances: %s') % ', '.join(response)
        raise nova_exceptions.ClientException(err_code, msg)

    return True


def tenant_absolute_limits(request, reserved=False):
    limits = novaclient(request).limits.get(reserved=reserved).absolute
    limits_dict = {}
    for limit in limits:
        if limit.value < 0:
            # Workaround for nova bug 1370867 that absolute_limits
            # returns negative value for total.*Used instead of 0.
            # For such case, replace negative values with 0.
            if limit.name.startswith('total') and limit.name.endswith('Used'):
                limits_dict[limit.name] = 0
            else:
                # -1 is used to represent unlimited quotas
                limits_dict[limit.name] = float("inf")
        else:
            limits_dict[limit.name] = limit.value
    return limits_dict


def availability_zone_list(request, detailed=False):
    return novaclient(request).availability_zones.list(detailed=detailed)


def service_list(request, binary=None):
    return novaclient(request).services.list(binary=binary)


def aggregate_details_list(request):
    result = []
    c = novaclient(request)
    for aggregate in c.aggregates.list():
        result.append(c.aggregates.get_details(aggregate.id))
    return result


def aggregate_create(request, name, availability_zone=None):
    return novaclient(request).aggregates.create(name, availability_zone)


def aggregate_delete(request, aggregate_id):
    return novaclient(request).aggregates.delete(aggregate_id)


def aggregate_get(request, aggregate_id):
    return novaclient(request).aggregates.get(aggregate_id)


def aggregate_update(request, aggregate_id, values):
    return novaclient(request).aggregates.update(aggregate_id, values)


def aggregate_set_metadata(request, aggregate_id, metadata):
    return novaclient(request).aggregates.set_metadata(aggregate_id, metadata)


def host_list(request):
    return novaclient(request).hosts.list()


def add_host_to_aggregate(request, aggregate_id, host):
    return novaclient(request).aggregates.add_host(aggregate_id, host)


def remove_host_from_aggregate(request, aggregate_id, host):
    return novaclient(request).aggregates.remove_host(aggregate_id, host)


@memoized
def list_extensions(request):
    return nova_list_extensions.ListExtManager(novaclient(request)).show_all()


@memoized
def extension_supported(extension_name, request):
    """Determine if nova supports a given extension name.

    Example values for the extension_name include AdminActions, ConsoleOutput,
    etc.
    """
    extensions = list_extensions(request)
    for extension in extensions:
        if extension.name == extension_name:
            return True
    return False


def can_set_server_password():
    features = getattr(settings, 'OPENSTACK_HYPERVISOR_FEATURES', {})
    return features.get('can_set_password', False)


def instance_action_list(request, instance_id):
    return nova_instance_action.InstanceActionManager(
        novaclient(request)).list(instance_id)
