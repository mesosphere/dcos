from dcos_e2e.base_classes import ClusterBackend
from dcos_e2e.cluster import Cluster
from dcos_e2e.node import DCOSVariant, Output


class TestUpgradeTests:
    """
    Tests for DC/OS upgrade.
    """

    def test_upgrade_from_url(
        self,
        docker_backend: ClusterBackend,
        artifact_url: str,
        upgrade_artifact_url: str,
    ) -> None:
        """
        DC/OS OSS can be upgraded from artifact_url to upgrade_artifact_url.
        """
        with Cluster(cluster_backend=docker_backend) as cluster:
            cluster.install_dcos_from_url(
                dcos_installer=artifact_url,
                dcos_config=cluster.base_config,
                ip_detect_path=docker_backend.ip_detect_path,
                output=Output.LOG_AND_CAPTURE,
            )
            cluster.wait_for_dcos_oss()

            for node in {
                *cluster.masters,
                *cluster.agents,
                *cluster.public_agents,
            }:
                build = node.dcos_build_info()
                assert build.version.startswith(artifact_url.split('/')[-2])
                # assert build.version.startswith('1.12')
                assert build.variant == DCOSVariant.OSS

            cluster.upgrade_dcos_from_url(
                dcos_installer=upgrade_artifact_url,
                dcos_config=cluster.base_config,
                ip_detect_path=docker_backend.ip_detect_path,
                output=Output.LOG_AND_CAPTURE,
            )

            cluster.wait_for_dcos_oss()
            for node in {
                *cluster.masters,
                *cluster.agents,
                *cluster.public_agents,
            }:
                build = node.dcos_build_info()
                assert build.version.startswith(upgrade_artifact_url.split('/')[-2])
                # assert build.version.startswith('1.13')
                assert build.variant == DCOSVariant.OSS
