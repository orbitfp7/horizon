     --^--
    /^ ^ ^\
       | O R B I T
       |
     | | http://www.orbitproject.eu/
      U


Introduction
===============

This repository includes a set of patches that aims to introduce disaster 
recovery capabilities in OpenStack Nova by implementing an Orchestrator (named
DR-Orchestration) and integrating with the disaster recovery mechanisms 
provided by IBM's DR-Engine (
https://github.com/os-cloud-storage/openstack-workload-disaster-recovery).

The main objective of these disaster recovery capabilities is to sent enough 
information to a backup datacenter, so that if the primary datacenter 
unexpectely fails, a recovery process could start in the backup datacenter, 
and the VMs plus their data can be quickly recreated, with the same 
configuration they have in the primary datacenter, and in a transparent way 
from the users point of view.

The DR-Orchestration is divided in two main modules:

* DR-Orchestrator: in charge of orchestrating and integrating the use of the 
  DR-Engine component, as well as of offering new APIs to the users for 
  protecting their VMs and Volumes, and to the failure detection agents to 
  trigger recovery actions upon a failure detection.

* DR-Logic: in charge of optimizing the protection actions over time, i.e., it
  includes mechanisms to decide on the right time to update the information
  available at the backup datacenter, as well as to adjust the bandwidth rates
  associated to each traffic flow (i.e., VM-image, Volumes replications and
  normal datacenter operation). This is implemented in a plug-in ready way, so
  that new logics can be easily integrated and used.



Prerequisites
===============
These patches are based on the Juno version of Nova. Make sure that your
OpenStack setup is running or is compatible with Nova running Juno.


DR-Engine is not currently merged into OpenStack, therefore it needs to be 
installed and configured before installing and using the DR-Orchestration. 
More information about DR-Engine at:
https://github.com/os-cloud-storage/openstack-workload-disaster-recovery



Installation
===============

To install the DR-Orchestration over an already configured OpenStack (Juno 
version) plus DR-Engine, the next steps need to be performed:

* Download the code from the ORBIT EU FP7 github repository:

    * Nova code: https://github.com/orbitfp7/nova/tree/DR-Orchestration

    * Python-novaclient code: 
      https://github.com/orbitfp7/python-novaclient/tree/dr-orchestrator

    * Horizon code: https://github.com/orbitfp7/horizon/tree/dr-orchestrator

* Copy the downloaded code into your current nova/python-novaclient/horizon 
  source code path -- note this will add new folders and replace some existing
  files.

* Create the DR-Orchestration service daemon file at 
  `/usr/lib/systemd/system/openstack-nova-dr_orchestrator`. The content is:

  ```
  [Unit]
  Description=OpenStack Nova DR-Orchestrator Server
  After=syslog.target network.target

  [Service]
  Type=notify
  Restart=always
  User=nova
  ExecStart=/usr/bin/nova-dr_orchestrator

  [Install]
  WantedBy=multi-user.target
  ```

* Create the nova-dr_orchestrator init script in `/usr/bin/`:

  ```
  #!/usr/bin/python

  import sys

  from nova.cmd.dr_orchestrator import main

  if __name__ == "__main__":
      sys.exit(main())
  ```


Setting up
===============

* Configure nova.conf by adding the needed information by DR-Orchestration
  to connect to DR-Engine APIs. For instance:

  ```
  [dragon]
  url=http://DRAGON_SERVER_IP:5000/v2.0/
  url_timeout=600
  admin_username=admin
  admin_tenant_name=admin
  admin_password=ADMIN_PASSWORD

  backup_swift_url=http://BACKUP_SWIFT_SERVER:5000/v2.0/
  backup_swift_key=SWIFT_PASSWORD
  backup_swift_tenant=admin
  backup_swift_user=admin
  ```

* There are also several parameters that need to be configured at nova.conf 
  based on DR-Engine installation, as well as the desired default behavior 
  or DR-Orchestration:

	* `drlogic_interval`: Interval for the DR-Logic optimization control loop.
	  Default value = 30 (seconds)

	* `drlogic_clean_up_interval`: Interval for data protection clean up.
	  Default value = 3600 (seconds)

	* `max_protection_interval`: Upper limit for the time between protection
	  actions. Default is 30 (minutes)


	* `dr_policy_name`: Default name for the protect recovery policy

	* `dr_instance`: Default value to represent instance protect type. 
	  (based on DR-Engine)

	* `dr_volume`: Default value to represent volume protect type.
	  (based on DR-Engine)

	* `dr_default_instance_action`: Default replication action for instances.
	  Default is Image Copy

	* `logic_driver`: To specify the logic driver you want to use. Default 
	  value is nova.dr_orchestrator.logic.dummy_logic.DummyLogic

* Ensure other nova and DR-Engine service are up and running:
	
	* sudo systemctl status [
	  openstack-nova-compute|openstack-dragon-api|openstack-dragon-engine|...]


* Start the dr_orchestrator daemon by:
	
	* sudo systemctl reload

	* sudo systemctl start openstack-nova-dr_orchestrator


Usage
===============

Just by starting the openstack-nova-dr_orchestrator daemon, a recovery policy
is generated where the VMs and Volumes to be protected are included over time.

To protect VMs and Volumes, the users have 3 different mechanisms:

* HORIZON

  * Volumes: At volumes panel, click "Protect Volume" for the selected Volume

  * VMs: At instances panel, clieck "Protect Instance" for the selected 
    Instance

* API

  ```
  POST http://[nova-api host]:8774/v2/[project ID]/servers/[instance ID]/
  action

  #Header
  Content-Type: application/json
  X-Auth-Token: [token]

  #Payload
  { "protect_vm": null }
  ```

  ```
  POST http://[nova-api host]:8774/v2/[project ID]/servers/[volume ID]/
  action

  #Header
  Content-Type: application/json
  X-Auth-Token: [token]

  #Payload
  { "protect_volume": null }
  ```

* Nova client

  * Volumes: nova protect-volume VOLUME_ID|VOLUME_NAME

  * VMs: nova protect-vm VM_ID|VM_NAME


As for the recovery actions, the admins or fault detection agents, can recover
a protected datacenter by using:

* API

  ```
  POST http://[nova-api host]:8774/v2/[project ID]/servers/[datacenterName]/
  action

  #Header
  Content-Type: application/json
  X-Auth-Token: [token]

  #Payload
  { "recovery": null }
  ```

* Nova client

  * nova recovery DATACENTER_NAME


