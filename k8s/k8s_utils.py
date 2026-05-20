import os
import re
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

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


def get_kubectl_context(session_id="default"):
    session_profile = minikube_profile_from_session(session_id)
    if kubectl_context_exists(session_profile):
        return session_profile
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
            "jsonpath={.items[0].metadata.name}",  # para extraer nombre
        ]
    )

    if code != 0 or not out.strip():
        return False, err if err else out

    return True, out.strip()


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


def list_deployments(session_id="default"):
    namespace = namespace_from_session(session_id)
    code, out, err = run_command(
        kubectl_command(session_id, "get", "deployments", "-n", namespace, "--no-headers")
    )

    if code != 0:
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


def get_cluster_pods(session_id="default"):
    code, out, err = run_command(kubectl_command(session_id, "get", "pods", "-A"))

    if code != 0:
        return False, err if err else out

    return True, out
