import argparse
import csv
import json
import statistics
import subprocess
import time
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


SCENARIOS = {
    "manual_deploy_nginx": {
        "action": "install",
        "replicas": 1,
        "expected_replicas": 1,
    },
    "manual_deploy_nginx_2_replicas": {
        "action": "install",
        "replicas": 2,
        "expected_replicas": 2,
    },
    "manual_scale_nginx_to_3": {
        "action": "scale",
        "expected_replicas": 3,
        "setup": True,
    },
    "manual_update_nginx_image": {
        "action": "update_image",
        "expected_replicas": 1,
        "expected_image": "nginx:1.27-alpine",
        "setup": True,
    },
    "manual_status_nginx": {
        "action": "status",
        "setup": True,
    },
    "manual_delete_nginx": {
        "action": "delete",
        "setup": True,
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


def namespace_for(run_id, scenario, iteration):
    token = scenario.replace("manual_", "").replace("_", "-")[:32].strip("-")
    return f"manual-{run_id[-6:]}-{token}-{iteration}"[:63].rstrip("-")


def deployment_manifest(replicas=1, image="nginx"):
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "nginx-deployment"},
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": {"app": "nginx"}},
            "template": {
                "metadata": {"labels": {"app": "nginx"}},
                "spec": {
                    "containers": [
                        {
                            "name": "nginx",
                            "image": image,
                            "ports": [{"containerPort": 80}],
                        }
                    ]
                },
            },
        },
    }


def service_manifest(service_type="NodePort"):
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "nginx-service"},
        "spec": {
            "selector": {"app": "nginx"},
            "ports": [{"protocol": "TCP", "port": 80, "targetPort": 80}],
            "type": service_type,
        },
    }


