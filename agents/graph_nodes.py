"""
Define the system agents (LangGraph).

Includes:
App deployment
Observation and diagnosis
Cluster creation (plan, provision, execute)

Each node represents a system action.
"""

import json
import os
import re

import time

# Used for dynamic waits between pod status checks.

import difflib

# Standard library for comparing similar strings.
# Used to detect simple typos such as "ngiinx" -> "nginx".

import subprocess

# Used to run shell commands, such as kubectl or provisioning scripts.

from core.state import AgentState

# Estado compartido entre todos los agentes.
# Cada nodo lo recibe, lo modifica y lo devuelve.

from k8s.yaml_generator import write_yaml_files

# Generates and stores Kubernetes manifests.

from llm.llm_yaml_generator import generate_yaml_with_llm
from langchain_core.messages import HumanMessage
from llm.llm_provider import get_llm

# Optional non-deterministic LLM YAML generation.

from provisioning.cloud_provisioner import (
    minikube_profile_from_session,
    read_minikube_status,
    provision_infrastructure,
    write_minikube_status,
)

from core.metrics import timed_node

from k8s.k8s_utils import (
    deploy_files,
    get_pods_output,
    delete_app_resources,
    scale_deployment,
    get_deployment_status,
    get_rollout_status,
    get_pod_logs,
    describe_pod,
    list_deployments,
    get_service_details,
    get_deployment_container_port,
    get_cluster_nodes,
    get_minikube_profile_status,
    get_kubectl_context,
    kubectl_command,
    ensure_namespace,
    namespace_from_session,
)

# Helper functions that interact with Kubernetes through kubectl.

from agents.llm_agents import diagnose_with_llm, suggest_fix_with_llm

# LLM-assisted helper functions:
# - diagnose_with_llm: LLM-assisted diagnosis
# - suggest_fix_with_llm: LLM-assisted repair suggestion
# Called from diagnosis and repair nodes when deterministic logic is not enough.


KNOWN_IMAGES = ["nginx", "httpd", "mongo", "redis", "postgres", "busybox"]
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERATED_MANIFESTS_DIR = os.path.join(PROJECT_ROOT, "generated", "manifests")
# Known safe images used for simple typo detection.
# This keeps simple remediation deterministic and independent from the LLM.

from agents.cluster_script_generator import generate_cluster_artifacts
from provisioning.cluster_executor import execute_cluster_provisioning

from builders.python_app_builder import build_python_app


def clean_infrastructure_log(text: str):
    cleaned = re.sub(r"\x1b\[[0-9;]*m", "", text or "")
    cleaned = re.sub(r"ssh-(?:ed25519|rsa)\s+[A-Za-z0-9+/=]+(?:\s+[^\n]+)?", "[redacted ssh public key]", cleaned)
    cleaned = re.sub(r"ocid1\.[A-Za-z0-9._-]+", "[redacted ocid]", cleaned)
    return cleaned


def summarize_infrastructure_failure(provider: str, raw_log: str):
    log = clean_infrastructure_log(raw_log)
    if provider in ["terraform", "oracle"] and "Out of host capacity" in log:
        return (
            "Terraform connected to OCI and initialized correctly, but OCI returned "
            "'Out of host capacity' while creating the free-tier VM. This is a cloud "
            "capacity limitation, not a local Terraform or application error."
        ), log

    return str(raw_log), log


def suggest_known_correction(image: str):
    # Check whether the user-provided image closely resembles a known image.
    # a known image.
    if is_explicit_image_reference(image):
        return None

    normalized = (image or "").split(":", 1)[0].lower().strip()
    if normalized in KNOWN_IMAGES:
        return {"app_name": normalized, "image": f"{normalized}:latest"}

    matches = difflib.get_close_matches(normalized, KNOWN_IMAGES, n=1, cutoff=0.78)
    if matches:
        candidate = matches[0]
        return {"app_name": candidate, "image": f"{candidate}:latest"}
    return None


def is_explicit_image_reference(image: str):
    image = (image or "").strip()
    if not image:
        return False

    # Images with a registry/repository path or an explicit tag are treated
    # as intentional user input. If they fail to pull, the system reports the
    # error instead of replacing them with an unrelated public image.
    return "/" in image or ":" in image


