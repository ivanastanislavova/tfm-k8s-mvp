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


def diagnose_with_llm(user_request, app_name, image, replicas, observation):
    prompt = f"""
You are a Kubernetes Diagnosis Agent.

Analyze the current Kubernetes deployment situation.

Return ONLY valid JSON with exactly this structure:
{{
  "diagnosis": "healthy | creating | image_pull_error | crash_loop | pending | cluster_unreachable | deployment_failed | unknown",
  "reason": "short explanation"
}}

Context:
- User request: {user_request}
- App name: {app_name}
- Image: {image}
- Replicas: {replicas}

Observed kubectl output:
{observation}

Important rules:
- If the observation shows connection errors, API server errors, "Unable to connect to the server",
  or failed kubectl execution, do NOT return healthy.
- If there is not enough evidence of success, do NOT return healthy.
- Only return healthy if the observation clearly indicates running pods or successful deployment state.
- Return JSON only.
- Do not add markdown.
- Keep the reason short.
"""

    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        content = response.content.strip()
        json_text = extract_json(content)
        return json.loads(json_text)
    except Exception:
        print("Error en diagnose_with_llm")
        return None


def suggest_fix_with_llm(app_name, image, replicas, diagnosis, reason, observation):
    prompt = f"""
You are a Kubernetes Remediation Agent.

Your job is to propose a corrected configuration if possible.

Return ONLY valid JSON with exactly this structure:
{{
  "app_name": "string",
  "image": "string",
  "replicas": integer
}}

Current configuration:
- app_name: {app_name}
- image: {image}
- replicas: {replicas}

Diagnosis:
- diagnosis: {diagnosis}
- reason: {reason}

Observed kubectl output:
{observation}

Rules:
- If there is a safe correction, return corrected values.
- If no safe correction is possible, return the same values unchanged.
- Do not add explanations.
- Output JSON only.
"""

    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        content = response.content.strip()
        json_text = extract_json(content)
        return json.loads(json_text)
    except Exception:
        print("Error en suggest_fix_with_llm")
        return None
