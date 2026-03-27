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
LOG_DIR="${HOME}/.android/logs"      # ← sdk_install 함수에서 참조하므로 반드시 상단에 정의

# 에뮬레이터 이미지 설정
# ⚠️ x86_64 전용: Android QEMU2는 크로스 아키텍처를 지원하지 않습니다.
#   x86_64 호스트에서 x86_64 이미지만 실행 가능.
#   x86_64 이미지 실행에는 KVM이 필수입니다 (/dev/kvm 필요).
#   arm64-v8a 이미지는 x86_64 호스트에서 실행 불가 (QEMU2 크로스 아키텍처 미지원).
API_LEVEL="34"
ABI="x86_64"
SYS_IMAGE="system-images;android-${API_LEVEL};google_apis;${ABI}"
AVD_NAME="Pixel6_API${API_LEVEL}_x86_64"
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
echo " [4/5] SDK 패키지 설치"
echo "================================================================"

BUNDLE_FILE="${HOME}/.android/android-sdk-bundle.tar.gz"

if [[ -f "${BUNDLE_FILE}" ]]; then
    # ── 오프라인 경로 ─────────────────────────────────────────────
    echo "   → 오프라인 번들 발견: ${BUNDLE_FILE}"
    echo "   → 번들 크기: $(du -sh "${BUNDLE_FILE}" | cut -f1)"
    echo "   → 추출 중... (수 분 소요)"

    mkdir -p "${LOG_DIR}"
    tar -xzf "${BUNDLE_FILE}" -C "${HOME}/.android/" 2>&1 \
        | tee -a "${LOG_DIR}/bundle_extract.log"

    echo "   → 추출 완료"

    # 추출 후 바이너리 검증
    CHECKS=(
        "${ANDROID_HOME}/platform-tools/adb:adb"
        "${ANDROID_HOME}/emulator/emulator:emulator"
        "${ANDROID_HOME}/system-images/android-${API_LEVEL}/google_apis/${ABI}/system.img:system.img"
    )
    INSTALL_OK=true
    for entry in "${CHECKS[@]}"; do
        local_path="${entry%%:*}"
        label="${entry##*:}"
        if [[ -e "${local_path}" ]]; then
            echo "   ✓ ${label}"
        else
            echo "   ✗ 없음: ${local_path}"
            INSTALL_OK=false
        fi
    done

    if [[ "${INSTALL_OK}" == "false" ]]; then
        echo "❌ 번들 추출 후 일부 파일이 없습니다."
        echo "   → 번들이 올바르게 생성되었는지 확인하세요:"
        echo "     로컬 머신에서: bash 02a_prepare_sdk_offline.sh"
        exit 1
    fi

    # 공간 절약을 위해 번들 삭제 여부 선택
    echo ""
    echo "   번들 파일을 삭제하시겠습니까? (공간 절약)"
    echo "   삭제하려면 y, 유지하려면 다른 키를 누르세요 [y/N]"
    read -r -t 10 REMOVE_BUNDLE || REMOVE_BUNDLE="n"
    if [[ "${REMOVE_BUNDLE,,}" == "y" ]]; then
        rm -f "${BUNDLE_FILE}"
        echo "   → 번들 삭제 완료"
    else
        echo "   → 번들 유지: ${BUNDLE_FILE}"
    fi

