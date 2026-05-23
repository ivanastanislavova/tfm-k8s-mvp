import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_API_URL = "http://127.0.0.1:8000/deploy"
DEFAULT_MODEL = "llama3.2:3b"
DEFAULT_MODES = ["hybrid_template", "full_ai_experimental"]


SCENARIOS = {
    "deploy_nginx": {
        "prompt": "deploy nginx",
        "app": "nginx",
        "expected_replicas": 1,
        "validation": "deployment_ready",
    },
    "deploy_nginx_2_replicas": {
        "prompt": "deploy nginx with 2 replicas",
        "app": "nginx",
        "expected_replicas": 2,
        "validation": "deployment_ready",
    },
    "scale_nginx_to_3": {
        "setup_prompt": "deploy nginx",
        "prompt": "scale nginx to 3 replicas",
        "app": "nginx",
        "expected_replicas": 3,
        "validation": "deployment_ready",
    },
    "update_nginx_image": {
        "setup_prompt": "deploy nginx",
        "prompt": "update nginx image to nginx:1.27-alpine",
        "app": "nginx",
        "expected_replicas": 1,
        "expected_image": "nginx:1.27-alpine",
        "validation": "deployment_ready",
    },
    "status_nginx": {
        "setup_prompt": "deploy nginx",
        "prompt": "what is the status of nginx",
        "app": "nginx",
        "validation": "api_success",
    },
    "list_deployments": {
        "setup_prompt": "deploy nginx",
        "prompt": "how many deployments i have",
        "app": "nginx",
        "validation": "deployments_listed",
    },
    "question_nginx_port": {
        "setup_prompt": "deploy nginx",
        "prompt": "what port is nginx using?",
        "app": "nginx",
        "validation": "answer_or_success",
    },
    "update_nginx_service_clusterip": {
        "setup_prompt": "deploy nginx",
        "prompt": "change nginx service to ClusterIP",
        "app": "nginx",
        "expected_service_type": "ClusterIP",
        "validation": "service_type",
    },
    "delete_nginx": {
        "setup_prompt": "deploy nginx",
        "prompt": "delete nginx",
        "app": "nginx",
        "validation": "deployment_deleted",
    },
    "cluster_status": {
        "prompt": "what is the status of the cluster",
        "validation": "cluster_status",
    },
    "create_cluster_1_worker": {
        "prompt": "create a cluster with 1 worker",
        "expected_nodes": 2,
        "validation": "cluster_ready",
        "cleanup_strategy": "session",
    },
    "create_cluster_3_workers": {
        "prompt": "create a cluster with 3 workers",
        "expected_nodes": 4,
        "validation": "cluster_ready",
        "cleanup_strategy": "session",
    },
    "create_cluster_7_workers": {
        "prompt": "create a cluster with 7 workers",
        "expected_nodes": 8,
        "validation": "cluster_ready",
        "cleanup_strategy": "session",
    },
    "create_cluster_15_workers": {
        "prompt": "create a cluster with 15 workers",
        "expected_nodes": 16,
        "validation": "cluster_ready",
        "cleanup_strategy": "session",
    },
    "create_cluster_31_workers": {
        "prompt": "create a cluster with 31 workers",
        "expected_nodes": 32,
        "validation": "cluster_ready",
        "cleanup_strategy": "session",
    },
}


def slugify(value):
    normalized = re.sub(r"[^a-z0-9-]+", "-", (value or "default").lower())
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    return normalized or "default"


def namespace_from_session(session_id):
    return f"tfm-{slugify(session_id)}"[:63].rstrip("-")


def minikube_profile_from_session(session_id):
    return f"kaf-{slugify(session_id)}"[:40].rstrip("-")


def make_session_id(session_prefix, run_id, scenario_name, mode, model, iteration):
    digest_source = f"{run_id}|{scenario_name}|{mode}|{model}|{iteration}"
    digest = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:8]
    scenario_token = "".join(part[0] for part in scenario_name.split("_") if part)[:4]
    mode_token = "full" if mode == "full_ai_experimental" else "hyb"
    model_token = slugify(model).replace("-", "")[:4]
    prefix = slugify(session_prefix)[:8]
    return f"{prefix}-{scenario_token}-{mode_token}-{model_token}-{iteration}-{digest}"


def run_command(command, timeout=120):
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


def kubectl_context_exists(context):
    code, out, _ = run_command(["kubectl", "config", "get-contexts", "-o", "name"])
    return code == 0 and context in [line.strip() for line in out.splitlines()]


