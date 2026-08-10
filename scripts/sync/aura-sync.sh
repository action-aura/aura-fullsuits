#!/usr/bin/env bash
# aura-sync.sh -- bash port of aura-sync.ps1 for the friend's OS (macOS/Linux/Git Bash).
# Same algorithm, same config file, same exit codes, same log/audit format as the
# PowerShell engine. Requires bash 3.2+ (macOS default, any Linux, Git Bash on Windows).
#
# Usage:
#   ./aura-sync.sh                    one sync pass (default)
#   ./aura-sync.sh --once             same as above, explicit
#   ./aura-sync.sh --watch            foreground loop, Ctrl+C to stop
#   ./aura-sync.sh --track-current-branch   sync whatever branch is checked out, not just master
#   ./aura-sync.sh --install [--interval-minutes 5] [--track-current-branch]   cron entry (Linux/macOS)
#   ./aura-sync.sh --uninstall        remove the cron entry
#   ./aura-sync.sh --status           show cron entry + last pass + pause state
#   ./aura-sync.sh --pause [--minutes 30] [--reason "risky rebase"]
#   ./aura-sync.sh --resume
#
# See docs/ops/auto-sync.md for the full guide, plus launchd/systemd alternatives to cron.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$REPO_ROOT" ]; then
    echo "aura-sync: '$SCRIPT_DIR' is not inside a git repository." >&2
    exit 4
fi

RUNTIME_DIR="$REPO_ROOT/.autosync"
LOG_DIR="$RUNTIME_DIR/logs"
LOCK_DIR="$RUNTIME_DIR/sync.lock"
STATE_FILE="$RUNTIME_DIR/state.kv"
PAUSE_FILE="$RUNTIME_DIR/PAUSED"
CONFLICT_FILE="$RUNTIME_DIR/CONFLICT.md"
LASTRUN_FILE="$RUNTIME_DIR/last-run.txt"
AUDIT_FILE="$LOG_DIR/audit.jsonl"
CHURN_FILE="$RUNTIME_DIR/churn.tsv"
CONFIG_DEFAULTS="$SCRIPT_DIR/sync.config"
CONFIG_LOCAL="$RUNTIME_DIR/sync.config.local"
CRON_MARKER="# aura-sync:$REPO_ROOT"

mkdir -p "$LOG_DIR"

TIMEOUT_BIN=""
if command -v timeout >/dev/null 2>&1; then TIMEOUT_BIN="timeout"
elif command -v gtimeout >/dev/null 2>&1; then TIMEOUT_BIN="gtimeout"
fi

# ---------------------------------------------------------------------------
# Config loading -- flat KEY=value, layered: defaults < local override < env.
# ---------------------------------------------------------------------------

declare_var_from_line() {
    local line="$1"
    line="${line%$'\r'}"   # tolerate CRLF -- .autosync/sync.config.local is gitignored
                           # (not covered by .gitattributes eol=lf) and easy to save as
                           # CRLF from a Windows editor.
    case "$line" in ''|'#'*) return ;; esac
    local key="${line%%=*}"
    local val="${line#*=}"
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    case "$key" in [A-Z_]*) ;; *) return ;; esac
    printf -v "$key" '%s' "$val"
}

load_config_file() {
    local file="$1"
    [ -f "$file" ] || return 0
    while IFS= read -r line || [ -n "$line" ]; do
        declare_var_from_line "$line"
    done < "$file"
}

apply_env_overrides() {
    local name val key
    while IFS='=' read -r name val; do
        case "$name" in
            AURA_SYNC_*)
                key="${name#AURA_SYNC_}"
                printf -v "$key" '%s' "$val"
                ;;
        esac
    done < <(env)
}

# ---------------------------------------------------------------------------
# Git wrapper -- hardened against interactive credential prompts (an expired
# token must fail fast, never pop a GUI prompt and hang a cron job forever).
# ---------------------------------------------------------------------------

git_hardened() {
    GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never GIT_ASKPASS= SSH_ASKPASS= \
        git -C "$REPO_ROOT" -c credential.interactive=false -c core.askPass= -c advice.detachedHead=false "$@"
}

git_hardened_timeout() {
    local secs="$1"; shift
    if [ -n "$TIMEOUT_BIN" ]; then
        GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never GIT_ASKPASS= SSH_ASKPASS= \
            "$TIMEOUT_BIN" "$secs" git -C "$REPO_ROOT" -c credential.interactive=false -c core.askPass= -c advice.detachedHead=false "$@"
    else
        git_hardened "$@"
    fi
}

