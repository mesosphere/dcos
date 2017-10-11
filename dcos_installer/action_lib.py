import asyncio
import json
import logging
import os
from typing import Optional

import pkgpanda
import ssh.utils
from dcos_installer.constants import BOOTSTRAP_DIR, CHECK_RUNNER_CMD, CLUSTER_PACKAGES_PATH, SERVE_DIR, SSH_KEY_PATH
from ssh.runner import Node


REMOTE_TEMP_DIR = '/opt/dcos_install_tmp'

log = logging.getLogger(__name__)


def get_async_runner(config, hosts, async_delegate=None):
    # TODO(cmaloney): Delete these repeats. Use gen / expanded configuration to get all the values.
    process_timeout = config.hacky_default_get('process_timeout', 120)
    extra_ssh_options = config.hacky_default_get('extra_ssh_options', '')
    ssh_key_path = config.hacky_default_get('ssh_key_path', SSH_KEY_PATH)

    # if ssh_parallelism is not set, use 20 concurrent ssh sessions by default.
    parallelism = config.hacky_default_get('ssh_parallelism', 20)

    return ssh.runner.MultiRunner(
        hosts,
        user=config['ssh_user'],
        key_path=ssh_key_path,
        process_timeout=process_timeout,
        extra_opts=extra_ssh_options,
        async_delegate=async_delegate,
        parallelism=parallelism,
        default_port=int(config.hacky_default_get('ssh_port', 22)))


def add_pre_action(chain, ssh_user):
    # Do setup steps for a chain
    chain.add_execute(['sudo', 'mkdir', '-p', REMOTE_TEMP_DIR], stage='Creating temp directory')
    chain.add_execute(['sudo', 'chown', ssh_user, REMOTE_TEMP_DIR],
                      stage='Ensuring {} owns temporary directory'.format(ssh_user))


def add_post_action(chain):
    # Do cleanup steps for a chain
    chain.add_execute(['sudo', 'rm', '-rf', REMOTE_TEMP_DIR],
                      stage='Cleaning up temporary directory')


class ExecuteException(Exception):
    """Raised when execution fails"""


def nodes_count_by_type(config):
    total_agents_count = len(config.hacky_default_get('agent_list', [])) + \
        len(config.hacky_default_get('public_agent_list', []))
    return {
        'total_masters': len(config['master_list']),
        'total_agents': total_agents_count
    }


def get_full_nodes_list(config):
    def add_nodes(nodes, tag):
        return [Node(node, tag, default_port=int(config.hacky_default_get('ssh_port', 22)))
                for node in nodes]

    node_role_map = {
        'master_list': 'master',
        'agent_list': 'agent',
        'public_agent_list': 'public_agent'
    }
    full_target_list = []
    for config_field, role in node_role_map.items():
        if config_field in config:
            full_target_list += add_nodes(config[config_field], {'role': role})
    log.debug("full_target_list: {}".format(full_target_list))
    return full_target_list


@asyncio.coroutine
def run_preflight(config, pf_script_path=(SERVE_DIR + '/dcos_install.sh'), block=False, state_json_dir=None,
                  async_delegate=None, retry=False, options=None):
    '''
    Copies preflight.sh to target hosts and executes the script. Gathers
    stdout, sterr and return codes and logs them to disk via SSH library.
    :param config: Dict, loaded config file from genconf/config.yaml
    :param pf_script_path: preflight.sh script location on a local host
    :param preflight_remote_path: destination location
    '''
    if not os.path.isfile(pf_script_path):
        log.error("{} does not exist. Please run --genconf before executing preflight.".format(pf_script_path))
        raise FileNotFoundError('{} does not exist'.format(pf_script_path))
    targets = get_full_nodes_list(config)

    pf = get_async_runner(config, targets, async_delegate=async_delegate)
    chains = []

    preflight_chain = ssh.utils.CommandChain('preflight')
    # In web mode run if no --offline flag used.
    if options.action == 'web':
        if options.offline:
            log.debug('Offline mode used. Do not install prerequisites on CentOS7, RHEL7 in web mode')
        else:
            _add_prereqs_script(preflight_chain)

    add_pre_action(preflight_chain, pf.user)
    preflight_chain.add_copy(pf_script_path, REMOTE_TEMP_DIR, stage='Copying preflight script')

    preflight_chain.add_execute(
        'sudo bash {} --preflight-only master'.format(
            os.path.join(REMOTE_TEMP_DIR, os.path.basename(pf_script_path))).split(),
        stage='Executing preflight check')
    chains.append(preflight_chain)

    # Setup the cleanup chain
    cleanup_chain = ssh.utils.CommandChain('preflight_cleanup')
    add_post_action(cleanup_chain)
    chains.append(cleanup_chain)
    result = yield from pf.run_commands_chain_async(chains, block=block, state_json_dir=state_json_dir,
                                                    delegate_extra_params=nodes_count_by_type(config))
    return result


