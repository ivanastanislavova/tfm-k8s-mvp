"""
Construye el flujo agentic del sistema.

Defines how the agents are connected.

Cluster flow:
create_cluster -> plan -> provision -> execute -> status
"""

from langgraph.graph import StateGraph, END

# StateGraph = estructura principal de LangGraph (grafo de estados)
# END = estado final del flujo

from core.state import AgentState

# Estado global compartido entre todos los agentes (clave del sistema)

from agents.graph_nodes import (
    validate_node,
    generate_yaml_node,
    deploy_node,
    scale_node,
    delete_node,
    status_node,
    list_deployments_node,
    answer_contextual_question_node,
    answer_question_node,
    show_app_port_node,
    observe_node,
    diagnose_node,
    repair_node,
    show_yaml_node,
    show_logs_node,
    describe_pod_node,
    cluster_plan_node,
    cluster_status_node,
    cluster_execute_node,
    cloud_provision_node,
    build_python_app_node,
    generate_llm_yaml_node,
    deploy_llm_yaml_node,
)
import core.state as state

# Cada nodo es un "agente" con una responsabilidad concreta

from agents.diagnose_llm_node import diagnose_llm_node

# LLM diagnosis node with stronger interpretation capability.

from agents.llm_repair_node import repair_llm_node

# LLM repair node with stronger correction capability.


