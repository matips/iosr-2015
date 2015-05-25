#/usr/bin/env bash

MASTER_FILE="/etc/cloud/master"

function usage {
    echo "Usage: `basename $0` start|stop master|slave [MASTER_HOSTNAME]"
    exit 1
} 

[[ $# -ge 2 ]] || usage

NODE_TYPE="$2"
ACTION="$1"

case "${ACTION}" in
"start")
    BASE_CMD="mesos-daemon.sh mesos-${NODE_TYPE} --work_dir=/var/mesos/${NODE_TYPE}"
    case "${NODE_TYPE}" in
    "master")
        ${BASE_CMD}
        MASTER_HOSTNAME=`hostname`
        mesos config master "$MASTER_HOSTNAME:5050"
        echo "${MASTER_HOSTNAME}" > ${MASTER_FILE}
        ;;
    "slave")
        [[ $# -ge 3 ]] || usage
	MASTER_HOSTNAME=$3
        MASTER_ADDR="${MASTER_HOSTNAME}:5050"
	echo "${BASE_CMD} --master=${MASTER_ADDR}"
	${BASE_CMD} --master=${MASTER_ADDR}
        echo "${MASTER_HOSTNAME}" > ${MASTER_FILE}
        ;;
    *) 
        usage 
        ;;
    esac
    ;;
"stop")
    [[ "master slave" =~ ${NODE_TYPE} ]] || usage
    killall mesos-${NODE_TYPE}
    ;;
*)
    usage
    ;;
esac
