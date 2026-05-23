import argparse
import csv
import hashlib
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_API_URL = "http://127.0.0.1:8000/deploy"


SCENARIOS = {
    "image_typo_ngnix": {
        "failure_type": "Image typo",
        "prompt": "deploy repairtest using ngnix",
        "expected_detection": "image_pull_error",
        "expected_final_image": "nginx",
        "expected_final_diagnosis": "healthy",
    },
    "unknown_image_pull": {
        "failure_type": "ErrImagePull",
        "prompt": "deploy repairtest using doesnotexist/unknown-image:latest",
        "expected_detection": "image_pull_error",
        "expected_final_diagnosis": "healthy",
    },
    "crash_loop_busybox": {
        "failure_type": "CrashLoopBackOff",
        "prompt": "deploy repairtest using busybox",
        "expected_detection": "crash_loop",
        "expected_final_diagnosis": "healthy",
    },
}


def slugify(value):
    normalized = re.sub(r"[^a-z0-9-]+", "-", (value or "default").lower())
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    return normalized or "default"


def namespace_from_session(session_id):
    return f"tfm-{slugify(session_id)}"[:63].rstrip("-")


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


def kubectl(session_id, *args, timeout=120):
    return run_command(["kubectl", "--context", "minikube", *args], timeout=timeout)


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


def delete_session(api_url, session_id, timeout=240):
    session_url = api_url.rsplit("/", 1)[0] + f"/sessions/{session_id}"
    request = urllib.request.Request(session_url, method="DELETE")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return True, response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        return False, str(exc)


def get_final_image(session_id, app_name):
    namespace = namespace_from_session(session_id)
    code, out, err = kubectl(
        session_id,
        "get",
        "deployment",
        f"{app_name}-deployment",
        "-n",
        namespace,
        "-o",
        "jsonpath={.spec.template.spec.containers[0].image}",
        timeout=60,
    )
    if code != 0:
        return ""
    return out.strip()


def make_session_id(run_id, scenario_name, iteration):
    digest = hashlib.sha1(f"{run_id}|{scenario_name}|{iteration}".encode("utf-8")).hexdigest()[:8]
    return f"repair-{scenario_name.replace('_', '-')[:18]}-{iteration}-{digest}"[:63].rstrip("-")


def history_text(response):
    history = response.get("history", [])
    if isinstance(history, list):
        return "\n".join(str(item) for item in history)
    return str(history)


def evaluate_response(session_id, scenario, response):
    history = history_text(response)
    diagnosis = response.get("diagnosis", "")
    reason = response.get("reason", "")
    app_name = response.get("app_name", "repairtest") or "repairtest"
    final_image = get_final_image(session_id, app_name)

    detected = scenario["expected_detection"] in history or scenario["expected_detection"] == diagnosis
    repaired = diagnosis == scenario.get("expected_final_diagnosis", "healthy")

    expected_image = scenario.get("expected_final_image")
    if expected_image:
        repaired = repaired and final_image == expected_image

    return {
        "detected": detected,
        "repaired": repaired,
        "final_diagnosis": diagnosis,
        "final_reason": reason,
        "final_image": final_image,
        "history": history,
    }


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def summarize(records):
    rows = []
    for scenario_name in sorted({record["scenario"] for record in records}):
        items = [record for record in records if record["scenario"] == scenario_name]
        rows.append(
            {
                "scenario": scenario_name,
                "failure_type": items[0]["failure_type"],
                "runs": len(items),
                "detected": sum(1 for item in items if item["detected"]),
                "repaired": sum(1 for item in items if item["repaired"]),
                "detection_rate": sum(1 for item in items if item["detected"]) / len(items),
                "repair_rate": sum(1 for item in items if item["repaired"]) / len(items),
                "mean_seconds": sum(float(item["seconds"]) for item in items) / len(items),
            }
        )
    return rows


def main():
    parser = argparse.ArgumentParser(description="Evaluate KubeAgentFlow error diagnosis and self-repair behavior.")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--model", default="llama3.2:3b")
    parser.add_argument("--mode", default="hybrid_template")
    parser.add_argument("--scenarios", nargs="+", default=list(SCENARIOS.keys()))
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()

    selected = [name for name in args.scenarios if name in SCENARIOS]
    unknown = [name for name in args.scenarios if name not in SCENARIOS]
    if unknown:
        raise SystemExit(f"Unknown scenarios: {', '.join(unknown)}")

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir) if args.output_dir else PROJECT_ROOT / "results" / "self_repair_runs" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    records = []
    total = len(selected) * args.repetitions
    current = 0
    for iteration in range(1, args.repetitions + 1):
        for scenario_name in selected:
            current += 1
            scenario = SCENARIOS[scenario_name]
            session_id = make_session_id(run_id, scenario_name, iteration)
            print(f"[{current}/{total}] {scenario_name} run {iteration}")
            ok, seconds, response, error = post_deploy(
                args.api_url,
                scenario["prompt"],
                session_id,
                args.model,
                args.mode,
            )
            evaluated = evaluate_response(session_id, scenario, response) if ok else {
                "detected": False,
                "repaired": False,
                "final_diagnosis": "request_failed",
                "final_reason": error,
                "final_image": "",
                "history": "",
            }
            record = {
                "scenario": scenario_name,
                "failure_type": scenario["failure_type"],
                "iteration": iteration,
                "session_id": session_id,
                "prompt": scenario["prompt"],
                "seconds": round(seconds, 4),
                "request_ok": ok,
                **evaluated,
            }
            records.append(record)
            print(
                f"  detected={record['detected']} repaired={record['repaired']} "
                f"diagnosis={record['final_diagnosis']} image={record['final_image']}"
            )
            if args.cleanup:
                delete_session(args.api_url, session_id)

    write_csv(output_dir / "raw_results.csv", records)
    write_csv(output_dir / "summary.csv", summarize(records))
    with (output_dir / "raw_results.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"Raw results: {output_dir / 'raw_results.csv'}")
    print(f"Summary:     {output_dir / 'summary.csv'}")


if __name__ == "__main__":
    main()
