#!/usr/bin/env bash
# Unison / SQUIRT status dashboard — login-safe, TTY-aware, color-sensitive

# ── Exit quietly if not an interactive TTY (e.g., scp, cron, pipe) ──
if [[ ! -t 1 ]]; then
  # If sourced, return; if executed, exit.
  if [[ "${BASH_SOURCE[0]-}" != "$0" ]]; then return 0; else exit 0; fi
fi

# Harden PATH so tools in sbin are found even in minimal environments
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin${PATH:+:$PATH}"

# Fail on unset vars & pipeline errors; we will guard risky calls explicitly
set -u
set -o pipefail
IFS=$'\n\t'

# ─── Config ─────────────────────────────────────────────────────────
LOG_FILE="${HOME}/unison_backup.log"
WIFI_IF="wlan0"
# ────────────────────────────────────────────────────────────────────

# ─── Color setup (disable automatically on unsupported terminals) ───
_color_init() {
  local ncolors=0
  if command -v tput >/dev/null 2>&1; then
    ncolors=$(tput colors 2>/dev/null || echo 0)
  fi
  if [[ -t 1 && "${TERM:-dumb}" != "dumb" && "$ncolors" -ge 8 ]]; then
    BOLD=$'\033[1m'; RESET=$'\033[0m'
    WHITE=$'\033[97m'; RED=$'\033[91m'; YELLOW=$'\033[93m'
    ORANGE=$'\033[38;5;208m'; GREEN=$'\033[32m'; BLUE=$'\033[94m'; CYAN=$'\033[36m'
  else
    BOLD='' ; RESET='' ; WHITE='' ; RED='' ; YELLOW=''
    ORANGE='' ; GREEN='' ; BLUE='' ; CYAN=''
  fi
}
_color_init
# ────────────────────────────────────────────────────────────────────

# ─── Clear only when it won't annoy (TTY, not dumb, not tmux/screen) ─
_should_clear() {
  [[ -t 1 ]] && [[ "${TERM:-dumb}" != "dumb" ]] && [[ -z "${TMUX-}" ]] && [[ -z "${STY-}" ]]
}
if _should_clear; then clear; fi

# ─── Header ─────────────────────────────────────────────────────────
printf '%bS%bQ%bU%bI%bR%bT%b\n' "${BOLD}${WHITE}" "${RED}" "${YELLOW}" "${ORANGE}" "${GREEN}" "${BLUE}" "${RESET}"
printf '%bSpectra%b-%bQualified %bUncomplicated %bInky %bRendering %bTools%b\n\n' \
  "${WHITE}" "${RESET}" "${RED}" "${YELLOW}" "${ORANGE}" "${GREEN}" "${BLUE}" "${RESET}"

# ─── Helpers ────────────────────────────────────────────────────────
_print_kv() { printf '%s: %s\n' "$1" "$2"; }

_get_uptime() {
  if command -v uptime >/dev/null 2>&1; then uptime -p 2>/dev/null || echo "N/A"; else echo "N/A"; fi
}

_get_disk() {
  if command -v df >/dev/null 2>&1 && command -v awk >/dev/null 2>&1; then
    df -h ~ 2>/dev/null | awk 'NR==2{print $4 " free in " $6 " (" $1 ")"}' || echo "N/A"
  else
    echo "N/A"
  fi
}

_get_mem() {
  # Prefer "available" if present; fall back to "free"
  if command -v free >/dev/null 2>&1 && command -v awk >/dev/null 2>&1; then
    free -h 2>/dev/null | awk '/^Mem:/ {
      avail = ($7 != "") ? $7 : $4;
      total = $2;
      print avail " available of " total
    }' || echo "N/A"
  else
    echo "N/A"
  fi
}

