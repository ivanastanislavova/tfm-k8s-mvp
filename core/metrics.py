import time
import json
import os
from functools import wraps

METRICS_FILE = "results/evaluation_results.jsonl"
os.makedirs("results", exist_ok=True)


def timed_node(node_name):
    def decorator(func):
        @wraps(func)
        def wrapper(state):
            start = time.time()

            result = func(state)

            elapsed = time.time() - start

            if "metrics" not in result:
                result["metrics"] = {}

            result["metrics"][node_name] = round(elapsed, 4)

            return result

        return wrapper

    return decorator


def save_evaluation_result(final_state, execution_time):
    record = {
        "session_id": final_state.get("session_id"),
        "user_request": final_state.get("user_request"),
        "intent": final_state.get("intent"),
        "generation_mode": final_state.get("generation_mode"),
        "llm_model": final_state.get("llm_model"),
        "diagnosis": final_state.get("diagnosis"),
        "reason": final_state.get("reason"),
        "success": final_state.get("diagnosis")
        in [
            "healthy",
            "logs_ready",
            "describe_ready",
            "deleted",
            "cluster_created",
            "cluster_plan_ready",
        ],
        "e2e_time_seconds": round(execution_time, 4),
        "node_times": final_state.get("metrics", {}),
        "app_name": final_state.get("app_name"),
        "image": final_state.get("image"),
        "replicas": final_state.get("replicas"),
        "service_type": final_state.get("service_type"),
        "history": final_state.get("history", []),
    }

    with open(METRICS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
