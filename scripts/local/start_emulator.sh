#!/usr/bin/env bash
# =============================================================================
# scripts/local/start_emulator.sh
# 역할: 로컬 PC에서 Android 에뮬레이터 실행 (KVM/no-KVM 모두 지원)
#
# 사용법:
#   bash scripts/local/start_emulator.sh                          # x86_64 AVD (KVM 필요)
#   AVD_ARCH=arm64-v8a bash scripts/local/start_emulator.sh      # ARM64 AVD (KVM 불필요)
#   AVD_NAME=MyDevice bash scripts/local/start_emulator.sh       # 직접 AVD 이름 지정
#
# KVM 없는 환경:
#   x86_64 AVD → 실행 불가 (에러)
#   arm64-v8a AVD → QEMU 에뮬레이션으로 실행 가능 (느리지만 동작)
#   → ARM64 AVD 생성: AVD_ARCH=arm64-v8a bash 02_setup_android_sdk.sh
# =============================================================================
set -euo pipefail

# ─── 설정값 ───────────────────────────────────────────────────────────────────
AVD_ARCH="${AVD_ARCH:-x86_64}"
AVD_ARCH_SAFE="${AVD_ARCH//-/_}"                               # arm64-v8a → arm64_v8a
AVD_NAME="${AVD_NAME:-Pixel6_API34_${AVD_ARCH_SAFE}}"
ANDROID_HOME="${ANDROID_HOME:-${HOME}/.android/sdk}"
EMULATOR_BIN="${ANDROID_HOME}/emulator/emulator"

# adb: 시스템에 있으면 사용, 없으면 SDK 내부 경로
if command -v adb &>/dev/null; then
    ADB_BIN="adb"
else
    ADB_BIN="${ANDROID_HOME}/platform-tools/adb"
fi

# ARM64 이면 부팅이 더 오래 걸림 (QEMU 에뮬레이션)
if [[ "${AVD_ARCH}" == "arm64-v8a" ]]; then
    BOOT_TIMEOUT="${BOOT_TIMEOUT:-600}"
else
    BOOT_TIMEOUT="${BOOT_TIMEOUT:-300}"
fi
# ─────────────────────────────────────────────────────────────────────────────

echo "================================================================"
echo " [1/4] 환경 확인"
echo "================================================================"
echo "   → AVD_ARCH: ${AVD_ARCH}"
echo "   → AVD_NAME: ${AVD_NAME}"

# ── KVM 확인 ──────────────────────────────────────────────────────────────────
HAS_KVM=false
if [[ -e /dev/kvm ]]; then
    HAS_KVM=true
    echo "   ✅ KVM 사용 가능 (/dev/kvm)"
else
    echo "   ⚠️  KVM 없음 (/dev/kvm 미발견)"
fi

# ── KVM + ABI 조합 검사 ────────────────────────────────────────────────────────
if [[ "${AVD_ARCH}" == "x86_64" ]] && [[ "${HAS_KVM}" == "false" ]]; then
    echo ""
    echo "   ❌ x86_64 에뮬레이터는 KVM 없이 실행 불가합니다."
    echo ""
    echo "   ── 해결책 1: KVM 활성화 ──────────────────────────────────────"
    echo "   BIOS에서 VT-x (Intel) 또는 AMD-V 활성화 후 재부팅"
    echo "   재부팅 후 커널 모듈 로드:"
    echo "     sudo modprobe kvm_intel   # Intel CPU"
    echo "     sudo modprobe kvm_amd    # AMD CPU"
    echo "   VM 환경(클라우드/WSL)이라면 nested virtualization 활성화 필요"
    echo ""
    echo "   ── 해결책 2: ARM64 이미지 사용 (권장, KVM 불필요) ────────────"
    echo "   ARM64 AVD를 생성하고 실행하세요 (QEMU 에뮬레이션, 느리지만 동작):"
    echo ""
    echo "     # 1. ARM64 시스템 이미지 다운로드 + AVD 생성 (~3GB)"
    echo "     AVD_ARCH=arm64-v8a bash 02_setup_android_sdk.sh"
    echo ""
    echo "     # 2. ARM64 에뮬레이터 실행"
    echo "     AVD_ARCH=arm64-v8a bash scripts/local/start_emulator.sh"
    echo ""
    echo "   ── 참고 ───────────────────────────────────────────────────────"
    echo "   ARM64 에뮬레이터는 x86_64 대비 5~10배 느립니다."
    echo "   AppAgent 실험에서 VLM 추론(수 초)이 병목이므로 실용적으로 허용됩니다."
    exit 1
