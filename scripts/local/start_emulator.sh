#!/usr/bin/env bash
# =============================================================================
# scripts/local/start_emulator.sh
# 역할: Android 에뮬레이터 실행 (KVM 필수)
#
# 사용법:
#   bash scripts/local/start_emulator.sh
#   AVD_NAME=MyDevice bash scripts/local/start_emulator.sh
#
# KVM 없는 환경에서는 에뮬레이터 대신 실제 기기 연결을 사용하세요:
#   scripts/local/connect_device.sh
# =============================================================================
set -euo pipefail

# ─── 설정값 ───────────────────────────────────────────────────────────────────
AVD_NAME="${AVD_NAME:-Pixel6_API34_x86_64}"
ANDROID_HOME="${ANDROID_HOME:-${HOME}/.android/sdk}"
EMULATOR_BIN="${ANDROID_HOME}/emulator/emulator"

if command -v adb &>/dev/null; then
    ADB_BIN="adb"
else
    ADB_BIN="${ANDROID_HOME}/platform-tools/adb"
fi

BOOT_TIMEOUT="${BOOT_TIMEOUT:-300}"
# ─────────────────────────────────────────────────────────────────────────────

echo "================================================================"
echo " [1/4] 환경 확인"
echo "================================================================"

# ── KVM 확인 ─────────────────────────────────────────────────────────────────
if [[ ! -e /dev/kvm ]]; then
    echo "   ❌ KVM 없음 (/dev/kvm 미발견)"
    echo ""
    echo "   Android 에뮬레이터(x86_64)는 KVM 없이 실행 불가합니다."
    echo "   ─────────────────────────────────────────────────────────────"
    echo ""
    echo "   ── 해결책 1: KVM 활성화 (이 서버에서) ──────────────────────"
    echo "   BIOS/UEFI에서 VT-x (Intel) 또는 AMD-V 활성화 후 재부팅"
    echo "   재부팅 후 커널 모듈 로드:"
    echo "     sudo modprobe kvm_intel   # Intel CPU"
    echo "     sudo modprobe kvm_amd    # AMD CPU"
    echo "     ls /dev/kvm              # 확인"
    echo ""
    echo "   VM 환경(클라우드 인스턴스)이라면 nested virtualization 확인:"
    echo "     grep -E 'vmx|svm' /proc/cpuinfo | head -1"
    echo "     cat /sys/module/kvm_intel/parameters/nested"
    echo ""
    echo "   ── 해결책 2: 실제 Android 기기 연결 (권장) ─────────────────"
    echo "   KVM 없어도 실제 기기를 ADB over TCP로 연결하면 실험 가능:"
    echo "     bash scripts/local/connect_device.sh --help"
    echo ""
    echo "   ── 해결책 3: 로컬 PC에서 실행 (KVM/Hypervisor 있는 환경) ───"
    echo "   이 스크립트를 KVM이 활성화된 로컬 PC에서 실행하세요."
    echo "   macOS: Android Studio (Hypervisor Framework 내장)"
    echo "   Windows: Android Studio (HAXM 또는 Hyper-V)"
    echo "   Linux + KVM: bash scripts/local/start_emulator.sh"
    echo ""
    echo "   ── 참고: arm64-v8a 이미지는 동작하지 않습니다 ──────────────"
    echo "   Android QEMU2는 크로스 아키텍처를 지원하지 않습니다."
    echo "   x86_64 호스트에서는 x86_64 이미지만 사용 가능하며,"
    echo "   x86_64 이미지 실행에는 KVM이 필수입니다."
    echo ""
    exit 1
fi

echo "   ✅ KVM 사용 가능 (/dev/kvm)"
GPU_MODE="${EMULATOR_GPU:-host}"

# emulator 바이너리 확인
if [[ ! -x "${EMULATOR_BIN}" ]]; then
    echo "   ❌ emulator 바이너리를 찾을 수 없습니다: ${EMULATOR_BIN}"
    echo "      02_setup_android_sdk.sh 를 먼저 실행하세요."
    exit 1
fi
echo "   ✅ emulator: ${EMULATOR_BIN}"

