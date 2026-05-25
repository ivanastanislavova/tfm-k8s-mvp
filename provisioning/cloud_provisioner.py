"""
Provision infrastructure for the cluster.

Providers:

- terraform/oracle: uses Terraform and OCI
- minikube: creates a reproducible local cluster
"""

import json
import os
import re
import subprocess
import threading
import traceback
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TERRAFORM_DIR = os.path.join(BASE_DIR, "terraform")
GENERATED_DIR = os.path.join(BASE_DIR, "generated_cluster")


def minikube_profile_from_session(session_id: str):
    normalized = re.sub(r"[^a-z0-9-]+", "-", (session_id or "default").lower())
    normalized = re.sub(r"-+", "-", normalized).strip("-") or "default"
    return f"kaf-{normalized}"[:40].rstrip("-")


def minikube_status_path(profile: str):
    return os.path.join(GENERATED_DIR, f"{profile}_status.json")


def _write_status(profile: str, payload: dict):
    os.makedirs(GENERATED_DIR, exist_ok=True)
    payload["profile"] = profile
    payload["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    with open(minikube_status_path(profile), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def write_minikube_status(profile: str, payload: dict):
    _write_status(profile, payload)


def read_minikube_status(profile: str):
    path = minikube_status_path(profile)
    if not os.path.exists(path):
        return {}

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _safe_text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def run_command(command, cwd=None, timeout=300):
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            shell=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except subprocess.TimeoutExpired as exc:
        out = _safe_text(exc.stdout)
        err = _safe_text(exc.stderr)
        err += f"\nCommand timed out after {timeout} seconds: {command}"
        return 124, out, err


def _get_node_lines(profile: str, logs: list, timeout=30):
    nodes_command = f"kubectl --context {profile} get nodes --no-headers"
    code, out, err = run_command(nodes_command, timeout=timeout)
    logs.append("=== kubectl get nodes --no-headers ===")
    logs.append(out + err)
    if code != 0:
        raise RuntimeError("\n".join(logs))
    return [line for line in out.splitlines() if line.strip()], out + err


def _wait_for_ready_nodes(profile: str, desired_nodes: int, logs: list, timeout=420):
    nodes_command = f"kubectl --context {profile} get nodes --no-headers"
    deadline = time.time() + timeout
    last_nodes_output = ""

    while time.time() < deadline:
        code, out, err = run_command(nodes_command, timeout=30)
        last_nodes_output = out + err
        ready_nodes = [
            line
            for line in out.splitlines()
            if line.strip() and " Ready " in f" {line} "
        ]

        if code == 0 and len(ready_nodes) >= desired_nodes:
            return ready_nodes, last_nodes_output

        time.sleep(5)

    logs.append(f"=== waiting for {desired_nodes} nodes to become Ready ===")
    logs.append(last_nodes_output)
    raise RuntimeError(
        f"Minikube profile {profile} has not reached {desired_nodes} Ready nodes."
    )


def provision_oracle_infrastructure(params: dict):
    os.makedirs(GENERATED_DIR, exist_ok=True)
    logs = []

    for cmd in [
        "terraform init",
        "terraform apply -auto-approve",
        "terraform output -json",
    ]:
        code, out, err = run_command(cmd, cwd=TERRAFORM_DIR)
        logs.append(f"=== {cmd} ===")
        logs.append(out + err)

        if code != 0:
            raise RuntimeError("\n".join(logs))

    outputs = json.loads(out)

    inventory = {
        "ssh_user": outputs.get("ssh_user", {}).get("value", "ubuntu"),
        "master": {"host": outputs["master_public_ip"]["value"]},
        "workers": [{"host": ip} for ip in outputs["worker_public_ips"]["value"]],
    }

    inventory_path = os.path.join(GENERATED_DIR, "inventory.json")

    with open(inventory_path, "w", encoding="utf-8") as f:
        json.dump(inventory, f, indent=2)

    return inventory, inventory_path, "\n".join(logs)


def provision_minikube_infrastructure(params: dict):
    os.makedirs(GENERATED_DIR, exist_ok=True)

    profile = params.get("profile") or minikube_profile_from_session(
        params.get("session_id", "default")
    )
    cpus = params.get("cpus", 2)
    memory = params.get("memory", 4096)
    masters = params.get("masters", 1)
    workers = params.get("workers", 1)
    desired_nodes = masters + workers
    logs = []
    timings = {}
    _write_status(
        profile,
        {
            "status": "running",
            "message": "Minikube cluster creation is running synchronously.",
            "logs": "",
        },
    )

    status_command = f"minikube -p {profile} status"
    code, out, err = run_command(status_command, timeout=30)
    logs.append(f"=== {status_command} ===")
    logs.append(out + err)

    if "Running" not in out:
        master_start = time.perf_counter()
        command = (
            f"minikube -p {profile} start --driver=docker --nodes=1 "
            f"--cpus={cpus} --memory={memory} "
            "--cni=calico "
            "--wait=all --wait-timeout=420s"
        )

        code, out, err = run_command(command, timeout=900)
        timings["master_node_deployment_seconds"] = round(
            time.perf_counter() - master_start, 4
        )

        logs.append(f"=== {command} ===")
        logs.append(out + err)

        if code != 0:
            raise RuntimeError("\n".join(logs))
    else:
        logs.append(f"Minikube profile {profile} is already running; start skipped.")

    _wait_for_ready_nodes(profile, masters, logs, timeout=420)
    current_nodes, _ = _get_node_lines(profile, logs)

    worker_addition_times = []

    while len(current_nodes) < desired_nodes:
        worker_start = time.perf_counter()
        command = f"minikube -p {profile} node add --worker"
        code, out, err = run_command(command, timeout=600)
        logs.append(f"=== {command} ===")
        logs.append(out + err)

        if code != 0:
            raise RuntimeError("\n".join(logs))

        current_nodes, _ = _get_node_lines(profile, logs)
        _wait_for_ready_nodes(profile, len(current_nodes), logs, timeout=420)
        worker_addition_times.append(
            round(time.perf_counter() - worker_start, 4)
        )

    timings["extra_node_addition_seconds"] = worker_addition_times
    timings["average_extra_node_addition_seconds"] = (
        round(sum(worker_addition_times) / len(worker_addition_times), 4)
        if worker_addition_times
        else 0
    )
    timings["complete_cluster_deployment_seconds"] = round(
        timings.get("master_node_deployment_seconds", 0)
        + sum(worker_addition_times),
        4,
    )

    _wait_for_ready_nodes(profile, desired_nodes, logs, timeout=420)

    code, out, err = run_command(
        f"kubectl --context {profile} get nodes --no-headers -o custom-columns=NAME:.metadata.name",
        timeout=30,
    )
    logs.append("=== kubectl get node names ===")
    logs.append(out + err)

    node_names = [line.strip() for line in out.splitlines() if line.strip()]
    master_name = node_names[0] if node_names else "minikube"
    worker_names = node_names[1:]

    if len(current_nodes) > desired_nodes:
        logs.append(
            f"Minikube already has {len(current_nodes)} nodes. "
            f"Requested {desired_nodes}; existing nodes were not removed automatically."
        )

    inventory = {
        "provider": "minikube",
        "profile": profile,
        "context": profile,
        "cluster": "local",
        "ssh_user": "",
        "master": {"host": master_name},
        "workers": [{"host": name} for name in worker_names],
        "timings": timings,
    }

    inventory_path = os.path.join(GENERATED_DIR, "inventory.json")

    with open(inventory_path, "w", encoding="utf-8") as f:
        json.dump(inventory, f, indent=2)

    _write_status(
        profile,
        {
            "status": "ready",
            "message": "Minikube cluster is ready.",
            "inventory": inventory,
            "inventory_path": inventory_path,
            "timings": timings,
            "logs": "\n".join(logs),
        },
    )

    return inventory, inventory_path, "\n".join(logs)


def start_minikube_provisioning_job(params: dict):
    profile = params.get("profile") or minikube_profile_from_session(
        params.get("session_id", "default")
    )
    status = read_minikube_status(profile)

    if status.get("status") == "running":
        return {
            "profile": profile,
            "status_path": minikube_status_path(profile),
            "message": f"Cluster creation is already running for profile {profile}.",
        }

    def worker():
        try:
            _write_status(
                profile,
                {
                    "status": "running",
                    "message": "Minikube cluster creation started.",
                    "logs": "",
                },
            )
            inventory, inventory_path, logs = provision_minikube_infrastructure(params)
            _write_status(
                profile,
                {
                    "status": "ready",
                    "message": "Minikube cluster is ready.",
                    "inventory": inventory,
                    "inventory_path": inventory_path,
                    "logs": logs,
                },
            )
        except Exception as exc:
            _write_status(
                profile,
                {
                    "status": "failed",
                    "message": str(exc),
                    "logs": traceback.format_exc(),
                },
            )

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    return {
        "profile": profile,
        "status_path": minikube_status_path(profile),
        "message": f"Cluster creation started in background for profile {profile}.",
    }


def provision_infrastructure(params: dict):
    provider = params.get("provider", "minikube")

    if provider in ["terraform", "oracle"]:
        return provision_oracle_infrastructure(params)

    if provider == "minikube":
        return provision_minikube_infrastructure(params)

    raise ValueError(f"Unsupported provider: {provider}")
