#/usr/bin/env bash

MASTER_FILE="/etc/cloud/master"

SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do # resolve $SOURCE until the file is no longer a symlink
    DIR="$( cd -P "$( dirname "$SOURCE" )" && pwd )"
    SOURCE="$(readlink "$SOURCE")"
    [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE" # if $SOURCE was a relative symlink, we need to resolve it relative to the path where the symlink file was located
done
DIR="$( cd -P "$( dirname "$SOURCE" )" && pwd )"

$(cd $DIR ; git pull)

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
