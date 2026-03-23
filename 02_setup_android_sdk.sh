#!/usr/bin/env bash
# =============================================================================
# 02_setup_android_sdk.sh
# 역할: sudo 없이 홈 디렉토리에 Android Command Line Tools 설치 후
#       sdkmanager / avdmanager 로 Pixel 기반 에뮬레이터 이미지 & AVD 생성
# 실행: bash 02_setup_android_sdk.sh
# =============================================================================
set -euo pipefail

# ─────────────────────────────────────────────────────────────────
# 설정값 (필요 시 수정)
# ─────────────────────────────────────────────────────────────────
ANDROID_HOME="${HOME}/.android/sdk"
CMDLINE_TOOLS_URL="https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
CMDLINE_TOOLS_ZIP="${HOME}/.android/cmdline-tools.zip"

# 에뮬레이터 이미지 설정
API_LEVEL="34"                       # Android 14
ABI="x86_64"                         # 서버 x86_64 환경
SYS_IMAGE="system-images;android-${API_LEVEL};google_apis;${ABI}"
AVD_NAME="Pixel6_API${API_LEVEL}"
DEVICE_PROFILE="pixel_6"             # avdmanager 내장 디바이스

# ─────────────────────────────────────────────────────────────────
echo "================================================================"
echo " [1/5] 디렉토리 생성"
echo "================================================================"
mkdir -p "${HOME}/.android/sdk"
mkdir -p "${HOME}/.android/avd"

# ─────────────────────────────────────────────────────────────────
echo "================================================================"
echo " [2/5] Android Command Line Tools 다운로드 & 설치"
echo "================================================================"
if [[ ! -f "${CMDLINE_TOOLS_ZIP}" ]]; then
    echo "   → ${CMDLINE_TOOLS_URL}"
    wget -q --show-progress -O "${CMDLINE_TOOLS_ZIP}" "${CMDLINE_TOOLS_URL}"
else
    echo "   → 이미 다운로드됨, 스킵"
fi

# cmdline-tools 압축 해제
# 표준 설치 경로: $ANDROID_HOME/cmdline-tools/latest/
CMDLINE_TOOLS_DIR="${ANDROID_HOME}/cmdline-tools"
mkdir -p "${CMDLINE_TOOLS_DIR}"

# 이미 압축 해제된 경우 스킵
if [[ ! -f "${CMDLINE_TOOLS_DIR}/latest/bin/sdkmanager" ]]; then
    echo "   → 압축 해제 중..."
    TMP_DIR=$(mktemp -d)
    unzip -q "${CMDLINE_TOOLS_ZIP}" -d "${TMP_DIR}"
    # zip 내부 최상위 폴더 이름이 'cmdline-tools' 이므로 latest 로 이동
    mv "${TMP_DIR}/cmdline-tools" "${CMDLINE_TOOLS_DIR}/latest"
    rm -rf "${TMP_DIR}"
else
    echo "   → 이미 설치됨, 스킵"
fi

# ─────────────────────────────────────────────────────────────────
echo "================================================================"
echo " [3/5] 환경 변수 설정 (~/.bashrc 에 추가)"
echo "================================================================"

EXPORT_BLOCK='
# ── Android SDK (자동 추가) ──────────────────────────────────────
export ANDROID_HOME="${HOME}/.android/sdk"
export ANDROID_AVD_HOME="${HOME}/.android/avd"
export PATH="${ANDROID_HOME}/cmdline-tools/latest/bin:${PATH}"
export PATH="${ANDROID_HOME}/platform-tools:${PATH}"
export PATH="${ANDROID_HOME}/emulator:${PATH}"
# ────────────────────────────────────────────────────────────────
'

# 중복 추가 방지
if ! grep -q "ANDROID_HOME" "${HOME}/.bashrc" 2>/dev/null; then
    echo "${EXPORT_BLOCK}" >> "${HOME}/.bashrc"
    echo "   → ~/.bashrc 에 PATH 추가 완료"
else
    echo "   → ~/.bashrc 에 이미 설정됨, 스킵"
fi

# 현재 세션에도 즉시 적용
export ANDROID_HOME="${HOME}/.android/sdk"
export ANDROID_AVD_HOME="${HOME}/.android/avd"
export PATH="${ANDROID_HOME}/cmdline-tools/latest/bin:${PATH}"
export PATH="${ANDROID_HOME}/platform-tools:${PATH}"
export PATH="${ANDROID_HOME}/emulator:${PATH}"

