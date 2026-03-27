#!/usr/bin/env bash
# =============================================================================
# scripts/local/check_environment.sh
# 역할: AppAgent 실험 실행 전 로컬 환경 전체 검증
#
# 사용법:
#   SERVER_IP="10.7.60.145" bash scripts/local/check_environment.sh
#
# 확인 항목:
#   [1/3] ADB 기기 연결 (에뮬레이터 또는 실제 기기)
#   [2/3] 원격 vLLM 서버 응답 (health + models 엔드포인트)
#   [3/3] Python 패키지 설치 확인 (appagent-env)
# =============================================================================
set -euo pipefail

SERVER_IP="${SERVER_IP:-}"
VLLM_PORT="${VLLM_PORT:-8080}"
APPAGENT_VENV="${APPAGENT_VENV:-${HOME}/appagent-env}"
ANDROID_HOME="${ANDROID_HOME:-${HOME}/.android/sdk}"

# 컬러 출력
green() { printf "\033[32m%s\033[0m\n" "$*"; }
red()   { printf "\033[31m%s\033[0m\n" "$*"; }
yellow(){ printf "\033[33m%s\033[0m\n" "$*"; }

PASS=0
FAIL=0

check() {
    local label="$1"; local result="$2"; local detail="${3:-}"
    if [[ "${result}" == "ok" ]]; then
        green "  ✅ ${label}"
        PASS=$((PASS + 1))
    else
        red   "  ❌ ${label}"
        [[ -n "${detail}" ]] && printf "     → %s\n" "${detail}"
        FAIL=$((FAIL + 1))
    fi
}

echo "================================================================"
echo " 환경 검증 시작"
echo "================================================================"
echo "  SERVER_IP  : ${SERVER_IP:-<미설정>}"
echo "  VLLM_PORT  : ${VLLM_PORT}"
echo "  VENV       : ${APPAGENT_VENV}"
echo ""

# ─── [1/3] ADB 기기 연결 ────────────────────────────────────────────────────
echo "================================================================"
echo " [1/3] ADB 기기 연결 확인"
echo "================================================================"

if command -v adb &>/dev/null; then
    ADB="adb"
elif [[ -x "${ANDROID_HOME}/platform-tools/adb" ]]; then
    ADB="${ANDROID_HOME}/platform-tools/adb"
else
    check "adb 명령어" "fail" "adb를 찾을 수 없습니다. Android SDK 설치 또는 PATH 확인"
    ADB=""
fi

if [[ -n "${ADB}" ]]; then
    DEVICES=$(${ADB} devices 2>/dev/null | tail -n +2 | grep -E "\s+device$" || true)
    if [[ -n "${DEVICES}" ]]; then
        check "ADB 기기 연결" "ok"
        echo "${DEVICES}" | while read -r line; do
            printf "     → %s\n" "${line}"
        done
        # 첫 번째 기기에서 부팅 완료 여부 확인
        DEVICE_ID=$(echo "${DEVICES}" | head -1 | awk '{print $1}')
        BOOT=$(${ADB} -s "${DEVICE_ID}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)
        if [[ "${BOOT}" == "1" ]]; then
            check "에뮬레이터 부팅 완료" "ok"
        else
            check "에뮬레이터 부팅 완료" "fail" "sys.boot_completed != 1 (부팅 중이거나 ADB 미연결)"
        fi
    else
        check "ADB 기기 연결" "fail" "연결된 기기가 없습니다. start_emulator.sh 실행 또는 기기 연결 확인"
    fi
fi

# ─── [2/3] 원격 vLLM 서버 ───────────────────────────────────────────────────
echo ""
echo "================================================================"
echo " [2/3] 원격 vLLM 서버 응답 확인"
echo "================================================================"

if [[ -z "${SERVER_IP}" ]]; then
    check "SERVER_IP 설정" "fail" "SERVER_IP 환경변수가 비어있습니다. 예: SERVER_IP=10.x.x.x bash $0"
else
    check "SERVER_IP 설정" "ok"

    VLLM_BASE="http://${SERVER_IP}:${VLLM_PORT}"

    # health check
    HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
        --connect-timeout 5 "${VLLM_BASE}/health" 2>/dev/null || echo "000")
    if [[ "${HTTP_STATUS}" == "200" ]]; then
        check "vLLM /health (${VLLM_BASE})" "ok"
    else
        check "vLLM /health (${VLLM_BASE})" "fail" "HTTP ${HTTP_STATUS} — 서버가 실행 중인지 확인: bash 05_setup_vllm.sh"
    fi

    # models check
    MODELS_RESP=$(curl -s --connect-timeout 5 "${VLLM_BASE}/v1/models" 2>/dev/null || true)
    MODEL_ID=$(echo "${MODELS_RESP}" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d['data'][0]['id'])
