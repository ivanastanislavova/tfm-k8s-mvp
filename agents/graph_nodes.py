"""
Define los agentes del sistema (LangGraph).

Incluye:
- Deploy de apps
- Observación y diagnóstico
- Creación de clúster (plan, provision, execute)

Cada nodo representa una acción del sistema.
"""

import os

import time

# Se usa en la espera dinámica entre comprobaciones del estado de los pods.

import difflib

# Librería estándar para comparar strings parecidos.
# Aquí se usa para detectar typos simples como "ngiinx" -> "nginx".

import subprocess

# Se usa para ejecutar comandos de shell, como kubectl o scripts de provisioning.

from core.state import AgentState

# Estado compartido entre todos los agentes.
# Cada nodo lo recibe, lo modifica y lo devuelve.

from k8s.yaml_generator import write_yaml_files

# Función que genera y guarda los YAMLs (Deployment, Service, ConfigMap, Ingress).

from llm.llm_yaml_generator import generate_yaml_with_llm

# Función que genera YAMLs usando un LLM (opcional, no determinista).

from provisioning.cloud_provisioner import provision_infrastructure

from core.metrics import timed_node

from k8s.k8s_utils import (
    deploy_files,
    get_pods_output,
    delete_app_resources,
    scale_deployment,
    get_deployment_status,
    get_pod_logs,
    describe_pod,
)

# Funciones auxiliares que interactúan con Kubernetes usando kubectl.

from agents.llm_agents import diagnose_with_llm, suggest_fix_with_llm

# Aquí están las funciones que sí usan IA/LLM:
# - diagnose_with_llm: diagnóstico asistido por LLM
# - suggest_fix_with_llm: sugerencia de corrección asistida por LLM
# Estas funciones se llaman desde los nodos de diagnóstico y reparación cuando no hay una solución determinista clara.


KNOWN_IMAGES = ["nginx", "httpd", "mongo", "redis", "postgres", "busybox"]
# Lista de imágenes conocidas y "seguras" para detectar typos simples.
# Esto hace que parte de la remediación sea determinista y no dependa del LLM.

from agents.cluster_script_generator import generate_cluster_artifacts
from provisioning.cluster_executor import execute_cluster_provisioning

from builders.python_app_builder import build_python_app


def suggest_known_image(image: str):
    # Busca si la imagen escrita por el usuario se parece mucho
    # a alguna imagen conocida.
    matches = difflib.get_close_matches(image, KNOWN_IMAGES, n=1, cutoff=0.8)
    if matches:
        return matches[0]
    return None


