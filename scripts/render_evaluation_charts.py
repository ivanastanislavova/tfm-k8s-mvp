import argparse
import csv
import html
import math
from pathlib import Path


COLORS = {
    "hybrid_template / llama3.2:3b": "#2563eb",
    "hybrid_template / mistral:latest": "#0891b2",
    "full_ai_experimental / llama3.2:3b": "#dc2626",
    "full_ai_experimental / mistral:latest": "#f97316",
    "hybrid_template": "#2563eb",
    "full_ai_experimental": "#dc2626",
}


def read_csv(path):
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def group_raw(rows):
    groups = {}
    for row in rows:
        key = (row["scenario"], series_name(row))
        groups.setdefault(key, []).append(row)
    return groups


def series_name(row):
    model = row.get("llm_model", "")
    if model:
        return f'{row["generation_mode"]} / {model}'
    return row["generation_mode"]


def ordered_series(rows):
    preferred = [
        "hybrid_template / llama3.2:3b",
        "hybrid_template / mistral:latest",
        "full_ai_experimental / llama3.2:3b",
        "full_ai_experimental / mistral:latest",
        "hybrid_template",
        "full_ai_experimental",
    ]
    present = {series_name(row) for row in rows}
    ordered = [name for name in preferred if name in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered


def row_is_success(row):
    if row.get("validation_success") == "true":
        return True
    if row.get("scenario") == "question_nginx_port":
        return row.get("diagnosis") in {"port_found", "port_ready", "service_ready"}
    return False


def scale(value, domain_min, domain_max, range_min, range_max):
    if domain_max == domain_min:
        return (range_min + range_max) / 2
    ratio = (value - domain_min) / (domain_max - domain_min)
    return range_min + ratio * (range_max - range_min)


def percentile(values, pct):
    values = sorted(values)
    if not values:
        return 0
    if len(values) == 1:
        return values[0]
    rank = (len(values) - 1) * pct
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return values[int(rank)]
    weight = rank - low
    return values[low] * (1 - weight) + values[high] * weight


def svg_header(width, height):
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text{font-family:Arial,sans-serif;fill:#111827}.axis{stroke:#9ca3af;stroke-width:1}.grid{stroke:#e5e7eb;stroke-width:1}.label{font-size:12px}.title{font-size:18px;font-weight:700}.small{font-size:11px;fill:#4b5563}</style>',
    ]


def write_svg(path, lines):
    Path(path).write_text("\n".join(lines + ["</svg>"]) + "\n", encoding="utf-8")


def render_success_rate(summary_rows, output_path):
    width = 1100
    height = 520
    margin_left = 180
    margin_right = 40
    margin_top = 60
    margin_bottom = 100
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom

    scenarios = sorted({row["scenario"] for row in summary_rows})
    modes = ordered_series(summary_rows)
    lookup = {(row["scenario"], series_name(row)): safe_float(row["success_rate"]) or 0 for row in summary_rows}

    lines = svg_header(width, height)
    lines.append(f'<text x="{margin_left}" y="32" class="title">Success rate by scenario and mode</text>')

    for tick in range(0, 6):
        value = tick / 5
        y = scale(value, 0, 1, margin_top + plot_height, margin_top)
        lines.append(f'<line x1="{margin_left}" y1="{y:.1f}" x2="{width - margin_right}" y2="{y:.1f}" class="grid"/>')
        lines.append(f'<text x="{margin_left - 45}" y="{y + 4:.1f}" class="small">{int(value * 100)}%</text>')

    group_width = plot_width / max(len(scenarios), 1)
    bar_width = min(36, group_width / (len(modes) + 1))

    for i, scenario in enumerate(scenarios):
        center = margin_left + group_width * i + group_width / 2
        for j, mode in enumerate(modes):
            value = lookup.get((scenario, mode), 0)
            x = center - (len(modes) * bar_width) / 2 + j * bar_width
            y = scale(value, 0, 1, margin_top + plot_height, margin_top)
            color = COLORS.get(mode, "#6b7280")
            height_value = margin_top + plot_height - y
            if value == 0:
                lines.append(f'<line x1="{x:.1f}" y1="{margin_top + plot_height:.1f}" x2="{x + bar_width - 4:.1f}" y2="{margin_top + plot_height:.1f}" stroke="{color}" stroke-width="4"/>')
                lines.append(f'<text x="{x - 1:.1f}" y="{margin_top + plot_height - 7:.1f}" class="small">0%</text>')
            else:
                lines.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width - 4:.1f}" height="{height_value:.1f}" fill="{color}"/>')
                lines.append(f'<text x="{x - 2:.1f}" y="{y - 5:.1f}" class="small">{int(value * 100)}%</text>')
        lines.append(
            f'<text transform="translate({center - 4:.1f},{height - 88}) rotate(45)" class="label">{html.escape(scenario)}</text>'
        )

    legend_x = margin_left
    for idx, mode in enumerate(modes):
        x = legend_x + idx * 210
        lines.append(f'<rect x="{x}" y="{height - 38}" width="14" height="14" fill="{COLORS.get(mode, "#6b7280")}"/>')
        lines.append(f'<text x="{x + 20}" y="{height - 27}" class="label">{html.escape(mode)}</text>')

    lines.append(f'<line x1="{margin_left}" y1="{margin_top + plot_height}" x2="{width - margin_right}" y2="{margin_top + plot_height}" class="axis"/>')
    lines.append(f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_height}" class="axis"/>')
    write_svg(output_path, lines)


