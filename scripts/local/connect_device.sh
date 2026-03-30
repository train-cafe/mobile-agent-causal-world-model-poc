#!/usr/bin/env bash
# =============================================================================
# scripts/local/connect_device.sh
# 역할: 실제 Android 기기를 ADB로 연결 (USB 또는 TCP)
#
# 사용법:
#   bash scripts/local/connect_device.sh              # 자동 감지 (USB 우선)
#   bash scripts/local/connect_device.sh --wifi        # 무선 디버깅
#   bash scripts/local/connect_device.sh --tcp         # TCP 연결 (IP 필요)
#   DEVICE_IP=192.168.1.100 bash scripts/local/connect_device.sh --tcp
# =============================================================================
set -euo pipefail

MODE="${1:-auto}"
DEVICE_IP="${DEVICE_IP:-}"
DEVICE_PORT="${DEVICE_PORT:-5555}"
ADB_BIN="${ADB_BIN:-adb}"

# ─── 도움말 ────────────────────────────────────────────────────────────────
if [[ "${MODE}" == "--help" || "${MODE}" == "-h" ]]; then
    cat <<'HELP'
================================================================
 connect_device.sh — 실제 Android 기기 ADB 연결
================================================================

사용법:
  bash scripts/local/connect_device.sh              자동 (USB 우선)
  bash scripts/local/connect_device.sh --wifi       무선 디버깅 (Android 11+)
  bash scripts/local/connect_device.sh --tcp        TCP 연결
  DEVICE_IP=192.168.1.100 bash scripts/local/connect_device.sh --tcp

── Samsung Galaxy 설정 가이드 ──────────────────────────────────

1) 개발자 옵션 활성화
   설정 → 휴대전화 정보 → 소프트웨어 정보
   → "빌드번호" 7회 탭 → "개발자 모드가 설정되었습니다"

2) USB 디버깅 활성화
   설정 → 개발자 옵션 → USB 디버깅 ON

3) USB 연결 (가장 간단)
   USB 케이블로 PC에 연결 → "USB 디버깅 허용" 팝업 → 허용
   → adb devices 로 확인

4) 무선 디버깅 (Android 11+ / One UI 3+)
   설정 → 개발자 옵션 → 무선 디버깅 ON
   → "페어링 코드로 기기 페어링" 탭
   → adb pair <IP:페어링포트> <코드>
   → adb connect <IP:연결포트>

5) TCP 연결 (USB 먼저 연결 필요)
   adb tcpip 5555    # USB 연결 상태에서 실행
   adb connect <기기IP>:5555
   # 이후 USB 분리 가능

HELP
    exit 0
fi

echo "================================================================"
echo " connect_device.sh — 실제 Android 기기 ADB 연결"
echo "================================================================"
echo ""

# adb 확인
if ! command -v "${ADB_BIN}" &>/dev/null; then
    echo "   ❌ adb를 찾을 수 없습니다."
    exit 1
fi