else
    # ── 온라인 경로 (sdkmanager) ──────────────────────────────────
    echo "   ※ 라이선스 수락 및 다운로드 중... (수 분 소요, 커서 깜빡임 정상)"

    printf 'y\n%.0s' {1..30} | sdkmanager --licenses 2>&1 \
        | grep -v "^$" | grep -v "^---" || \
    printf 'y\n%.0s' {1..30} | sdkmanager --no_https --licenses 2>&1 \
        | grep -v "^$" | grep -v "^---" || true
    echo "   → 라이선스 수락 완료"

    sdk_install() {
        local pkg="$1"
        local check_path="${2:-}"
        local sdklog="${LOG_DIR}/sdkmanager.log"
        echo "   → [설치] ${pkg}"

        set +e
        sdkmanager --verbose "${pkg}" 2>&1 | tee -a "${sdklog}"
        local rc="${PIPESTATUS[0]}"
        set -e

        if [[ "${rc}" -ne 0 ]] || [[ -n "${check_path}" && ! -e "${check_path}" ]]; then
            echo "   ⚠ HTTPS 시도 실패(rc=${rc}). --no_https 로 재시도..."
            set +e
            sdkmanager --verbose --no_https "${pkg}" 2>&1 | tee -a "${sdklog}"
            rc="${PIPESTATUS[0]}"
            set -e
        fi

        if [[ "${rc}" -ne 0 ]]; then
            echo ""
            echo "❌ sdkmanager 로 패키지를 설치할 수 없습니다: ${pkg}"
            echo "   원인: dl.google.com 에 대한 네트워크 접근이 차단된 것으로 보입니다."
            echo ""
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            echo "  해결 방법: 오프라인 번들 사용"
            echo ""
            echo "  1. 인터넷이 되는 로컬 머신(Mac/Linux)에서 실행:"
            echo "     bash 02a_prepare_sdk_offline.sh"
            echo ""
            echo "  2. 생성된 번들을 이 서버로 전송:"
            echo "     scp ~/android-sdk-bundle.tar.gz <사용자>@<서버IP>:~/.android/"
            echo ""
            echo "  3. 이 스크립트를 다시 실행:"
            echo "     bash 02_setup_android_sdk.sh"
            echo "     (번들 파일을 자동 감지하여 오프라인 설치합니다)"
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            exit 1
        fi

        if [[ -n "${check_path}" && ! -e "${check_path}" ]]; then
            echo "❌ 설치 후 파일이 없습니다: ${check_path}"
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

    echo "   → system-image 설치 중... (가장 오래 걸림, ~2-4 GB)"
    echo "   → ABI: ${ABI} (KVM 필수 — /dev/kvm 없으면 에뮬레이터 실행 불가)"
    sdk_install "${SYS_IMAGE}" \
        "${ANDROID_HOME}/system-images/android-${API_LEVEL}/google_apis/${ABI}/system.img"
fi

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

# GPU 설정 패치
# 기본값: host (로컬 PC KVM+GPU 가속용)
# 서버 헤드리스 환경이 필요하면: AVD_GPU_MODE=swiftshader_indirect bash 02_setup_android_sdk.sh
AVD_GPU_MODE="${AVD_GPU_MODE:-host}"
AVD_CONFIG="${HOME}/.android/avd/${AVD_NAME}.avd/config.ini"
if [[ -f "${AVD_CONFIG}" ]]; then
    if [[ "${AVD_GPU_MODE}" == "host" ]]; then
        # 로컬 PC (KVM + GPU 가속) — Android Studio 에뮬레이터와 동일한 설정
        sed -i 's/^hw.gpu.enabled=.*/hw.gpu.enabled=yes/' "${AVD_CONFIG}" 2>/dev/null || \
            echo "hw.gpu.enabled=yes" >> "${AVD_CONFIG}"
    else
        sed -i 's/^hw.gpu.enabled=.*/hw.gpu.enabled=no/' "${AVD_CONFIG}" 2>/dev/null || \
            echo "hw.gpu.enabled=no" >> "${AVD_CONFIG}"
    fi
    sed -i "s/^hw.gpu.mode=.*/hw.gpu.mode=${AVD_GPU_MODE}/" "${AVD_CONFIG}" 2>/dev/null || \
        echo "hw.gpu.mode=${AVD_GPU_MODE}" >> "${AVD_CONFIG}"
    echo "   → config.ini GPU 설정 완료: hw.gpu.mode=${AVD_GPU_MODE}"
fi

echo ""
echo "✅ Android SDK 및 AVD 설정 완료"
echo "   AVD 목록:"
avdmanager list avd | grep "Name:" || true
echo ""
echo "▶  다음 단계: bash scripts/local/start_emulator.sh"