_get_cpu_load_pct() {
  # CPU load % = (1‑min load avg / #cores) × 100
  local load1 cores
  if [[ -r /proc/loadavg ]] && command -v awk >/dev/null 2>&1 && command -v getconf >/dev/null 2>&1; then
    read -r load1 _ < /proc/loadavg || load1=""
    cores=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo "")
    if [[ -n "${load1}" && -n "${cores}" && "${cores}" -gt 0 ]] 2>/dev/null; then
      awk -v l="$load1" -v c="$cores" 'BEGIN{printf "%.1f",(l/c)*100}'
      return 0
    fi
  fi
  echo "N/A"
}

_get_cpu_temp_c() {
  local p="/sys/class/thermal/thermal_zone0/temp"
  if [[ -r "$p" ]] && command -v awk >/dev/null 2>&1; then
    awk '{printf "%.1f", $1/1000}' "$p" 2>/dev/null || echo "N/A"
  else
    echo "N/A"
  fi
}

_get_wifi_rssi() {
  if command -v iw >/dev/null 2>&1 && iw dev "$WIFI_IF" link 2>/dev/null | grep -q 'signal:'; then
    iw dev "$WIFI_IF" link 2>/dev/null | awk '/signal:/ {print $2 " dBm"}' || echo "N/A"
  else
    echo "N/A"
  fi
}

# Parse both formats:
# 1) ".... Result: status=OK reason=sync RSSI=-55 loss=0 latency=7 exit=0"
# 2) ".... {"status":"OK","reason":"sync","rssi":-55,"loss":0,"lat":7,"exit":0}"
_parse_log() {
  local buf last_any last_ok ts_any ts_ok status reason rssi loss lat
  if [[ ! -r "$LOG_FILE" ]]; then
    printf 'N/A|N/A|N/A|N/A|N/A|N/A|N/A\n'
    return 0
  fi

  # Read only the tail to keep log parsing snappy on large files
  buf=$(tail -n 2000 "$LOG_FILE" 2>/dev/null || echo "")

  # Last successful
  last_ok=$(printf '%s\n' "$buf" | grep -E 'status=OK|\"status\":\"OK\"' | tail -n1 || true)
  ts_ok=${last_ok%% *}; [[ -z "$ts_ok" ]] && ts_ok="N/A"

  # Last attempt
  last_any=$(printf '%s\n' "$buf" | grep -E 'Result:|\"status\":' | tail -n1 || true)
  if [[ -z "$last_any" ]]; then
    printf '%s|%s|%s|%s|%s|%s|%s\n' "$ts_ok" "N/A" "" "N/A" "N/A" "N/A" "N/A"
    return 0
  fi

  ts_any=${last_any%% *}

  if [[ "$last_any" == *"Result:"* ]]; then
    status=$(grep -o 'status=[^ ]*'  <<<"$last_any" | cut -d= -f2)
    reason=$(grep -o 'reason=[^ ]*'  <<<"$last_any" | cut -d= -f2)
    rssi=$(  grep -o 'RSSI=[^ ]*'    <<<"$last_any" | cut -d= -f2)
    loss=$(  grep -o 'loss=[^ ]*'    <<<"$last_any" | cut -d= -f2)
    lat=$(   grep -o 'latency=[^ ]*' <<<"$last_any" | cut -d= -f2)
  else
    # JSON line
    status=$(sed -n 's/.*"status":"\([^"]*\)".*/\1/p' <<<"$last_any")
    reason=$(sed -n 's/.*"reason":"\([^"]*\)".*/\1/p' <<<"$last_any")
    rssi=$(  sed -n 's/.*"rssi":\(-\{0,1\}[0-9]\+\).*/\1/p' <<<"$last_any")
    loss=$(  sed -n 's/.*"loss":\([0-9]\+\).*/\1/p' <<<"$last_any")
    lat=$(   sed -n 's/.*"lat":\([0-9]\+\).*/\1/p'  <<<"$last_any")
  fi

  [[ -z "$status" ]] && status="N/A"
  [[ -z "$reason" ]] && reason=""
  [[ -z "$rssi"   ]] && rssi="N/A"
  [[ -z "$loss"   ]] && loss="N/A"
  [[ -z "$lat"    ]] && lat="N/A"

  printf '%s|%s|%s|%s|%s|%s|%s\n' "$ts_ok" "$ts_any" "$status" "$reason" "$rssi" "$loss" "$lat"
}

