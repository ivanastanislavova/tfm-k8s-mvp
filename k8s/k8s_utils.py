import os
import json
import re
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GENERATED_CLUSTER_DIR = PROJECT_ROOT / "provisioning" / "generated_cluster"
UPDATED_MINIKUBE_CONTEXTS = set()

# os.environ["KUBECONFIG"] = "C:/tfm-k8s-mvp/config"


def run_command(command):
    # FUNCIÓN BASE
    # Ejecuta cualquier comando de terminal (kubectl)

    result = subprocess.run(
        command,
        capture_output=True,  # Captura stdout y stderr
        text=True,  # Devuelve strings en vez de bytes
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )

    # returncode → 0 = OK, !=0 = error
    return result.returncode, result.stdout, result.stderr


def minikube_profile_from_session(session_id):
    normalized = re.sub(r"[^a-z0-9-]+", "-", (session_id or "default").lower())
    normalized = re.sub(r"-+", "-", normalized).strip("-") or "default"
    return f"kaf-{normalized}"[:40].rstrip("-")


def kubectl_context_exists(context):
    code, out, _ = run_command(["kubectl", "config", "get-contexts", "-o", "name"])
    if code != 0:
        return False
    return context in [line.strip() for line in out.splitlines()]


def update_minikube_context_once(profile):
    if profile in UPDATED_MINIKUBE_CONTEXTS:
        return

    try:
        subprocess.run(
            ["minikube", "-p", profile, "update-context"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except Exception:
        pass

    UPDATED_MINIKUBE_CONTEXTS.add(profile)


def get_kubectl_context(session_id="default"):
    session_profile = minikube_profile_from_session(session_id)
    if kubectl_context_exists(session_profile):
        update_minikube_context_once(session_profile)
        return session_profile
    if kubectl_context_exists("minikube"):
        update_minikube_context_once("minikube")
    return "minikube"


def kubectl_command(session_id, *args):
    return ["kubectl", "--context", get_kubectl_context(session_id), *args]


def namespace_from_session(session_id):
    # Kubernetes namespaces must be DNS labels. This keeps the user-facing
    # session id flexible while making the cluster resource name safe.
    normalized = re.sub(r"[^a-z0-9-]+", "-", (session_id or "default").lower())
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    if not normalized:
        normalized = "default"
    return f"tfm-{normalized}"[:63].rstrip("-")


def ensure_namespace(namespace, session_id="default"):
    code, out, err = run_command(kubectl_command(session_id, "get", "namespace", namespace))
    if code == 0:
        phase_code, phase_out, phase_err = run_command(
            kubectl_command(
                session_id,
                "get",
                "namespace",
                namespace,
                "-o",
                "jsonpath={.status.phase}",
            )
        )
        if phase_code == 0 and phase_out.strip().lower() == "terminating":
            return (
                False,
                f"Namespace {namespace} is still terminating. Wait until Kubernetes finishes deleting it or use a new session.",
            )
        return True, out

    code, out, err = run_command(kubectl_command(session_id, "create", "namespace", namespace))
    if code != 0:
        return False, err if err else out

    return True, out


def deploy_files(session_id="default"):
    # AGENTE DE EJECUCIÓN REAL
    # Aplica los YAMLs al cluster con kubectl

    success = True
    combined_output = []
    namespace = namespace_from_session(session_id)

    namespace_ready, namespace_output = ensure_namespace(namespace, session_id)
    if namespace_output:
        combined_output.append(namespace_output)
    if not namespace_ready:
        return False, "\n".join(combined_output)

    # Siempre aplicas deployment + service
    files_to_apply = [PROJECT_ROOT / "deployment.yaml", PROJECT_ROOT / "service.yaml"]

    # SOLO si existe configmap → lo añadimos
    configmap_path = PROJECT_ROOT / "configmap.yaml"
    if configmap_path.exists():
        files_to_apply.insert(0, configmap_path)

    # SOLO si existe ingress → lo añadimos
    ingress_path = PROJECT_ROOT / "ingress.yaml"
    if ingress_path.exists():
        files_to_apply.append(ingress_path)

    # Ejecuta kubectl apply para cada YAML
    for file_name in files_to_apply:
        code, out, err = run_command(
            kubectl_command(session_id, "apply", "-n", namespace, "-f", str(file_name))
        )

        if out:
            print(out)
            combined_output.append(out)

        if err:
            print(err)
            combined_output.append(err)

        if code != 0:
            success = False

    # Devuelve si todo fue bien + logs completos
    return success, "\n".join(combined_output)


def get_pods_output(app_name, session_id="default"):
    # MONITORING
    # Obtiene los pods de tu app

    code, out, err = run_command(
        [
            *kubectl_command(session_id),
            "get",
            "pods",
            "-n",
            namespace_from_session(session_id),
            "-l",
            f"app={app_name}",  # selector por label
            "--no-headers",
        ]
    )

    if code != 0:
        return False, err if err else out

    return True, out


def get_first_pod_name(app_name, session_id="default"):
    # UTILIDAD INTERNA
    # Saca el nombre del primer pod
    if not app_name:
        return False, "No application is selected for this session."

    code, out, err = run_command(
        [
            *kubectl_command(session_id),
            "get",
            "pods",
            "-n",
            namespace_from_session(session_id),
            "-l",
            f"app={app_name}",
            "-o",
            "json",
        ]
    )

    if code != 0:
        return False, err if err else out

    try:
        payload = json.loads(out or "{}")
    except json.JSONDecodeError:
        return False, out or "kubectl returned invalid JSON while looking for pods."

    items = payload.get("items", [])
    if not items:
        return (
            False,
            f"No pod found for app={app_name} in namespace {namespace_from_session(session_id)}.",
        )

    pod_name = items[0].get("metadata", {}).get("name", "")
    if not pod_name:
        return False, f"No pod name found for app={app_name}."

    return True, pod_name


def get_pod_logs(app_name, session_id="default"):
    # OBSERVABILIDAD: logs

    ok, pod_name = get_first_pod_name(app_name, session_id)
    if not ok:
        return False, pod_name

    code, out, err = run_command(
        kubectl_command(session_id, "logs", "-n", namespace_from_session(session_id), pod_name)
    )

    if code != 0:
        return False, err if err else out

    return True, out


def describe_pod(app_name, session_id="default"):
    # OBSERVABILIDAD: describe

    ok, pod_name = get_first_pod_name(app_name, session_id)
    if not ok:
        return False, pod_name

    code, out, err = run_command(
        [
            *kubectl_command(session_id),
            "describe",
            "pod",
            "-n",
            namespace_from_session(session_id),
            pod_name,
        ]
    )

    if code != 0:
        return False, err if err else out

    return True, out


def scale_deployment(app_name, replicas, session_id="default"):
    # ESCALADO dinámico

    code, out, err = run_command(
        [
            *kubectl_command(session_id),
            "scale",
            "deployment",
            "-n",
            namespace_from_session(session_id),
            f"{app_name}-deployment",
            f"--replicas={replicas}",
        ]
    )

    if code != 0:
        return False, err if err else out

    return True, out


def get_deployment_status(app_name, session_id="default"):
    # CONSULTAR estado del deployment

    code, out, err = run_command(
        [
            *kubectl_command(session_id),
            "get",
            "deployment",
            "-n",
            namespace_from_session(session_id),
            f"{app_name}-deployment",
        ]
    )

    if code != 0:
        return False, err if err else out

    return True, out


def get_rollout_status(app_name, session_id="default", timeout_seconds=20):
    code, out, err = run_command(
        [
            *kubectl_command(session_id),
            "rollout",
            "status",
            "deployment",
            "-n",
            namespace_from_session(session_id),
            f"{app_name}-deployment",
            f"--timeout={timeout_seconds}s",
        ]
    )

    if code != 0:
        return False, err if err else out

    return True, out


def list_deployments(session_id="default"):
    namespace = namespace_from_session(session_id)
    namespace_code, namespace_out, namespace_err = run_command(
        kubectl_command(session_id, "get", "namespace", namespace)
    )
    if namespace_code != 0:
        namespace_message = namespace_err if namespace_err else namespace_out
        if "NotFound" in namespace_message or "not found" in namespace_message:
            return True, ""
        return False, namespace_message

    code, out, err = run_command(
        kubectl_command(session_id, "get", "deployments", "-n", namespace, "--no-headers")
    )

    if code != 0:
        message = err if err else out
        if "No resources found" in message:
            return True, ""
        return False, err if err else out

    return True, out


def get_service_details(app_name, session_id="default"):
    code, out, err = run_command(
        [
            *kubectl_command(session_id),
            "get",
            "service",
            "-n",
            namespace_from_session(session_id),
            f"{app_name}-service",
            "-o",
            "jsonpath={.spec.ports[0].port} {.spec.ports[0].targetPort} {.spec.type}",
        ]
    )

    if code != 0:
        return False, err if err else out

    return True, out


def get_deployment_container_port(app_name, session_id="default"):
    code, out, err = run_command(
        [
            *kubectl_command(session_id),
            "get",
            "deployment",
            "-n",
            namespace_from_session(session_id),
            f"{app_name}-deployment",
            "-o",
            "jsonpath={.spec.template.spec.containers[0].ports[0].containerPort}",
        ]
    )

    if code != 0:
        return False, err if err else out

    return True, out


def delete_app_resources(app_name, session_id="default"):
    # LIMPIEZA TOTAL
    # Borra todos los recursos asociados a la app
    namespace = namespace_from_session(session_id)
    outputs = []

    code, out, err = run_command(
        [
            *kubectl_command(session_id),
            "delete",
            "deployment",
            "-n",
            namespace,
            f"{app_name}-deployment",
            "--ignore-not-found",  # evita errores si no existe
        ]
    )
    outputs.extend([text for text in [out, err] if text])

    code, out, err = run_command(
        [
            *kubectl_command(session_id),
            "delete",
            "service",
            "-n",
            namespace,
            f"{app_name}-service",
            "--ignore-not-found",
        ]
    )
    outputs.extend([text for text in [out, err] if text])

    code, out, err = run_command(
        [
            *kubectl_command(session_id),
            "delete",
            "configmap",
            "-n",
            namespace,
            f"{app_name}-config",
            "--ignore-not-found",
        ]
    )
    outputs.extend([text for text in [out, err] if text])

    code, out, err = run_command(
        [
            *kubectl_command(session_id),
            "delete",
            "ingress",
            "-n",
            namespace,
            f"{app_name}-ingress",
            "--ignore-not-found",
        ]
    )
    outputs.extend([text for text in [out, err] if text])

    combined_output = "\n".join(outputs)
    return "deleted" in combined_output.lower(), combined_output


def get_cluster_nodes(session_id="default"):
    code, out, err = run_command(kubectl_command(session_id, "get", "nodes", "-o", "wide"))

    if code != 0:
        return False, err if err else out

    return True, out


def get_minikube_profile_status(profile):
    code, out, err = run_command(["minikube", "-p", profile, "status"])
    return code == 0, "\n".join(part for part in [out, err] if part)


def get_cluster_pods(session_id="default"):
    code, out, err = run_command(kubectl_command(session_id, "get", "pods", "-A"))

    if code != 0:
        return False, err if err else out

    return True, out


def cleanup_session_resources(session_id="default"):
    namespace = namespace_from_session(session_id)
    profile = minikube_profile_from_session(session_id)
    outputs = []

    code, out, err = run_command(
        kubectl_command(session_id, "delete", "namespace", namespace, "--ignore-not-found")
    )
    namespace_output = (out or "") + (err or "")
    namespace_ok = code == 0 or "not found" in namespace_output.lower()
    outputs.append(f"=== delete namespace {namespace} ===")
    outputs.append(namespace_output)

    outputs.append(
        f"Minikube profile {profile} was not deleted. Session cleanup only removes the namespace."
    )

    status_path = GENERATED_CLUSTER_DIR / f"{profile}_status.json"
    if status_path.exists():
        status_path.unlink()
        outputs.append(f"Deleted local cluster status file: {status_path}")

    return namespace_ok, "\n".join(outputs)
