#!/bin/zsh

set -e

SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"

WEB_HOST="${FUND_FLOW_WEB_HOST:-127.0.0.1}"
WEB_PORT="${FUND_FLOW_WEB_PORT:-3000}"
API_PORT="${FUND_FLOW_API_PORT:-8765}"
WEB_URL="http://${WEB_HOST}:${WEB_PORT}"
API_HEALTH_URL="http://127.0.0.1:${API_PORT}/api/health"
API_PID=""
WEB_PID=""

pause_before_exit() {
  if [[ -t 0 ]]; then
    read -k 1 "?按任意键关闭此窗口…"
    echo
  fi
}

url_is_ready() {
  /usr/bin/curl --silent --fail --connect-timeout 1 --max-time 2 "$1" >/dev/null 2>&1
}

api_is_current() {
  /usr/bin/curl --silent --fail --connect-timeout 1 --max-time 3 "$API_HEALTH_URL" 2>/dev/null | /usr/bin/grep -q '"apiVersion":9'
}

web_is_current() {
  /usr/bin/curl --silent --fail --connect-timeout 1 --max-time 5 "$1/strategy" 2>/dev/null | /usr/bin/grep -q 'data-fund-flow-version="8"'
}

stop_orphaned_web() {
  local listener_pid
  listener_pid=$(/usr/sbin/lsof -tiTCP:"$WEB_PORT" -sTCP:LISTEN 2>/dev/null | /usr/bin/head -n 1)
  if [[ -z "$listener_pid" ]]; then
    return 1
  fi
  if /usr/sbin/lsof -a -p "$listener_pid" -d cwd -Fn 2>/dev/null | /usr/bin/grep -Fxq "n${SCRIPT_DIR}"; then
    echo "发现上次遗留的网页服务，但数据服务未运行，正在一并重启…"
    kill "$listener_pid" 2>/dev/null || true
    sleep 1
    return 0
  fi
  return 1
}

wait_for_url() {
  local url="$1"
  local attempts="$2"
  local count=0

  while (( count < attempts )); do
    if url_is_ready "$url"; then
      return 0
    fi
    sleep 1
    (( count += 1 ))
  done

  return 1
}

cleanup() {
  if [[ -n "$WEB_PID" ]]; then
    kill "$WEB_PID" 2>/dev/null || true
  fi
  if [[ -n "$API_PID" ]]; then
    kill "$API_PID" 2>/dev/null || true
  fi
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 Python 3，请先安装 Python 3。"
  pause_before_exit
  exit 1
fi

if ! command -v node >/dev/null 2>&1 || [[ ! -x "node_modules/.bin/vinext" ]]; then
  echo "网页运行环境尚未安装完整，请在 Codex 中重新完成项目安装。"
  pause_before_exit
  exit 1
fi

echo "资金脉络正在启动…"

if url_is_ready "$WEB_URL" && ! url_is_ready "$API_HEALTH_URL"; then
  stop_orphaned_web || true
fi

if url_is_ready "$API_HEALTH_URL" && ! api_is_current; then
  STALE_API_PID=$(/usr/sbin/lsof -tiTCP:"$API_PORT" -sTCP:LISTEN 2>/dev/null | /usr/bin/head -n 1)
  if [[ -n "$STALE_API_PID" ]] && /usr/sbin/lsof -a -p "$STALE_API_PID" -d cwd -Fn 2>/dev/null | /usr/bin/grep -Fxq "n${SCRIPT_DIR}"; then
    echo "发现同一项目的旧数据服务，正在安全重启…"
    kill "$STALE_API_PID" 2>/dev/null || true
    sleep 1
  else
    echo "端口 ${API_PORT} 被另一个旧服务占用。请关闭旧的资金脉络终端窗口后重试。"
    pause_before_exit
    exit 1
  fi
fi

if api_is_current; then
  echo "数据服务已经在运行，将直接使用。"
else
  python3 local_server.py &
  API_PID=$!

  if ! wait_for_url "$API_HEALTH_URL" 20; then
    echo "数据服务启动失败。请查看上方提示，或关闭其他正在运行的资金脉络窗口后重试。"
    pause_before_exit
    exit 1
  fi
fi

if url_is_ready "$WEB_URL" && ! web_is_current "$WEB_URL"; then
  for CANDIDATE_PORT in {3001..3010}; do
    CANDIDATE_URL="http://${WEB_HOST}:${CANDIDATE_PORT}"
    if ! url_is_ready "$CANDIDATE_URL"; then
      WEB_PORT="$CANDIDATE_PORT"
      WEB_URL="$CANDIDATE_URL"
      echo "端口 3000 上是旧网页，本次改用 ${WEB_URL}。"
      break
    fi
  done
fi

if web_is_current "$WEB_URL"; then
  echo "网页已经在运行，正在打开浏览器：${WEB_URL}"
  if [[ "${FUND_FLOW_NO_OPEN:-0}" != "1" ]]; then
    open "$WEB_URL" >/dev/null 2>&1 || true
  fi

  if [[ -n "$API_PID" ]]; then
    echo "关闭本窗口即可停止本次启动的数据服务。"
    wait "$API_PID"
  else
    echo "网页已打开，可以关闭此窗口。"
    pause_before_exit
  fi
  exit 0
fi

node_modules/.bin/vinext dev --hostname "$WEB_HOST" --port "$WEB_PORT" &
WEB_PID=$!

if ! wait_for_url "$WEB_URL" 60; then
  echo "网页服务未能在一分钟内启动。请查看上方提示后重试。"
  pause_before_exit
  exit 1
fi

if ! web_is_current "$WEB_URL"; then
  echo "网页已启动，但缺少当前版本的个股页面。请重新安装网页依赖后重试。"
  pause_before_exit
  exit 1
fi

echo "网页已准备好，正在打开浏览器：${WEB_URL}"
echo "关闭本窗口即可停止程序。"
if [[ "${FUND_FLOW_NO_OPEN:-0}" != "1" ]]; then
  open "$WEB_URL" >/dev/null 2>&1 || true
fi

wait "$WEB_PID"