# JAVA_HOME 명시 (sdkmanager 가 JDK를 못 찾을 경우 대비)
if [[ -z "${JAVA_HOME:-}" ]]; then
    JAVA_HOME=$(dirname "$(dirname "$(readlink -f "$(which java)")")")
    export JAVA_HOME
    echo "   → JAVA_HOME 자동 설정: ${JAVA_HOME}"
fi

# ─────────────────────────────────────────────────────────────────
echo "================================================================"
echo " [4/5] SDK 패키지 설치 (sdkmanager)"
echo "       - platform-tools, emulator, system-image"
echo "   ※ 라이선스 수락 및 다운로드 중... (수 분 소요, 커서 깜빡임 정상)"
echo "================================================================"

# 라이선스 자동 수락
# yes | 방식은 JVM stdin 처리 문제로 hang 발생 → printf + 파이프 방식으로 대체
# sdkmanager 는 라이선스마다 'y\n' 을 요구 → 충분한 수(30개)를 미리 넣어줌
printf 'y\n%.0s' {1..30} | sdkmanager --licenses 2>&1 \
    | grep -v "^$" | grep -v "^---" || true
echo "   → 라이선스 수락 완료"

# SDK 패키지별 설치 + 설치 후 바이너리 존재 검증
# ※ | tail 파이프 금지: sdkmanager 실패 exit code가 파이프에 묻혀 무시됨

sdk_install() {
    local pkg="$1"
    local check_path="${2:-}"
    echo "   → [설치] ${pkg}"
    # tee 로 실시간 출력 + 로그 저장, sdkmanager 실패 시 즉시 abort
    sdkmanager --verbose "${pkg}" 2>&1 | tee -a "${LOG_DIR}/sdkmanager.log"
    if [[ -n "${check_path}" && ! -e "${check_path}" ]]; then
        echo "❌ 설치 후 파일이 없습니다: ${check_path}"
        echo "   sdkmanager 로그: ${LOG_DIR}/sdkmanager.log"
        exit 1
    fi
    echo "   → [완료] ${pkg}"
}

mkdir -p "${LOG_DIR}"

sdk_install "platform-tools" \
    "${ANDROID_HOME}/platform-tools/adb"

sdk_install "emulator" \
    "${ANDROID_HOME}/emulator/emulator"

sdk_install "platforms;android-${API_LEVEL}" \
    "${ANDROID_HOME}/platforms/android-${API_LEVEL}"

echo "   → system-image 설치 중... (가장 오래 걸림, ~1-2 GB)"
sdk_install "${SYS_IMAGE}" \
    "${ANDROID_HOME}/system-images/android-${API_LEVEL}/google_apis/${ABI}/system.img"

echo "   → SDK 설치 완료"

# ─────────────────────────────────────────────────────────────────
echo "================================================================"
echo " [5/5] AVD 생성 (avdmanager)"
echo "       이름: ${AVD_NAME}  /  디바이스: ${DEVICE_PROFILE}"
echo "================================================================"

# 이미 존재하면 스킵
if avdmanager list avd | grep -q "Name: ${AVD_NAME}"; then
    echo "   → AVD '${AVD_NAME}' 이미 존재함, 스킵"
else
    echo "no" | avdmanager create avd \
        --name "${AVD_NAME}" \
        --package "${SYS_IMAGE}" \
        --device "${DEVICE_PROFILE}" \
        --force
    echo "   → AVD '${AVD_NAME}' 생성 완료"
fi

# 하드웨어 가속(소프트웨어 렌더링) 설정 패치
AVD_CONFIG="${HOME}/.android/avd/${AVD_NAME}.avd/config.ini"
if [[ -f "${AVD_CONFIG}" ]]; then
    # GPU 소프트웨어 렌더링 (서버 환경에 필수)
    sed -i 's/^hw.gpu.enabled=.*/hw.gpu.enabled=no/' "${AVD_CONFIG}" 2>/dev/null || \
        echo "hw.gpu.enabled=no" >> "${AVD_CONFIG}"
    sed -i 's/^hw.gpu.mode=.*/hw.gpu.mode=swiftshader_indirect/' "${AVD_CONFIG}" 2>/dev/null || \
        echo "hw.gpu.mode=swiftshader_indirect" >> "${AVD_CONFIG}"
    echo "   → config.ini GPU 소프트웨어 렌더링 설정 완료"
fi

echo ""
echo "✅ Android SDK 및 AVD 설정 완료"
echo "   AVD 목록:"
avdmanager list avd | grep "Name:" || true
echo ""
echo "▶  다음 단계: bash 03_run_emulator.sh"