git_dir_path() {
    local d; d="$(git_hardened rev-parse --git-dir 2>/dev/null)"
    case "$d" in /*) printf '%s' "$d" ;; *) printf '%s/%s' "$REPO_ROOT" "$d" ;; esac
}

# ---------------------------------------------------------------------------
# State (per-machine, gitignored, never shared -- flat key=value)
# ---------------------------------------------------------------------------

get_state() {
    local key="$1"
    [ -f "$STATE_FILE" ] || return 0
    awk -F= -v k="$key" '$1==k{ $0=substr($0, length(k)+2); print; exit }' "$STATE_FILE" 2>/dev/null
}

set_state() {
    local key="$1" val="$2"
    local tmp="$STATE_FILE.tmp.$$"
    { [ -f "$STATE_FILE" ] && grep -v -E "^${key}=" "$STATE_FILE"; printf '%s=%s\n' "$key" "$val"; } > "$tmp" 2>/dev/null
    mv "$tmp" "$STATE_FILE"
}

# ---------------------------------------------------------------------------
# Logging -- human log (.autosync/logs/sync-YYYY-MM-DD.log) + structured
# audit log (.autosync/logs/audit.jsonl), written together on every event.
# ---------------------------------------------------------------------------

new_pass_id() {
    printf '%s-%04d' "$(date -u +%Y%m%dT%H%M%S)" "$((RANDOM % 9999))"
}

json_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\t'/\\t}"
    printf '%s' "$s"
}

write_log() {
    local level="$1"; shift
    local event="$1"; shift
    local human_now iso_now
    human_now="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    iso_now="$human_now"
    local human="$human_now [$level] pass=$PASS_ID event=$event"
    local json_fields=""
    local kv k v vesc
    for kv in "$@"; do
        [ -z "$kv" ] && continue
        k="${kv%%=*}"
        v="${kv#*=}"
        human="$human $k=$v"
        vesc="$(json_escape "$v")"
        if [ -n "$json_fields" ]; then json_fields="$json_fields,"; fi
        json_fields="$json_fields\"$k\":\"$vesc\""
    done
    echo "$human" >> "$LOG_DIR/sync-$(date -u '+%Y-%m-%d').log"
    printf '{"timestamp":"%s","pass":"%s","level":"%s","event":"%s","host":"%s","user":"%s","pid":%d,"data":{%s}}\n' \
        "$iso_now" "$PASS_ID" "$level" "$event" "$(hostname)" "$(whoami)" "$$" "$json_fields" >> "$AUDIT_FILE"
    case "$level" in WARN|ERROR|CRITICAL) echo "$human" >&2 ;; esac
}

run_simple_guard() {
    local name="$1" func="$2"
    GUARD_REASON="$("$func")"
    local rc=$?
    if [ $rc -eq 0 ]; then
        write_log DEBUG guard "guard=$name" "pass=true" "reason=$GUARD_REASON"
    else
        write_log WARN guard "guard=$name" "pass=false" "reason=$GUARD_REASON"
    fi
    return $rc
}

send_notification() {
    local title="$1" message="$2"
    case "$(uname -s)" in
        Darwin)
            osascript -e "display notification \"$(printf '%s' "$message" | sed 's/"/\\"/g')\" with title \"$(printf '%s' "$title" | sed 's/"/\\"/g')\"" >/dev/null 2>&1
            ;;
        Linux)
            command -v notify-send >/dev/null 2>&1 && notify-send "$title" "$message" >/dev/null 2>&1
            ;;
        MINGW*|MSYS*|CYGWIN*)
            command -v powershell.exe >/dev/null 2>&1 && powershell.exe -NoProfile -Command \
                "Add-Type -AssemblyName System.Windows.Forms; \$n=New-Object System.Windows.Forms.NotifyIcon; \$n.Icon=[System.Drawing.SystemIcons]::Information; \$n.Visible=\$true; \$n.ShowBalloonTip(8000,'$title','$message','Info'); Start-Sleep -Milliseconds 300" >/dev/null 2>&1
            ;;
    esac
    return 0
}

write_summary() {
    local result="$1" reason="$2" pulled="$3" committed_files="$4" push_result="$5" skipped="$6"
    local pulled_count=0 committed_count=0
    [ -n "$pulled" ] && pulled_count="$(printf '%s\n' "$pulled" | grep -c .)"
    [ -n "$committed_files" ] && committed_count="$(printf '%s\n' "$committed_files" | grep -c .)"

    {
        echo "aura-sync  $(date -u '+%Y-%m-%d %H:%M:%S UTC')  pass=$PASS_ID  result=$result"
        if [ -n "${PASS_BRANCH:-}" ]; then
            local mode_label="fixed"
            [ "${TRACK_CURRENT_MODE:-0}" -eq 1 ] && mode_label="current"
            echo "branch: $PASS_BRANCH (mode=$mode_label)"
        fi
        echo "reason: $reason"
        if [ "$pulled_count" -gt 0 ]; then
            echo "pulled ($pulled_count):"
            printf '%s\n' "$pulled" | sed 's/^/  /'
        else
            echo "pulled: none"
        fi
        if [ "$committed_count" -gt 0 ]; then
            echo "committed ($committed_count): $(printf '%s' "$committed_files" | tr '\n' ',' | sed 's/,$//')"
        else
            echo "committed: none"
        fi
        echo "push: $push_result"
        if [ -n "$skipped" ]; then
            echo "skipped (guard blocked): $skipped"
        fi
    } > "$LASTRUN_FILE"

    cat "$LASTRUN_FILE"
    write_log INFO pass-summary "result=$result" "pulled=$pulled_count" "committed=$committed_count" "push=$push_result"

    if [ "${NOTIFY:-true}" = "true" ]; then
        if [ "$result" != "ok" ]; then
            send_notification "aura-sync: $result" "$reason"
        elif [ "$pulled_count" -gt 0 ] || [ "$committed_count" -gt 0 ]; then
            send_notification "aura-sync: synced" "pulled $pulled_count, committed $committed_count, push=$push_result"
        fi
    fi
}

prune_old_logs() {
    local retention_days="$1"
    find "$LOG_DIR" -maxdepth 1 -name 'sync-*.log' -mtime "+$retention_days" -exec rm -f {} \; 2>/dev/null
}

# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

enter_lock() {
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        printf '%s@%s %s\n' "$$" "$(hostname)" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > "$LOCK_DIR/owner.txt"
        echo "acquired"; return 0
    fi
    if [ -d "$LOCK_DIR" ]; then
        local created_epoch now_epoch age
        created_epoch="$(stat -c %Y "$LOCK_DIR" 2>/dev/null || stat -f %m "$LOCK_DIR" 2>/dev/null)"
        now_epoch="$(date -u +%s)"
        age=$(( now_epoch - ${created_epoch:-now_epoch} ))
        if [ "$age" -gt "${LOCK_STALE_SECONDS:-900}" ]; then
            local owner_pid
            owner_pid="$(cut -d@ -f1 "$LOCK_DIR/owner.txt" 2>/dev/null)"
            if [ -n "$owner_pid" ] && ! kill -0 "$owner_pid" 2>/dev/null; then
                rm -rf "$LOCK_DIR"
                if mkdir "$LOCK_DIR" 2>/dev/null; then
                    printf '%s@%s %s\n' "$$" "$(hostname)" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > "$LOCK_DIR/owner.txt"
                    echo "reclaimed-stale-lock"; return 0
                fi
            fi
        fi
    fi
    echo "lock-held"; return 1
}

exit_lock() { rm -rf "$LOCK_DIR" 2>/dev/null; }

set_pause_flag() {
    local reason="$1" minutes="${2:-0}"
    {
        echo "reason=$reason"
        echo "setAtEpoch=$(date -u +%s)"
        if [ "${minutes:-0}" -gt 0 ] 2>/dev/null; then
            echo "expiresAtEpoch=$(( $(date -u +%s) + minutes*60 ))"
        fi
    } > "$PAUSE_FILE"
}

guard_pause() {
    if [ -f "$PAUSE_FILE" ]; then
        local reason expires_epoch now_epoch
        reason="$(sed -n 's/^reason=//p' "$PAUSE_FILE" | head -1)"
        expires_epoch="$(sed -n 's/^expiresAtEpoch=//p' "$PAUSE_FILE" | head -1)"
        now_epoch="$(date -u +%s)"
        if [ -n "$expires_epoch" ] && [ "$expires_epoch" -lt "$now_epoch" ] 2>/dev/null; then
            rm -f "$PAUSE_FILE"
        else
            echo "paused:${reason:-manual}"; return 1
        fi
    fi
    if [ "$(git_hardened config --local --get aura.sync.paused 2>/dev/null)" = "true" ]; then
        echo "paused:git-config"; return 1
    fi
    echo "not-paused"; return 0
}

guard_in_progress() {
    local git_dir; git_dir="$(git_dir_path)"
    local marker
    for marker in MERGE_HEAD rebase-merge rebase-apply CHERRY_PICK_HEAD REVERT_HEAD BISECT_LOG; do
        if [ -e "$git_dir/$marker" ]; then echo "operation-in-progress"; return 1; fi
    done
    echo "clean"; return 0
}

guard_branch() {
    local branch; branch="$(git_hardened symbolic-ref --quiet --short HEAD 2>/dev/null)"
    if [ -z "$branch" ]; then echo "detached-head"; return 1; fi
    if [ "${TRACK_CURRENT_MODE:-0}" -eq 1 ]; then
        echo "$branch"; return 0
    fi
    local allowed b ok=1
    IFS=',' read -ra allowed <<< "$SYNC_BRANCHES"
    for b in "${allowed[@]}"; do
        b="$(printf '%s' "$b" | tr -d '[:space:]')"
        [ "$b" = "$branch" ] && ok=0
    done
    if [ "$ok" -ne 0 ]; then echo "branch-not-allowed:$branch"; return 1; fi
    echo "$branch"; return 0
}

guard_upstream() {
    local upstream; upstream="$(git_hardened rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)"
    if [ -z "$upstream" ]; then echo "no-upstream"; return 1; fi
    echo "$upstream"; return 0
}

# "Track current branch" mode equivalent of guard_upstream: a branch that's
# never had `git push -u` run against it has no @{u} configured even though it
# may already exist on origin (common right after checking out a teammate's
# branch) -- fall back to a matching remote-tracking ref before concluding
# it's genuinely brand-new. Sets globals (not a $(...) capture -- this needs
# to mutate the caller's shell, which a subshell can't do) UPSTREAM,
# REMOTE_EXISTS, NEEDS_SET_UPSTREAM, UPSTREAM_REASON. Returns 0/1.
resolve_upstream() {
    local branch="$1" allow_new="$2" upstream
    upstream="$(git_hardened rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)"
    if [ -n "$upstream" ]; then
        UPSTREAM="$upstream"; REMOTE_EXISTS=1; NEEDS_SET_UPSTREAM=0; UPSTREAM_REASON="$upstream"
        return 0
    fi
    if git_hardened rev-parse --verify --quiet "refs/remotes/origin/$branch" >/dev/null 2>&1; then
        UPSTREAM="origin/$branch"; REMOTE_EXISTS=1; NEEDS_SET_UPSTREAM=1; UPSTREAM_REASON="upstream-inferred"
        return 0
    fi
    if [ "$allow_new" -eq 1 ]; then
        UPSTREAM=""; REMOTE_EXISTS=0; NEEDS_SET_UPSTREAM=1; UPSTREAM_REASON="new-remote-branch"
        return 0
    fi
    UPSTREAM_REASON="no-upstream"
    return 1
}

# Guards the window between reading $branch and actually pushing/pulling
# against it -- the jitter sleep and the fetch both give a dev time to
# `git checkout` something else mid-pass.
guard_branch_unchanged() {
    local expected="$1" actual
    actual="$(git_hardened symbolic-ref --quiet --short HEAD 2>/dev/null)"
    if [ -z "$actual" ] || [ "$actual" != "$expected" ]; then
        echo "branch-changed-mid-pass:$expected->$actual"; return 1
    fi
    echo "unchanged"; return 0
}

guard_identity() {
    local name email
    name="$(git_hardened config user.name 2>/dev/null)"
    email="$(git_hardened config user.email 2>/dev/null)"
    if [ -z "$name" ] || [ -z "$email" ]; then echo "missing-identity"; return 1; fi
    echo "ok"; return 0
}

guard_backoff() {
    local until now
    until="$(get_state authBackoffUntilEpoch)"
    if [ -n "$until" ]; then
        now="$(date -u +%s)"
        if [ "$until" -gt "$now" ] 2>/dev/null; then echo "auth-backoff-until:$until"; return 1; fi
    fi
    echo "ok"; return 0
}

path_denylisted() {
    local path="$1" leaf pat
    leaf="$(basename "$path")"
    local IFS=','
    for pat in $PATH_DENYLIST; do
        case "$leaf" in $pat) return 0 ;; esac
        case "$path" in $pat) return 0 ;; esac
    done
    return 1
}

SECRET_NAMES=(aws-access-key github-pat-classic github-pat-fine private-key-block slack-token generic-sk-key google-api-key)
SECRET_REGEXES=(
    'AKIA[0-9A-Z]{16}'
    'ghp_[A-Za-z0-9]{36}'
    'github_pat_[A-Za-z0-9_]{22,}'
    '-----BEGIN [A-Z ]*PRIVATE KEY-----'
    'xox[baprs]-[A-Za-z0-9-]{10,}'
    'sk-[A-Za-z0-9]{20,}'
    'AIza[0-9A-Za-z_-]{35}'
)

secret_scan() {
    local diff hits=0 current_file="" line content i re
    diff="$(git_hardened diff --cached -U0)"
    while IFS= read -r line; do
        case "$line" in
            "+++ b/"*) current_file="${line#+++ b/}"; continue ;;
        esac
        case "$line" in
            "+++"*) continue ;;
            "+"*)
                content="${line:1}"
                i=0
                for re in "${SECRET_REGEXES[@]}"; do
                    if [[ "$content" =~ $re ]]; then
                        hits=$((hits+1))
                        write_log ERROR secret-detected "path=$current_file" "rule=${SECRET_NAMES[$i]}"
                    fi
                    i=$((i+1))
                done
                ;;
        esac
    done <<< "$diff"
    [ "$hits" -eq 0 ]
}

conflict_marker_gate() {
    local upstream="$1" diff
    diff="$(git_hardened diff "$upstream...HEAD" -U0)"
    if printf '%s' "$diff" | grep -q '^\+<<<<<<< ' && printf '%s' "$diff" | grep -q '^\+=======$' && printf '%s' "$diff" | grep -q '^\+>>>>>>> '; then
        return 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------

sync_status() {
    local out line
    out="$(git_hardened status --porcelain=v2 --branch --untracked-files=normal)"
    ST_AHEAD=0; ST_BEHIND=0; ST_DIRTY_COUNT=0; ST_UNTRACKED_COUNT=0; ST_UNMERGED=0
    while IFS= read -r line; do
        case "$line" in
            "# branch.ab "*)
                ST_AHEAD="$(printf '%s' "$line" | sed -n 's/.*+\([0-9]*\) -.*/\1/p')"
                ST_BEHIND="$(printf '%s' "$line" | sed -n 's/.*-\([0-9]*\)$/\1/p')"
                ;;
            "1 "*|"2 "*) ST_DIRTY_COUNT=$((ST_DIRTY_COUNT+1)) ;;
            "? "*) ST_UNTRACKED_COUNT=$((ST_UNTRACKED_COUNT+1)) ;;
            "u "*) ST_UNMERGED=1 ;;
        esac
    done <<< "$out"
    ST_AHEAD="${ST_AHEAD:-0}"; ST_BEHIND="${ST_BEHIND:-0}"
}