_reason_pretty() {
  case "${1:-}" in
    sync)     printf '%bSync OK%b'       "$GREEN" "$RESET" ;;
    wifi-off) printf '%bWi-Fi unavailable%b' "$YELLOW" "$RESET" ;;
    weak)     printf '%bWi-Fi too weak%b'    "$YELLOW" "$RESET" ;;
    loss)     printf '%bNetwork loss too high%b' "$YELLOW" "$RESET" ;;
    latency)  printf '%bNetwork latency too high%b' "$YELLOW" "$RESET" ;;
    unison)   printf '%bUnison error%b'   "$RED" "$RESET" ;;
    OK)       printf '%bSync OK%b'        "$GREEN" "$RESET" ;;
    *)        printf '%bUnknown%b'        "$RED" "$RESET" ;;
  esac
}

_status_col() {
  if [[ "${1:-}" == "OK" ]]; then printf '%bOK%b' "$GREEN" "$RESET"
  elif [[ "${1:-}" == "N/A" ]]; then printf 'N/A'
  else printf '%b%s%b' "$RED" "${1:-}" "$RESET"; fi
}

# ─── System-health block ────────────────────────────────────────────
printf '%b%s%b\n' "${BOLD}${CYAN}" "System Health" "${RESET}"
_print_kv "Uptime"    "$(_get_uptime)"
_print_kv "Free disk" "$(_get_disk)"
_print_kv "Free RAM"  "$(_get_mem)"
_print_kv "CPU load"  "$(_get_cpu_load_pct)%"
_print_kv "CPU temp"  "$(_get_cpu_temp_c)°C"
_print_kv "Wi‑Fi RSSI ($WIFI_IF)" "$(_get_wifi_rssi)"
printf '\n'

# ─── Parse log & display ────────────────────────────────────────────
IFS='|' read -r TS_OK TS_ANY ST_REASON STATUS_REASON RSSI_VAL LOSS_VAL LAT_VAL < <(_parse_log)

printf '%b========= Unison Backup Report =========%b\n\n' "${BOLD}${CYAN}" "${RESET}"

printf '%bLast Successful Backup%b\n' "${BOLD}${CYAN}" "${RESET}"
if [[ "${TS_OK}" != "N/A" ]]; then
  printf '  %b%s%b\n\n' "$GREEN" "$TS_OK" "$RESET"
else
  printf '  %bNo successful backup found.%b\n\n' "$RED" "$RESET"
fi

printf '%bLast Backup Attempt%b\n' "${BOLD}${CYAN}" "${RESET}"
printf '  Time:    %s\n' "${TS_ANY:-N/A}"
printf '  Status:  %s\n' "$(_status_col "${ST_REASON:-N/A}")"
if [[ -n "${STATUS_REASON:-}" && "${ST_REASON:-}" != "OK" && "${ST_REASON:-}" != "N/A" ]]; then
  printf '  Reason:  %s\n' "$(_reason_pretty "${STATUS_REASON}")"
fi

# Units only when numeric
if [[ "${RSSI_VAL}" =~ ^-?[0-9]+$ ]]; then
  printf '  RSSI:    %s dBm\n' "${RSSI_VAL}"
else
  printf '  RSSI:    %s\n' "${RSSI_VAL:-N/A}"
fi
if [[ "${LOSS_VAL}" =~ ^[0-9]+$ ]]; then
  printf '  Loss:    %s %%\n' "${LOSS_VAL}"
else
  printf '  Loss:    %s\n' "${LOSS_VAL:-N/A}"
fi
if [[ "${LAT_VAL}" =~ ^-?[0-9]+$ ]]; then
  printf '  Latency: %s ms\n' "${LAT_VAL}"
else
  printf '  Latency: %s\n' "${LAT_VAL:-N/A}"
fi

printf '\n%b========================================%b\n' "${CYAN}" "${RESET}"