def summarize_image_pull_failure(text: str):
    lower = (text or "").lower()
    if "unauthorized" in lower or "authentication required" in lower:
        return (
            "image pull failed for the explicit image reference: registry "
            "authentication or authorization is required"
        )
    if "not found" in lower or "manifest unknown" in lower:
        return (
            "image pull failed for the explicit image reference: image or tag "
            "was not found in the registry"
        )
    if "tls" in lower or "certificate" in lower:
        return (
            "image pull failed for the explicit image reference: registry TLS "
            "or certificate validation failed"
        )
    if "i/o timeout" in lower or "connection refused" in lower or "no such host" in lower:
        return (
            "image pull failed for the explicit image reference: registry "
            "network access failed"
        )
    return (
        "image pull failed for the explicit image reference; no safe automatic "
        "replacement was applied"
    )


def suggest_known_image(image: str):
    correction = suggest_known_correction(image)
    return correction["image"] if correction else None


@timed_node("validate")
def validate_node(state: AgentState):
    print("\n[AGENT] Validator Agent\n")
    # Deterministic agent.
    # Validates the user input before executing any infrastructure action.

    intent = state["intent"]

    if intent == "protected_cluster_operation":
        state["has_error"] = False
        state["diagnosis"] = "cluster_operation_requires_confirmation"
        state["reason"] = (
            "Cluster and node deletion is not available from the chat. "
            "Use the session delete control to remove the associated cluster resources."
        )
        state["observation"] = state["reason"]
        state["history"].append("Validator: destructive cluster operation blocked")
        return state

    if intent in ["list_deployments", "answer_contextual_question", "answer_question"]:
        state["has_error"] = False
        state["history"].append(f"Validator: validation successful for {intent}")
        return state

    # Specific validation for create_cluster.
    if intent == "create_cluster":
        masters = state.get("masters", 0)
        workers = state.get("workers", 0)

        if masters < 1:
            state["has_error"] = True
            state["diagnosis"] = "invalid_input"
            state["reason"] = "masters must be >= 1"
            state["history"].append("Validator: invalid masters")
            print("Invalid masters value; it must be >= 1.\n")
            return state

        if workers < 1:
            state["has_error"] = True
            state["diagnosis"] = "invalid_input"
            state["reason"] = "workers must be >= 1"
            state["history"].append("Validator: invalid workers")
            print("Invalid workers value; it must be >= 1.\n")
            return state

        print("Validation successful for create_cluster.\n")
        state["has_error"] = False
        state["history"].append("Validator: validation successful for cluster")
        return state

    # Standard validation for other intents.
    app_name = state["app_name"].strip()
    image = state["image"].strip()
    replicas = state["replicas"]
    port = state["port"]
    service_type = state["service_type"]

    # Basic application-name validation.
    if not app_name:
        state["has_error"] = True
        state["diagnosis"] = "invalid_input"
        state["reason"] = "app_name is empty"
        state["history"].append("Validator: empty app_name")
        print("app_name is empty.\n")
        return state

    if " " in app_name:
        state["has_error"] = True
        state["diagnosis"] = "invalid_input"
        state["reason"] = "app_name contains spaces"
        state["history"].append("Validator: app_name contains spaces")
        print("app_name contains spaces.\n")
        return state

    # Deploy requests also validate image, replicas, port, and service_type.
    if intent == "deploy":
        if not image:
            state["has_error"] = True
            state["diagnosis"] = "invalid_input"
            state["reason"] = "image is empty"
            state["history"].append("Validator: empty image")
            print("image is empty.\n")
            return state

        if " " in image:
            state["has_error"] = True
            state["diagnosis"] = "invalid_input"
            state["reason"] = "image contains spaces"
            state["history"].append("Validator: image contains spaces")
            print("image contains spaces.\n")
            return state

        if replicas < 1:
            state["has_error"] = True
            state["diagnosis"] = "invalid_input"
            state["reason"] = "replicas must be >= 1"
            state["history"].append("Validator: invalid replicas")
            print("Invalid replicas value.\n")
            return state

        if port < 1:
            state["has_error"] = True
            state["diagnosis"] = "invalid_input"
            state["reason"] = "port must be >= 1"
            state["history"].append("Validator: invalid port")
            print("Invalid port value.\n")
            return state

        if service_type not in ["ClusterIP", "NodePort"]:
            state["has_error"] = True
            state["diagnosis"] = "invalid_input"
            state["reason"] = "invalid service_type"
            state["history"].append("Validator: invalid service_type")
            print("Invalid service_type.\n")
            return state

    # Scale requests only need replica validation.
    if intent == "scale":
        if replicas < 1:
            state["has_error"] = True
            state["diagnosis"] = "invalid_input"
            state["reason"] = "replicas must be >= 1"
            state["history"].append("Validator: invalid replicas in scale")
            print("Invalid replicas value.\n")
            return state

    print("Validation successful.\n")
    state["has_error"] = False
    state["history"].append("Validator: validation successful")
    return state


