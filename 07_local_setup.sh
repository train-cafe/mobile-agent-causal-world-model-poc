#!/usr/bin/env bash
# =============================================================================
# 07_local_setup.sh
# 로컬 PC에서 AppAgent 실행 + 원격 vLLM 서버 연동
#
# 사용법:
#   SERVER_IP="10.7.60.145" bash 07_local_setup.sh
#
# 전제 조건:
#   - 로컬 PC에 Android 에뮬레이터 또는 기기가 ADB로 연결되어 있어야 함
#   - 원격 서버에서 05_setup_vllm.sh 가 먼저 실행되어야 함
#   - CUDA/GPU가 없어도 됨 (VLM 추론은 원격 서버에서 실행)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ─── 설정값 ───────────────────────────────────────────────────────────────────
APPAGENT_DIR="${HOME}/AppAgent"
APPAGENT_VENV="${HOME}/appagent-env"
APPAGENT_REPO="https://github.com/mnotgod96/AppAgent.git"

# 원격 vLLM 서버 주소 (필수: SERVER_IP 환경변수로 지정)
SERVER_IP="${SERVER_IP:-}"
VLLM_HOST="${VLLM_HOST:-${SERVER_IP}}"
VLLM_PORT="${VLLM_PORT:-8080}"

if [[ -z "${VLLM_HOST}" ]]; then
    echo "❌ 오류: SERVER_IP (또는 VLLM_HOST) 환경변수를 설정해주세요."
    echo "   사용법: SERVER_IP=\"10.7.60.145\" bash $0"
    exit 1
fi

VLLM_BASE_URL="http://${VLLM_HOST}:${VLLM_PORT}/v1"
APPAGENT_API_BASE="${VLLM_BASE_URL}/chat/completions"

MODEL="${MODEL:-ByteDance-Seed/UI-TARS-1.5-7B}"
MAX_TOKENS="${MAX_TOKENS:-2048}"
TEMPERATURE="${TEMPERATURE:-0.0}"
REQUEST_INTERVAL="${REQUEST_INTERVAL:-3}"
MAX_ROUNDS="${MAX_ROUNDS:-20}"

ADB_SCREENSHOT_DIR="${ADB_SCREENSHOT_DIR:-/sdcard}"
# ─────────────────────────────────────────────────────────────────────────────

echo "================================================================"
echo " [1/5] AppAgent 리포지토리 클론 / 업데이트"
echo "================================================================"
if [[ -d "${APPAGENT_DIR}/.git" ]]; then
    echo "   → 이미 클론됨. 최신화..."
    git -C "${APPAGENT_DIR}" pull --rebase origin main 2>/dev/null || \
    git -C "${APPAGENT_DIR}" pull --rebase origin master 2>/dev/null || true
else
    echo "   → 클론 중: ${APPAGENT_REPO}"
    git clone "${APPAGENT_REPO}" "${APPAGENT_DIR}"
fi
echo "   → AppAgent 위치: ${APPAGENT_DIR}"

echo "================================================================"
echo " [2/5] Python 가상환경 생성 및 의존성 설치"
echo "================================================================"
if [[ ! -d "${APPAGENT_VENV}" ]]; then
    echo "   → venv 생성: ${APPAGENT_VENV}"
    python3 -m venv "${APPAGENT_VENV}"
fi
source "${APPAGENT_VENV}/bin/activate"
echo "   Python: $(python --version)"

pip install --upgrade pip -q

if [[ -f "${APPAGENT_DIR}/requirements.txt" ]]; then
    echo "   → requirements.txt 설치..."
    pip install -r "${APPAGENT_DIR}/requirements.txt" -q
fi

pip install -q \
    openai \
    Pillow \
    pyyaml \
    colorama \
    requests \
    lxml \
    uiautomator2

pip install -q matplotlib 2>/dev/null || true
pip install -q sounddevice 2>/dev/null || true

echo "   → 의존성 설치 완료"

echo "================================================================"
echo " [3/5] 원격 vLLM 서버 연결 확인 및 config.yaml 생성"
echo "================================================================"
CONFIG_FILE="${APPAGENT_DIR}/config.yaml"

