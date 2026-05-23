import argparse
import csv
import math
import json
import shutil
import statistics
from pathlib import Path


def read_csv(path):
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_run_rows(run_path):
    csv_path = run_path / "raw_results.csv"
    if csv_path.exists():
        return read_csv(csv_path)

    jsonl_path = run_path / "raw_results.jsonl"
    rows = []
    with jsonl_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                rows.append({key: str(value).lower() if isinstance(value, bool) else value for key, value in row.items()})
    return rows


def write_csv(path, rows):
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def percentile(values, pct):
    values = sorted(values)
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    rank = (len(values) - 1) * pct
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return values[int(rank)]
    weight = rank - low
    return values[low] * (1 - weight) + values[high] * weight


def is_success(row):
    if row.get("validation_success") == "true":
        return True
    if row.get("scenario") == "question_nginx_port":
        return row.get("diagnosis") in {"port_found", "port_ready", "service_ready"}
    return False


def summarize(raw_rows):
    groups = {}
    for row in raw_rows:
        key = (row["scenario"], row["generation_mode"], row["llm_model"])
        groups.setdefault(key, []).append(row)

    summary_rows = []
    for (scenario, mode, model), rows in sorted(groups.items()):
        valid_rows = [row for row in rows if is_success(row)]
        times = [safe_float(row["http_time_seconds"]) for row in valid_rows]
        times = [value for value in times if value is not None]
        summary_rows.append(
            {
                "scenario": scenario,
                "generation_mode": mode,
                "llm_model": model,
                "runs": len(rows),
                "valid_runs": len(valid_rows),
                "success_rate": round(len(valid_rows) / len(rows), 4) if rows else 0,
                "mean_seconds": round(statistics.mean(times), 4) if times else "",
                "stdev_seconds": round(statistics.stdev(times), 4) if len(times) > 1 else "",
                "min_seconds": round(min(times), 4) if times else "",
                "q1_seconds": round(percentile(times, 0.25), 4) if times else "",
                "median_seconds": round(percentile(times, 0.50), 4) if times else "",
                "q3_seconds": round(percentile(times, 0.75), 4) if times else "",
                "max_seconds": round(max(times), 4) if times else "",
            }
        )
    return summary_rows


def main():
    parser = argparse.ArgumentParser(description="Combine several KubeAgentFlow evaluation run folders.")
    parser.add_argument("run_dirs", nargs="+", help="Run directories containing raw_results.csv and summary.csv.")
    parser.add_argument("--output-dir", required=True, help="Directory for combined CSV files.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_rows = []
    for run_dir in args.run_dirs:
        run_path = Path(run_dir)
        raw_rows.extend(read_run_rows(run_path))

    summary_rows = summarize(raw_rows)
    write_csv(output_dir / "raw_results.csv", raw_rows)
    write_csv(output_dir / "summary.csv", summary_rows)

    print(f"Combined raw results: {output_dir / 'raw_results.csv'}")
    print(f"Combined summary:     {output_dir / 'summary.csv'}")


if __name__ == "__main__":
    main()
