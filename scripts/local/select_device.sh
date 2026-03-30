#!/usr/bin/env bash
# =============================================================================
# scripts/local/select_device.sh
# 역할: 연결된 ADB 기기 목록을 보여주고, 사용할 기기를 선택/설정
#
# 사용법:
#   bash scripts/local/select_device.sh          # 대화형 선택
#   bash scripts/local/select_device.sh 1        # 첫 번째 기기 선택
#   bash scripts/local/select_device.sh list      # 목록만 출력
#
# 결과: ANDROID_SERIAL 환경변수를 설정하는 명령을 출력
#       eval $(bash scripts/local/select_device.sh 1) 로 적용 가능
# =============================================================================
set -euo pipefail

ADB_BIN="${ADB_BIN:-adb}"
MODE="${1:-}"

echo "================================================================" >&2
echo " ADB 기기 선택" >&2
echo "================================================================" >&2
echo "" >&2

# ─── 연결된 기기 목록 ──────────────────────────────────────────────────────
mapfile -t DEVICES < <(${ADB_BIN} devices 2>/dev/null | tail -n +2 | grep -E '\t(device|recovery)$' | awk '{print $1}')

if [[ ${#DEVICES[@]} -eq 0 ]]; then
    echo "   ❌ 연결된 ADB 기기가 없습니다." >&2
    echo "" >&2
    echo "   에뮬레이터 시작:" >&2
    echo "     emulator -avd \$(emulator -list-avds | head -1) &" >&2
    echo "" >&2
    echo "   실제 기기 연결:" >&2
    echo "     bash scripts/local/connect_device.sh" >&2
    exit 1
fi

# ─── 기기 정보 수집 ─────────────────────────────────────────────────────────
echo "   연결된 기기:" >&2
for i in "${!DEVICES[@]}"; do
    DEV="${DEVICES[$i]}"
    # 기기 정보 수집
    MODEL=$(${ADB_BIN} -s "${DEV}" shell getprop ro.product.model 2>/dev/null | tr -d '\r' || echo "unknown")
    BRAND=$(${ADB_BIN} -s "${DEV}" shell getprop ro.product.brand 2>/dev/null | tr -d '\r' || echo "unknown")
    SDK=$(${ADB_BIN} -s "${DEV}" shell getprop ro.build.version.sdk 2>/dev/null | tr -d '\r' || echo "?")
    RELEASE=$(${ADB_BIN} -s "${DEV}" shell getprop ro.build.version.release 2>/dev/null | tr -d '\r' || echo "?")

    # 에뮬레이터 vs 실제 기기 판별
    if echo "${DEV}" | grep -qE "^emulator-|^localhost:"; then
        TYPE="에뮬레이터"
    else
        TYPE="실제 기기"
    fi

    NUM=$((i + 1))
    echo "   [${NUM}] ${DEV}" >&2
    echo "       ${BRAND} ${MODEL} | Android ${RELEASE} (API ${SDK}) | ${TYPE}" >&2
done
echo "" >&2

# ─── list 모드면 여기서 종료 ─────────────────────────────────────────────────
if [[ "${MODE}" == "list" ]]; then
    exit 0
fi

# ─── 기기가 1개면 자동 선택 ──────────────────────────────────────────────────
if [[ ${#DEVICES[@]} -eq 1 ]]; then
    SELECTED="${DEVICES[0]}"
    echo "   → 기기 1개 — 자동 선택: ${SELECTED}" >&2
else
    # 번호 지정이 있으면 사용
    if [[ -n "${MODE}" && "${MODE}" =~ ^[0-9]+$ ]]; then
        IDX=$((MODE - 1))
        if [[ ${IDX} -ge 0 && ${IDX} -lt ${#DEVICES[@]} ]]; then
            SELECTED="${DEVICES[$IDX]}"
            echo "   → 선택: [${MODE}] ${SELECTED}" >&2
        else
            echo "   ❌ 잘못된 번호: ${MODE} (1~${#DEVICES[@]})" >&2
            exit 1
        fi
    else
        # 대화형 선택
        echo -n "   번호를 입력하세요 (1~${#DEVICES[@]}): " >&2
        read -r CHOICE
        IDX=$((CHOICE - 1))
        if [[ ${IDX} -ge 0 && ${IDX} -lt ${#DEVICES[@]} ]]; then
            SELECTED="${DEVICES[$IDX]}"
            echo "   → 선택: [${CHOICE}] ${SELECTED}" >&2
        else
            echo "   ❌ 잘못된 번호" >&2
            exit 1
        fi
    fi
fi

echo "" >&2
echo "================================================================" >&2
echo " ✅ 선택된 기기: ${SELECTED}" >&2
echo "================================================================" >&2
echo "" >&2
echo "   적용 방법:" >&2
echo "     export ANDROID_SERIAL=\"${SELECTED}\"" >&2
echo "" >&2
echo "   또는 한 줄로:" >&2
echo "     eval \$(bash scripts/local/select_device.sh ${MODE:-})" >&2
echo "" >&2

# stdout으로 export 명령 출력 (eval 용)
echo "export ANDROID_SERIAL=\"${SELECTED}\""