new_snapshot() {
    local epoch head_sha wip_sha
    epoch="$(date -u +%s)"
    head_sha="$(git_hardened rev-parse HEAD 2>/dev/null)"
    [ -n "$head_sha" ] && git_hardened update-ref "refs/autosync/backup/$epoch" "$head_sha" >/dev/null 2>&1
    wip_sha="$(git_hardened stash create 2>/dev/null)"
    [ -n "$wip_sha" ] && git_hardened update-ref "refs/autosync/wip/$epoch" "$wip_sha" >/dev/null 2>&1
    write_log DEBUG snapshot "epoch=$epoch" "backup=${head_sha:-none}" "wip=${wip_sha:-none}"
}

prune_snapshots() {
    local keep="$1" refs ref count=0
    refs="$(git_hardened for-each-ref --sort=-creatordate --format='%(refname)' refs/autosync/)"
    while IFS= read -r ref; do
        [ -z "$ref" ] && continue
        count=$((count+1))
        [ "$count" -gt "$keep" ] && git_hardened update-ref -d "$ref" >/dev/null 2>&1
    done <<< "$refs"
}

check_merge_tree_conflict() {
    local base="$1" upstream="$2" out rc
    out="$(git_hardened merge-tree --write-tree --name-only "$base" "$upstream" 2>&1)"
    rc=$?
    if [ $rc -eq 0 ]; then
        MT_CONFLICT=0; MT_FILES=""
    elif [ $rc -eq 1 ]; then
        # Output is a blank-line-separated paragraph pair: paragraph 1 is "<tree
        # oid>\n<conflicted file>\n..." (--name-only), paragraph 2 (if present) is
        # informational "Auto-merging"/"CONFLICT" messages -- not part of the file list.
        MT_CONFLICT=1
        MT_FILES="$(printf '%s\n' "$out" | awk -v RS='' 'NR==1{print}' | tail -n +2)"
    else
        write_log ERROR merge-tree-error "stderr=$(printf '%s' "$out" | tr '\n' ' ')"
        MT_CONFLICT=1; MT_FILES="(merge-tree error, see log)"
    fi
}