def build_graph():
    graph = StateGraph(AgentState)
    # Se define que todo el sistema trabaja sobre un estado compartido
    # This shared state is what makes the workflow agentic.

    # =========================
    # AGENT DEFINITIONS
    # =========================
    graph.add_node("validate", validate_node)  # valida la entrada
    graph.add_node("generate_yaml", generate_yaml_node)  # genera manifests
    graph.add_node("deploy", deploy_node)  # ejecuta kubectl apply
    graph.add_node("scale", scale_node)  # escala replicas
    graph.add_node("delete", delete_node)  # delete resources
    graph.add_node("status", status_node)  # consulta estado
    graph.add_node("list_deployments", list_deployments_node)
    graph.add_node("answer_contextual_question", answer_contextual_question_node)
    graph.add_node("answer_question", answer_question_node)
    graph.add_node("show_app_port", show_app_port_node)
    graph.add_node("observe", observe_node)  # observa cluster (pods)
    graph.add_node("diagnose", diagnose_node)  # interpreta estado
    graph.add_node("diagnose_llm", diagnose_llm_node)  # interpreta estado
    graph.add_node("repair", repair_node)  # intenta corregir errores
    graph.add_node("repair_llm", repair_llm_node)  # intenta corregir errores con LLM
    graph.add_node("show_yaml", show_yaml_node)  # muestra YAML
    graph.add_node("show_logs", show_logs_node)  # logs del pod
    graph.add_node("describe_pod", describe_pod_node)  # describe del pod
    graph.add_node("cluster_plan", cluster_plan_node)  # plan de cluster
    graph.add_node("cloud_provision", cloud_provision_node)  # cloud provisioning
    graph.add_node("cluster_execute", cluster_execute_node)  # cluster execution
    graph.add_node("cluster_status", cluster_status_node)  # estado del cluster
    graph.add_node(
        "build_python_app", build_python_app_node
    )  # build app from python code
    graph.add_node(
        "generate_llm_yaml", generate_llm_yaml_node
    )  # generate deployment YAML through the LLM
    graph.add_node(
        "deploy_llm_yaml", deploy_llm_yaml_node
    )  # deploy LLM-generated YAML
    graph.set_entry_point("validate")
    # Always start with validation to avoid early execution errors.

    # =========================
    # ROUTING PRINCIPAL (INTENT)
    # =========================
    def route_after_validation(state):

        # Stop early when the input is invalid.
        if state["diagnosis"] == "invalid_input":
            return END

        if state["intent"] == "protected_cluster_operation":
            return END

        # DEPLOY -> generate YAML.
        if state["intent"] == "deploy":
            if state.get("generation_mode") == "full_ai_experimental":
                return "generate_llm_yaml"
            return "generate_yaml"

        # SCALE -> scale directly without generating YAML.
        if state["intent"] == "scale":
            return "scale"

        # MODIFICACIONES CONVERSACIONALES
        # Estas acciones reutilizan el estado anterior
        if state["intent"] in [
            "update_image",
            "update_port",
            "update_service",
            "add_config",
            "enable_ingress",
            "disable_ingress",
        ]:
            return "generate_yaml"

        # DELETE -> remove resources directly.
        if state["intent"] == "delete":
            return "delete"

        # STATUS -> query deployment status.
        if state["intent"] == "status":
            return "status"

        if state["intent"] == "list_deployments":
            return "list_deployments"

        if state["intent"] == "answer_contextual_question":
            return "answer_contextual_question"

        if state["intent"] == "answer_question":
            return "answer_question"

        if state["intent"] == "show_app_port":
            return "show_app_port"

        # SHOW YAML -> generate updated YAML first.
        if state["intent"] == "show_yaml":
            return "generate_yaml"

        # OBSERVABILIDAD DIRECTA
        if state["intent"] == "show_logs":
            return "show_logs"

        if state["intent"] == "describe_pod":
            return "describe_pod"

        # CLUSTER PLAN -> plan directly without passing through YAML generation.
        if state["intent"] == "create_cluster":
            return "cluster_plan"

        # CLUSTER STATUS -> validate cluster health.
        if state["intent"] == "cluster_status":
            return "cluster_status"

        # BUILD APP -> build an application image from Python source code.
        if state["intent"] == "deploy_python":
            return "build_python_app"

        return END

    # Conditional edges make the workflow dynamic instead of a fixed script.
    graph.add_conditional_edges("validate", route_after_validation)

    # =========================
    # AFTER BUILDING A PYTHON APP
    # =========================
    def route_after_build_python_app(state):
        if state["diagnosis"] == "build_ready":
            return "generate_yaml"
        return END

    graph.add_conditional_edges("build_python_app", route_after_build_python_app)

    # =========================
    # AFTER GENERATING YAML
    # =========================
    def route_after_generate_yaml(state):

        # Return the generated YAML without deploying it.
        if state["intent"] == "show_yaml":
            return "show_yaml"

        # Otherwise, deploy the generated manifests.
        return "deploy"

    graph.add_conditional_edges("generate_yaml", route_after_generate_yaml)

    # =========================
    # AFTER GENERATING YAML WITH THE LLM
    # =========================
    def route_after_generate_llm_yaml(state):
        if state["diagnosis"] != "llm_yaml_ready":
            return END
        return "deploy_llm_yaml"

    graph.add_conditional_edges("generate_llm_yaml", route_after_generate_llm_yaml)

    def route_after_deploy_llm_yaml(state):
        if state["diagnosis"] in ["deployment_failed", "llm_yaml_dry_run_failed"]:
            if state.get("generation_mode") == "full_ai_experimental":
                return "repair_llm"
            return END

        return "observe"

    graph.add_conditional_edges("deploy_llm_yaml", route_after_deploy_llm_yaml)

    # =========================
    # AFTER DEPLOYMENT
    # =========================
    def route_after_deploy(state):

        # Route failures to diagnosis.
        if state["diagnosis"] == "deployment_failed":
            return "diagnose"

        # Otherwise, observe the real cluster state.
        return "observe"

    graph.add_conditional_edges("deploy", route_after_deploy)

    # =========================
    # FLUJOS LINEALES
    # =========================
    graph.add_edge("scale", "observe")  # after scaling, observe cluster state
    graph.add_edge("status", END)  # status is a terminal node
    graph.add_edge("list_deployments", END)
    graph.add_edge("answer_contextual_question", END)
    graph.add_edge("answer_question", END)
    graph.add_edge("show_app_port", END)

    def route_after_observe(state):
        if state.get("generation_mode") == "full_ai_experimental":
            return "diagnose_llm"

        return "diagnose"

    graph.add_conditional_edges("observe", route_after_observe)

    # =========================
    # AGENTIC CORE (DIAGNOSIS -> ACTION)
    # =========================
    def route_after_diagnose(state):

        if state["diagnosis"] == "creating":
            return "observe"

        if state.get("generation_mode") == "full_ai_experimental":
            if (
                state["diagnosis"]
                in [
                    "deployment_failed",
                    "llm_yaml_dry_run_failed",
                    "image_pull_error",
                    "crash_loop",
                    "unknown",
                ]
                and state["retries"] < state["max_retries"]
            ):
                return "repair_llm"

            return END

        if state["diagnosis"] in ["image_pull_error", "crash_loop", "unknown"]:
            if state["intent"] == "deploy" and state["retries"] < state["max_retries"]:
                return "repair"

        return END

    # Adaptive system reasoning happens here.
    graph.add_conditional_edges("diagnose", route_after_diagnose)

    # LLM diagnosis uses the same agentic core with stronger interpretation.
    graph.add_conditional_edges("diagnose_llm", route_after_diagnose)

    # =========================
    # REMEDIATION LOOP
    # =========================
    def route_after_repair(state):
        if state["diagnosis"].endswith("_unrepaired"):
            return END
        return "generate_yaml"

    graph.add_conditional_edges("repair", route_after_repair)
    # Ciclo completo:
    # error -> repair -> generate_yaml -> deploy -> observe -> diagnose

    def route_after_repair_llm(state):
        if state["diagnosis"] == "llm_yaml_ready":
            return "deploy_llm_yaml"
        return END

    graph.add_conditional_edges("repair_llm", route_after_repair_llm)
    # Ciclo completo para LLM:
    # error -> repair_llm -> deploy_llm_yaml -> observe -> diagnose_llm

    graph.add_edge("cluster_plan", "cloud_provision")

    def route_after_cloud_provision(state):
        if state["diagnosis"] != "infrastructure_ready":
            return END

        if state.get("provider") == "minikube":
            return "cluster_status"

        return "cluster_execute"

    graph.add_conditional_edges("cloud_provision", route_after_cloud_provision)

    def route_after_cluster_execute(state):
        if state["diagnosis"] == "cluster_created":
            return "cluster_status"
        return END

    graph.add_conditional_edges("cluster_execute", route_after_cluster_execute)

    # =========================
    # SALIDAS FINALES
    # =========================

    graph.add_edge("cluster_status", END)

    return graph.compile()
    # Compile the graph so it is ready to execute.
