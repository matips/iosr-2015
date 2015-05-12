# Copyright (c) 2014 Mirantis Inc.
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
import json

from sahara import exceptions as ex
from sahara.service.edp.spark import engine as edp_engine
from sahara import conductor
from sahara import context
from sahara import exceptions as e
from sahara.i18n import _
from sahara.plugins import utils as plugin_utils
from sahara.service.edp import hdfs_helper as h
from sahara.service.edp import job_utils
from sahara.swift import utils as su
from sahara.utils import edp
from sahara.utils import remote


class EdpEngine(edp_engine.SparkJobEngine):
    edp_base_version = "1.0.0"

    @staticmethod
    def edp_supported(version):
        return version >= EdpEngine.edp_base_version

    def validate_job_execution(self, cluster, job, data):
        if not self.edp_supported(cluster.hadoop_version):
            raise ex.InvalidDataException(
                _('Spark {base} or higher required to run {type} jobs').format(
                    base=EdpEngine.edp_base_version, type=job.type))

        super(EdpEngine, self).validate_job_execution(cluster, job, data)

    def _get_job_status_from_remote(self, r, framework, job_execution):
        ret, stdout = r.execute_command('job.sh status {0}'.format(framework))
        if ret != 0:
            return {"status": edp.JOB_STATUS_DONEWITHERROR}

        statuses = json.loads(stdout)

        status = edp.JOB_STATUS_DONEWITHERROR
        pending_states = ['TASK_STAGING', 'TASK_STARTING']
        failed_states = ['TASK_FAILED', 'TASK_LOST', 'TASK_ERROR']

        if all(map(lambda s: s['state'] in pending_states, statuses)):
            status = edp.JOB_STATUS_PENDING
        elif any(map(lambda s: s['state'] == 'TASK_RUNNING', statuses)):
            status = edp.JOB_STATUS_RUNNING
        elif all(map(lambda s: s['state'] == 'TASK_FINISHED', statuses)):
            status = edp.JOB_STATUS_SUCCEEDED
        elif any(map(lambda s: s['state'] == 'TASK_KILLED', statuses)):
            status = edp.JOB_STATUS_KILLED
        elif any(map(lambda s: s['state'] in failed_states, statuses)):
            status = edp.JOB_STATUS_FAILED

        return {'status': status}

    @staticmethod
    def _get_framework_id_if_running(job_execution):
        framework_id = job_execution.oozie_job_id
        if not framework_id or (
                    job_execution.info[
                        'status'] in edp.JOB_STATUSES_TERMINATED):
            return None

        return framework_id

    def run_job(self, job_execution):
        ctx = context.ctx()
        job = conductor.job_get(ctx, job_execution.job_id)

        additional_sources, updated_job_configs = (
            job_utils.resolve_data_source_references(job_execution.job_configs)
        )

        for data_source in additional_sources:
            if data_source and data_source.type == 'hdfs':
                h.configure_cluster_for_hdfs(self.cluster, data_source)
                break

        # We'll always run the driver program on the master
        master = plugin_utils.get_instance(self.cluster, "master")

        # TODO(tmckay): wf_dir should probably be configurable.
        # The only requirement is that the dir is writable by the image user
        wf_dir = job_utils.create_workflow_dir(master, '/tmp/spark-edp', job,
                                               job_execution.id, "700")

        paths, builtin_paths = self._upload_job_files(
            master, wf_dir, job, updated_job_configs)

        # We can shorten the paths in this case since we'll run out of wf_dir
        paths = [os.path.basename(p) for p in paths]
        builtin_paths = [os.path.basename(p) for p in builtin_paths]

        # TODO(tmckay): for now, paths[0] is always assumed to be the app
        # jar and we generate paths in order (mains, then libs).
        # When we have a Spark job type, we can require a "main" and set
        # the app jar explicitly to be "main"
        app_jar = paths.pop(0)
        job_class = updated_job_configs["configs"]["edp.java.main_class"]

        # If we uploaded builtins then we are using a wrapper jar. It will
        # be the first one on the builtin list and the original app_jar needs
        # to be added to the  'additional' jars
        if builtin_paths:
            wrapper_jar = builtin_paths.pop(0)
            wrapper_class = 'org.openstack.sahara.edp.SparkWrapper'
            wrapper_xml = self._upload_wrapper_xml(master,
                                                   wf_dir,
                                                   updated_job_configs)
            wrapper_args = "%s %s" % (wrapper_xml, job_class)

            additional_jars = ",".join([app_jar] + paths + builtin_paths)

        else:
            wrapper_jar = wrapper_class = wrapper_args = ""
            additional_jars = ",".join(paths)

        # All additional jars are passed with the --jars option
        if additional_jars:
            additional_jars = " --jars " + additional_jars

        # Launch the spark job using spark-submit and deploy_mode = client
        # TODO(tmckay): we need to clean up wf_dirs on long running clusters
        # TODO(tmckay): probably allow for general options to spark-submit
        args = updated_job_configs.get('args', [])
        args = " ".join([su.inject_swift_url_suffix(arg) for arg in args])
        if args:
            args = " " + args

        if wrapper_jar and wrapper_class:
            # Substrings which may be empty have spaces
            # embedded if they are non-empty
            cmd = ('job.sh submit{driver_cp} --class {wrapper_class}'
                   '{addnl_jars} {wrapper_jar} {wrapper_args}{args}').format(
                driver_cp=self.get_driver_classpath(),
                klass=wrapper_class,
                addnl_jars=additional_jars,
                wrapper_jar=wrapper_jar, wrapper_args=wrapper_args,
                args=args)
        else:
            cmd = ('job.sh submit --class {klass}{addnl_jars}'
                   '{app_jar}{args}').format(
                klass=job_class,
                addnl_jars=additional_jars,
                app_jar=app_jar,
                args=args)

        job_execution = conductor.job_execution_get(ctx, job_execution.id)
        if job_execution.info['status'] == edp.JOB_STATUS_TOBEKILLED:
            return (None, edp.JOB_STATUS_KILLED, None)

        # If an exception is raised here, the job_manager will mark
        # the job failed and log the exception
        # The redirects of stdout and stderr will preserve output in the wf_dir
        with remote.get_remote(master) as r:
            # Upload the command launch script
            launch = os.path.join(wf_dir, "launch_command")
            r.write_file_to(launch, self._job_script())
            r.execute_command("chmod +x %s" % launch)
            ret, stdout = r.execute_command(
                "cd %s; ./launch_command %s" % (wf_dir, cmd))

        if ret == 0:
            # Success, we'll add the wf_dir in job_execution.extra and store
            # pid@instance_id as the job id
            # We know the job is running so return "RUNNING"
            return (stdout.strip(), edp.JOB_STATUS_RUNNING,
                    {'spark-path': wf_dir})

        # Hmm, no execption but something failed.
        # Since we're using backgrounding with redirect, this is unlikely.
        raise e.EDPError(_("Spark job execution failed. Exit status = "
                           "%(status)s, stdout = %(stdout)s") %
                         {'status': ret, 'stdout': stdout})

    def cancel_job(self, job_execution):
        framework_id = self._get_framework_id_if_running(job_execution)

        if framework_id is not None:
            master = plugin_utils.get_instance(self.cluster, "master")
            with remote.get_remote(master) as r:
                ret, stdout = r.execute_command(
                    'job.sh shutdown {0}'.format(framework_id))
                if ret == 0:
                    return self._get_job_status_from_remote(r, framework_id,
                                                            job_execution)

    def get_job_status(self, job_execution):
        framework_id = self._get_framework_id_if_running(job_execution)

        if framework_id is not None:
            master = plugin_utils.get_instance(self.cluster, "master")
            with remote.get_remote(master) as r:
                return self._get_job_status_from_remote(r, framework_id,
                                                        job_execution)

    def get_driver_classpath(self):
        return ''
