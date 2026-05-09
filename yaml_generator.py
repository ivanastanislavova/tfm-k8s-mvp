def generate_deployment_yaml(app_name, image, replicas, port, use_configmap):
    # Genera el YAML de tipo Deployment.
    # Este recurso es el que define cuántos pods quieres, qué imagen usarán y qué puerto exponen.

    env_from_block = ""
    # Este bloque se añadirá solo si existe ConfigMap

    if use_configmap:
        env_from_block = f"""
        envFrom:
        - configMapRef:
            name: {app_name}-config"""
        # Si hay configuración, el contenedor cargará variables desde el ConfigMap
        # Ejemplo: ENV=prod, DEBUG=false

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
    # Esto devuelve un string YAML completo.
    # Puntos importantes:
    # - metadata.name = nombre del Deployment
    # - replicas = número de pods
    # - selector/matchLabels = cómo Kubernetes identifica los pods de esta app
    # - image = imagen Docker
    # - containerPort = puerto que abre el contenedor
    # - envFrom = conexión opcional con ConfigMap


def generate_service_yaml(app_name, port, service_type):
    # Genera el YAML del recurso Service.
    # El Service sirve para exponer o conectar los pods por red.

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
    # Puntos importantes:
    # - selector.app = enlaza este Service con los pods que tengan label app=<app_name>
    # - port = puerto del Service
    # - targetPort = puerto del contenedor
    # - type = NodePort o ClusterIP


def generate_configmap_yaml(app_name, config_data):
    # Genera el YAML del recurso ConfigMap.
    # Un ConfigMap guarda pares clave-valor para pasar configuración al contenedor.

    lines = []
    for key, value in config_data.items():
        lines.append(f'  {key}: "{value}"')
    # Convierte el diccionario en líneas YAML:
    # ENV=prod ->   ENV: "prod"

    data_block = "\n".join(lines)

    return f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: {app_name}-config
data:
{data_block}
"""
    # Devuelve el YAML del ConfigMap.
    # Este recurso luego se conecta al Deployment con envFrom.


def generate_ingress_yaml(app_name, port, ingress_host):
    # Genera el YAML del recurso Ingress.
    # El Ingress define reglas HTTP para acceder a la app usando un host/dominio.

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
    # Puntos importantes:
    # - host = dominio o nombre tipo myweb.local
    # - backend.service.name = Service al que apunta
    # - backend.service.port.number = puerto del Service


def write_yaml_files(app_name, image, replicas, port, service_type, config_data, use_ingress, ingress_host):
    # Esta función es la que orquesta todo.
    # Genera los YAMLs necesarios, los guarda en archivos .yaml y además los devuelve como texto.

    deployment_yaml = generate_deployment_yaml(
        app_name, image, replicas, port, bool(config_data)
    )
    # Genera Deployment.
    # bool(config_data) será True si config_data no está vacío.

    service_yaml = generate_service_yaml(app_name, port, service_type)
    # Genera Service.

    with open("deployment.yaml", "w", encoding="utf-8") as f:
        f.write(deployment_yaml)
    # Guarda deployment.yaml en disco

    with open("service.yaml", "w", encoding="utf-8") as f:
        f.write(service_yaml)
    # Guarda service.yaml en disco

    configmap_yaml = ""
    if config_data:
        configmap_yaml = generate_configmap_yaml(app_name, config_data)
        with open("configmap.yaml", "w", encoding="utf-8") as f:
            f.write(configmap_yaml)
    # Solo genera y guarda configmap.yaml si hay datos de configuración

    ingress_yaml = ""
    if use_ingress and ingress_host:
        ingress_yaml = generate_ingress_yaml(app_name, port, ingress_host)
        with open("ingress.yaml", "w", encoding="utf-8") as f:
            f.write(ingress_yaml)
    # Solo genera y guarda ingress.yaml si se ha activado ingress y hay host

    return deployment_yaml, service_yaml, configmap_yaml, ingress_yaml
    # Devuelve todos los YAMLs como strings
    # Esto se usa luego para:
    # - guardarlos en state
    # - mostrarlos con show_yaml