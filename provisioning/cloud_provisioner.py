"""
Provisiona infraestructura para el clúster.

Providers:
- oracle: usa Terraform y OCI
- minikube: crea un clúster local reproducible
"""

import json
import os
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TERRAFORM_DIR = os.path.join(BASE_DIR, "terraform")
GENERATED_DIR = os.path.join(BASE_DIR, "generated_cluster")


def run_command(command, cwd=None):
    result = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, shell=True
    )
    return result.returncode, result.stdout, result.stderr


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

    cpus = params.get("cpus", 2)
    memory = params.get("memory", 4096)

    command = f"minikube start --driver=docker --cpus={cpus} --memory={memory}"

    code, out, err = run_command(command)

    logs = f"=== {command} ===\n{out}\n{err}"

    if code != 0:
        raise RuntimeError(logs)

    inventory = {
        "provider": "minikube",
        "cluster": "local",
        "ssh_user": "",
        "master": {"host": "minikube"},
        "workers": [],
    }

    inventory_path = os.path.join(GENERATED_DIR, "inventory.json")

    with open(inventory_path, "w", encoding="utf-8") as f:
        json.dump(inventory, f, indent=2)

    return inventory, inventory_path, logs


def provision_infrastructure(params: dict):
    provider = params.get("provider", "minikube")

    if provider == "oracle":
        return provision_oracle_infrastructure(params)

    if provider == "minikube":
        return provision_minikube_infrastructure(params)

    raise ValueError(f"Provider no soportado: {provider}")