@timed_node("generate_yaml_template")
def generate_yaml_node(state: AgentState):
    print("\n[AGENT] YAML Generator Agent\n")
    # Manifest generation agent.
    # In this implementation it deterministically generates YAML from state.
    if state["use_ingress"] and not state["ingress_host"]:
        state["ingress_host"] = f"{state['app_name']}.local"

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

    # Store generated YAML in the shared state.
    state["deployment_yaml"] = deployment_yaml
    state["service_yaml"] = service_yaml
    state["configmap_yaml"] = configmap_yaml
    state["ingress_yaml"] = ingress_yaml

    print("YAML generated.\n")
    state["history"].append("YAML Generator: YAML generated")

    # Store generated-resource traces in the technical history.
    if state["config_data"]:
        state["history"].append("YAML Generator: ConfigMap generated")

    if state["use_ingress"] and state["ingress_host"]:
        state["history"].append(
            f"YAML Generator: Ingress generated for host={state['ingress_host']}"
        )

    return state


@timed_node("deploy_kubectl")
def deploy_node(state: AgentState):
    print("\n[AGENT] Execution Agent\n")
    # Execution agent.
    # Applies manifests to the cluster through kubectl.

    success, output = deploy_files(state["session_id"])

    state["history"].append(
        f"Execution: intento de despliegue app={state['app_name']}, image={state['image']}, replicas={state['replicas']}, port={state['port']}, service_type={state['service_type']}"
    )

    if not success:
        state["has_error"] = True
        state["diagnosis"] = "deployment_failed"
        state["reason"] = output.strip() or "kubectl apply failed"
        state["observation"] = output
        state["history"].append("Execution: kubectl apply failed")
    else:
        state["history"].append("Execution: kubectl apply correcto")

    return state


@timed_node("scale")
def scale_node(state: AgentState):
    print("\n[AGENT] Scale Agent\n")
    # Agent specialized in scaling an existing application.

    success, output = scale_deployment(
        state["app_name"], state["replicas"], state["session_id"]
    )
    print(output)

    if not success:
        state["has_error"] = True
        state["diagnosis"] = "scale_failed"
        state["reason"] = "kubectl scale failed"
        state["observation"] = output
        state["history"].append("Scale: kubectl scale failed")
    else:
        state["history"].append(
            f"Scale: deployment {state['app_name']} escalado a {state['replicas']} replicas"
        )

    return state


@timed_node("delete")
def delete_node(state: AgentState):
    print("\n[AGENT] Delete Agent\n")
    # Agent that deletes all resources associated with the application.

    deleted, output = delete_app_resources(state["app_name"], state["session_id"])
    state["observation"] = output

    if deleted:
        state["diagnosis"] = "deleted"
        state["reason"] = "resources deleted"
        state["history"].append(f"Delete: resources for {state['app_name']} deleted")
    else:
        state["diagnosis"] = "not_found"
        state["reason"] = "resources not found in this session namespace"
        state["history"].append(
            f"Delete: no resources for {state['app_name']} in this session"
        )

    return state


@timed_node("status")
def status_node(state: AgentState):
    print("\n[AGENT] Status Agent\n")

    success, output = get_deployment_status(state["app_name"], state["session_id"])
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


@timed_node("list_deployments")
def list_deployments_node(state: AgentState):
    print("\n[AGENT] Deployment Inventory Agent\n")

    success, output = list_deployments(state["session_id"])
    print(output)

    state["observation"] = output
    state["history"].append("Inventory: deployments consultados")

    if not success:
        state["has_error"] = True
        state["diagnosis"] = "list_deployments_failed"
        state["reason"] = output.strip() or "kubectl get deployments failed"
        return state

    lines = [line for line in output.splitlines() if line.strip()]
    count = len(lines)

    state["has_error"] = False
    state["diagnosis"] = "deployments_listed"
    state["reason"] = f"Found {count} deployment{'s' if count != 1 else ''} in this session namespace"
    return state


