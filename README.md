# Mobile Agent (AppAgent) — Headless Ubuntu + Local VLM 세팅

GUI 없는 Ubuntu 서버에서 Android 에뮬레이터를 띄우고, 브라우저로 원격 접속하며,
H100 GPU로 로컬 Vision-Language Model을 서빙해 AppAgent가 자율적으로 앱을 조작하는
전체 환경을 자동화한 스크립트 모음입니다.

```
┌─────────────────────────────────────────────────────────────┐
│  브라우저 (ws-scrcpy :8000)                                 │
│        ↕                                                    │
│  Android 에뮬레이터 (ADB emulator-5554)                     │
│        ↕                                                    │
│  AppAgent  ──→  vLLM API (:8080)  ──→  H100 x2 GPU         │
│              Qwen3-VL-32B-Instruct                          │
└─────────────────────────────────────────────────────────────┘
```

---

## 사전 요구 사항

### Step 1 (에뮬레이터 환경)

| 항목 | 사양 |
|------|------|
| OS | Ubuntu 20.04 / 22.04 / 24.04 LTS (x86_64) |
| RAM | 8 GB 이상 |
| Disk | 20 GB 이상 여유 공간 |
| 권한 | `sudo apt-get install` 가능 |
| 네트워크 | HuggingFace, GitHub, npm registry 접근 가능 |

### Step 2 (VLM 서빙)

| 항목 | 사양 |
|------|------|
| GPU | NVIDIA H100 80GB × 2 이상 (32B 모델 기준) |
| CUDA | 12.1 이상 |
| Python | 3.10 ~ 3.12 |
| HF 계정 | HuggingFace 토큰 권장 (다운로드 rate limit 완화) |

---

## 전체 실행 순서 요약

```bash
# Step 1: Android 에뮬레이터 환경
bash 01_install_packages.sh     # 시스템 패키지
bash 02_setup_android_sdk.sh    # Android SDK + AVD
bash 03_run_emulator.sh         # 헤드리스 에뮬레이터 실행
bash 04_setup_ws_scrcpy.sh      # 브라우저 원격 화면 서버

# Step 2: VLM 서빙 + AppAgent
bash 05_setup_vllm.sh           # vLLM + Qwen3-VL-32B 서빙
bash 06_setup_appagent.sh       # AppAgent 설치 + 로컬 VLM 연동

# 통합 테스트
source ~/AppAgent/.env_appagent
source ~/appagent-env/bin/activate
python test_appagent_integration.py
```

---

## Step 1 — Android 에뮬레이터

### 1단계 — 필수 패키지 설치

```bash
bash 01_install_packages.sh
```

설치 패키지:
- `openjdk-17-jdk` — Android SDK / 에뮬레이터
- `adb` — Android Debug Bridge
- `nodejs`, `npm` — ws-scrcpy 빌드
- `qemu-kvm` + `xvfb` — 하드웨어 가속 및 헤드리스 렌더링

---

### 2단계 — Android SDK & AVD 생성

```bash
bash 02_setup_android_sdk.sh
```

- `~/.android/sdk` 에 Android Command Line Tools 설치
- `system-images;android-34;google_apis;x86_64` 다운로드
- **Pixel 6 / API 34 / x86_64** AVD 생성

> Google 서버 접근이 막힌 환경이라면 `02a_prepare_sdk_offline.sh` 로
> 로컬 머신에서 번들을 만든 뒤 서버에 전송해 오프라인 설치합니다.

---

### 3단계 — 헤드리스 에뮬레이터 실행

```bash
bash 03_run_emulator.sh
```

1. Xvfb 가상 디스플레이(`:99`) 시작
2. `-no-window -no-audio -accel off` 플래그로 백그라운드 실행
3. ADB `sys.boot_completed=1` 까지 최대 900초 대기

```bash
# 에뮬레이터 상태 확인
adb devices
# emulator-5554   device

# 에뮬레이터 종료
kill $(cat ~/.android/logs/emulator.pid | head -1)
```

---

### 4단계 — ws-scrcpy 원격 화면 서버

```bash
bash 04_setup_ws_scrcpy.sh
```

- Node.js 20 확인 (미충족 시 자동 설치)
- `npm run dist` 빌드 → 포트 **8000** 백그라운드 실행

**브라우저 접속 (SSH 포트 포워딩 필요):**
```bash
# 로컬 머신에서
ssh -L 8000:localhost:8000 <사용자>@<서버_IP>
```
→ 브라우저에서 `http://localhost:8000` 접속

**직접 접근 가능한 경우:**
```
http://<서버_IP>:8000
```

연결되면 `aDevice Tracker` 화면에서 에뮬레이터가 초록 점(●)으로 표시됩니다.
**Configure stream → Start** 클릭 시 에뮬레이터 화면이 실시간 스트리밍됩니다.

