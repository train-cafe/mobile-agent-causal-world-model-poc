#!/usr/bin/env bash
# =============================================================================
# scripts/local/start_emulator.sh
# 역할: 로컬 PC에서 KVM 가속 Android 에뮬레이터 실행
#
# 사용법:
#   bash scripts/local/start_emulator.sh
#   AVD_NAME=MyDevice bash scripts/local/start_emulator.sh
#
# 전제 조건:
#   - Android SDK + AVD 생성 완료 (02_setup_android_sdk.sh)
#   - 로컬 PC에 KVM 활성화 권장 (/dev/kvm)
# =============================================================================
set -euo pipefail

# ─── 설정값 ───────────────────────────────────────────────────────────────────
AVD_NAME="${AVD_NAME:-Pixel6_API34_x86_64}"
ANDROID_HOME="${ANDROID_HOME:-${HOME}/.android/sdk}"
EMULATOR_BIN="${ANDROID_HOME}/emulator/emulator"
ADB_BIN="${ANDROID_HOME}/platform-tools/adb"

# 시스템 adb가 없으면 SDK 내 adb 사용
if command -v adb &>/dev/null; then
    ADB_BIN="adb"
fi

GPU_MODE="${EMULATOR_GPU:-host}"      # host = GPU 가속, swiftshader_indirect = CPU
BOOT_TIMEOUT="${BOOT_TIMEOUT:-300}"   # 부팅 대기 최대 초 (KVM이면 60~90초면 충분)
# ─────────────────────────────────────────────────────────────────────────────

echo "================================================================"
echo " [1/4] 환경 확인"
echo "================================================================"

# KVM 확인
if [[ -e /dev/kvm ]]; then
    echo "   ✅ KVM 사용 가능 (/dev/kvm)"
else
    echo "   ⚠️  KVM 없음 — 소프트웨어 에뮬레이션으로 실행됩니다 (느릴 수 있음)"
    echo "      KVM 활성화 방법: sudo modprobe kvm_intel (또는 kvm_amd)"
    GPU_MODE="swiftshader_indirect"
fi

# emulator 바이너리 확인
if [[ ! -x "${EMULATOR_BIN}" ]]; then
    echo "   ❌ emulator 바이너리를 찾을 수 없습니다: ${EMULATOR_BIN}"
    echo "      먼저 02_setup_android_sdk.sh 를 실행하세요."
    exit 1
fi
echo "   ✅ emulator: ${EMULATOR_BIN}"

echo "================================================================"
echo " [2/4] AVD 확인"
echo "================================================================"
AVD_LIST=$("${ANDROID_HOME}/cmdline-tools/latest/bin/avdmanager" list avd 2>/dev/null || \
           "${ANDROID_HOME}/cmdline-tools/bin/avdmanager" list avd 2>/dev/null || \
           echo "")

if echo "${AVD_LIST}" | grep -q "Name: ${AVD_NAME}"; then
    echo "   ✅ AVD 발견: ${AVD_NAME}"
else
    echo "   ❌ AVD '${AVD_NAME}' 를 찾을 수 없습니다."
    echo "   사용 가능한 AVD 목록:"
    echo "${AVD_LIST}" | grep "Name:" | awk '{printf "     %s\n", $0}'
    echo ""
    echo "   AVD 생성: bash 02_setup_android_sdk.sh"
    echo "   또는 AVD 이름 지정: AVD_NAME=<이름> bash $0"
    exit 1
fi

echo "================================================================"
echo " [3/4] 에뮬레이터 실행"
echo "================================================================"

# 이미 실행 중인 에뮬레이터 확인
EXISTING=$(${ADB_BIN} devices 2>/dev/null | grep "emulator" | grep "device" | head -1 || true)
if [[ -n "${EXISTING}" ]]; then
    echo "   ℹ️  이미 실행 중인 에뮬레이터 발견: ${EXISTING}"
    echo "   새 에뮬레이터를 시작하려면 기존 에뮬레이터를 종료 후 재실행하세요."
    echo "   종료 명령: adb emu kill"
    echo ""
    echo "   현재 ADB 기기 목록:"
    ${ADB_BIN} devices | tail -n +2 | grep -v "^$" | awk '{printf "     %s\n", $0}'
    exit 0
fi

export ANDROID_HOME="${ANDROID_HOME}"
export PATH="${ANDROID_HOME}/platform-tools:${PATH}"

echo "   → AVD: ${AVD_NAME}"
echo "   → GPU: ${GPU_MODE}"
echo "   → 백그라운드 실행 중..."

LOG_DIR="${HOME}/.android/logs"
mkdir -p "${LOG_DIR}"
EMU_LOG="${LOG_DIR}/emulator_local.log"

nohup "${EMULATOR_BIN}" \
    -avd "${AVD_NAME}" \
    -gpu "${GPU_MODE}" \
    -no-audio \
    -no-snapshot-save \
    > "${EMU_LOG}" 2>&1 &

EMU_PID=$!
echo "${EMU_PID}" > "${LOG_DIR}/emulator_local.pid"
echo "   → PID: ${EMU_PID}"
echo "   → 로그: ${EMU_LOG}"

echo "================================================================"
echo " [4/4] ADB 부팅 대기 (최대 ${BOOT_TIMEOUT}초)"
echo "================================================================"
ELAPSED=0
INTERVAL=5

while [[ ${ELAPSED} -lt ${BOOT_TIMEOUT} ]]; do
    # 프로세스 생존 확인
    if ! kill -0 "${EMU_PID}" 2>/dev/null; then
        echo "   ❌ 에뮬레이터 프로세스가 종료되었습니다. 로그 확인:"
        tail -20 "${EMU_LOG}" | awk '{printf "     %s\n", $0}'
        exit 1
    fi

    BOOT_STATUS=$(${ADB_BIN} shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)
    if [[ "${BOOT_STATUS}" == "1" ]]; then
        echo "   ✅ 부팅 완료! (${ELAPSED}초 소요)"
        break
    fi

    echo "   ... 대기 중 (${ELAPSED}/${BOOT_TIMEOUT}초)"
    sleep ${INTERVAL}
    ELAPSED=$((ELAPSED + INTERVAL))
done

if [[ "${BOOT_STATUS}" != "1" ]]; then
    echo "   ❌ 부팅 타임아웃 (${BOOT_TIMEOUT}초 초과)"
    echo "   로그 확인: tail -f ${EMU_LOG}"
    exit 1
fi

echo ""
echo "================================================================"
echo " ✅ 에뮬레이터 준비 완료"
echo "================================================================"
echo ""
echo "  연결된 ADB 기기:"
${ADB_BIN} devices | tail -n +2 | grep -v "^$" | awk '{printf "    %s\n", $0}'
echo ""
echo "  다음 단계:"
echo "    SERVER_IP=\"<원격서버IP>\" bash scripts/local/check_environment.sh"
echo "    SERVER_IP=\"<원격서버IP>\" bash 07_local_setup.sh"
echo ""
echo "  에뮬레이터 종료:"
echo "    adb emu kill   또는   kill ${EMU_PID}"
