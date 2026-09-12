# Provision the Google Places API key that switches on Owner's lead discovery.
#
# ONE human step precedes this and cannot be automated: the account holder
# must log in once with
#
#     gcloud auth login
#
# (it opens a browser; sign in with the Google account that should own the
# key). Everything after that -- project, API enablement, a key restricted to
# the Places API, and the two env lines Owner needs -- this script does.
#
# What it deliberately does NOT do: attach a billing account. Google lets the
# Places API be enabled on a project without billing, but requests are refused
# until one is linked. Linking billing means agreeing to be charged, and that
# is the account holder's decision made in the console, not a script's. The
# script tells you exactly where when it gets there. Free tier at Text Search
# pricing covers roughly 6,000 searches a month (2026-09 pricing), which is
# well beyond a small sales team's use.
#
# Safe to re-run: every step is idempotent (existing project/API/key reused).
param(
    [string]$ProjectId = "aura-owner-leads",
    [string]$KeyName   = "aura-owner-lead-discovery"
)
$ErrorActionPreference = "Stop"
$gcloud = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
if (-not (Test-Path $gcloud)) { throw "gcloud not found at $gcloud -- install Google.CloudSDK via winget first" }

$account = & $gcloud auth list --filter="status:ACTIVE" --format="value(account)" 2>$null
if (-not $account) {
    Write-Host "Not logged in. Run this first, then re-run the script:" -ForegroundColor Yellow
    Write-Host "    gcloud auth login"
    exit 2
}
Write-Host "Logged in as $account"

# 1. Project (reuse if it exists).
$existing = & $gcloud projects list --filter="projectId:$ProjectId" --format="value(projectId)" 2>$null
if (-not $existing) {
    Write-Host "Creating project $ProjectId ..."
    & $gcloud projects create $ProjectId --name="Aura Owner lead discovery" | Out-Null
} else { Write-Host "Project $ProjectId exists" }
& $gcloud config set project $ProjectId | Out-Null

# 2. Billing -- report, never decide.
$billing = & $gcloud billing projects describe $ProjectId --format="value(billingEnabled)" 2>$null
if ($billing -ne "True") {
    Write-Host ""
    Write-Host "BILLING IS NOT LINKED to $ProjectId. Places requests will be refused until it is." -ForegroundColor Yellow
    Write-Host "Link it here (your decision, your card):"
    Write-Host "    https://console.cloud.google.com/billing/linkedaccount?project=$ProjectId"
    Write-Host "Then re-run this script. Continuing so the key exists and is ready."
}

# 3. Enable the Places API (New).
Write-Host "Enabling places-backend.googleapis.com ..."
& $gcloud services enable places-backend.googleapis.com --project $ProjectId | Out-Null

# 4. API key restricted to the Places API only (reuse by display name).
$keyName = & $gcloud services api-keys list --project $ProjectId --filter="displayName:$KeyName" --format="value(name)" 2>$null | Select-Object -First 1
if (-not $keyName) {
    Write-Host "Creating API key $KeyName ..."
    & $gcloud services api-keys create --project $ProjectId --display-name=$KeyName `
        --api-target=service=places-backend.googleapis.com | Out-Null
    $keyName = & $gcloud services api-keys list --project $ProjectId --filter="displayName:$KeyName" --format="value(name)" | Select-Object -First 1
} else { Write-Host "API key $KeyName exists" }
$keyString = & $gcloud services api-keys get-key-string $keyName --format="value(keyString)"

Write-Host ""
Write-Host "Put these two lines in Owner's environment (/etc/aura-owner.env on the droplet, or the shell that starts it):" -ForegroundColor Green
Write-Host "OWNER_LEAD_DISCOVERY_PROVIDER=google_places"
Write-Host "OWNER_GOOGLE_PLACES_API_KEY=$keyString"
Write-Host ""
Write-Host "Restart Owner. The 'Discover leads from a map search' button appears on the Leads page for SALES staff."