@timed_node("validate")
def validate_node(state: AgentState):
    print("\n[AGENT] Validator Agent\n")
    # Agente determinista.
    # Su función es revisar si la entrada del usuario tiene sentido antes de ejecutar nada.

    intent = state["intent"]

    # Validación específica para create_cluster
    if intent == "create_cluster":
        masters = state.get("masters", 0)
        workers = state.get("workers", 0)

        if masters < 1:
            state["has_error"] = True
            state["diagnosis"] = "invalid_input"
            state["reason"] = "masters must be >= 1"
            state["history"].append("Validator: masters inválidos")
            print("masters inválidos (debe ser >= 1).\n")
            return state

        if workers < 1:
            state["has_error"] = True
            state["diagnosis"] = "invalid_input"
            state["reason"] = "workers must be >= 1"
            state["history"].append("Validator: workers inválidos")
            print("workers inválidos (debe ser >= 1).\n")
            return state

        print("Validación correcta para create_cluster.\n")
        state["has_error"] = False
        state["history"].append("Validator: validación correcta para cluster")
        return state

    # Para otros intents, validación normal
    app_name = state["app_name"].strip()
    image = state["image"].strip()
    replicas = state["replicas"]
    port = state["port"]
    service_type = state["service_type"]

    # Validación básica del nombre de la app
    if not app_name:
        state["has_error"] = True
        state["diagnosis"] = "invalid_input"
        state["reason"] = "app_name is empty"
        state["history"].append("Validator: app_name vacío")
        print("app_name vacío.\n")
        return state

    if " " in app_name:
        state["has_error"] = True
        state["diagnosis"] = "invalid_input"
        state["reason"] = "app_name contains spaces"
        state["history"].append("Validator: app_name con espacios")
        print("app_name contiene espacios.\n")
        return state

    # Si la intención es deploy, validamos también image, replicas, port y service_type
    if intent == "deploy":
        if not image:
            state["has_error"] = True
            state["diagnosis"] = "invalid_input"
            state["reason"] = "image is empty"
            state["history"].append("Validator: image vacía")
            print("image vacía.\n")
            return state

        if " " in image:
            state["has_error"] = True
            state["diagnosis"] = "invalid_input"
            state["reason"] = "image contains spaces"
            state["history"].append("Validator: image con espacios")
            print("image contiene espacios.\n")
            return state

        if replicas < 1:
            state["has_error"] = True
            state["diagnosis"] = "invalid_input"
            state["reason"] = "replicas must be >= 1"
            state["history"].append("Validator: replicas inválidas")
            print("replicas inválidas.\n")
            return state

        if port < 1:
            state["has_error"] = True
            state["diagnosis"] = "invalid_input"
            state["reason"] = "port must be >= 1"
            state["history"].append("Validator: puerto inválido")
            print("puerto inválido.\n")
            return state

        if service_type not in ["ClusterIP", "NodePort"]:
            state["has_error"] = True
            state["diagnosis"] = "invalid_input"
            state["reason"] = "invalid service_type"
            state["history"].append("Validator: service_type inválido")
            print("service_type inválido.\n")
            return state

    # Si la intención es scale, solo hace falta validar réplicas
    if intent == "scale":
        if replicas < 1:
            state["has_error"] = True
            state["diagnosis"] = "invalid_input"
            state["reason"] = "replicas must be >= 1"
            state["history"].append("Validator: replicas inválidas en scale")
            print("replicas inválidas.\n")
            return state

    print("Validación correcta.\n")
    state["has_error"] = False
    state["history"].append("Validator: validación correcta")
    return state


@timed_node("generate_yaml_template")
def generate_yaml_node(state: AgentState):
    print("\n[AGENT] YAML Generator Agent\n")
    # Agente generador de infraestructura.
    # En esta implementación es determinista: genera los YAML a partir del estado.

    deployment_yaml, service_yaml, configmap_yaml, ingress_yaml = write_yaml_files(
        state["app_name"],
        state["image"],
        state["replicas"],
        state["port"],
        state["service_type"],
        state["config_data"],
        state["use_ingress"],
        state["ingress_host"],
    )

    # Guardamos en el estado los YAMLs generados
    state["deployment_yaml"] = deployment_yaml
    state["service_yaml"] = service_yaml
    state["configmap_yaml"] = configmap_yaml
    state["ingress_yaml"] = ingress_yaml

    print("YAML generado.\n")
    state["history"].append("YAML Generator: YAML generado")

    # Añadimos trazas al historial para saber qué recursos se generaron
    if state["config_data"]:
        state["history"].append("YAML Generator: ConfigMap generado")

    if state["use_ingress"] and state["ingress_host"]:
        state["history"].append(
            f"YAML Generator: Ingress generado para host={state['ingress_host']}"
        )

    return state


@timed_node("deploy_kubectl")
def deploy_node(state: AgentState):
    print("\n[AGENT] Execution Agent\n")
    # Agente de ejecución.
    # Se encarga de aplicar los manifiestos al clúster usando kubectl.

    success, output = deploy_files()

    state["history"].append(
        f"Execution: intento de despliegue app={state['app_name']}, image={state['image']}, replicas={state['replicas']}, port={state['port']}, service_type={state['service_type']}"
    )

    if not success:
        state["has_error"] = True
        state["diagnosis"] = "deployment_failed"
        state["reason"] = "kubectl apply failed"
        state["observation"] = output
        state["history"].append("Execution: kubectl apply falló")
    else:
        state["history"].append("Execution: kubectl apply correcto")

    return state


