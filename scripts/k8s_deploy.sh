#!/usr/bin/env bash
# scripts/k8s_deploy.sh — Day 12
# Builds the SYNAPSE image, loads it into a local kind/minikube cluster,
# and applies every manifest in dependency order.
#
# Usage:
#   ./scripts/k8s_deploy.sh
#
# Prerequisites:
#   - kubectl pointed at your cluster (kind/minikube/Docker Desktop)
#   - .env file in repo root with your API keys
#   - Docker image buildable (same Dockerfile from Day 11)

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

IMAGE_NAME="synapse-app:latest"
CLUSTER_TYPE="${SYNAPSE_K8S_CLUSTER:-kind}"   # kind | minikube | none
KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-synapse}"

echo "=== [1/6] Building Docker image: $IMAGE_NAME ==="
docker build -t "$IMAGE_NAME" .

echo ""
echo "=== [2/6] Loading image into cluster (type: $CLUSTER_TYPE) ==="
case "$CLUSTER_TYPE" in
  kind)
    kind load docker-image "$IMAGE_NAME" --name "$KIND_CLUSTER_NAME"
    ;;
  minikube)
    minikube image load "$IMAGE_NAME"
    ;;
  none)
    echo "    Skipping image load (SYNAPSE_K8S_CLUSTER=none) — assuming"
    echo "    your cluster can already pull/see this image."
    ;;
  *)
    echo "    Unknown cluster type '$CLUSTER_TYPE'. Set SYNAPSE_K8S_CLUSTER"
    echo "    to kind, minikube, or none."
    exit 1
    ;;
esac

echo ""
echo "=== [3/6] Creating namespace ==="
kubectl apply -f k8s/00-namespace.yaml

echo ""
echo "=== [4/6] Applying ConfigMap ==="
kubectl apply -f k8s/01-configmap.yaml

echo ""
echo "=== [5/6] Creating Secret from .env ==="
if [[ ! -f .env ]]; then
  echo "    ERROR: .env not found in repo root. Copy .env.example to .env"
  echo "    and fill in your API keys first."
  exit 1
fi
kubectl create secret generic synapse-secrets \
  --namespace=synapse \
  --from-env-file=.env \
  --dry-run=client -o yaml | kubectl apply -f -

echo ""
echo "=== [6/6] Applying infra, tool servers, agents, and UI ==="
kubectl apply -f k8s/03-infra.yaml
echo "    Waiting for Redis + Phoenix to become ready..."
kubectl wait --namespace=synapse --for=condition=ready pod -l tier=infra --timeout=120s

kubectl apply -f k8s/04-tool-servers.yaml
kubectl apply -f k8s/05-agents.yaml
kubectl apply -f k8s/06-ui.yaml

echo ""
echo "=== Done. Checking rollout status ==="
kubectl get pods -n synapse

echo ""
echo "Once all pods show Running:"
echo "  UI:      http://localhost:31501  (NodePort — may need 'kubectl port-forward' on some setups)"
echo "  Phoenix: kubectl port-forward -n synapse svc/phoenix-svc 6006:6006"
echo "  Redis:   kubectl port-forward -n synapse svc/redis-svc 6379:6379"
echo ""
echo "Watch pods come up live:  kubectl get pods -n synapse -w"