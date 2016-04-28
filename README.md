     --^--
    /^ ^ ^\
       | O R B I T
       |
     | | http://www.orbitproject.eu/
      U


Introduction
===============

This repository includes a set of patches that aims to introduce post-copy
live migration capabilities in OpenStack Nova by implementing an integration
with post-copy feature in QEMU (available since v2.5.0) and LibVirt (available
since v1.3.3).

With post-copy live migration, live migration length becomes more predictable
as well as it sets an upper limit in the amount on memory to be transferred. 
Moreover, it ensure that VMs can be migrated regardless of the VM's memory 
access pattern and the available network bandwidth.


Prerequisites
===============
These patches are based on the Juno version of Nova. Make sure that your
OpenStack setup is running or is compatible with Nova running Juno.

Post-copy features were introduced in QEMU since version 2.5.0, therefore, it
requires to have installed at least that version.

Post-copy live migration was introduced in libvirt from version 1.3.3. 
However, the nova implementation its based on a previous version of this 
release, which provides an automatic switch from pre- to post-copy after the
first iteration of memory copying. The code can be downloaded from:
`https://gitlab.com/jirkade/libvirt/tree/post-copy-migration-v1`

Note specs for post-copy at Newton version will use the upstream libvirt and 
the switch between pre- and post-copy will be decided at openstack level.



Installation
===============

To install the extensions to support postcopy, it is just needed to:

* Download the code from the ORBIT EU FP7 github repository :

    * Nova code: https://github.com/orbitfp7/nova/commits/post-copy

    * Python-novaclient code: 
      https://github.com/orbitfp7/python-novaclient/commits/post-copy

    * Horizon code: https://github.com/orbitfp7/horizon/commits/post-copy


* Restart the affected services:

  * sudo systemctl restart [openstack-nova-compute|openstack-nova-api|
    httpd|apache2]



Setting up
===============

Nothing in this version. In the future it will be the threshold for the pre- 
to post-copy switch, i.e., the default number of memory iteration before the 
switch.


Usage
===============

To migrate a VM using the postcopy mechanisms (with the automatic switch after
the first memory iteration) the admins can use the next three options:

* HORIZON

  * At Admin, Instances panel, use the `live migration` action, and then click
    the post-copy checkbox if post-copy mechanisms wants to be used.

* API

  ```
  POST http://[nova-api host]:8774/v2/{tenant_id}​/servers/​{server_id}​/action
  action

  #Header
  Content-Type: application/json
  X-Auth-Token: [token]

  #Payload
  {
    "os-migrateLive": {
        "host": "...",
        "block_migration": false,
        "disk_over_commit": false,
        "post_copy": false,
    }
  }
  ```


* Nova client

  * nova live-migration [--block-migrate] [-postcopy] VM_ID|VM_NAME [DST_HOST]