auto_commit() {
    local branch="$1"
    AC_COMMITTED=0; AC_FILES=""; AC_SKIPPED=""; AC_SHA=""; AC_SECRET_BLOCKED=0

    sync_status
    if [ "$ST_DIRTY_COUNT" -eq 0 ] && [ "$ST_UNTRACKED_COUNT" -eq 0 ]; then
        set_state dirtyFingerprint ""
        return 0
    fi

    local porcelain fingerprint now_epoch prev_fp since
    porcelain="$(git_hardened status --porcelain=v2 --untracked-files=normal)"
    if command -v sha256sum >/dev/null 2>&1; then
        fingerprint="$(printf '%s' "$porcelain" | sha256sum | cut -d' ' -f1 | cut -c1-16)"
    elif command -v shasum >/dev/null 2>&1; then
        fingerprint="$(printf '%s' "$porcelain" | shasum -a 256 | cut -d' ' -f1 | cut -c1-16)"
    else
        fingerprint="$(printf '%s' "$porcelain" | cksum | cut -d' ' -f1)"
    fi
    now_epoch="$(date -u +%s)"
    prev_fp="$(get_state dirtyFingerprint)"

    if [ "$prev_fp" != "$fingerprint" ]; then
        set_state dirtyFingerprint "$fingerprint"
        set_state dirtyFingerprintSinceEpoch "$now_epoch"
        write_log DEBUG commit-debounce "reason=tree-changed-this-pass"
        return 0
    fi
    since="$(get_state dirtyFingerprintSinceEpoch)"
    since="${since:-$now_epoch}"
    if [ $(( now_epoch - since )) -lt "${COMMIT_QUIET_SECONDS:-90}" ]; then
        write_log DEBUG commit-debounce "reason=quiet-period-not-elapsed"
        return 0
    fi

    git_hardened add -A -- . >/dev/null 2>&1

    local staged path skip reason size_kb churn_count kept_files=""
    staged="$(git_hardened diff --cached --name-only)"
    while IFS= read -r path; do
        [ -z "$path" ] && continue
        skip=0; reason=""
        if path_denylisted "$path"; then
            skip=1; reason="denylist"
        else
            size_kb="$(git_hardened cat-file -s ":$path" 2>/dev/null)"
            size_kb=$(( ${size_kb:-0} / 1024 ))
            if [ "$size_kb" -gt "${MAX_FILE_KB:-5120}" ]; then skip=1; reason="size"; fi
        fi
        if [ "$skip" -eq 0 ]; then
            churn_count="$(awk -F'\t' -v p="$path" -v now="$now_epoch" '$1==p && (now-$2)<3600{c++} END{print c+0}' "$CHURN_FILE" 2>/dev/null)"
            if [ "${churn_count:-0}" -gt "${CHURN_LIMIT:-5}" ]; then skip=1; reason="churn"; fi
        fi
        if [ "$skip" -eq 1 ]; then
            git_hardened restore --staged -- "$path" >/dev/null 2>&1
            AC_SKIPPED="$AC_SKIPPED$path;"
            write_log WARN skip-path "path=$path" "reason=$reason"
        fi
    done <<< "$staged"

    local still_staged
    still_staged="$(git_hardened diff --cached --name-only)"
    if [ -z "$still_staged" ]; then
        set_state dirtyFingerprint ""
        return 0
    fi

    if [ "${SECRET_SCAN:-true}" = "true" ]; then
        if ! secret_scan; then
            git_hardened reset >/dev/null 2>&1
            AC_SECRET_BLOCKED=1
            return 0
        fi
    fi

    local files count top_dirs subject
    files="$(git_hardened diff --cached --name-only)"
    count="$(printf '%s\n' "$files" | grep -c .)"
    top_dirs="$(printf '%s\n' "$files" | awk -F/ '{print $1}' | sort -u | head -3 | paste -sd, -)"
    subject="$COMMIT_PREFIX: $count file(s) in $top_dirs [$(hostname)] $(date -u '+%Y-%m-%d %H:%M')"

    local can_amend=0 head_subject unpushed committer_epoch upstream_ref remote_branch_exists is_ancestor
    if [ "${AMEND_AUTOSYNC_COMMITS:-true}" = "true" ]; then
        head_subject="$(git_hardened log -1 --format=%s)"
        upstream_ref="origin/$branch"
        if git_hardened rev-parse --verify --quiet "refs/remotes/$upstream_ref" >/dev/null 2>&1; then
            remote_branch_exists=1
            unpushed="$(git_hardened rev-list --count "$upstream_ref..HEAD" 2>/dev/null)"
        else
            # Brand-new branch, nothing pushed yet -- every commit is unpushed
            # and nothing can be "already an ancestor of origin".
            remote_branch_exists=0
            unpushed="$(git_hardened rev-list --count HEAD 2>/dev/null)"
        fi
        case "$head_subject" in
            "$COMMIT_PREFIX"*)
                if [ "${unpushed:-0}" -ge 1 ]; then
                    is_ancestor=1
                    if [ "$remote_branch_exists" -eq 1 ]; then
                        git_hardened merge-base --is-ancestor HEAD "$upstream_ref" >/dev/null 2>&1
                        is_ancestor=$?
                    fi
                    if [ "$is_ancestor" -ne 0 ]; then
                        committer_epoch="$(git_hardened log -1 --format=%ct)"
                        if [ $(( now_epoch - ${committer_epoch:-0} )) -lt "${AMEND_WINDOW_SECONDS:-900}" ]; then
                            can_amend=1
                        fi
                    fi
                fi
                ;;
        esac
    fi

    if [ "$can_amend" -eq 1 ]; then
        git_hardened commit --amend -m "$subject" >/dev/null 2>&1
    else
        git_hardened commit -m "$subject" >/dev/null 2>&1
    fi

    AC_SHA="$(git_hardened rev-parse HEAD)"
    AC_COMMITTED=1
    AC_FILES="$files"

    local f
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        printf '%s\t%s\n' "$f" "$now_epoch" >> "$CHURN_FILE"
    done <<< "$files"
    if [ -f "$CHURN_FILE" ]; then
        awk -F'\t' -v now="$now_epoch" '(now-$2)<3600' "$CHURN_FILE" > "$CHURN_FILE.tmp" 2>/dev/null && mv "$CHURN_FILE.tmp" "$CHURN_FILE"
    fi

    set_state dirtyFingerprint ""
    write_log INFO "$([ "$can_amend" -eq 1 ] && echo amend || echo commit)" "files=$count" "sha=$AC_SHA"
}