@timed_node("answer_contextual_question")
def answer_contextual_question_node(state: AgentState):
    print("\n[AGENT] Context Answer Agent\n")

    last_intent = state.get("last_intent", "")
    last_observation = state.get("last_observation", "")

    if last_intent == "list_deployments" and last_observation.strip():
        names = []
        for line in last_observation.splitlines():
            parts = line.split()
            if parts:
                names.append(parts[0])

        if names:
            state["has_error"] = False
            state["diagnosis"] = "context_answered"
            state["reason"] = "They are: " + ", ".join(names)
            state["observation"] = "\n".join(f"- {name}" for name in names)
            state["history"].append("Context Answer: answered from last deployment list")
            return state

    state["has_error"] = True
    state["diagnosis"] = "context_missing"
    state["reason"] = "I do not have a previous result to answer that follow-up"
    state["observation"] = state.get("last_reason", "")
    state["history"].append("Context Answer: missing usable context")
    return state


@timed_node("answer_question")
def answer_question_node(state: AgentState):
    print("\n[AGENT] Conversational Answer Agent\n")

    llm = get_llm(state.get("llm_model", "llama3.2:3b"))
    prompt = f"""
You are KubeAgentFlow's conversational Kubernetes assistant.

Answer the user's question using only the context below.
Do not execute actions.
Do not claim that you changed, deployed, deleted, scaled, or created anything.
If the user asks for an action, say what command/action they should ask explicitly.
If the user asks how many replicas the current or selected application has and replicas is a positive integer, answer with that exact number.
Do not say replicas are unspecified when the current app context contains a numeric replicas value.
If the available context is not enough, say what information is missing.
Keep the answer concise.

User question:
{state["user_request"]}

Current app context:
- app_name: {state.get("app_name", "")}
- image: {state.get("image", "")}
- replicas: {state.get("replicas", "")}
- port: {state.get("port", "")}
- service_type: {state.get("service_type", "")}
- use_ingress: {state.get("use_ingress", "")}
- ingress_host: {state.get("ingress_host", "")}

Last system result:
- last_intent: {state.get("last_intent", "")}
- last_reason: {state.get("last_reason", "")}
- last_observation:
{state.get("last_observation", "")}

Recent chat history:
{state.get("chat_history", [])}
"""

    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        answer = response.content.strip()
    except Exception as e:
        answer = f"I could not answer with the LLM: {e}"
        state["has_error"] = True
        state["diagnosis"] = "answer_failed"
        state["reason"] = answer
        state["observation"] = answer
        state["history"].append("Conversational Answer: LLM failed")
        return state

    state["has_error"] = False
    state["diagnosis"] = "answer_ready"
    state["reason"] = answer
    state["observation"] = answer
    state["history"].append("Conversational Answer: answered with LLM context")
    return state


@timed_node("show_app_port")
def show_app_port_node(state: AgentState):
    print("\n[AGENT] Port Inspector Agent\n")

    app_name = state["app_name"]
    service_ok, service_output = get_service_details(app_name, state["session_id"])

    if service_ok and service_output.strip():
        parts = service_output.split()
        service_port = parts[0] if len(parts) > 0 else ""
        target_port = parts[1] if len(parts) > 1 else ""
        service_type = parts[2] if len(parts) > 2 else ""

        state["port"] = int(service_port) if service_port.isdigit() else state["port"]
        state["service_type"] = service_type or state["service_type"]
        state["has_error"] = False
        state["diagnosis"] = "port_found"
        state["reason"] = (
            f"{app_name}-service exposes port {service_port}"
            + (f" and targets container port {target_port}" if target_port else "")
            + (f" as {service_type}" if service_type else "")
        )
        state["observation"] = state["reason"]
        state["history"].append(f"Port Inspector: service port found for {app_name}")
        return state

    deployment_ok, deployment_output = get_deployment_container_port(
        app_name, state["session_id"]
    )

    if deployment_ok and deployment_output.strip():
        container_port = deployment_output.strip()
        state["port"] = int(container_port) if container_port.isdigit() else state["port"]
        state["has_error"] = False
        state["diagnosis"] = "port_found"
        state["reason"] = f"{app_name}-deployment container exposes port {container_port}"
        state["observation"] = state["reason"]
        state["history"].append(f"Port Inspector: deployment port found for {app_name}")
        return state

    state["has_error"] = True
    state["diagnosis"] = "port_not_found"
    state["reason"] = f"Could not find a Service or Deployment port for {app_name}"
    state["observation"] = service_output if service_output else deployment_output
    state["history"].append(f"Port Inspector: no port found for {app_name}")
    return state


