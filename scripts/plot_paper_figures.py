import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
import pandas as pd


MODE_LABELS = {
    "hybrid_template": "Hybrid",
    "full_ai_experimental": "Full AI",
}

MODEL_LABELS = {
    "llama3.2:3b": "Llama 3.2 3B",
    "mistral:latest": "Mistral 7B",
}

SCENARIO_LABELS = {
    "deploy_nginx": "Deploy nginx",
    "deploy_nginx_2_replicas": "Deploy nginx\n2 replicas",
    "scale_nginx_to_3": "Scale to\n3 replicas",
    "update_nginx_image": "Update image",
    "update_nginx_service_clusterip": "Change service\ntype",
    "status_nginx": "Status",
    "list_deployments": "List\ndeployments",
    "question_nginx_port": "Ask port",
    "delete_nginx": "Delete",
    "cluster_status": "Cluster\nstatus",
    "create_cluster_1_worker": "Create cluster\n1 worker",
    "create_cluster_3_workers": "Create cluster\n3 workers",
}


def load_raw(run_dir: Path) -> pd.DataFrame:
    raw_path = run_dir / "raw_results.csv"
    if raw_path.exists():
        return pd.read_csv(raw_path)

    jsonl_path = run_dir / "raw_results.jsonl"
    if jsonl_path.exists():
        return pd.read_json(jsonl_path, lines=True)

    raise FileNotFoundError(f"No raw_results.csv or raw_results.jsonl found in {run_dir}")


