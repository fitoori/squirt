#!/usr/bin/env bash
# Wi‑Fi–gated Unison backup — bounded, self‑healing, explicit error reasons
# - Always emits a final `Result:` line
# - Handles DNS/ping/NFS hiccups and missing optional tools gracefully
# - Cleans AppleDouble (._*) files in local Unison roots before syncing

## REQUIRES UNISON - instructions below ##

'''

# Download the installer
curl -LO https://github.com/fitoori/unison-installer/install_unison_stack.sh

# Make it executable
chmod +x install_unison_stack.sh

# Run as root
sudo ./install_unison_stack.sh

# Skip confirmation and accept defaults
sudo ./install_unison_stack.sh -y

'''

# ---- Environment hardening -----------------------------------------
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin${PATH:+:$PATH}"
set -Eeuo pipefail   # 'E' ensures ERR trap in functions/subshells
IFS=$'\n\t'

### USER SETTINGS ####################################################
WIFI_IF="wlan0"
PROFILE="squirt-sync"
PING_HOST="home.example.com" # This value must be changed prior to running
LOG_FILE="${HOME:-/tmp}/unison_backup.log"

MIN_RSSI_DBM=-75
MAX_LOSS_PCT=20
MAX_LAT_MS=300
######################################################################

# ---- Bounded waits (seconds) ---------------------------------------
readonly PING_COUNT=10
readonly PING_DEADLINE_S=15
readonly UNISON_TIMEOUT_S=600
readonly MOUNT_TIMEOUT_S=8

# ---- Timeout flavor detection --------------------------------------
declare -a TIMEOUT_CMD=()
if timeout --help 2>&1 | grep -q -- '--foreground'; then
  TIMEOUT_CMD=(timeout --foreground)
else
  TIMEOUT_CMD=(timeout)
fi

# ---- Logging -------------------------------------------------------
timestamp(){ date -Is; }
_log_path_init(){
  local d
  d="$(dirname -- "$LOG_FILE")"
  if ! { mkdir -p -- "$d" 2>/dev/null && touch -- "$LOG_FILE" 2>/dev/null; }; then
    LOG_FILE="/tmp/unison_backup.log"
    mkdir -p -- /tmp >/dev/null 2>&1 || true
    touch -- "$LOG_FILE" 2>/dev/null || true
  fi
}
_log_path_init
log(){
  local line; line="$(timestamp) $*"
  printf '%s\n' "$line"
  printf '%s\n' "$line" >>"$LOG_FILE" 2>/dev/null || true
}

# ---- Final result emission (guaranteed) ----------------------------
STATUS="SKIP"; REASON="early-exit"   # explicit default
RSSI_INT=-999; LOSS_INT=100; LAT_INT=9999
EXIT=1; EMITTED=0
emit_result(){ EMITTED=1; log "Result: status=$1 reason=$2 RSSI=$RSSI_INT loss=$LOSS_INT latency=$LAT_INT exit=$3"; }

# ---- Specific failure capture --------------------------------------
on_error(){ local ln="${1:-?}" cmd="${2:-?}"; STATUS="FAIL"; REASON="err-line-${ln}:$(printf '%s' "$cmd" | cut -c1-120)"; emit_result "$STATUS" "$REASON" 98; exit 98; }
trap 'on_error "${LINENO:-?}" "${BASH_COMMAND:-?}"' ERR
trap 'if [[ $EMITTED -eq 0 ]]; then emit_result "${STATUS:-FAIL}" "${REASON:-early-exit}" "${EXIT:-97}"; fi' EXIT
trap 'STATUS="FAIL"; REASON="interrupted"; emit_result "$STATUS" "$REASON" 99; exit 99' SIGINT SIGTERM

# ---- Dependencies (graceful handling) ------------------------------
need_or_reason_exit(){ local cmd="$1" why="$2"; command -v "$cmd" >/dev/null 2>&1 || { STATUS="FAIL"; REASON="$why"; EXIT=90; emit_result "$STATUS" "$REASON" "$EXIT"; exit "$EXIT"; }; }
need_or_reason_exit ping     "missing-ping"
need_or_reason_exit unison   "missing-unison"
need_or_reason_exit awk      "missing-awk"
need_or_reason_exit grep     "missing-grep"
need_or_reason_exit timeout  "missing-timeout"