@timed_node("observe_kubernetes")
def observe_node(state: AgentState):
    print("\n[AGENT] Monitor Agent\n")
    print("Observing Kubernetes state with dynamic wait.\n")

    rollout_success, rollout_output = get_rollout_status(
        state["app_name"], state["session_id"], timeout_seconds=20
    )

    if rollout_output:
        state["history"].append(f"Rollout: {rollout_output.strip()}")

    max_attempts = 5
    wait_seconds = 2

    final_output = ""

    for attempt in range(max_attempts):
        success, output = get_pods_output(state["app_name"], state["session_id"])
        final_output = output

        print(output)

        if not success:
            state["has_error"] = True
            state["diagnosis"] = "cluster_unreachable"
            state["reason"] = "kubectl get pods failed"
            state["observation"] = output
            state["history"].append("Monitor: cluster unreachable")
            return state

        cleaned_output = output.strip()

        if cleaned_output != "":
            state["observation"] = output

            if (
                "ErrImagePull" in output
                or "ImagePullBackOff" in output
                or "CrashLoopBackOff" in output
                or rollout_success
            ):
                break

        if attempt < max_attempts - 1:
            time.sleep(wait_seconds)

    state["observation"] = "\n".join(
        part for part in [rollout_output.strip(), final_output.strip()] if part
    )
    state["history"].append(
        f"Stabilization: checking pod readiness for app={state['app_name']}"
    )

    if final_output.strip() == "":
        message = (
            f"No pods found for app={state['app_name']} "
            f"in namespace {namespace_from_session(state['session_id'])}."
        )
        state["has_error"] = True
        state["diagnosis"] = "unknown"
        state["reason"] = message
        state["observation"] = message
        state["history"].append("Monitor: no se encontraron pods de la app")
    elif not rollout_success:
        state["has_error"] = True
        state["diagnosis"] = "rollout_failed"
        state["reason"] = rollout_output.strip() or "deployment rollout did not complete"
        state["history"].append("Monitor: rollout no completado")

    return state


@timed_node("diagnose_deterministic")
def diagnose_node(state: AgentState):
    print("\n[AGENT] Diagnosis Agent\n")
    # Este agente interpreta lo observado.
    # Hybrid diagnosis strategy:
    # 1) primero reglas deterministas
    # 2) si no basta, opcionalmente LLM

    # Reuse clear diagnoses from previous nodes instead of reinterpreting.
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
        print(f"Diagnosis: {state['diagnosis']}")
        print(f"Reason: {state['reason']}\n")
        state["history"].append(f"Diagnosis: {state['diagnosis']} - {state['reason']}")
        return state

    observation = state["observation"].strip()

    # Diagnosis cannot proceed reliably without an observation.
    if observation == "":
        state["diagnosis"] = "unknown"
        state["reason"] = "empty observation"
        state["has_error"] = True
        print("Diagnosis: unknown")
        print("Reason: empty observation\n")
        state["history"].append("Diagnosis: empty observation")
        return state

    # Deterministic rule for image pull errors.
    if "ErrImagePull" in observation or "ImagePullBackOff" in observation:
        state["diagnosis"] = "image_pull_error"
        state["reason"] = "image pull failed"
        state["has_error"] = True
        print(f"Diagnosis: {state['diagnosis']}")
        print(f"Reason: {state['reason']}\n")
        state["history"].append(f"Diagnosis: {state['diagnosis']} - {state['reason']}")
        return state

    # Regla determinista para crash loop
    if "CrashLoopBackOff" in observation:
        state["diagnosis"] = "crash_loop"
        state["reason"] = "container crash loop detected"
        state["has_error"] = True
        print(f"Diagnosis: {state['diagnosis']}")
        print(f"Reason: {state['reason']}\n")
        state["history"].append(f"Diagnosis: {state['diagnosis']} - {state['reason']}")
        return state

    # Deterministic rule for resources that are still being created.
    if "Pending" in observation or "ContainerCreating" in observation:
        state["diagnosis"] = "creating"
        state["reason"] = "pods are still starting"
        state["has_error"] = False
        print(f"Diagnosis: {state['diagnosis']}")
        print(f"Reason: {state['reason']}\n")
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
        print(f"Diagnosis: {state['diagnosis']}")
        print(f"Reason: {state['reason']}\n")
        state["history"].append(f"Diagnosis: {state['diagnosis']} - {state['reason']}")
        return state

    # Use AI only when no clear deterministic pattern matches.
    # This is one of the points where the LLM is used.
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

        print(f"Diagnosis: {state['diagnosis']}")
        print(f"Reason: {state['reason']}\n")

        state["history"].append(f"Diagnosis: {state['diagnosis']} - {state['reason']}")
    else:
        state["diagnosis"] = "unknown"
        state["reason"] = "LLM diagnosis failed"
        state["has_error"] = True
        print("Diagnosis: unknown")
        print("Reason: LLM diagnosis failed\n")
        state["history"].append("Diagnosis: LLM failed")

    return state


