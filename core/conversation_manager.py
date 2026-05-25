from copy import deepcopy


# Baseline context used when a new chat session starts.
DEFAULT_CONTEXT = {
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


class ConversationManager:
    def __init__(self):
        self.sessions = {}

    def get_session(self, session_id: str):
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "context": deepcopy(DEFAULT_CONTEXT),
                "messages": [],
            }

        return self.sessions[session_id]

    def get_context(self, session_id: str):
        return self.get_session(session_id)["context"]

    def add_message(self, session_id: str, role: str, text: str):
        self.get_session(session_id)["messages"].append({"role": role, "text": text})

    def update_context_from_parsed(self, session_id: str, parsed: dict):
        session = self.get_session(session_id)
        context = session["context"]

        for key in [
            "app_name",
            "image",
            "replicas",
            "port",
            "service_type",
            "config_data",
            "use_ingress",
            "ingress_host",
            "masters",
            "workers",
            "provider",
        ]:
            if key not in parsed:
                continue

            value = parsed[key]

            if key == "config_data":
                # Merge new key-value pairs without deleting existing config.
                if isinstance(value, dict) and value:
                    context["config_data"].update(value)
                continue

            if key == "use_ingress":
                if value is not None:
                    context["use_ingress"] = bool(value)
                    if value is False:
                        context["ingress_host"] = ""
                continue

            if key == "ingress_host":
                if value not in ["", None]:
                    context["ingress_host"] = value
                    context["use_ingress"] = True
                continue

            if key in ["app_name", "image", "service_type", "provider"]:
                if value not in ["", None]:
                    context[key] = value
                continue

            if key in ["replicas", "port"]:
                if isinstance(value, int) and value > 0:
                    context[key] = value
                continue

    def fill_missing_from_context(self, session_id: str, parsed: dict):
        session = self.get_session(session_id)
        context = session["context"]
        completed = deepcopy(parsed)

        for key in [
            "app_name",
            "image",
            "replicas",
            "port",
            "service_type",
            "config_data",
            "use_ingress",
            "ingress_host",
            "masters",
            "workers",
            "provider",
        ]:
            if key not in completed:
                completed[key] = deepcopy(context[key])
                continue

            value = completed[key]

            if key in ["app_name", "image", "service_type", "ingress_host", "provider"]:
                if value in ["", None]:
                    completed[key] = deepcopy(context[key])
                continue

            if key in ["replicas", "port"]:
                if value in [0, None]:
                    completed[key] = deepcopy(context[key])
                continue

            if key == "config_data":
                merged = deepcopy(context["config_data"])
                if isinstance(value, dict):
                    merged.update(value)
                completed[key] = merged
                continue

            if key == "use_ingress":
                if value is None:
                    completed[key] = deepcopy(context["use_ingress"])
                continue

        return completed

    def update_last_result(self, session_id: str, final_state: dict):
        context = self.get_session(session_id)["context"]
        context["last_intent"] = final_state.get("intent", "")
        context["last_observation"] = final_state.get("observation", "")
        context["last_reason"] = final_state.get("reason", "")

    def get_messages(self, session_id: str):
        return self.get_session(session_id)["messages"]
