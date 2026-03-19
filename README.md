# Mobile Agent (AppAgent) — Headless Ubuntu 환경 세팅

GUI 없는 Ubuntu 서버에서 Android 에뮬레이터를 띄우고,
브라우저로 원격 접속(`ws-scrcpy`)하는 전체 과정을 자동화한 스크립트 모음입니다.

## 사전 요구 사항

| 항목 | 최소 사양 |
|------|-----------|
| OS | Ubuntu 20.04 / 22.04 / 24.04 LTS |
| CPU | x86_64, KVM 가상화 지원 권장 (`egrep -c '(vmx\|svm)' /proc/cpuinfo > 0`) |
| RAM | 4 GB 이상 (에뮬레이터 2 GB + 여유분) |
| Disk | 10 GB 이상 여유 공간 |
| 권한 | `sudo apt-get install` 가능, 그 외 sudo 불필요 |
| 인터넷 | Google Android 서버 다운로드 가능 |

---

## 실행 순서

### 1단계 — 필수 패키지 설치

```bash
bash 01_install_packages.sh
```

설치되는 패키지:

- `openjdk-17-jdk` — Android SDK / 에뮬레이터 실행
- `adb` — Android Debug Bridge
- `wget`, `unzip`, `curl`, `git` — 다운로드·압축 해제
- `nodejs`, `npm` — ws-scrcpy 빌드
- `qemu-kvm` + 관련 패키지 — 하드웨어 가속 (KVM 지원 서버)
- `xvfb` + OpenGL 라이브러리 — 헤드리스 렌더링

---

### 2단계 — Android SDK & 에뮬레이터 이미지 생성

```bash
bash 02_setup_android_sdk.sh
```

수행 내용:

1. `~/.android/sdk` 에 **Android Command Line Tools** 설치 (sudo 불필요)
2. `sdkmanager` 로 아래 패키지 설치
   - `platform-tools` (adb, fastboot)
   - `emulator`
   - `platforms;android-34` (Android 14)
   - `system-images;android-34;google_apis;x86_64`
3. `avdmanager` 로 **Pixel 6 / API 34** AVD 생성
4. `config.ini` — GPU 소프트웨어 렌더링(`swiftshader_indirect`) 설정

> **주의**: 이미지 다운로드 용량이 크므로 (약 1-2 GB) 네트워크 상태에 따라 시간이 걸릴 수 있습니다.

---

### 3단계 — 에뮬레이터 헤드리스 실행

```bash
bash 03_run_emulator.sh
```

수행 내용:

1. **Xvfb** 가상 디스플레이(`:99`) 시작
2. `emulator -no-window -no-audio -no-boot-anim` 플래그로 백그라운드 실행
3. ADB `sys.boot_completed=1` 까지 대기 (최대 300초)

에뮬레이터 종료:

```bash
kill $(cat ~/.android/logs/emulator.pid | head -1)
```

---

### 4단계 — ws-scrcpy 원격 화면 서버 실행

```bash
bash 04_setup_ws_scrcpy.sh
```

수행 내용:

1. Node.js 버전 확인 (16 미만이면 nvm 으로 v20 설치)
2. `ws-scrcpy` 리포지토리 클론 및 `npm run build`
3. 포트 **8000** 으로 서버 백그라운드 실행

브라우저 접속:

```
http://<서버_IP>:8000
```

ws-scrcpy 종료:

```bash
kill $(cat ~/.android/logs/ws-scrcpy.pid)
```

---

## 파일 구조

```
.
├── 01_install_packages.sh   # apt-get 패키지 설치
├── 02_setup_android_sdk.sh  # Android SDK + AVD 생성
├── 03_run_emulator.sh       # 헤드리스 에뮬레이터 실행
├── 04_setup_ws_scrcpy.sh    # ws-scrcpy 원격 화면
└── README.md
```

로그 파일 위치:

```
~/.android/logs/
├── xvfb.log        # Xvfb 가상 디스플레이
├── emulator.log    # Android 에뮬레이터
├── emulator.pid    # 에뮬레이터 PID
├── ws-scrcpy.log   # ws-scrcpy 서버
└── ws-scrcpy.pid   # ws-scrcpy PID
```

---

## 트러블슈팅

### KVM 없이 실행 시 속도가 느린 경우

CPU가 KVM을 지원하지 않거나 권한이 없으면 에뮬레이터가 순수 소프트웨어 에뮬레이션으로 동작합니다.
`03_run_emulator.sh` 내 `-memory` 값을 줄이거나 API Level을 낮춰보세요.

```bash
# KVM 사용 가능 여부 확인
ls -la /dev/kvm 2>/dev/null && echo "KVM 사용 가능" || echo "KVM 없음"
```

### `sdkmanager: command not found`

02 스크립트를 다시 실행하거나 아래를 현재 터미널에 직접 입력하세요.

```bash
source ~/.bashrc
```

### ws-scrcpy 빌드 오류 (Node.js 버전)

```bash
# nvm 으로 Node.js 20 수동 설치
source ~/.nvm/nvm.sh
nvm install 20 && nvm use 20
cd ~/ws-scrcpy && npm install && npm run build
```

### 포트 8000 방화벽

클라우드 서버(AWS, GCP 등)를 사용한다면 보안 그룹/방화벽에서 TCP **8000** 인바운드를 허용해야 합니다.

---

## 참고 링크

- [ws-scrcpy GitHub](https://github.com/NetrisTV/ws-scrcpy)
- [Android Studio Emulator — 헤드리스 가이드](https://developer.android.com/studio/run/emulator-commandline)
- [AppAgent GitHub](https://github.com/mnotgod96/AppAgent)
