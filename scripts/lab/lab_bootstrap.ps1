# Aura Lab -- one-time, idempotent Owner Control Center bootstrap.
#
# Follows docs/ops/free-hosting-owner.md's bootstrap order (section 4.3-4.5),
# adapted from Docker/Linux to a local Windows Postgres + venv run:
#   migrate -> restore signing key -> seed rbac/catalog/plans/offline-policy
#   -> create admin -> preflight
#
# Safe to re-run: every step below either checks first or is idempotent by
# construction (see each step's own comment for exactly how).
#
# Windows PowerShell 5.1 AND pwsh 7 compatible: no '&&', no ternary '?:', no
# null-coalescing '??', ASCII only.

# NOT "Stop": external tools (alembic, flask, psql) write benign INFO/NOTICE
# lines to stderr, and with 2>&1 merging those into the pipeline, "Stop"
# treats each such line as a terminating error even on success. Every
# external call below is gated on its own explicit $LASTEXITCODE check
# instead, which is the real signal.
$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "lab_common.ps1")

$FoundDeviation = @()  # first-ever-run findings vs. the runbook, reported at the end.

function Add-Deviation {
    param([string]$Text)
    $Script:FoundDeviation += $Text
    Write-LabLog "DEVIATION FROM RUNBOOK: $Text"
}

Write-LabLog "=== Aura Lab bootstrap starting ==="
Write-LabLog "LabRoot=$LabRoot RepoRoot=$RepoRoot"

