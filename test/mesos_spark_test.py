from saharaclient.api.job_executions import JobExecution

__author__ = 'Rafal Slota'

import os
import time

from saharaclient.api.client import Client as saharaclient
from novaclient.client import Client as novaclient
from keystoneclient import session
from keystoneclient.auth.identity import v2


openstack_hostname = "172.17.84.1"
# openstack_username = "student"
# openstack_password = "student123"
openstack_project = "student"

openstack_token = os.environ['AUTH_TOKEN']
openstack_tenant = os.environ['TENANT_ID']

plugin_name = u'spark_mesos'
hadoop_version = "1.0.0"

auth_url = "http://{0}:5000/v2.0".format(openstack_hostname)
sahara_url = "http://{0}:8386/v1.1/{1}".format(openstack_hostname, openstack_tenant)

job_binary_name = "simple-test-8.jar"
job_binary_path = "simple-test.jar"

common_test_name = "mesos-test-8"

def test_pi():
    # Create global session for nova and other non-sahara services
    globalauth = v2.Token(auth_url=auth_url,
                          token=openstack_token,
                          tenant_id=openstack_tenant)
    globalsess = session.Session(auth=globalauth)

    # Create sahara API and its sessions
    sahara = saharaclient(auth_url=auth_url,
                          sahara_url=sahara_url,
                          input_auth_token=openstack_token,
                          project_id=openstack_project)
                          #username=openstack_username,
                          #api_key=openstack_password,
                          #project_name=openstack_project)

    # Find suitable flavor
    nova = novaclient(3, session=globalsess)

    flavors = map(lambda fl: (fl._info.get(u'disk'), fl),
                  filter(lambda fl: fl._info.get(u'ram') >= 1024 and fl._info.get(u'vcpus') >= 2,
                         nova.flavors.list()))
    flavors.sort()
    _, flavor = flavors[0]


    image = None
    plugin = None

    for i in sahara.images.list():
        if plugin_name in i.tags:
            image = i
    assert image is not None



    for p in sahara.plugins.list():
        if plugin_name == p.name:
            plugin = p
    assert plugin is not None

    cleanup(sahara, nova)

    # Upload keypair
    pub_key_data = load_file("cloud.key.pub")
    pub_key = nova.keypairs.create(common_test_name, pub_key_data)


    master_group = sahara.node_group_templates.create("mesos-master-test", plugin_name, hadoop_version, flavor._info.get(u'id'),
                                      auto_security_group=True,
                                      node_processes=["namenode", "datanode", "master", "slave"])

    slave_group = sahara.node_group_templates.create("mesos-slave-test", plugin_name, hadoop_version, flavor._info.get(u'id'),
                                      auto_security_group=True,
                                      node_processes=["datanode", "slave"])


    cluster_template = sahara.cluster_templates.create(common_test_name, plugin_name, hadoop_version,
                                    node_groups=[
                                        {
                                            u'name': "master",
                                            u'node_group_template_id': master_group.id,
                                            u'count': 1,
                                            u'floating_ip_pool': u'574c79a7-2af2-4686-95eb-817054d0105e'
                                        },
                                        # {
                                        #     u'name': "slave",
                                        #     u'node_group_template_id': slave_group.id,
                                        #     u'count': 1,
                                        #     u'floating_ip_pool': u'574c79a7-2af2-4686-95eb-817054d0105e'
                                        # }
                                    ])



    cluster = sahara.clusters.create(common_test_name, plugin_name, hadoop_version,
                                     cluster_template_id=cluster_template.id,
                                     default_image_id=image.id,
                                     net_id=u'1f741b49-4e3e-43fa-900a-564c85d0c9c6',
                                     user_keypair_id=pub_key.id)


    # Load job binary from file
    data = load_file(job_binary_path)


    # Upload and register job binary
    internal_data = sahara.job_binary_internals.create(job_binary_name, data)
    job_binary = sahara.job_binaries.create(job_binary_name, "internal-db://{0}".format(internal_data.id), "", {})

    job = sahara.jobs.create(common_test_name, "Spark", [job_binary.id], [], "")

    print sahara.clusters.find(name=common_test_name)[0]._info

    # Wait for cluster init
    cluster_started = lambda: sahara.clusters.find(name=common_test_name)[0]._info.get(u'status') in [u'Active', u'Error']
    print cluster_started()
    assert wait_until(cluster_started, 60 * 10)
    assert sahara.clusters.find(name=common_test_name)[0]._info.get(u'status') == u'Active'


    job_exec = sahara.job_executions.create(job.id, cluster.id, None, None,
                                            {
                                                u'configs': {
                                                    u'edp.java.main_class': u'JavaSparkPi'
                                                },
                                                u'args': ["2"]
                                            })

    # @todo: wait for job execution and check job results
    get_job_status = lambda: sahara.job_executions.find(id=job_exec.id)[0]._info.get(u'info').get(u'status').upper()
    job_completed = lambda: get_job_status() not in [u'PENDING', u'RUNNING']
    wait_until(job_completed, 60 * 5)

    assert get_job_status() == u'SUCCEEDED'


# JOB_STATUS_DONEWITHERROR = 'DONEWITHERROR'
# JOB_STATUS_FAILED = 'FAILED'
# JOB_STATUS_KILLED = 'KILLED'
# JOB_STATUS_PENDING = 'PENDING'
# JOB_STATUS_RUNNING = 'RUNNING'
# JOB_STATUS_SUCCEEDED = 'SUCCEEDED'
# JOB_STATUS_TOBEKILLED = 'TOBEKILLED'

    try:
        sahara.job_executions.delete(job_exec.id)
        cleanup(sahara, nova)
    except Exception:
        pass


def wait_until(predicate, timeout, period=0.25, *args, **kwargs):
    mustend = time.time() + timeout
    while time.time() < mustend:
        if predicate(*args, **kwargs):
            return True
        time.sleep(period)
    return False


def cleanup(sahara, nova):
    # Cleanup clusters
    for c in sahara.clusters.find(name=common_test_name):
        sahara.clusters.delete(c.id)

    wait_until(lambda: len(sahara.clusters.find(name=common_test_name)) == 0, 60)


    # Cleanup templates
    for t in sahara.cluster_templates.find(name=common_test_name):
        sahara.cluster_templates.delete(t.id)

    # Cleanup templates
    for t in sahara.node_group_templates.find(name="mesos-master-test") + \
            sahara.node_group_templates.find(name="mesos-slave-test"):
        sahara.node_group_templates.delete(t.id)

    # Cleanup jobs
    for j in sahara.job_executions.find(name=common_test_name):
        sahara.jobs.delete(j.id)

    for j in sahara.jobs.find(name=common_test_name):
        sahara.jobs.delete(j.id)

    # Cleanup jo binaries
    for j in sahara.job_binaries.find(name=job_binary_name):
        sahara.job_binaries.delete(j.id)

    for j in sahara.job_binary_internals.find(name=job_binary_name):
        sahara.job_binary_internals.delete(j.id)

    try:
        k = nova.keypairs.find(name=common_test_name)
        nova.keypairs.delete(k.id)
    except Exception:
        pass



def load_file(file_path):
    data = ""
    with open(file_path, "rb") as f:
        while True:
            old_len = len(data)
            data += f.read(1024)
            if len(data) == old_len:
                break

    return data