echo "================================================================"
echo " [2/4] AVD 확인"
echo "================================================================"
AVDMANAGER=""
for p in \
    "${ANDROID_HOME}/cmdline-tools/latest/bin/avdmanager" \
    "${ANDROID_HOME}/cmdline-tools/bin/avdmanager"; do
    if [[ -x "${p}" ]]; then AVDMANAGER="${p}"; break; fi
done

AVD_LIST=""
if [[ -n "${AVDMANAGER}" ]]; then
    AVD_LIST=$("${AVDMANAGER}" list avd 2>/dev/null || true)
fi

if echo "${AVD_LIST}" | grep -q "Name: ${AVD_NAME}"; then
    echo "   ✅ AVD 발견: ${AVD_NAME}"
else
    echo "   ❌ AVD '${AVD_NAME}' 를 찾을 수 없습니다."
    echo ""
    echo "   사용 가능한 AVD 목록:"
    if [[ -n "${AVD_LIST}" ]]; then
        echo "${AVD_LIST}" | grep "Name:" | awk '{printf "     %s\n", $0}'
    else
        echo "     (avdmanager 조회 실패)"
    fi
    echo ""
    echo "   AVD 생성: bash 02_setup_android_sdk.sh"
    exit 1
fi

echo "================================================================"
echo " [3/4] 에뮬레이터 실행"
echo "================================================================"

EXISTING=$(${ADB_BIN} devices 2>/dev/null | grep "emulator" | grep "device" | head -1 || true)
if [[ -n "${EXISTING}" ]]; then
    echo "   ℹ️  이미 실행 중인 에뮬레이터 발견: ${EXISTING}"
    echo "   종료 후 재실행하려면: ${ADB_BIN} emu kill"
    ${ADB_BIN} devices | tail -n +2 | grep -v "^$" | awk '{printf "     %s\n", $0}'
    exit 0
fi

export ANDROID_HOME="${ANDROID_HOME}"
export PATH="${ANDROID_HOME}/platform-tools:${PATH}"

LOG_DIR="${HOME}/.android/logs"
mkdir -p "${LOG_DIR}"
EMU_LOG="${LOG_DIR}/emulator_local.log"

echo "   → AVD : ${AVD_NAME}"
echo "   → GPU : ${GPU_MODE}"
echo "   → 로그: ${EMU_LOG}"

nohup "${EMULATOR_BIN}" \
    -avd "${AVD_NAME}" \
    -gpu "${GPU_MODE}" \
    -no-audio \
    -no-snapshot-save \
    > "${EMU_LOG}" 2>&1 &

EMU_PID=$!
echo "${EMU_PID}" > "${LOG_DIR}/emulator_local.pid"
echo "   → PID: ${EMU_PID}"

echo "================================================================"
echo " [4/4] ADB 부팅 대기 (최대 ${BOOT_TIMEOUT}초)"
echo "================================================================"
ELAPSED=0
INTERVAL=5
BOOT_STATUS=""

sleep 5

while [[ ${ELAPSED} -lt ${BOOT_TIMEOUT} ]]; do
    if ! kill -0 "${EMU_PID}" 2>/dev/null; then
        echo ""
        echo "   ❌ 에뮬레이터 프로세스가 종료되었습니다. 로그:"
        echo "   ─────────────────────────────────────────────────"
        tail -25 "${EMU_LOG}" | awk '{printf "   %s\n", $0}'
        echo "   ─────────────────────────────────────────────────"
        exit 1
    fi

    BOOT_STATUS=$(${ADB_BIN} shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)
    if [[ "${BOOT_STATUS}" == "1" ]]; then
        echo "   ✅ 부팅 완료! (${ELAPSED}초 소요)"
        break
    fi

    printf "   ... 부팅 대기 중 (%d/%d초)\r" "${ELAPSED}" "${BOOT_TIMEOUT}"
    sleep ${INTERVAL}
    ELAPSED=$((ELAPSED + INTERVAL))
done
echo ""

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
echo "    ${ADB_BIN} emu kill   또는   kill ${EMU_PID}"
