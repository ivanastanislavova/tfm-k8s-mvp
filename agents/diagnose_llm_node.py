from langchain_core.messages import HumanMessage
from llm.llm_provider import get_llm
from core.metrics import timed_node


@timed_node("diagnose_llm")
def diagnose_llm_node(state):

    print("\n[AGENT] LLM Diagnosis Agent\n")

    llm = get_llm(state.get("llm_model", "llama3.2:3b"))

    observation = state["observation"]

    prompt = f"""
You are a Kubernetes diagnosis agent.

Analyze this kubectl get pods output:

{observation}

Return ONLY one word from this list:
healthy
creating
image_pull_error
crash_loop
unknown

Rules:
- If all pods show 1/1 Running, return healthy.
- If any pod shows ErrImagePull or ImagePullBackOff, return image_pull_error.
- If any pod shows CrashLoopBackOff, return crash_loop.
- If any pod shows ContainerCreating or Pending, return creating.
- If the output is empty or unclear, return unknown.
- Do not explain.
"""

    response = llm.invoke([
        HumanMessage(content=prompt)
    ])

    diagnosis = response.content.strip().lower()

    allowed = ["healthy", "creating", "image_pull_error", "crash_loop", "unknown"]

    if diagnosis not in allowed:
        diagnosis = "unknown"

    # Capa de seguridad determinista para evitar errores absurdos del LLM
    if (
        "1/1" in observation
        and "Running" in observation
        and "ErrImagePull" not in observation
        and "ImagePullBackOff" not in observation
        and "CrashLoopBackOff" not in observation
    ):
        diagnosis = "healthy"

    state["diagnosis"] = diagnosis

    if diagnosis == "healthy":
        state["reason"] = "LLM detected running pods"
        state["has_error"] = False

    elif diagnosis == "creating":
        state["reason"] = "LLM detected pods still creating"
        state["has_error"] = False

    elif diagnosis == "crash_loop":
        state["reason"] = "LLM detected crash loop"
        state["has_error"] = True

    elif diagnosis == "image_pull_error":
        state["reason"] = "LLM detected image pull failure"
        state["has_error"] = True

    else:
        state["reason"] = "LLM uncertain"
        state["has_error"] = True

    state["history"].append(
        f"LLM Diagnosis: {diagnosis}"
    )

    return state