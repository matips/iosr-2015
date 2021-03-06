# Copyright (c) 2014 Hoang Do, Phuc Vo, P. Michiardi, D. Venzano
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from oslo_config import cfg
from oslo_log import log as logging
import six

from sahara import conductor as c
from sahara.i18n import _
from sahara.plugins import provisioning as p
from sahara.plugins import utils
from sahara.swift import swift_helper as swift
from sahara.topology import topology_helper as topology
from sahara.utils import files as f
from sahara.utils import types as types
from sahara.utils import xmlutils as x


conductor = c.API
LOG = logging.getLogger(__name__)
CONF = cfg.CONF

CORE_DEFAULT = x.load_hadoop_xml_defaults(
    'plugins/spark/resources/core-default.xml')

HDFS_DEFAULT = x.load_hadoop_xml_defaults(
    'plugins/spark/resources/hdfs-default.xml')

SWIFT_DEFAULTS = swift.read_default_swift_configs()

XML_CONFS = {
    "HDFS": [CORE_DEFAULT, HDFS_DEFAULT, SWIFT_DEFAULTS]
}

_default_executor_classpath = ":".join(
    ['/usr/lib/hadoop/lib/jackson-core-asl-1.8.8.jar',
     '/usr/lib/hadoop/hadoop-swift.jar'])


HADOOP_CONF_DIR = "/opt/hadoop/etc/hadoop"

ENV_CONFS = {
    "HDFS": {
        'Name Node Heap Size': 'HADOOP_NAMENODE_OPTS=\\"-Xmx%sm\\"',
        'Data Node Heap Size': 'HADOOP_DATANODE_OPTS=\\"-Xmx%sm\\"'
    }
}

ENABLE_DATA_LOCALITY = p.Config('Enable Data Locality', 'general', 'cluster',
                                config_type="bool", priority=1,
                                default_value=True, is_optional=True)

ENABLE_SWIFT = p.Config('Enable Swift', 'general', 'cluster',
                        config_type="bool", priority=1,
                        default_value=True, is_optional=False)

# Default set to 1 day, which is the default Keystone token
# expiration time. After the token is expired we can't continue
# scaling anyway.
DECOMMISSIONING_TIMEOUT = p.Config('Decommissioning Timeout', 'general',
                                   'cluster', config_type='int', priority=1,
                                   default_value=86400, is_optional=True,
                                   description='Timeout for datanode'
                                               ' decommissioning operation'
                                               ' during scaling, in seconds')

HIDDEN_CONFS = ['fs.defaultFS', 'dfs.namenode.name.dir',
                'dfs.datanode.data.dir']

CLUSTER_WIDE_CONFS = ['dfs.block.size', 'dfs.permissions', 'dfs.replication',
                      'dfs.replication.min', 'dfs.replication.max',
                      'io.file.buffer.size']

PRIORITY_1_CONFS = ['dfs.datanode.du.reserved',
                    'dfs.datanode.failed.volumes.tolerated',
                    'dfs.datanode.max.xcievers', 'dfs.datanode.handler.count',
                    'dfs.namenode.handler.count']

# for now we have not so many cluster-wide configs
# lets consider all of them having high priority
PRIORITY_1_CONFS += CLUSTER_WIDE_CONFS


def _initialise_configs():
    configs = []
    for service, config_lists in XML_CONFS.iteritems():
        for config_list in config_lists:
            for config in config_list:
                if config['name'] not in HIDDEN_CONFS:
                    cfg = p.Config(config['name'], service, "node",
                                   is_optional=True, config_type="string",
                                   default_value=str(config['value']),
                                   description=config['description'])
                    if cfg.default_value in ["true", "false"]:
                        cfg.config_type = "bool"
                        cfg.default_value = (cfg.default_value == 'true')
                    elif types.is_int(cfg.default_value):
                        cfg.config_type = "int"
                        cfg.default_value = int(cfg.default_value)
                    if config['name'] in CLUSTER_WIDE_CONFS:
                        cfg.scope = 'cluster'
                    if config['name'] in PRIORITY_1_CONFS:
                        cfg.priority = 1
                    configs.append(cfg)

    for service, config_items in ENV_CONFS.iteritems():
        for name, param_format_str in config_items.iteritems():
            configs.append(p.Config(name, service, "node",
                                    default_value=1024, priority=1,
                                    config_type="int"))

    configs.append(DECOMMISSIONING_TIMEOUT)
    configs.append(ENABLE_SWIFT)
    if CONF.enable_data_locality:
        configs.append(ENABLE_DATA_LOCALITY)

    return configs

