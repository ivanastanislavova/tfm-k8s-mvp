import argparse
import csv
import json
import statistics
import subprocess
import time
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHART_DIR = PROJECT_ROOT / "charts" / "nginx"


SCENARIOS = {
    "helm_deploy_nginx": {
        "action": "install",
        "replicas": "1",
        "expected_replicas": 1,
    },
    "helm_deploy_nginx_2_replicas": {
        "action": "install",
        "replicas": "2",
        "expected_replicas": 2,
    },
    "helm_scale_nginx_to_3": {
        "action": "scale",
        "expected_replicas": 3,
    },
    "helm_update_nginx_image": {
        "action": "update_image",
        "expected_replicas": 1,
        "expected_image": "nginx:1.27-alpine",
    },
    "helm_status_nginx": {
        "action": "status",
    },
    "helm_delete_nginx": {
        "action": "delete",
    },
}


def run_command(command, timeout=240):
    started = time.perf_counter()
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    elapsed = time.perf_counter() - started
    return result.returncode, result.stdout, result.stderr, elapsed


def kubectl(context, *args, timeout=180):
    return run_command(["kubectl", "--context", context, *args], timeout=timeout)


def helm(*args, timeout=300):
    return run_command(["helm", *args], timeout=timeout)


def namespace_for(run_id, scenario, iteration):
    token = scenario.replace("helm_", "").replace("_", "-")[:32].strip("-")
    return f"helm-{run_id[-6:]}-{token}-{iteration}"[:63].rstrip("-")


def install_release(namespace, replicas="1", image_tag="latest", context="minikube"):
    return helm(
        "upgrade",
        "--install",
        "nginx",
        str(CHART_DIR),
        "--namespace",
        namespace,
        "--create-namespace",
        "--kube-context",
        context,
        "--set",
        f"replicaCount={replicas}",
        "--set",
        "image.repository=nginx",
        "--set",
        f"image.tag={image_tag}",
        "--wait",
        "--timeout",
        "240s",
        timeout=300,
    )


def upgrade_release(namespace, context, *set_values):
    command = [
        "upgrade",
        "nginx",
        str(CHART_DIR),
        "--namespace",
        namespace,
        "--kube-context",
        context,
        "--reuse-values",
        "--wait",
        "--timeout",
        "240s",
    ]
    for value in set_values:
        command.extend(["--set", value])
    return helm(*command, timeout=300)


def wait_deployment_ready(namespace, replicas, context):
    code, out, err, _ = kubectl(
        context,
        "rollout",
        "status",
        "deployment/nginx-deployment",
        "-n",
        namespace,
        "--timeout=180s",
        timeout=210,
    )
    if code != 0:
        return False, err or out

    code, out, err, _ = kubectl(
        context,
        "get",
        "deployment",
        "nginx-deployment",
        "-n",
        namespace,
        "-o",
        "json",
        timeout=60,
    )
    if code != 0:
        return False, err or out

    data = json.loads(out)
    available = data.get("status", {}).get("availableReplicas", 0)
    desired = data.get("spec", {}).get("replicas", 0)
    if desired != replicas or available < replicas:
        return False, f"expected {replicas} replicas, got desired={desired}, available={available}"
    return True, "deployment ready"


def image_matches(namespace, expected_image, context):
    code, out, err, _ = kubectl(
        context,
        "get",
        "deployment",
        "nginx-deployment",
        "-n",
        namespace,
        "-o",
        "jsonpath={.spec.template.spec.containers[0].image}",
        timeout=60,
    )
    if code != 0:
        return False, err or out
    return out.strip() == expected_image, out.strip()


def cleanup(namespace, context):
    helm("uninstall", "nginx", "--namespace", namespace, "--kube-context", context, "--wait", "--timeout", "120s", timeout=180)
    kubectl(context, "delete", "namespace", namespace, "--ignore-not-found=true", timeout=180)