echo "   → vLLM 서버 연결 확인 중: http://${VLLM_HOST}:${VLLM_PORT}/health"
VLLM_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    "http://${VLLM_HOST}:${VLLM_PORT}/health" 2>/dev/null || echo "000")

if [[ "${VLLM_STATUS}" == "200" ]]; then
    echo "   ✅ vLLM 서버 응답 정상"
    SERVED_MODEL=$(curl -s "http://${VLLM_HOST}:${VLLM_PORT}/v1/models" 2>/dev/null \
        | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    models = [m['id'] for m in d.get('data', [])]
    print(models[0] if models else '')
except:
    print('')
" 2>/dev/null || true)
    if [[ -n "${SERVED_MODEL}" ]]; then
        MODEL="${SERVED_MODEL}"
        echo "   → 서빙 중인 모델: ${MODEL}"
    fi
else
    echo "   ⚠️  vLLM 서버 미응답 (HTTP ${VLLM_STATUS})"
    echo "      원격 서버에서 05_setup_vllm.sh 를 먼저 실행하세요."
    echo "      config는 작성하지만 실행 시 연결 오류가 발생할 수 있습니다."
fi

python3 - <<PYEOF
import yaml, os

config = {
    "MODEL": "OpenAI",
    "OPENAI_API_BASE":  "${APPAGENT_API_BASE}",
    "OPENAI_API_KEY":   "local-vllm-no-key",
    "OPENAI_API_MODEL": "${MODEL}",
    "MAX_TOKENS":       int("${MAX_TOKENS}"),
    "TEMPERATURE":      float("${TEMPERATURE}"),
    "REQUEST_INTERVAL": int("${REQUEST_INTERVAL}"),
    "DASHSCOPE_API_KEY": "sk-unused",
    "QWEN_MODEL":        "qwen-vl-max",
    "ANDROID_SCREENSHOT_DIR": "${ADB_SCREENSHOT_DIR}",
    "ANDROID_XML_DIR":        "${ADB_SCREENSHOT_DIR}",
    "DOC_REFINE":  False,
    "MAX_ROUNDS":  int("${MAX_ROUNDS}"),
    "DARK_MODE":   False,
    "MIN_DIST":    30,
    "CAUSAL_MODE": True,
    "WRAPPER_ENABLED": True,
}

config_path = "${CONFIG_FILE}"
with open(config_path, "w") as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
print(f"   → config.yaml 작성 완료: {config_path}")
PYEOF

echo ""
echo "   생성된 config.yaml 주요 항목:"
python3 -c "
import yaml
with open('${CONFIG_FILE}') as f:
    c = yaml.safe_load(f)
keys = ['MODEL','OPENAI_API_BASE','OPENAI_API_MODEL','MAX_TOKENS','CAUSAL_MODE','WRAPPER_ENABLED']
for k in keys:
    print(f'     {k}: {c.get(k, \"(없음)\")}')
"

echo "================================================================"
echo " [4/5] Causal World Model 래퍼 설치 + task_executor.py 패치"
echo "================================================================"
CAUSAL_WRAPPER_SRC="${SCRIPT_DIR}/causal_wrapper.py"
CAUSAL_WRAPPER_DST="${APPAGENT_DIR}/scripts/causal_wrapper.py"

if [[ ! -f "${CAUSAL_WRAPPER_SRC}" ]]; then
    echo "❌ causal_wrapper.py 를 찾을 수 없습니다: ${CAUSAL_WRAPPER_SRC}"
    echo "   이 스크립트와 같은 디렉토리에 causal_wrapper.py 가 있어야 합니다."
    exit 1
fi

cp "${CAUSAL_WRAPPER_SRC}" "${CAUSAL_WRAPPER_DST}"
echo "   → causal_wrapper.py 복사 완료: ${CAUSAL_WRAPPER_DST}"

# causal_action_wrapper.py 복사
ACTION_WRAPPER_SRC="${SCRIPT_DIR}/causal_action_wrapper.py"
ACTION_WRAPPER_DST="${APPAGENT_DIR}/scripts/causal_action_wrapper.py"
if [[ -f "${ACTION_WRAPPER_SRC}" ]]; then
    cp "${ACTION_WRAPPER_SRC}" "${ACTION_WRAPPER_DST}"
    echo "   → causal_action_wrapper.py 복사 완료: ${ACTION_WRAPPER_DST}"
else
    echo "   ⚠️  causal_action_wrapper.py 없음 — 액션 래퍼 미설치"
fi

# coordinate_executor.py 복사 (좌표 직접 출력 모드)
COORD_EXEC_SRC="${SCRIPT_DIR}/coordinate_executor.py"
COORD_EXEC_DST="${APPAGENT_DIR}/scripts/coordinate_executor.py"
if [[ -f "${COORD_EXEC_SRC}" ]]; then
    cp "${COORD_EXEC_SRC}" "${COORD_EXEC_DST}"
    echo "   → coordinate_executor.py 복사 완료: ${COORD_EXEC_DST}"
fi

TASK_EXECUTOR="${APPAGENT_DIR}/scripts/task_executor.py"
PATCH_SCRIPT="${SCRIPT_DIR}/patch_task_executor.py"

if [[ ! -f "${PATCH_SCRIPT}" ]]; then
    echo "❌ patch_task_executor.py 를 찾을 수 없습니다: ${PATCH_SCRIPT}"
    exit 1
fi

echo "   → task_executor.py 패치 적용 중..."
# 기존 패치가 있으면 원본에서 다시 적용 (들여쓰기 버그 등 수정 반영)
if [[ -f "${TASK_EXECUTOR}.orig" ]]; then
    cp "${TASK_EXECUTOR}.orig" "${TASK_EXECUTOR}"
    echo "   → 원본 복원 후 재패치: ${TASK_EXECUTOR}.orig"
fi
python3 "${PATCH_SCRIPT}" "${TASK_EXECUTOR}"

# and_controller.py swipe safe zone 패치
AND_CONTROLLER="${APPAGENT_DIR}/scripts/and_controller.py"
SWIPE_PATCH="${SCRIPT_DIR}/patch_and_controller.py"
if [[ -f "${SWIPE_PATCH}" ]] && [[ -f "${AND_CONTROLLER}" ]]; then
    echo "   → and_controller.py swipe safe zone 패치 적용 중..."
    if [[ -f "${AND_CONTROLLER}.orig" ]]; then
        cp "${AND_CONTROLLER}.orig" "${AND_CONTROLLER}"
        echo "   → 원본 복원 후 재패치: ${AND_CONTROLLER}.orig"
    fi
    python3 "${SWIPE_PATCH}" "${AND_CONTROLLER}"
fi

# run.py 패치: coordinate_executor.py를 기본 executor로 사용
RUN_PY="${APPAGENT_DIR}/run.py"
if [[ -f "${RUN_PY}" ]] && [[ -f "${APPAGENT_DIR}/scripts/coordinate_executor.py" ]]; then
    echo "   → run.py 패치: coordinate_executor.py를 기본 executor로 설정..."
    if ! grep -q "coordinate_executor" "${RUN_PY}"; then
        # 백업
        if [[ ! -f "${RUN_PY}.orig" ]]; then
            cp "${RUN_PY}" "${RUN_PY}.orig"
        fi
        # task_executor.py → coordinate_executor.py 로 교체
        sed -i 's|scripts/task_executor.py|scripts/coordinate_executor.py|g' "${RUN_PY}"
        echo "   ✅ run.py 패치 완료: coordinate_executor.py 사용"
    else
        echo "   ✅ run.py 이미 coordinate_executor 사용 중"
    fi
fi

echo "================================================================"
echo " [5/7] 에뮬레이터 네비게이션 모드 + ADBKeyboard 설정"
echo "================================================================"
if command -v adb &>/dev/null && adb devices 2>/dev/null | grep -q "device$"; then
    echo "   → 3버튼 네비게이션 활성화 중..."
    adb shell cmd overlay enable com.android.internal.systemui.navbar.threebutton 2>/dev/null \
        && echo "   ✅ 3버튼 네비게이션 활성화 완료" \
        || echo "   ⚠️  3버튼 네비게이션 활성화 실패 (수동 설정: 설정 → 시스템 → 제스처 → 시스템 탐색)"

    # ADBKeyboard 설치 확인 (한글 입력 지원)
    echo "   → ADBKeyboard 확인 중..."
    if adb shell pm list packages 2>/dev/null | grep -q "com.android.adbkeyboard"; then
        echo "   ✅ ADBKeyboard 이미 설치됨"
        adb shell ime set com.android.adbkeyboard/.AdbIME 2>/dev/null
        echo "   ✅ ADBKeyboard를 기본 IME로 설정"
    else
        echo "   ⚠️  ADBKeyboard 미설치 — 한글 입력이 필요하면 아래 설치:"
        echo "      1. https://github.com/nicewook/ADBKeyboard/releases 에서 APK 다운로드"
        echo "      2. adb install ADBKeyboard.apk"
        echo "      3. adb shell ime set com.android.adbkeyboard/.AdbIME"
    fi
else
    echo "   ⚠️  ADB 기기 미연결 — 에뮬레이터 시작 후 아래 명령 실행:"
    echo "      adb shell cmd overlay enable com.android.internal.systemui.navbar.threebutton"
fi

echo "================================================================"
echo " [6/7] 환경변수 파일 생성 (.env_appagent)"
echo "================================================================"
ENV_FILE="${APPAGENT_DIR}/.env_appagent"
cat > "${ENV_FILE}" <<EOF
# source ~/AppAgent/.env_appagent

export APPAGENT_VENV="${APPAGENT_VENV}"
export APPAGENT_DIR="${APPAGENT_DIR}"

export OPENAI_API_KEY="local-vllm-no-key"
export OPENAI_BASE_URL="${VLLM_BASE_URL}"

export ANDROID_HOME="\${ANDROID_HOME:-\${HOME}/.android/sdk}"
export PATH="\${ANDROID_HOME}/platform-tools:\${PATH}"

# Causal World Model 활성화 여부 (true/false)
export CAUSAL_MODE="true"
export WRAPPER_ENABLED="true"
EOF
echo "   → ${ENV_FILE} 생성 완료"

echo ""
echo "================================================================"
echo " [7/7] ADB 기기 현황"
echo "================================================================"
if command -v adb &>/dev/null; then
    DEVICE_COUNT=$(adb devices 2>/dev/null | tail -n +2 | grep -c "device$" || true)
    echo "   연결된 기기: ${DEVICE_COUNT}개"
    adb devices 2>/dev/null | tail -n +2 | grep -v "^$" | while read -r line; do
        DEV=$(echo "$line" | awk '{print $1}')
        MODEL=$(adb -s "$DEV" shell getprop ro.product.model 2>/dev/null | tr -d '\r' || echo "?")
        BRAND=$(adb -s "$DEV" shell getprop ro.product.brand 2>/dev/null | tr -d '\r' || echo "?")
        if echo "$DEV" | grep -qE "^emulator-"; then
            echo "     ${DEV}  ${BRAND} ${MODEL}  [에뮬레이터]"
        else
            echo "     ${DEV}  ${BRAND} ${MODEL}  [실제 기기]"
        fi
    done
    echo ""
    if [[ "${DEVICE_COUNT}" -gt 1 ]]; then
        echo "   ⚠️  기기가 여러 개입니다. 사용할 기기를 선택하세요:"
        echo "      bash scripts/local/select_device.sh"
        echo "      또는: export ANDROID_SERIAL=<기기ID>"
    fi
else
    echo "   → adb 미설치"
fi

echo ""
echo "================================================================"
echo " ✅ 로컬 PC 설정 완료"
echo "================================================================"
echo ""
echo "  ── 실행 방법 ──"
echo ""
echo "  1) 환경 준비:"
echo "     source ${ENV_FILE}"
echo "     source ${APPAGENT_VENV}/bin/activate"
echo "     cd ${APPAGENT_DIR}"
echo ""
echo "  2) 기기 선택 (에뮬레이터 + 실제 기기 동시 사용 시):"
echo "     eval \$(bash ${SCRIPT_DIR}/scripts/local/select_device.sh)"
echo ""
echo "  3) AppAgent 실행:"
echo "     python run.py --app <앱패키지명>"
echo ""
echo "  ── 실제 Samsung 기기 연결 ──"
echo ""
echo "  USB:    USB 케이블 연결 → bash ${SCRIPT_DIR}/scripts/local/connect_device.sh"
echo "  무선:   bash ${SCRIPT_DIR}/scripts/local/connect_device.sh --wifi"
echo ""