integrate() {
    local branch="$1" upstream="$2" pull_output rc before after
    local merge_tree_base wip_sha has_dirty=0 we_stashed=0 stash_out pop_out
    IG_SUCCESS=1; IG_CONFLICT=0; IG_CONFLICT_FILES=""; IG_PULLED=""

    IG_PULLED="$(git_hardened log --format='%h %s' "HEAD..$upstream")"

    # A debounced (not-yet-committed) local edit is invisible to a plain `HEAD vs
    # upstream` merge-tree check. If we predict "clean" while ignoring it, autostash
    # can silently leave real conflict markers in the working tree even though `git
    # pull` reports success. So: fold any dirty tracked changes into the prediction
    # by using a `stash create` commit (non-destructive) as the merge-tree base.
    sync_status
    [ "$ST_DIRTY_COUNT" -gt 0 ] && has_dirty=1

    merge_tree_base="HEAD"
    if [ "$has_dirty" -eq 1 ]; then
        wip_sha="$(git_hardened stash create 2>/dev/null)"
        case "$wip_sha" in [0-9a-f]*) merge_tree_base="$wip_sha" ;; esac
    fi

    check_merge_tree_conflict "$merge_tree_base" "$upstream"
    if [ "$MT_CONFLICT" -eq 1 ]; then
        IG_SUCCESS=0; IG_CONFLICT=1; IG_CONFLICT_FILES="$MT_FILES"
        write_log ERROR conflict-predicted "files=$(printf '%s' "$MT_FILES" | tr '\n' ',')" "includesDirty=$has_dirty"
        return 1
    fi

    before="$(git_hardened rev-parse HEAD)"

    # Manage the stash ourselves (rather than trusting rebase/merge autostash) so a
    # "successful" pull can never quietly leave a conflicted stash-pop behind.
    if [ "$has_dirty" -eq 1 ]; then
        stash_out="$(git_hardened stash push --quiet -m aura-sync-temp 2>&1)"
        if [ $? -ne 0 ]; then
            IG_SUCCESS=0; IG_CONFLICT=1; IG_CONFLICT_FILES="could not stash local changes -- see git status"
            write_log ERROR integrate-stash-failed "stderr=$(printf '%s' "$stash_out" | tr '\n' ' ')"
            return 1
        fi
        we_stashed=1
    fi

    case "$PULL_STRATEGY" in
        rebase) pull_output="$(git_hardened_timeout 90 -c rebase.autoStash=true pull --rebase=merges --no-tags origin "$branch" 2>&1)"; rc=$? ;;
        merge)  pull_output="$(git_hardened_timeout 90 -c merge.autoStash=true pull --no-rebase --no-edit --no-tags origin "$branch" 2>&1)"; rc=$? ;;
        *)      pull_output="$(git_hardened_timeout 90 pull --ff-only --no-tags origin "$branch" 2>&1)"; rc=$? ;;
    esac

    if [ "$rc" -ne 0 ]; then
        local git_dir; git_dir="$(git_dir_path)"
        if [ "$PULL_STRATEGY" = "rebase" ] && { [ -d "$git_dir/rebase-merge" ] || [ -d "$git_dir/rebase-apply" ]; }; then
            git_hardened rebase --abort >/dev/null 2>&1
        elif [ -f "$git_dir/MERGE_HEAD" ]; then
            git_hardened merge --abort >/dev/null 2>&1
        fi
        if [ "$we_stashed" -eq 1 ]; then
            git_hardened stash pop --quiet >/dev/null 2>&1
            if [ $? -ne 0 ]; then
                write_log CRITICAL stash-restore-failed-after-integrate-abort ""
            fi
        fi
        after="$(git_hardened rev-parse HEAD)"
        if [ "$after" != "$before" ]; then
            write_log CRITICAL integrate-abort-mismatch "before=$before" "after=$after"
        fi
        IG_SUCCESS=0; IG_CONFLICT=1; IG_CONFLICT_FILES="see git status"
        write_log ERROR integrate-failed "stderr=$(printf '%s' "$pull_output" | tr '\n' ' ')"
        return 1
    fi

    if [ "$we_stashed" -eq 1 ]; then
        pop_out="$(git_hardened stash pop --quiet 2>&1)"
        if [ $? -ne 0 ]; then
            # The merge-tree pre-check said this would be clean; if the pop still
            # conflicts (race, rename/mode edge case), stop rather than guess. Stash
            # is deliberately NOT dropped.
            write_log CRITICAL stash-pop-conflict-after-integrate "stderr=$(printf '%s' "$pop_out" | tr '\n' ' ')"
            IG_SUCCESS=0; IG_CONFLICT=1
            IG_CONFLICT_FILES="working tree has conflict markers from stash pop -- see git status; pre-sync stash preserved (git stash list)"
            return 1
        fi
    fi

    # Backstop: confirm the tree actually came out clean.
    sync_status
    if [ "$ST_UNMERGED" -eq 1 ]; then
        write_log CRITICAL unmerged-after-integrate ""
        IG_SUCCESS=0; IG_CONFLICT=1
        IG_CONFLICT_FILES="unexpected unmerged paths after integrate -- see git status"
        return 1
    fi

    write_log INFO pull-ok "commits=$(printf '%s\n' "$IG_PULLED" | grep -c .)" "strategy=$PULL_STRATEGY" "hadDirty=$has_dirty"
    return 0
}

