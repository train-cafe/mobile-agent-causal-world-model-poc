#!/usr/bin/env bash
# =============================================================================
# 04_setup_ws_scrcpy.sh
# 역할: ws-scrcpy 클론 → 빌드 → 포트 8000 에서 실행
#       브라우저에서 http://<서버IP>:8000 으로 에뮬레이터 화면 접속 가능
# 실행: bash 04_setup_ws_scrcpy.sh
# =============================================================================
set -euo pipefail

# ─────────────────────────────────────────────────────────────────
# 설정값
# ─────────────────────────────────────────────────────────────────
WS_SCRCPY_PORT=8000
WS_SCRCPY_DIR="${HOME}/ws-scrcpy"
WS_SCRCPY_REPO="https://github.com/NetrisTV/ws-scrcpy.git"
LOG_DIR="${HOME}/.android/logs"
WS_LOG="${LOG_DIR}/ws-scrcpy.log"
WS_PID_FILE="${LOG_DIR}/ws-scrcpy.pid"

mkdir -p "${LOG_DIR}"

# ─────────────────────────────────────────────────────────────────
# 환경 변수 (현재 세션)
# ─────────────────────────────────────────────────────────────────
export ANDROID_HOME="${HOME}/.android/sdk"
export PATH="${ANDROID_HOME}/platform-tools:${PATH}"

# ─────────────────────────────────────────────────────────────────
check_node_version() {
    # ws-scrcpy 는 Node.js 16+ 필요
    local node_major
    node_major=$(node --version 2>/dev/null | sed 's/v//' | cut -d. -f1 || echo "0")
    if [[ "${node_major}" -lt 16 ]]; then
        echo "⚠️  Node.js 버전이 낮습니다 (현재: $(node --version 2>/dev/null || echo '없음'), 필요: v16+)"
        install_node_upgrade
    else
        echo "   Node.js 버전 OK: $(node --version)"
    fi
}

install_node_upgrade() {
    # 1단계: nvm 으로 시도 (--lts → 20 → 18 → 16 순서)
    if [[ ! -s "${HOME}/.nvm/nvm.sh" ]]; then
        echo "   → nvm 설치 중..."
        curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash \
            || { echo "   ⚠ nvm 설치 실패 (네트워크 차단?)"; }
    fi

    if [[ -s "${HOME}/.nvm/nvm.sh" ]]; then
        # shellcheck source=/dev/null
        source "${HOME}/.nvm/nvm.sh"
        for ver in "--lts" "20" "18" "16"; do
            echo "   → nvm install ${ver} 시도..."
            if nvm install "${ver}" 2>&1 | grep -v "^$"; then
                nvm use "${ver}"    2>/dev/null || true
                nvm alias default "${ver}" 2>/dev/null || true
                echo "   → Node.js $(node --version) 활성화 (nvm)"
                return 0
            fi
        done
        echo "   ⚠ nvm 모든 버전 시도 실패"
    fi

    # 2단계: Node.js 바이너리 직접 wget 다운로드 (sudo 불필요)
    echo "   → Node.js 바이너리 직접 다운로드..."
    local NODE_VER="20.19.0"
    local NODE_DIR="${HOME}/.local/node-v${NODE_VER}-linux-x64"
    local NODE_URL="https://nodejs.org/dist/v${NODE_VER}/node-v${NODE_VER}-linux-x64.tar.xz"
    local NODE_TAR="${HOME}/.local/node.tar.xz"

    mkdir -p "${HOME}/.local"
    if [[ ! -d "${NODE_DIR}" ]]; then
        wget -q --show-progress -O "${NODE_TAR}" "${NODE_URL}"
        tar -xJf "${NODE_TAR}" -C "${HOME}/.local/"
        rm -f "${NODE_TAR}"
    fi

    export PATH="${NODE_DIR}/bin:${PATH}"

    # ~/.bashrc 에 PATH 추가 (중복 방지)
    if ! grep -q "node-v${NODE_VER}" "${HOME}/.bashrc" 2>/dev/null; then
        echo "export PATH=\"${NODE_DIR}/bin:\${PATH}\"" >> "${HOME}/.bashrc"
    fi

    echo "   → Node.js $(node --version) 활성화 (직접 설치)"
}

