#!/usr/bin/env bash
# =============================================================================
# scripts/local/connect_device.sh
# 역할: 실제 Android 기기를 ADB over TCP로 연결 (KVM 없는 환경 대안)
#
# 사용법:
#   bash scripts/local/connect_device.sh --help
#   bash scripts/local/connect_device.sh                     # 대화형 모드
#   DEVICE_IP=192.168.1.100 bash scripts/local/connect_device.sh
#   DEVICE_IP=192.168.1.100 DEVICE_PORT=5555 bash scripts/local/connect_device.sh
#
# 사전 조건 (기기에서):
#   1) 설정 → 개발자 옵션 → USB 디버깅 활성화
#   2) 설정 → 개발자 옵션 → 무선 디버깅 활성화 (Android 11+)
#      또는 USB 연결 후: adb tcpip 5555
# =============================================================================
set -euo pipefail

# ─── 도움말 ──────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<'EOF'
================================================================
 connect_device.sh — 실제 Android 기기 ADB TCP 연결 도우미
================================================================

KVM 없는 환경에서 실제 Android 기기로 실험을 진행하는 방법입니다.

── 방법 1: USB 연결 후 TCP 전환 (가장 간단) ────────────────────
  1. USB 케이블로 기기를 PC에 연결
  2. 기기에서 "USB 디버깅 허용" 팝업 → 허용
  3. TCP 모드 전환:
       adb tcpip 5555
  4. USB 케이블 제거
  5. 이 스크립트 실행:
       DEVICE_IP=<기기IP> bash scripts/local/connect_device.sh

── 방법 2: 무선 디버깅 (Android 11+) ──────────────────────────
  1. 설정 → 개발자 옵션 → 무선 디버깅 활성화
  2. "페어링 코드로 기기 페어링" 탭 → IP:Port와 코드 확인
  3. 페어링:
       adb pair <IP:페어링포트> <코드>
  4. 연결:
       adb connect <IP:5555>

── 기기 IP 확인 방법 ────────────────────────────────────────────
  • 설정 → Wi-Fi → 연결된 네트워크 → IP 주소
  • 또는: adb shell ip addr show wlan0 | grep 'inet '

── 이 스크립트 사용법 ───────────────────────────────────────────
  DEVICE_IP=192.168.1.100 bash scripts/local/connect_device.sh
  DEVICE_IP=192.168.1.100 DEVICE_PORT=5555 bash scripts/local/connect_device.sh

EOF
    exit 0
fi

# ─── 설정값 ──────────────────────────────────────────────────────────────────
DEVICE_IP="${DEVICE_IP:-}"
DEVICE_PORT="${DEVICE_PORT:-5555}"
ANDROID_HOME="${ANDROID_HOME:-${HOME}/.android/sdk}"
CONNECT_TIMEOUT=10

if command -v adb &>/dev/null; then
    ADB_BIN="adb"
else
    ADB_BIN="${ANDROID_HOME}/platform-tools/adb"
fi
# ─────────────────────────────────────────────────────────────────────────────

echo "================================================================"
echo " connect_device.sh — Android 기기 ADB TCP 연결"
echo "================================================================"
echo ""

# adb 존재 확인
if ! command -v "${ADB_BIN}" &>/dev/null; then
    echo "   ❌ adb를 찾을 수 없습니다: ${ADB_BIN}"
    echo "      Android SDK 설치: bash 02_setup_android_sdk.sh"
    exit 1
fi
echo "   ✅ adb: $(command -v "${ADB_BIN}" 2>/dev/null || echo "${ADB_BIN}")"

# ─── IP 입력 (환경변수 없으면 대화형) ────────────────────────────────────────
if [[ -z "${DEVICE_IP}" ]]; then
    echo ""
    echo "   기기 IP 주소를 입력하세요."
    echo "   (기기: 설정 → Wi-Fi → 연결된 네트워크 → IP 주소)"
    echo ""
    read -r -p "   DEVICE_IP: " DEVICE_IP
    if [[ -z "${DEVICE_IP}" ]]; then
        echo "   ❌ IP를 입력하지 않았습니다. 종료."
        exit 1
    fi
fi

TARGET="${DEVICE_IP}:${DEVICE_PORT}"
echo ""
echo "   → 연결 대상: ${TARGET}"
echo ""

# ─── 기존 연결 확인 ──────────────────────────────────────────────────────────
EXISTING=$(${ADB_BIN} devices 2>/dev/null | grep "${DEVICE_IP}" | grep "device$" || true)
if [[ -n "${EXISTING}" ]]; then
    echo "   ℹ️  이미 연결됨: ${EXISTING}"
    echo ""
    echo "   연결된 ADB 기기:"
    ${ADB_BIN} devices | tail -n +2 | grep -v "^$" | awk '{printf "     %s\n", $0}'
    exit 0
fi

# ─── adb connect ─────────────────────────────────────────────────────────────
echo "   → adb connect ${TARGET} ..."
CONNECT_RESULT=$(${ADB_BIN} connect "${TARGET}" 2>&1 || true)
echo "   → 결과: ${CONNECT_RESULT}"

if echo "${CONNECT_RESULT}" | grep -qE "^connected to|already connected"; then
    echo ""
    echo "   ✅ 연결 성공!"
else
    echo ""
    echo "   ❌ 연결 실패. 확인 사항:"
    echo ""
    echo "   1) 기기와 PC가 같은 Wi-Fi 네트워크에 있는지 확인"
    echo "   2) 기기에서 USB 디버깅이 활성화되어 있는지 확인"
    echo "      설정 → 개발자 옵션 → USB 디버깅"
    echo "   3) TCP 모드가 활성화되어 있는지 확인"
    echo "      USB 연결 후: adb tcpip 5555"
    echo "   4) 방화벽이 포트 ${DEVICE_PORT}를 차단하지 않는지 확인"
    echo "      기기에서: adb shell getprop service.adb.tcp.port"
    echo ""
    echo "   자세한 도움말: bash scripts/local/connect_device.sh --help"
    exit 1
fi

# ─── 부팅 완료 대기 ──────────────────────────────────────────────────────────
echo ""
echo "   → 기기 부팅 상태 확인 중..."
BOOT_STATUS=$(${ADB_BIN} -s "${TARGET}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)
if [[ "${BOOT_STATUS}" == "1" ]]; then
    echo "   ✅ 기기 준비 완료"
else
    echo "   ⚠️  부팅 미완료 상태 (sys.boot_completed=${BOOT_STATUS:-<없음>})"
    echo "      잠금 화면 해제 후 다시 시도하세요."
fi

# ─── 결과 출력 ───────────────────────────────────────────────────────────────
echo ""
echo "================================================================"
echo " ✅ ADB 연결 완료"
echo "================================================================"
echo ""
echo "  연결된 ADB 기기:"
${ADB_BIN} devices | tail -n +2 | grep -v "^$" | awk '{printf "    %s\n", $0}'
echo ""
echo "  다음 단계:"
echo "    SERVER_IP=\"<원격서버IP>\" bash scripts/local/check_environment.sh"
echo "    SERVER_IP=\"<원격서버IP>\" bash 07_local_setup.sh"
echo ""
echo "  연결 해제:"
echo "    ${ADB_BIN} disconnect ${TARGET}"
