#!/usr/bin/python

import sys
import json

jobId = sys.argv[1]

mesosState = json.load(sys.stdin, encoding="utf8")

framework = None
completed = False

for fw in mesosState[u'completed_frameworks']:
    if fw[u'id'] == jobId:
        framework = fw
        completed = True
        break

for fw in mesosState[u'frameworks']:
    if fw[u'id'] == jobId:
        framework = fw
        break

if framework is None:
    print "Unknown framework."
    exit(1)

tasks = []

for task in framework[u'completed_tasks'] + framework[u'tasks']:
    if completed and task[u'state'] != u'TASK_FINISHED':
        task[u'state'] = u'TASK_KILLED'
    tasks.append({u'id': task[u'id'], u'state': task[u'state']})


print json.dumps(tasks)