@timed_node("scale")
def scale_node(state: AgentState):
    print("\n[AGENT] Scale Agent\n")
    # Agente especializado para escalar una aplicación ya existente.

    success, output = scale_deployment(state["app_name"], state["replicas"])
    print(output)

    if not success:
        state["has_error"] = True
        state["diagnosis"] = "scale_failed"
        state["reason"] = "kubectl scale failed"
        state["observation"] = output
        state["history"].append("Scale: kubectl scale falló")
    else:
        state["history"].append(
            f"Scale: deployment {state['app_name']} escalado a {state['replicas']} replicas"
        )

    return state


@timed_node("delete")
def delete_node(state: AgentState):
    print("\n[AGENT] Delete Agent\n")
    # Agente que elimina todos los recursos asociados a la app.

    delete_app_resources(state["app_name"])
    state["diagnosis"] = "deleted"
    state["reason"] = "resources deleted"
    state["history"].append(f"Delete: recursos de {state['app_name']} eliminados")
    return state


@timed_node("status")
def status_node(state: AgentState):
    print("\n[AGENT] Status Agent\n")

    success, output = get_deployment_status(state["app_name"])
    print(output)

    state["observation"] = output
    state["history"].append(f"Status: estado consultado para {state['app_name']}")

    if not success:
        state["has_error"] = True
        state["diagnosis"] = "status_failed"
        state["reason"] = "kubectl get deployment failed"
        return state

    if "READY" in output and "AVAILABLE" in output:
        lines = output.strip().splitlines()

        if len(lines) >= 2:
            parts = lines[1].split()
            ready = parts[1] if len(parts) > 1 else ""

            try:
                ready_count, desired_count = ready.split("/")
                ready_count = int(ready_count)
                desired_count = int(desired_count)
            except Exception:
                state["diagnosis"] = "unknown"
                state["reason"] = f"Deployment ready value could not be parsed: {ready}"
                state["has_error"] = True
                return state

            if ready_count == desired_count and desired_count > 0:
                state["diagnosis"] = "healthy"
                state["reason"] = f"Deployment status ready: {ready}"
                state["has_error"] = False
            else:
                state["diagnosis"] = "creating"
                state["reason"] = f"Deployment not fully ready: {ready}"
                state["has_error"] = False

            return state

    state["diagnosis"] = "unknown"
    state["reason"] = "Deployment status could not be interpreted"
    state["has_error"] = True

    return state


@timed_node("observe_kubernetes")
def observe_node(state: AgentState):
    print("\n[AGENT] Monitor Agent\n")
    print("Observando el estado de Kubernetes con espera dinámica...\n")

    max_attempts = 5
    wait_seconds = 2

    final_output = ""

    for attempt in range(max_attempts):
        success, output = get_pods_output(state["app_name"])
        final_success = success
        final_output = output

        print(output)

        if not success:
            state["has_error"] = True
            state["diagnosis"] = "cluster_unreachable"
            state["reason"] = "kubectl get pods failed"
            state["observation"] = output
            state["history"].append("Monitor: clúster inaccesible")
            return state

        cleaned_output = output.strip()

        if cleaned_output != "":
            state["observation"] = output

            if (
                "Running" in output
                or "ErrImagePull" in output
                or "ImagePullBackOff" in output
                or "CrashLoopBackOff" in output
            ):
                break

        if attempt < max_attempts - 1:
            time.sleep(wait_seconds)

    state["observation"] = final_output
    state["history"].append(
        f"Stabilization: checking pod readiness for app={state['app_name']}"
    )

    if final_output.strip() == "":
        state["has_error"] = True
        state["diagnosis"] = "unknown"
        state["reason"] = "no pods found for app"
        state["history"].append("Monitor: no se encontraron pods de la app")

    return state