```bash
# ws-scrcpy 종료
kill $(cat ~/.android/logs/ws-scrcpy.pid)
```

---

## Step 2 — 로컬 VLM 서빙 + AppAgent

### 5단계 — vLLM 서버 실행 (H100 x2)

```bash
# HuggingFace 토큰 설정 (다운로드 rate limit 완화, 권장)
export HF_TOKEN="hf_xxxxxxxxxxxx"

bash 05_setup_vllm.sh
```

수행 내용:
1. `~/vllm-env` Python venv 생성 + vLLM 설치
2. `Qwen/Qwen3-VL-32B-Instruct` 모델 사전 다운로드 (~65 GB)
3. GPU 0,1 전용으로 **포트 8080**, `tensor-parallel-size=2` 실행
4. `/health` 엔드포인트 폴링으로 준비 완료 자동 확인

```bash
# 서버 상태 수동 확인
curl http://localhost:8080/health && echo "OK"

# 서빙 중인 모델 확인
curl -s http://localhost:8080/v1/models | python3 -m json.tool

# 로그 스트리밍
tail -f ~/.vllm/logs/vllm-server.log

# tmux 세션 확인
tmux attach -t vllm-server
```

**환경변수로 동작 커스터마이징:**
```bash
MODEL="Qwen/Qwen3-VL-32B-Instruct" \
TENSOR_PARALLEL=2 \
CUDA_VISIBLE_DEVICES=0,1 \
MAX_MODEL_LEN=32768 \
GPU_MEM_UTIL=0.90 \
bash 05_setup_vllm.sh
```

**vLLM API 직접 테스트:**
```bash
curl -s http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer local-key" \
  -d '{
    "model": "Qwen/Qwen3-VL-32B-Instruct",
    "messages": [{"role":"user","content":"Hello, are you ready?"}],
    "max_tokens": 64
  }' | python3 -m json.tool
```

---

### 6단계 — AppAgent 설치 및 로컬 VLM 연동

```bash
bash 06_setup_appagent.sh
```

수행 내용:
1. `~/AppAgent` 클론 + `~/appagent-env` venv 의존성 설치
2. `config.yaml` 을 로컬 vLLM 엔드포인트로 자동 패치
3. `~/.env_appagent` 환경변수 파일 생성

**생성되는 `config.yaml` 핵심 설정:**
```yaml
MODEL: "OpenAI"
OPENAI_API_BASE: "http://localhost:8080/v1/chat/completions"
OPENAI_API_KEY: "local-vllm-no-key"
OPENAI_API_MODEL: "Qwen/Qwen3-VL-32B-Instruct"
MAX_TOKENS: 1024
TEMPERATURE: 0.0
REQUEST_INTERVAL: 3
MAX_ROUNDS: 20
```

---

### 통합 테스트

```bash
source ~/AppAgent/.env_appagent
source ~/appagent-env/bin/activate
python test_appagent_integration.py
```

테스트 항목:
1. vLLM 서버 헬스체크
2. 텍스트 API 응답 확인
3. ADB 기기 연결 확인
4. 스크린샷 캡처 → Vision API 전달 (실제 VLM 추론)
5. AppAgent `config.yaml` 검증
6. ADB 설정 앱 열기 제어

**모두 통과 시 출력:**
```
✅ PASS  vLLM 서버 헬스체크
✅ PASS  vLLM 텍스트 API
✅ PASS  ADB 기기 연결
✅ PASS  Vision API (스크린샷)
✅ PASS  AppAgent config.yaml
✅ PASS  ADB 제어 (앱 열기)

🎉 모든 테스트 통과! AppAgent + 로컬 vLLM 연동 준비 완료.
```

---

### AppAgent 실행 예시

**환경 준비:**
```bash
source ~/AppAgent/.env_appagent
source ~/appagent-env/bin/activate
cd ~/AppAgent
```

**태스크 실행 (run 모드) — 대화형 입력:**
```bash
python run.py --app com.android.settings
```

실행 후 아래 프롬프트에 순서대로 답합니다:
```
# 문서가 없을 경우 (처음 실행 시)
No documentations found ... Do you want to proceed with no docs? Enter y or n
→ y  ← 입력

# 기기가 여러 개일 경우 (에뮬레이터 1개면 자동 선택)
Device selected: emulator-5554

# 태스크 설명 입력
Please enter the description of the task...
→ 설정에서 WiFi 메뉴로 이동해줘  ← 입력
```

