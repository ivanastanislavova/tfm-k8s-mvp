import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.graph_builder import build_graph
from llm.llm_parser import parse_user_input
from core.conversation_manager import ConversationManager

from core.metrics import save_evaluation_result
from fastapi.responses import FileResponse

app = FastAPI()
conversation_manager = ConversationManager()
app.mount("/app/assets", StaticFiles(directory="app/assets"), name="assets")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class DeployRequest(BaseModel):
    text: str
    session_id: str = "default"
    llm_model: str = "llama3.2:3b"
    generation_mode: str = "hybrid_template"
    selected_workload: dict = Field(default_factory=dict)


@app.get("/", response_class=FileResponse)
def home():
    return FileResponse("app/index.html")


@app.post("/deploy")
def deploy(request: DeployRequest):
    user_text = request.text
    session_id = request.session_id
    llm_model = request.llm_model
    generation_mode = request.generation_mode

    try:
        start_time = time.time()

        selected_workload = request.selected_workload or {}
        selected_app_name = selected_workload.get("app_name", "")
        if selected_app_name and selected_app_name != "cluster":
            conversation_manager.update_context_from_parsed(session_id, selected_workload)

        context = conversation_manager.get_context(session_id)
        parse_start = time.time()
        parsed = parse_user_input(user_text, context=context, llm_model=llm_model)
        interpretation_time = time.time() - parse_start

        if not parsed:
            return {
                "error": "No se pudo interpretar la petición",
                "input": user_text,
                "session_id": session_id,
            }

        completed = conversation_manager.fill_missing_from_context(session_id, parsed)

        graph = build_graph()

        initial_state = {
            "session_id": session_id,
            "user_request": user_text,
            "intent": completed["intent"],
            "app_name": completed["app_name"],
            "image": completed["image"],
            "replicas": completed["replicas"],
            "port": completed["port"],
            "service_type": completed["service_type"],
            "config_data": completed["config_data"],
            "use_ingress": completed["use_ingress"],
            "ingress_host": completed["ingress_host"],
            "masters": completed.get("masters", 1),
            "workers": completed.get("workers", 1),
            "provider": completed.get("provider", "minikube"),
            "has_error": False,
            "diagnosis": "",
            "reason": "",
            "deployment_yaml": "",
            "service_yaml": "",
            "configmap_yaml": "",
            "ingress_yaml": "",
            "observation": "",
            "logs_output": "",
            "describe_output": "",
            "history": [],
            "chat_history": conversation_manager.get_messages(session_id),
            "retries": 0,
            "max_retries": 2,
            "cluster_plan": "",
            "master_script": "",
            "worker_script": "",
            "virtualbox_script": "",
            "cluster_inventory": {},
            "cluster_inventory_path": "",
            "python_file": completed.get("python_file", ""),
            "source_type": completed.get("source_type", ""),
            "llm_model": llm_model,
            "generation_mode": generation_mode,
            "llm_generated_yaml": "",
            "metrics": {
                "interpretation_time_seconds": round(interpretation_time, 4),
            },
            "last_intent": context.get("last_intent", ""),
            "last_observation": context.get("last_observation", ""),
            "last_reason": context.get("last_reason", ""),
        }

        final_state = graph.invoke(initial_state)

        execution_time = time.time() - start_time

        save_evaluation_result(final_state, execution_time)

        conversation_manager.add_message(session_id, "user", user_text)
        conversation_manager.add_message(
            session_id,
            "assistant",
            f"{final_state['diagnosis']}: {final_state['reason']}",
        )
        conversation_manager.update_last_result(session_id, final_state)

        # Actualizar contexto si la acción modifica o mantiene el despliegue
        if final_state["intent"] in [
            "deploy",
            "scale",
            "update_image",
            "update_port",
            "update_service",
            "add_config",
            "enable_ingress",
            "disable_ingress",
            "create_cluster",
        ]:
            conversation_manager.update_context_from_parsed(session_id, final_state)

        # Limpiar completamente el contexto si se elimina la app
        if final_state["intent"] == "delete" and final_state["diagnosis"] == "deleted":
            conversation_manager.get_session(session_id)["context"] = {
                "app_name": "",
                "image": "",
                "replicas": 1,
                "port": 80,
                "service_type": "NodePort",
                "config_data": {},
                "use_ingress": False,
                "ingress_host": "",
                "masters": 1,
                "workers": 1,
                "provider": "minikube",
                "last_intent": "",
                "last_observation": "",
                "last_reason": "",
            }

            final_state["app_name"] = ""
            final_state["image"] = ""
            final_state["replicas"] = 1
            final_state["port"] = 80
            final_state["service_type"] = "NodePort"
            final_state["config_data"] = {}
            final_state["use_ingress"] = False
            final_state["ingress_host"] = ""
            final_state["masters"] = 1
            final_state["workers"] = 1

        return {
            "session_id": session_id,
            "intent": final_state["intent"],
            "app_name": final_state["app_name"],
            "image": final_state["image"],
            "replicas": final_state["replicas"],
            "port": final_state["port"],
            "service_type": final_state["service_type"],
            "config_data": final_state["config_data"],
            "use_ingress": final_state["use_ingress"],
            "ingress_host": final_state["ingress_host"],
            "masters": final_state["masters"],
            "workers": final_state["workers"],
            "diagnosis": final_state["diagnosis"],
            "reason": final_state["reason"],
            "history": final_state["history"],
            "observation": final_state["observation"],
            "execution_time_seconds": round(execution_time, 2),
            "cluster_plan": final_state["cluster_plan"],
            "master_script": final_state["master_script"],
            "worker_script": final_state["worker_script"],
            "virtualbox_script": final_state["virtualbox_script"],
            "cluster_inventory": final_state["cluster_inventory"],
            "llm_model": final_state["llm_model"],
            "generation_mode": final_state["generation_mode"],
            "llm_generated_yaml": final_state["llm_generated_yaml"],
            "metrics": final_state["metrics"],
            "deployment_yaml": final_state["deployment_yaml"],
            "service_yaml": final_state["service_yaml"],
            "configmap_yaml": final_state["configmap_yaml"],
            "ingress_yaml": final_state["ingress_yaml"],
            "logs_output": final_state["logs_output"],
            "describe_output": final_state["describe_output"],
        }

    except Exception as e:
        return {
            "error": "Backend exception",
            "details": str(e),
            "input": user_text,
            "session_id": session_id,
        }
