#!/usr/bin/python

from ansible.module_utils.basic import AnsibleModule
from openstack import connection
from openstack.exceptions import ConflictException

# Define the list of resource types to purge
RESOURCES = [
    'server', 'volume', 'snapshot', 'image', 'port', 'network', 'subnet',
    'router', 'security_group', 'floating_ip', 'keypair', 'stack'
]

# Define a dictionary to map resource types to their respective OpenStack client methods
RESOURCE_METHODS = {
    'server': ('compute', 'servers', 'delete_server'),
    'volume': ('block_storage', 'volumes', 'delete_volume'),
    'snapshot': ('block_storage', 'snapshots', 'delete_snapshot'),
    'image': ('image', 'images', 'delete_image'),
    'port': ('network', 'ports', 'delete_port'),
    'network': ('network', 'networks', 'delete_network'),
    'subnet': ('network', 'subnets', 'delete_subnet'),
    'router': ('network', 'routers', 'delete_router'),
    'security_group': ('network', 'security_groups', 'delete_security_group'),
    'floating_ip': ('network', 'ips', 'delete_ip'),
    'keypair': ('compute', 'keypairs', 'delete_keypair'),
    'stack': ('orchestration', 'stacks', 'delete_stack')
}

def gather_resource_info(conn, resource, project_id, module):
    """Retrieves information for resources of a specific type within a project."""
    module.debug(f"Gathering {resource}s for project ID {project_id}")
    client, list_method, _ = RESOURCE_METHODS[resource]
    try:
        list_func = getattr(getattr(conn, client), list_method)
        if resource in ['server', 'volume', 'snapshot']:
            return list(list_func(details=True, filters={'project_id': project_id}))
        elif resource == 'image':
            return list(list_func(filters={'owner': project_id}))
        else:
            return list(list_func(filters={'project_id': project_id}))
    except Exception as e:
        module.debug(f"Failed to gather {resource}s information: {str(e)}")
        raise Exception(f"Failed to gather {resource}s information: {str(e)}")

def delete_resources(conn, resource, resources_info, module):
    """Deletes resources of a specific type."""
    module.debug(f"Deleting {resource}s")
    client, _, delete_method = RESOURCE_METHODS[resource]
    delete_func = getattr(getattr(conn, client), delete_method)
    try:
        for res in resources_info:
            module.debug(f"Deleting {resource} {res.id}")
            if resource == 'port':
                handle_port_deletion(conn, res, module)
            elif resource == 'router':
                handle_router_deletion(conn, res, module)
            else:
                delete_func(res.id)
    except Exception as e:
        module.debug(f"Failed to delete {resource}s: {str(e)}")
        raise Exception(f"Failed to delete {resource}s: {str(e)}")

def handle_port_deletion(conn, port, module):
    """Handles the special cases for deleting ports."""
    try:
        if port.device_owner == 'network:router_interface':
            router_id = port.device_id
            module.debug(f"Removing port {port.id} from router {router_id}")
            conn.network.remove_interface_from_router(router_id, port_id=port.id)
        elif port.device_owner == 'network:router_gateway':
            router_id = port.device_id
            module.debug(f"Removing gateway port {port.id} from router {router_id}")
            conn.network.update_router(router_id, external_gateway_info=None)
        elif port.device_owner == 'network:floatingip':
            floating_ip = conn.network.find_ip(port.fixed_ips[0]['ip_address'])
            module.debug(f"Disassociating floating IP {floating_ip.id} from port {port.id}")
            conn.network.update_ip(floating_ip.id, port_id=None)
        conn.network.delete_port(port.id)
    except ConflictException as e:
        module.warn(f"Failed to delete port {port.id}: {str(e)}")

def handle_router_deletion(conn, router, module):
    """Handles the special cases for deleting routers."""
    interfaces = conn.network.ports(filters={'device_id': router.id})
    for iface in interfaces:
        module.debug(f"Removing interface {iface.id} from router {router.id}")
        conn.network.remove_interface_from_router(router.id, port_id=iface.id)
    conn.network.delete_router(router.id)

def main():
    module_args = dict(
        cloud=dict(type='str', required=True),
        project_name=dict(type='str', required=True),
        keep_project=dict(type='bool', default=False)
    )

    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=True
    )

    cloud = module.params['cloud']
    project_name = module.params['project_name']
    keep_project = module.params['keep_project']

    try:
        conn = connection.Connection(cloud=cloud)

        # Get project ID from project name
        module.debug(f"Looking up project {project_name}")
        project = conn.identity.find_project(project_name)
        if not project:
            module.fail_json(msg=f"Project {project_name} not found")
        project_id = project.id
        module.debug(f"Found project ID: {project_id}")

        if module.check_mode:
            module.exit_json(changed=True, msg="Project resources would be purged in check mode")

        for resource in RESOURCES:
            module.debug(f"Processing resource type: {resource}")
            resources_info = gather_resource_info(conn, resource, project_id, module)
            if resources_info:
                module.debug(f"Found {len(resources_info)} {resource}(s) to delete")
                delete_resources(conn, resource, resources_info, module)

        if not keep_project:
            module.debug(f"Deleting project {project_name}")
            conn.identity.delete_project(project_id)

        module.exit_json(changed=True, msg=f"Project {project_name} purged successfully")

    except Exception as e:
        module.fail_json(msg=str(e))

if __name__ == '__main__':
    main()