# ---- Wi‑Fi / RSSI helpers -----------------------------------------
wifi_state(){  # prints: connected|disconnected|unknown
  if ! command -v iw >/dev/null 2>&1; then echo "unknown"; return 0; fi
  local out; out="$(iw dev "$WIFI_IF" link 2>/dev/null || true)"
  if grep -q 'Not connected' <<<"$out"; then echo "disconnected"; else echo "connected"; fi
}
get_rssi(){    # prints e.g., -57 or NA
  if ! command -v iw >/dev/null 2>&1; then echo "NA"; return 0; fi
  iw dev "$WIFI_IF" link 2>/dev/null | awk '/signal:/ {print $2; exit}' || echo "NA"
}

# ---- Ping parser with diagnostics ----------------------------------
# prints: "<loss> <avg-lat> <diag>"  diag ∈ {ok,dns,net-unreach,timeout,noreply,perm}
parse_ping(){
  local raw loss lat diag="ok"
  raw=$(LANG=C "${TIMEOUT_CMD[@]}" "${PING_DEADLINE_S}s" ping -c "$PING_COUNT" -w "$PING_DEADLINE_S" -q "$PING_HOST" 2>&1 || true)
  case "$raw" in
    *"unknown host"*|*"Name or service not known"*|*"Temporary failure in name resolution"*) diag="dns" ;;
    *"Network is unreachable"*) diag="net-unreach" ;;
    *"Operation not permitted"*) diag="perm" ;;
    *) : ;;
  esac
  loss=$(echo "$raw" | awk -F',' '/packet loss/ { gsub(/[^0-9]/,"",$3); print ($3==""?100:$3) }')
  lat=$( echo "$raw" | awk '/^rtt/ { split($0,a,"="); split(a[2],b,"/"); printf("%d\n", b[2]) }')
  [[ -z "$loss" ]] && loss=100
  if [[ -z "$lat" ]]; then
    lat="NA"
    if [[ "$diag" == "ok" && "$loss" -ge 100 ]]; then
      if grep -q "100% packet loss" <<<"$raw"; then diag="timeout"; else diag="noreply"; fi
    fi
  fi
  printf '%s %s %s\n' "$loss" "$lat" "$diag"
}

# ---- NFS mount (bounded, non-fatal) --------------------------------
is_mounted(){
  local dst="$1"
  if grep -qE "[[:space:]]${dst//\//\\/}[[:space:]]" /proc/mounts 2>/dev/null; then
    return 0
  fi
  return 1
}
maybe_mount_nfs(){
  local src="home.bajaj.com:/volume1/Family/sat" dst="/mnt"
  [[ -d "$dst" ]] || { log "MOUNT: skipped dst-missing $dst"; return 0; }
  if is_mounted "$dst"; then log "MOUNT: already $dst"; return 0; fi
  if command -v sudo >/dev/null 2>&1; then
    if "${TIMEOUT_CMD[@]}" "${MOUNT_TIMEOUT_S}s" sudo -n mount -t nfs "$src" "$dst" >/dev/null 2>"/tmp/mount.err.$$"; then
      log "MOUNT: ok $src -> $dst"
    else
      local ec=$? err=""; err="$(tr -d '\n' <"/tmp/mount.err.$$" 2>/dev/null || true)"; rm -f "/tmp/mount.err.$$" || true
      case "$ec" in 124) log "MOUNT: timeout ${MOUNT_TIMEOUT_S}s";; *) log "MOUNT: failed ec=$ec msg=${err:-unknown}";; esac
    fi
  else
    log "MOUNT: skipped (sudo-not-found)"
  fi
}

