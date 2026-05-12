"""
Define el estado compartido entre agentes.

Contiene:
- Configuración del despliegue
- Estado del clúster
- Diagnóstico y logs

Base del comportamiento agentic.
"""

from typing import TypedDict, List, Dict

# Definimos la estructura global del estado compartido entre todos los agentes
# Este estado es el "cerebro compartido" del sistema (LangGraph)
class AgentState(TypedDict):

    # =========================
    # CONTEXTO DE ENTRADA
    # =========================

    session_id: str  
    # Identificador de la conversación (permite múltiples sesiones tipo chat)

    user_request: str  
    # Texto original del usuario (ej: "deploy nginx with 2 replicas")

    intent: str  
    # Intención detectada (deploy, scale, update_image, etc.)

    # =========================
    # CONFIGURACIÓN DEL DESPLIEGUE
    # =========================

    app_name: str  
    # Nombre de la aplicación (también usado como label en Kubernetes)

    image: str  
    # Imagen Docker a usar (ej: nginx, nginx:latest)

    replicas: int  
    # Número de réplicas (pods)

    port: int  
    # Puerto del contenedor

    service_type: str  
    # Tipo de servicio Kubernetes (NodePort o ClusterIP)

    config_data: Dict[str, str]  
    # Variables de configuración tipo ENV (para ConfigMap)
    # Ej: {"ENV": "prod", "DEBUG": "false"}

    # =========================
    # EXPOSICIÓN (INGRESS)
    # =========================

    use_ingress: bool  
    # Indica si se debe crear un Ingress o no

    ingress_host: str  
    # Dominio para el Ingress (ej: myapp.local)

    masters: int
    # Número de nodos master en el cluster

    workers: int
    # Número de nodos worker en el cluster

    # =========================
    # ESTADO Y DIAGNÓSTICO
    # =========================

    has_error: bool  
    # Flag rápido para saber si hay error en el sistema

    diagnosis: str  
    # Tipo de diagnóstico (ej: "healthy", "image_pull_error")

    reason: str  
    # Explicación del diagnóstico (ej: "pods running", "image pull failed")

    # =========================
    # YAML GENERADO
    # =========================

    deployment_yaml: str  
    # YAML del Deployment generado dinámicamente

    service_yaml: str  
    # YAML del Service

    configmap_yaml: str  
    # YAML del ConfigMap (si hay config_data)

    ingress_yaml: str  
    # YAML del Ingress (si use_ingress=True)

    # =========================
    # OBSERVABILIDAD
    # =========================

    observation: str  
    # Output de kubectl get pods (estado de los pods)

    logs_output: str  
    # Logs del pod (kubectl logs)

    describe_output: str  
    # Describe del pod (kubectl describe)

    # =========================
    # MEMORIA DEL SISTEMA
    # =========================

    history: List[str]  
    # Historial técnico de lo que han hecho los agentes
    # Ej: ["Validator OK", "Execution OK", "Diagnosis healthy"]

    chat_history: List[Dict[str, str]]  
    # Historial conversacional (para contexto tipo ChatGPT)
    # Ej: [{"role": "user", "content": "..."}]

    # =========================
    # CONTROL DE RETRIES
    # =========================

    retries: int  
    # Número de intentos de remediación realizados

    max_retries: int  
    # Límite máximo de intentos (evita bucles infinitos)

    # =========================
    # CLUSTER PROVISIONING
    # =========================

    cluster_plan: str
    # Plan textual para crear el clúster Kubernetes desde cero

    master_script: str
    # Script de instalación/configuración para el nodo master/control-plane

    worker_script: str
    # Script de instalación/configuración para el nodo worker

    virtualbox_script: str
    # Script PowerShell para crear las VMs automáticamente en VirtualBox

    cluster_inventory: Dict[str, any]
    # Inventario JSON con IPs y configuración de los nodos

    cluster_inventory_path: str
    # Ruta al archivo de inventario (para orquestador)

    provider: str
    # Proveedor de infraestructura (oracle, minikube, virtualbox)

    python_file: str
    # Archivo Python generado dinámicamente para ejecutar código específico (ej: minikube)

    source_type: str
    # Tipo de fuente del despliegue (ej: "user_request", "generated_code", etc.)

    llm_model: str
    # Modelo LLM a usar para interpretaciones y generación (ej: "llama3.2:3b", "mistral")

    generation_mode: str
    # Modo de generación:
    # - "hybrid_template": usa plantilla determinista
    # - "llm_yaml": el LLM genera todo el YAML

    llm_generated_yaml: str
    # YAML completo generado directamente por el LLM

    metrics: Dict[str, float]
    # Métricas de tiempo para cada nodo (ej: {"diagnose": 1.23, "repair": 2.34})