def kubectl_context(session_id):
    profile = minikube_profile_from_session(session_id)
    if kubectl_context_exists(profile):
        return profile
    return "minikube"


def kubectl(session_id, *args, timeout=120):
    return run_command(["kubectl", "--context", kubectl_context(session_id), *args], timeout)


def post_deploy(api_url, text, session_id, llm_model, generation_mode, timeout=900):
    payload = {
        "text": text,
        "session_id": session_id,
        "llm_model": llm_model,
        "generation_mode": generation_mode,
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        api_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            elapsed = time.perf_counter() - started
            return True, elapsed, json.loads(body), ""
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        elapsed = time.perf_counter() - started
        return False, elapsed, {}, str(exc)


def delete_session(api_url, session_id, timeout=600):
    session_url = api_url.rsplit("/", 1)[0] + f"/sessions/{session_id}"
    request = urllib.request.Request(session_url, method="DELETE")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return True, body
    except (urllib.error.URLError, TimeoutError) as exc:
        return False, str(exc)


def wait_deployment_ready(session_id, app_name, timeout_seconds=180):
    namespace = namespace_from_session(session_id)
    code, out, err = kubectl(
        session_id,
        "rollout",
        "status",
        f"deployment/{app_name}-deployment",
        "-n",
        namespace,
        f"--timeout={timeout_seconds}s",
        timeout=timeout_seconds + 20,
    )
    if code != 0:
        return False, err or out
    return True, out


def get_deployment_json(session_id, app_name):
    namespace = namespace_from_session(session_id)
    code, out, err = kubectl(
        session_id,
        "get",
        "deployment",
        f"{app_name}-deployment",
        "-n",
        namespace,
        "-o",
        "json",
    )
    if code != 0:
        return False, {}, err or out
    try:
        return True, json.loads(out), ""
    except json.JSONDecodeError as exc:
        return False, {}, str(exc)


def get_service_json(session_id, app_name):
    namespace = namespace_from_session(session_id)
    code, out, err = kubectl(
        session_id,
        "get",
        "service",
        f"{app_name}-service",
        "-n",
        namespace,
        "-o",
        "json",
    )
    if code != 0:
        return False, {}, err or out
    try:
        return True, json.loads(out), ""
    except json.JSONDecodeError as exc:
        return False, {}, str(exc)


def get_nodes_json(session_id):
    code, out, err = kubectl(session_id, "get", "nodes", "-o", "json", timeout=120)
    if code != 0:
        return False, {}, err or out
    try:
        return True, json.loads(out), ""
    except json.JSONDecodeError as exc:
        return False, {}, str(exc)


def validate_result(session_id, scenario, response):
    validation = scenario["validation"]
    app_name = scenario.get("app", response.get("app_name", ""))

    if validation == "api_success":
        ok = response.get("diagnosis") in {
            "healthy",
            "answer_ready",
            "context_answered",
            "deployments_listed",
            "port_found",
        }
        return ok, response.get("reason", "")

    if validation == "deployments_listed":
        ok = response.get("diagnosis") in {"deployments_listed", "context_answered"}
        if not ok:
            return False, response.get("reason", "")
        namespace = namespace_from_session(session_id)
        code, out, err = kubectl(session_id, "get", "deployments", "-n", namespace, "--no-headers")
        if code != 0:
            return False, err or out
        return bool(out.strip()), out.strip() or "no deployments listed"

    if validation == "answer_or_success":
        ok = response.get("diagnosis") in {
            "healthy",
            "answer_ready",
            "context_answered",
            "service_ready",
            "port_ready",
            "port_found",
        }
        return ok, response.get("reason", "")

    if validation == "deployment_deleted":
        namespace = namespace_from_session(session_id)
        code, out, err = kubectl(
            session_id,
            "get",
            "deployment",
            f"{app_name}-deployment",
            "-n",
            namespace,
        )
        if code != 0:
            return True, "deployment not found after delete"
        return False, out or err

    if validation == "service_type":
        ok, service, message = get_service_json(session_id, app_name)
        if not ok:
            return False, message
        expected = scenario.get("expected_service_type")
        actual = service.get("spec", {}).get("type", "")
        return actual == expected, f"service_type={actual}, expected={expected}"

    if validation == "cluster_status":
        ok = response.get("diagnosis") in {"healthy", "cluster_creating"}
        return ok, response.get("reason", "")

    if validation == "cluster_ready":
        if response.get("diagnosis") not in {"healthy", "cluster_created"}:
            return False, response.get("reason", "")

        ok, nodes, message = get_nodes_json(session_id)
        if not ok:
            return False, message

        items = nodes.get("items", [])
        ready_nodes = 0
        for node in items:
            conditions = node.get("status", {}).get("conditions", [])
            if any(
                condition.get("type") == "Ready" and condition.get("status") == "True"
                for condition in conditions
            ):
                ready_nodes += 1

        expected_nodes = scenario.get("expected_nodes")
        if expected_nodes is not None and ready_nodes != expected_nodes:
            return False, f"ready_nodes={ready_nodes}, expected={expected_nodes}"

        return ready_nodes > 0, f"ready_nodes={ready_nodes}"

    if validation == "deployment_ready":
        ready, message = wait_deployment_ready(session_id, app_name)
        if not ready:
            return False, message

        ok, deployment, message = get_deployment_json(session_id, app_name)
        if not ok:
            return False, message

        expected_replicas = scenario.get("expected_replicas")
        if expected_replicas is not None:
            ready_replicas = deployment.get("status", {}).get("readyReplicas", 0)
            if ready_replicas != expected_replicas:
                return False, f"readyReplicas={ready_replicas}, expected={expected_replicas}"

        expected_image = scenario.get("expected_image")
        if expected_image:
            containers = deployment.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
            images = [container.get("image", "") for container in containers]
            if expected_image not in images:
                return False, f"images={images}, expected={expected_image}"

        return True, "deployment ready"

    return False, f"unknown validation={validation}"


def cleanup_namespace(session_id):
    namespace = namespace_from_session(session_id)
    code, out, err = kubectl(
        session_id,
        "delete",
        "namespace",
        namespace,
        "--ignore-not-found",
        "--wait=true",
        timeout=180,
    )
    return code == 0, (out or "") + (err or "")


def cleanup_after_run(api_url, session_id, scenario):
    if scenario.get("cleanup_strategy") == "session":
        return delete_session(api_url, session_id)
    return cleanup_namespace(session_id)


def percentile(values, pct):
    if not values:
        return ""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[int(rank)]
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def summarize(records):
    groups = {}
    for record in records:
        key = (record["scenario"], record["generation_mode"], record["llm_model"])
        groups.setdefault(key, []).append(record)

    rows = []
    for (scenario, mode, model), items in sorted(groups.items()):
        valid_items = [item for item in items if item["validation_success"] == "true"]
        times = [float(item["http_time_seconds"]) for item in valid_items]
        rows.append(
            {
                "scenario": scenario,
                "generation_mode": mode,
                "llm_model": model,
                "runs": len(items),
                "valid_runs": len(valid_items),
                "success_rate": round(len(valid_items) / len(items), 4) if items else 0,
                "mean_seconds": round(statistics.mean(times), 4) if times else "",
                "stdev_seconds": round(statistics.stdev(times), 4) if len(times) > 1 else "",
                "min_seconds": round(min(times), 4) if times else "",
                "q1_seconds": round(percentile(times, 0.25), 4) if times else "",
                "median_seconds": round(percentile(times, 0.50), 4) if times else "",
                "q3_seconds": round(percentile(times, 0.75), 4) if times else "",
                "max_seconds": round(max(times), 4) if times else "",
            }
        )
    return rows


def write_csv(path, rows):
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def run_evaluation(args):
    selected_scenarios = [name for name in args.scenarios if name in SCENARIOS]
    unknown = [name for name in args.scenarios if name not in SCENARIOS]
    if unknown:
        raise SystemExit(f"Unknown scenarios: {', '.join(unknown)}")

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir) / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    records = []
    if args.start_iteration < 1:
        raise SystemExit("--start-iteration must be >= 1")
    if args.end_iteration is not None and args.end_iteration < args.start_iteration:
        raise SystemExit("--end-iteration must be >= --start-iteration")

    end_iteration = args.end_iteration or args.repetitions
    iterations = list(range(args.start_iteration, end_iteration + 1))
    total = len(selected_scenarios) * len(args.modes) * len(args.models) * len(iterations)
    current = 0

    for model in args.models:
        for mode in args.modes:
            for scenario_name in selected_scenarios:
                scenario = SCENARIOS[scenario_name]
                for iteration in iterations:
                    current += 1
                    session_id = make_session_id(
                        args.session_prefix, run_id, scenario_name, mode, model, iteration
                    )
                    print(f"[{current}/{total}] {scenario_name} | {mode} | {model} | run {iteration}")

                    setup_prompt = scenario.get("setup_prompt", "")
                    setup_success = True
                    setup_error = ""
                    if setup_prompt:
                        setup_model = args.setup_model or model
                        setup_success, _, setup_response, setup_error = post_deploy(
                            args.api_url,
                            setup_prompt,
                            session_id,
                            setup_model,
                            args.setup_mode,
                            timeout=args.request_timeout,
                        )
                        if setup_response.get("error"):
                            setup_success = False
                            setup_error = setup_response.get("details") or setup_response.get("error")

                        if setup_success:
                            setup_success, setup_error = validate_result(
                                session_id,
                                {
                                    "validation": "deployment_ready",
                                    "app": scenario.get("app", "nginx"),
                                    "expected_replicas": 1,
                                },
                                setup_response,
                            )

                    if setup_success:
                        request_ok, http_time, response, request_error = post_deploy(
                            args.api_url,
                            scenario["prompt"],
                            session_id,
                            model,
                            mode,
                            timeout=args.request_timeout,
                        )
                        backend_error = ""
                        if response.get("error"):
                            request_ok = False
                            backend_error = response.get("details") or response.get("error")
                        validation_success, validation_message = (
                            validate_result(session_id, scenario, response)
                            if request_ok
                            else (False, backend_error or request_error)
                        )
                    else:
                        request_ok = False
                        http_time = 0.0
                        response = {}
                        request_error = setup_error
                        backend_error = setup_error
                        validation_success = False
                        validation_message = f"setup failed: {setup_error}"

                    record = {
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                        "run_id": run_id,
                        "scenario": scenario_name,
                        "iteration": iteration,
                        "generation_mode": mode,
                        "llm_model": model,
                        "session_id": session_id,
                        "setup_generation_mode": args.setup_mode if setup_prompt else "",
                        "setup_llm_model": (args.setup_model or model) if setup_prompt else "",
                        "setup_prompt": setup_prompt,
                        "prompt": scenario["prompt"],
                        "request_success": str(request_ok).lower(),
                        "validation_success": str(validation_success).lower(),
                        "validation_message": validation_message.strip(),
                        "http_time_seconds": round(http_time, 4),
                        "api_execution_time_seconds": response.get("execution_time_seconds", ""),
                        "intent": response.get("intent", ""),
                        "diagnosis": response.get("diagnosis", ""),
                        "reason": response.get("reason", ""),
                        "app_name": response.get("app_name", ""),
                        "image": response.get("image", ""),
                        "replicas": response.get("replicas", ""),
                        "cluster_inventory_json": json.dumps(
                            response.get("cluster_inventory", {}), ensure_ascii=False
                        ),
                        "node_times_json": json.dumps(response.get("metrics", {}), ensure_ascii=False),
                        "error": backend_error or request_error,
                    }
                    records.append(record)

                    with (output_dir / "raw_results.jsonl").open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

                    if args.cleanup:
                        cleanup_after_run(args.api_url, session_id, scenario)

    write_csv(output_dir / "raw_results.csv", records)
    summary_rows = summarize(records)
    write_csv(output_dir / "summary.csv", summary_rows)

    print(f"\nRaw results: {output_dir / 'raw_results.csv'}")
    print(f"Summary:     {output_dir / 'summary.csv'}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run repeatable KubeAgentFlow evaluation experiments through the HTTP API."
    )
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "results" / "evaluation_runs"))
    parser.add_argument("--session-prefix", default="eval")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--start-iteration", type=int, default=1)
    parser.add_argument("--end-iteration", type=int)
    parser.add_argument("--request-timeout", type=int, default=900)
    parser.add_argument("--models", nargs="+", default=[DEFAULT_MODEL])
    parser.add_argument("--modes", nargs="+", default=DEFAULT_MODES)
    parser.add_argument("--setup-mode", default="hybrid_template")
    parser.add_argument("--setup-model", default="")
    parser.add_argument("--scenarios", nargs="+", default=list(SCENARIOS.keys()))
    parser.add_argument("--cleanup", action="store_true", help="Delete the namespace after each run.")
    return parser.parse_args()


if __name__ == "__main__":
    run_evaluation(parse_args())
