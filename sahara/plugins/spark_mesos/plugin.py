# Copyright (c) 2014 Hoang Do, Phuc Vo, P. Michiardi, D. Venzano
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import os

from oslo_config import cfg
from oslo_log import log as logging

from sahara import context
from sahara import conductor
from sahara.i18n import _
from sahara.plugins import exceptions as ex
from sahara.plugins import provisioning as p
from sahara.plugins.spark_mesos import edp_engine
from sahara.plugins.spark_mesos import node_starter as ns
from sahara.plugins.spark_mesos import config_helper as c_helper
from sahara.plugins.spark_mesos import scaling as sc
from sahara.plugins import utils
from sahara.topology import topology_helper as th
from sahara.utils import files as f
from sahara.utils import remote
from sahara.utils import cluster_progress_ops as cpo

conductor = conductor.API
LOG = logging.getLogger(__name__)
CONF = cfg.CONF


class SparkMesosProvider(p.ProvisioningPluginBase):
    def __init__(self):
        self.processes = {
            "HDFS": ["namenode", "datanode"],
            "Spark": ["master", "slave"]
        }

    def get_title(self):
        return "Apache Spark + Mesos"

    def get_description(self):
        return _("This plugin provides an ability to launch Spark on Hadoop "
                 "CDH cluster, managed by Mesos.")

    def get_versions(self):
        return ['1.0.0']

    def get_configs(self, hadoop_version):
        return []

    def get_node_processes(self, hadoop_version):
        return self.processes

    def validate(self, cluster):
        nn_count = utils.get_instances_count(cluster, "namenode")
        if nn_count != 1:
            raise ex.InvalidComponentCountException("namenode", 1, nn_count)

        dn_count = utils.get_instances_count(cluster, "datanode")
        if dn_count < 1:
            raise ex.InvalidComponentCountException("datanode", _("1 or more"),
                                                    dn_count)

        # validate Spark Master Node and Spark Slaves
        sm_count = utils.get_instances_count(cluster, "master")
        if sm_count != 1:
            raise ex.InvalidComponentCountException("Spark master", 1, sm_count)

        sl_count = utils.get_instances_count(cluster, "slave")
        if sl_count < 1:
            raise ex.InvalidComponentCountException("Spark slave",
                                                    _("1 or more"),
                                                    sl_count)

    def update_infra(self, cluster):
        pass

    def configure_cluster(self, cluster):
        extra = self._extract_configs_to_extra(cluster)
        all_instances = utils.get_instances(cluster)
        cpo.add_provisioning_step(
            cluster.id, _("Push configs to nodes"), len(all_instances))

        with context.ThreadGroup() as tg:
            for instance in all_instances:
                tg.spawn('spark-configure-%s' % instance.instance_name,
                         self._push_configs_to_new_node, cluster,
                         extra, instance)

    @cpo.event_wrapper(mark_successful_on_exit=True)
    def _push_configs_to_new_node(self, cluster, extra, instance):
        ng_extra = extra[instance.node_group.id]

        files_hadoop = {
            os.path.join(c_helper.HADOOP_CONF_DIR,
                         "core-site.xml"): ng_extra['xml']['core-site'],
            os.path.join(c_helper.HADOOP_CONF_DIR,
                         "hdfs-site.xml"): ng_extra['xml']['hdfs-site']}

        files_init = {
            '/tmp/sahara-hadoop-init.sh': ng_extra['setup_script'],
            'id_rsa': cluster.management_private_key,
            'authorized_keys': cluster.management_public_key}

        # pietro: This is required because the (secret) key is not stored in
        # .ssh which hinders password-less ssh required by spark scripts
        key_cmd = ('sudo cp $HOME/id_rsa $HOME/.ssh/; '
                   'sudo chown $USER $HOME/.ssh/id_rsa; '
                   'sudo chmod 600 $HOME/.ssh/id_rsa')

        storage_paths = instance.node_group.storage_paths()
        dn_path = ' '.join(c_helper.make_hadoop_path(storage_paths, '/dfs/dn'))
        nn_path = ' '.join(c_helper.make_hadoop_path(storage_paths, '/dfs/nn'))

        hdfs_dir_cmd = ('sudo mkdir -p %(nn_path)s %(dn_path)s &&'
                        'sudo chown -R hdfs:hadoop %(nn_path)s %(dn_path)s &&'
                        'sudo chmod 755 %(nn_path)s %(dn_path)s' %
                        {"nn_path": nn_path, "dn_path": dn_path})

        with remote.get_remote(instance) as r:
            r.execute_command('sudo chown -R $USER:$USER /etc/hadoop')
            r.write_files_to(files_hadoop)
            r.write_files_to(files_init)
            r.execute_command('sudo chmod 0500 /tmp/sahara-hadoop-init.sh')
            r.execute_command(
                'sudo /tmp/sahara-hadoop-init.sh '
                '>> /tmp/sahara-hadoop-init.log 2>&1')

            r.execute_command(hdfs_dir_cmd)
            r.execute_command(key_cmd)

            if c_helper.is_data_locality_enabled(cluster):
                r.write_file_to(
                    '/etc/hadoop/topology.sh',
                    f.get_file_text('plugins/spark/resources/topology.sh'))
                r.execute_command('sudo chmod +x /etc/hadoop/topology.sh')

            self._write_topology_data(r, cluster, extra)
            self._push_master_configs(r, cluster, instance)
            self._push_cleanup_job(r, extra, instance)

    @staticmethod
    def _write_topology_data(r, cluster, extra):
        if c_helper.is_data_locality_enabled(cluster):
            topology_data = extra['topology_data']
            r.write_file_to('/etc/hadoop/topology.data', topology_data)

    def _push_master_configs(self, r, cluster, instance):
        node_processes = instance.node_group.node_processes
        if 'namenode' in node_processes:
            self._push_namenode_configs(cluster, r)

    @staticmethod
    def _push_cleanup_job(r, extra, instance):
        node_processes = instance.node_group.node_processes
        if 'master' in node_processes:
            if extra['job_cleanup']['valid']:
                r.write_file_to('/etc/hadoop/tmp-cleanup.sh',
                                extra['job_cleanup']['script'])
                r.execute_command("chmod 755 /etc/hadoop/tmp-cleanup.sh")
            else:
                r.execute_command("sudo rm -f /etc/hadoop/tmp-cleanup.sh")

    @staticmethod
    def _push_namenode_configs(cluster, r):
        r.write_file_to('/etc/hadoop/dn.incl',
                        utils.generate_fqdn_host_names(
                            utils.get_instances(cluster, "datanode")))
        r.write_file_to('/etc/hadoop/dn.excl', '')

    @staticmethod
    def _extract_configs_to_extra(cluster):
        nn = utils.get_instance(cluster, "namenode")

        extra = dict()

        extra['job_cleanup'] = c_helper.generate_job_cleanup_config(cluster)
        for ng in cluster.node_groups:
            extra[ng.id] = {
                'xml': c_helper.generate_xml_configs(
                    ng.configuration(),
                    ng.storage_paths(),
                    nn.hostname(), None
                ),
                'setup_script': c_helper.generate_hadoop_setup_script(
                    ng.storage_paths(),
                    c_helper.extract_hadoop_environment_confs(
                        ng.configuration())
                )
            }

        if c_helper.is_data_locality_enabled(cluster):
            topology_data = th.generate_topology_map(
                cluster, CONF.enable_hypervisor_awareness)
            extra['topology_data'] = "\n".join(
                [k + " " + v for k, v in topology_data.items()]) + "\n"

        return extra

    def start_cluster(self, cluster):
        ns.start_namenode(cluster)
        ns.start_datanodes(cluster)
        ns.prepare_namenode_dirs(cluster)
        ns.start_master(cluster)
        ns.start_slaves(cluster)

    def validate_scaling(self, cluster, existing, additional):
        raise ex.ClusterCannotBeScaled(cluster.name, _("Not Yet Implemented"))

    def decommission_nodes(self, cluster, instances):
        slaves = filter(lambda inst: 'slave' in inst.node_group.node_processes,
                        instances)

        ns.stop_slaves(list(slaves), cluster)

        dns = utils.get_instances(cluster, "datanode")
        decommission_dns = False

        for i in instances:
            if 'datanode' in i.node_group.node_processes:
                dns.remove(i)
                decommission_dns = True

        nn = utils.get_instance(cluster, "namenode")
        if decommission_dns:
            sc.decommission_dn(nn, instances, dns)

    def scale_cluster(self, cluster, instances):
        pass

    def get_edp_engine(self, cluster, job_type):
        if job_type in edp_engine.EdpEngine.get_supported_job_types():
            return edp_engine.EdpEngine(cluster)

        return None

    def get_edp_job_types(self, versions=None):
        res = {}
        for vers in self.get_versions():
            if not versions or vers in versions:
                if edp_engine.EdpEngine.edp_supported(vers):
                    res[vers] = edp_engine.EdpEngine.get_supported_job_types()
        return res

    def get_edp_config_hints(self, job_type, version):
        if edp_engine.EdpEngine.edp_supported(version):
            return edp_engine.EdpEngine.get_possible_job_config(job_type)
        return {}

    def get_open_ports(self, node_group):
        ports_map = {
            'namenode': [8020, 50070, 50470],
            'datanode': [50010, 1004, 50075, 1006, 50020],
            'master': [5050],
            'slave': []
        }

        ports = []
        for process in node_group.node_processes:
            if process in ports_map:
                ports.extend(ports_map[process])

        return ports
