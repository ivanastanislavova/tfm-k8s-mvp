import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


def load_rows(run_dirs):
    rows = []
    for run_dir in run_dirs:
        raw_path = Path(run_dir) / "raw_results.csv"
        df = pd.read_csv(raw_path)
        rows.append(df)
    return pd.concat(rows, ignore_index=True)


def extract_cluster_data(df):
    records = []
    for _, row in df.iterrows():
        if str(row.get("validation_success", "")).lower() != "true":
            continue
        inventory = json.loads(row.get("cluster_inventory_json", "{}") or "{}")
        timings = inventory.get("timings", {})
        workers = len(inventory.get("workers", []))
        cluster_size = workers + 1 if workers else None
        if not cluster_size or not timings:
            continue
        records.append(
            {
                "cluster_size": cluster_size,
                "master_node_deployment": timings.get("master_node_deployment_seconds", 0),
                "complete_cluster_deployment": timings.get(
                    "complete_cluster_deployment_seconds",
                    row.get("http_time_seconds", 0),
                ),
                "extra_node_addition": timings.get(
                    "average_extra_node_addition_seconds", 0
                ),
                "end_to_end": float(row.get("http_time_seconds", 0)),
            }
        )
    return pd.DataFrame(records).sort_values("cluster_size")


def plot_cluster_deployment(data, output_dir):
    fig, ax = plt.subplots(figsize=(9, 5.4))

    series = [
        (
            "master_node_deployment",
            "Control-plane deployment",
            "#1f77b4",
            (-18, 12),
        ),
        (
            "complete_cluster_deployment",
            "Complete cluster deployment",
            "#ff7f0e",
            (0, 10),
        ),
        (
            "extra_node_addition",
            "Average worker addition",
            "#2ca02c",
            (20, -15),
        ),
    ]

    ax.plot(
        data["cluster_size"],
        data["master_node_deployment"],
        marker="o",
        linewidth=2.2,
        color="#1f77b4",
        label="Control-plane deployment",
    )
    ax.plot(
        data["cluster_size"],
        data["complete_cluster_deployment"],
        marker="o",
        linewidth=2.2,
        color="#ff7f0e",
        label="Complete cluster deployment",
    )
    ax.plot(
        data["cluster_size"],
        data["extra_node_addition"],
        marker="o",
        linewidth=2.2,
        color="#2ca02c",
        label="Average worker addition",
    )

    for _, row in data.iterrows():
        for column, _, color, offset in series:
            ax.annotate(
                f"{row[column]:.1f}",
                (row["cluster_size"], row[column]),
                textcoords="offset points",
                xytext=offset,
                ha="center",
                va="center",
                fontsize=8,
                color=color,
                bbox={
                    "boxstyle": "round,pad=0.18",
                    "facecolor": "white",
                    "edgecolor": color,
                    "linewidth": 0.6,
                    "alpha": 0.88,
                },
            )

    ax.set_title("Cluster deployment evaluation")
    ax.set_xlabel("Cluster size (nodes)")
    ax.set_ylabel("Seconds")
    ax.set_xticks(data["cluster_size"].tolist())
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    ax.legend(loc="upper left", fontsize=8)
    plt.tight_layout()
    plt.savefig(output_dir / "paper_cluster_deployment.png", dpi=220)
    plt.close()


def main():
    parser = argparse.ArgumentParser(description="Generate cluster deployment paper figures.")
    parser.add_argument("run_dirs", nargs="+", help="Evaluation run directories.")
    parser.add_argument("--output-dir", default="results/cluster_figures")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    raw = load_rows(args.run_dirs)
    data = extract_cluster_data(raw)
    data.to_csv(output_dir / "cluster_deployment_summary.csv", index=False)
    plot_cluster_deployment(data, output_dir)

    print(f"Cluster figures written to {output_dir}")


if __name__ == "__main__":
    main()