# ─────────────────────────────────────────────────────────────────
echo "================================================================"
echo " [1/5] Node.js 버전 확인"
echo "================================================================"
check_node_version

# ─────────────────────────────────────────────────────────────────
echo "================================================================"
echo " [2/5] ws-scrcpy 리포지토리 클론 / 업데이트"
echo "================================================================"

if [[ -d "${WS_SCRCPY_DIR}/.git" ]]; then
    echo "   → 이미 클론됨. 최신화..."
    git -C "${WS_SCRCPY_DIR}" pull --rebase origin master 2>/dev/null || \
    git -C "${WS_SCRCPY_DIR}" pull --rebase origin main  2>/dev/null || true
else
    echo "   → 클론: ${WS_SCRCPY_REPO}"
    git clone "${WS_SCRCPY_REPO}" "${WS_SCRCPY_DIR}"
fi

# ─────────────────────────────────────────────────────────────────
echo "================================================================"
echo " [3/5] 의존성 설치 (npm install)"
echo "================================================================"
cd "${WS_SCRCPY_DIR}"

# 사용할 npm 레지스트리 목록 (앞에서부터 순서대로 시도)
NPM_REGISTRIES=(
    "https://registry.npmjs.org"          # 기본 (해외)
    "https://registry.npmmirror.com"      # 타오바오 미러 (중국/제한 네트워크)
    "https://r.cnpmjs.org"                # CNPM 미러
)

npm_install_with_fallback() {
    for registry in "${NPM_REGISTRIES[@]}"; do
        echo "   → npm install --registry ${registry}"
        if timeout 120 npm install --registry "${registry}" --prefer-offline 2>&1; then
            echo "   → 의존성 설치 완료 (registry: ${registry})"
            # 성공한 레지스트리를 기본값으로 저장
            npm config set registry "${registry}"
            return 0
        fi
        echo "   ⚠ 실패 또는 타임아웃 (${registry}), 다음 레지스트리 시도..."
    done
    echo "❌ 모든 레지스트리 실패"
    echo "   → 로컬에서 node_modules 번들을 생성하여 전송하는 방법:"
    echo "     로컬: cd ~/ws-scrcpy && npm install && tar -czf ~/ws-scrcpy-modules.tar.gz node_modules"
    echo "     전송: scp ~/ws-scrcpy-modules.tar.gz <USER>@<SERVER>:~/ws-scrcpy/"
    echo "     서버: cd ~/ws-scrcpy && tar -xzf ws-scrcpy-modules.tar.gz"
    return 1
}

npm_install_with_fallback

# ─────────────────────────────────────────────────────────────────
echo "================================================================"
echo " [4/5] 프로덕션 빌드 (npm run dist)"
echo "================================================================"
npm run dist

# dist/ 의 외부 의존성은 루트 node_modules 심볼릭 링크로 해결
# (dist/ 에서 별도 npm install 하면 레지스트리 hang 위험이 있고,
#  루트에 이미 adbkit, node-pty, ws 등이 설치되어 있어 재설치 불필요)
if [[ ! -e "${WS_SCRCPY_DIR}/dist/node_modules" ]]; then
    echo "   → dist/node_modules → 루트 node_modules 심볼릭 링크 생성"
    ln -sf "${WS_SCRCPY_DIR}/node_modules" "${WS_SCRCPY_DIR}/dist/node_modules"
else
    echo "   → dist/node_modules 이미 존재"
fi

# ─────────────────────────────────────────────────────────────────
echo "================================================================"
echo " [5/5] ws-scrcpy 서버 백그라운드 실행 (포트: ${WS_SCRCPY_PORT})"
echo "================================================================"

# 포트 점유 프로세스 완전 정리 함수
kill_port() {
    local port="$1"
    local pids=""
    # ss 로 직접 PID 추출 (가장 신뢰할 수 있음)
    pids=$(ss -tlnp 2>/dev/null | grep ":${port}[^0-9]" | grep -oP '(?<=pid=)\d+' | sort -u || true)
    if [[ -z "${pids}" ]] && command -v lsof &>/dev/null; then
        pids=$(lsof -t -i ":${port}" 2>/dev/null || true)
    fi
    if [[ -n "${pids}" ]]; then
        echo "   → 포트 ${port} 점유 PID: ${pids} 종료"
        kill -9 ${pids} 2>/dev/null || true
        return 0
    fi
    return 1
}

