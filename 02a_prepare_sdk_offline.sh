#!/usr/bin/env bash
# =============================================================================
# 02a_prepare_sdk_offline.sh
# 역할: 【로컬 머신(Mac/Linux)에서 실행】
#       인터넷이 되는 환경에서 Android SDK 패키지를 모두 다운로드한 뒤
#       tarball 로 묶어 서버로 전송할 수 있게 준비한다.
#
# 실행 방법:
#   1. 인터넷이 되는 Mac 또는 Linux 에서 실행:
#      bash 02a_prepare_sdk_offline.sh
#
#   2. 출력된 scp 명령어로 서버에 전송:
#      scp ~/android-sdk-bundle.tar.gz <USER>@<SERVER>:~/.android/
#
#   3. 서버에서 02_setup_android_sdk.sh 를 실행하면
#      번들을 자동으로 감지하여 오프라인 설치합니다.
# =============================================================================
set -euo pipefail

# ─────────────────────────────────────────────────────────────────
# 설정값
# ─────────────────────────────────────────────────────────────────
API_LEVEL="34"
ABI="x86_64"
SYS_IMAGE="system-images;android-${API_LEVEL};google_apis;${ABI}"

WORK_DIR="${HOME}/.android-sdk-prep"
SDK_DIR="${WORK_DIR}/sdk"
BUNDLE_OUT="${HOME}/android-sdk-bundle.tar.gz"

CMDLINE_TOOLS_URL="https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
# macOS 용 (위의 Linux 버전과 다름, OS 감지 후 자동 선택)
CMDLINE_TOOLS_URL_MAC="https://dl.google.com/android/repository/commandlinetools-mac-11076708_latest.zip"

# ─────────────────────────────────────────────────────────────────
detect_os() {
    if [[ "$(uname)" == "Darwin" ]]; then
        echo "mac"
    else
        echo "linux"
    fi
}

ensure_deps() {
    local missing=()
    for cmd in wget unzip java tar; do
        if ! command -v "${cmd}" &>/dev/null; then
            missing+=("${cmd}")
        fi
    done
    if [[ "${#missing[@]}" -gt 0 ]]; then
        echo "❌ 다음 명령어가 없습니다: ${missing[*]}"
        echo ""
        if [[ "$(detect_os)" == "mac" ]]; then
            echo "   Mac 설치 방법:"
            echo "   brew install wget openjdk"
        else
            echo "   Linux 설치 방법:"
            echo "   sudo apt-get install -y wget unzip openjdk-17-jdk"
        fi
        exit 1
    fi
}

# ─────────────────────────────────────────────────────────────────
echo "================================================================"
echo " 사전 준비 확인"
echo "================================================================"
ensure_deps

OS=$(detect_os)
echo "   → 운영체제: ${OS}"
echo "   → 작업 디렉토리: ${WORK_DIR}"
echo "   → 번들 출력: ${BUNDLE_OUT}"
echo ""

# ─────────────────────────────────────────────────────────────────
echo "================================================================"
echo " [1/5] Android Command Line Tools 다운로드"
echo "================================================================"
mkdir -p "${SDK_DIR}/cmdline-tools"

if [[ "${OS}" == "mac" ]]; then
    TOOLS_URL="${CMDLINE_TOOLS_URL_MAC}"
else
    TOOLS_URL="${CMDLINE_TOOLS_URL}"
fi

TOOLS_ZIP="${WORK_DIR}/cmdline-tools.zip"
if [[ ! -f "${TOOLS_ZIP}" ]]; then
    echo "   → 다운로드: ${TOOLS_URL}"
    wget -q --show-progress -O "${TOOLS_ZIP}" "${TOOLS_URL}"
else
    echo "   → 이미 다운로드됨, 스킵"
fi