def _add_copy_dcos_install(chain, local_install_path=SERVE_DIR):
    dcos_install_script = 'dcos_install.sh'
    local_install_path = os.path.join(local_install_path, dcos_install_script)
    remote_install_path = os.path.join(REMOTE_TEMP_DIR, dcos_install_script)
    chain.add_copy(local_install_path, remote_install_path, stage='Copying dcos_install.sh')


def _add_copy_packages(chain, local_pkg_base_path=SERVE_DIR):
    if not os.path.isfile(CLUSTER_PACKAGES_PATH):
        err_msg = '{} not found'.format(CLUSTER_PACKAGES_PATH)
        log.error(err_msg)
        raise ExecuteException(err_msg)

    cluster_packages = pkgpanda.load_json(CLUSTER_PACKAGES_PATH)
    for package, params in cluster_packages.items():
        destination_package_dir = os.path.join(REMOTE_TEMP_DIR, 'packages', package)
        local_pkg_path = os.path.join(local_pkg_base_path, params['filename'])

        chain.add_execute(['mkdir', '-p', destination_package_dir], stage='Creating package directory')
        chain.add_copy(local_pkg_path, destination_package_dir,
                       stage='Copying packages')


def _add_copy_bootstap(chain, local_bs_path):
    remote_bs_path = REMOTE_TEMP_DIR + '/bootstrap'
    chain.add_execute(['mkdir', '-p', remote_bs_path], stage='Creating directory')
    chain.add_copy(local_bs_path, remote_bs_path,
                   stage='Copying bootstrap')


def _get_bootstrap_tarball(tarball_base_dir=BOOTSTRAP_DIR):
    '''
    Get a bootstrap tarball from a local filesystem
    :return: String, location of a tarball
    '''
    if 'BOOTSTRAP_ID' not in os.environ:
        err_msg = 'BOOTSTRAP_ID must be set'
        log.error(err_msg)
        raise ExecuteException(err_msg)

    tarball = os.path.join(tarball_base_dir, '{}.bootstrap.tar.xz'.format(os.environ['BOOTSTRAP_ID']))
    if not os.path.isfile(tarball):
        log.error('Ensure environment variable BOOTSTRAP_ID is set correctly')
        log.error('Ensure that the bootstrap tarball exists in '
                  '{}/[BOOTSTRAP_ID].bootstrap.tar.xz'.format(tarball_base_dir))
        log.error('You must run genconf.py before attempting Deploy.')
        raise ExecuteException('bootstrap tarball not found in {}'.format(tarball_base_dir))
    return tarball


def _read_state_file(state_file):
    if not os.path.isfile(state_file):
        return {}

    with open(state_file) as fh:
        return json.load(fh)


def _remove_host(state_file, host):

    json_state = _read_state_file(state_file)

    if 'hosts' not in json_state or host not in json_state['hosts']:
        return False

    log.debug('removing host {} from {}'.format(host, state_file))
    try:
        del json_state['hosts'][host]
    except KeyError:
        return False

    with open(state_file, 'w') as fh:
        json.dump(json_state, fh)

    return True


