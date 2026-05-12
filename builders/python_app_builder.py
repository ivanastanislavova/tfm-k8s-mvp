import subprocess
from pathlib import Path


GENERATED_DIR = Path("generated_apps")


def run_command(command):
    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True
    )
    return result.returncode, result.stdout, result.stderr


def build_python_app(app_name: str, python_file: str):
    GENERATED_DIR.mkdir(exist_ok=True)

    app_dir = GENERATED_DIR / app_name
    app_dir.mkdir(exist_ok=True)

    source_path = Path(python_file)

    if not source_path.exists():
        return False, "", f"Python file not found: {python_file}"

    target_file = app_dir / "app.py"
    target_file.write_text(
        source_path.read_text(encoding="utf-8"),
        encoding="utf-8"
    )

    dockerfile = app_dir / "Dockerfile"
    dockerfile.write_text(
        """FROM python:3.11-slim

WORKDIR /app

COPY app.py .

CMD ["python", "app.py"]
""",
        encoding="utf-8"
    )

    image_name = f"{app_name}:local"

    build_code, build_out, build_err = run_command(
        f"docker build -t {image_name} {app_dir}"
    )

    if build_code != 0:
        return False, "", build_out + build_err

    load_code, load_out, load_err = run_command(
        f"minikube image load {image_name}"
    )

    if load_code != 0:
        return False, "", build_out + build_err + "\n" + load_out + load_err

    logs = build_out + build_err + "\n" + load_out + load_err

    return True, image_name, logs