fi

# GPU 모드 결정
if [[ "${HAS_KVM}" == "true" ]] && [[ "${AVD_ARCH}" == "x86_64" ]]; then
    GPU_MODE="${EMULATOR_GPU:-host}"
    echo "   ✅ 모드: x86_64 + KVM (빠름)"
else
    GPU_MODE="swiftshader_indirect"
    echo "   ℹ️  모드: ${AVD_ARCH} + QEMU 에뮬레이션 (느림, KVM 불필요)"
    echo "      부팅까지 최대 ${BOOT_TIMEOUT}초 소요될 수 있습니다."
fi

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
    echo "   AVD 생성:"
    echo "     AVD_ARCH=${AVD_ARCH} bash 02_setup_android_sdk.sh"
    exit 1
fi

echo "================================================================"
echo " [3/4] 에뮬레이터 실행"
echo "================================================================"

# 이미 실행 중인 에뮬레이터 확인
EXISTING=$(${ADB_BIN} devices 2>/dev/null | grep "emulator" | grep "device" | head -1 || true)
if [[ -n "${EXISTING}" ]]; then
    echo "   ℹ️  이미 실행 중인 에뮬레이터 발견: ${EXISTING}"
    echo "   기존 에뮬레이터를 사용하거나, 종료 후 재실행하세요."
    echo "   종료 명령: ${ADB_BIN} emu kill"
    echo ""
    echo "   현재 ADB 기기 목록:"
    ${ADB_BIN} devices | tail -n +2 | grep -v "^$" | awk '{printf "     %s\n", $0}'
    exit 0
fi

export ANDROID_HOME="${ANDROID_HOME}"
export PATH="${ANDROID_HOME}/platform-tools:${PATH}"

LOG_DIR="${HOME}/.android/logs"
mkdir -p "${LOG_DIR}"
EMU_LOG="${LOG_DIR}/emulator_local.log"

echo "   → AVD    : ${AVD_NAME}"
echo "   → ABI    : ${AVD_ARCH}"
echo "   → GPU    : ${GPU_MODE}"
echo "   → 로그   : ${EMU_LOG}"
echo "   → 백그라운드 실행 중..."

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

# 첫 10초는 프로세스 안정화 대기
sleep 5

while [[ ${ELAPSED} -lt ${BOOT_TIMEOUT} ]]; do
    # 프로세스 생존 확인
    if ! kill -0 "${EMU_PID}" 2>/dev/null; then
        echo ""
        echo "   ❌ 에뮬레이터 프로세스가 종료되었습니다. 로그:"
        echo "   ─────────────────────────────────────────────────"
        tail -25 "${EMU_LOG}" | awk '{printf "   %s\n", $0}'
        echo "   ─────────────────────────────────────────────────"
        echo ""
        echo "   전체 로그: tail -f ${EMU_LOG}"
        exit 1
    fi

    BOOT_STATUS=$(${ADB_BIN} shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)
    if [[ "${BOOT_STATUS}" == "1" ]]; then
        echo "   ✅ 부팅 완료! (${ELAPSED}초 소요)"
        break
    fi

    printf "   ... 부팅 대기 중 (%d/%d초) \r" "${ELAPSED}" "${BOOT_TIMEOUT}"
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