def write_manifest(path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def prepare_files(output_dir, namespace, replicas=1, image="nginx"):
    manifest_dir = output_dir / "manifests" / namespace
    manifest_dir.mkdir(parents=True, exist_ok=True)
    deployment_path = manifest_dir / "deployment.json"
    service_path = manifest_dir / "service.json"
    write_manifest(deployment_path, deployment_manifest(replicas=replicas, image=image))
    write_manifest(service_path, service_manifest())
    return deployment_path, service_path


def apply_manual(namespace, output_dir, context, replicas=1, image="nginx"):
    deployment_path, service_path = prepare_files(output_dir, namespace, replicas, image)
    command_count = 0
    outputs = []

    for command in [
        ("create namespace", ["create", "namespace", namespace]),
        ("apply deployment", ["apply", "-n", namespace, "-f", str(deployment_path)]),
        ("apply service", ["apply", "-n", namespace, "-f", str(service_path)]),
        ("rollout status", ["rollout", "status", "deployment/nginx-deployment", "-n", namespace, "--timeout=180s"]),
    ]:
        label, args = command
        code, out, err, _ = kubectl(context, *args, timeout=210)
        command_count += 1
        outputs.append(f"=== {label} ===\n{out or err}")
        if code != 0:
            return False, "\n".join(outputs), command_count

    return True, "\n".join(outputs), command_count


def wait_deployment_ready(namespace, replicas, context):
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
    kubectl(context, "delete", "namespace", namespace, "--ignore-not-found=true", timeout=180)


def run_scenario(run_id, scenario_name, scenario, iteration, context, output_dir):
    namespace = namespace_for(run_id, scenario_name, iteration)
    action = scenario["action"]
    setup_elapsed = 0.0
    setup_command_count = 0
    command_count = 0

    if scenario.get("setup"):
        setup_started = time.perf_counter()
        ok, message, setup_command_count = apply_manual(namespace, output_dir, context)
        setup_elapsed = time.perf_counter() - setup_started
        if not ok:
            cleanup(namespace, context)
            return {
                "scenario": scenario_name,
                "iteration": iteration,
                "namespace": namespace,
                "success": False,
                "seconds": setup_elapsed,
                "setup_seconds": setup_elapsed,
                "command_count": setup_command_count,
                "validation_message": message,
            }

    started = time.perf_counter()
    success = True
    validation_message = ""

    if action == "install":
        success, validation_message, command_count = apply_manual(
            namespace,
            output_dir,
            context,
            replicas=scenario["replicas"],
        )
        if success:
            success, validation_message = wait_deployment_ready(namespace, scenario["expected_replicas"], context)
    elif action == "scale":
        code, out, err, _ = kubectl(context, "scale", "deployment/nginx-deployment", "-n", namespace, "--replicas=3", timeout=60)
        command_count += 1
        success = code == 0
        validation_message = out or err
        if success:
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
            command_count += 1
            success = code == 0
            validation_message = out or err
        if success:
            success, validation_message = wait_deployment_ready(namespace, scenario["expected_replicas"], context)
    elif action == "update_image":
        code, out, err, _ = kubectl(
            context,
            "set",
            "image",
            "deployment/nginx-deployment",
            "nginx=nginx:1.27-alpine",
            "-n",
            namespace,
            timeout=60,
        )
        command_count += 1
        success = code == 0
        validation_message = out or err
        if success:
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
            command_count += 1
            success = code == 0
            validation_message = out or err
        if success:
            success, validation_message = image_matches(namespace, scenario["expected_image"], context)
    elif action == "status":
        code, out, err, _ = kubectl(context, "get", "deployment", "nginx-deployment", "-n", namespace, timeout=60)
        command_count += 1
        success = code == 0
        validation_message = out or err
    elif action == "delete":
        for args in [
            ("delete deployment", ["delete", "deployment", "nginx-deployment", "-n", namespace, "--ignore-not-found=true"]),
            ("delete service", ["delete", "service", "nginx-service", "-n", namespace, "--ignore-not-found=true"]),
        ]:
            _, command_args = args
            code, out, err, _ = kubectl(context, *command_args, timeout=60)
            command_count += 1
            success = success and code == 0
            validation_message += out or err
        code, out, err, _ = kubectl(context, "get", "deployment", "nginx-deployment", "-n", namespace, timeout=60)
        command_count += 1
        success = success and code != 0
        validation_message = "deployment deleted" if success else validation_message + out
    else:
        raise ValueError(f"Unknown action: {action}")

    elapsed = time.perf_counter() - started
    cleanup(namespace, context)

    return {
        "scenario": scenario_name,
        "iteration": iteration,
        "namespace": namespace,
        "success": success,
        "seconds": elapsed,
        "setup_seconds": setup_elapsed,
        "command_count": command_count,
        "setup_command_count": setup_command_count,
        "total_command_count": command_count + setup_command_count,
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
        command_counts = [record["command_count"] for record in items if record["success"]]
        total_command_counts = [record["total_command_count"] for record in items if record["success"]]
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
                "mean_command_count": statistics.mean(command_counts) if command_counts else "",
                "mean_total_command_count": statistics.mean(total_command_counts) if total_command_counts else "",
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
    parser = argparse.ArgumentParser(description="Benchmark manual kubectl baseline operations for KubeAgentFlow results.")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--context", default="minikube")
    parser.add_argument("--scenarios", nargs="+", default=list(SCENARIOS.keys()))
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()

    selected = [name for name in args.scenarios if name in SCENARIOS]
    unknown = [name for name in args.scenarios if name not in SCENARIOS]
    if unknown:
        raise SystemExit(f"Unknown scenarios: {', '.join(unknown)}")

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir) if args.output_dir else PROJECT_ROOT / "results" / "manual_runs" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    records = []
    total = len(selected) * args.repetitions
    current = 0
    for iteration in range(1, args.repetitions + 1):
        for scenario_name in selected:
            current += 1
            print(f"[{current}/{total}] {scenario_name} run {iteration}")
            record = run_scenario(run_id, scenario_name, SCENARIOS[scenario_name], iteration, args.context, output_dir)
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
