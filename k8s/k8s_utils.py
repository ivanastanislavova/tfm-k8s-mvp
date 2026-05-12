import os
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


def deploy_files():
    # AGENTE DE EJECUCIÓN REAL
    # Aplica los YAMLs al cluster con kubectl

    success = True
    combined_output = []

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
        code, out, err = run_command(["kubectl", "apply", "-f", file_name])

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


def get_pods_output(app_name):
    # MONITORING
    # Obtiene los pods de tu app

    code, out, err = run_command(
        [
            "kubectl",
            "get",
            "pods",
            "-l",
            f"app={app_name}",  # selector por label
            "--no-headers",
        ]
    )

    if code != 0:
        return False, err if err else out

    return True, out


def get_first_pod_name(app_name):
    # UTILIDAD INTERNA
    # Saca el nombre del primer pod

    code, out, err = run_command(
        [
            "kubectl",
            "get",
            "pods",
            "-l",
            f"app={app_name}",
            "-o",
            "jsonpath={.items[0].metadata.name}",  # para extraer nombre
        ]
    )

    if code != 0 or not out.strip():
        return False, err if err else out

    return True, out.strip()


def get_pod_logs(app_name):
    # OBSERVABILIDAD: logs

    ok, pod_name = get_first_pod_name(app_name)
    if not ok:
        return False, pod_name

    code, out, err = run_command(["kubectl", "logs", pod_name])

    if code != 0:
        return False, err if err else out

    return True, out


def describe_pod(app_name):
    # OBSERVABILIDAD: describe

    ok, pod_name = get_first_pod_name(app_name)
    if not ok:
        return False, pod_name

    code, out, err = run_command(["kubectl", "describe", "pod", pod_name])

    if code != 0:
        return False, err if err else out

    return True, out


def scale_deployment(app_name, replicas):
    # ESCALADO dinámico

    code, out, err = run_command(
        [
            "kubectl",
            "scale",
            "deployment",
            f"{app_name}-deployment",
            f"--replicas={replicas}",
        ]
    )

    if code != 0:
        return False, err if err else out

    return True, out


def get_deployment_status(app_name):
    # CONSULTAR estado del deployment

    code, out, err = run_command(
        ["kubectl", "get", "deployment", f"{app_name}-deployment"]
    )

    if code != 0:
        return False, err if err else out

    return True, out


def delete_app_resources(app_name):
    # LIMPIEZA TOTAL
    # Borra todos los recursos asociados a la app

    run_command(
        [
            "kubectl",
            "delete",
            "deployment",
            f"{app_name}-deployment",
            "--ignore-not-found",  # evita errores si no existe
        ]
    )

    run_command(
        ["kubectl", "delete", "service", f"{app_name}-service", "--ignore-not-found"]
    )

    run_command(
        ["kubectl", "delete", "configmap", f"{app_name}-config", "--ignore-not-found"]
    )

    run_command(
        ["kubectl", "delete", "ingress", f"{app_name}-ingress", "--ignore-not-found"]
    )


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
