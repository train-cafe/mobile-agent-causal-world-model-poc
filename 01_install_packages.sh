#!/usr/bin/env bash
# =============================================================================
# 01_install_packages.sh
# 역할: sudo apt-get install 권한만으로 필수 패키지 전체 설치
# 실행: bash 01_install_packages.sh
# =============================================================================
set -euo pipefail

echo "================================================================"
echo " [1/1] 필수 패키지 설치 (sudo apt-get install)"
echo "================================================================"

sudo apt-get update -y

# ── Java (OpenJDK 17) ─────────────────────────────────────────────
# Android SDK / emulator 및 sdkmanager 실행에 필요
sudo apt-get install -y openjdk-17-jdk

# ── ADB (Android Debug Bridge) ────────────────────────────────────
sudo apt-get install -y adb

# ── 빌드/다운로드 도구 ──────────────────────────────────────────────
sudo apt-get install -y \
    wget \
    unzip \
    curl \
    git

# ── Node.js / npm (ws-scrcpy 빌드 및 AppAgent 의존성) ──────────────
# NodeSource PPA 없이 apt 기본 패키지 먼저 설치
# (버전이 너무 낮으면 아래 nvm 섹션에서 덮어씌움)
sudo apt-get install -y nodejs npm

# npm이 설치됐지만 버전이 너무 낮을 경우 nvm으로 보완 (선택 사항)
# nvm은 sudo 없이 ~/ 에 설치되므로 여기서는 스킵 — 03_ 스크립트에서 처리

# ── 가상화 / KVM (에뮬레이터 하드웨어 가속, 서버 KVM 지원 시) ──────
sudo apt-get install -y \
    qemu-kvm \
    libvirt-daemon-system \
    libvirt-clients \
    bridge-utils

# ── 헤드리스 X11 / OpenGL 의존성 (에뮬레이터 GPU 소프트웨어 렌더링용) ─
sudo apt-get install -y \
    libgl1-mesa-glx \
    libgl1-mesa-dri \
    libgles2-mesa \
    libpulse0 \
    libnss3 \
    libx11-6 \
    libxcomposite1 \
    libxcursor1 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxtst6 \
    xvfb

echo ""
echo "✅ 패키지 설치 완료"
echo "   Java 버전: $(java -version 2>&1 | head -1)"
echo "   ADB  버전: $(adb version | head -1)"
echo "   Node 버전: $(node --version)"
echo "   npm  버전: $(npm --version)"