def run_scenario(run_id, scenario_name, scenario, iteration, context):
    namespace = namespace_for(run_id, scenario_name, iteration)
    action = scenario["action"]
    setup_elapsed = 0.0
    setup_ok = True
    setup_output = ""

    if action in {"scale", "update_image", "status", "delete"}:
        code, out, err, setup_elapsed = install_release(namespace, context=context)
        setup_ok = code == 0
        setup_output = out or err
        if not setup_ok:
            return {
                "scenario": scenario_name,
                "iteration": iteration,
                "namespace": namespace,
                "success": False,
                "seconds": setup_elapsed,
                "setup_seconds": setup_elapsed,
                "validation_message": setup_output,
            }

    started = time.perf_counter()
    code = 0
    out = ""
    err = ""

    if action == "install":
        code, out, err, command_elapsed = install_release(namespace, replicas=scenario["replicas"], context=context)
    elif action == "scale":
        code, out, err, command_elapsed = upgrade_release(namespace, context, "replicaCount=3")
    elif action == "update_image":
        code, out, err, command_elapsed = upgrade_release(namespace, context, "image.tag=1.27-alpine")
    elif action == "status":
        code, out, err, command_elapsed = helm("status", "nginx", "--namespace", namespace, "--kube-context", context, timeout=120)
    elif action == "delete":
        code, out, err, command_elapsed = helm(
            "uninstall",
            "nginx",
            "--namespace",
            namespace,
            "--kube-context",
            context,
            "--wait",
            "--timeout",
            "120s",
            timeout=180,
        )
    else:
        raise ValueError(f"Unknown action: {action}")

    elapsed = time.perf_counter() - started
    success = code == 0
    validation_message = out or err

    if success and action in {"install", "scale", "update_image"}:
        success, validation_message = wait_deployment_ready(namespace, scenario["expected_replicas"], context)
    if success and action == "update_image":
        success, validation_message = image_matches(namespace, scenario["expected_image"], context)
    if success and action == "delete":
        code, out, err, _ = kubectl(context, "get", "deployment", "nginx-deployment", "-n", namespace, timeout=60)
        success = code != 0
        validation_message = "deployment deleted" if success else out

    if action != "delete":
        cleanup(namespace, context)
    else:
        kubectl(context, "delete", "namespace", namespace, "--ignore-not-found=true", timeout=180)

    return {
        "scenario": scenario_name,
        "iteration": iteration,
        "namespace": namespace,
        "success": success,
        "seconds": elapsed,
        "setup_seconds": setup_elapsed,
        "helm_command_seconds": command_elapsed,
        "validation_message": validation_message,
    }


def percentile(values, fraction):
    if not values:
        return ""
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def summarize(records):
    rows = []
    for scenario in sorted({record["scenario"] for record in records}):
        items = [record for record in records if record["scenario"] == scenario]
        values = [record["seconds"] for record in items if record["success"]]
        rows.append(
            {
                "scenario": scenario,
                "runs": len(items),
                "valid_runs": len(values),
                "success_rate": len(values) / len(items) if items else 0,
                "mean_seconds": statistics.mean(values) if values else "",
                "median_seconds": statistics.median(values) if values else "",
                "stdev_seconds": statistics.stdev(values) if len(values) > 1 else 0 if values else "",
                "q1_seconds": percentile(values, 0.25) if values else "",
                "q3_seconds": percentile(values, 0.75) if values else "",
                "min_seconds": min(values) if values else "",
                "max_seconds": max(values) if values else "",
            }
        )
    return rows


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Benchmark Helm baseline operations for KubeAgentFlow results.")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--context", default="minikube")
    parser.add_argument("--scenarios", nargs="+", default=list(SCENARIOS.keys()))
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()

    code, out, err, _ = run_command(["helm", "version"], timeout=30)
    if code != 0:
        raise SystemExit("Helm is not installed or not available in PATH. Install Helm before running this script.")

    selected = [name for name in args.scenarios if name in SCENARIOS]
    unknown = [name for name in args.scenarios if name not in SCENARIOS]
    if unknown:
        raise SystemExit(f"Unknown scenarios: {', '.join(unknown)}")

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir) if args.output_dir else PROJECT_ROOT / "results" / "helm_runs" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    records = []
    total = len(selected) * args.repetitions
    current = 0
    for iteration in range(1, args.repetitions + 1):
        for scenario_name in selected:
            current += 1
            print(f"[{current}/{total}] {scenario_name} run {iteration}")
            record = run_scenario(run_id, scenario_name, SCENARIOS[scenario_name], iteration, args.context)
            records.append(record)
            print(f"  success={record['success']} seconds={record['seconds']:.3f}")

    write_csv(output_dir / "raw_results.csv", records)
    summary = summarize(records)
    write_csv(output_dir / "summary.csv", summary)
    with (output_dir / "raw_results.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"Raw results: {output_dir / 'raw_results.csv'}")
    print(f"Summary:     {output_dir / 'summary.csv'}")


if __name__ == "__main__":
    main()
