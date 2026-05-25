"""
Shared state definition for the LangGraph agents.

The state contains deployment configuration, cluster status,
diagnostic outputs, generated manifests, and runtime metadata.
Each graph node receives this object, updates its own fields, and
passes it to the next node.
"""

from typing import Dict, List, TypedDict


class AgentState(TypedDict):
    # Input context
    session_id: str
    user_request: str
    intent: str

    # Deployment configuration
    app_name: str
    image: str
    replicas: int
    port: int
    service_type: str
    config_data: Dict[str, str]

    # Ingress configuration
    use_ingress: bool
    ingress_host: str

    # Cluster provisioning parameters
    masters: int
    workers: int

    # Runtime status and diagnosis
    has_error: bool
    diagnosis: str
    reason: str

    # Generated manifests
    deployment_yaml: str
    service_yaml: str
    configmap_yaml: str
    ingress_yaml: str

    # Observability outputs
    observation: str
    logs_output: str
    describe_output: str

    # Workflow memory
    history: List[str]
    chat_history: List[Dict[str, str]]
    last_intent: str
    last_observation: str
    last_reason: str

    # Remediation control
    retries: int
    max_retries: int

    # Cluster provisioning artifacts
    cluster_plan: str
    master_script: str
    worker_script: str
    virtualbox_script: str
    cluster_inventory: Dict[str, object]
    cluster_inventory_path: str
    provider: str

    # Optional generated application source
    python_file: str
    source_type: str

    # LLM configuration
    llm_model: str
    generation_mode: str
    llm_generated_yaml: str

    # Execution timing per graph node
    metrics: Dict[str, float]