# 기존 실행 중인 프로세스 정리
# 1) PID 파일로 종료
if [[ -f "${WS_PID_FILE}" ]]; then
    OLD_PID=$(cat "${WS_PID_FILE}" 2>/dev/null || true)
    if [[ -n "${OLD_PID}" ]] && kill -0 "${OLD_PID}" 2>/dev/null; then
        echo "   → 기존 ws-scrcpy 프로세스(PID: ${OLD_PID}) 종료"
        kill -9 "${OLD_PID}" 2>/dev/null || true
    fi
fi
# 2) 포트 점유 프로세스 강제 종료하고 해제될 때까지 대기
echo "   → 포트 ${WS_SCRCPY_PORT} 점유 프로세스 정리..."
kill_port "${WS_SCRCPY_PORT}" || true
# 포트가 실제로 해제될 때까지 최대 10초 대기
PORT_FREE=false
for _w in $(seq 1 10); do
    sleep 1
    if ! ss -tlnp 2>/dev/null | grep -q ":${WS_SCRCPY_PORT}[^0-9]"; then
        PORT_FREE=true
        break
    fi
    echo "   → 포트 ${WS_SCRCPY_PORT} 아직 점유 중... (${_w}s)"
    kill_port "${WS_SCRCPY_PORT}" || true
done
if [[ "${PORT_FREE}" == "false" ]]; then
    echo "⚠️  포트 ${WS_SCRCPY_PORT} 해제 실패. 강행합니다."
fi

# ws-scrcpy 서버 실행: dist/ 디렉토리 안에서 node ./index.js
# ※ ws-scrcpy 는 --port CLI 인자 미지원, 기본 포트 8000 사용
nohup bash -c "cd '${WS_SCRCPY_DIR}/dist' && exec node ./index.js" \
    > "${WS_LOG}" 2>&1 &
WS_PID=$!
echo "${WS_PID}" > "${WS_PID_FILE}"

# 서버 기동 확인 (최대 30초 대기)
echo "   서버 기동 대기 중..."
STARTED=false
for i in $(seq 1 30); do
    sleep 1
    if ! kill -0 "${WS_PID}" 2>/dev/null; then
        echo "❌ ws-scrcpy 프로세스가 예기치 않게 종료됐습니다."
        echo "   로그: ${WS_LOG}"
        tail -20 "${WS_LOG}"
        exit 1
    fi
    # 포트 리슨 여부 확인
    if ss -tlnp 2>/dev/null | grep -q ":${WS_SCRCPY_PORT}" || \
       netstat -tlnp 2>/dev/null | grep -q ":${WS_SCRCPY_PORT}"; then
        STARTED=true
        break
    fi
    printf "   %ds...\r" "${i}"
done

echo ""
if [[ "${STARTED}" == "true" ]]; then
    SERVER_IP=$(hostname -I | awk '{print $1}')
    echo "✅ ws-scrcpy 실행 중"
    echo "   PID     : ${WS_PID}"
    echo "   포트    : ${WS_SCRCPY_PORT}"
    echo "   로그    : ${WS_LOG}"
    echo ""
    echo "   ┌─────────────────────────────────────────────────────┐"
    echo "   │  브라우저에서 아래 주소로 접속하세요                │"
    echo "   │  http://${SERVER_IP}:${WS_SCRCPY_PORT}              │"
    echo "   │  (또는 http://localhost:${WS_SCRCPY_PORT})          │"
    echo "   └─────────────────────────────────────────────────────┘"
else
    echo "⚠️  포트 ${WS_SCRCPY_PORT} 가 열리지 않았습니다."
    echo "   프로세스는 실행 중이지만 바인딩이 지연될 수 있습니다."
    echo "   로그를 확인하세요: ${WS_LOG}"
    echo "   수동 확인: ss -tlnp | grep ${WS_SCRCPY_PORT}"
fi

echo ""
echo "▶  ws-scrcpy 종료 방법:"
echo "   kill \$(cat ${WS_PID_FILE})"
