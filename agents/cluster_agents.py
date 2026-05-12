"""
Agente LLM para interpretar creación de clúster.

Extrae:
- masters
- workers
- configuración básica

Solo genera parámetros, no ejecuta comandos.
"""

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage
import json
import re

llm = ChatOllama(model="llama3.2:3b", temperature=0)


def extract_json(text: str):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0)
    return text


def parse_cluster_request_with_llm(user_request: str):
    prompt = f"""
You are a Kubernetes cluster planning agent.

Extract only the infrastructure parameters from the user request.

Return ONLY valid JSON with exactly this structure:
{{
  "masters": integer,
  "workers": integer,
  "cni": "calico | flannel",
  "kubernetes_version": "string",
  "container_runtime": "containerd"
}}

Rules:
- If masters is not specified, use 1.
- If workers is not specified, use 1.
- If CNI is not specified, use "calico".
- If Kubernetes version is not specified, use "v1.35".
- Always use containerd.
- Do not generate shell scripts.
- Do not add explanations.
- Output JSON only.

User request:
{user_request}
"""

    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        content = response.content.strip()
        json_text = extract_json(content)
        data = json.loads(json_text)

        return {
            "masters": int(data.get("masters", 1)),
            "workers": int(data.get("workers", 1)),
            "cni": data.get("cni", "calico"),
            "kubernetes_version": data.get("kubernetes_version", "v1.35"),
            "container_runtime": "containerd",
        }

    except Exception:
        print("Error parsing cluster request with LLM")
        print(content if "content" in locals() else "")

        return {
            "masters": 1,
            "workers": 1,
            "cni": "calico",
            "kubernetes_version": "v1.35",
            "container_runtime": "containerd",
        }
