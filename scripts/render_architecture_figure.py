from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch


OUT_DIR = Path("results/architecture")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def add_box(ax, x, y, w, h, text, fc="#ffffff", ec="#1f2937", fontsize=10, weight="normal"):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.018,rounding_size=0.02",
        linewidth=1.0,
        edgecolor=ec,
        facecolor=fc,
    )
    ax.add_patch(patch)
    ax.text(
        x + w / 2,
        y + h / 2,
        text,
        ha="center",
        va="center",
        fontsize=fontsize,
        fontweight=weight,
        color="#111827",
        wrap=True,
    )
    return patch


def add_layer(ax, y, h, title, fc, ec="#111827"):
    patch = FancyBboxPatch(
        (0.035, y),
        0.93,
        h,
        boxstyle="round,pad=0.012,rounding_size=0.012",
        linewidth=1.0,
        edgecolor=ec,
        facecolor=fc,
    )
    ax.add_patch(patch)
    ax.text(0.05, y + h - 0.035, title, ha="left", va="top", fontsize=10, fontweight="bold")
    return patch


def arrow(ax, start, end, dashed=False, color="#111827", lw=1.2):
    ax.annotate(
        "",
        xy=end,
        xytext=start,
        arrowprops=dict(
            arrowstyle="-|>",
            color=color,
            lw=lw,
            linestyle="--" if dashed else "-",
            shrinkA=4,
            shrinkB=4,
        ),
    )


def main():
    fig, ax = plt.subplots(figsize=(15.5, 9.4))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    ax.text(
        0.5,
        0.985,
        "Architecture of the Proposed Agentic Kubernetes Orchestration System",
        ha="center",
        va="top",
        fontsize=17,
        fontweight="bold",
    )

    add_layer(ax, 0.820, 0.105, "Web Layer", "#eef8e8")
    add_layer(ax, 0.675, 0.120, "Backend Layer", "#e8f3ff")
    add_layer(ax, 0.335, 0.305, "LangGraph Orchestrator (stateful agentic workflow)", "#fff8dc")
    add_layer(ax, 0.105, 0.180, "Kubernetes Execution Layer", "#f5ecff")

    ax.text(0.5, 0.915, "User request", ha="center", va="center", fontsize=9)
    web = add_box(ax, 0.395, 0.850, 0.21, 0.052, "Web-based chat interface")

    api = add_box(ax, 0.10, 0.710, 0.18, 0.052, "API Server\n(FastAPI)", fc="#ffffff")
    parser = add_box(ax, 0.40, 0.710, 0.20, 0.052, "Intent Parser\n(rule-based + LLM)", fc="#e2efff")
    memory = add_box(ax, 0.72, 0.710, 0.20, 0.052, "Conversation Manager\n(session context)")

    router = add_box(ax, 0.39, 0.565, 0.22, 0.052, "Shared AgentState\nand route selection", fc="#ffffff")
    validator = add_box(ax, 0.06, 0.465, 0.12, 0.058, "Validator\nAgent")
    template = add_box(ax, 0.23, 0.465, 0.15, 0.058, "YAML Generator\n(templates)", fc="#ffffff")
    llm_yaml = add_box(ax, 0.23, 0.377, 0.15, 0.058, "LLM YAML\nGenerator", fc="#e2efff")
    execution = add_box(ax, 0.44, 0.465, 0.15, 0.058, "Execution Nodes\nkubectl actions")
    monitor = add_box(ax, 0.64, 0.465, 0.12, 0.058, "Monitor\nAgent")
    diagnosis = add_box(ax, 0.82, 0.465, 0.13, 0.058, "Diagnosis\nAgent", fc="#e2efff")
    repair = add_box(ax, 0.82, 0.360, 0.13, 0.058, "Remediation\nAgent", fc="#f3e8ff")
    aux = add_box(ax, 0.44, 0.370, 0.32, 0.055, "Auxiliary nodes: status, logs, describe,\nshow YAML, contextual questions")
    cluster_prov = add_box(ax, 0.06, 0.370, 0.12, 0.055, "Cluster\nprovisioning")

    kube_api = add_box(ax, 0.39, 0.225, 0.22, 0.052, "Kubernetes API / kubectl access")
    deploy = add_box(ax, 0.11, 0.135, 0.13, 0.050, "Deployment")
    service = add_box(ax, 0.30, 0.135, 0.13, 0.050, "Service")
    config = add_box(ax, 0.49, 0.135, 0.13, 0.050, "ConfigMap")
    ingress = add_box(ax, 0.68, 0.135, 0.13, 0.050, "Ingress")
    pods = add_box(ax, 0.84, 0.135, 0.10, 0.050, "Pods")

    arrow(ax, (0.5, 0.907), (0.5, 0.902))
    arrow(ax, (0.5, 0.850), (0.19, 0.762))
    arrow(ax, (0.28, 0.736), (0.40, 0.736))
    arrow(ax, (0.60, 0.736), (0.72, 0.736))
    arrow(ax, (0.82, 0.710), (0.55, 0.617))

    arrow(ax, (0.50, 0.565), (0.12, 0.523))
    arrow(ax, (0.18, 0.494), (0.23, 0.494))
    arrow(ax, (0.38, 0.494), (0.44, 0.494))
    arrow(ax, (0.59, 0.494), (0.64, 0.494))
    arrow(ax, (0.76, 0.494), (0.82, 0.494))
    arrow(ax, (0.18, 0.476), (0.23, 0.406), dashed=True, color="#2563eb")
    arrow(ax, (0.38, 0.406), (0.44, 0.476), dashed=True, color="#2563eb")
    arrow(ax, (0.885, 0.465), (0.885, 0.418), dashed=True, color="#7c3aed")
    arrow(ax, (0.82, 0.405), (0.38, 0.494), dashed=True, color="#7c3aed")
    arrow(ax, (0.12, 0.370), (0.39, 0.250), dashed=True, color="#6b7280")

    arrow(ax, (0.515, 0.465), (0.50, 0.277))
    for x in [0.175, 0.365, 0.555, 0.745, 0.89]:
        arrow(ax, (0.50, 0.225), (x, 0.185))

    # Legend
    add_box(ax, 0.055, 0.032, 0.15, 0.038, "LLM-assisted", fc="#e2efff", fontsize=9)
    add_box(ax, 0.225, 0.032, 0.15, 0.038, "Deterministic/tool-based", fc="#ffffff", fontsize=9)
    add_box(ax, 0.395, 0.032, 0.15, 0.038, "Remediation loop", fc="#f3e8ff", fontsize=9)
    ax.text(0.60, 0.051, "Dashed arrows denote optional or failure-driven paths.", fontsize=9, va="center")

    fig.tight_layout(pad=0.6)
    fig.savefig(OUT_DIR / "workflow2_updated.png", dpi=220)
    fig.savefig(OUT_DIR / "workflow2_updated.pdf")


if __name__ == "__main__":
    main()
