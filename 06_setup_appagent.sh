#!/usr/bin/env bash
# =============================================================================
# 06_setup_appagent.sh
# AppAgent 클론 + 의존성 설치 + 로컬 vLLM 연동 설정
# =============================================================================
# ⚠️ DEPRECATED: server-side emulator 경로 폐기.
# 사유: headless + swiftshader에서 상용 앱 ANR. docs/migration_plan.md 참조.
# 대체: 로컬 PC에서 SERVER_IP=<IP> bash 07_local_setup.sh
set -euo pipefail

# ─── 설정값 ───────────────────────────────────────────────────────────────────
APPAGENT_DIR="${HOME}/AppAgent"
APPAGENT_VENV="${HOME}/appagent-env"
APPAGENT_REPO="https://github.com/mnotgod96/AppAgent.git"

VLLM_HOST="${VLLM_HOST:-localhost}"
VLLM_PORT="${VLLM_PORT:-8080}"
VLLM_BASE_URL="http://${VLLM_HOST}:${VLLM_PORT}/v1"
# AppAgent config 의 OPENAI_API_BASE 는 /chat/completions 전체 URL
APPAGENT_API_BASE="${VLLM_BASE_URL}/chat/completions"

MODEL="${MODEL:-Qwen/Qwen2-VL-7B-Instruct}"
MAX_TOKENS="${MAX_TOKENS:-1024}"
TEMPERATURE="${TEMPERATURE:-0.0}"
REQUEST_INTERVAL="${REQUEST_INTERVAL:-3}"
MAX_ROUNDS="${MAX_ROUNDS:-20}"

ADB_SCREENSHOT_DIR="${ADB_SCREENSHOT_DIR:-/sdcard}"
# ─────────────────────────────────────────────────────────────────────────────

echo "================================================================"
echo " [1/4] AppAgent 리포지토리 클론 / 업데이트"
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
echo " [2/4] Python 가상환경 생성 및 의존성 설치"
echo "================================================================"
if [[ ! -d "${APPAGENT_VENV}" ]]; then
    echo "   → venv 생성: ${APPAGENT_VENV}"
    python3 -m venv "${APPAGENT_VENV}"
fi
source "${APPAGENT_VENV}/bin/activate"
echo "   Python: $(python --version)"

pip install --upgrade pip -q

# AppAgent requirements.txt 설치
if [[ -f "${APPAGENT_DIR}/requirements.txt" ]]; then
    echo "   → requirements.txt 설치..."
    pip install -r "${APPAGENT_DIR}/requirements.txt" -q
fi

# 추가 패키지 (vLLM 연동 및 ADB 제어에 필요)
pip install -q \
    openai \
    Pillow \
    pyyaml \
    colorama \
    requests \
    lxml \
    uiautomator2

# 선택적 패키지 (없어도 동작하나 'Warning! No module named X' 경고 제거)
pip install -q matplotlib 2>/dev/null || true
# sounddevice 는 libportaudio2 시스템 라이브러리 필요 → 실패해도 무시
pip install -q sounddevice 2>/dev/null || true

echo "   → 의존성 설치 완료"

echo "================================================================"
echo " [3/4] AppAgent config.yaml → 로컬 vLLM 연동 설정"
echo "================================================================"
CONFIG_FILE="${APPAGENT_DIR}/config.yaml"

# vLLM 서버 연결 확인
echo "   → vLLM 서버 연결 확인 중..."
VLLM_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    "http://${VLLM_HOST}:${VLLM_PORT}/health" 2>/dev/null || echo "000")
if [[ "${VLLM_STATUS}" == "200" ]]; then
    echo "   ✅ vLLM 서버 응답 정상 (http://${VLLM_HOST}:${VLLM_PORT})"
    # 실제 서빙 중인 모델명 가져오기
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
    echo "   ⚠️  vLLM 서버 미응답 (${VLLM_STATUS}). config는 작성하지만 서버를 먼저 실행하세요."
    echo "      bash 05_setup_vllm.sh"
fi

# config.yaml 생성 (Python으로 YAML 안전하게 작성)
python3 - <<PYEOF
import yaml, os

