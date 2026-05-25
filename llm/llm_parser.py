from llm.llm_provider import get_llm

# Hybrid parser that interprets the user request.

from langchain_core.messages import HumanMessage

# Tipo de mensaje que se le pasa al LLM

import json

# Se usa para convertir texto JSON en diccionarios Python

import re

# Se usa para regex (parseo determinista sin IA)


def extract_json(text: str):
    # Some LLMs may return extra text before or after the JSON object.
    # Extract only the JSON object when possible.
    start = text.find("{")
    if start >= 0:
        try:
            _, end = json.JSONDecoder().raw_decode(text[start:])
            return text[start : start + end]
        except Exception:
            pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        json_text = match.group(0)
        while json_text.endswith("}}"):
            candidate = json_text[:-1]
            try:
                json.loads(candidate)
                return candidate
            except Exception:
                json_text = candidate
        return json_text
    return text


def normalize_service_type(text: str):
    # Normaliza service_type para que siempre quede
    # exactamente como Kubernetes espera:
    # "NodePort" expone el servicio externamente abriendo un puerto especifico
    # o "ClusterIP" expone el servicio solo internamente dentro del cluster

    if not text:
        return ""
    text = re.sub(r"[\s_-]+", "", text.lower())
    if text == "nodeport":
        return "NodePort"
    if text == "clusterip":
        return "ClusterIP"
    return text


def parse_config_data(raw_config: str):
    # Convierte un texto como:
    # "ENV=prod,DEBUG=false"
    # en:
    # {"ENV": "prod", "DEBUG": "false"}

    config = {}

    if not raw_config:
        return config

    pairs = raw_config.split(",")
    for pair in pairs:
        pair = pair.strip()
        if "=" in pair:
            key, value = pair.split("=", 1)
            config[key.strip()] = value.strip()

    return config


def normalize_app_reference(name: str):
    name = (name or "").strip()
    name = re.sub(r"-(?:deployment|service|pod)$", "", name, flags=re.IGNORECASE)
    return name


KNOWN_IMAGE_ALIASES = {
    "apache": "httpd:latest",
    "apache2": "httpd:latest",
}


def normalize_known_image_alias(image: str):
    image = (image or "").strip()
    if not image:
        return image
    return KNOWN_IMAGE_ALIASES.get(image.lower(), image)


def infer_app_name_from_image(image: str):
    image = (image or "").strip()
    if not image:
        return ""

    # Registry paths such as bitnami/nginx or ghcr.io/user/api:v1 are valid
    # image names, but Kubernetes labels need a simple DNS-compatible name.
    name = image.rsplit("/", 1)[-1]
    name = name.split(":", 1)[0]
    name = re.sub(r"[^a-zA-Z0-9\-]+", "-", name).strip("-").lower()
    return name or "app"


def infer_app_name_from_text(user_text: str, context: dict | None = None):
    context = context or {}
    text = user_text.strip()
    context_app = context.get("app_name", "")

    if context_app and re.search(rf"\b{re.escape(context_app)}(?:-(?:deployment|service|pod))?\b", text, re.IGNORECASE):
        return context_app

    patterns = [
        r"(?:using|uses|used by|for|of|has|with|tiene|usa|de)\s+([a-zA-Z0-9\-]+)(?:\?|$)",
        r"\b([a-zA-Z0-9\-]+)-(?:deployment|service|pod)\b",
    ]

    stopwords = {
        "service",
        "deployment",
        "pod",
        "port",
        "cluster",
        "namespace",
        "the",
        "that",
        "this",
        "using",
        "uses",
        "tiene",
        "usa",
    }

    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in reversed(matches):
            candidate = normalize_app_reference(match)
            if candidate and candidate.lower() not in stopwords:
                return candidate

    return ""


