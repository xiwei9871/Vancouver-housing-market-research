#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/Users/xiwei/northshore_property_watchdog}"
LOG_DIR="$PROJECT_DIR/outputs/logs"
REPORT_PATH="$PROJECT_DIR/outputs/real_weekly_report.md"
RENTAL_REPORT_PATH="$PROJECT_DIR/outputs/rental_weekly_report.zh.md"
STATUS_DIR="$PROJECT_DIR/outputs/status"
export PATH="/Users/xiwei/.local/node/bin:/Users/xiwei/.local/npm/bin:/usr/bin:/bin:/usr/sbin:/sbin"
OPENCLAW_BIN="${OPENCLAW_BIN:-/Users/xiwei/.local/npm/bin/openclaw}"
WATCHDOG_BIN="${WATCHDOG_BIN:-$PROJECT_DIR/.venv/bin/northshore-watchdog}"
FEISHU_TARGET="${FEISHU_TARGET:-chat:oc_82dd978138a0cde5864868c5b5b8e754}"
FEISHU_ACCOUNT="${FEISHU_ACCOUNT:-jarvis}"
SEND_FEISHU="${SEND_FEISHU:-1}"
STAMP="$(date '+%Y%m%d_%H%M%S')"
LOG_PATH="$LOG_DIR/weekly_watchdog_$STAMP.log"
ACQUISITION_PROMPT="${ACQUISITION_PROMPT:-$PROJECT_DIR/config/weekly_auto_acquisition_prompt.md}"
ACQUISITION_MANIFEST="${ACQUISITION_MANIFEST:-$PROJECT_DIR/outputs/acquisition/latest.json}"
ACQUISITION_STATUS_PATH="${ACQUISITION_STATUS_PATH:-$STATUS_DIR/acquisition_$STAMP.json}"
AUTO_SEARCH_TIMEOUT_SECONDS="${AUTO_SEARCH_TIMEOUT_SECONDS:-1800}"
PURCHASE_STATUS_PATH="$STATUS_DIR/purchase_$STAMP.json"
RENTAL_STATUS_PATH="$STATUS_DIR/rental_$STAMP.json"

mkdir -p "$LOG_DIR" "$STATUS_DIR"
cd "$PROJECT_DIR"

run_and_log() {
  "$@" 2>&1 | tee -a "$LOG_PATH"
  return "${PIPESTATUS[0]}"
}

{
  echo "started_at=$(date '+%Y-%m-%dT%H:%M:%S%z')"
  echo "project_dir=$PROJECT_DIR"
  echo "acquisition_manifest=$ACQUISITION_MANIFEST"
  echo "acquisition_status_path=$ACQUISITION_STATUS_PATH"
  echo "purchase_status_path=$PURCHASE_STATUS_PATH"
} | tee "$LOG_PATH"

set +e
ACQUISITION_STARTED_AT="$(date '+%Y-%m-%dT%H:%M:%S%z')"
echo "acquisition_started_at=$ACQUISITION_STARTED_AT" | tee -a "$LOG_PATH"
run_and_log "$OPENCLAW_BIN" agent \
  --agent agent_jarvis \
  --session-key "property-watchdog-acquisition-$STAMP" \
  --message-file "$ACQUISITION_PROMPT" \
  --timeout "$AUTO_SEARCH_TIMEOUT_SECONDS" \
  --json
acquisition_exit_code=$?
echo "acquisition_exit_code=$acquisition_exit_code" | tee -a "$LOG_PATH"

if [[ "$acquisition_exit_code" -eq 0 ]]; then
  run_and_log "$WATCHDOG_BIN" validate-acquisition \
    --manifest "$ACQUISITION_MANIFEST" \
    --purchase-config "$PROJECT_DIR/config/real.yaml" \
    --rental-config "$PROJECT_DIR/config/rentals.yaml" \
    --min-observed-at "$ACQUISITION_STARTED_AT" \
    --status-path "$ACQUISITION_STATUS_PATH"
  acquisition_validate_exit_code=$?