if [[ ! -f "${SDK_DIR}/cmdline-tools/latest/bin/sdkmanager" ]]; then
    TMP=$(mktemp -d)
    unzip -q "${TOOLS_ZIP}" -d "${TMP}"
    mv "${TMP}/cmdline-tools" "${SDK_DIR}/cmdline-tools/latest"
    rm -rf "${TMP}"
    echo "   → 압축 해제 완료"
fi

export ANDROID_HOME="${SDK_DIR}"
export PATH="${SDK_DIR}/cmdline-tools/latest/bin:${PATH}"

# JAVA_HOME 자동 설정
if [[ -z "${JAVA_HOME:-}" ]]; then
    if [[ "${OS}" == "mac" ]]; then
        JAVA_HOME=$(/usr/libexec/java_home 2>/dev/null || true)
    else
        JAVA_HOME=$(dirname "$(dirname "$(readlink -f "$(which java)")")")
    fi
    export JAVA_HOME
fi
echo "   → JAVA_HOME: ${JAVA_HOME}"

# ─────────────────────────────────────────────────────────────────
echo "================================================================"
echo " [2/5] 라이선스 수락"
echo "================================================================"
printf 'y\n%.0s' {1..30} | sdkmanager --sdk_root="${SDK_DIR}" --licenses 2>&1 \
    | grep -v "^$" | grep -v "^---" || true
echo "   → 라이선스 수락 완료"

# ─────────────────────────────────────────────────────────────────
echo "================================================================"
echo " [3/5] SDK 패키지 다운로드"
echo "       (platform-tools / emulator / platforms / system-image)"
echo "       ※ system-image 는 약 1-2 GB 이므로 시간이 걸립니다"
echo "================================================================"

sdk_dl() {
    local pkg="$1"
    echo "   → 다운로드: ${pkg}"
    sdkmanager --sdk_root="${SDK_DIR}" --verbose "${pkg}" 2>&1 \
        | grep -E "(Downloading|Installing|100%|완료|Done|Error)" || true
    echo "   → 완료: ${pkg}"
}

sdk_dl "platform-tools"
sdk_dl "emulator"
sdk_dl "platforms;android-${API_LEVEL}"
sdk_dl "${SYS_IMAGE}"

# ─────────────────────────────────────────────────────────────────
echo "================================================================"
echo " [4/5] 설치 검증"
echo "================================================================"
CHECKS=(
    "${SDK_DIR}/platform-tools/adb"
    "${SDK_DIR}/emulator/emulator"
    "${SDK_DIR}/system-images/android-${API_LEVEL}/google_apis/${ABI}/system.img"
)

ALL_OK=true
for f in "${CHECKS[@]}"; do
    if [[ -e "${f}" ]]; then
        echo "   ✓ $(basename "${f}"): ${f}"
    else
        echo "   ✗ 없음: ${f}"
        ALL_OK=false
    fi
done

if [[ "${ALL_OK}" == "false" ]]; then
    echo "❌ 일부 파일이 없습니다. 위 출력을 확인하세요."
    exit 1
fi

# ─────────────────────────────────────────────────────────────────
echo "================================================================"
echo " [5/5] tarball 생성: ${BUNDLE_OUT}"
echo "       (완료까지 수 분 소요)"
echo "================================================================"

# 이미 존재하면 백업
if [[ -f "${BUNDLE_OUT}" ]]; then
    mv "${BUNDLE_OUT}" "${BUNDLE_OUT}.bak"
    echo "   → 기존 번들 백업: ${BUNDLE_OUT}.bak"
fi

tar -czf "${BUNDLE_OUT}" -C "${WORK_DIR}" sdk
echo "   → 번들 크기: $(du -sh "${BUNDLE_OUT}" | cut -f1)"

echo ""
echo "✅ 오프라인 번들 생성 완료!"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  다음 단계: 아래 scp 명령으로 서버에 전송하세요"
echo ""
echo "  scp \"${BUNDLE_OUT}\" <사용자명>@<서버IP>:~/.android/"
echo ""
echo "  전송 완료 후 서버에서:"
echo "  bash 02_setup_android_sdk.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
