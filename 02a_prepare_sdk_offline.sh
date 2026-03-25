#!/usr/bin/env bash
# =============================================================================
# 02a_prepare_sdk_offline.sh
# 역할: 【로컬 머신(Mac/Linux)에서 실행】
#       sdkmanager 가 Java 네트워크 차단으로 실패할 경우
#       wget 으로 직접 패키지를 다운로드하여 tarball 로 묶는다.
#
# 실행 방법:
#   1. 이 스크립트를 로컬 머신에서 실행:
#      bash 02a_prepare_sdk_offline.sh
#
#   2. 생성된 번들을 서버로 전송:
#      scp ~/android-sdk-bundle.tar.gz <USER>@<SERVER>:~/.android/
#
#   3. 서버에서 실행:
#      bash 02_setup_android_sdk.sh  (번들 자동 감지)
# =============================================================================
set -euo pipefail

# ─────────────────────────────────────────────────────────────────
# 설정값
# ─────────────────────────────────────────────────────────────────
API_LEVEL="34"
ABI="x86_64"
SYS_IMAGE_PATH="system-images;android-${API_LEVEL};google_apis;${ABI}"

WORK_DIR="${HOME}/.android-sdk-prep"
SDK_DIR="${WORK_DIR}/sdk"
BUNDLE_OUT="${HOME}/android-sdk-bundle.tar.gz"

REPO_BASE="https://dl.google.com/android/repository"
CMDLINE_TOOLS_URL="${REPO_BASE}/commandlinetools-linux-11076708_latest.zip"
CMDLINE_TOOLS_URL_MAC="${REPO_BASE}/commandlinetools-mac-11076708_latest.zip"

# ─────────────────────────────────────────────────────────────────
# 유틸리티 함수
# ─────────────────────────────────────────────────────────────────
detect_os() {
    [[ "$(uname)" == "Darwin" ]] && echo "mac" || echo "linux"
}

ensure_deps() {
    local missing=()
    for cmd in wget unzip python3 tar; do
        command -v "${cmd}" &>/dev/null || missing+=("${cmd}")
    done
    if [[ "${#missing[@]}" -gt 0 ]]; then
        echo "❌ 필요한 명령어 없음: ${missing[*]}"
        [[ "$(detect_os)" == "mac" ]] \
            && echo "   brew install wget python3" \
            || echo "   sudo apt-get install -y wget unzip python3"
        exit 1
    fi
}

# 이미 다운로드된 경우 스킵, 없으면 wget (3회 재시도)
wget_dl() {
    local url="$1" out="$2"
    if [[ -f "${out}" ]]; then
        echo "   → 캐시 사용: $(basename "${out}")"
        return
    fi
    echo "   → wget: ${url}"
    wget -q --show-progress --tries=3 -O "${out}" "${url}"
}