# ─── 자동 모드: USB 연결 확인 ───────────────────────────────────────────────
if [[ "${MODE}" == "auto" ]]; then
    echo "   → USB 연결 기기 감지 중..."
    USB_DEVICES=$(${ADB_BIN} devices 2>/dev/null | tail -n +2 | grep -v "^$" | grep -vE "^emulator-|^localhost:" || true)

    if [[ -n "${USB_DEVICES}" ]]; then
        echo "   ✅ USB 기기 감지됨:"
        echo "${USB_DEVICES}" | awk '{printf "      %s\n", $0}'

        # 기기 정보 출력
        FIRST_DEV=$(echo "${USB_DEVICES}" | head -1 | awk '{print $1}')
        MODEL=$(${ADB_BIN} -s "${FIRST_DEV}" shell getprop ro.product.model 2>/dev/null | tr -d '\r' || echo "unknown")
        BRAND=$(${ADB_BIN} -s "${FIRST_DEV}" shell getprop ro.product.brand 2>/dev/null | tr -d '\r' || echo "unknown")
        RELEASE=$(${ADB_BIN} -s "${FIRST_DEV}" shell getprop ro.build.version.release 2>/dev/null | tr -d '\r' || echo "?")
        ONEUI=$(${ADB_BIN} -s "${FIRST_DEV}" shell getprop ro.build.version.oneui 2>/dev/null | tr -d '\r' || echo "")

        echo ""
        echo "   기기 정보:"
        echo "     브랜드: ${BRAND}"
        echo "     모델:   ${MODEL}"
        echo "     Android: ${RELEASE}"
        if [[ -n "${ONEUI}" ]]; then
            ONEUI_MAJOR=$((ONEUI / 10000))
            ONEUI_MINOR=$(( (ONEUI % 10000) / 100 ))
            echo "     One UI: ${ONEUI_MAJOR}.${ONEUI_MINOR}"
        fi

        echo ""
        echo "================================================================"
        echo " ✅ 기기 연결 완료: ${BRAND} ${MODEL}"
        echo "================================================================"
        echo ""
        echo "   AppAgent에서 이 기기 사용:"
        echo "     export ANDROID_SERIAL=\"${FIRST_DEV}\""
        echo "     cd ~/AppAgent && python run.py --app <패키지명>"
        echo ""
        echo "   에뮬레이터와 동시 사용 시 기기 선택:"
        echo "     bash scripts/local/select_device.sh"
        exit 0
    else
        echo "   → USB 기기 없음."
        echo ""
        echo "   연결 방법:"
        echo "     1) USB 케이블 연결 후 다시 실행"
        echo "     2) bash scripts/local/connect_device.sh --wifi  (무선)"
        echo "     3) bash scripts/local/connect_device.sh --tcp   (TCP)"
        exit 1
    fi
fi

# ─── 무선 디버깅 모드 (Android 11+) ────────────────────────────────────────
if [[ "${MODE}" == "--wifi" ]]; then
    echo "   ── 무선 디버깅 연결 (Android 11+ / One UI 3+) ──"
    echo ""
    echo "   1) 기기: 설정 → 개발자 옵션 → 무선 디버깅 ON"
    echo "   2) '페어링 코드로 기기 페어링' 탭"
    echo ""
    read -r -p "   페어링 주소 (예: 192.168.1.100:37000): " PAIR_ADDR
    read -r -p "   페어링 코드 (예: 123456): " PAIR_CODE

    echo ""
    echo "   → adb pair ${PAIR_ADDR} ${PAIR_CODE}"
    ${ADB_BIN} pair "${PAIR_ADDR}" "${PAIR_CODE}" || {
        echo "   ❌ 페어링 실패"
        exit 1
    }

    echo ""
    PAIR_IP=$(echo "${PAIR_ADDR}" | cut -d: -f1)
    read -r -p "   연결 포트 (무선 디버깅 화면에 표시됨, 예: 42000): " CONN_PORT
    CONN_ADDR="${PAIR_IP}:${CONN_PORT}"

    echo "   → adb connect ${CONN_ADDR}"
    ${ADB_BIN} connect "${CONN_ADDR}"

    echo ""
    echo "   ✅ 무선 디버깅 연결 완료"
    ${ADB_BIN} devices | tail -n +2 | grep -v "^$" | awk '{printf "     %s\n", $0}'
    exit 0
fi

# ─── TCP 모드 ──────────────────────────────────────────────────────────────
if [[ "${MODE}" == "--tcp" ]]; then
    if [[ -z "${DEVICE_IP}" ]]; then
        echo "   기기 IP를 입력하세요 (설정 → Wi-Fi → 연결된 네트워크)"
        read -r -p "   DEVICE_IP: " DEVICE_IP
        if [[ -z "${DEVICE_IP}" ]]; then
            echo "   ❌ IP 미입력"
            exit 1
        fi
    fi

    TARGET="${DEVICE_IP}:${DEVICE_PORT}"
    echo "   → adb connect ${TARGET}"
    RESULT=$(${ADB_BIN} connect "${TARGET}" 2>&1 || true)
    echo "   → ${RESULT}"

    if echo "${RESULT}" | grep -qE "^connected to|already connected"; then
        echo ""
        echo "   ✅ TCP 연결 성공: ${TARGET}"
    else
        echo ""
        echo "   ❌ 연결 실패. USB 연결 후 아래 명령 먼저 실행:"
        echo "     adb tcpip 5555"
        exit 1
    fi
fi

echo ""
echo "   현재 ADB 기기:"
${ADB_BIN} devices | tail -n +2 | grep -v "^$" | awk '{printf "     %s\n", $0}'