config = {
    # ── 모델 설정 ──────────────────────────────────────────────────
    "MODEL": "OpenAI",          # OpenAI 호환 API 사용
    "OPENAI_API_BASE":  "${APPAGENT_API_BASE}",   # vLLM /v1/chat/completions
    "OPENAI_API_KEY":   "local-vllm-no-key",      # vLLM 은 키 불필요, 더미값
    "OPENAI_API_MODEL": "${MODEL}",               # vLLM 에 서빙 중인 모델명
    "MAX_TOKENS":       int("${MAX_TOKENS}"),
    "TEMPERATURE":      float("${TEMPERATURE}"),
    "REQUEST_INTERVAL": int("${REQUEST_INTERVAL}"),

    # ── Qwen DashScope (미사용, 더미 유지) ─────────────────────────
    "DASHSCOPE_API_KEY": "sk-unused",
    "QWEN_MODEL":        "qwen-vl-max",

    # ── Android / ADB 설정 ─────────────────────────────────────────
    "ANDROID_SCREENSHOT_DIR": "${ADB_SCREENSHOT_DIR}",
    "ANDROID_XML_DIR":        "${ADB_SCREENSHOT_DIR}",

    # ── 에이전트 동작 설정 ─────────────────────────────────────────
    "DOC_REFINE":  False,
    "MAX_ROUNDS":  int("${MAX_ROUNDS}"),
    "DARK_MODE":   False,
    "MIN_DIST":    30,
}

config_path = "${CONFIG_FILE}"
with open(config_path, "w") as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True,
              sort_keys=False)
print(f"   → config.yaml 작성 완료: {config_path}")
PYEOF

echo ""
echo "   생성된 config.yaml 주요 항목:"
python3 -c "
import yaml
with open('${CONFIG_FILE}') as f:
    c = yaml.safe_load(f)
keys = ['MODEL','OPENAI_API_BASE','OPENAI_API_MODEL','MAX_TOKENS','TEMPERATURE']
for k in keys:
    print(f'     {k}: {c.get(k,\"(없음)\")}')
"

echo "================================================================"
echo " [4/4] 환경변수 파일 생성 (.env_appagent)"
echo "================================================================"
ENV_FILE="${APPAGENT_DIR}/.env_appagent"
cat > "${ENV_FILE}" <<EOF
# AppAgent 실행 시 source 하거나 export 해서 사용
# source ~/AppAgent/.env_appagent

export APPAGENT_VENV="${APPAGENT_VENV}"
export APPAGENT_DIR="${APPAGENT_DIR}"

# vLLM 엔드포인트 (openai 라이브러리가 자동으로 읽는 환경변수)
export OPENAI_API_KEY="local-vllm-no-key"
export OPENAI_BASE_URL="${VLLM_BASE_URL}"

# ADB 경로 (Step 1 에서 설정한 Android SDK)
export ANDROID_HOME="\${ANDROID_HOME:-\${HOME}/.android/sdk}"
export PATH="\${ANDROID_HOME}/platform-tools:\${PATH}"
EOF
echo "   → ${ENV_FILE} 생성 완료"

echo ""
echo "================================================================"
echo " ✅ AppAgent 설정 완료"
echo "================================================================"
echo ""
echo "  AppAgent 실행 방법:"
echo ""
echo "  1) 탐색(explore) 모드 - 앱 사용법 학습:"
echo "     source ${ENV_FILE}"
echo "     source ${APPAGENT_VENV}/bin/activate"
echo "     cd ${APPAGENT_DIR}"
echo "     python run.py --app <앱패키지명> --task \"<태스크 설명\""
echo "                   --series test --model ${MODEL}"
echo ""
echo "  2) 통합 테스트 (ADB + vLLM):"
echo "     python test_appagent_integration.py"
echo ""
echo "  현재 ADB 기기 목록:"
if command -v adb &>/dev/null; then
    adb devices 2>/dev/null | tail -n +2 | grep -v "^$" | \
        awk '{printf "     %s\n", $0}' || echo "     (없음)"
else
    ANDROID_HOME="${HOME}/.android/sdk"
    if [[ -x "${ANDROID_HOME}/platform-tools/adb" ]]; then
        "${ANDROID_HOME}/platform-tools/adb" devices 2>/dev/null | tail -n +2 | \
            grep -v "^$" | awk '{printf "     %s\n", $0}' || echo "     (없음)"
    else
        echo "     adb 명령어를 찾을 수 없습니다."
    fi
fi