@timed_node("diagnose_deterministic")
def diagnose_node(state: AgentState):
    print("\n[AGENT] Diagnosis Agent\n")
    # Este agente interpreta lo observado.
    # Aquí tienes una estrategia híbrida:
    # 1) primero reglas deterministas
    # 2) si no basta, opcionalmente LLM

    # Si ya venimos con un diagnóstico claro de otro agente, no hace falta reinterpretar
    if state["diagnosis"] in [
        "deployment_failed",
        "cluster_unreachable",
        "invalid_input",
        "scale_failed",
        "status_failed",
        "deleted",
        "logs_failed",
        "describe_failed",
        "logs_ready",
        "describe_ready",
    ]:
        print(f"Diagnóstico: {state['diagnosis']}")
        print(f"Razón: {state['reason']}\n")
        state["history"].append(f"Diagnosis: {state['diagnosis']} - {state['reason']}")
        return state

    observation = state["observation"].strip()

    # Si no hay observación, no se puede razonar bien
    if observation == "":
        state["diagnosis"] = "unknown"
        state["reason"] = "empty observation"
        state["has_error"] = True
        print("Diagnóstico: unknown")
        print("Razón: empty observation\n")
        state["history"].append("Diagnosis: observación vacía")
        return state

    # Regla determinista para error de imagen
    if "ErrImagePull" in observation or "ImagePullBackOff" in observation:
        state["diagnosis"] = "image_pull_error"
        state["reason"] = "image pull failed"
        state["has_error"] = True
        print(f"Diagnóstico: {state['diagnosis']}")
        print(f"Razón: {state['reason']}\n")
        state["history"].append(f"Diagnosis: {state['diagnosis']} - {state['reason']}")
        return state

    # Regla determinista para crash loop
    if "CrashLoopBackOff" in observation:
        state["diagnosis"] = "crash_loop"
        state["reason"] = "container crash loop detected"
        state["has_error"] = True
        print(f"Diagnóstico: {state['diagnosis']}")
        print(f"Razón: {state['reason']}\n")
        state["history"].append(f"Diagnosis: {state['diagnosis']} - {state['reason']}")
        return state

    # Regla determinista para estado aún en creación
    if "Pending" in observation or "ContainerCreating" in observation:
        state["diagnosis"] = "creating"
        state["reason"] = "pods are still starting"
        state["has_error"] = False
        print(f"Diagnóstico: {state['diagnosis']}")
        print(f"Razón: {state['reason']}\n")
        state["history"].append("Stabilization: pods are still starting")
        return state

    # Caso sano por reglas
    if (
        "Running" in observation
        and "0/1" not in observation
        and "Err" not in observation
        and "BackOff" not in observation
    ):
        state["diagnosis"] = "healthy"
        state["reason"] = "pods running"
        state["has_error"] = False
        print(f"Diagnóstico: {state['diagnosis']}")
        print(f"Razón: {state['reason']}\n")
        state["history"].append(f"Diagnosis: {state['diagnosis']} - {state['reason']}")
        return state

    # Solo si no hay patrón claro, se apoya en IA
    # Este es uno de los puntos donde sí entra el LLM
    result = diagnose_with_llm(
        user_request=state["user_request"],
        app_name=state["app_name"],
        image=state["image"],
        replicas=state["replicas"],
        observation=observation,
    )

    if result:
        state["diagnosis"] = result["diagnosis"]
        state["reason"] = result["reason"]
        state["has_error"] = result["diagnosis"] != "healthy"

        print(f"Diagnóstico: {state['diagnosis']}")
        print(f"Razón: {state['reason']}\n")

        state["history"].append(f"Diagnosis: {state['diagnosis']} - {state['reason']}")
    else:
        state["diagnosis"] = "unknown"
        state["reason"] = "LLM diagnosis failed"
        state["has_error"] = True
        print("Diagnóstico: unknown")
        print("Razón: fallo al obtener diagnóstico del LLM\n")
        state["history"].append("Diagnosis: fallo en el LLM")

    return state


