"""Trusted enforcement probe. Reads its own cgroup interface files and reports them raw.

This container measures nothing about a workload. It exists only to show what limits the
engine actually applied to a container created with a given profile.
"""

import json
import os

FILES = {"cpu_max": "cpu.max", "memory_max": "memory.max", "memory_swap_max": "memory.swap.max",
         "pids_max": "pids.max", "cpuset_cpus": "cpuset.cpus",
         "cpuset_cpus_effective": "cpuset.cpus.effective", "cpu_stat": "cpu.stat"}
ROOT = "/sys/fs/cgroup"


def read(name):
    try:
        with open(os.path.join(ROOT, name)) as stream:
            return stream.read().strip()
    except OSError as error:
        return {"unreadable": type(error).__name__}


unified = os.path.exists(os.path.join(ROOT, "cgroup.controllers"))
payload = {"cgroup_v2_unified": unified,
           "online_cpus": len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
           "files": {key: read(name) for key, name in FILES.items()} if unified else {},
           "inconclusive_reason": None if unified else
               "No cgroup v2 unified hierarchy inside the container; limits were not asserted"}
print(json.dumps({"evalnoise_probe": {"version": 1, "payload": payload}}))
