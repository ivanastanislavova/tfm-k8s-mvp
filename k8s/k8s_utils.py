import os
import re
import subprocess

# os.environ["KUBECONFIG"] = "C:/tfm-k8s-mvp/config"


def run_command(command):
    # FUNCIÓN BASE
    # Ejecuta cualquier comando de terminal (kubectl)

    result = subprocess.run(
        command,
        capture_output=True,  # Captura stdout y stderr
        text=True,  # Devuelve strings en vez de bytes
    )

    # returncode → 0 = OK, !=0 = error
    return result.returncode, result.stdout, result.stderr


def namespace_from_session(session_id):
    # Kubernetes namespaces must be DNS labels. This keeps the user-facing
    # session id flexible while making the cluster resource name safe.
    normalized = re.sub(r"[^a-z0-9-]+", "-", (session_id or "default").lower())
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    if not normalized:
        normalized = "default"
    return f"tfm-{normalized}"[:63].rstrip("-")


def ensure_namespace(namespace):
    code, out, err = run_command(["kubectl", "get", "namespace", namespace])
    if code == 0:
        return True, out

    code, out, err = run_command(["kubectl", "create", "namespace", namespace])
    if code != 0:
        return False, err if err else out

    return True, out


def deploy_files(session_id="default"):
    # AGENTE DE EJECUCIÓN REAL
    # Aplica los YAMLs al cluster con kubectl

    success = True
    combined_output = []
    namespace = namespace_from_session(session_id)

    namespace_ready, namespace_output = ensure_namespace(namespace)
    if namespace_output:
        combined_output.append(namespace_output)
    if not namespace_ready:
        return False, "\n".join(combined_output)

    # Siempre aplicas deployment + service
    files_to_apply = ["deployment.yaml", "service.yaml"]

    # SOLO si existe configmap → lo añadimos
    if os.path.exists("configmap.yaml"):
        files_to_apply.insert(0, "configmap.yaml")

    # SOLO si existe ingress → lo añadimos
    if os.path.exists("ingress.yaml"):
        files_to_apply.append("ingress.yaml")

    # Ejecuta kubectl apply para cada YAML
    for file_name in files_to_apply:
        code, out, err = run_command(
            ["kubectl", "apply", "-n", namespace, "-f", file_name]
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
            "kubectl",
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
            "kubectl",
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
        ["kubectl", "logs", "-n", namespace_from_session(session_id), pod_name]
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
            "kubectl",
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
            "kubectl",
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
            "kubectl",
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


def delete_app_resources(app_name, session_id="default"):
    # LIMPIEZA TOTAL
    # Borra todos los recursos asociados a la app
    namespace = namespace_from_session(session_id)
    outputs = []

    code, out, err = run_command(
        [
            "kubectl",
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
            "kubectl",
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
            "kubectl",
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
            "kubectl",
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


def get_cluster_nodes():
    code, out, err = run_command(["kubectl", "get", "nodes", "-o", "wide"])

    if code != 0:
        return False, err if err else out

    return True, out


def get_cluster_pods():
    code, out, err = run_command(["kubectl", "get", "pods", "-A"])

    if code != 0:
        return False, err if err else out

    return True, out