@asyncio.coroutine
def install_dcos(
        config,
        block=False,
        state_json_dir=None,
        hosts: Optional[list]=None,
        async_delegate=None,
        try_remove_stale_dcos=False,
        **kwargs):
    if hosts is None:
        hosts = []

    # Role specific parameters
    role_params = {
        'master': {
            'tags': {'role': 'master', 'dcos_install_param': 'master'},
            'hosts': config['master_list']
        },
        'agent': {
            'tags': {'role': 'agent', 'dcos_install_param': 'slave'},
            'hosts': config.hacky_default_get('agent_list', [])
        },
        'public_agent': {
            'tags': {'role': 'public_agent', 'dcos_install_param': 'slave_public'},
            'hosts': config.hacky_default_get('public_agent_list', [])
        }
    }

    bootstrap_tarball = _get_bootstrap_tarball()
    log.debug("Local bootstrap found: %s", bootstrap_tarball)

    targets = []
    if hosts:
        targets = hosts
    else:
        for role, params in role_params.items():
            targets += [Node(node, params['tags'], default_port=int(config.hacky_default_get('ssh_port', 22)))
                        for node in params['hosts']]

    runner = get_async_runner(config, targets, async_delegate=async_delegate)
    chains = []
    if try_remove_stale_dcos:
        pkgpanda_uninstall_chain = ssh.utils.CommandChain('remove_stale_dcos')
        pkgpanda_uninstall_chain.add_execute(['sudo', '-i', '/opt/mesosphere/bin/pkgpanda', 'uninstall'],
                                             stage='Trying pkgpanda uninstall')
        chains.append(pkgpanda_uninstall_chain)

        remove_dcos_chain = ssh.utils.CommandChain('remove_stale_dcos')
        remove_dcos_chain.add_execute(['rm', '-rf', '/opt/mesosphere', '/etc/mesosphere'],
                                      stage="Removing DC/OS files")
        chains.append(remove_dcos_chain)

    chain = ssh.utils.CommandChain('deploy')
    chains.append(chain)

    add_pre_action(chain, runner.user)
    _add_copy_dcos_install(chain)
    _add_copy_packages(chain)
    _add_copy_bootstap(chain, bootstrap_tarball)

    chain.add_execute(
        lambda node: (
            'sudo bash {}/dcos_install.sh {}'.format(REMOTE_TEMP_DIR, node.tags['dcos_install_param'])).split(),
        stage=lambda node: 'Installing DC/OS'
    )

    # UI expects total_masters, total_agents to be top level keys in deploy.json
    delegate_extra_params = nodes_count_by_type(config)
    if kwargs.get('retry') and state_json_dir:
        state_file_path = os.path.join(state_json_dir, 'deploy.json')
        log.debug('retry executed for a state file deploy.json')
        for _host in hosts:
            _remove_host(state_file_path, '{}:{}'.format(_host.ip, _host.port))

        # We also need to update total number of hosts
        json_state = _read_state_file(state_file_path)
        delegate_extra_params['total_hosts'] = json_state['total_hosts']

    # Setup the cleanup chain
    cleanup_chain = ssh.utils.CommandChain('deploy_cleanup')
    add_post_action(cleanup_chain)
    chains.append(cleanup_chain)

    result = yield from runner.run_commands_chain_async(chains, block=block, state_json_dir=state_json_dir,
                                                        delegate_extra_params=delegate_extra_params)
    return result


@asyncio.coroutine
def run_postflight(config, block=False, state_json_dir=None, async_delegate=None, retry=False, options=None):
    targets = get_full_nodes_list(config)
    node_runner = get_async_runner(config, targets, async_delegate=async_delegate)
    cluster_runner = get_async_runner(config, [targets[0]], async_delegate=async_delegate)

    # Run the check script for up to 15 minutes (900 seconds) to ensure we do not return failure on a cluster
    # that is still booting.
    check_script_template = """
T=900
until OUT=$(sudo /opt/mesosphere/bin/dcos-shell {check_cmd} {check_type}) || [[ T -eq 0 ]]; do
    sleep 1
    let T=T-1
done
RETCODE=$?
echo $OUT
exit $RETCODE"""
    node_check_script = check_script_template.format(
        check_cmd=CHECK_RUNNER_CMD,
        check_type='node-poststart')
    cluster_check_script = check_script_template.format(
        check_cmd=CHECK_RUNNER_CMD,
        check_type='cluster')

    node_postflight_chain = ssh.utils.CommandChain('postflight')
    node_postflight_chain.add_execute(
        [node_check_script],
        stage='Executing node postflight checks')

    node_cleanup_chain = ssh.utils.CommandChain('postflight_cleanup')
    node_cleanup_chain.add_execute(
        ['sudo', 'rm', '-f', '/opt/dcos-prereqs.installed'],
        stage='Removing prerequisites flag')

    cluster_postflight_chain = ssh.utils.CommandChain('cluster_postflight')
    cluster_postflight_chain.add_execute(
        [cluster_check_script],
        stage='Executing cluster postflight checks')

    node_check_result = yield from node_runner.run_commands_chain_async(
        [node_postflight_chain, node_cleanup_chain],
        block=block,
        state_json_dir=state_json_dir,
        delegate_extra_params=nodes_count_by_type(config))

    cluster_check_result = yield from cluster_runner.run_commands_chain_async(
        [cluster_postflight_chain],
        block=block,
        state_json_dir=state_json_dir)

    if block:
        result = node_check_result + cluster_check_result
    else:
        result = None
    return result


