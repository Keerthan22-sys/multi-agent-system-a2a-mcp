#!/usr/bin/env bash
# scripts/k8s_teardown.sh — Day 12
# Removes the entire SYNAPSE deployment. Pass --volumes to also delete
# PVCs (wipes memory, conversations, eval results, phoenix traces).

set -euo pipefail

if [[ "${1:-}" == "--volumes" ]]; then
  echo "Deleting namespace 'synapse' INCLUDING persistent volumes..."
  echo "This wipes: memory, conversations, eval results, phoenix traces, redis data."
  read -p "Are you sure? [y/N] " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
  fi
fi

echo "Deleting namespace 'synapse' (this removes all 15 deployments + services)..."
kubectl delete namespace synapse --ignore-not-found=true

echo "Done. PersistentVolumes backing the deleted PVCs may take a few minutes"
echo "to be reclaimed by the cluster depending on your storage class."