@timed_node("repair_deterministic")
def repair_node(state: AgentState):
    print("\n[AGENT] Remediation Agent\n")
    # Agent that attempts automatic error correction.
    # It is one of the most agentic nodes because it acts on the problem.

    # Prevent infinite remediation loops.
    if state["retries"] >= state["max_retries"]:
        print("Maximum retry count reached.\n")
        state["history"].append("Remediation: maximum retry count reached")
        return state

    # First layer: deterministic correction using known-image similarity.
    no_pods_found = "No pods found" in (
        state.get("reason", "") + "\n" + state.get("observation", "")
    )
    if state["diagnosis"] == "image_pull_error" or (
        state["diagnosis"] == "unknown" and no_pods_found
    ):
        if is_explicit_image_reference(state["image"]):
            state["retries"] = state["max_retries"]
            state["has_error"] = True
            state["diagnosis"] = "image_pull_error_unrepaired"
            state["reason"] = summarize_image_pull_failure(
                state.get("observation", "") + "\n" + state.get("reason", "")
            )
            state["history"].append("Remediation: explicit image reference left unchanged")
            return state

        correction = suggest_known_correction(state["image"])

        if correction and correction["image"] != state["image"]:
            old_app_name = state["app_name"]
            old_image = state["image"]
            new_app_name = correction["app_name"]
            new_image = correction["image"]

            print(f"Applying similarity-based correction: {old_image} -> {new_image}\n")

            state["image"] = new_image

            if state["app_name"] == old_image:
                state["app_name"] = new_app_name
            # Keep app_name aligned so Kubernetes labels and resources remain coherent.

            delete_app_resources(old_app_name, state["session_id"])
            # Delete old resources before redeploying.

            state["retries"] += 1
            state["history"].append(
                f"Remediation: corrected app={old_app_name}->{state['app_name']}, image={old_image}->{new_image}"
            )
            return state

    # Segunda capa: si la regla no basta, pedir sugerencia al LLM
    # This is the other main point where the LLM is used.
    suggestion = suggest_fix_with_llm(
        app_name=state["app_name"],
        image=state["image"],
        replicas=state["replicas"],
        diagnosis=state["diagnosis"],
        reason=state["reason"],
        observation=state["observation"],
    )

    if not suggestion:
        print("Could not obtain an LLM correction.\n")
        state["retries"] += 1
        state["history"].append("Remediation: sin sugerencia del LLM")
        if state["diagnosis"] == "image_pull_error":
            state["diagnosis"] = "image_pull_error_unrepaired"
            state["reason"] = "image pull failed and no safe correction was found"
            state["has_error"] = True
            state["retries"] = state["max_retries"]
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

        print("Applying correction suggested by the agent.\n")
        print(f"app_name: {old_app_name} -> {new_app_name}")
        print(f"image: {old_image} -> {new_image}")
        print(f"replicas: {old_replicas} -> {new_replicas}\n")

        state["app_name"] = new_app_name
        state["image"] = new_image
        state["replicas"] = new_replicas

        delete_app_resources(old_app_name, state["session_id"])

        state["retries"] += 1
        state["history"].append(
            f"Remediation: corrected app={old_app_name}->{new_app_name}, image={old_image}->{new_image}, replicas={old_replicas}->{new_replicas}"
        )
    else:
        # If there is no useful correction, keep the current values unchanged.
        print("No safe correction found. Keeping current values.\n")
        state["retries"] += 1
        state["history"].append("Remediation: sin cambios")
        if state["diagnosis"] == "image_pull_error":
            state["diagnosis"] = "image_pull_error_unrepaired"
            state["reason"] = "image pull failed and no safe correction was found"
            state["has_error"] = True
            state["retries"] = state["max_retries"]

    return state


@timed_node("show_yaml")
def show_yaml_node(state: AgentState):
    print("\n[AGENT] YAML Viewer Agent\n")
    # Visualization agent.
    # It does not deploy anything; it only returns generated YAML.

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

    success, output = get_pod_logs(state["app_name"], state["session_id"])
    print(output)

    state["logs_output"] = output
    state["observation"] = output
    state["history"].append(f"Logs: logs consultados para {state['app_name']}")

    if not success:
        state["has_error"] = True
        state["diagnosis"] = "logs_failed"
        state["reason"] = output.strip() or "kubectl logs failed"
    else:
        state["diagnosis"] = "logs_ready"
        state["reason"] = "pod logs returned"

    return state