New-Item -ItemType Directory -Force -Path (Join-Path $LabRoot "logs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $LabRoot "run") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $LabRoot "desktop") | Out-Null

# ---------------------------------------------------------------------------
# 0. Sanity: the owner venv actually has what create_app() needs.
# ---------------------------------------------------------------------------
Write-LabLog "[0/9] Verifying owner venv imports..."
& $OwnerPython -c "import flask, sqlalchemy, psycopg, flask_babel, alembic, cryptography" 2>&1 | ForEach-Object { Write-LabLog "  $_" }
if ($LASTEXITCODE -ne 0) {
    throw "Owner venv is missing a required package (flask/sqlalchemy/psycopg/flask_babel/alembic/cryptography). Fix the venv before continuing."
}
Write-LabLog "[0/9] Owner venv OK."

# ---------------------------------------------------------------------------
# 1. Postgres up
# ---------------------------------------------------------------------------
Write-LabLog "[1/9] Checking Postgres on 5432..."
if (Test-Listening -Port 5432) {
    Write-LabLog "[1/9] Postgres already listening on 5432."
} else {
    Write-LabLog "[1/9] Postgres not listening -- starting it."
    & $PgCtl -D $PgData -l "C:/pgportable/logfile.txt" -o "-p 5432" start
    $deadline = (Get-Date).AddSeconds(30)
    while (-not (Test-Listening -Port 5432) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
    }
    if (-not (Test-Listening -Port 5432)) {
        throw "Postgres did not start listening on 5432 within 30s -- check C:/pgportable/logfile.txt"
    }
    Write-LabLog "[1/9] Postgres is now listening on 5432."
}

# ---------------------------------------------------------------------------
# 2. Create the lab database if absent (never touches any other aura_owner_* db)
# ---------------------------------------------------------------------------
Write-LabLog "[2/9] Checking aura_owner_lab database..."
$env:PGPASSWORD = "aura_owner_dev"

$canCreateDb = Invoke-PsqlScalar -Database "postgres" -Sql "SELECT rolcreatedb FROM pg_roles WHERE rolname='aura_owner';"
if ($canCreateDb -ne "t") {
    throw "Role aura_owner cannot create databases (rolcreatedb=$canCreateDb) -- stopping per fixed decisions. A human must grant CREATEDB or create aura_owner_lab manually."
}

$dbExists = Invoke-PsqlScalar -Database "postgres" -Sql "SELECT 1 FROM pg_database WHERE datname='aura_owner_lab';"
if ($dbExists -eq "1") {
    Write-LabLog "[2/9] Database aura_owner_lab already exists -- leaving it alone."
} else {
    Write-LabLog "[2/9] Creating database aura_owner_lab (owner aura_owner)..."
    & $PgPsql -h localhost -p 5432 -U aura_owner -d postgres -c "CREATE DATABASE aura_owner_lab OWNER aura_owner;"
    if ($LASTEXITCODE -ne 0) {
        throw "CREATE DATABASE aura_owner_lab failed (exit $LASTEXITCODE)."
    }
    Write-LabLog "[2/9] Database aura_owner_lab created."
}

# ---------------------------------------------------------------------------
# 3. Generate lab.env.json if absent (NEVER overwrite existing secrets)
# ---------------------------------------------------------------------------
Write-LabLog "[3/9] Checking lab.env.json..."
$labEnvPath = Get-LabEnvPath
if (Test-Path $labEnvPath) {
    Write-LabLog "[3/9] lab.env.json already exists at $labEnvPath -- leaving all existing values untouched."
} else {
    Write-LabLog "[3/9] Generating lab.env.json (secrets generated ONCE, never regenerated)..."
    $newLabEnv = [ordered]@{
        generated_at                = (Get-Date).ToString("o")
        lab_root                    = $LabRoot
        repo_root                   = $RepoRoot
        owner_db_name                = "aura_owner_lab"
        owner_database_url          = "postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_lab"
        owner_port                  = 5551
        owner_base_url              = "http://127.0.0.1:5551"
        owner_sync_relay_public_url = "http://127.0.0.1:5551"
        owner_secret_key            = (New-HexSecret -ByteLength 32)
        owner_license_pepper        = (New-HexSecret -ByteLength 32)
        signing_key_id              = "owner-ed25519-20260903T160641Z-cdcaa65b"
        signing_key_directory       = "C:/Users/MSI/.aura-signing/owner-signing-keys"
        admin_email                 = "owner@aura.lab"
        admin_display_name          = "Lab Owner"
        admin_password              = (New-StrongPassword -Length 20)
    }
    ($newLabEnv | ConvertTo-Json) | Set-Content -Path $labEnvPath -Encoding UTF8
    Write-LabLog "[3/9] Wrote $labEnvPath."
}
$LabEnv = Read-LabEnv

# ---------------------------------------------------------------------------
# 4. Copy the signing key PEM into the durable directory (idempotent, verified)
# ---------------------------------------------------------------------------
Write-LabLog "[4/9] Signing key PEM..."
$sourcePem = "C:/Users/MSI/Desktop/aura-fullsuits/.claude/worktrees/ci-hardening-w0.3-continue/owner/var/signing-keys/$($LabEnv.signing_key_id).pem"
$durableDir = $LabEnv.signing_key_directory
$destPem = Join-Path $durableDir ("$($LabEnv.signing_key_id).pem")

if (-not (Test-Path $sourcePem)) {
    throw "Source signing-key PEM not found at $sourcePem -- cannot proceed (see FIXED DECISIONS for its only known location)."
}
if (-not (Test-Path $durableDir)) {
    New-Item -ItemType Directory -Force -Path $durableDir | Out-Null
    Write-LabLog "[4/9] Created durable signing-key directory $durableDir."
}

$sourceHash = (Get-FileHash -Path $sourcePem -Algorithm SHA256).Hash
if (Test-Path $destPem) {
    $destHash = (Get-FileHash -Path $destPem -Algorithm SHA256).Hash
    if ($destHash -eq $sourceHash) {
        Write-LabLog "[4/9] $destPem already present with matching SHA256 ($destHash) -- not overwriting."
    } else {
        throw "REFUSING: $destPem already exists but its SHA256 ($destHash) does NOT match the source PEM's SHA256 ($sourceHash). A human must resolve this -- never auto-overwriting a signing key."
    }
} else {
    Copy-Item -Path $sourcePem -Destination $destPem -ErrorAction Stop
    $copiedHash = (Get-FileHash -Path $destPem -Algorithm SHA256).Hash
    if ($copiedHash -ne $sourceHash) {
        throw "REFUSING: copy verification failed -- $destPem's SHA256 ($copiedHash) does not match source ($sourceHash)."
    }
    Write-LabLog "[4/9] Copied signing key to $destPem (SHA256 $copiedHash verified equal to source)."
}

# ---------------------------------------------------------------------------
# 5. Set Owner process env, then run migrations
# ---------------------------------------------------------------------------
Write-LabLog "[5/9] Setting Owner environment and running migrations (alembic upgrade head)..."
Set-OwnerEnv -LabEnv $LabEnv | Out-Null
# Needed only for restore_signing_key.py's "from app import create_app"
# (it inserts a Linux container path, /app/owner, into sys.path -- a no-op
# on Windows -- so this run needs the real owner dir on PYTHONPATH instead).
$env:PYTHONPATH = (Join-Path $RepoRoot "owner")

$ownerDir = Join-Path $RepoRoot "owner"
Push-Location $ownerDir
try {
    & $OwnerPython -m alembic upgrade head 2>&1 | ForEach-Object { Write-LabLog "  alembic: $_" }
    if ($LASTEXITCODE -ne 0) {
        throw "alembic upgrade head failed (exit $LASTEXITCODE)."
    }
    Write-LabLog "[5/9] Migrations at head."

    # -----------------------------------------------------------------
    # 6. Restore the EXISTING signing key (register + activate), never
    #    generate a new one. Cross-checked against trust_anchor.json.
    # -----------------------------------------------------------------
    Write-LabLog "[6/9] Restoring signing key $($LabEnv.signing_key_id)..."
    $trustAnchorPath = Join-Path $RepoRoot "commercial_runtime/licensing_contracts/trust_anchor.json"
    if (-not (Test-Path $trustAnchorPath)) {
        Add-Deviation "trust_anchor.json not found at $trustAnchorPath -- restore_signing_key.py will WARN and proceed without a cross-check. Runbook assumes it is always available; on this checkout it is gitignored but present."
    }
    $restoreOutput = & $OwnerPython (Join-Path $RepoRoot "scripts/deploy/restore_signing_key.py") $LabEnv.signing_key_id "--trust-anchor" $trustAnchorPath 2>&1
    $restoreExit = $LASTEXITCODE
    $restoreOutput | ForEach-Object { Write-LabLog "  restore_signing_key: $_" }
    if ($restoreExit -ne 0) {
        throw "restore_signing_key.py REFUSED or failed (exit $restoreExit) -- STOPPING per fixed decisions (do not bypass a cross-check failure)."
    }
    Write-LabLog "[6/9] Signing key restored/activated."

    # -----------------------------------------------------------------
    # 7. Seeds (each individually idempotent per its own CLI docstring)
    # -----------------------------------------------------------------
    Write-LabLog "[7/9] Seeding rbac/catalog/plans/offline-policy..."
    foreach ($seedCmd in @("seed-rbac", "seed-catalog", "seed-plans", "seed-offline-policy")) {
        $seedOutput = & $OwnerFlaskExe $seedCmd 2>&1
        $seedExit = $LASTEXITCODE
        $seedOutput | ForEach-Object { Write-LabLog "  ${seedCmd}: $_" }
        if ($seedExit -ne 0) {
            throw "flask $seedCmd failed (exit $seedExit)."
        }
    }
    Write-LabLog "[7/9] Seeds complete."

    # -----------------------------------------------------------------
    # 8. Create the superadmin ONLY if none exists yet in THIS db.
    #    create-superadmin itself refuses if one already exists -- that
    #    refusal IS the idempotency check, not a fallback for one.
    # -----------------------------------------------------------------
    Write-LabLog "[8/9] Ensuring a Super Admin exists..."
    $env:OWNER_BOOTSTRAP_PASSWORD = $LabEnv.admin_password
    $adminOutput = & $OwnerFlaskExe create-superadmin --non-interactive --email $LabEnv.admin_email --display-name $LabEnv.admin_display_name 2>&1
    $adminExit = $LASTEXITCODE
    Remove-Item Env:OWNER_BOOTSTRAP_PASSWORD -ErrorAction SilentlyContinue
    $adminOutputText = ($adminOutput -join "`n")
    if ($adminExit -eq 0) {
        Write-LabLog "[8/9] Super Admin created: $($LabEnv.admin_email)."
    } elseif ($adminOutputText -match "A Super Admin already exists") {
        Write-LabLog "[8/9] Super Admin already exists -- skipped (idempotent)."
    } else {
        Write-LabLog "  create-superadmin output: $adminOutputText"
        throw "flask create-superadmin failed unexpectedly (exit $adminExit)."
    }

    # -----------------------------------------------------------------
    # 9. Verify: commercial preflight + signing-key health
    # -----------------------------------------------------------------
    Write-LabLog "[9/9] Running flask commercial preflight..."
    $preflightOutput = & $OwnerFlaskExe commercial preflight 2>&1
    $preflightExit = $LASTEXITCODE
    $preflightText = ($preflightOutput -join "`n")
    Write-LabLog "  preflight: $preflightText"

    Write-LabLog "[9/9] Running flask licensing verify-signing-key-health..."
    $healthOutput = & $OwnerFlaskExe licensing verify-signing-key-health 2>&1
    $healthExit = $LASTEXITCODE
    $healthText = ($healthOutput -join "`n")
    Write-LabLog "  signing-key-health: $healthText"
} finally {
    Pop-Location
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
}

Write-LabLog "=== Aura Lab bootstrap finished ==="
Write-LabLog "Preflight exit=$preflightExit  Signing-key-health exit=$healthExit"
if ($FoundDeviation.Count -eq 0) {
    Write-LabLog "No deviations from docs/ops/free-hosting-owner.md's bootstrap order were found on this run."
} else {
    Write-LabLog "Deviations found this run:"
    foreach ($d in $FoundDeviation) { Write-LabLog "  - $d" }
}

if ($preflightExit -ne 0 -or $healthExit -ne 0) {
    Write-LabLog "RESULT: FAILED -- preflight or signing-key-health reported a problem. See log above."
    exit 1
}
Write-LabLog "RESULT: OK -- Owner is bootstrapped: aura_owner_lab, signing key active, RBAC/catalog/plans/offline-policy seeded, Super Admin present."