sync_push() {
    local branch="$1" remote_exists="${2:-1}" set_upstream="${3:-0}"
    local ahead diff_out attempt max_attempts out rc
    PUSH_PUSHED=0; PUSH_REASON="not-attempted"; PUSH_CONFLICT_FILES=""; PUSH_AUTH_FAILED=0

    local bu_reason
    bu_reason="$(guard_branch_unchanged "$branch")"
    if [ $? -ne 0 ]; then
        PUSH_REASON="branch-changed-mid-pass"
        return 0
    fi

    if [ "$remote_exists" -eq 1 ]; then
        ahead="$(git_hardened rev-list --count "origin/$branch..HEAD" 2>/dev/null)"
    else
        ahead="$(git_hardened rev-list --count HEAD 2>/dev/null)"
    fi
    ahead="${ahead:-0}"
    if [ "$ahead" -eq 0 ]; then PUSH_REASON="nothing-to-push"; return 0; fi

    if [ -n "${PRE_PUSH_CHECK:-}" ]; then
        if ! ( cd "$REPO_ROOT" && eval "$PRE_PUSH_CHECK" ); then
            PUSH_REASON="held-precheck-failed"
            write_log WARN push-held "reason=precheck"
            return 0
        fi
    fi

    if [ "$remote_exists" -eq 1 ]; then
        if ! conflict_marker_gate "origin/$branch"; then
            PUSH_REASON="held-conflict-markers"
            write_log WARN guard "guard=conflict-marker-gate" "pass=false" "reason=conflict-markers-in-diff"
            return 0
        fi
        write_log DEBUG guard "guard=conflict-marker-gate" "pass=true" "reason=clean"
    else
        write_log DEBUG guard "guard=conflict-marker-gate" "pass=true" "reason=no-remote-branch"
    fi

    attempt=1
    max_attempts=$((1 + ${PUSH_RETRIES:-2}))
    while [ "$attempt" -le "$max_attempts" ]; do
        local push_args=(push --porcelain)
        if [ "$set_upstream" -eq 1 ] && [ "$attempt" -eq 1 ]; then push_args+=(--set-upstream); fi
        push_args+=(origin "HEAD:refs/heads/$branch")
        out="$(git_hardened "${push_args[@]}" 2>&1)"
        rc=$?

        if [ "$rc" -eq 0 ]; then
            PUSH_PUSHED=1; PUSH_REASON="pushed"
            write_log INFO push-ok "attempt=$attempt"
            return 0
        fi

        if printf '%s' "$out" | grep -q '\[rejected\]' && printf '%s' "$out" | grep -Eq '(non-fast-forward|fetch first)'; then
            write_log WARN push-rejected "attempt=$attempt"
            if [ "$attempt" -ge "$max_attempts" ]; then break; fi
            sleep $(( (RANDOM % 4) + 2 ))
            git_hardened fetch --prune --no-tags origin >/dev/null 2>&1
            if ! integrate "$branch" "origin/$branch"; then
                PUSH_REASON="conflict-on-retry"; PUSH_CONFLICT_FILES="$IG_CONFLICT_FILES"
                return 0
            fi
            remote_exists=1
            write_log INFO push-retry "attempt=$attempt"
            attempt=$((attempt+1))
            continue
        fi

        if printf '%s' "$out" | grep -q '\[remote rejected\]'; then
            PUSH_REASON="remote-rejected"
            write_log ERROR push-remote-rejected "stderr=$(printf '%s' "$out" | tr '\n' ' ')"
            return 0
        fi

        if printf '%s' "$out" | grep -Eq 'Authentication failed|could not read Username|terminal prompts disabled|HTTP 403|Permission denied \(publickey\)'; then
            PUSH_REASON="auth-failed"; PUSH_AUTH_FAILED=1
            write_log ERROR auth-failed ""
            return 0
        fi

        PUSH_REASON="push-failed"
        write_log ERROR push-failed "stderr=$(printf '%s' "$out" | tr '\n' ' ')"
        return 0
    done

    PUSH_REASON="push-retries-exhausted"
    return 0
}

write_conflict_report() {
    local branch="$1" upstream="$2" files="$3" head_info upstream_info
    head_info="$(git_hardened log -1 --format='%H %s')"
    upstream_info="$(git_hardened log -1 --format='%H %s' "$upstream")"
    {
        echo "# aura-sync conflict report"
        echo ""
        echo "Detected: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        echo "Branch: $branch"
        echo "Local HEAD:            $head_info"
        echo "Remote ($upstream): $upstream_info"
        echo ""
        echo "## Conflicting files"
        echo ""
        printf '%s\n' "$files" | sed 's/^/- /'
        echo ""
        echo "## What happened"
        echo ""
        echo "aura-sync predicted a merge conflict BEFORE touching your working tree"
        echo "(via git merge-tree). Nothing was merged or rebased -- your files were"
        echo "not modified. Auto-sync is now paused."
        echo ""
        echo "## How to resolve"
        echo ""
        echo "  git pull --no-rebase origin $branch"
        echo "  # fix conflicts in the files listed above"
        echo "  git add <resolved files>"
        echo "  git commit"
        echo "  git push origin $branch"
        echo ""
        echo "  ./scripts/sync/aura-sync.sh --resume"
        echo ""
        echo "## Recovery"
        echo ""
        echo "  git for-each-ref refs/autosync/"
    } > "$CONFLICT_FILE"
}

# ---------------------------------------------------------------------------
# One sync pass
# ---------------------------------------------------------------------------

