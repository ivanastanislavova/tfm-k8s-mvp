# KubeAgentFlow

KubeAgentFlow is a web-based agentic AI assistant for Kubernetes application deployment and inspection. It accepts natural-language requests, converts them into Kubernetes operations, executes them through `kubectl`, observes the cluster state, and reports the result in the web interface.

## Quick Start Guide

These steps assume a Windows machine with PowerShell. The project also works on Linux/macOS with equivalent commands.

### 1. Install required applications

Install these tools first:

- Git: <https://git-scm.com/downloads>
- Python 3.10 or 3.11: <https://www.python.org/downloads/>
- Docker Desktop with the WSL2 backend enabled: <https://www.docker.com/products/docker-desktop/>
- kubectl: <https://kubernetes.io/docs/tasks/tools/>
- Minikube: <https://minikube.sigs.k8s.io/docs/start/>
- Ollama: <https://ollama.com/download>
- Terraform, only for OCI-based infrastructure provisioning: <https://developer.hashicorp.com/terraform/downloads>
- OCI CLI, only for Oracle Cloud Infrastructure provisioning: <https://docs.oracle.com/en-us/iaas/Content/API/SDKDocs/cliinstall.htm>

After installing Docker Desktop, start it and wait until the Docker engine is running.

### 2. Clone the repository

```powershell
git clone https://github.com/ivanastanislavova/tfm-k8s-mvp.git
cd tfm-k8s-mvp
```

### 3. Create and activate the Python environment

```powershell
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If `py -3.11` is not available, use:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Install the local LLM models

Start Ollama and download the default model:

```powershell
ollama pull llama3.2:3b
```

Optional comparison model:

```powershell
ollama pull mistral
```

### 5. Start a local Kubernetes cluster

Make sure Docker Desktop is running, then start Minikube:

```powershell
minikube start --driver=docker
kubectl get nodes
```

The second command should show at least one node in `Ready` state.

### 6. Run KubeAgentFlow

From the repository root:

```powershell
.\venv\Scripts\uvicorn.exe app.api:app --reload --host 127.0.0.1 --port 8000
```

Open the web interface:

```text
http://127.0.0.1:8000
```

### 7. Try example requests

Use the chat panel with requests such as:

```text
deploy nginx with 2 replicas
scale nginx to 3 replicas
show logs
what deployments are active?
change nginx service to ClusterIP
delete nginx
```

The default mode is `Hybrid`, where Kubernetes YAML generation is deterministic and the LLM is used for natural-language interpretation and diagnosis support.

## Optional: Terraform and Oracle Cloud Infrastructure

The Terraform workflow creates OCI virtual machines that can be used for Kubernetes infrastructure experiments. This requires your own OCI account and credentials.

### 1. Configure OCI locally

Install OCI CLI and run:

```powershell
oci setup config
```

This creates a local OCI config file, usually at:

```text
C:\Users\<your-user>\.oci\config
```

### 2. Create a local Terraform variables file

Copy the example file:

```powershell
Copy-Item provisioning\terraform\terraform.tfvars.example provisioning\terraform\terraform.tfvars
```

Edit `provisioning\terraform\terraform.tfvars` with your own:

- `tenancy_ocid`
- `compartment_id`
- `subnet_id`
- `ssh_public_key`
- `worker_count`

Do not commit `terraform.tfvars`; it is intentionally ignored because it contains local cloud configuration.

### 3. Test Terraform manually

```powershell
cd provisioning\terraform
terraform init
terraform plan
cd ..\..
```

### 4. Trigger Terraform from the chat

In the web interface, use a request such as:

```text
create a cluster with 1 master and 1 worker with terraform
```

If OCI returns `Out of host capacity`, Terraform and KubeAgentFlow are connected correctly, but the free-tier region currently has no available VM capacity for the requested shape.

## Project Structure

```text
app/                     FastAPI entrypoint and web interface
app/assets/              UI assets, including logo.png
agents/                  LangGraph workflow nodes
core/                    Shared state, graph builder, metrics, conversation memory
k8s/                     Kubernetes manifest generation and kubectl helpers
llm/                     LLM provider, intent parser, and LLM YAML generation
provisioning/            Minikube, Terraform, and generated cluster artifacts
provisioning/terraform/  OCI Terraform configuration
scripts/                 Evaluation and plotting scripts
charts/                  Helm baseline chart
generated/               Runtime-generated artifacts
results/                 Evaluation outputs
```

Generated runtime files are intentionally kept out of the repository root. Kubernetes manifests created by the application are written under `generated/manifests/`.

## Troubleshooting

- If `kubectl` cannot connect and points to `localhost:8080`, start Docker Desktop and run `minikube update-context`.
- If no pods are found after restarting Docker Desktop, verify the current context with `kubectl config get-contexts` and `minikube profile list`.
- If the chat returns an image pull error for a private or non-existent image, the system leaves the explicit image unchanged and reports the failure.
- If Ollama is not running or the model is missing, start Ollama and run `ollama pull llama3.2:3b`.
- If Terraform fails with OCI capacity errors, retry later or select another OCI region/shape if your account allows it.
