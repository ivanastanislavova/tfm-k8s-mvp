"""
Construye el flujo agentic del sistema.

Define cómo se conectan los agentes.

Flujo de clúster:
create_cluster → plan → provision → execute → status
"""
from langgraph.graph import StateGraph, END
# StateGraph = estructura principal de LangGraph (grafo de estados)
# END = estado final del flujo

from state import AgentState
# Estado global compartido entre todos los agentes (clave del sistema)

from graph_nodes import (
    validate_node,
    generate_yaml_node,
    deploy_node,
    scale_node,
    delete_node,
    status_node,
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
import state
# Cada nodo es un "agente" con una responsabilidad concreta


def build_graph():
    graph = StateGraph(AgentState)
    # Se define que todo el sistema trabaja sobre un estado compartido
    # Esto es lo que convierte el sistema en agentic (no variables sueltas)

    # =========================
    # DEFINICIÓN DE AGENTES
    # =========================
    graph.add_node("validate", validate_node)        # valida la entrada
    graph.add_node("generate_yaml", generate_yaml_node)  # genera manifests
    graph.add_node("deploy", deploy_node)            # ejecuta kubectl apply
    graph.add_node("scale", scale_node)              # escala replicas
    graph.add_node("delete", delete_node)            # elimina recursos
    graph.add_node("status", status_node)            # consulta estado
    graph.add_node("observe", observe_node)          # observa cluster (pods)
    graph.add_node("diagnose", diagnose_node)        # interpreta estado
    graph.add_node("repair", repair_node)            # intenta corregir errores
    graph.add_node("show_yaml", show_yaml_node)      # muestra YAML
    graph.add_node("show_logs", show_logs_node)      # logs del pod
    graph.add_node("describe_pod", describe_pod_node)  # describe del pod
    graph.add_node("cluster_plan", cluster_plan_node) # plan de cluster
    graph.add_node("cloud_provision", cloud_provision_node) # provisión en la nube
    graph.add_node("cluster_execute", cluster_execute_node)  # ejecución del cluster
    graph.add_node("cluster_status", cluster_status_node) # estado del cluster
    graph.add_node("build_python_app", build_python_app_node) # build app from python code
    graph.add_node("generate_llm_yaml", generate_llm_yaml_node) # generar YAML específico para despliegue desde LLM
    graph.add_node("deploy_llm_yaml", deploy_llm_yaml_node) # desplegar YAML específico para despliegue desde LLM
    graph.set_entry_point("validate")
    # Siemre se empieza validando → evita errores desde el inicio

    # =========================
    # ROUTING PRINCIPAL (INTENT)
    # =========================
    def route_after_validation(state):

        # Si la entrada no es válida → terminar
        if state["diagnosis"] == "invalid_input":
            return END

        # DEPLOY → generar YAML
        if state["intent"] == "deploy":
            if state.get("generation_mode") == "llm_yaml":
                return "generate_llm_yaml"
            return "generate_yaml"

        # SCALE → ir directo a escalar (no hace falta YAML)
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

        # DELETE → eliminar directamente
        if state["intent"] == "delete":
            return "delete"

        # STATUS → consultar estado
        if state["intent"] == "status":
            return "status"

        # SHOW YAML → primero generar YAML actualizado
        if state["intent"] == "show_yaml":
            return "generate_yaml"

        # OBSERVABILIDAD DIRECTA
        if state["intent"] == "show_logs":
            return "show_logs"

        if state["intent"] == "describe_pod":
            return "describe_pod"
        
        # PLAN DE CLUSTER → ir directo a planificar (sin pasar por YAML)
        if state["intent"] == "create_cluster":
            return "cluster_plan"
        
        # STATUS DEL CLUSTER → validar salud del cluster
        if state["intent"] == "cluster_status":
            return "cluster_status"
        
        # BUILD APP → construir app desde código Python
        if state["intent"] == "deploy_python":
            return "build_python_app"

        return END

    # Esto hace el flujo dinámico → NO es un script fijo
    graph.add_conditional_edges("validate", route_after_validation)

    # =========================
    # DESPUÉS DE BUILD PYTHON APP
    # =========================
    def route_after_build_python_app(state):
        if state["diagnosis"] == "build_ready":
            return "generate_yaml"
        return END

    graph.add_conditional_edges("build_python_app", route_after_build_python_app)

    # =========================
    # DESPUÉS DE GENERAR YAML
    # =========================
    def route_after_generate_yaml(state):

        # Si solo quieres ver YAML → no despliegas
        if state["intent"] == "show_yaml":
            return "show_yaml"

        # En cualquier otro caso → despliegas
        return "deploy"

    graph.add_conditional_edges("generate_yaml", route_after_generate_yaml)

    # =========================
    # DESPUÉS DE GENERAR YAML CON LLM
    # =========================
    def route_after_generate_llm_yaml(state):
        if state["diagnosis"] != "llm_yaml_ready":
            return END
        return "deploy_llm_yaml"

    graph.add_conditional_edges("generate_llm_yaml", route_after_generate_llm_yaml)

    def route_after_deploy_llm_yaml(state):
        if state["diagnosis"] == "deployment_failed":
            return END
        return "observe"

    graph.add_conditional_edges("deploy_llm_yaml", route_after_deploy_llm_yaml)

    # =========================
    # DESPUÉS DE DEPLOY
    # =========================
    def route_after_deploy(state):

        # Si algo falla → ir a diagnóstico
        if state["diagnosis"] == "deployment_failed":
            return "diagnose"

        # Si no → observar estado real del cluster
        return "observe"

    graph.add_conditional_edges("deploy", route_after_deploy)

    # =========================
    # FLUJOS LINEALES
    # =========================
    graph.add_edge("scale", "observe")      # tras escalar → observar
    graph.add_edge("status", "diagnose")    # status → diagnóstico
    graph.add_edge("observe", "diagnose")   # observación → diagnóstico

    # =========================
    # CORAZÓN AGENTIC (DIAGNOSIS → ACTION)
    # =========================
    def route_after_diagnose(state):

        # Si el pod sigue arrancando → volver a observar (loop)
        if state["diagnosis"] == "creating":
            return "observe"

        # Si hay error real
        if state["diagnosis"] in ["image_pull_error", "crash_loop", "unknown"]:

            # Solo reparamos si:
            # - estamos en deploy
            # - no hemos superado el límite de intentos
            if state["intent"] == "deploy" and state["retries"] < state["max_retries"]:
                return "repair"

        # Si está todo bien o no se puede reparar → terminar
        return END

    # Aquí ocurre el razonamiento adaptativo del sistema
    graph.add_conditional_edges("diagnose", route_after_diagnose)

    # =========================
    # LOOP DE REMEDIACIÓN
    # =========================
    graph.add_edge("repair", "generate_yaml")
    # Ciclo completo:
    # error → repair → generate_yaml → deploy → observe → diagnose
    
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
    # Se compila el grafo → listo para ejecutar


    