@timed_node("repair_deterministic")
def repair_node(state: AgentState):
    print("\n[AGENT] Remediation Agent\n")
    # Este es el agente que intenta corregir errores automáticamente.
    # Es uno de los más "agentic" porque actúa sobre el problema.

    # Protección contra bucles infinitos
    if state["retries"] >= state["max_retries"]:
        print("Se alcanzó el número máximo de reintentos.\n")
        state["history"].append("Remediation: máximo de reintentos alcanzado")
        return state

    # Primera capa: corrección determinista usando similitud con imágenes conocidas
    if state["diagnosis"] == "image_pull_error":
        candidate = suggest_known_image(state["image"])

        if candidate and candidate != state["image"]:
            old_app_name = state["app_name"]
            old_image = state["image"]

            print(f"Aplicando corrección por similitud: {old_image} -> {candidate}\n")

            state["image"] = candidate
            state["app_name"] = candidate
            # Ojo: aquí además cambiamos app_name para mantener coherencia de labels/recursos

            delete_app_resources(old_app_name)
            # Borramos recursos antiguos antes de redeploy

            state["retries"] += 1
            state["history"].append(
                f"Remediation: corrected image {old_image} -> {candidate}"
            )
            return state

    # Segunda capa: si la regla no basta, pedir sugerencia al LLM
    # Este es el otro gran punto donde sí entra la IA
    suggestion = suggest_fix_with_llm(
        app_name=state["app_name"],
        image=state["image"],
        replicas=state["replicas"],
        diagnosis=state["diagnosis"],
        reason=state["reason"],
        observation=state["observation"],
    )

    if not suggestion:
        print("No se pudo obtener una corrección del LLM.\n")
        state["retries"] += 1
        state["history"].append("Remediation: sin sugerencia del LLM")
        return state

    new_app_name = suggestion.get("app_name", state["app_name"])
    new_image = suggestion.get("image", state["image"])
    new_replicas = suggestion.get("replicas", state["replicas"])

    # Comprobamos si la sugerencia cambia algo de verdad
    changed = (
        new_app_name != state["app_name"]
        or new_image != state["image"]
        or new_replicas != state["replicas"]
    )

    if changed:
        old_app_name = state["app_name"]
        old_image = state["image"]
        old_replicas = state["replicas"]

        print("Aplicando corrección sugerida por el agente...\n")
        print(f"app_name: {old_app_name} -> {new_app_name}")
        print(f"image: {old_image} -> {new_image}")
        print(f"replicas: {old_replicas} -> {new_replicas}\n")

        state["app_name"] = new_app_name
        state["image"] = new_image
        state["replicas"] = new_replicas

        delete_app_resources(old_app_name)

        state["retries"] += 1
        state["history"].append(
            f"Remediation: corrected app={old_app_name}->{new_app_name}, image={old_image}->{new_image}, replicas={old_replicas}->{new_replicas}"
        )
    else:
        # Si no hay corrección útil, no se cambia nada
        print("No hay corrección segura. Se mantienen los valores actuales.\n")
        state["retries"] += 1
        state["history"].append("Remediation: sin cambios")

    return state


@timed_node("show_yaml")
def show_yaml_node(state: AgentState):
    print("\n[AGENT] YAML Viewer Agent\n")
    # Agente de visualización.
    # No despliega nada: solo devuelve los YAML ya generados.

    parts = []

    if state["deployment_yaml"]:
        parts.append("# deployment.yaml\n" + state["deployment_yaml"])

    if state["service_yaml"]:
        parts.append("# service.yaml\n" + state["service_yaml"])

    if state["configmap_yaml"]:
        parts.append("# configmap.yaml\n" + state["configmap_yaml"])

    if state["ingress_yaml"]:
        parts.append("# ingress.yaml\n" + state["ingress_yaml"])

    state["observation"] = "\n\n".join(parts)
    state["diagnosis"] = "yaml_ready"
    state["reason"] = "generated YAML returned"
    state["history"].append("YAML Viewer: YAML mostrado")
    return state


@timed_node("show_logs")
def show_logs_node(state: AgentState):
    print("\n[AGENT] Logs Agent\n")
    # Observabilidad: consulta logs del pod activo

    success, output = get_pod_logs(state["app_name"])
    print(output)

    state["logs_output"] = output
    state["observation"] = output
    state["history"].append(f"Logs: logs consultados para {state['app_name']}")

    if not success:
        state["has_error"] = True
        state["diagnosis"] = "logs_failed"
        state["reason"] = "kubectl logs failed"
    else:
        state["diagnosis"] = "logs_ready"
        state["reason"] = "pod logs returned"

    return state


@timed_node("describe_pod")
def describe_pod_node(state: AgentState):
    print("\n[AGENT] Describe Agent\n")
    # Observabilidad: describe del pod activo

    success, output = describe_pod(state["app_name"])
    print(output)

    state["describe_output"] = output
    state["observation"] = output
    state["history"].append(f"Describe: describe consultado para {state['app_name']}")

    if not success:
        state["has_error"] = True
        state["diagnosis"] = "describe_failed"
        state["reason"] = "kubectl describe pod failed"
    else:
        state["diagnosis"] = "describe_ready"
        state["reason"] = "pod description returned"

    return state


