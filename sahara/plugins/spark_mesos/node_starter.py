from sahara import context
from sahara.plugins import utils
from sahara.utils import cluster_progress_ops as cpo
from sahara.utils import remote


@cpo.event_wrapper(mark_successful_on_exit=True,
                   step=utils.start_process_event_message("NameNode"))
def start_namenode(cluster):
    namenode = utils.get_instance(cluster, "namenode")
    with remote.get_remote(namenode) as r:
        r.execute_command("sudo -u hadoop hadoop namenode -format")
        r.execute_command("sudo service hadoop-hdfs-namenode start")


@cpo.event_wrapper(mark_successful_on_exit=True,
                   step=utils.start_process_event_message("NameNode dirs"))
def prepare_namenode_dirs(cluster):
    namenode = utils.get_instance(cluster, "namenode")
    with remote.get_remote(namenode) as r:
        r.execute_command("sudo -u hadoop hdfs dfs -mkdir -p /user/$USER/")
        r.execute_command("sudo -u hadoop hdfs dfs -chown $USER /user/$USER/")


def start_datanodes(cluster):
    datanodes = utils.get_instances(cluster, "datanodes")

    cpo.add_provisioning_step(cluster.id,
                              utils.start_process_event_message("DataNodes"),
                              len(datanodes))

    with context.ThreadGroup() as tg:
        for i in datanodes:
            tg.spawn('spark-mesos-start-dn-{0}'.format(i.instance_name),
                     _start_datanode, i)


@cpo.event_wrapper(mark_successful_on_exit=True,
                   step=utils.start_process_event_message("master"))
def start_master(cluster):
    master = utils.get_instance(cluster, "master")
    with remote.get_remote(master) as r:
        r.execute_command("node-admin.sh start master")


def start_slaves(cluster):
    master = utils.get_instance(cluster, "master")
    slaves = utils.get_instances(cluster, "slave")

    cpo.add_provisioning_step(cluster.id,
                              utils.start_process_event_message("slaves"),
                              len(slaves))

    with context.ThreadGroup() as tg:
        for i in slaves:
            tg.spawn('spark-mesos-start-sl-{0}'.format(i.instance_name),
                     _start_slave, master, i)


@cpo.event_wrapper(mark_successful_on_exit=True)
def _start_datanode(instance):
    with remote.get_remote(instance) as r:
        r.execute_command("sudo service hadoop-hdfs-datanode start")


@cpo.event_wrapper(mark_successful_on_exit=True)
def _start_slave(master, instance):
    with remote.get_remote(instance) as r:
        r.execute_command(
            "node-admin.sh start slave {0}".format(master.internal_ip))
