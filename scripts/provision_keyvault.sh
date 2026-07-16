#!/usr/bin/env bash
# provision_keyvault.sh -- one-time Key Vault provisioning for the Fabric PHI de-id
# accelerator (Option 1, production).
#
# Creates (or reuses) an RBAC-enabled Key Vault, generates a high-entropy tokenization
# pepper, stores it as the secret 'phi-deid-pepper', and grants the identity that runs the
# de-id notebook read-only access ('Key Vault Secrets User').
#
# The pepper is generated locally and written straight to the vault; it is NEVER printed,
# logged, or returned. HMAC is one-way, so the pepper is not a decryption key -- but it does
# determine every token value, so treat it as a rotation-controlled secret.
#
# Run this once per adopting tenant/workspace. For synthetic-data demos you do NOT need this
# script -- set the PHI_DEID_PEPPER env var instead (Option 2). See
# docs/pepper_rotation_runbook.md.
#
# Usage:
#   ./provision_keyvault.sh \
#       --vault-name kv-phideid-contoso \
#       --resource-group rg-phi-deid \
#       --grantee-object-id "$(az ad signed-in-user show --query id -o tsv)" \
#       [--location eastus] [--secret-name phi-deid-pepper] [--disable-public-access]
#
# CHOOSE --grantee-object-id CAREFULLY -- the notebook runs as whatever identity triggered
# it, not as whoever created the vault:
#   - Interactive runs (a person clicks Run):  the USER's object ID
#         az ad signed-in-user show --query id -o tsv
#   - Scheduled / Data Pipeline runs:          the WORKSPACE MANAGED IDENTITY object ID
#         (Fabric workspace -> Workspace settings -> Managed identity)
set -euo pipefail

VAULT_NAME=""
RESOURCE_GROUP=""
GRANTEE_OBJECT_ID=""
LOCATION="eastus"
SECRET_NAME="phi-deid-pepper"
DISABLE_PUBLIC_ACCESS="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vault-name) VAULT_NAME="$2"; shift 2 ;;
    --resource-group) RESOURCE_GROUP="$2"; shift 2 ;;
    --grantee-object-id) GRANTEE_OBJECT_ID="$2"; shift 2 ;;
    --location) LOCATION="$2"; shift 2 ;;
    --secret-name) SECRET_NAME="$2"; shift 2 ;;
    --disable-public-access) DISABLE_PUBLIC_ACCESS="true"; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$VAULT_NAME" || -z "$RESOURCE_GROUP" || -z "$GRANTEE_OBJECT_ID" ]]; then
  echo "ERROR: --vault-name, --resource-group and --grantee-object-id are required." >&2
  exit 2
fi

command -v az >/dev/null 2>&1 || { echo "ERROR: 'az' not found on PATH." >&2; exit 1; }
command -v python >/dev/null 2>&1 || PY=python3 && command -v ${PY:-python} >/dev/null 2>&1 || { echo "ERROR: python not found." >&2; exit 1; }
PY="$(command -v python || command -v python3)"

echo "==> Verifying Azure login..."
az account show --query "{name:name, id:id}" -o json >/dev/null || { echo "Not logged in. Run 'az login'." >&2; exit 1; }

# 1. Create or reuse the vault (RBAC authorization is required by the accelerator).
if az keyvault show --name "$VAULT_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv >/dev/null 2>&1; then
  echo "==> Reusing existing vault $VAULT_NAME"
else
  echo "==> Creating vault $VAULT_NAME in $LOCATION (RBAC authorization)..."
  az keyvault create \
    --name "$VAULT_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --enable-rbac-authorization true >/dev/null
fi

SCOPE="$(az keyvault show --name "$VAULT_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)"

# 2. Grant runtime identity read access + current user write access (RBAC lag is normal).
echo "==> Granting 'Key Vault Secrets User' to $GRANTEE_OBJECT_ID..."
az role assignment create --assignee "$GRANTEE_OBJECT_ID" --role "Key Vault Secrets User" --scope "$SCOPE" >/dev/null

ME="$(az ad signed-in-user show --query id -o tsv)"
echo "==> Granting 'Key Vault Secrets Officer' to current user (to write the secret)..."
az role assignment create --assignee "$ME" --role "Key Vault Secrets Officer" --scope "$SCOPE" >/dev/null

echo "    Waiting for RBAC propagation..."
sleep 15

# 3. Generate a high-entropy pepper LOCALLY and write it without echoing the value.
echo "==> Generating and storing pepper (value never displayed)..."
PEPPER="$("$PY" -c 'import secrets; print(secrets.token_urlsafe(48))')"
az keyvault secret set --vault-name "$VAULT_NAME" --name "$SECRET_NAME" --value "$PEPPER" --output none
unset PEPPER

# 4. Optionally lock down networking for real PHI.
if [[ "$DISABLE_PUBLIC_ACCESS" == "true" ]]; then
  echo "==> Disabling public network access (real-PHI posture)..."
  az keyvault update --name "$VAULT_NAME" --resource-group "$RESOURCE_GROUP" --public-network-access Disabled >/dev/null
  echo "    NEXT: create a Fabric managed private endpoint to this vault and approve the pending connection."
fi

# 5. Report (metadata only -- no secret value).
echo ""
echo "==> Done. Secret metadata:"
az keyvault secret show --vault-name "$VAULT_NAME" --name "$SECRET_NAME" \
  --query "{name:name, enabled:attributes.enabled, version:id}" -o json

cat <<EOF

In the Fabric notebook, wire Option 1 like this:
  import os
  os.environ['PHI_DEID_KEYVAULT_URL'] = 'https://${VAULT_NAME}.vault.azure.net/'
  os.environ.pop('PHI_DEID_PEPPER', None)  # ensure Option 2 does not shadow the vault
  PEPPER = get_pepper('${SECRET_NAME}')     # never print PEPPER
EOF