@timed_node("cluster_plan")
def cluster_plan_node(state: AgentState):
    print("\n[AGENT] Cluster Provisioning Agent\n")

    # Usar los parámetros ya parseados del state
    params = {
        "masters": state["masters"],
        "workers": state["workers"],
        "cni": "calico",
        "kubernetes_version": "v1.30",
        "container_runtime": "containerd",
    }

    print(f"Parámetros del clúster: {params}\n")
    state["history"].append(f"Cluster Provisioning: parámetros {params}")

    cluster_plan, master_script, worker_script, virtualbox_script, inventory = (
        generate_cluster_artifacts(params)
    )

    state["cluster_plan"] = cluster_plan
    state["master_script"] = master_script
    state["worker_script"] = worker_script
    state["virtualbox_script"] = virtualbox_script
    state["cluster_inventory"] = inventory
    state["diagnosis"] = "cluster_plan_ready"
    state["reason"] = (
        "cluster provisioning plan and scripts generated from LLM parameters"
    )

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(BASE_DIR, "generated_cluster")
    os.makedirs(output_dir, exist_ok=True)

    with open(os.path.join(output_dir, "master_setup.sh"), "w", encoding="utf-8") as f:
        f.write(master_script)

    with open(os.path.join(output_dir, "worker_setup.sh"), "w", encoding="utf-8") as f:
        f.write(worker_script)

    with open(os.path.join(output_dir, "create_vms.ps1"), "w", encoding="utf-8") as f:
        f.write(virtualbox_script)

    state["observation"] = (
        "Scripts generated in generated_cluster/: "
        "create_vms.ps1, master_setup.sh and worker_setup.sh"
    )
    state["history"].append(
        "Cluster Provisioning: scripts guardados en generated_cluster/"
    )

    print("Plan de clúster generado.\n")

    return state


@timed_node("cluster_status")
def cluster_status_node(state: AgentState):
    print("\n[AGENT] Cluster Status Agent\n")

    provider = state.get("provider", "minikube")

    try:
        if provider == "minikube":
            result = subprocess.run(
                "kubectl get nodes", capture_output=True, text=True, shell=True
            )

            output = result.stdout + result.stderr

            state["observation"] = output

            if "Ready" in output:
                state["diagnosis"] = "healthy"
                state["reason"] = "Minikube cluster funcionando correctamente"
            else:
                state["diagnosis"] = "unhealthy"
                state["reason"] = "El cluster no está listo"

        else:
            # tu lógica actual de Oracle
            pass

        state["history"].append("Cluster Status: comprobación realizada")

    except Exception as e:
        state["has_error"] = True
        state["diagnosis"] = "status_failed"
        state["reason"] = str(e)

    return state


@timed_node("cluster_execute")
def cluster_execute_node(state: AgentState):
    print("\n[AGENT] Cluster Execution Agent\n")

    inventory_path = state.get(
        "cluster_inventory_path", "generated_cluster/inventory.json"
    )

    print("Ejecutando provisioning de Kubernetes sobre infraestructura cloud...")

    success, logs, join_command = execute_cluster_provisioning(inventory_path)

    if success:
        state["has_error"] = False
        state["diagnosis"] = "cluster_created"
        state["reason"] = "Cluster Kubernetes creado exitosamente"
        state["observation"] = logs
        state["history"].append("Cluster Execute: cluster creado exitosamente")
    else:
        state["has_error"] = True
        state["diagnosis"] = "cluster_provisioning_failed"
        state["reason"] = "Falló el provisioning de Kubernetes"
        state["observation"] = logs
        state["history"].append("Cluster Execute: provisioning falló")

    if state.get("provider") == "minikube":
        subprocess.run("kubectl create deployment nginx --image=nginx", shell=True)

    if join_command:
        state["history"].append("Cluster Execute: join command generado y usado")

    print(f"Diagnóstico: {state['diagnosis']}")
    print(f"Razón: {state['reason']}\n")

    return state