def rule_based_parse(user_text: str):
    # PRIMERA CAPA: parseo determinista SIN IA
    # First try to interpret the request with deterministic regex rules.
    # This is faster and safer than using AI for simple predictable commands.

    text = user_text.strip()
    uses_terraform = bool(re.search(r"\bterraform\b", text, re.IGNORECASE))
    command_words = {
        "show",
        "get",
        "list",
        "status",
        "logs",
        "describe",
        "delete",
        "scale",
        "update",
    }
    if re.match(
        r"^deploy\s+(?:" + "|".join(command_words) + r")\b",
        text,
        re.IGNORECASE,
    ):
        return {
            "intent": "deploy",
            "app_name": "",
            "image": "",
            "replicas": 1,
            "port": 80,
            "service_type": "NodePort",
            "config_data": {},
            "use_ingress": None,
            "ingress_host": "",
        }

    # =========================
    # LIST DEPLOYMENTS
    # =========================
    list_deployments_pattern = re.search(
        r"(?:how many|cu[aá]nt[oa]s?|list|show|get|dime|muestra).*(?:deployments?|despliegues?)",
        text,
        re.IGNORECASE,
    )

    if list_deployments_pattern:
        return {
            "intent": "list_deployments",
            "app_name": "cluster",
            "image": "",
            "replicas": 0,
            "port": 0,
            "service_type": "",
            "config_data": {},
            "use_ingress": None,
            "ingress_host": "",
            "provider": "minikube",
        }

    contextual_question_pattern = re.search(
        r"^(?:which are they|what are they|what are their names|cu[aá]les son|cuales son|y cuales|y cu[aá]les|qu[eé] son|dime cuales|dime cu[aá]les)\??$",
        text,
        re.IGNORECASE,
    )

    if contextual_question_pattern:
        return {
            "intent": "answer_contextual_question",
            "app_name": "cluster",
            "image": "",
            "replicas": 0,
            "port": 0,
            "service_type": "",
            "config_data": {},
            "use_ingress": None,
            "ingress_host": "",
            "provider": "minikube",
        }

    app_port_patterns = [
        r"(?:what|which).*(?:port).*(?:does|do)\s+([a-zA-Z0-9\-]+)\s+(?:use|uses|have|has)",
        r"(?:what|which).*(?:port).*(?:has|uses|is|for|of)\s+([a-zA-Z0-9\-]+)",
        r"(?:cu[aá]l|que|qu[eé]).*(?:puerto).*(?:tiene|usa|es|de)\s+([a-zA-Z0-9\-]+)",
    ]
    app_port_pattern = None
    for pattern in app_port_patterns:
        app_port_pattern = re.search(pattern, text, re.IGNORECASE)
        if app_port_pattern:
            break

    if app_port_pattern:
        return {
            "intent": "show_app_port",
            "app_name": normalize_app_reference(app_port_pattern.group(1)),
            "image": "",
            "replicas": 0,
            "port": 0,
            "service_type": "",
            "config_data": {},
            "use_ingress": None,
            "ingress_host": "",
            "provider": "minikube",
        }

    # =========================
    # CLUSTER STATUS / HEALTH
    # =========================
    cluster_status_pattern = re.search(
        r"(?:check|show|get|validate).*(?:cluster).*(?:health|status|nodes|state)"
        r"|(?:what|which).*(?:health|status|nodes|state).*(?:cluster)"
        r"|(?:health|status|nodes|state)\s+(?:of\s+)?(?:the\s+)?cluster"
        r"|(?:cluster)\s+(?:health|status|nodes|state)",
        text,
        re.IGNORECASE,
    )

    if cluster_status_pattern:
        return {
            "intent": "cluster_status",
            "app_name": "cluster",
            "image": "",
            "replicas": 0,
            "port": 0,
            "service_type": "",
            "config_data": {},
            "use_ingress": None,
            "ingress_host": "",
        }

    # =========================
    # CREATE CLUSTER
    # =========================
    create_cluster_pattern = re.search(
        r"(?:create|setup|install|provision).*(?:(?:kubernetes|k8s)\s+)?cluster",
        text,
        re.IGNORECASE,
    )

    if create_cluster_pattern:
        masters_match = re.search(r"(\d+)\s+master(?:s)?", text, re.IGNORECASE)
        workers_match = re.search(r"(\d+)\s+worker(?:s)?", text, re.IGNORECASE)
        masters = int(masters_match.group(1)) if masters_match else 1
        workers = int(workers_match.group(1)) if workers_match else 1

        return {
            "intent": "create_cluster",
            "app_name": "cluster",
            "image": "",
            "replicas": 0,
            "port": 0,
            "service_type": "",
            "config_data": {},
            "use_ingress": None,
            "ingress_host": "",
            "masters": masters,
            "workers": workers,
            "provider": "terraform" if uses_terraform else "minikube",
        }

    # =========================
    # DEPLOY PYTHON FILE
    # =========================
    python_deploy_pattern = re.search(
        r"(?:deploy|despliega).*(?:python|\.py)\s+([a-zA-Z0-9_\-\.\/\\]+)",
        text,
        re.IGNORECASE,
    )

    if python_deploy_pattern:
        return {
            "intent": "deploy_python",
            "app_name": "python-app",
            "image": "",
            "replicas": 1,
            "port": 80,
            "service_type": "NodePort",
            "config_data": {},
            "use_ingress": None,
            "ingress_host": "",
            "python_file": python_deploy_pattern.group(1),
        }

    # =========================
    # DEPLOY
    # =========================
    image_only_deploy_pattern = re.search(
        r"^deploy\s+([a-zA-Z0-9\:\._\-\/]+)"
        r"(?:\s+with\s+(\d+)\s+replicas?)?"
        r"(?:\s+on\s+port\s+(\d+))?"
        r"(?:\s+as\s+(NodePort|ClusterIP))?\s*$",
        text,
        re.IGNORECASE,
    )

    if image_only_deploy_pattern and (
        "/" in image_only_deploy_pattern.group(1)
        or ":" in image_only_deploy_pattern.group(1)
    ):
        image = image_only_deploy_pattern.group(1)
        return {
            "intent": "deploy",
            "app_name": infer_app_name_from_image(image),
            "image": image,
            "replicas": int(image_only_deploy_pattern.group(2)) if image_only_deploy_pattern.group(2) else 1,
            "port": int(image_only_deploy_pattern.group(3)) if image_only_deploy_pattern.group(3) else 80,
            "service_type": (
                normalize_service_type(image_only_deploy_pattern.group(4))
                if image_only_deploy_pattern.group(4)
                else "NodePort"
            ),
            "config_data": {},
            "use_ingress": None,
            "ingress_host": "",
        }

    deploy_pattern = re.search(
        r"^deploy\s+([a-zA-Z0-9\-]+)"
        r"(?:\s+using\s+([a-zA-Z0-9\:\._\-\/]+))?"
        r"(?:\s+with\s+(\d+)\s+replicas?)?"
        r"(?:\s+on\s+port\s+(\d+))?"
        r"(?:\s+as\s+(NodePort|ClusterIP))?"
        r"(?:\s+(?:with|con)\s+(?:config|variables|env)\s+([A-Za-z0-9_\-=,\.\:]+))?"
        r"(?:\s+(?:with|con)\s+ingress(?:\s+host\s+([a-zA-Z0-9\.\-]+))?)?\s*$",
        text,
        re.IGNORECASE,
    )
    # Esta regex intenta capturar una frase tipo:
    # deploy my-web using nginx with 3 replicas on port 8080 as NodePort with config ENV=prod,DEBUG=false with ingress host myweb.local

    if deploy_pattern:
        if uses_terraform:
            return {
                "intent": "create_cluster",
                "app_name": "cluster",
                "image": "",
                "replicas": 0,
                "port": 0,
                "service_type": "",
                "config_data": {},
                "use_ingress": None,
                "ingress_host": "",
                "masters": 1,
                "workers": 1,
                "provider": "terraform",
            }

        app_name = deploy_pattern.group(1)
        if app_name.lower() in command_words:
            return {
                "intent": "deploy",
                "app_name": "",
                "image": "",
                "replicas": 1,
                "port": 80,
                "service_type": "NodePort",
                "config_data": {},
                "use_ingress": None,
                "ingress_host": "",
            }
        # Nombre de la app

        image = deploy_pattern.group(2) if deploy_pattern.group(2) else app_name
        # If the user does not provide an explicit image, use app_name as image.

        replicas = int(deploy_pattern.group(3)) if deploy_pattern.group(3) else 1
        # Default to one replica when replicas are not specified.

        port = int(deploy_pattern.group(4)) if deploy_pattern.group(4) else 80
        # Default to port 80 when no port is specified.

        service_type = (
            normalize_service_type(deploy_pattern.group(5))
            if deploy_pattern.group(5)
            else "NodePort"
        )
        # Default to NodePort when no service type is specified.

        config_data = parse_config_data(deploy_pattern.group(6))
        # Convert key-value configuration text into a dictionary.

        ingress_host = deploy_pattern.group(7) if deploy_pattern.group(7) else ""
        # Host del ingress si existe

        use_ingress = bool(ingress_host) or bool(
            re.search(r"(?:with|con)\s+ingress", text, re.IGNORECASE)
        )
        # Enable Ingress when a host is provided or ingress is requested.

        return {
            "intent": "deploy",
            "app_name": app_name,
            "image": image,
            "replicas": replicas,
            "port": port,
            "service_type": service_type,
            "config_data": config_data,
            "use_ingress": use_ingress,
            "ingress_host": ingress_host,
        }

    # =========================
    # COMBINED SCALE + SERVICE UPDATE
    # =========================
    combined_replicas_pattern = re.search(
        r"(?:scale(?:\s+[a-zA-Z0-9\-]+)?\s+to|set|change|with|to)\s+(\d+)\s+replicas?",
        text,
        re.IGNORECASE,
    )
    combined_service_pattern = re.search(
        r"(Cluster\s*IP|ClusterIP|Node\s*Port|NodePort)",
        text,
        re.IGNORECASE,
    )

    if combined_replicas_pattern and combined_service_pattern:
        return {
            "intent": "update_service",
            "app_name": "",
            "image": "",
            "replicas": int(combined_replicas_pattern.group(1)),
            "port": 0,
            "service_type": normalize_service_type(combined_service_pattern.group(1)),
            "config_data": {},
            "use_ingress": None,
            "ingress_host": "",
        }

    # =========================
    # Explicit scale command.
    # =========================
    scale_explicit_pattern = re.search(
        r"scale\s+([a-zA-Z0-9\-]+)\s+to\s+(\d+)\s+replicas?", text, re.IGNORECASE
    )
    # Captura frases como:
    # scale my-web to 5 replicas

    if scale_explicit_pattern:
        return {
            "intent": "scale",
            "app_name": scale_explicit_pattern.group(1),
            "image": "",
            "replicas": int(scale_explicit_pattern.group(2)),
            "port": 0,
            "service_type": "",
            "config_data": {},
            "use_ingress": None,
            "ingress_host": "",
        }

    # =========================
    # SCALE contextual
    # =========================
    scale_contextual_pattern = re.search(
        r"(?:scale\s+to|set|pon)\s+(\d+)\s+replicas?", text, re.IGNORECASE
    )
    # Captures short contextual requests such as:
    # scale to 3 replicas
    # set 3 replicas
    # pon 3 replicas
    # app_name may be omitted here and completed from session context later.

    if scale_contextual_pattern:
        return {
            "intent": "scale",
            "app_name": "",
            "image": "",
            "replicas": int(scale_contextual_pattern.group(1)),
            "port": 0,
            "service_type": "",
            "config_data": {},
            "use_ingress": None,
            "ingress_host": "",
        }

    # =========================
    # UPDATE IMAGE
    # =========================
    update_image_pattern = re.search(
        r"(?:change|update|set|cambia|actualiza)\s+(?:(?:the|la)\s+)?(?:image|imagen)\s+(?:to|a)\s+([a-zA-Z0-9\:\._\-\/]+)",
        text,
        re.IGNORECASE,
    )

    if not update_image_pattern:
        update_image_pattern = re.search(
            r"(?:change|update|set|cambia|actualiza)\s+(?:to|a)\s+([a-zA-Z0-9\:\._\-\/]+)\s+(?:image|imagen)",
            text,
            re.IGNORECASE,
        )
    # Captura:
    # change image to nginx:latest
    # change image to nginx:latest

    if update_image_pattern:
        return {
            "intent": "update_image",
            "app_name": "",
            "image": update_image_pattern.group(1),
            "replicas": 0,
            "port": 0,
            "service_type": "",
            "config_data": {},
            "use_ingress": None,
            "ingress_host": "",
        }

    incomplete_update_image_pattern = re.search(
        r"^(?:change|update|set|cambia|actualiza)\s+(?:(?:the|la)\s+)?(?:image|imagen)\s*$",
        text,
        re.IGNORECASE,
    )

    if incomplete_update_image_pattern:
        return {
            "intent": "update_image",
            "app_name": "",
            "image": "__MISSING_IMAGE__",
            "replicas": 0,
            "port": 0,
            "service_type": "",
            "config_data": {},
            "use_ingress": None,
            "ingress_host": "",
        }

    # =========================
    # UPDATE PORT
    # =========================
    update_port_pattern = re.search(
        r"(?:change|cambia).*(?:port|puerto)\s+(?:to\s+)?(\d+)", text, re.IGNORECASE
    )

    if update_port_pattern:
        return {
            "intent": "update_port",
            "app_name": "",
            "image": "",
            "replicas": 0,
            "port": int(update_port_pattern.group(1)),
            "service_type": "",
            "config_data": {},
            "use_ingress": None,
            "ingress_host": "",
        }

    # =========================
    # UPDATE SERVICE TYPE
    # =========================
    update_service_pattern = re.search(
        r"(?:make|hazlo|set|change|cambia).*(Cluster\s*IP|ClusterIP|Node\s*Port|NodePort)",
        text,
        re.IGNORECASE,
    )

    if update_service_pattern:
        return {
            "intent": "update_service",
            "app_name": "",
            "image": "",
            "replicas": 0,
            "port": 0,
            "service_type": normalize_service_type(update_service_pattern.group(1)),
            "config_data": {},
            "use_ingress": None,
            "ingress_host": "",
        }

    # =========================
    # ADD CONFIG
    # =========================
    add_config_pattern = re.search(
        r"(?:add|añade|agrega)\s+(?:config|variables|env)\s+([A-Za-z0-9_\-=,\.\:]+)",
        text,
        re.IGNORECASE,
    )

    if add_config_pattern:
        return {
            "intent": "add_config",
            "app_name": "",
            "image": "",
            "replicas": 0,
            "port": 0,
            "service_type": "",
            "config_data": parse_config_data(add_config_pattern.group(1)),
            "use_ingress": None,
            "ingress_host": "",
        }

    # =========================
    # ENABLE INGRESS
    # =========================
    ingress_pattern = re.search(
        r"(?:enable|add|with|activa|añade|agrega|con)\s+ingress(?:\s+host\s+([a-zA-Z0-9\.\-]+))?",
        text,
        re.IGNORECASE,
    )

    if ingress_pattern:
        return {
            "intent": "enable_ingress",
            "app_name": "",
            "image": "",
            "replicas": 0,
            "port": 0,
            "service_type": "",
            "config_data": {},
            "use_ingress": True,
            "ingress_host": (
                ingress_pattern.group(1) if ingress_pattern.group(1) else ""
            ),
        }

    # =========================
    # DISABLE INGRESS
    # =========================
    disable_ingress_pattern = re.search(
        r"(?:disable|remove|delete)\s+ingress", text, re.IGNORECASE
    )

    if disable_ingress_pattern:
        return {
            "intent": "disable_ingress",
            "app_name": "",
            "image": "",
            "replicas": 0,
            "port": 0,
            "service_type": "",
            "config_data": {},
            "use_ingress": False,
            "ingress_host": "",
        }

    # =========================
    # PROTECTED CLUSTER DELETE / NODE DELETE
    # =========================
    protected_cluster_delete_pattern = re.search(
        r"(?:delete|destroy|remove|b[oó]rrar|borra|eliminar|elimina)"
        r".*(?:cluster|node|nodes|worker|workers|master|control-?plane)",
        text,
        re.IGNORECASE,
    )

    if protected_cluster_delete_pattern:
        return {
            "intent": "protected_cluster_operation",
            "app_name": "cluster",
            "image": "",
            "replicas": 0,
            "port": 0,
            "service_type": "",
            "config_data": {},
            "use_ingress": None,
            "ingress_host": "",
        }

    # =========================
    # DELETE
    # =========================
    delete_pattern = re.search(
        r"(?:delete|elim[ií]nalo|b[oó]rralo|remove)\s*([a-zA-Z0-9\-]+)?",
        text,
        re.IGNORECASE,
    )

    if delete_pattern:
        return {
            "intent": "delete",
            "app_name": delete_pattern.group(1) if delete_pattern.group(1) else "",
            "image": "",
            "replicas": 0,
            "port": 0,
            "service_type": "",
            "config_data": {},
            "use_ingress": None,
            "ingress_host": "",
        }

    # =========================
    # STATUS
    # =========================
    status_pattern = re.search(
        r"(?:status\s+of|status|show status|mu[eé]strame el estado)\s*([a-zA-Z0-9\-]+)?",
        text,
        re.IGNORECASE,
    )

    if status_pattern:
        return {
            "intent": "status",
            "app_name": status_pattern.group(1) if status_pattern.group(1) else "",
            "image": "",
            "replicas": 0,
            "port": 0,
            "service_type": "",
            "config_data": {},
            "use_ingress": None,
            "ingress_host": "",
        }

    # =========================
    # SHOW YAML
    # =========================
    show_yaml_pattern = re.search(
        r"(?:show yaml|show me the yaml|ens[eé][ñn]ame el yaml)", text, re.IGNORECASE
    )

    if show_yaml_pattern:
        return {
            "intent": "show_yaml",
            "app_name": "",
            "image": "",
            "replicas": 0,
            "port": 0,
            "service_type": "",
            "config_data": {},
            "use_ingress": None,
            "ingress_host": "",
        }

    # =========================
    # SHOW LOGS
    # =========================
    show_logs_pattern = re.search(
        r"(?:show logs|show me the logs|ens[eé][ñn]ame los logs|logs)\s*([a-zA-Z0-9\-]+)?",
        text,
        re.IGNORECASE,
    )

    if show_logs_pattern:
        return {
            "intent": "show_logs",
            "app_name": (
                show_logs_pattern.group(1) if show_logs_pattern.group(1) else ""
            ),
            "image": "",
            "replicas": 0,
            "port": 0,
            "service_type": "",
            "config_data": {},
            "use_ingress": None,
            "ingress_host": "",
        }

    # =========================
    # DESCRIBE POD
    # =========================
    describe_pattern = re.search(
        r"(?:describe pod|describe|ens[eé][ñn]ame el describe)\s*([a-zA-Z0-9\-]+)?",
        text,
        re.IGNORECASE,
    )

    if describe_pattern:
        return {
            "intent": "describe_pod",
            "app_name": describe_pattern.group(1) if describe_pattern.group(1) else "",
            "image": "",
            "replicas": 0,
            "port": 0,
            "service_type": "",
            "config_data": {},
            "use_ingress": None,
            "ingress_host": "",
        }

    # If no regex matches, rule-based parsing could not interpret the request.
    return None


