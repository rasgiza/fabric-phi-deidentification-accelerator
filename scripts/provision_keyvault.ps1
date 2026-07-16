<#
.SYNOPSIS
  One-time Key Vault provisioning for the Fabric PHI de-id accelerator (Option 1, production).

.DESCRIPTION
  Creates (or reuses) an RBAC-enabled Key Vault, generates a high-entropy tokenization
  pepper, stores it as the secret 'phi-deid-pepper', and grants the identity that runs the
  de-id notebook read-only access ('Key Vault Secrets User').

  The pepper is generated locally and written straight to the vault; it is NEVER printed,
  logged, or returned. HMAC is one-way, so the pepper is not a decryption key -- but it does
  determine every token value, so treat it as a rotation-controlled secret.

  Run this once per adopting tenant/workspace. For synthetic-data demos you do NOT need this
  script at all -- set the PHI_DEID_PEPPER env var instead (Option 2). See
  docs/pepper_rotation_runbook.md.

.PARAMETER VaultName
  Globally-unique Key Vault name (e.g. 'kv-phideid-contoso').

.PARAMETER ResourceGroup
  Resource group that holds (or will hold) the vault.

.PARAMETER Location
  Azure region for a newly-created vault (ignored if the vault already exists). Default: eastus.

.PARAMETER GranteeObjectId
  Object ID of the identity that will READ the secret at runtime. CHOOSE CAREFULLY:
    - Interactive runs (a person clicks Run):  the USER's object ID
        az ad signed-in-user show --query id -o tsv
    - Scheduled / Data Pipeline runs:          the WORKSPACE MANAGED IDENTITY object ID
        (Fabric workspace -> Workspace settings -> Managed identity)
  This is the single most common mistake -- the notebook runs as whatever identity triggered
  it, not as whoever created the vault.

.PARAMETER SecretName
  Secret name. Default 'phi-deid-pepper' (matches DEFAULT_SECRET_NAME in tokenization.py).

.PARAMETER DisablePublicAccess
  If set, disables public network access on the vault. Use this for REAL PHI and then create
  a Fabric managed private endpoint to the vault. Omit for a synthetic demo where public
  access is a convenience.

.EXAMPLE
  ./provision_keyvault.ps1 -VaultName kv-phideid-contoso -ResourceGroup rg-phi-deid `
      -GranteeObjectId (az ad signed-in-user show --query id -o tsv)

.EXAMPLE
  # Production, scheduled notebook, locked-down networking:
  ./provision_keyvault.ps1 -VaultName kv-phideid-prod -ResourceGroup rg-phi `
      -GranteeObjectId <workspace-managed-identity-oid> -DisablePublicAccess
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$VaultName,
    [Parameter(Mandatory = $true)][string]$ResourceGroup,
    [Parameter(Mandatory = $true)][string]$GranteeObjectId,
    [string]$Location = "eastus",
    [string]$SecretName = "phi-deid-pepper",
    [switch]$DisablePublicAccess
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-Tool($name) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "Required tool '$name' not found on PATH."
    }
}
Assert-Tool az
Assert-Tool python

Write-Host "==> Verifying Azure login..." -ForegroundColor Cyan
$account = az account show --query "{name:name, id:id}" -o json 2>$null
if (-not $account) { throw "Not logged in. Run 'az login' first." }
Write-Host "    $account"

# 1. Create or reuse the vault (RBAC authorization is required by the accelerator).
$exists = az keyvault show --name $VaultName --resource-group $ResourceGroup --query id -o tsv 2>$null
if ($exists) {
    Write-Host "==> Reusing existing vault $VaultName" -ForegroundColor Cyan
} else {
    Write-Host "==> Creating vault $VaultName in $Location (RBAC authorization)..." -ForegroundColor Cyan
    az keyvault create `
        --name $VaultName `
        --resource-group $ResourceGroup `
        --location $Location `
        --enable-rbac-authorization true | Out-Null
}

$scope = az keyvault show --name $VaultName --resource-group $ResourceGroup --query id -o tsv

# 2. Grant the runtime identity read-only access, and grant the CURRENT user write access
#    (Secrets Officer) so we can set the secret below. RBAC propagation can lag a few seconds.
Write-Host "==> Granting 'Key Vault Secrets User' to $GranteeObjectId..." -ForegroundColor Cyan
az role assignment create --assignee $GranteeObjectId --role "Key Vault Secrets User" --scope $scope | Out-Null

$me = az ad signed-in-user show --query id -o tsv
Write-Host "==> Granting 'Key Vault Secrets Officer' to current user (to write the secret)..." -ForegroundColor Cyan
az role assignment create --assignee $me --role "Key Vault Secrets Officer" --scope $scope | Out-Null

Write-Host "    Waiting for RBAC propagation..." -ForegroundColor DarkGray
Start-Sleep -Seconds 15

# 3. Generate a high-entropy pepper LOCALLY and write it without ever echoing the value.
Write-Host "==> Generating and storing pepper (value never displayed)..." -ForegroundColor Cyan
$pepper = python -c "import secrets; print(secrets.token_urlsafe(48))"
az keyvault secret set --vault-name $VaultName --name $SecretName --value "$pepper" --output none
$pepper = $null   # drop the plaintext from memory as soon as it is stored

# 4. Optionally lock down networking for real PHI.
if ($DisablePublicAccess) {
    Write-Host "==> Disabling public network access (real-PHI posture)..." -ForegroundColor Yellow
    az keyvault update --name $VaultName --resource-group $ResourceGroup --public-network-access Disabled | Out-Null
    Write-Host "    NEXT: create a Fabric managed private endpoint to this vault and approve the pending connection." -ForegroundColor Yellow
}

# 5. Report (metadata only -- no secret value).
$meta = az keyvault secret show --vault-name $VaultName --name $SecretName --query "{name:name, enabled:attributes.enabled, version:id}" -o json
Write-Host ""
Write-Host "==> Done. Secret metadata:" -ForegroundColor Green
Write-Host $meta
Write-Host ""
Write-Host "In the Fabric notebook, wire Option 1 like this:" -ForegroundColor Green
Write-Host "  import os" -ForegroundColor Gray
Write-Host "  os.environ['PHI_DEID_KEYVAULT_URL'] = 'https://$VaultName.vault.azure.net/'" -ForegroundColor Gray
Write-Host "  os.environ.pop('PHI_DEID_PEPPER', None)  # ensure Option 2 does not shadow the vault" -ForegroundColor Gray
Write-Host "  PEPPER = get_pepper('$SecretName')       # never print PEPPER" -ForegroundColor Gray
