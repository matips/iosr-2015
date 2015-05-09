#!/usr/bin/python

import sys
import json

jobId = sys.argv[1]

mesosState = json.load(sys.stdin, encoding="utf8")

framework = None

for fw in mesosState[u'completed_frameworks']:
    if fw[u'id'] == jobId:
        framework = fw
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
    tasks.append({u'id': task[u'id'], u'state': task[u'state']})


print json.dumps(tasks)