def llm_parse(
    user_text: str, context: dict | None = None, llm_model: str = "llama3.2:3b"
):
    # SEGUNDA CAPA: parseo con IA
    # Used only when rule_based_parse cannot interpret the text.

    context = context or {}

    prompt = f"""
You are a Kubernetes conversational intent parser.

Return ONLY valid JSON with exactly these keys:
{{
  "intent": "deploy | deploy_python | scale | delete | status | update_image | update_port | update_service | add_config | enable_ingress | disable_ingress | show_yaml | show_logs | describe_pod | list_deployments | answer_contextual_question | answer_question | show_app_port | create_cluster | cluster_status",
  "app_name": "string",
  "image": "string",
  "replicas": integer,
  "port": integer,
  "service_type": "ClusterIP | NodePort | ''",
  "config_data": {{}},
  "use_ingress": null,
  "ingress_host": "string",
  "masters": integer,
  "workers": integer,
  "provider": "minikube | terraform | oracle",
  "python_file": "string"
}}

Current context:
{json.dumps(context, ensure_ascii=False)}

Rules:
- If the user refers to the current app implicitly, app_name may be empty and will be filled from context later.
- For missing string values use "".
- For missing numeric values use 0.
- For missing config use {{}}.
- If the user asks how many deployments exist or asks to list deployments, use intent "list_deployments".
- If the user asks a follow-up such as "which are they" or "cuáles son", use intent "answer_contextual_question".
- If the user asks which port an app, service, or deployment uses, use intent "show_app_port".
- If the user asks a general question and no safe action intent is clear, use intent "answer_question".
- Never force a question into "deploy" just because it mentions an app name.
- Use action intents only when the user clearly asks to create, modify, delete, inspect, or deploy something.
- If ingress is not explicitly changed, set "use_ingress" to null.
- If Terraform is explicitly requested for cluster/infrastructure provisioning, set provider to "terraform".
- If no provider is explicitly requested, set provider to "minikube".
- Use false only for explicit disable_ingress.
- Use true only for explicit enable_ingress.
- Output JSON only.
- No markdown.
- No explanation.

User request:
{user_text}
"""
    # Build the prompt sent to the LLM.
    # Ask the model to behave as a structured parser, not as a chatbot.
    # Muy importante: le obligamos a devolver JSON estricto.

    llm = get_llm(llm_model)
    # Load the local LLM through get_llm from llm_provider.py.

    response = llm.invoke([HumanMessage(content=prompt)])
    # Llamamos al modelo local

    content = response.content.strip()
    # Guardamos el texto que devuelve

    try:
        json_text = extract_json(content)
        # Intentamos quedarnos solo con el JSON

        data = json.loads(json_text)
        # Lo convertimos de string a diccionario Python

        if "use_ingress" not in data:
            data["use_ingress"] = None
        # Aseguramos ese campo aunque el modelo se lo olvide

        if not data.get("app_name"):
            inferred_app_name = infer_app_name_from_text(user_text, context)
            if inferred_app_name:
                data["app_name"] = inferred_app_name

        return data
    except Exception:
        # Detect malformed LLM responses here.
        print("Error parseando respuesta del LLM:")
        print(content)
        return None


def parse_user_input(
    user_text: str, context: dict | None = None, llm_model: str = "llama3.2:3b"
):
    # Main parser entry point used by the system.
    # Hybrid strategy:
    # 1) primero reglas deterministas
    # 2) si fallan, usar IA

    parsed = rule_based_parse(user_text)
    if parsed:
        return parsed

    return llm_parse(user_text, context=context, llm_model=llm_model)