run_pass() {
    PASS_ID="$(new_pass_id)"
    write_log INFO start ""

    local lock_reason
    lock_reason="$(enter_lock)"
    if [ $? -ne 0 ]; then
        write_log WARN guard "guard=lock" "pass=false" "reason=$lock_reason"
        write_summary skipped "$lock_reason" "" "" "none" ""
        return 2
    fi
    write_log DEBUG guard "guard=lock" "pass=true" "reason=$lock_reason"
    trap 'exit_lock' EXIT INT TERM

    if ! run_simple_guard pause guard_pause; then
        write_summary skipped "$GUARD_REASON" "" "" "none" ""; exit_lock; trap - EXIT INT TERM; return 2
    fi
    if ! run_simple_guard in-progress guard_in_progress; then
        write_summary skipped "$GUARD_REASON" "" "" "none" ""; exit_lock; trap - EXIT INT TERM; return 2
    fi
    if ! run_simple_guard branch guard_branch; then
        write_summary skipped "$GUARD_REASON" "" "" "none" ""; exit_lock; trap - EXIT INT TERM; return 2
    fi
    local branch="$GUARD_REASON"
    PASS_BRANCH="$branch"

    local track_current="${TRACK_CURRENT_MODE:-0}"
    local upstream remote_exists=1 needs_set_upstream=0
    if [ "$track_current" -eq 1 ]; then
        if resolve_upstream "$branch" 1; then
            write_log DEBUG guard "guard=upstream" "pass=true" "reason=$UPSTREAM_REASON"
        else
            write_log WARN guard "guard=upstream" "pass=false" "reason=$UPSTREAM_REASON"
            write_summary error "$UPSTREAM_REASON" "" "" "none" ""
            exit_lock; trap - EXIT INT TERM
            return 4
        fi
        upstream="$UPSTREAM"; remote_exists="$REMOTE_EXISTS"; needs_set_upstream="$NEEDS_SET_UPSTREAM"
    else
        if ! run_simple_guard upstream guard_upstream; then
            write_summary error "$GUARD_REASON" "" "" "none" ""; exit_lock; trap - EXIT INT TERM; return 4
        fi
        upstream="$GUARD_REASON"
    fi
    if ! run_simple_guard identity guard_identity; then
        write_summary error "$GUARD_REASON" "" "" "none" ""; exit_lock; trap - EXIT INT TERM; return 4
    fi
    if ! run_simple_guard backoff guard_backoff; then
        write_summary skipped "$GUARD_REASON" "" "" "none" ""; exit_lock; trap - EXIT INT TERM; return 2
    fi

    local jitter="${JITTER_SECONDS:-20}"
    [ "$jitter" -le 0 ] 2>/dev/null && jitter=1
    sleep $(( RANDOM % jitter ))

    new_snapshot

    local fetch_out fetch_rc
    fetch_out="$(git_hardened_timeout 60 fetch --prune --no-tags origin 2>&1)"; fetch_rc=$?
    if [ "$fetch_rc" -ne 0 ]; then
        if printf '%s' "$fetch_out" | grep -Eq 'Authentication failed|could not read Username|terminal prompts disabled|HTTP 403|Permission denied \(publickey\)'; then
            set_state authBackoffUntilEpoch "$(( $(date -u +%s) + ${AUTH_BACKOFF_MINUTES:-30}*60 ))"
            write_log ERROR auth-failed "phase=fetch"
            write_summary error "auth failed on fetch -- check credentials (gh auth status)" "" "" "none" ""
            exit_lock; trap - EXIT INT TERM
            return 3
        fi
        write_log WARN fetch-failed "reason=network"
        write_summary skipped "offline or fetch failed (network)" "" "" "none" ""
        exit_lock; trap - EXIT INT TERM
        return 0
    fi
    write_log DEBUG fetch-ok ""

    local bu_reason
    bu_reason="$(guard_branch_unchanged "$branch")"
    if [ $? -ne 0 ]; then
        write_log WARN guard "guard=branch-unchanged" "pass=false" "reason=$bu_reason"
        write_summary skipped "$bu_reason" "" "" "none" ""
        exit_lock; trap - EXIT INT TERM
        return 2
    fi
    write_log DEBUG guard "guard=branch-unchanged" "pass=true" "reason=$bu_reason"

    if [ "$track_current" -eq 1 ] && [ "$remote_exists" -eq 0 ]; then
        # Pre-fetch, this clone may not have known origin/<branch> existed yet
        # (e.g. a teammate pushed it moments ago) -- re-resolve now that fetch ran.
        if resolve_upstream "$branch" 1; then
            upstream="$UPSTREAM"; remote_exists="$REMOTE_EXISTS"; needs_set_upstream="$NEEDS_SET_UPSTREAM"
        fi
    fi

    AC_COMMITTED=0; AC_FILES=""; AC_SKIPPED=""; AC_SECRET_BLOCKED=0
    if [ "${AUTO_COMMIT:-true}" = "true" ]; then
        auto_commit "$branch"
        if [ "$AC_SECRET_BLOCKED" -eq 1 ]; then
            set_pause_flag secret
            write_summary error "possible secret staged, auto-sync PAUSED (see log for path/rule -- never the matched text)" "" "" "none" ""
            exit_lock; trap - EXIT INT TERM
            return 3
        fi
    fi

    sync_status
    local behind="$ST_BEHIND"
    if [ "$remote_exists" -eq 1 ] && [ "$needs_set_upstream" -eq 1 ]; then
        # No @{u} configured, so sync_status can't report ahead/behind --
        # compute it directly against the resolved upstream instead.
        behind="$(git_hardened rev-list --count "HEAD..$upstream" 2>/dev/null)"
        behind="${behind:-0}"
    fi

    IG_SUCCESS=1; IG_CONFLICT=0; IG_CONFLICT_FILES=""; IG_PULLED=""
    if [ "$remote_exists" -eq 1 ] && [ "$behind" -gt 0 ]; then
        if ! integrate "$branch" "$upstream"; then
            write_conflict_report "$branch" "$upstream" "$IG_CONFLICT_FILES"
            set_pause_flag conflict
            write_summary conflict "conflict predicted -- see .autosync/CONFLICT.md" "" "$AC_FILES" "none" "$AC_SKIPPED"
            exit_lock; trap - EXIT INT TERM
            return 3
        fi
    fi

    PUSH_PUSHED=0; PUSH_REASON="auto-push-disabled"; PUSH_CONFLICT_FILES=""; PUSH_AUTH_FAILED=0
    if [ "${AUTO_PUSH:-true}" = "true" ]; then
        sync_push "$branch" "$remote_exists" "$needs_set_upstream"
        if [ "$PUSH_AUTH_FAILED" -eq 1 ]; then
            set_state authBackoffUntilEpoch "$(( $(date -u +%s) + ${AUTH_BACKOFF_MINUTES:-30}*60 ))"
        fi
        if [ "$PUSH_REASON" = "conflict-on-retry" ]; then
            local upstream_display="${upstream:-origin/$branch}"
            write_conflict_report "$branch" "$upstream_display" "$PUSH_CONFLICT_FILES"
            set_pause_flag conflict
        fi
    fi

    local push_label="$PUSH_REASON"
    [ "$PUSH_PUSHED" -eq 1 ] && push_label="pushed"
    [ "$PUSH_REASON" = "nothing-to-push" ] && push_label="none"
    [ "$PUSH_REASON" = "auto-push-disabled" ] && push_label="disabled"

    local failed=0
    case "$PUSH_REASON" in push-failed|remote-rejected|conflict-on-retry|auth-failed) failed=1 ;; esac

    local prev_failures cur_failures
    prev_failures="$(get_state consecutiveFailures)"; prev_failures="${prev_failures:-0}"
    if [ "$failed" -eq 1 ]; then
        set_state consecutiveFailures "$((prev_failures+1))"
    else
        set_state consecutiveFailures 0
    fi
    set_state lastRunEpoch "$(date -u +%s)"
    set_state lastResult "$([ "$failed" -eq 1 ] && echo error || echo ok)"

    cur_failures="$(get_state consecutiveFailures)"; cur_failures="${cur_failures:-0}"
    if [ "$cur_failures" -ge "${MAX_CONSECUTIVE_FAILURES:-5}" ]; then
        set_pause_flag too-many-failures
        write_log CRITICAL auto-paused "consecutiveFailures=$cur_failures"
    fi

    prune_snapshots "${SNAPSHOT_KEEP:-50}"
    prune_old_logs "${LOG_RETENTION_DAYS:-14}"

    local overall="ok"
    if [ "$IG_CONFLICT" -eq 1 ] || [ "$PUSH_REASON" = "conflict-on-retry" ]; then overall="conflict"
    elif [ "$failed" -eq 1 ]; then overall="error"
    fi

    write_summary "$overall" "pass complete" "$IG_PULLED" "$AC_FILES" "$push_label" "$AC_SKIPPED"
    write_log INFO finish "result=$overall"

    exit_lock
    trap - EXIT INT TERM

    [ "$overall" = "ok" ] && return 0 || return 3
}

