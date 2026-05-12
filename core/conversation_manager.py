from copy import deepcopy

# deepcopy se usa para copiar estructuras complejas (diccionarios)
# sin compartir referencias (muy importante para evitar bugs)


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
}
# CONTEXTO BASE
# Esto es como el "estado inicial" de una conversación
# Si el usuario no ha dicho nada, se parte de aquí


class ConversationManager:
    def __init__(self):
        self.sessions = {}
        # Diccionario de sesiones
        # clave = session_id
        # valor = {
        #   context: {...},
        #   messages: [...]
        # }

    def get_session(self, session_id: str):
        # Obtiene o crea una sesión

        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "context": deepcopy(DEFAULT_CONTEXT),
                # Cada sesión tiene su propio contexto independiente
                "messages": [],
                # Historial de mensajes tipo chat
            }

        return self.sessions[session_id]

    def get_context(self, session_id: str):
        # Devuelve el contexto actual de la sesión
        session = self.get_session(session_id)
        return session["context"]

    def add_message(self, session_id: str, role: str, text: str):
        # Guarda mensajes tipo chat
        # role: "user" o "assistant"

        session = self.get_session(session_id)

        session["messages"].append({"role": role, "text": text})

    def update_context_from_parsed(self, session_id: str, parsed: dict):
        # ACTUALIZA el contexto con lo que el usuario acaba de decir

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
        ]:
            if key not in parsed:
                continue

            value = parsed[key]

            # =========================
            # CONFIG DATA (merge)
            # =========================
            if key == "config_data":
                # No sobrescribe → combina configs
                # ejemplo:
                # antes: ENV=prod
                # ahora: DEBUG=false
                # resultado: ENV=prod, DEBUG=false

                if isinstance(value, dict) and value:
                    context["config_data"].update(value)
                continue

            # =========================
            # USE INGRESS
            # =========================
            if key == "use_ingress":
                if value is not None:
                    context["use_ingress"] = bool(value)

                    # Si se desactiva ingress → borrar host
                    if value is False:
                        context["ingress_host"] = ""
                continue

            # =========================
            # INGRESS HOST
            # =========================
            if key == "ingress_host":
                if value not in ["", None]:
                    context["ingress_host"] = value
                    context["use_ingress"] = True
                continue

            # =========================
            # STRINGS
            # =========================
            if key in ["app_name", "image", "service_type"]:
                if value not in ["", None]:
                    context[key] = value
                continue

            # =========================
            # NUMBERS
            # =========================
            if key in ["replicas", "port"]:
                if isinstance(value, int) and value > 0:
                    context[key] = value
                continue

    def fill_missing_from_context(self, session_id: str, parsed: dict):
        # COMPLETA lo que falta en el input del usuario usando contexto

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
        ]:

            if key not in completed:
                # Si no viene en parsed → coger del contexto
                completed[key] = deepcopy(context[key])
                continue

            value = completed[key]

            # =========================
            # STRINGS
            # =========================
            if key in ["app_name", "image", "service_type", "ingress_host"]:
                if value in ["", None]:
                    completed[key] = deepcopy(context[key])
                continue

            # =========================
            # NUMBERS
            # =========================
            if key in ["replicas", "port"]:
                if value in [0, None]:
                    completed[key] = deepcopy(context[key])
                continue

            # =========================
            # CONFIG MERGE
            # =========================
            if key == "config_data":
                merged = deepcopy(context["config_data"])
                if isinstance(value, dict):
                    merged.update(value)
                completed[key] = merged
                continue

            # =========================
            # USE INGRESS
            # =========================
            if key == "use_ingress":
                if value is None:
                    completed[key] = deepcopy(context["use_ingress"])
                continue

        return completed

    def get_messages(self, session_id: str):
        # Devuelve el historial del chat
        session = self.get_session(session_id)
        return session["messages"]
