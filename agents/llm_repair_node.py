import re
from pathlib import Path
import re

import yaml
from langchain_core.messages import HumanMessage
from llm.llm_provider import get_llm
from core.metrics import timed_node

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def extract_yaml(text: str) -> str:
    text = text.strip()
    text = re.sub(r"```yaml", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```", "", text)
    return text.strip()


@timed_node("repair_llm")
def repair_llm_node(state):
    print("\n[AGENT] LLM Repair Agent\n")

    llm = get_llm(state.get("llm_model", "llama3.2:3b"))

    prompt = f"""
You are a Kubernetes YAML repair agent.

The following Kubernetes YAML failed or produced an unhealthy deployment.

Original user request:
{state["user_request"]}

Current diagnosis:
{state["diagnosis"]}

Reason:
{state["reason"]}

Observation/error:
{state["observation"]}

Broken YAML:
{state["llm_generated_yaml"]}

Return ONLY corrected raw Kubernetes YAML.
Do not explain anything.
Do not use markdown.
Do not use Helm.
Do not use placeholders.
Do not use {{ }} syntax.

Rules:
- The YAML must be directly valid for kubectl apply.
- Generate a Deployment and a Service.
- Use document separator --- between resources.
- Deployment container ports must be written exactly as:
  ports:
  - containerPort: 80
"""

    response = llm.invoke([HumanMessage(content=prompt)])
    repaired_yaml = extract_yaml(response.content)

    try:
        docs = list(yaml.safe_load_all(repaired_yaml))

        if len(docs) < 2:
            return _fail(
                state,
                repaired_yaml,
                "Repaired YAML must contain Deployment and Service",
            )

        deployment = docs[0]
        containers = deployment["spec"]["template"]["spec"]["containers"]

        if "ports" not in containers[0]:
            return _fail(
                state, repaired_yaml, "Repaired YAML missing container ports section"
            )

    except Exception as e:
        return _fail(state, repaired_yaml, f"Invalid repaired YAML: {e}")

    with open(PROJECT_ROOT / "llm_generated.yaml", "w", encoding="utf-8") as f:
        f.write(repaired_yaml)

    state["llm_generated_yaml"] = repaired_yaml
    state["deployment_yaml"] = repaired_yaml
    state["diagnosis"] = "llm_yaml_ready"
    state["reason"] = "YAML repaired by LLM"
    state["has_error"] = False
    state["retries"] += 1
    state["history"].append("LLM Repair: YAML corregido por IA")

    return state


def _fail(state, yaml_text, reason):
    state["llm_generated_yaml"] = yaml_text
    state["deployment_yaml"] = yaml_text
    state["diagnosis"] = "llm_repair_failed"
    state["reason"] = reason
    state["has_error"] = True
    state["retries"] += 1
    state["history"].append(f"LLM Repair: fallo - {reason}")
    return state