@timed_node("describe_pod")
def describe_pod_node(state: AgentState):
    print("\n[AGENT] Describe Agent\n")
    # Observabilidad: describe del pod activo

    success, output = describe_pod(state["app_name"], state["session_id"])
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

    # Use parameters already parsed into the state.
    params = {
        "masters": state["masters"],
        "workers": state["workers"],
        "cni": "calico",
        "kubernetes_version": "v1.30",
        "container_runtime": "containerd",
    }

    print(f"Cluster parameters: {params}\n")
    state["history"].append(f"Cluster Provisioning: parameters {params}")

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

    output_dir = os.path.join(PROJECT_ROOT, "provisioning", "generated_cluster")
    os.makedirs(output_dir, exist_ok=True)

    with open(os.path.join(output_dir, "master_setup.sh"), "w", encoding="utf-8") as f:
        f.write(master_script)

    with open(os.path.join(output_dir, "worker_setup.sh"), "w", encoding="utf-8") as f:
        f.write(worker_script)

    with open(os.path.join(output_dir, "create_vms.ps1"), "w", encoding="utf-8") as f:
        f.write(virtualbox_script)

    state["observation"] = (
        "Scripts generated in provisioning/generated_cluster/: "
        "create_vms.ps1, master_setup.sh and worker_setup.sh"
    )
    state["history"].append(
        "Cluster Provisioning: scripts guardados en provisioning/generated_cluster/"
    )

    print("Cluster plan generated.\n")

    return state


@timed_node("cluster_status")
def cluster_status_node(state: AgentState):
    print("\n[AGENT] Cluster Status Agent\n")

    provider = state.get("provider", "minikube")
    profile = get_kubectl_context(state["session_id"])

    try:
        success, output = get_cluster_nodes(state["session_id"])
        job_status = read_minikube_status(minikube_profile_from_session(state["session_id"]))
        profile_status_ok, profile_status = get_minikube_profile_status(profile)

        if job_status:
            output += "\n\nProvisioning job status:\n"
            output += json.dumps(job_status, indent=2)

        if not success and profile_status:
            output += "\n\nMinikube profile status:\n"
            output += profile_status

        state["observation"] = output

        if job_status.get("status") == "running":
            state["has_error"] = False
            state["diagnosis"] = "cluster_creating"
            state["reason"] = f"Cluster profile {profile} is still being created"
        elif success and " Ready" in output:
            state["has_error"] = False
            state["diagnosis"] = "healthy"
            state["reason"] = f"Cluster reachable using provider={provider}, profile={profile}"
        elif "kubeconfig: Misconfigured" in profile_status or "stale minikube-vm" in profile_status:
            state["has_error"] = True
            state["diagnosis"] = "cluster_disconnected"
            state["reason"] = (
                f"Cluster profile {profile} exists but kubeconfig is stale after Docker/Minikube restart. "
                f"Run: minikube -p {profile} start"
            )
        elif "apiserver: Stopped" in profile_status or "kubelet: Stopped" in profile_status:
            state["has_error"] = True
            state["diagnosis"] = "cluster_stopped"
            state["reason"] = (
                f"Cluster profile {profile} exists but the control-plane is not running. "
                f"Run: minikube -p {profile} start"
            )
        else:
            state["has_error"] = True
            state["diagnosis"] = "unhealthy"
            state["reason"] = (
                f"The cluster profile {profile} is not reachable or no node is Ready"
            )


        state["history"].append("Cluster Status: check completed")

    except Exception as e:
        state["has_error"] = True
        state["diagnosis"] = "status_failed"
        state["reason"] = str(e)

    return state


