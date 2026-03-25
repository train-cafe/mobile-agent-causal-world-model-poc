#!/usr/bin/env bash
# =============================================================================
# 05_setup_vllm.sh
# H100 x2 에서 vLLM OpenAI-compatible 서버 실행
# - Qwen/Qwen3-VL-32B-Instruct (기본)
# - GPU 0,1 전용 (GPU 2,3은 에뮬레이터 등이 점유 중)
# - tensor-parallel-size=2, 포트 8080 (ws-scrcpy 8000과 충돌 방지)
# =============================================================================
set -euo pipefail

# ─── 설정값 ───────────────────────────────────────────────────────────────────
VENV_DIR="${HOME}/vllm-env"
VLLM_PORT="${VLLM_PORT:-8080}"
VLLM_HOST="${VLLM_HOST:-0.0.0.0}"
TENSOR_PARALLEL="${TENSOR_PARALLEL:-2}"
GPU_MEM_UTIL="${GPU_MEM_UTIL:-0.90}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-16384}"

# GPU 0,1만 사용 (GPU 2,3은 이미 점유됨)
CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0,1}"

# 모델: Qwen3-VL-32B (H100 2x 160GB VRAM으로 충분)
MODEL="${MODEL:-Qwen/Qwen3-VL-32B-Instruct}"

LOG_DIR="${HOME}/.vllm/logs"
VLLM_LOG="${LOG_DIR}/vllm-server.log"
VLLM_PID_FILE="${LOG_DIR}/vllm-server.pid"
# ─────────────────────────────────────────────────────────────────────────────

mkdir -p "${LOG_DIR}"

echo "================================================================"
echo " [1/4] Python 가상환경 생성/확인"
echo "================================================================"
if [[ ! -d "${VENV_DIR}" ]]; then
    echo "   → venv 생성: ${VENV_DIR}"
    python3 -m venv "${VENV_DIR}"
else
    echo "   → 기존 venv 사용: ${VENV_DIR}"
fi

source "${VENV_DIR}/bin/activate"
echo "   Python: $(python --version)"
echo "   pip   : $(pip --version)"

echo "================================================================"
echo " [2/4] vLLM 및 의존성 설치"
echo "================================================================"
pip install --upgrade pip -q

# vLLM 이미 설치됐으면 스킵
if python -c "import vllm" 2>/dev/null; then
    echo "   → vLLM 이미 설치됨: $(python -c 'import vllm; print(vllm.__version__)')"
else
    echo "   → vLLM 설치 중 (CUDA wheel, 시간이 걸립니다)..."
    pip install vllm --extra-index-url https://download.pytorch.org/whl/cu121
fi

# Qwen2-VL 추가 의존성
pip install -q \
    transformers>=4.45.0 \
    accelerate \
    qwen-vl-utils \
    pillow \
    requests

echo "   → 설치 완료"

echo "================================================================"
echo " [3/4] GPU 상태 확인"
echo "================================================================"
if ! command -v nvidia-smi &>/dev/null; then
    echo "⚠️  nvidia-smi 없음. GPU 확인 불가. 계속 진행합니다."
else
    nvidia-smi --query-gpu=index,name,memory.total,memory.free \
               --format=csv,noheader,nounits | \
    awk -F',' '{printf "   GPU %s: %s  (전체 %s MiB / 여유 %s MiB)\n", $1, $2, $3, $4}'
fi

echo "================================================================"
echo " [4/4] vLLM OpenAI-compatible 서버 백그라운드 실행"
echo "================================================================"

# 기존 프로세스 정리
if [[ -f "${VLLM_PID_FILE}" ]]; then
    OLD_PID=$(cat "${VLLM_PID_FILE}" 2>/dev/null || true)
    if [[ -n "${OLD_PID}" ]] && kill -0 "${OLD_PID}" 2>/dev/null; then
        echo "   → 기존 vLLM 서버(PID: ${OLD_PID}) 종료"
        kill "${OLD_PID}" 2>/dev/null || true
        sleep 3
    fi
fi
# 포트 점유 프로세스 정리
CWD_PIDS=$(find /proc/[0-9]*/cwd -lname "${VENV_DIR}*" 2>/dev/null \
    | grep -oP '(?<=/proc/)\d+' | sort -u || true)
[[ -n "${CWD_PIDS}" ]] && kill -9 ${CWD_PIDS} 2>/dev/null || true