@timed_node("cloud_provision")
def cloud_provision_node(state: AgentState):
    print("\n[AGENT] Cloud Provisioner Agent\n")

    provider = state.get("provider", "minikube")

    params = {
        "provider": provider,
        "masters": state["masters"],
        "workers": state["workers"],
        "ssh_user": "ubuntu",
        "cpus": 2,
        "memory": 4096,
    }

    try:
        inventory, inventory_path, logs = provision_infrastructure(params)

        state["cluster_inventory"] = inventory
        state["cluster_inventory_path"] = inventory_path
        state["observation"] = logs

        state["diagnosis"] = "infrastructure_ready"
        state["reason"] = f"Infrastructure ready using provider={provider}"

        state["history"].append(
            f"Cloud Provisioner: infraestructura preparada con provider={provider}"
        )

    except Exception as e:
        state["has_error"] = True
        state["diagnosis"] = "infrastructure_failed"
        state["reason"] = str(e)
        state["observation"] = str(e)
        state["history"].append(
            "Cloud Provisioner: fallo al provisionar infraestructura"
        )

    return state


@timed_node("build_python_app")
def build_python_app_node(state: AgentState):
    print("\n[AGENT] Python App Builder Agent\n")

    success, image_name, output = build_python_app(
        state["app_name"], state["python_file"]
    )

    state["observation"] = output

    if not success:
        state["has_error"] = True
        state["diagnosis"] = "build_failed"
        state["reason"] = "Python app Docker build failed"
        state["history"].append("Python Builder: build falló")
        return state

    state["image"] = image_name
    state["diagnosis"] = "build_ready"
    state["reason"] = "Python app image built"
    state["history"].append(f"Python Builder: imagen generada {image_name}")

    return state


@timed_node("generate_yaml_llm")
def generate_llm_yaml_node(state: AgentState):
    print("\n[AGENT] LLM YAML Generator Agent\n")

    success, yaml_text, message = generate_yaml_with_llm(
        user_request=state["user_request"],
        app_name=state["app_name"],
        image=state["image"],
        replicas=state["replicas"],
        port=state["port"],
        service_type=state["service_type"],
        llm_model=state.get("llm_model", "llama3.2:3b"),
    )

    state["llm_generated_yaml"] = yaml_text
    state["deployment_yaml"] = yaml_text
    state["observation"] = message

    if not success:
        state["has_error"] = True
        state["diagnosis"] = "llm_yaml_invalid"
        state["reason"] = message
        state["history"].append("LLM YAML Generator: YAML inválido")
        return state

    with open("llm_generated.yaml", "w", encoding="utf-8") as f:
        f.write(yaml_text)

    state["has_error"] = False
    state["diagnosis"] = "llm_yaml_ready"
    state["reason"] = "YAML generated completely by LLM"
    state["history"].append("LLM YAML Generator: YAML generado por IA")

    print("YAML generado completamente por el LLM.\n")

    return state


@timed_node("deploy_llm_yaml")
def deploy_llm_yaml_node(state: AgentState):
    print("\n[AGENT] LLM YAML Execution Agent\n")

    dry_run = subprocess.run(
        "kubectl apply --dry-run=client -f llm_generated.yaml",
        shell=True,
        capture_output=True,
        text=True,
    )

    if dry_run.returncode != 0:
        state["has_error"] = True
        state["diagnosis"] = "llm_yaml_dry_run_failed"
        state["reason"] = "kubectl dry-run failed"
        state["observation"] = dry_run.stdout + dry_run.stderr
        state["history"].append("LLM YAML Execution: dry-run falló")
        return state

    result = subprocess.run(
        "kubectl apply -f llm_generated.yaml",
        shell=True,
        capture_output=True,
        text=True,
    )

    state["observation"] = result.stdout + result.stderr

    if result.returncode != 0:
        state["has_error"] = True
        state["diagnosis"] = "deployment_failed"
        state["reason"] = "kubectl apply failed"
        state["history"].append("LLM YAML Execution: kubectl apply falló")
    else:
        state["has_error"] = False
        state["history"].append("LLM YAML Execution: kubectl apply correcto")

    return state