@timed_node("cluster_execute")
def cluster_execute_node(state: AgentState):
    print("\n[AGENT] Cluster Execution Agent\n")

    inventory_path = state.get(
        "cluster_inventory_path",
        os.path.join(PROJECT_ROOT, "provisioning", "generated_cluster", "inventory.json"),
    )

    print("Ejecutando provisioning de Kubernetes sobre infraestructura cloud...")

    success, logs, join_command = execute_cluster_provisioning(inventory_path)

    if success:
        state["has_error"] = False
        state["diagnosis"] = "cluster_created"
        state["reason"] = "Kubernetes cluster created successfully"
        state["observation"] = logs
        state["history"].append("Cluster Execute: cluster created successfully")
    else:
        state["has_error"] = True
        state["diagnosis"] = "cluster_provisioning_failed"
        state["reason"] = "Kubernetes provisioning failed"
        state["observation"] = logs
        state["history"].append("Cluster Execute: provisioning failed")

    if state.get("provider") == "minikube":
        subprocess.run("kubectl create deployment nginx --image=nginx", shell=True)

    if join_command:
        state["history"].append("Cluster Execute: join command generated and used")

    print(f"Diagnosis: {state['diagnosis']}")
    print(f"Reason: {state['reason']}\n")

    return state


@timed_node("cloud_provision")
def cloud_provision_node(state: AgentState):
    print("\n[AGENT] Cloud Provisioner Agent\n")

    provider = state.get("provider", "minikube")

    params = {
        "provider": provider,
        "masters": state["masters"],
        "workers": state["workers"],
        "session_id": state["session_id"],
        "profile": minikube_profile_from_session(state["session_id"]),
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
        failure_reason, failure_observation = summarize_infrastructure_failure(
            provider, str(e)
        )
        if provider == "minikube":
            write_minikube_status(
                params["profile"],
                {
                    "status": "failed",
                    "message": failure_reason,
                    "logs": failure_observation,
                },
            )
        state["has_error"] = True
        state["diagnosis"] = "infrastructure_failed"
        state["reason"] = failure_reason
        state["observation"] = failure_observation
        state["history"].append(
            "Cloud Provisioner: infrastructure provisioning failed"
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
        state["history"].append("Python Builder: build failed")
        return state

    state["image"] = image_name
    state["diagnosis"] = "build_ready"
    state["reason"] = "Python app image built"
    state["history"].append(f"Python Builder: image generated {image_name}")

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
        state["history"].append("LLM YAML Generator: invalid YAML")
        return state

    os.makedirs(GENERATED_MANIFESTS_DIR, exist_ok=True)
    llm_yaml_path = os.path.join(GENERATED_MANIFESTS_DIR, "llm_generated.yaml")

    with open(llm_yaml_path, "w", encoding="utf-8") as f:
        f.write(yaml_text)

    state["has_error"] = False
    state["diagnosis"] = "llm_yaml_ready"
    state["reason"] = "YAML generated completely by LLM"
    state["history"].append("LLM YAML Generator: YAML generated by AI")

    print("YAML generated completely by the LLM.\n")

    return state


@timed_node("deploy_llm_yaml")
def deploy_llm_yaml_node(state: AgentState):
    print("\n[AGENT] LLM YAML Execution Agent\n")
    namespace = namespace_from_session(state["session_id"])
    namespace_ready, namespace_output = ensure_namespace(namespace, state["session_id"])

    if not namespace_ready:
        state["has_error"] = True
        state["diagnosis"] = "deployment_failed"
        state["reason"] = "namespace creation failed"
        state["observation"] = namespace_output
        state["history"].append("LLM YAML Execution: namespace creation failed")
        return state

    dry_run = subprocess.run(
        [
            *kubectl_command(state["session_id"]),
            "apply",
            "--dry-run=client",
            "-n",
            namespace,
            "-f",
            os.path.join(GENERATED_MANIFESTS_DIR, "llm_generated.yaml"),
        ],
        capture_output=True,
        text=True,
    )

    if dry_run.returncode != 0:
        state["has_error"] = True
        state["diagnosis"] = "llm_yaml_dry_run_failed"
        state["reason"] = "kubectl dry-run failed"
        state["observation"] = dry_run.stdout + dry_run.stderr
        state["history"].append("LLM YAML Execution: dry-run failed")
        return state

    result = subprocess.run(
        [
            *kubectl_command(state["session_id"]),
            "apply",
            "-n",
            namespace,
            "-f",
            os.path.join(GENERATED_MANIFESTS_DIR, "llm_generated.yaml"),
        ],
        capture_output=True,
        text=True,
    )

    state["observation"] = result.stdout + result.stderr

    if result.returncode != 0:
        state["has_error"] = True
        state["diagnosis"] = "deployment_failed"
        state["reason"] = state["observation"].strip() or "kubectl apply failed"
        state["history"].append("LLM YAML Execution: kubectl apply failed")
    else:
        state["has_error"] = False
        state["history"].append("LLM YAML Execution: kubectl apply correcto")

    return state
