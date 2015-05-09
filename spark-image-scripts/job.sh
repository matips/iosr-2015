#!/usr/bin/env bash

SPARK_HOME="/usr/local/spark-1.3.1"
MASTER_FILE="/etc/cloud/master"

function usage {
echo "Usage: `basename $0` (submit CLASS JAR_PATH)|(status FRAMEWORK_ID)"
    exit 1
} 

function get_master {
    MASTER_HOSTNAME=`cat ${MASTER_FILE}`
    echo "mesos://${MASTER_HOSTNAME}:5050"
}

[[ $# -ge 1 ]] || usage

case "$1" in
"submit")
    [[ $# -ge 3 ]] || usage
    CLASS="$2"
    JAR_PATH="$3"
    LOCAL_UUID=`uuidgen`
    TMP_FILE=/tmp/job_${LOCAL_UUID}
    nohup ${SPARK_HOME}/bin/spark-submit --master `get_master` --class ${CLASS} ${JAR_PATH} > ${TMP_FILE} 2>&1 &
    for i in {1..10}; do
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
    STATE=`mesos-state | parse_job_status.py "${FW_ID}"`
    EXIT=$?
    echo "${STATE}"
    exit $PYEXIT
    ;;    
*)
    usage
    ;;
esac