# ---- Unison profile parsing & AppleDouble cleanup ------------------
_profile_file(){ local p="$HOME/.unison/${PROFILE}.prf"; [[ -r "$p" ]] && { printf '%s\n' "$p"; return 0; }; log "INFO: profile-not-found $p (cleanup skipped)"; return 1; }
_profile_roots_raw(){
  local prf; prf=$(_profile_file) || return 1
  awk '
    BEGIN{ IGNORECASE=1 }
    /^[[:space:]]*#/ {next}
    /^[[:space:]]*root[[:space:]]*=/ {
      line=$0
      sub(/^[[:space:]]*root[[:space:]]*=[[:space:]]*/,"",line)
      sub(/[[:space:]]*#.*$/,"",line)
      gsub(/^[[:space:]]+|[[:space:]]+$/,"",line)
      if (line ~ /^".*"$/) { sub(/^"/,"",line); sub(/"$/,"",line) }
      print line
    }' "$prf"
}
_list_local_roots(){
  local r p
  while IFS= read -r r; do
    p=$r
    case "$p" in
      ssh:*|ssh://*|socket:*|socket://*|rsync:*|rsync://*|ftp://*|http://*|https://*) log "INFO: skip-remote-root $p"; continue ;;
      file://*) p="${p#file://}"; [[ "$p" != /* ]] && p="/${p#*/}" ;;
      "~/"*)    p="$HOME/${p#~/}" ;;
      /*)       : ;;
      *)        log "INFO: skip-non-abs-root $p"; continue ;;
    esac
    printf '%s\n' "$p"
  done < <(_profile_roots_raw || true)
}
_cleanup_appledouble(){
  if ! command -v find >/dev/null 2>&1 || ! command -v rm >/dev/null 2>&1; then
    log "CLEAN: skipped (missing-find-or-rm)"; return 0
  fi
  local roots=() root del total=0
  mapfile -t roots < <(_list_local_roots || true)
  if ((${#roots[@]} == 0)); then log "CLEAN: no-local-roots"; return 0; fi
  for root in "${roots[@]}"; do
    if [[ -d "$root" ]]; then
      del=0
      # NUL-safe traversal; ignore find warnings
      while IFS= read -r -d '' f; do
        if rm -f -- "$f"; then
          del=$((del + 1))            # avoid ((del++)) under set -e
        else
          log "WARN: rm-failed $f"
        fi
      done < <( { find "$root" -type f -name '._*' -print0 2>/dev/null || true; } )
      log "CLEAN: root=$root deleted=$del"
      total=$((total + del))          # avoid bare (( total += del ))
    else
      log "INFO: skip-non-dir-root $root"
    fi
  done
  log "CLEAN: total_deleted=$total"
}

# ---- Main ----------------------------------------------------------
main(){
  log "🔄 Running sync check..."

  # Best-effort mount (never fatal)
  maybe_mount_nfs

  # Telemetry inputs
  local RSSI LOSS LAT DIAG
  RSSI=$(get_rssi)
  IFS=' ' read -r LOSS LAT DIAG <<<"$(parse_ping)"

  # Normalize numbers for arithmetic
  case "$RSSI" in (NA|'') RSSI_INT=-999 ;; (*) RSSI_INT=${RSSI%%.*} ;; esac
  case "$LOSS"  in (''|*[!0-9]*) LOSS_INT=100 ;; (*) LOSS_INT=${LOSS%%.*} ;; esac
  case "$LAT"   in (''|*[!0-9]*) LAT_INT=9999 ;; (*) LAT_INT=${LAT%%.*} ;; esac

  # Prefer specific network reasons before thresholds
  case "${DIAG:-ok}" in
    dns)         STATUS="SKIP"; REASON="dns-failure";     EXIT=1 ;;
    net-unreach) STATUS="SKIP"; REASON="net-unreachable"; EXIT=1 ;;
    timeout)     STATUS="SKIP"; REASON="ping-timeout";    EXIT=1 ;;
    noreply)     STATUS="SKIP"; REASON="ping-no-reply";   EXIT=1 ;;
    perm)        STATUS="SKIP"; REASON="ping-permission"; EXIT=1 ;;
    *)           STATUS="PENDING"; REASON="gated";        EXIT=1 ;;
  esac

  # Wi‑Fi gating (only if we can tell)
  if [[ "$STATUS" == "PENDING" ]]; then
    case "$(wifi_state)" in
      disconnected) STATUS="SKIP"; REASON="wifi-off"; EXIT=1 ;;
      *) : ;;
    esac
  fi

  # Threshold gating
  if [[ "$STATUS" == "PENDING" ]]; then
    if (( RSSI_INT < MIN_RSSI_DBM )); then STATUS="SKIP"; REASON="weak";    EXIT=1
    elif (( LOSS_INT > MAX_LOSS_PCT )); then STATUS="SKIP"; REASON="loss";  EXIT=1
    elif (( LAT_INT  > MAX_LAT_MS  )); then STATUS="SKIP"; REASON="latency";EXIT=1
    else
      STATUS="RUN"; REASON="eligible"; EXIT=0
    fi
  fi

  # Perform sync if eligible
  if [[ "$STATUS" == "RUN" ]]; then
    _cleanup_appledouble
    if "${TIMEOUT_CMD[@]}" "${UNISON_TIMEOUT_S}s" \
         unison "$PROFILE" -batch -auto -logfile /dev/stdout >/dev/null 2>&1; then
      STATUS="OK";   REASON="sync";    EXIT=0
    else
      rc=$?
      if [[ $rc -eq 124 || $rc -eq 137 ]]; then
        STATUS="FAIL"; REASON="timeout"; EXIT=3
      else
        STATUS="FAIL"; REASON="unison";  EXIT=2
      fi
    fi
  fi

  emit_result "$STATUS" "$REASON" "$EXIT"
  exit "$EXIT"
}
main "$@"
