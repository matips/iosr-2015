# Copyright (c) 2013 Mirantis Inc.
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

from savanna.context import ctx
from savanna.context import model_query
import savanna.db.models as m


## Cluster ops
# todo check tenant_id and etc.

def get_clusters(**args):
    return model_query(m.Cluster).filter_by(**args).all()


def get_cluster(**args):
    return model_query(m.Cluster).filter_by(**args).first()


def create_cluster(values):
    session = ctx().session
    with session.begin():
        values['tenant_id'] = ctx().tenant_id
        ngs_vals = values.pop('node_groups', [])
        cluster = m.Cluster(**values)
        for ng in ngs_vals:
            node_group = m.NodeGroup(**ng)
            cluster.node_groups.append(node_group)
            session.add(node_group)
        session.add(cluster)

        return cluster


def terminate_cluster(cluster):
    with ctx().session.begin():
        ctx().session.delete()


## ClusterTemplate ops

def get_cluster_templates(**args):
    return model_query(m.ClusterTemplate).filter_by(**args).all()


def get_cluster_template(**args):
    return model_query(m.ClusterTemplate).filter_by(**args).first()


def create_cluster_template(values):
    session = ctx().session
    with session.begin():
        values['tenant_id'] = ctx().tenant_id
        ngts_vals = values.pop('node_group_templates', [])
        cluster_template = m.ClusterTemplate(**values)
        for ngt in ngts_vals:
            relation = cluster_template.add_node_group_template(
                ngt['node_group_template_id'], ngt['node_group_name'],
                ngt['count'])
            session.add(relation)
        session.add(cluster_template)

        return cluster_template


def terminate_cluster_template(**args):
    with ctx().session.begin():
        ctx().session.delete(get_cluster_template(**args))


## NodeGroupTemplate ops

def get_node_group_templates(**args):
    return model_query(m.NodeGroupTemplate).filter_by(**args).all()


def get_node_group_template(**args):
    return model_query(m.NodeGroupTemplate).filter_by(**args).first()


def create_node_group_template(values):
    session = ctx().session
    with session.begin():
        values['tenant_id'] = ctx().tenant_id
        node_group_template = m.NodeGroupTemplate(**values)
        session.add(node_group_template)
        return node_group_template


def terminate_node_group_template(**args):
    with ctx().session.begin():
        ctx().session.delete(get_node_group_template(**args))