**입력을 파이프로 자동화 (비대화형 실행):**
```bash
# 문서 없이 태스크 실행 (y = no-doc 허용, 태스크 설명 순서)
printf 'y\n설정에서 WiFi 메뉴로 이동해줘\n' | python run.py --app com.android.settings

# Chrome으로 검색
printf 'y\n구글에서 vLLM 사용법을 검색해줘\n' | python run.py --app com.android.chrome
```

**탐색(learn) 모드 — 앱 사용법 문서 자동 생성:**
```bash
python learn.py --app com.android.settings
```

**ADB로 현재 에뮬레이터 화면 확인:**
```bash
# 스크린샷 저장
adb exec-out screencap -p > ~/screen.png

# 현재 포커스된 앱 확인
adb shell dumpsys window windows | grep mCurrentFocus

# 앱 패키지 목록
adb shell pm list packages | grep -v "package:com.google\|package:com.android" | head -20
```

---

## 파일 구조

```
.
├── 01_install_packages.sh        # apt-get 패키지 설치
├── 02_setup_android_sdk.sh       # Android SDK + AVD 생성
├── 02a_prepare_sdk_offline.sh    # 방화벽 환경용 오프라인 번들 준비 (로컬 실행)
├── 03_run_emulator.sh            # 헤드리스 에뮬레이터 실행
├── 04_setup_ws_scrcpy.sh         # ws-scrcpy 원격 화면 서버 (포트 8000)
├── 05_setup_vllm.sh              # vLLM + Qwen3-VL-32B 서빙 (포트 8080)
├── 06_setup_appagent.sh          # AppAgent 클론 + 로컬 VLM 연동
├── test_appagent_integration.py  # VLM + ADB 통합 테스트
└── README.md
```

**로그 파일:**
```
~/.android/logs/
├── xvfb.log          # Xvfb 가상 디스플레이
├── emulator.log      # Android 에뮬레이터
├── emulator.pid      # 에뮬레이터 PID
├── ws-scrcpy.log     # ws-scrcpy 서버
└── ws-scrcpy.pid     # ws-scrcpy PID

~/.vllm/logs/
├── vllm-server.log   # vLLM 서버 로그
└── vllm-server.pid   # vLLM PID
```

---

## 포트 사용 현황

| 포트 | 서비스 | 설명 |
|------|--------|------|
| 5554/5555 | Android 에뮬레이터 | ADB 연결 |
| 8000 | ws-scrcpy | 브라우저 원격 화면 |
| 8080 | vLLM | OpenAI 호환 API |

---

## 트러블슈팅

### KVM 없이 에뮬레이터가 느린 경우

```bash
# KVM 사용 가능 여부 확인
ls -la /dev/kvm 2>/dev/null && echo "KVM 사용 가능" || echo "KVM 없음 (소프트웨어 에뮬레이션)"
```

KVM 없으면 x86_64 소프트웨어 에뮬레이션으로 동작합니다. 부팅에 10~20분 소요될 수 있습니다.

### ws-scrcpy 브라우저 접속 불가 (내부 IP)

클라우드/쿠버네티스 환경에서는 SSH 포트 포워딩이 필요합니다:
```bash
# 로컬 터미널에서 실행
ssh -L 8000:localhost:8000 -L 8080:localhost:8080 <사용자>@<서버_호스트명>
```
→ `http://localhost:8000` 및 `http://localhost:8080`으로 접속

### vLLM 429 Too Many Requests (HuggingFace)

```bash
# HF 토큰으로 rate limit 우회
export HF_TOKEN="hf_xxxx"
bash 05_setup_vllm.sh

# 또는 huggingface-cli로 수동 다운로드
source ~/vllm-env/bin/activate
huggingface-cli download Qwen/Qwen3-VL-32B-Instruct \
    --exclude "*.pt" "original/*"
```

### vLLM 엔진 초기화 실패

```bash
# 로그 확인
head -80 ~/.vllm/logs/vllm-server.log
tail -40 ~/.vllm/logs/vllm-server.log

# CUDA/GPU 상태 확인
nvidia-smi
nvidia-smi topo -m   # NVLink 연결 확인

# GPU 메모리 부족 시 컨텍스트 길이 줄이기
MAX_MODEL_LEN=8192 bash 05_setup_vllm.sh
```

### AppAgent config 재설정

```bash
# vLLM 서버 재시작 후 모델명이 바뀐 경우
bash 06_setup_appagent.sh
```

---

## 참고 링크

- [ws-scrcpy GitHub](https://github.com/NetrisTV/ws-scrcpy)
- [AppAgent GitHub](https://github.com/mnotgod96/AppAgent)
- [vLLM 공식 문서](https://docs.vllm.ai)
- [Qwen3-VL HuggingFace](https://huggingface.co/Qwen/Qwen3-VL-32B-Instruct)
- [Android Emulator CLI 가이드](https://developer.android.com/studio/run/emulator-commandline)
