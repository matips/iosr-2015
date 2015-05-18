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

from oslo_config import cfg
from oslo_log import log as logging

from sahara import conductor
from sahara.i18n import _
from sahara.plugins import exceptions as ex
from sahara.plugins import provisioning as p
from sahara.plugins.spark_mesos import edp_engine
from sahara.plugins.spark_mesos import node_starter as ns
from sahara.plugins.spark_mesos import scaling as sc
from sahara.plugins import utils

conductor = conductor.API
LOG = logging.getLogger(__name__)
CONF = cfg.CONF


class SparkProvider(p.ProvisioningPluginBase):
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
        pass

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
            'master': [5050, 80, 443],
            'slave': [80, 443]
        }

        ports = []
        for process in node_group.node_processes:
            if process in ports_map:
                ports.extend(ports_map[process])

        return ports
