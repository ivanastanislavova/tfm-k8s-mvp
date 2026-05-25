"""
Execute the actual Kubernetes installation.

Copy scripts via SSH
Run kubeadm init on master
Run kubeadm join on workers

Turn the plan into a real cluster.
"""

import json
import os
import re
import subprocess


def load_inventory(path="cluster_inventory.json"):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_local_command(command):
    result = subprocess.run(command, capture_output=True, text=True, shell=True)
    return result.returncode, result.stdout, result.stderr


def copy_script_to_host(local_script, user, host, remote_path):
    command = f'scp "{local_script}" {user}@{host}:{remote_path}'
    return run_local_command(command)


def run_remote_script(user, host, remote_path):
    command = f'ssh {user}@{host} "chmod +x {remote_path} && {remote_path}"'
    return run_local_command(command)


def extract_join_command(output):
    match = re.search(r"kubeadm join\s+[^\n]+(?:\n\s+--[^\n]+)*", output)

    if not match:
        return ""

    return " ".join(line.strip() for line in match.group(0).splitlines())


def execute_cluster_provisioning(inventory_path=None):
    if inventory_path is None:
        inventory_path = "cluster_inventory.json"

    inventory = load_inventory(inventory_path)

    ssh_user = inventory["ssh_user"]
    master_host = inventory["master"]["host"]
    workers = inventory["workers"]

    base_dir = os.path.dirname(os.path.abspath(__file__))
    master_script = os.path.join(base_dir, "generated_cluster", "master_setup.sh")
    worker_script = os.path.join(base_dir, "generated_cluster", "worker_setup.sh")

    logs = []

    logs.append(f"Copying master script to {master_host}")
    copy_code, copy_out, copy_err = copy_script_to_host(
        master_script, ssh_user, master_host, "/tmp/master_setup.sh"
    )
    logs.append(copy_out + copy_err)

    if copy_code != 0:
        return False, "\n".join(logs), ""

    logs.append(f"Executing master script on {master_host}")
    master_code, master_out, master_err = run_remote_script(
        ssh_user, master_host, "/tmp/master_setup.sh"
    )
    logs.append(master_out + master_err)

    if master_code != 0:
        return False, "\n".join(logs), ""

    join_command = extract_join_command(master_out + master_err)

    if not join_command:
        logs.append("Join command could not be extracted from master output.")
        return False, "\n".join(logs), ""

    logs.append(f"Join command extracted: {join_command}")

    for worker in workers:
        worker_host = worker["host"]

        logs.append(f"Copying worker script to {worker_host}")
        copy_code, copy_out, copy_err = copy_script_to_host(
            worker_script, ssh_user, worker_host, "/tmp/worker_setup.sh"
        )
        logs.append(copy_out + copy_err)

        if copy_code != 0:
            return False, "\n".join(logs), join_command

        remote_command = (
            f"ssh {ssh_user}@{worker_host} "
            f'"chmod +x /tmp/worker_setup.sh && /tmp/worker_setup.sh && sudo {join_command}"'
        )

        logs.append(f"Executing worker setup and join on {worker_host}")
        worker_code, worker_out, worker_err = run_local_command(remote_command)
        logs.append(worker_out + worker_err)

        if worker_code != 0:
            return False, "\n".join(logs), join_command

    return True, "\n".join(logs), join_command