# Initialise plugin Hadoop configurations
PLUGIN_CONFIGS = _initialise_configs()


def get_plugin_configs():
    return PLUGIN_CONFIGS


def get_config_value(service, name, cluster=None):
    if cluster:
        for ng in cluster.node_groups:
            if (ng.configuration().get(service) and
                    ng.configuration()[service].get(name)):
                return ng.configuration()[service][name]

    for configs in PLUGIN_CONFIGS:
        if configs.applicable_target == service and configs.name == name:
            return configs.default_value

    raise RuntimeError(_("Unable to get parameter '%(param_name)s' from "
                         "service %(service)s"),
                       {'param_name': name, 'service': service})


def generate_cfg_from_general(cfg, configs, general_config,
                              rest_excluded=False):
    if 'general' in configs:
        for nm in general_config:
            if nm not in configs['general'] and not rest_excluded:
                configs['general'][nm] = general_config[nm]['default_value']
        for name, value in configs['general'].items():
            if value:
                cfg = _set_config(cfg, general_config, name)
                LOG.debug("Applying config: {name}".format(name=name))
    else:
        cfg = _set_config(cfg, general_config)
    return cfg


def _get_hostname(service):
    return service.hostname() if service else None


def generate_xml_configs(configs, storage_path, nn_hostname, hadoop_port):
    if hadoop_port is None:
        hadoop_port = 8020

    cfg = {
        'fs.defaultFS': 'hdfs://%s:%s' % (nn_hostname, str(hadoop_port)),
        'dfs.namenode.name.dir': extract_hadoop_path(storage_path,
                                                     '/dfs/nn'),
        'dfs.datanode.data.dir': extract_hadoop_path(storage_path,
                                                     '/dfs/dn'),
        'hadoop.tmp.dir': extract_hadoop_path(storage_path,
                                              '/dfs'),
        'dfs.hosts': '/opt/hadoop/etc/hadoop/dn.incl',
        'dfs.hosts.exclude': '/opt/hadoop/etc/hadoop/dn.excl'
    }

    # inserting user-defined configs
    for key, value in extract_hadoop_xml_confs(configs):
        cfg[key] = value

    # Add the swift defaults if they have not been set by the user
    swft_def = []
    if is_swift_enabled(configs):
        swft_def = SWIFT_DEFAULTS
        swift_configs = extract_name_values(swift.get_swift_configs())
        for key, value in six.iteritems(swift_configs):
            if key not in cfg:
                cfg[key] = value

    # invoking applied configs to appropriate xml files
    core_all = CORE_DEFAULT + swft_def

    if CONF.enable_data_locality:
        cfg.update(topology.TOPOLOGY_CONFIG)
        # applying vm awareness configs
        core_all += topology.vm_awareness_core_config()

    xml_configs = {
        'core-site': x.create_hadoop_xml(cfg, core_all),
        'hdfs-site': x.create_hadoop_xml(cfg, HDFS_DEFAULT)
    }

    return xml_configs


def generate_spark_executor_classpath():
    return " --driver-class-path {0}".format(_default_executor_classpath)


def extract_hadoop_environment_confs(configs):
    """Returns environment specific Hadoop configurations.

    :returns list of Hadoop parameters which should be passed via environment
    """

    lst = []
    for service, srv_confs in configs.items():
        if ENV_CONFS.get(service):
            for param_name, param_value in srv_confs.items():
                for cfg_name, cfg_format_str in ENV_CONFS[service].items():
                    if param_name == cfg_name and param_value is not None:
                        lst.append(cfg_format_str % param_value)
    return lst