except:
    print('')
" 2>/dev/null || true)
    if [[ -n "${MODEL_ID}" ]]; then
        check "vLLM 모델 서빙 확인" "ok"
        printf "     → 서빙 중: %s\n" "${MODEL_ID}"
    else
        check "vLLM 모델 서빙 확인" "fail" "/v1/models 응답 없음 — 모델 로딩 완료까지 기다리세요"
    fi
fi

# ─── [3/3] Python 패키지 ────────────────────────────────────────────────────
echo ""
echo "================================================================"
echo " [3/3] Python 패키지 확인 (${APPAGENT_VENV})"
echo "================================================================"

if [[ ! -d "${APPAGENT_VENV}" ]]; then
    check "appagent-env 존재" "fail" "venv를 찾을 수 없습니다. SERVER_IP=<IP> bash 07_local_setup.sh 실행 필요"
else
    check "appagent-env 존재" "ok"
    PYTHON="${APPAGENT_VENV}/bin/python"

    REQUIRED_PKGS=("openai" "PIL" "yaml" "colorama" "requests" "lxml")
    for pkg in "${REQUIRED_PKGS[@]}"; do
        IMPORT_NAME="${pkg}"
        [[ "${pkg}" == "PIL" ]] && IMPORT_NAME="PIL"
        [[ "${pkg}" == "yaml" ]] && IMPORT_NAME="yaml"
        if "${PYTHON}" -c "import ${IMPORT_NAME}" 2>/dev/null; then
            check "  패키지: ${pkg}" "ok"
        else
            check "  패키지: ${pkg}" "fail" "pip install ${pkg}"
        fi
    done

    # causal_wrapper 확인
    APPAGENT_DIR="${HOME}/AppAgent"
    if [[ -f "${APPAGENT_DIR}/scripts/causal_wrapper.py" ]]; then
        check "  causal_wrapper.py 설치됨" "ok"
    else
        check "  causal_wrapper.py 설치됨" "fail" "SERVER_IP=<IP> bash 07_local_setup.sh 재실행 필요"
    fi

    # task_executor 패치 확인
    if [[ -f "${APPAGENT_DIR}/scripts/task_executor.py" ]]; then
        if grep -q "CAUSAL_PATCH" "${APPAGENT_DIR}/scripts/task_executor.py" 2>/dev/null; then
            check "  task_executor.py 패치 적용됨" "ok"
        else
            check "  task_executor.py 패치 적용됨" "fail" "SERVER_IP=<IP> bash 07_local_setup.sh 재실행 필요"
        fi
    else
        check "  task_executor.py 존재" "fail" "${APPAGENT_DIR}/scripts/task_executor.py 없음"
    fi
fi

# ─── 결과 요약 ───────────────────────────────────────────────────────────────
echo ""
echo "================================================================"
TOTAL=$((PASS + FAIL))
if [[ ${FAIL} -eq 0 ]]; then
    green " ✅ 모든 검증 통과 (${PASS}/${TOTAL})"
    echo ""
    echo "  다음 단계 실행 가능:"
    echo "    source ~/AppAgent/.env_appagent"
    echo "    source ~/appagent-env/bin/activate"
    echo "    cd ~/AppAgent"
    echo "    printf 'y\\n태스크 설명\\n' | python run.py --app com.android.settings"
else
    red " ❌ ${FAIL}개 항목 실패 (${PASS}/${TOTAL} 통과)"
    echo ""
    echo "  위의 ❌ 항목을 해결한 후 다시 실행하세요."
fi
echo "================================================================"

exit ${FAIL}