# TODO: DCOS-250 (skumaran@mesosphere.com)- Create an comprehensive DC/OS uninstall strategy.
# This routine is currently unused and unexposed.
@asyncio.coroutine
def uninstall_dcos(config, block=False, state_json_dir=None, async_delegate=None, options=None):
    targets = get_full_nodes_list(config)

    # clean the file to all targets
    runner = get_async_runner(config, targets, async_delegate=async_delegate)
    uninstall_chain = ssh.utils.CommandChain('uninstall')

    uninstall_chain.add_execute([
        'sudo',
        '-i',
        '/opt/mesosphere/bin/pkgpanda',
        'uninstall',
        '&&',
        'sudo',
        'rm',
        '-rf',
        '/opt/mesosphere/'], stage='Uninstalling DC/OS')
    result = yield from runner.run_commands_chain_async([uninstall_chain], block=block, state_json_dir=state_json_dir)

    return result


def _add_prereqs_script(chain):
    inline_script = """
#!/usr/bin/env bash

# Exit on error, unset variable, or error in pipe chain
set -o errexit -o nounset -o pipefail

if [[ -f /opt/dcos-prereqs.installed ]]; then
  echo "install_prereqs has been already executed on this host, exiting..."
  exit 0
fi

echo "Validating distro..."
distro=$(cat /etc/os-release | sed -n 's/^ID="\(.*\)"$/\1/p')
if [[ "${distro}" == 'coreos' ]]; then
  echo "Distro: CoreOS"
  echo "CoreOS includes all prerequisites by default." >&2
  exit 0
elif [[ "${distro}" == 'rhel' ]]; then
  echo "Distro: RHEL"
elif [[ "${distro}" == 'centos' ]]; then
  echo "Distro: CentOS"
else
  echo "Distro: ${distro}"
  echo "Error: Distro ${distro} is not supported. Only CoreOS, RHEL, and CentOS are supported." >&2
  exit 1
fi

echo "Validating distro version..."
# CentOS & RHEL < 7 have inconsistent release file locations
distro_major_version=$(cat /etc/*elease | sed -n 's/^VERSION_ID="\([0-9][0-9]*\).*"$/\1/p')
if [[ ${distro_major_version} -lt 7 ]]; then
  echo "Error: Distro version ${distro_major_version} is not supported. Only >= 7 is supported." >&2
  exit 1
fi
# CentOS & RHEL >= 7 both have the full version in /etc/redhat-release
distro_minor_version="$(cat /etc/redhat-release | sed -e 's/[^0-9]*[0-9][0-9]*\.\([0-9][0-9]*\).*/\1/')"
if [[ ${distro_minor_version} -lt 2 ]]; then
  echo "Error: Distro version ${distro_minor_version} is not supported. Only >= 7.2 is supported." >&2
  exit 1
fi

echo "Validating kernel version..."
kernel_major_version="$(uname -r | sed -e 's/\([0-9][0-9]*\).*/\1/')"
kernel_minor_version="$(uname -r | sed -e "s/${kernel_major_version}\.\([0-9][0-9]*\).*/\1/")"
if [[ ${kernel_major_version} -lt 3 || ${kernel_minor_version} -lt 10 ]]; then
  echo "Error: Kernel version ${kernel_major_version}.${kernel_minor_version} is not supported. Only >= 3.10 is supported." >&2
  exit 1
fi

echo "Validating kernel modules..."
if ! lsmod | grep -q overlay; then
  echo "Enabling OverlayFS kernel module..."
  # Enable now
  sudo modprobe overlay
  # Load on reboot via systemd
  sudo tee /etc/modules-load.d/overlay.conf <<-'EOF'
overlay
EOF
fi

echo "Validating file system..."
sudo mkdir -p /var/lib/docker
file_system="$(df --output=fstype /var/lib/docker | tail -1)"
echo "File System: ${file_system}"
if [[ "${file_system}" != 'xfs' ]] || ! xfs_info /var/lib/docker | grep -q 'ftype=1'; then
  echo "Error: /var/lib/docker must use XFS provisioned with ftype=1 to avoid known issues with OverlayFS." >&2
  exit 1
fi

echo "Installing Utilities..."
sudo yum install -y wget
sudo yum install -y curl
sudo yum install -y git
sudo yum install -y unzip
sudo yum install -y xz
sudo yum install -y ipset

echo "Disabling SELinux..."
sudo /usr/sbin/setenforce 0
sudo sed -i --follow-symlinks 's/^SELINUX=.*/SELINUX=disabled/g' /etc/sysconfig/selinux

echo "Detecting Docker..."
install_docker='true'
if hash docker 2>/dev/null; then
  docker_client_version="$(docker --version | sed -e 's/Docker version \(.*\),.*/\1/')"
  echo "Docker Client Version: ${docker_client_version}"

  if ! docker info &>/dev/null; then
    echo "Docker Server not found. Please uninstall Docker and try again." >&2
    exit 1
  fi

  docker_server_version="$(docker info | grep 'Server Version:' | sed -e 's/Server Version: \(.*\)/\1/')"
  echo "Docker Server Version: ${docker_server_version}"

  if [[ "${docker_client_version}" != "${docker_server_version}" ]]; then
    echo "Docker Server and Client versions do not match. Please uninstall Docker and try again." >&2
    exit 1
  fi

  if echo "${docker_server_version}" | grep -q '\-ce'; then
    echo "Docker Community Edition not yet supported. Please uninstall Docker and try again." >&2
    exit 1
  fi

  if echo "${docker_server_version}" | grep -q '\-ee'; then
    echo "Docker Enterprise Edition not yet supported. Please uninstall Docker and try again." >&2
    exit 1
  fi

  docker_major_version="$(echo "${docker_server_version}" | sed -e 's/\([0-9][0-9]*\.[0-9][0-9]*\).*/\1/')"
  if ! [[ "${docker_major_version}" == '1.11' ||
          "${docker_major_version}" == '1.12' ||
          "${docker_major_version}" == '1.13' ]]; then
    echo "Docker "${docker_server_version}" not supported. Please uninstall Docker and try again." >&2
    exit 1
  fi

  install_docker='false'
fi

if [[ "${install_docker}" == 'true' ]]; then
  echo "Installing Docker 1.13.1..."

  # Add Docker Yum Repo
  sudo tee /etc/yum.repos.d/docker.repo <<-'EOF'
[dockerrepo]
name=Docker Repository
baseurl=https://yum.dockerproject.org/repo/main/centos/7
enabled=1
gpgcheck=1
gpgkey=https://yum.dockerproject.org/gpg
EOF

  # Add Docker systemd service
  sudo mkdir -p /etc/systemd/system/docker.service.d
  sudo tee /etc/systemd/system/docker.service.d/override.conf <<- EOF
[Service]
Restart=always
StartLimitInterval=0
RestartSec=15
ExecStartPre=-/sbin/ip link del docker0
ExecStart=
ExecStart=/usr/bin/dockerd --storage-driver=overlay
EOF

  # Install and enable Docker
  sudo yum install -y docker-engine-17.05.0.ce docker-engine-selinux-17.05.0.ce
  sudo systemctl start docker
  sudo systemctl enable docker
fi

if ! sudo getent group nogroup >/dev/null; then
  echo "Creating 'nogroup' group..."
  sudo groupadd nogroup
fi

sudo touch /opt/dcos-prereqs.installed
echo "Prerequisites successfully installed."
"""
    # Run a first command to get json file generated.
    chain.add_execute(['echo', 'INSTALL', 'PREREQUISITES'], stage="Installing prerequisites")
    chain.add_execute([inline_script], stage='Installing preflight prerequisites')


@asyncio.coroutine
def install_prereqs(config, block=False, state_json_dir=None, async_delegate=None, options=None):
    targets = get_full_nodes_list(config)
    runner = get_async_runner(config, targets, async_delegate=async_delegate)
    prereqs_chain = ssh.utils.CommandChain('install_prereqs')
    _add_prereqs_script(prereqs_chain)
    result = yield from runner.run_commands_chain_async([prereqs_chain], block=block, state_json_dir=state_json_dir)
    return result
