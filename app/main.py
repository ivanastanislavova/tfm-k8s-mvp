from core.graph_builder import build_graph
from llm.llm_parser import parse_user_input
from core.conversation_manager import ConversationManager

conversation_manager = ConversationManager()

if __name__ == "__main__":
    session_id = "cli"

    while True:
        user_text = input("K8s chat> ").strip()

        if user_text.lower() in ["exit", "quit"]:
            break

        context = conversation_manager.get_context(session_id)
        parsed = parse_user_input(user_text, context=context)

        if not parsed:
            print("No se pudo interpretar la petición.")
            continue

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
        }

        final_state = graph.invoke(initial_state)

        if final_state["intent"] in [
            "deploy", "scale", "update_image", "update_port", "update_service",
            "add_config", "enable_ingress", "disable_ingress"
        ]:
            conversation_manager.update_context_from_parsed(session_id, final_state)

        print("\nResultado:")
        print(f"Intent: {final_state['intent']}")
        print(f"App: {final_state['app_name']}")
        print(f"Diagnosis: {final_state['diagnosis']}")
        print(f"Reason: {final_state['reason']}")

        if final_state["intent"] == "create_cluster":
            print("\nCluster plan:")
            print(final_state["cluster_plan"])

            print("\nMaster script:")
            print(final_state["master_script"])

            print("\nWorker script:")
            print(final_state["worker_script"])

            print("\nVirtualBox script:")
            print(final_state["virtualbox_script"])

        if final_state["observation"]:
            print("\nObservation:")
            print(final_state["observation"])
        print()

        print("\nHistory:")
        for h in final_state["history"]:
            print("-", h)