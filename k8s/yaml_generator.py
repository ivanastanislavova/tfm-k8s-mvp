import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFESTS_DIR = PROJECT_ROOT / "generated" / "manifests"


def generate_deployment_yaml(app_name, image, replicas, port, use_configmap):
    # Deployment controls the desired number of pods, container image, and
    # exposed container port for the application.
    env_from_block = ""

    if use_configmap:
        env_from_block = f"""
        envFrom:
        - configMapRef:
            name: {app_name}-config"""

    return f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {app_name}-deployment
spec:
  replicas: {replicas}
  selector:
    matchLabels:
      app: {app_name}
  template:
    metadata:
      labels:
        app: {app_name}
    spec:
      containers:
      - name: {app_name}
        image: {image}
        ports:
        - containerPort: {port}{env_from_block}
"""


def generate_service_yaml(app_name, port, service_type):
    # Service exposes the pods through the requested Kubernetes service type.
    return f"""apiVersion: v1
kind: Service
metadata:
  name: {app_name}-service
spec:
  selector:
    app: {app_name}
  ports:
  - protocol: TCP
    port: {port}
    targetPort: {port}
  type: {service_type}
"""


def generate_configmap_yaml(app_name, config_data):
    # ConfigMap stores key-value configuration injected into the container.
    lines = []
    for key, value in config_data.items():
        lines.append(f'  {key}: "{value}"')

    data_block = "\n".join(lines)

    return f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: {app_name}-config
data:
{data_block}
"""


def generate_ingress_yaml(app_name, port, ingress_host):
    # Ingress defines an HTTP route for the application service.
    return f"""apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {app_name}-ingress
spec:
  rules:
  - host: {ingress_host}
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: {app_name}-service
            port:
              number: {port}
"""


def write_yaml_files(
    app_name,
    image,
    replicas,
    port,
    service_type,
    config_data,
    use_ingress,
    ingress_host,
):
    # Generate the Kubernetes manifests, persist them as runtime artifacts, and
    # return their contents so the UI can display the applied YAML.
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

    if use_ingress and not ingress_host:
        ingress_host = f"{app_name}.local"

    deployment_yaml = generate_deployment_yaml(
        app_name, image, replicas, port, bool(config_data)
    )
    service_yaml = generate_service_yaml(app_name, port, service_type)

    with open(MANIFESTS_DIR / "deployment.yaml", "w", encoding="utf-8") as f:
        f.write(deployment_yaml)

    with open(MANIFESTS_DIR / "service.yaml", "w", encoding="utf-8") as f:
        f.write(service_yaml)

    configmap_yaml = ""
    if config_data:
        configmap_yaml = generate_configmap_yaml(app_name, config_data)
        with open(MANIFESTS_DIR / "configmap.yaml", "w", encoding="utf-8") as f:
            f.write(configmap_yaml)
    elif os.path.exists(MANIFESTS_DIR / "configmap.yaml"):
        os.remove(MANIFESTS_DIR / "configmap.yaml")

    ingress_yaml = ""
    if use_ingress and ingress_host:
        ingress_yaml = generate_ingress_yaml(app_name, port, ingress_host)
        with open(MANIFESTS_DIR / "ingress.yaml", "w", encoding="utf-8") as f:
            f.write(ingress_yaml)
    elif os.path.exists(MANIFESTS_DIR / "ingress.yaml"):
        os.remove(MANIFESTS_DIR / "ingress.yaml")

    return deployment_yaml, service_yaml, configmap_yaml, ingress_yaml