def add_labels(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["mode_label"] = df["generation_mode"].map(MODE_LABELS).fillna(df["generation_mode"])
    df["model_label"] = df["llm_model"].map(MODEL_LABELS).fillna(df["llm_model"])
    df["series"] = df["mode_label"] + " / " + df["model_label"]
    df["scenario_label"] = df["scenario"].map(SCENARIO_LABELS).fillna(df["scenario"])
    df["validation_success"] = df["validation_success"].astype(str).str.lower() == "true"
    df["http_time_seconds"] = pd.to_numeric(df["http_time_seconds"], errors="coerce")
    return df


def save_bar_mean(summary: pd.DataFrame, output_dir: Path):
    scenarios = [
        "deploy_nginx",
        "deploy_nginx_2_replicas",
        "scale_nginx_to_3",
        "update_nginx_image",
        "status_nginx",
        "delete_nginx",
    ]
    df = summary[summary["scenario"].isin(scenarios)].copy()
    df["scenario_label"] = df["scenario"].map(SCENARIO_LABELS)
    df["series"] = (
        df["generation_mode"].map(MODE_LABELS).fillna(df["generation_mode"])
        + " / "
        + df["llm_model"].map(MODEL_LABELS).fillna(df["llm_model"])
    )
    df["mean_seconds"] = pd.to_numeric(df["mean_seconds"], errors="coerce")

    pivot = df.pivot(index="scenario_label", columns="series", values="mean_seconds")
    pivot = pivot.reindex([SCENARIO_LABELS[item] for item in scenarios])

    ax = pivot.plot(kind="bar", figsize=(12, 6), width=0.82)
    ax.set_title("Mean execution time by operation, mode and model")
    ax.set_xlabel("")
    ax.set_ylabel("Mean time (seconds)")
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    ax.legend(title="", ncols=2, fontsize=8)
    plt.xticks(rotation=0)
    plt.tight_layout()
    plt.savefig(output_dir / "paper_mean_time_by_operation.png", dpi=220)
    plt.close()


def save_success_rate(summary: pd.DataFrame, output_dir: Path):
    scenarios = [
        "deploy_nginx",
        "deploy_nginx_2_replicas",
        "scale_nginx_to_3",
        "update_nginx_image",
        "status_nginx",
        "delete_nginx",
    ]
    df = summary[summary["scenario"].isin(scenarios)].copy()
    df["scenario_label"] = df["scenario"].map(SCENARIO_LABELS)
    df["series"] = (
        df["generation_mode"].map(MODE_LABELS).fillna(df["generation_mode"])
        + " / "
        + df["llm_model"].map(MODEL_LABELS).fillna(df["llm_model"])
    )
    df["success_rate"] = pd.to_numeric(df["success_rate"], errors="coerce") * 100

    pivot = df.pivot(index="scenario_label", columns="series", values="success_rate")
    pivot = pivot.reindex([SCENARIO_LABELS[item] for item in scenarios])

    ax = pivot.plot(kind="bar", figsize=(12, 5.4), width=0.82)
    ax.set_title("Success rate by operation, mode and model")
    ax.set_xlabel("")
    ax.set_ylabel("Successful executions (%)")
    ax.set_ylim(0, 105)
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    ax.legend(title="", ncols=2, fontsize=8)
    plt.xticks(rotation=0)
    plt.tight_layout()
    plt.savefig(output_dir / "paper_success_rate.png", dpi=220)
    plt.close()


def save_winner_boxplot(raw: pd.DataFrame, output_dir: Path):
    scenarios = [
        "deploy_nginx",
        "deploy_nginx_2_replicas",
        "scale_nginx_to_3",
        "update_nginx_image",
        "status_nginx",
        "delete_nginx",
    ]
    df = raw[
        (raw["generation_mode"] == "hybrid_template")
        & (raw["llm_model"] == "llama3.2:3b")
        & (raw["scenario"].isin(scenarios))
        & (raw["validation_success"])
    ].copy()
    df["scenario_label"] = df["scenario"].map(SCENARIO_LABELS)

    ordered_labels = [SCENARIO_LABELS[item] for item in scenarios]
    data = [df[df["scenario_label"] == label]["http_time_seconds"].dropna() for label in ordered_labels]

    fig, ax = plt.subplots(figsize=(11, 5.6))
    ax.boxplot(data, labels=ordered_labels, showmeans=True)
    ax.set_title("Execution time distribution for the selected configuration\nHybrid + Llama 3.2 3B")
    ax.set_ylabel("Time (seconds)")
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    plt.xticks(rotation=0)
    plt.tight_layout()
    plt.savefig(output_dir / "paper_hybrid_llama_boxplot.png", dpi=220)
    plt.close()


def save_node_time_breakdown(raw: pd.DataFrame, output_dir: Path):
    rows = []
    for _, row in raw.iterrows():
        if not row["validation_success"]:
            continue
        if row["generation_mode"] != "hybrid_template" or row["llm_model"] != "llama3.2:3b":
            continue
        if row["scenario"] not in {"deploy_nginx", "scale_nginx_to_3", "update_nginx_image", "delete_nginx"}:
            continue
        try:
            node_times = json.loads(row["node_times_json"])
        except (TypeError, ValueError):
            continue
        for node, value in node_times.items():
            rows.append(
                {
                    "scenario": row["scenario"],
                    "scenario_label": SCENARIO_LABELS.get(row["scenario"], row["scenario"]),
                    "node": node,
                    "seconds": float(value),
                }
            )

    if not rows:
        return

    df = pd.DataFrame(rows)
    grouped = df.groupby(["scenario_label", "node"], as_index=False)["seconds"].mean()
    pivot = grouped.pivot(index="scenario_label", columns="node", values="seconds").fillna(0)

    scenario_order = [
        SCENARIO_LABELS["deploy_nginx"],
        SCENARIO_LABELS["scale_nginx_to_3"],
        SCENARIO_LABELS["update_nginx_image"],
        SCENARIO_LABELS["delete_nginx"],
    ]
    node_order = [
        "validate",
        "interpretation_time_seconds",
        "generate_yaml_template",
        "deploy_kubectl",
        "scale",
        "delete",
        "observe_kubernetes",
        "diagnose_deterministic",
    ]
    pivot = pivot.reindex(index=[item for item in scenario_order if item in pivot.index])
    pivot = pivot.reindex(columns=[item for item in node_order if item in pivot.columns]).fillna(0)
    pivot.to_csv(output_dir / "paper_node_time_breakdown.csv")

    values = pivot.to_numpy()
    positive = values[values > 0]
    if positive.size == 0:
        return

    masked = values.copy()
    masked[masked <= 0] = positive.min() / 10

    fig, ax = plt.subplots(figsize=(12.5, 5.8))
    image = ax.imshow(
        masked,
        cmap="YlGnBu",
        norm=LogNorm(vmin=positive.min() / 2, vmax=positive.max()),
        aspect="auto",
    )

    ax.set_title("Average graph-node execution time\nHybrid + Llama 3.2 3B")
    ax.set_xlabel("Graph node")
    ax.set_ylabel("Operation")
    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels(
        [column.replace("_time_seconds", "").replace("_", "\n") for column in pivot.columns],
        fontsize=8,
    )
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels(pivot.index)

    for row_index, scenario in enumerate(pivot.index):
        for col_index, node in enumerate(pivot.columns):
            value = pivot.loc[scenario, node]
            label = "0" if value <= 0 else f"{value:.3f}"
            ax.text(
                col_index,
                row_index,
                label,
                ha="center",
                va="center",
                fontsize=7,
                color="white" if value > positive.max() * 0.35 else "black",
            )

    colorbar = fig.colorbar(image, ax=ax)
    colorbar.set_label("Mean time (seconds, log scale)")
    ax.set_xticks([x - 0.5 for x in range(1, len(pivot.columns))], minor=True)
    ax.set_yticks([y - 0.5 for y in range(1, len(pivot.index))], minor=True)
    ax.grid(which="minor", color="white", linestyle="-", linewidth=1.2)
    ax.tick_params(which="minor", bottom=False, left=False)
    plt.tight_layout()
    plt.savefig(output_dir / "paper_node_time_breakdown.png", dpi=220)
    plt.close()


def main():
    parser = argparse.ArgumentParser(description="Generate paper-ready figures from evaluation results.")
    parser.add_argument("run_dir", help="Directory containing raw_results and summary.csv.")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    output_dir = run_dir / "paper_figures"
    output_dir.mkdir(exist_ok=True)

    raw = add_labels(load_raw(run_dir))
    summary = pd.read_csv(run_dir / "summary.csv")

    save_bar_mean(summary, output_dir)
    save_success_rate(summary, output_dir)
    save_winner_boxplot(raw, output_dir)
    save_node_time_breakdown(raw, output_dir)

    print(f"Paper figures written to {output_dir}")


if __name__ == "__main__":
    main()