else
  acquisition_validate_exit_code=1
  mkdir -p "$(dirname "$ACQUISITION_STATUS_PATH")"
  jq -n \
    --arg reason "weekly acquisition agent failed with exit code $acquisition_exit_code" \
    '{status:"error", entries_validated:0, reasons:[$reason]}' \
    > "$ACQUISITION_STATUS_PATH"
  echo "acquisition_validation_skipped=1" | tee -a "$LOG_PATH"
fi
echo "acquisition_validate_exit_code=$acquisition_validate_exit_code" | tee -a "$LOG_PATH"

if [[ "$acquisition_exit_code" -eq 0 && "$acquisition_validate_exit_code" -eq 0 ]]; then
  run_and_log "$WATCHDOG_BIN" run-weekly \
    --config "$PROJECT_DIR/config/real.yaml" \
    --status-path "$PURCHASE_STATUS_PATH"
  purchase_exit_code=$?
  echo "purchase_exit_code=$purchase_exit_code" | tee -a "$LOG_PATH"
  echo "rental_status_path=$RENTAL_STATUS_PATH" | tee -a "$LOG_PATH"
  run_and_log "$WATCHDOG_BIN" rentals-run-weekly \
    --config "$PROJECT_DIR/config/rentals.yaml" \
    --status-path "$RENTAL_STATUS_PATH"
  rental_exit_code=$?
  echo "rental_exit_code=$rental_exit_code" | tee -a "$LOG_PATH"
else
  purchase_exit_code=1
  rental_exit_code=1
  echo "purchase/rental scoring skipped because acquisition was not fresh" | tee -a "$LOG_PATH"
fi
{
  echo "report_path=$REPORT_PATH"
  echo "rental_report_path=$RENTAL_REPORT_PATH"
  echo "finished_at=$(date '+%Y-%m-%dT%H:%M:%S%z')"
} | tee -a "$LOG_PATH"
set -e

status_value() {
  local path="$1"
  if [[ ! -s "$path" ]]; then
    echo "error"
    return
  fi
  jq -r 'if .status == "fresh" or .status == "stale" or .status == "error" then .status else "error" end' "$path" 2>/dev/null || echo "error"
}

status_detail() {
  local label="$1"
  local path="$2"
  if [[ ! -s "$path" ]]; then
    echo "$label: status=error; status file missing or empty"
    return
  fi
  jq -r --arg label "$label" '"\($label): status=\(.status // "unknown"); newest=\(.newest.path // "none"); age=\((.newest.age_days // "unknown") | tostring) days; stale_directories=\((.stale_directories // []) | join(", ")); reasons=\((.reasons // []) | join("; "))"' "$path" 2>/dev/null \
    || echo "$label: status=error; status file malformed"
}

acquisition_detail() {
  if [[ ! -s "$ACQUISITION_STATUS_PATH" ]]; then
    echo "acquisition: status=error; status file missing or empty"
    return
  fi
  jq -r '"acquisition: status=\(.status // "unknown"); entries=\(.entries_validated // 0); reasons=\((.reasons // []) | join("; "))"' "$ACQUISITION_STATUS_PATH" 2>/dev/null \
    || echo "acquisition: status=error; status file malformed"
}

delta_detail() {
  local label="$1"
  local path="$2"
  if [[ ! -s "$path" ]]; then
    echo "$label: delta unavailable"
    return
  fi
  jq -r --arg label "$label" '"\($label): baseline=\(.delta.baseline // false), new=\(.delta.new_count // 0), price_reduced=\(.delta.price_reduced_count // 0), price_increased=\(.delta.price_increased_count // 0), not_seen=\(.delta.not_seen_count // 0)"' "$path" 2>/dev/null \
    || echo "$label: delta unavailable"
}

acquisition_status="$(status_value "$ACQUISITION_STATUS_PATH")"
purchase_status="$(status_value "$PURCHASE_STATUS_PATH")"
rental_status="$(status_value "$RENTAL_STATUS_PATH")"
echo "acquisition_status=$acquisition_status" | tee -a "$LOG_PATH"
echo "purchase_status=$purchase_status" | tee -a "$LOG_PATH"
echo "rental_status=$rental_status" | tee -a "$LOG_PATH"

