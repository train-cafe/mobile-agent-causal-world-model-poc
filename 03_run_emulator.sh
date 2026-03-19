#!/usr/bin/env bash
# =============================================================================
# 03_run_emulator.sh
# 역할: Xvfb 가상 디스플레이 위에서 Android 에뮬레이터를 헤드리스 백그라운드 실행
#       ADB 연결 확인까지 대기
# 실행: bash 03_run_emulator.sh
# =============================================================================
set -euo pipefail

# ─────────────────────────────────────────────────────────────────
# 설정값 (02_setup_android_sdk.sh 와 동일하게 유지)
# ─────────────────────────────────────────────────────────────────
AVD_NAME="Pixel6_API34"
DISPLAY_NUM=99          # Xvfb 가상 디스플레이 번호
XVFB_RESOLUTION="1080x1920x24"
ADB_WAIT_TIMEOUT=300    # seconds

# 로그 파일
LOG_DIR="${HOME}/.android/logs"
mkdir -p "${LOG_DIR}"
XVFB_LOG="${LOG_DIR}/xvfb.log"
EMU_LOG="${LOG_DIR}/emulator.log"
PID_FILE="${LOG_DIR}/emulator.pid"

# ─────────────────────────────────────────────────────────────────
# 환경 변수 (현재 세션)
# ─────────────────────────────────────────────────────────────────
export ANDROID_HOME="${HOME}/.android/sdk"
export ANDROID_AVD_HOME="${HOME}/.android/avd"
export PATH="${ANDROID_HOME}/cmdline-tools/latest/bin:${PATH}"
export PATH="${ANDROID_HOME}/platform-tools:${PATH}"
export PATH="${ANDROID_HOME}/emulator:${PATH}"

# ─────────────────────────────────────────────────────────────────
stop_existing() {
    echo "── 기존 에뮬레이터/Xvfb 프로세스 정리 ──"
    pkill -f "emulator.*${AVD_NAME}" 2>/dev/null && echo "   → 기존 에뮬레이터 종료" || true
    pkill -f "Xvfb :${DISPLAY_NUM}"  2>/dev/null && echo "   → 기존 Xvfb 종료"       || true
    sleep 1
}

# ─────────────────────────────────────────────────────────────────
echo "================================================================"
echo " [1/4] Xvfb 가상 디스플레이 시작 (DISPLAY=:${DISPLAY_NUM})"
echo "================================================================"

stop_existing

Xvfb ":${DISPLAY_NUM}" -screen 0 "${XVFB_RESOLUTION}" \
    > "${XVFB_LOG}" 2>&1 &
XVFB_PID=$!
export DISPLAY=":${DISPLAY_NUM}"

# Xvfb 기동 대기
sleep 2
if ! kill -0 "${XVFB_PID}" 2>/dev/null; then
    echo "❌ Xvfb 시작 실패. 로그: ${XVFB_LOG}"
    exit 1
fi
echo "   → Xvfb PID: ${XVFB_PID}  (DISPLAY=${DISPLAY})"

# ─────────────────────────────────────────────────────────────────
echo "================================================================"
echo " [2/4] Android 에뮬레이터 백그라운드 실행"
echo "       AVD: ${AVD_NAME}"
echo "================================================================"

"${ANDROID_HOME}/emulator/emulator" \
    -avd "${AVD_NAME}" \
    -no-window \
    -no-audio \
    -no-boot-anim \
    -no-snapshot-save \
    -gpu swiftshader_indirect \
    -memory 2048 \
    -cores 2 \
    > "${EMU_LOG}" 2>&1 &

EMU_PID=$!
echo "${EMU_PID}" > "${PID_FILE}"
echo "   → 에뮬레이터 PID: ${EMU_PID}  (로그: ${EMU_LOG})"

# ─────────────────────────────────────────────────────────────────
echo "================================================================"
echo " [3/4] ADB 연결 대기 (최대 ${ADB_WAIT_TIMEOUT}초)"
echo "================================================================"

"${ANDROID_HOME}/platform-tools/adb" start-server > /dev/null 2>&1

ELAPSED=0
INTERVAL=5
DEVICE_SERIAL=""

while [[ ${ELAPSED} -lt ${ADB_WAIT_TIMEOUT} ]]; do
    # 부팅 완료 여부 확인
    BOOT_STATUS=$(
        "${ANDROID_HOME}/platform-tools/adb" -e \
            shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true
    )
    if [[ "${BOOT_STATUS}" == "1" ]]; then
        DEVICE_SERIAL=$(
            "${ANDROID_HOME}/platform-tools/adb" devices \
            | awk '/emulator/{print $1; exit}'
        )
        echo "   → 에뮬레이터 부팅 완료! 시리얼: ${DEVICE_SERIAL}  (경과: ${ELAPSED}s)"
        break
    fi

    printf "   기다리는 중... %ds / %ds\r" "${ELAPSED}" "${ADB_WAIT_TIMEOUT}"
    sleep "${INTERVAL}"
    ELAPSED=$((ELAPSED + INTERVAL))
done

if [[ -z "${DEVICE_SERIAL}" ]]; then
    echo ""
    echo "⚠️  타임아웃: 에뮬레이터가 ${ADB_WAIT_TIMEOUT}초 내에 부팅되지 않았습니다."
    echo "   로그를 확인하세요: ${EMU_LOG}"
    exit 1
fi

# ─────────────────────────────────────────────────────────────────
echo "================================================================"
echo " [4/4] 상태 확인"
echo "================================================================"

echo "   ADB 장치 목록:"
"${ANDROID_HOME}/platform-tools/adb" devices

echo ""
echo "✅ 에뮬레이터 실행 완료"
echo "   DISPLAY     : ${DISPLAY}"
echo "   Xvfb PID    : ${XVFB_PID}"
echo "   에뮬레이터 PID: ${EMU_PID}"
echo "   ADB 시리얼  : ${DEVICE_SERIAL}"
echo "   에뮬레이터 로그: ${EMU_LOG}"
echo ""
echo "▶  에뮬레이터 종료 방법:"
echo "   kill \$(cat ${PID_FILE})"
echo ""
echo "▶  다음 단계: bash 04_setup_ws_scrcpy.sh"

# PID 저장 (종료 스크립트용)
echo "${XVFB_PID}" >> "${PID_FILE}"