def extract_hadoop_xml_confs(configs):
    """Returns xml specific Hadoop configurations.

    :returns list of Hadoop parameters which should be passed into general
    configs like core-site.xml
    """

    lst = []
    for service, srv_confs in configs.items():
        if XML_CONFS.get(service):
            for param_name, param_value in srv_confs.items():
                for cfg_list in XML_CONFS[service]:
                    names = [cfg['name'] for cfg in cfg_list]
                    if param_name in names and param_value is not None:
                        lst.append((param_name, param_value))
    return lst


def generate_hadoop_setup_script(storage_paths, env_configs):
    script_lines = ["#!/bin/bash -x"]
    script_lines.append("echo -n > /tmp/hadoop-env.sh")
    for line in env_configs:
        if 'HADOOP' in line:
            script_lines.append('echo "%s" >> /tmp/hadoop-env.sh' % line)
    script_lines.append("cat /opt/hadoop/etc/hadoop/hadoop-env.sh >> /tmp/hadoop-env.sh")
    script_lines.append("cp /tmp/hadoop-env.sh /opt/hadoop/etc/hadoop/hadoop-env.sh")

    hadoop_log = storage_paths[0] + "/log/hadoop/\$USER/"
    script_lines.append('sed -i "s,export HADOOP_LOG_DIR=.*,'
                        'export HADOOP_LOG_DIR=%s," /opt/hadoop/etc/hadoop/hadoop-env.sh'
                        % hadoop_log)

    hadoop_log = storage_paths[0] + "/log/hadoop/hdfs"
    script_lines.append('sed -i "s,export HADOOP_SECURE_DN_LOG_DIR=.*,'
                        'export HADOOP_SECURE_DN_LOG_DIR=%s," '
                        '/opt/hadoop/etc/hadoop/hadoop-env.sh' % hadoop_log)

    for path in storage_paths:
        script_lines.append("chown -R hadoop:hadoop %s" % path)
        script_lines.append("chmod -R 755 %s" % path)
    return "\n".join(script_lines)


def generate_job_cleanup_config(cluster):
    args = {
        'minimum_cleanup_megabytes': 4096,
        'minimum_cleanup_seconds': 86400,
        'maximum_cleanup_seconds': 1209600
    }
    job_conf = {'valid': (args['maximum_cleanup_seconds'] > 0 and
                          (args['minimum_cleanup_megabytes'] > 0
                           and args['minimum_cleanup_seconds'] > 0))}
    if job_conf['valid']:
        job_conf['cron'] = f.get_file_text(
            'plugins/spark/resources/spark-cleanup.cron'),
        job_cleanup_script = f.get_file_text(
            'plugins/spark/resources/tmp-cleanup.sh.template')
        job_conf['script'] = job_cleanup_script.format(**args)
    return job_conf


def extract_name_values(configs):
    return {cfg['name']: cfg['value'] for cfg in configs}


def make_hadoop_path(base_dirs, suffix):
    return [base_dir + suffix for base_dir in base_dirs]


def extract_hadoop_path(lst, hadoop_dir):
    if lst:
        return ",".join(make_hadoop_path(lst, hadoop_dir))


def _set_config(cfg, gen_cfg, name=None):
    if name in gen_cfg:
        cfg.update(gen_cfg[name]['conf'])
    if name is None:
        for name in gen_cfg:
            cfg.update(gen_cfg[name]['conf'])
    return cfg


def _get_general_config_value(conf, option):
    if 'general' in conf and option.name in conf['general']:
        return conf['general'][option.name]
    return option.default_value


def _get_general_cluster_config_value(cluster, option):
    return _get_general_config_value(cluster.cluster_configs, option)


def is_data_locality_enabled(cluster):
    if not CONF.enable_data_locality:
        return False
    return _get_general_cluster_config_value(cluster, ENABLE_DATA_LOCALITY)


def is_swift_enabled(configs):
    return _get_general_config_value(configs, ENABLE_SWIFT)


def get_decommissioning_timeout(cluster):
    return _get_general_cluster_config_value(cluster, DECOMMISSIONING_TIMEOUT)


def get_port_from_config(service, name, cluster=None):
    address = get_config_value(service, name, cluster)
    return utils.get_port_from_address(address)
