#!/usr/bin/env bash

SPARK_HOME="/usr/local/spark-1.3.1"
MASTER_FILE="/etc/cloud/master"

function usage {
echo "Usage: `basename $0` (submit CLASS JAR_PATH)|(status FRAMEWORK_ID)"
    exit 1
}

function get_master_hostname {
    echo "`cat ${MASTER_FILE}`"
}

function get_master {
    MASTER_HOSTNAME=`get_master_hostname`
    echo "mesos://${MASTER_HOSTNAME}:5050"
}


[[ $# -ge 1 ]] || usage

case "$1" in
"submit")
    [[ $# -ge 3 ]] || usage
    LOCAL_UUID=`uuidgen`
    TMP_FILE=/tmp/job_${LOCAL_UUID}
    shift
    COMMAND="${SPARK_HOME}/bin/spark-submit --master `get_master` $@"
#    echo $COMMAND
    nohup $COMMAND > ${TMP_FILE} 2>&1 &
    for i in {1..30}; do
        if [ ! -f ${TMP_FILE} ]; then
            sleep 1
            continue
        fi

        ID_LINE=`cat ${TMP_FILE} | grep "Registered as framework ID"`
        if [ -z "${ID_LINE}" ]; then 
            sleep 1
            continue
        fi    

        echo ${ID_LINE} | awk '{ print $(NF) }'
        rm -rf ${TMP_FILE} ${TMP_FILE}.err
        exit 0
    done

    echo `cat ${TMP_FILE}`
    rm -rf ${TMP_FILE} ${TMP_FILE}.err
    exit 1
    ;;
"status")
    [[ $# -ge 2 ]] || usage
    FW_ID="$2"
    mesos config master "`get_master_hostname`:5050"
    STATE=`mesos-state | parse_job_status.py "${FW_ID}"`
    EXIT=$?
    echo "${STATE}"
    exit $PYEXIT
    ;;
"shutdown")
    [[ $# -ge 2 ]] || usage
    FW_ID="$2"
    echo "frameworkId=${FW_ID}" | curl -d@- -X POST http://`get_master_hostname`:5050/master/shutdown
    echo
    ;;
*)
    usage
    ;;
esac