if [[ "$acquisition_exit_code" -ne 0 || "$acquisition_validate_exit_code" -ne 0 || "$acquisition_status" != "fresh" || "$purchase_exit_code" -ne 0 || "$rental_exit_code" -ne 0 || "$purchase_status" == "error" || "$rental_status" == "error" ]]; then
  run_mode="error"
elif [[ "$purchase_status" == "stale" || "$rental_status" == "stale" ]]; then
  run_mode="stale"
else
  run_mode="fresh"
fi
echo "delivery_mode=$run_mode" | tee -a "$LOG_PATH"

if [[ "$SEND_FEISHU" == "1" ]]; then
  if [[ "$run_mode" == "fresh" ]]; then
    summary_lines="$(grep -E '^- (Total listings|A/B/Review/C/Excluded|West Vancouver candidates|North Vancouver candidates|New listings|Price reductions|Price increases|Not seen in current run|Urgent alerts):' "$REPORT_PATH" || true)"
  review_candidates="$(awk '
    /^## Review Candidates/ { in_section=1; next }
    /^## / && in_section { exit }
    in_section && /^### / { sub(/^### /, "- "); print }
  ' "$REPORT_PATH" | head -5)"
  if [[ -z "$review_candidates" ]]; then
    review_candidates="- none"
  fi
  rental_summary_lines="$(grep -E '^- (总房源数|A/B/C/Excluded|Richmond / Vancouver / Burnaby|紧急提醒):' "$RENTAL_REPORT_PATH" || true)"
  rental_a_candidates="$(awk '
    /^## A类候选/ { in_section=1; next }
    /^## / && in_section { exit }
    in_section && /^### / { sub(/^### /, "- "); print }
  ' "$RENTAL_REPORT_PATH" | head -5)"
  if [[ -z "$rental_a_candidates" ]]; then
    rental_a_candidates="- none"
  fi
    message="$(cat <<EOF
North Shore Property Watchdog 周报

$(acquisition_detail)

$summary_lines

$(status_detail "purchase" "$PURCHASE_STATUS_PATH")
$(delta_detail "purchase" "$PURCHASE_STATUS_PATH")

Top Review 候选:
$review_candidates

租房雷达:
$rental_summary_lines

$(status_detail "rental" "$RENTAL_STATUS_PATH")
$(delta_detail "rental" "$RENTAL_STATUS_PATH")

租房 A 类候选:
$rental_a_candidates

买房报告: $REPORT_PATH
租房报告: $RENTAL_REPORT_PATH
日志: $LOG_PATH
EOF
)"
  elif [[ "$run_mode" == "stale" ]]; then
    message="$(cat <<EOF
North Shore Property Watchdog 数据过期告警

本周未发送正常候选周报，也未用过期数据覆盖上次正常报告。

$(acquisition_detail)

$(status_detail "purchase" "$PURCHASE_STATUS_PATH")
$(status_detail "rental" "$RENTAL_STATUS_PATH")

请更新授权快照或 saved-search 输入后重新运行。
日志: $LOG_PATH
EOF
)"
  else
    message="$(cat <<EOF
North Shore Property Watchdog 执行错误告警

本周未发送正常候选周报；自动搜索采集、采集校验或至少一个雷达执行失败。

$(acquisition_detail)

$(status_detail "purchase" "$PURCHASE_STATUS_PATH")
$(status_detail "rental" "$RENTAL_STATUS_PATH")

请检查日志和状态文件。
日志: $LOG_PATH
EOF
)"
  fi
  "$OPENCLAW_BIN" message send \
    --channel feishu \
    --account "$FEISHU_ACCOUNT" \
    --target "$FEISHU_TARGET" \
    --message "$message"
else
  echo "notification=disabled" | tee -a "$LOG_PATH"
fi

echo "log_path=$LOG_PATH" | tee -a "$LOG_PATH"