# ---------------------------------------------------------------------------
# Scheduling (cron -- works the same on Linux and macOS). See docs/ops for
# launchd (macOS) / systemd timer (Linux) alternatives if you'd rather not
# use cron.
# ---------------------------------------------------------------------------

cmd_install() {
    local interval_min="${1:-5}" track_flag="${2:-0}"
    local existing new_line extra_args=""
    [ "$track_flag" = "1" ] && extra_args=" --track-current-branch"
    existing="$(crontab -l 2>/dev/null | grep -v -F "$CRON_MARKER")"
    new_line="*/$interval_min * * * * \"$SCRIPT_DIR/aura-sync.sh\" --once$extra_args >/dev/null 2>&1 $CRON_MARKER"
    { [ -n "$existing" ] && printf '%s\n' "$existing"; printf '%s\n' "$new_line"; } | crontab -
    echo "Installed cron entry: every $interval_min min (note: cron intervals must divide 60 evenly -- use 5/10/15/20/30, or --watch for arbitrary intervals)."
    echo "View with: crontab -l | grep aura-sync"
}

cmd_uninstall() {
    local existing
    existing="$(crontab -l 2>/dev/null | grep -v -F "$CRON_MARKER")"
    printf '%s\n' "$existing" | crontab - 2>/dev/null
    echo "Removed aura-sync cron entry (if any)."
}

cmd_status() {
    echo "cron entries:"
    crontab -l 2>/dev/null | grep -F "$CRON_MARKER" || echo "  (none -- run --install to set one up, or --watch for a foreground loop)"
    echo ""
    if [ -f "$LASTRUN_FILE" ]; then cat "$LASTRUN_FILE"; else echo "No sync pass has run yet."; fi
    if [ -f "$PAUSE_FILE" ]; then echo ""; echo "PAUSED:"; cat "$PAUSE_FILE"; fi
}

# ---------------------------------------------------------------------------
# CLI dispatch
# ---------------------------------------------------------------------------

ONCE=0; WATCH=0; DO_PAUSE=0; DO_RESUME=0; DO_STATUS=0; DO_INSTALL=0; DO_UNINSTALL=0
PAUSE_MINUTES=0; PAUSE_REASON="manual"; INSTALL_INTERVAL=5; CLI_TRACK_CURRENT=0

while [ $# -gt 0 ]; do
    case "$1" in
        --once) ONCE=1 ;;
        --watch) WATCH=1 ;;
        --pause) DO_PAUSE=1 ;;
        --minutes) shift; PAUSE_MINUTES="${1:-0}" ;;
        --reason) shift; PAUSE_REASON="${1:-manual}" ;;
        --resume) DO_RESUME=1 ;;
        --status) DO_STATUS=1 ;;
        --install) DO_INSTALL=1 ;;
        --interval-minutes) shift; INSTALL_INTERVAL="${1:-5}" ;;
        --uninstall) DO_UNINSTALL=1 ;;
        --track-current-branch) CLI_TRACK_CURRENT=1 ;;
        -h|--help) grep '^# ' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 4 ;;
    esac
    shift
done

load_config_file "$CONFIG_DEFAULTS"
load_config_file "$CONFIG_LOCAL"
apply_env_overrides

# CLI flag > env var / config file, per the documented precedence -- applied
# after every other config source has loaded so it can't be clobbered by them.
[ "$CLI_TRACK_CURRENT" -eq 1 ] && SYNC_BRANCHES=current

TRACK_CURRENT_MODE=0
case "${SYNC_BRANCHES:-master}" in *current*) TRACK_CURRENT_MODE=1 ;; esac

case "${PULL_STRATEGY:-rebase}" in
    rebase|merge|ff-only) ;;
    *) echo "sync.config: PULL_STRATEGY must be rebase|merge|ff-only, got '$PULL_STRATEGY'" >&2; exit 4 ;;
esac

if [ "$DO_INSTALL" -eq 1 ]; then cmd_install "$INSTALL_INTERVAL" "$CLI_TRACK_CURRENT"; exit 0; fi
if [ "$DO_UNINSTALL" -eq 1 ]; then cmd_uninstall; exit 0; fi
if [ "$DO_STATUS" -eq 1 ]; then cmd_status; exit 0; fi
if [ "$DO_PAUSE" -eq 1 ]; then
    set_pause_flag "$PAUSE_REASON" "$PAUSE_MINUTES"
    if [ "$PAUSE_MINUTES" -gt 0 ] 2>/dev/null; then
        echo "Auto-sync paused ($PAUSE_REASON) -- resumes automatically in $PAUSE_MINUTES min."
    else
        echo "Auto-sync paused ($PAUSE_REASON) -- resume manually with --resume."
    fi
    exit 0
fi
if [ "$DO_RESUME" -eq 1 ]; then rm -f "$PAUSE_FILE"; echo "Auto-sync resumed."; exit 0; fi

if [ "$WATCH" -eq 1 ]; then
    if [ "$TRACK_CURRENT_MODE" -eq 1 ]; then
        echo "aura-sync watching -- interval ${INTERVAL_SECONDS:-300}s, mode=current (tracks whatever branch is checked out). Ctrl+C to stop."
    else
        echo "aura-sync watching -- interval ${INTERVAL_SECONDS:-300}s, branch(es): ${SYNC_BRANCHES:-master}. Ctrl+C to stop."
    fi
    while :; do
        run_pass
        sleep "${INTERVAL_SECONDS:-300}"
    done
    exit 0
fi

run_pass
exit $?