def render_mean_time(summary_rows, output_path):
    rows = [row for row in summary_rows if safe_float(row.get("mean_seconds")) is not None]
    width = 1100
    height = 540
    margin_left = 180
    margin_right = 40
    margin_top = 60
    margin_bottom = 120
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom
    max_value = max([safe_float(row["mean_seconds"]) or 0 for row in rows] + [1])

    scenarios = sorted({row["scenario"] for row in rows})
    modes = ordered_series(rows)
    lookup = {(row["scenario"], series_name(row)): safe_float(row["mean_seconds"]) for row in rows}

    lines = svg_header(width, height)
    lines.append(f'<text x="{margin_left}" y="32" class="title">Mean execution time for valid runs</text>')

    for tick in range(0, 6):
        value = max_value * tick / 5
        y = scale(value, 0, max_value, margin_top + plot_height, margin_top)
        lines.append(f'<line x1="{margin_left}" y1="{y:.1f}" x2="{width - margin_right}" y2="{y:.1f}" class="grid"/>')
        lines.append(f'<text x="{margin_left - 58}" y="{y + 4:.1f}" class="small">{value:.1f}s</text>')

    group_width = plot_width / max(len(scenarios), 1)
    bar_width = min(36, group_width / (len(modes) + 1))

    for i, scenario in enumerate(scenarios):
        center = margin_left + group_width * i + group_width / 2
        for j, mode in enumerate(modes):
            value = lookup.get((scenario, mode))
            if value is None:
                continue
            x = center - (len(modes) * bar_width) / 2 + j * bar_width
            y = scale(value, 0, max_value, margin_top + plot_height, margin_top)
            color = COLORS.get(mode, "#6b7280")
            lines.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width - 4:.1f}" height="{margin_top + plot_height - y:.1f}" fill="{color}"/>')
            lines.append(f'<text x="{x - 2:.1f}" y="{y - 5:.1f}" class="small">{value:.2f}</text>')
        lines.append(
            f'<text transform="translate({center - 4:.1f},{height - 105}) rotate(45)" class="label">{html.escape(scenario)}</text>'
        )

    legend_x = margin_left
    for idx, mode in enumerate(modes):
        x = legend_x + idx * 210
        lines.append(f'<rect x="{x}" y="{height - 38}" width="14" height="14" fill="{COLORS.get(mode, "#6b7280")}"/>')
        lines.append(f'<text x="{x + 20}" y="{height - 27}" class="label">{html.escape(mode)}</text>')

    lines.append(f'<line x1="{margin_left}" y1="{margin_top + plot_height}" x2="{width - margin_right}" y2="{margin_top + plot_height}" class="axis"/>')
    lines.append(f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_height}" class="axis"/>')
    write_svg(output_path, lines)


