from llm.llm_provider import get_llm

# Aquí definimos el parser híbrido que interpreta el texto del usuario.

from langchain_core.messages import HumanMessage

# Tipo de mensaje que se le pasa al LLM

import json

# Se usa para convertir texto JSON en diccionarios Python

import re

# Se usa para regex (parseo determinista sin IA)


def extract_json(text: str):
    # Algunos LLMs a veces devuelven texto extra antes o después del JSON.
    # Esta función intenta extraer solo el bloque {...}.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0)
    return text


def normalize_service_type(text: str):
    # Normaliza service_type para que siempre quede
    # exactamente como Kubernetes espera:
    # "NodePort" expone el servicio externamente abriendo un puerto especifico
    # o "ClusterIP" expone el servicio solo internamente dentro del cluster

    if not text:
        return ""
    text = text.lower()
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


def rule_based_parse(user_text: str):
    # PRIMERA CAPA: parseo determinista SIN IA
    # Aquí intentamos interpretar la petición con regex.
    # Esto es mejor que usar IA cuando la instrucción es simple y predecible.

    text = user_text.strip()

    # =========================
    # CLUSTER STATUS / HEALTH
    # =========================
    cluster_status_pattern = re.search(
        r"(?:check|show|get|validate).*(?:cluster).*(?:health|status|nodes|state)|(?:cluster)\s+(?:health|status|nodes|state)",
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
        r"(?:create|setup|install|provision).*(?:kubernetes|k8s).*(?:cluster)(?:\s+with\s+(\d+)\s+master(?:s)?\s+and\s+(\d+)\s+worker(?:s)?)?",
        text,
        re.IGNORECASE,
    )

    if create_cluster_pattern:
        masters = (
            int(create_cluster_pattern.group(1))
            if create_cluster_pattern.group(1)
            else 1
        )
        workers = (
            int(create_cluster_pattern.group(2))
            if create_cluster_pattern.group(2)
            else 1
        )

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
    deploy_pattern = re.search(
        r"deploy\s+([a-zA-Z0-9\-]+)"
        r"(?:\s+using\s+([a-zA-Z0-9\:\._\-\/]+))?"
        r"(?:\s+with\s+(\d+)\s+replicas?)?"
        r"(?:\s+on\s+port\s+(\d+))?"
        r"(?:\s+as\s+(NodePort|ClusterIP))?"
        r"(?:\s+with\s+config\s+([A-Za-z0-9_\-=,\.\:]+))?"
        r"(?:\s+with\s+ingress(?:\s+host\s+([a-zA-Z0-9\.\-]+))?)?",
        text,
        re.IGNORECASE,
    )
    # Esta regex intenta capturar una frase tipo:
    # deploy my-web using nginx with 3 replicas on port 8080 as NodePort with config ENV=prod,DEBUG=false with ingress host myweb.local

    if deploy_pattern:
        app_name = deploy_pattern.group(1)
        # Nombre de la app

        image = deploy_pattern.group(2) if deploy_pattern.group(2) else app_name
        # Si el usuario no da imagen explícita, por diseño se asume image = app_name

        replicas = int(deploy_pattern.group(3)) if deploy_pattern.group(3) else 1
        # Si no especifica réplicas, se usa 1

        port = int(deploy_pattern.group(4)) if deploy_pattern.group(4) else 80
        # Si no especifica puerto, se usa 80

        service_type = (
            normalize_service_type(deploy_pattern.group(5))
            if deploy_pattern.group(5)
            else "NodePort"
        )
        # Si no especifica tipo de servicio, se usa NodePort por defecto

        config_data = parse_config_data(deploy_pattern.group(6))
        # Convierte el texto de configuración en diccionario

        ingress_host = deploy_pattern.group(7) if deploy_pattern.group(7) else ""
        # Host del ingress si existe

        use_ingress = bool(ingress_host) or ("with ingress" in text.lower())
        # Si hay host o se menciona "with ingress", activamos ingress

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
    # SCALE explícito
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
        r"pon\s+(\d+)\s+replicas?", text, re.IGNORECASE
    )
    # Captura frases cortas tipo:
    # pon 3 replicas
    # Aquí no sabemos app_name explícitamente → se completará con el contexto después

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
        r"(?:change|cambia)\s+(?:(?:the|la)\s+)?(?:image|imagen)\s+(?:to|a)\s+([a-zA-Z0-9\:\._\-\/]+)",
        text,
        re.IGNORECASE,
    )
    # Captura:
    # change image to nginx:latest
    # cambia la imagen a nginx:latest

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
        r"(?:make|hazlo|set).*(ClusterIP|NodePort)", text, re.IGNORECASE
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
        r"(?:add|añade)\s+config\s+([A-Za-z0-9_\-=,\.\:]+)", text, re.IGNORECASE
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
        r"(?:enable|add|with)\s+ingress(?:\s+host\s+([a-zA-Z0-9\.\-]+))?",
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

    # Si ninguna regex encaja, no se pudo interpretar por reglas
    return None


def llm_parse(
    user_text: str, context: dict | None = None, llm_model: str = "llama3.2:3b"
):
    # SEGUNDA CAPA: parseo con IA
    # Solo se usa si rule_based_parse no ha podido interpretar el texto.

    context = context or {}

    prompt = f"""
You are a Kubernetes conversational intent parser.

Return ONLY valid JSON with exactly these keys:
{{
  "intent": "deploy | deploy_python | scale | delete | status | update_image | update_port | update_service | add_config | enable_ingress | disable_ingress | show_yaml | show_logs | describe_pod | create_cluster | cluster_status",
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
  "python_file": "string"
}}

Current context:
{json.dumps(context, ensure_ascii=False)}

Rules:
- If the user refers to the current app implicitly, app_name may be empty and will be filled from context later.
- For missing string values use "".
- For missing numeric values use 0.
- For missing config use {{}}.
- If ingress is not explicitly changed, set "use_ingress" to null.
- Use false only for explicit disable_ingress.
- Use true only for explicit enable_ingress.
- Output JSON only.
- No markdown.
- No explanation.

User request:
{user_text}
"""
    # Aquí construimos el prompt que le das al LLM.
    # Le pedimos que actúe como parser estructurado, no como chatbot.
    # Muy importante: le obligamos a devolver JSON estricto.

    llm = get_llm(llm_model)
    # Obtenemos el modelo LLM local usando la función get_llm que definimos en llm_provider.py

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

        return data
    except Exception:
        # Si el LLM devuelve algo mal formado, lo detectas aquí
        print("Error parseando respuesta del LLM:")
        print(content)
        return None


def parse_user_input(
    user_text: str, context: dict | None = None, llm_model: str = "llama3.2:3b"
):
    # Función principal que usa el sistema
    # Estrategia híbrida:
    # 1) primero reglas deterministas
    # 2) si fallan, usar IA

    parsed = rule_based_parse(user_text)
    if parsed:
        return parsed

    return llm_parse(user_text, context=context, llm_model=llm_model)