# vLLM 버전에 따라 --no-enable-log-requests 또는 --disable-log-requests 사용
LOG_FLAG=$(
    "${VENV_DIR}/bin/python" -m vllm.entrypoints.openai.api_server --help 2>&1 \
    | grep -q "no-enable-log-requests" \
    && echo "--no-enable-log-requests" \
    || echo ""
)

# tmux 세션이 있으면 tmux, 없으면 nohup으로 실행
# --enforce-eager: CUDA 그래프 비활성화 (초기화 실패 방지)
# --max-num-seqs: 동시 시퀀스 수 제한 (메모리 안정성)
VLLM_CMD="CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES} \
    ${VENV_DIR}/bin/python -m vllm.entrypoints.openai.api_server \
    --model \"${MODEL}\" \
    --tensor-parallel-size ${TENSOR_PARALLEL} \
    --host ${VLLM_HOST} \
    --port ${VLLM_PORT} \
    --served-model-name \"${MODEL}\" \
    --trust-remote-code \
    --max-model-len ${MAX_MODEL_LEN} \
    --gpu-memory-utilization ${GPU_MEM_UTIL} \
    --dtype auto \
    --enforce-eager \
    --max-num-seqs 16 \
    ${LOG_FLAG}"

if command -v tmux &>/dev/null; then
    echo "   → tmux 세션 'vllm-server' 로 실행"
    echo "   → CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES} (GPU 0,1 사용)"
    tmux kill-session -t vllm-server 2>/dev/null || true
    tmux new-session -d -s vllm-server \
        "source ${VENV_DIR}/bin/activate && ${VLLM_CMD} 2>&1 | tee ${VLLM_LOG}"
    # tmux에서는 PID 추적이 복잡하므로 잠시 후 pgrep으로 찾음
    sleep 3
    VLLM_PID=$(pgrep -f "vllm.entrypoints.openai" | head -1 || true)
else
    echo "   → nohup 백그라운드 실행"
    eval "nohup ${VLLM_CMD} > '${VLLM_LOG}' 2>&1 &"
    VLLM_PID=$!
fi

echo "${VLLM_PID:-unknown}" > "${VLLM_PID_FILE}"
echo "   → 서버 시작됨 (PID: ${VLLM_PID:-unknown})"
echo "   → 로그: ${VLLM_LOG}"
echo ""
echo "   ※ 모델 로딩에 수분이 걸립니다. 아래 명령어로 준비 확인:"
echo "      watch -n5 'curl -s http://localhost:${VLLM_PORT}/health && echo OK'"
echo ""
echo "   ※ 준비 완료 확인 후 06_setup_appagent.sh 실행하세요."
echo ""

# 기동 대기 (최대 5분 = 모델 로딩 시간)
echo "   서버 기동 대기 중 (최대 300초, 모델 로딩 포함)..."
READY=false
for i in $(seq 1 60); do
    sleep 5
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        "http://localhost:${VLLM_PORT}/health" 2>/dev/null || echo "000")
    if [[ "${HTTP_CODE}" == "200" ]]; then
        READY=true
        break
    fi
    # 프로세스 사망 여부 확인
    if [[ -n "${VLLM_PID:-}" ]] && ! kill -0 "${VLLM_PID}" 2>/dev/null; then
        echo "❌ vLLM 서버 프로세스가 종료됐습니다."
        echo "   ── 로그 앞부분 (root cause) ──"
        head -60 "${VLLM_LOG}"
        echo "   ── 로그 끝부분 ──"
        tail -40 "${VLLM_LOG}"
        exit 1
    fi
    printf "   %ds...\r" "$((i * 5))"
done

echo ""
if [[ "${READY}" == "true" ]]; then
    echo "✅ vLLM 서버 준비 완료!"
    echo ""
    # 모델 목록 확인
    MODELS=$(curl -s "http://localhost:${VLLM_PORT}/v1/models" 2>/dev/null \
        | python3 -c "import json,sys; d=json.load(sys.stdin); \
          [print('   모델:', m['id']) for m in d.get('data',[])]" 2>/dev/null || true)
    echo "${MODELS}"
    echo ""
    echo "   API 엔드포인트: http://localhost:${VLLM_PORT}/v1"
    echo "   다음 단계: bash 06_setup_appagent.sh"
else
    echo "⚠️  300초 내 준비 안 됨. 모델 로딩 중일 수 있습니다."
    echo "   로그 확인: tail -f ${VLLM_LOG}"
    echo "   수동 확인: curl http://localhost:${VLLM_PORT}/health"
fi