def render_boxplot(raw_rows, output_path):
    groups = []
    no_valid_groups = []
    for (scenario, mode), rows in sorted(group_raw(raw_rows).items()):
        values = [
            safe_float(row["http_time_seconds"])
            for row in rows
            if row_is_success(row) and safe_float(row["http_time_seconds"]) is not None
        ]
        if values:
            groups.append((scenario, mode, values))
        else:
            no_valid_groups.append((scenario, mode))

    width = 1200
    height = 620
    margin_left = 170
    margin_right = 40
    margin_top = 60
    margin_bottom = 170
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom
    max_value = max([max(values) for _, _, values in groups] + [1])

    lines = svg_header(width, height)
    lines.append(f'<text x="{margin_left}" y="32" class="title">Execution time distribution for successful runs</text>')
    lines.append(f'<text x="{margin_left}" y="50" class="small">Groups with 0% success are marked as "no valid runs" because no execution time can be summarized.</text>')

    for tick in range(0, 6):
        value = max_value * tick / 5
        y = scale(value, 0, max_value, margin_top + plot_height, margin_top)
        lines.append(f'<line x1="{margin_left}" y1="{y:.1f}" x2="{width - margin_right}" y2="{y:.1f}" class="grid"/>')
        lines.append(f'<text x="{margin_left - 58}" y="{y + 4:.1f}" class="small">{value:.1f}s</text>')

    step = plot_width / max(len(groups), 1)
    box_width = min(42, step * 0.55)
    for i, (scenario, mode, values) in enumerate(groups):
        x = margin_left + step * i + step / 2
        q1 = percentile(values, 0.25)
        median = percentile(values, 0.50)
        q3 = percentile(values, 0.75)
        low = min(values)
        high = max(values)
        y_low = scale(low, 0, max_value, margin_top + plot_height, margin_top)
        y_high = scale(high, 0, max_value, margin_top + plot_height, margin_top)
        y_q1 = scale(q1, 0, max_value, margin_top + plot_height, margin_top)
        y_q3 = scale(q3, 0, max_value, margin_top + plot_height, margin_top)
        y_median = scale(median, 0, max_value, margin_top + plot_height, margin_top)
        color = COLORS.get(mode, "#6b7280")

        lines.append(f'<line x1="{x:.1f}" y1="{y_high:.1f}" x2="{x:.1f}" y2="{y_low:.1f}" stroke="{color}" stroke-width="2"/>')
        lines.append(f'<line x1="{x - box_width / 3:.1f}" y1="{y_high:.1f}" x2="{x + box_width / 3:.1f}" y2="{y_high:.1f}" stroke="{color}" stroke-width="2"/>')
        lines.append(f'<line x1="{x - box_width / 3:.1f}" y1="{y_low:.1f}" x2="{x + box_width / 3:.1f}" y2="{y_low:.1f}" stroke="{color}" stroke-width="2"/>')
        lines.append(f'<rect x="{x - box_width / 2:.1f}" y="{y_q3:.1f}" width="{box_width:.1f}" height="{max(y_q1 - y_q3, 1):.1f}" fill="{color}" opacity="0.25" stroke="{color}" stroke-width="2"/>')
        lines.append(f'<line x1="{x - box_width / 2:.1f}" y1="{y_median:.1f}" x2="{x + box_width / 2:.1f}" y2="{y_median:.1f}" stroke="{color}" stroke-width="3"/>')
        lines.append(
            f'<text transform="translate({x - 5:.1f},{height - 150}) rotate(55)" class="label">{html.escape(scenario)} / {html.escape(mode)}</text>'
        )

    lines.append(f'<line x1="{margin_left}" y1="{margin_top + plot_height}" x2="{width - margin_right}" y2="{margin_top + plot_height}" class="axis"/>')
    lines.append(f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_height}" class="axis"/>')
    write_svg(output_path, lines)

    if no_valid_groups:
        no_valid_path = Path(output_path).with_name("no_valid_time_groups.txt")
        no_valid_path.write_text(
            "\n".join(f"{scenario} / {mode}" for scenario, mode in no_valid_groups) + "\n",
            encoding="utf-8",
        )


def render_report(run_dir, summary_rows):
    lines = [
        "# KubeAgentFlow Evaluation Report",
        "",
        "Generated from automated evaluation CSV files.",
        "",
        "## Figures",
        "",
        "- `success_rate.svg`",
        "- `mean_time.svg`",
        "- `time_boxplot.svg`",
        "",
        "## Summary",
        "",
        "| Scenario | Mode | Runs | Valid runs | Success rate | Mean (s) | Median (s) | Q1 (s) | Q3 (s) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary_rows:
        lines.append(
            "| {scenario} | {generation_mode} | {runs} | {valid_runs} | {success_rate} | {mean_seconds} | {median_seconds} | {q1_seconds} | {q3_seconds} |".format(
                **row
            )
        )
    Path(run_dir, "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Render SVG charts for a KubeAgentFlow evaluation run.")
    parser.add_argument("run_dir", help="Directory containing raw_results.csv and summary.csv.")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    raw_rows = read_csv(run_dir / "raw_results.csv")
    summary_rows = read_csv(run_dir / "summary.csv")

    render_success_rate(summary_rows, run_dir / "success_rate.svg")
    render_mean_time(summary_rows, run_dir / "mean_time.svg")
    render_boxplot(raw_rows, run_dir / "time_boxplot.svg")
    render_report(run_dir, summary_rows)

    print(f"Charts written to {run_dir}")


if __name__ == "__main__":
    main()
