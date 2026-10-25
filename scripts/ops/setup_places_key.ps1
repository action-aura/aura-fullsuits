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
# script tells you exactly where when it gets there.
#
# Cost (corrected 2026-10-25): the old "roughly 6,000 free searches a month"
# here was the pre-March-2025 USD 200 monthly credit at the cheaper price
# tier. Owner's search asks for phone numbers and websites, which Google bills
# as "Text Search Enterprise": 1,000 free requests a month, then about USD 35
# per 1,000 (March 2025 pricing -- check the console's pricing page, Google
# changes it). Owner caps itself at OWNER_LEAD_DISCOVERY_DAILY_CAP (100) per
# Jordan day and OWNER_LEAD_DISCOVERY_MONTHLY_CAP (1000) per month, refusing
# before it calls Google. Set a Google-side daily quota as well (printed at
# the end) so a bug or a copied key cannot spend past it either.
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
# Fixed 2026-10-25: this used to enable places-backend.googleapis.com -- the
# LEGACY Places API. Owner calls https://places.googleapis.com/v1/places:searchText
# (app/leads/discovery.py), which is the NEW API, service places.googleapis.com;
# a key limited to the legacy service is refused by it (403), so every search
# would have failed on the first day. Not run against Google from here (no
# gcloud login in the dev environment) -- the names come from the endpoint.
Write-Host "Enabling places.googleapis.com (Places API (New)) ..."
& $gcloud services enable places.googleapis.com --project $ProjectId | Out-Null

# 4. API key restricted to the Places API (New) only (reuse by display name).
$keyName = & $gcloud services api-keys list --project $ProjectId --filter="displayName:$KeyName" --format="value(name)" 2>$null | Select-Object -First 1
if (-not $keyName) {
    Write-Host "Creating API key $KeyName ..."
    & $gcloud services api-keys create --project $ProjectId --display-name=$KeyName `
        --api-target=service=places.googleapis.com | Out-Null
    $keyName = & $gcloud services api-keys list --project $ProjectId --filter="displayName:$KeyName" --format="value(name)" | Select-Object -First 1
} else {
    # Re-apply the restriction, so a key made by the old (legacy-API) version
    # of this script is corrected rather than reused as it was.
    Write-Host "API key $KeyName exists -- restricting it to places.googleapis.com"
    & $gcloud services api-keys update $keyName --api-target=service=places.googleapis.com | Out-Null
}
$keyString = & $gcloud services api-keys get-key-string $keyName --format="value(keyString)"

Write-Host ""
Write-Host "Put these two lines in Owner's environment (/etc/aura-owner.env on the host, or the shell that starts it):" -ForegroundColor Green
Write-Host "OWNER_LEAD_DISCOVERY_PROVIDER=google_places"
Write-Host "OWNER_GOOGLE_PLACES_API_KEY=$keyString"
Write-Host "OWNER_LEAD_DISCOVERY_DAILY_CAP=100"
Write-Host "OWNER_LEAD_DISCOVERY_MONTHLY_CAP=1000"
Write-Host ""
Write-Host "Second safety net, in Google Cloud (do this once, by hand):" -ForegroundColor Yellow
Write-Host "  1. https://console.cloud.google.com/apis/api/places.googleapis.com/quotas?project=$ProjectId"
Write-Host "     Edit the Text Search requests-per-day quota down to about 110 (a little above Owner's 100,"
Write-Host "     so Owner's own friendly limit message is what staff normally see)."
Write-Host "  2. https://console.cloud.google.com/billing -> Budgets & alerts: a budget of USD 5 with email alerts."
Write-Host "     (A budget only warns; the quota in step 1 is what actually stops spending.)"
Write-Host ""
Write-Host "Restart Owner. The 'Discover leads from a map search' button appears on the Leads page for SALES staff."