# zip 최상위 디렉토리를 벗겨내고 target 에 설치
smart_extract() {
    local zip="$1" target="$2"
    local tmp
    tmp=$(mktemp -d)
    echo "   → 압축 해제: $(basename "${zip}") → ${target}"
    unzip -q "${zip}" -d "${tmp}"

    local top_items=("${tmp}"/*)
    if [[ ${#top_items[@]} -eq 1 && -d "${top_items[0]}" ]]; then
        # 최상위 디렉토리가 하나 → 그 안의 내용을 target 으로
        mkdir -p "${target}"
        cp -r "${top_items[0]}/." "${target}/"
    else
        mkdir -p "${target}"
        cp -r "${tmp}/." "${target}/"
    fi
    rm -rf "${tmp}"
}

# Android repo XML 에서 패키지 다운로드 URL 추출 (Python)
parse_repo_xml() {
    local xml_file="$1" pkg_path="$2" base_url="$3"
    local host_os="${4:-linux}"

    python3 - "${xml_file}" "${pkg_path}" "${base_url}" "${host_os}" <<'PYEOF'
import xml.etree.ElementTree as ET, sys

xml_file, pkg_path, base_url, host_os = sys.argv[1:]

tree = ET.parse(xml_file)
root = tree.getroot()

for elem in root.iter():
    tag = elem.tag.split('}')[-1]
    if tag != 'remotePackage' or elem.get('path') != pkg_path:
        continue
    for archive in elem.iter():
        if archive.tag.split('}')[-1] != 'archive':
            continue
        host_os_val = url_val = None
        for child in archive:
            cl = child.tag.split('}')[-1]
            if cl == 'host-os':
                host_os_val = child.text
            elif cl == 'complete':
                for cc in child:
                    if cc.tag.split('}')[-1] == 'url':
                        url_val = cc.text
        if url_val and (host_os_val is None or host_os_val == host_os):
            print(base_url + url_val)
            sys.exit(0)

print(f"ERROR: '{pkg_path}' 를 XML 에서 찾을 수 없음", file=sys.stderr)
sys.exit(1)
PYEOF
}

# ─────────────────────────────────────────────────────────────────
# 패키지별 wget 직접 설치 함수
# ─────────────────────────────────────────────────────────────────
wget_install_platform_tools() {
    echo "   [wget] platform-tools"
    local zip="${WORK_DIR}/platform-tools.zip"
    wget_dl "${REPO_BASE}/platform-tools-latest-linux.zip" "${zip}"
    smart_extract "${zip}" "${SDK_DIR}/platform-tools"
    chmod +x "${SDK_DIR}/platform-tools/adb" 2>/dev/null || true
}

wget_install_emulator() {
    echo "   [wget] emulator"
    local repo_xml="${WORK_DIR}/repository2-3.xml"
    wget_dl "${REPO_BASE}/repository2-3.xml" "${repo_xml}"

    local emu_url
    emu_url=$(parse_repo_xml "${repo_xml}" "emulator" "${REPO_BASE}/" "linux")
    echo "   → URL: ${emu_url}"

    local zip="${WORK_DIR}/emulator.zip"
    wget_dl "${emu_url}" "${zip}"
    smart_extract "${zip}" "${SDK_DIR}/emulator"
    chmod +x "${SDK_DIR}/emulator/emulator" 2>/dev/null || true
}

wget_install_sysimg() {
    echo "   [wget] system-images;android-${API_LEVEL};google_apis;${ABI}"
    local sysimg_xml="${WORK_DIR}/sys-img2-3.xml"
    wget_dl "${REPO_BASE}/sys-img/google_apis/sys-img2-3.xml" "${sysimg_xml}"

    local sysimg_url
    sysimg_url=$(parse_repo_xml \
        "${sysimg_xml}" \
        "${SYS_IMAGE_PATH}" \
        "${REPO_BASE}/sys-img/google_apis/")
    echo "   → URL: ${sysimg_url}"

    local zip="${WORK_DIR}/sysimg.zip"
    wget_dl "${sysimg_url}" "${zip}"
    mkdir -p "${SDK_DIR}/system-images/android-${API_LEVEL}/google_apis"
    smart_extract "${zip}" \
        "${SDK_DIR}/system-images/android-${API_LEVEL}/google_apis/${ABI}"
}

# sdkmanager 시도 → 실패 시 wget 폴백
install_package() {
    local label="$1"       # 로그 표시용
    local sdkpkg="$2"      # sdkmanager 패키지 이름
    local check_path="$3"  # 설치 성공 여부 검증 파일
    local wget_fn="$4"     # wget 폴백 함수 이름

    if [[ -e "${check_path}" ]]; then
        echo "   → 이미 설치됨, 스킵: ${label}"
        return
    fi

    echo ""
    echo "── ${label} ──────────────────────────────────────"

    # sdkmanager 시도
    echo "   [시도 1] sdkmanager"
    set +e
    sdkmanager --sdk_root="${SDK_DIR}" "${sdkpkg}" 2>&1 | \
        grep -v "^Warning" | grep -v "^Info: IO" | grep -v "^$" || true
    set -e

    if [[ -e "${check_path}" ]]; then
        echo "   → sdkmanager 성공"
        return
    fi

    # wget 폴백
    echo "   [시도 2] wget 직접 다운로드 (sdkmanager 네트워크 차단으로 실패)"
    "${wget_fn}"

    if [[ ! -e "${check_path}" ]]; then
        echo "❌ wget 폴백도 실패: ${check_path}"
        exit 1
    fi
    echo "   → wget 설치 성공"
}

# ─────────────────────────────────────────────────────────────────
echo "================================================================"
echo " 사전 준비 확인"
echo "================================================================"
ensure_deps

OS=$(detect_os)
echo "   → OS: ${OS}"
echo "   → 작업 디렉토리: ${WORK_DIR}"
echo "   → 번들 출력: ${BUNDLE_OUT}"
mkdir -p "${WORK_DIR}" "${SDK_DIR}"

# ─────────────────────────────────────────────────────────────────
echo ""
echo "================================================================"
echo " [1/5] Android Command Line Tools 설치"
echo "================================================================"
[[ "${OS}" == "mac" ]] && TOOLS_URL="${CMDLINE_TOOLS_URL_MAC}" || TOOLS_URL="${CMDLINE_TOOLS_URL}"

TOOLS_ZIP="${WORK_DIR}/cmdline-tools.zip"
wget_dl "${TOOLS_URL}" "${TOOLS_ZIP}"

if [[ ! -f "${SDK_DIR}/cmdline-tools/latest/bin/sdkmanager" ]]; then
    TMP=$(mktemp -d)
    unzip -q "${TOOLS_ZIP}" -d "${TMP}"
    mkdir -p "${SDK_DIR}/cmdline-tools"
    mv "${TMP}/cmdline-tools" "${SDK_DIR}/cmdline-tools/latest"
    rm -rf "${TMP}"
    echo "   → cmdline-tools 설치 완료"
fi

export ANDROID_HOME="${SDK_DIR}"
export PATH="${SDK_DIR}/cmdline-tools/latest/bin:${PATH}"

if [[ -z "${JAVA_HOME:-}" ]]; then
    if [[ "${OS}" == "mac" ]]; then
        JAVA_HOME=$(/usr/libexec/java_home 2>/dev/null || true)
    else
        JAVA_HOME=$(dirname "$(dirname "$(readlink -f "$(which java)")")")
    fi
    export JAVA_HOME
fi
echo "   → JAVA_HOME: ${JAVA_HOME:-미설정}"

# ─────────────────────────────────────────────────────────────────
echo ""
echo "================================================================"
echo " [2/5] 라이선스 수락"
echo "================================================================"
printf 'y\n%.0s' {1..30} | \
    sdkmanager --sdk_root="${SDK_DIR}" --licenses 2>&1 | \
    grep -v "^$" | grep -v "^---" || true
echo "   → 완료"

# ─────────────────────────────────────────────────────────────────
echo ""
echo "================================================================"
echo " [3/5] SDK 패키지 설치"
echo "       sdkmanager 실패 시 wget 자동 폴백"
echo "================================================================"

install_package \
    "platform-tools" \
    "platform-tools" \
    "${SDK_DIR}/platform-tools/adb" \
    "wget_install_platform_tools"

install_package \
    "emulator" \
    "emulator" \
    "${SDK_DIR}/emulator/emulator" \
    "wget_install_emulator"

install_package \
    "system-image (android-${API_LEVEL} / google_apis / ${ABI})" \
    "${SYS_IMAGE_PATH}" \
    "${SDK_DIR}/system-images/android-${API_LEVEL}/google_apis/${ABI}/system.img" \
    "wget_install_sysimg"

# ─────────────────────────────────────────────────────────────────
echo ""
echo "================================================================"
echo " [4/5] 설치 검증"
echo "================================================================"
ALL_OK=true
for f in \
    "${SDK_DIR}/platform-tools/adb" \
    "${SDK_DIR}/emulator/emulator" \
    "${SDK_DIR}/system-images/android-${API_LEVEL}/google_apis/${ABI}/system.img"
do
    if [[ -e "${f}" ]]; then
        echo "   ✓ ${f}"
    else
        echo "   ✗ 없음: ${f}"
        ALL_OK=false
    fi
done

if [[ "${ALL_OK}" == "false" ]]; then
    echo "❌ 검증 실패"
    exit 1
fi

# ─────────────────────────────────────────────────────────────────
echo ""
echo "================================================================"
echo " [5/5] tarball 생성: ${BUNDLE_OUT}"
echo "================================================================"
[[ -f "${BUNDLE_OUT}" ]] && mv "${BUNDLE_OUT}" "${BUNDLE_OUT}.bak" && \
    echo "   → 기존 번들 백업: ${BUNDLE_OUT}.bak"

tar -czf "${BUNDLE_OUT}" -C "${WORK_DIR}" sdk
echo "   → 번들 크기: $(du -sh "${BUNDLE_OUT}" | cut -f1)"

echo ""
echo "✅ 오프라인 번들 생성 완료!"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  서버로 전송:"
echo "  scp \"${BUNDLE_OUT}\" <사용자>@<서버IP>:~/.android/"
echo ""
echo "  서버에서 실행:"
echo "  bash 02_setup_android_sdk.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
