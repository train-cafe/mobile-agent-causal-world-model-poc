#!/usr/bin/env bash
# =============================================================================
# scripts/server/verify_vllm.sh
# 역할: 서버에서 vLLM API 정상 동작 4단계 검증
#
# 사용법 (서버에서 실행):
#   bash scripts/server/verify_vllm.sh
#
# 원격에서 검증하려면:
#   VLLM_HOST="10.7.60.145" bash scripts/server/verify_vllm.sh
# =============================================================================
set -euo pipefail

VLLM_HOST="${VLLM_HOST:-localhost}"
VLLM_PORT="${VLLM_PORT:-8080}"
VLLM_BASE="http://${VLLM_HOST}:${VLLM_PORT}"
TIMEOUT=10

green() { printf "\033[32m%s\033[0m\n" "$*"; }
red()   { printf "\033[31m%s\033[0m\n" "$*"; }

PASS=0; FAIL=0

check_step() {
    local n="$1"; local label="$2"; local result="$3"; local detail="${4:-}"
    if [[ "${result}" == "ok" ]]; then
        green "  ✅ [${n}] ${label}"
        PASS=$((PASS+1))
    else
        red   "  ❌ [${n}] ${label}"
        [[ -n "${detail}" ]] && printf "     %s\n" "${detail}"
        FAIL=$((FAIL+1))
    fi
}

echo "================================================================"
echo " vLLM API 검증 — ${VLLM_BASE}"
echo "================================================================"
echo ""

# ─── [1/4] health 엔드포인트 ─────────────────────────────────────────────────
HTTP=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout ${TIMEOUT} \
    "${VLLM_BASE}/health" 2>/dev/null || echo "000")
if [[ "${HTTP}" == "200" ]]; then
    check_step "1/4" "GET /health → 200 OK" "ok"
else
    check_step "1/4" "GET /health → 200 OK" "fail" \
        "HTTP ${HTTP}. tmux attach -t vllm-server 으로 로그 확인"
fi

# ─── [2/4] /v1/models — 서빙 중인 모델 확인 ──────────────────────────────────
MODELS_JSON=$(curl -s --connect-timeout ${TIMEOUT} "${VLLM_BASE}/v1/models" 2>/dev/null || echo "{}")
MODEL_ID=$(echo "${MODELS_JSON}" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    ids = [m['id'] for m in d.get('data',[])]
    print(ids[0] if ids else '')
except: print('')
" 2>/dev/null || true)

if [[ -n "${MODEL_ID}" ]]; then
    check_step "2/4" "GET /v1/models — 모델 확인" "ok"
    printf "     서빙 중: %s\n" "${MODEL_ID}"
else
    check_step "2/4" "GET /v1/models — 모델 확인" "fail" \
        "모델 미응답. 로딩 완료까지 대기 또는 tail -f ~/.vllm/logs/vllm-server.log"
    MODEL_ID="Qwen/Qwen3-VL-32B-Instruct"   # 이후 테스트용 기본값
fi
echo ""

# ─── [3/4] 텍스트 추론 테스트 ─────────────────────────────────────────────────
echo "  → 텍스트 추론 테스트 (max_tokens=16)..."
TEXT_RESP=$(curl -s --connect-timeout ${TIMEOUT} -m 60 \
    "${VLLM_BASE}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer local-vllm-no-key" \
    -d "{
      \"model\": \"${MODEL_ID}\",
      \"messages\": [{\"role\": \"user\", \"content\": \"Reply with one word: ready\"}],
      \"max_tokens\": 16,
      \"temperature\": 0.0
    }" 2>/dev/null || echo "{}")

TEXT_CONTENT=$(echo "${TEXT_RESP}" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d['choices'][0]['message']['content'].strip())
except: print('')
" 2>/dev/null || true)

if [[ -n "${TEXT_CONTENT}" ]]; then
    check_step "3/4" "텍스트 추론 테스트" "ok"
    printf "     응답: \"%s\"\n" "${TEXT_CONTENT}"
else
    check_step "3/4" "텍스트 추론 테스트" "fail" \
        "응답 없음 또는 오류. 응답: $(echo "${TEXT_RESP}" | head -c 200)"
fi
echo ""

# ─── [4/4] 비전 추론 테스트 (1x1 투명 PNG base64) ──────────────────────────────
echo "  → 비전 추론 테스트 (1x1 PNG 이미지 전송)..."
# 1x1 투명 PNG (최소 PNG 파일의 base64)
TINY_PNG_B64="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

VISION_RESP=$(curl -s --connect-timeout ${TIMEOUT} -m 90 \
    "${VLLM_BASE}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer local-vllm-no-key" \
    -d "{
      \"model\": \"${MODEL_ID}\",
      \"messages\": [{
        \"role\": \"user\",
        \"content\": [
          {\"type\": \"text\", \"text\": \"What color is this image? Answer in one word.\"},
          {\"type\": \"image_url\", \"image_url\": {\"url\": \"data:image/png;base64,${TINY_PNG_B64}\"}}
        ]
      }],
      \"max_tokens\": 16,
      \"temperature\": 0.0
    }" 2>/dev/null || echo "{}")

VISION_CONTENT=$(echo "${VISION_RESP}" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d['choices'][0]['message']['content'].strip())
except: print('')
" 2>/dev/null || true)

if [[ -n "${VISION_CONTENT}" ]]; then
    check_step "4/4" "비전 추론 테스트 (이미지 → 텍스트)" "ok"
    printf "     응답: \"%s\"\n" "${VISION_CONTENT}"
else
    check_step "4/4" "비전 추론 테스트 (이미지 → 텍스트)" "fail" \
        "이미지 처리 실패. VLM이 비전 모드를 지원하는지 확인"
fi

# ─── 결과 요약 ────────────────────────────────────────────────────────────────
echo ""
echo "================================================================"
TOTAL=$((PASS + FAIL))
if [[ ${FAIL} -eq 0 ]]; then
    green " ✅ 4/4 통과 — vLLM 서버 정상 (텍스트 + 비전 모두 응답)"
    echo ""
    echo "  로컬 PC에서 연결하려면:"
    echo "    SERVER_IP=\"$(hostname -I | awk '{print $1}')\" bash scripts/local/check_environment.sh"
else
    red " ❌ ${FAIL}개 실패 (${PASS}/${TOTAL} 통과)"
    echo ""
    echo "  로그 확인:"
    echo "    tail -50 ~/.vllm/logs/vllm-server.log"
    echo "    tmux attach -t vllm-server"
    echo ""
    echo "  vLLM 재시작:"
    echo "    bash 05_setup_vllm.sh"
fi
echo "================================================================"

exit ${FAIL}
