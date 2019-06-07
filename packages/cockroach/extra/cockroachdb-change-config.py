#!/usr/bin/env python
import json
import logging
import subprocess

from dcos_internal_utils import utils


"""Use this node's internal IP address to reach the local CockroachDB instance
and update its configuration state.

This program must be expected to be invoked periodicially or arbitrarily often.
That is, each configuration update must be idempotent, or at least it must be
applicable multiple times at arbitrary points in time during cluster runtime
without harming the operation of CockroachDB.
"""


log = logging.getLogger(__name__)
logging.basicConfig(format='[%(levelname)s] %(message)s', level='INFO')


def set_num_replicas(my_internal_ip: str, num_replicas: int) -> None:
    """
    Use `cockroach zone set` to set the cluster-wide configuration setting
    num_replicas to `num_replicas`. This does not matter on a 3-master
    DC/OS cluster because the CockroachDB default for num_replicas is 3.
    This however ensures that num_replicas is set to 5 on a 5-master DC/OS
    cluster. Feed the configuration key/value pair to the `cockroach` program
    via stdin.

    Relevant JIRA ticket: https://jira.mesosphere.com/browse/DCOS-20352

    Display `num_replicas` that have to be changed by using the SQL command:
    `SHOW ALL ZONE CONFIGURATIONS;` in CockroachDB.
    """

    def _set_replicas_for_zone(zone: str, db_entity: str) -> None:
        zone_config = 'num_replicas = {}'.format(num_replicas)
        sql_command = (
            'ALTER {db_entity} CONFIGURE ZONE USING {zone_config};'
        ).format(
            db_entity=db_entity,
            zone_config=zone_config,
        )
        command = (
            '/opt/mesosphere/active/cockroach/bin/cockroach '
            'sql -e "{sql_command}" --insecure --host={host}'
        ).format(
            sql_command=sql_command,
            host=my_internal_ip,
        )
        message = (
            'Set {zone_config} for {zone} via command {command}'
        ).format(
            zone_config=zone_config,
            zone=zone,
            command=command,
        )
        log.info(message)
        subprocess.run(command, shell=True)
        log.info('Command returned')

    zone_database_entities = [
        ('.default', 'RANGE default'),
        ('system', 'DATABASE system'),
        ('system.jobs', 'TABLE system.public.jobs'),
        ('.meta', 'RANGE meta'),
        ('.system', 'RANGE system'),
        ('.liveness', 'RANGE liveness'),
    ]
    for zone, db_entity in zone_database_entities:
        _set_replicas_for_zone(zone=zone, db_entity=db_entity)


def get_expected_master_node_count() -> int:
    """Identify and return the expected number of DC/OS master nodes."""

    # This is the expanded DC/OS configuration JSON document w/o sensitive
    # values. Read it, parse it.
    dcos_cfg_path = '/opt/mesosphere/etc/expanded.config.json'
    with open(dcos_cfg_path, 'rb') as f:
        dcos_config = json.loads(f.read().decode('utf-8'))

    # If the master discovery strategy is dynamic, the num_masters
    # configuration item is required to specify the expected number of masters.
    # If the master discovery strategy is static, the num_masters configuration
    # item is auto-populated from the given master_list. As such, we rely on
    # num_masters regardless of master discovery strategy.
    log.info("Get master node count from dcos_config['num_masters']")
    return int(dcos_config['num_masters'])


def main() -> None:
    # Determine the internal IP address of this node.
    my_internal_ip = utils.detect_ip()
    log.info('My internal IP address is `{}`'.format(my_internal_ip))

    master_node_count = get_expected_master_node_count()
    log.info('Expected number of DC/OS master nodes: %s', master_node_count)

    set_num_replicas(my_internal_ip, master_node_count)


if __name__ == '__main__':
    main()
