# Mobile Agent + Causal World Model PoC

AppAgent에 Pearl's Causality Ladder Level 2 (Intervention) 추론 + 액션 검증 래퍼를 추가하는 PoC.

```
┌──────────────────────────────────────┐     ┌─────────────────────────────┐
│           로컬 PC                     │     │       원격 서버 (H100 x2)   │
│                                      │     │                             │
│  Android 에뮬레이터                   │     │  vLLM                       │
│  (KVM 가속 + GPU 렌더링)              │     │  Qwen3-VL-32B-Instruct      │
│       ↕ ADB                          │ SSH │  port 8080                  │
│  AppAgent                            │◄───►│  tensor-parallel=2          │
│  + CausalWrapper (프롬프트 + 액션)   │tunnel│                             │
└──────────────────────────────────────┘     └─────────────────────────────┘
```

---

## 목차

- [빠른 시작 (이미 환경 구성됨)](#빠른-시작-이미-환경-구성됨)
- [초기 설치 (처음부터)](#초기-설치-처음부터)
- [AppAgent 실행](#appagent-실행)
- [PoC 실험](#poc-실험)
- [CausalWrapper 설명](#causalwrapper-설명)
- [트러블슈팅](#트러블슈팅)
- [파일 구조](#파일-구조)

---

## 빠른 시작 (재부팅 후 재실행)

> **전제 조건**: 아래 [초기 설치](#초기-설치-처음부터)가 **모두 완료된 상태**입니다.
> 즉, `~/AppAgent` 디렉토리, `~/appagent-env` venv, `config.yaml`, 패치 적용이 끝난 상태.
> 초기 설치를 아직 안 했다면 [초기 설치 (처음부터)](#초기-설치-처음부터)로 이동하세요.

로컬 PC 재부팅, 서버 재시작, SSH 끊김 이후 **다시 실행**하는 전체 순서입니다.

### 1단계: 서버 — vLLM 재시작

```bash
# 서버 SSH 접속 후
cd /group-volume/<user>/mobile-agent-causal-world-model-poc
bash 05_setup_vllm.sh
# → 기존 vLLM 세션 종료 후 재시작, 모델 로딩 대기 (약 2~3분)

# 준비 확인
bash scripts/server/verify_vllm.sh
```

### 2단계: 로컬 — SSH 터널 연결

서버 IP로 직접 접근이 안 되는 환경에서 필수입니다.

```bash
# 터미널 1: 터널 열어두기 (백그라운드)
ssh -i mlp-n8.pem -p 3307 \
    -L 8080:10.11.245.167:8080 \
    kang9.lee@jumping-host.n8.sr-cloud.com -N &

# 연결 확인
curl -s http://127.0.0.1:8080/health && echo "OK"
```

### 3단계: 로컬 — 에뮬레이터 시작

```bash
# AVD 이름 확인
emulator -list-avds

# 에뮬레이터 시작
emulator -avd <AVD이름> &
# Android Studio 사용 시: Device Manager → ▶ (Play)

# 부팅 완료 대기
adb wait-for-device && adb shell 'while [ "$(getprop sys.boot_completed)" != "1" ]; do sleep 2; done' && echo "부팅 완료"
```

### 4단계: 로컬 — AppAgent 실행

```bash
source ~/AppAgent/.env_appagent
source ~/appagent-env/bin/activate
cd ~/AppAgent

# 대화형 실행
python run.py --app <앱패키지명>
```

### 에뮬레이터 창을 닫았을 때

에뮬레이터 창을 닫으면 프로세스가 종료됩니다. 다시 시작:

```bash
# AVD 목록 확인
emulator -list-avds

# 재시작
emulator -avd <AVD이름> &

# 부팅 완료 확인
adb wait-for-device
adb shell getprop sys.boot_completed   # "1" 출력 시 완료

# Android Studio 사용 시
# Tools → Device Manager → 해당 AVD의 ▶ (Play) 클릭
```

---

## 초기 설치 (처음부터)

### 사전 요구 사항

| 구성 | 항목 | 사양 |
|------|------|------|
| **로컬 PC** | OS | Ubuntu 20.04+ (x86_64) |
| | RAM | 8 GB 이상 |
| | KVM | `/dev/kvm` 존재 권장 |
| | ADB | Android Studio 또는 `adb` CLI |
| **원격 서버** | GPU | NVIDIA H100 80GB × 2 이상 |
| | CUDA | 12.1 이상 |
| | Python | 3.10 ~ 3.12 |

### Step 1: 서버 — vLLM 서빙

```bash
# (서버에서)
# HuggingFace 토큰 설정 (다운로드 rate limit 완화, 권장)
export HF_TOKEN="hf_xxxxxxxxxxxx"

cd /group-volume/<user>/mobile-agent-causal-world-model-poc
bash 05_setup_vllm.sh
# → venv 생성, vLLM 설치, 모델 다운로드 (~65GB), 서버 시작
# → 모델 로딩 완료까지 약 2~5분

# 검증
bash scripts/server/verify_vllm.sh
```

### Step 2: 로컬 — Android 에뮬레이터

**방법 A: Android Studio (권장)**
1. [Android Studio](https://developer.android.com/studio) 설치
2. Tools → Device Manager → Create Device
3. Pixel 6 / API 34 / x86_64 선택 → 실행

**방법 B: CLI**
```bash
bash 02_setup_android_sdk.sh    # SDK + AVD 생성
emulator -avd Pixel_6_API_34 &  # 에뮬레이터 시작
```

```bash
# 부팅 확인
adb devices                     # emulator-5554  device
adb shell getprop sys.boot_completed  # 1
```

### Step 3: SSH 터널 (서버 직접 접근 불가 시)

로컬에서 서버 IP로 직접 접근이 안 되면 SSH 터널이 필요합니다:

```bash
# 터미널 1: 터널 유지 (열어두기)
ssh -i mlp-n8.pem -p 3307 \
    -L 8080:10.11.245.167:8080 \
    kang9.lee@jumping-host.n8.sr-cloud.com -N
```

이후 `SERVER_IP`는 `127.0.0.1` 사용.

### Step 4: 로컬 — AppAgent + CausalWrapper 설정

```bash
cd ~/PythonProgramming/mobile-agent-causal-world-model-poc
SERVER_IP="127.0.0.1" bash 07_local_setup.sh
```

수행 내용:
1. `~/AppAgent` 클론 + `~/appagent-env` venv 생성
2. `config.yaml` 생성 (vLLM 엔드포인트, CAUSAL_MODE, WRAPPER_ENABLED)
3. `causal_wrapper.py` + `causal_action_wrapper.py` → `~/AppAgent/scripts/` 복사
4. `task_executor.py` 패치 (프롬프트 래퍼 + 액션 래퍼)
5. `and_controller.py` 패치 (swipe safe zone + 한글 입력)
6. 3버튼 네비게이션 활성화 + ADBKeyboard 확인

### Step 5: ADBKeyboard 설치 (한글 입력)

ADB `input text`는 ASCII만 지원합니다. 한글 입력이 필요하면:

```bash
# https://github.com/nicewook/ADBKeyboard/releases 에서 APK 다운로드
adb install ADBKeyboard.apk
adb shell ime set com.android.adbkeyboard/.AdbIME
```

### Step 6: 통합 테스트

```bash
source ~/AppAgent/.env_appagent
source ~/appagent-env/bin/activate
python test_appagent_integration.py
```

6/6 통과 시 준비 완료.

### Step (선택): 실제 Samsung 기기 연결

에뮬레이터와 **동시에** 또는 **대신** 실제 Galaxy 기기를 사용할 수 있습니다.

```bash
# 방법 1: USB 연결 (가장 간단)
# Galaxy에서: 설정 → 개발자 옵션 → USB 디버깅 ON
# USB 케이블로 PC에 연결 → "USB 디버깅 허용" 팝업 → 허용
bash scripts/local/connect_device.sh

# 방법 2: 무선 디버깅 (Android 11+ / One UI 3+)
# Galaxy에서: 설정 → 개발자 옵션 → 무선 디버깅 ON
bash scripts/local/connect_device.sh --wifi

# 방법 3: TCP (USB 연결 후 무선 전환)
adb tcpip 5555                   # USB 연결 상태에서
DEVICE_IP=192.168.1.100 bash scripts/local/connect_device.sh --tcp
```

### 기기 선택 (에뮬레이터 + 실제 기기 동시 사용)

```bash
# 연결된 기기 목록 + 정보 확인
bash scripts/local/select_device.sh list

# 대화형 선택
eval $(bash scripts/local/select_device.sh)

# 번호로 직접 선택 (예: 2번째 기기)
eval $(bash scripts/local/select_device.sh 2)

# 수동 지정
export ANDROID_SERIAL="192.168.1.100:5555"   # 실제 기기
export ANDROID_SERIAL="emulator-5554"         # 에뮬레이터
```

`ANDROID_SERIAL`이 설정되면 AppAgent와 모든 `adb` 명령이 해당 기기를 사용합니다.

---

## AppAgent 실행

```bash
source ~/AppAgent/.env_appagent
source ~/appagent-env/bin/activate
cd ~/AppAgent
```

### 대화형 실행

```bash
python run.py --app com.android.settings
```

프롬프트 순서:
```
No documentations found ... Do you want to proceed with no docs?
→ y

Please enter the description of the task...
→ 설정에서 WiFi 메뉴로 이동해줘
```

#### 대화형에서 Wrapper ON/OFF 전환

대화형 실행 전에 환경변수를 `export`로 설정합니다:

```bash
# Wrapper OFF (순수 AppAgent)
export CAUSAL_MODE=false
export WRAPPER_ENABLED=false
python run.py --app com.android.settings

# Wrapper ON (기본값으로 복원)
export CAUSAL_MODE=true
export WRAPPER_ENABLED=true
python run.py --app com.android.settings

# 또는 한 줄로 (export 없이)
CAUSAL_MODE=false WRAPPER_ENABLED=false python run.py --app com.android.settings
```

### 비대화형 실행 (파이프)

```bash
printf 'y\n설정에서 WiFi 메뉴로 이동해줘\n' | python run.py --app com.android.settings
```

#### 비대화형에서 Wrapper ON/OFF 전환

> **주의**: `VAR=val cmd1 | cmd2` 구문은 `cmd1`(printf)에만 환경변수가 적용되고,
> `cmd2`(python)에는 적용되지 않습니다. 반드시 `export`를 사용하세요.

```bash
# 방법 1: export 후 파이프 (권장)
export CAUSAL_MODE=false WRAPPER_ENABLED=false
printf 'y\n구글 지도에서 강남역 검색해줘\n' | python run.py --app com.google.android.apps.maps

# 방법 2: subshell에서 export
(export CAUSAL_MODE=false WRAPPER_ENABLED=false; \
 printf 'y\n구글 지도에서 강남역 검색해줘\n' | python run.py --app com.google.android.apps.maps)

# 다시 ON으로 되돌리기
export CAUSAL_MODE=true WRAPPER_ENABLED=true
```

### Wrapper ON/OFF 정리

| 제어 대상 | ON (기본) | OFF |
|-----------|-----------|-----|
| **프롬프트 래퍼** | `export CAUSAL_MODE=true` | `export CAUSAL_MODE=false` |
| **액션 래퍼** | `export WRAPPER_ENABLED=true` | `export WRAPPER_ENABLED=false` |
| **모두 OFF** | — | `export CAUSAL_MODE=false WRAPPER_ENABLED=false` |

**우선순위**: 환경변수 > config.yaml. 환경변수 미설정 시 config.yaml 값을 사용합니다.

config.yaml에서도 기본값 변경 가능:
```yaml
CAUSAL_MODE: False       # 프롬프트 래퍼 기본 OFF
WRAPPER_ENABLED: False   # 액션 래퍼 기본 OFF
```

실행 시 콘솔에 현재 설정이 표시됩니다:
```
[Causal] CAUSAL_MODE=False (env='false')
[Causal] WRAPPER_ENABLED=False (env='false')
```

---

## PoC 실험

```bash
source ~/AppAgent/.env_appagent
source ~/appagent-env/bin/activate
cd ~/PythonProgramming/mobile-agent-causal-world-model-poc

# 0) 환경 점검 (ADB, vLLM, AppAgent 패치 상태 확인)
python poc_experiment.py --check

# 1) 시나리오 목록 확인
python poc_experiment.py --scenarios

# 2) Control (Causal OFF) — 3회
export CAUSAL_MODE=false WRAPPER_ENABLED=false
python poc_experiment.py --scenario settings_developer_usb_debug --mode control --rounds 3

# 3) Treatment (Causal ON) — 3회
export CAUSAL_MODE=true WRAPPER_ENABLED=true
python poc_experiment.py --scenario settings_developer_usb_debug --mode treatment --rounds 3

# 4) 결과 비교
python poc_experiment.py --compare --scenario settings_developer_usb_debug

# 5) 저장된 결과 목록
python poc_experiment.py --list
```

> **실행 전 필수**: `--check`로 환경 점검을 먼저 하세요. ADB 미연결, vLLM 미응답, 패치 미적용 시 즉시 안내합니다.
> 
> 라운드당 약 3~10분 소요됩니다 (VLM 응답 속도 + 앱 복잡도에 따라).
> 타임아웃 기본값 600초, `ROUND_TIMEOUT=300` 환경변수로 조정 가능.

### 시나리오 목록

> 모든 에이전트 명령(task)은 **영어**로 작성되어 있습니다.
> Wrapper가 차이를 만들 수 있도록 **다단계 + 함정** 이 포함된 시나리오입니다.

#### TIER 1: Multi-step + Irreversible Trap (구매/결제 함정)

| 시나리오 | 앱 | Wrapper 핵심 | 태스크 |
|---|---|---|---|
| `coupang_cart_with_options` | Coupang | IrreversibleGuard + Precondition | 상품검색 → 사이즈 선택 → 장바구니 담기 (구매 아님) → 팝업 닫기 |
| `coupang_price_check_no_buy` | Coupang | IrreversibleGuard | 가격 확인만 (구매/담기 버튼 터치 금지) |

#### TIER 2: Deep Navigation + Loop Trap (스크롤/메뉴 미로)

| 시나리오 | 앱 | Wrapper 핵심 | 태스크 |
|---|---|---|---|
| `settings_developer_usb_debug` | Settings | StateTransition | 설정 → 시스템 → 개발자옵션 → USB 디버깅 (스크롤 필요) |
| `settings_change_font_size` | Settings | StateTransition | 접근성 → 글꼴 크기 슬라이더 최대 |
| `maps_multistep_directions` | Maps | StateTransition + Precondition | 장소검색 → 경로 → 대중교통 → 도보로 전환 |

#### TIER 3: Multi-field Input + Confirmation Trap (다중 입력 + 팝업 오판)

| 시나리오 | 앱 | Wrapper 핵심 | 태스크 |
|---|---|---|---|
| `myrealtrip_search_with_date` | MyRealTrip | Precondition + StateTransition | 여행검색 + 날짜 설정 + 정렬 + 상세 확인 |
| `cgv_check_specific_movie` | CGV | IrreversibleGuard + StateTransition | 영화/극장 선택 + 상영시간 확인 (예매 금지) |
| `clock_alarm_with_label_and_repeat` | Clock | Precondition + StateTransition | 알람 + 라벨 + 반복요일 설정 후 저장 |

#### TIER 4: Compound Navigation + Distractor UI (복합 조건)

| 시나리오 | 앱 | Wrapper 핵심 | 태스크 |
|---|---|---|---|
| `navermap_route_then_save` | Naver Map | StateTransition + Precondition | 경로검색 → 돌아가서 → 즐겨찾기 저장 |
| `chrome_multi_tab_compare` | Chrome | StateTransition | 탭1 검색 → 탭2 검색 → 탭1로 복귀 → FINISH |
| `settings_wifi_connect_specific` | Settings | StateTransition + Precondition | Wi-Fi ON → 5G 네트워크 찾기(스크롤) → 상세보기 |

### 왜 이 시나리오에서 Wrapper가 차이를 만드는가

| 오류 클래스 | 발생 상황 | Wrapper 방어 | 해당 시나리오 |
|---|---|---|---|
| `buy_instead_of_cart` | "담기"가 목표인데 "바로구매" 클릭 | **Irreversible Guard** | coupang_cart, coupang_price |
| `missing_option` | 사이즈/날짜 미선택 후 다음 단계 시도 | **Precondition Check** | coupang_cart, myrealtrip, clock_alarm |
| `premature_finish_popup` | "장바구니에 담겼습니다" 팝업에서 FINISH | **Prompt Wrapper** | coupang_cart, cgv |
| `loop_stuck` | 스크롤/탭전환 반복하며 목표 못 찾음 | **State Transition** | settings_dev, maps_multi, chrome_tab |
| `invalid_element` | VLM이 없는 UI 번호 출력 | **elem_list 범위 검증** | 전체 (복잡한 UI일수록 빈번) |
| `accidental_booking` | "확인만" 태스크인데 예매/결제 진입 | **Irreversible Guard** | cgv, coupang_price |

---

## CausalWrapper 설명

### 아키텍처

```
AppAgent 메인 루프:
    스크린샷 캡처
    ↓
    프롬프트 빌드
    ↓
    ★ causal_wrapper.wrap_prompt()     ← 프롬프트 래퍼
    ↓
    VLM 호출 (Qwen3-VL-32B)
    ↓
    응답 파싱 → proposed_action
    ↓
    ★ elem_list 범위 검증              ← IndexError 방어
    ★ action_wrapper.evaluate()        ← 액션 래퍼 (3단계)
        A. Precondition Check
        B. State Transition Check
        C. Irreversible Guard
    ↓
    통과 → ADB 실행 (tap/swipe/text)
    차단 → 스킵 + 로그 + 다음 라운드
```

### 액션 래퍼 3단계

**A. Precondition Check** — 사전조건 검증
- 로딩 상태 감지 → 대기 권고
- 장바구니 담기 시 상품 상세 페이지 미경유 → 차단
- 필수 옵션 미선택 → 차단

**B. State Transition Consistency** — 상태 전이 일관성
- 직전 tap/swipe 후 화면 변화 없음 → 액션 미작동 감지
- 동일 화면 3회 연속 반복 → 다른 접근법 권고
- 뒤로가기 후 페이지 미변경 → 네비게이션 실패

**C. Irreversible Action Guard** — 비가역 방어
- 결제/삭제/비우기 키워드 감지
- task_goal과 대조하여 불일치 시 차단
- 일치 시 허용

### VLM critic으로 교체

```python
# rule-based (현재)
wrapper = CausalWrapper(task_goal, precondition_checker=RulePreconditionChecker())

# VLM critic (향후) — ABC 인터페이스만 구현하면 교체 완료
wrapper = CausalWrapper(task_goal, precondition_checker=VLMPreconditionChecker(mllm))
```

교체 대상 클래스: `RulePreconditionChecker`, `RuleStateTransitionChecker`, `RuleIrreversibleGuard`
인터페이스: `check(action, context) -> (result: str, reason: str)`

---

## 트러블슈팅

### 에뮬레이터가 안 열림 / 창을 닫은 후 재시작

```bash
# AVD 목록 확인
emulator -list-avds

# cold boot (캐시 없이 재시작)
emulator -avd <AVD이름> -no-snapshot-load &

# 그래도 안 되면: 기존 lock 파일 삭제
rm -f ~/.android/avd/<AVD이름>.avd/*.lock
emulator -avd <AVD이름> &
```

### 에뮬레이터에서 swipe가 홈으로 나감

```bash
# 3버튼 네비게이션 활성화 (제스처 네비 비활성화)
adb shell cmd overlay enable com.android.internal.systemui.navbar.threebutton
```

### ADB 한글 입력 실패 (NullPointerException)

```bash
# ADBKeyboard 설치 필요
adb install ADBKeyboard.apk
adb shell ime set com.android.adbkeyboard/.AdbIME
```

### SSH 터널 끊김

```bash
# 터널 재연결
ssh -i mlp-n8.pem -p 3307 -L 8080:10.11.245.167:8080 kang9.lee@jumping-host.n8.sr-cloud.com -N &

# 연결 확인
curl -s http://127.0.0.1:8080/health && echo "OK"
```

### vLLM 서버 미응답

```bash
# 서버에서 확인
bash scripts/server/verify_vllm.sh

# 재시작
bash 05_setup_vllm.sh

# 로그 확인
tail -40 ~/.vllm/logs/vllm-server.log
```

### IndexError: list index out of range

VLM이 존재하지 않는 UI 요소 번호를 출력한 경우. `WRAPPER_ENABLED=true`이면 자동 스킵됩니다.
패치 미적용 시:
```bash
SERVER_IP="127.0.0.1" bash 07_local_setup.sh
```

### vLLM 429 Too Many Requests (HuggingFace)

```bash
export HF_TOKEN="hf_xxxx"
bash 05_setup_vllm.sh
```

---

## 파일 구조

```
.
├── 05_setup_vllm.sh              # [서버] vLLM + Qwen3-VL-32B 서빙
├── 02_setup_android_sdk.sh       # [로컬] Android SDK + AVD 생성
├── 07_local_setup.sh             # [로컬] AppAgent + CausalWrapper 설정 (메인)
│
├── causal_wrapper.py             # 프롬프트 래퍼 (Pearl Level 2)
├── causal_action_wrapper.py      # 액션 래퍼 (3단계 검증기)
├── patch_task_executor.py        # task_executor.py 자동 패치
├── patch_and_controller.py       # and_controller.py 자동 패치 (swipe + 한글)
│
├── poc_experiment.py             # Control vs Treatment 비교 실험
├── test_appagent_integration.py  # 6항목 통합 테스트
│
├── scripts/
│   ├── server/verify_vllm.sh     # vLLM 상태 검증
│   └── local/
│       ├── start_emulator.sh     # 로컬 에뮬레이터 실행
│       ├── check_environment.sh  # 환경 검증
│       └── connect_device.sh     # 실제 기기 ADB 연결
│
├── docs/
│   ├── progress_report.md        # 진행 상황 정리
│   ├── architecture_audit.md     # 아키텍처 감사
│   └── migration_plan.md         # 마이그레이션 계획
│
└── (deprecated)
    ├── 01_install_packages.sh    # 서버 에뮬레이터용 (미사용)
    ├── 03_run_emulator.sh        # 서버 에뮬레이터 실행 (미사용)
    ├── 04_setup_ws_scrcpy.sh     # 원격 화면 뷰어 (미사용)
    └── 06_setup_appagent.sh      # 서버 올인원 (미사용)
```

---

## 참고 링크

- [AppAgent](https://github.com/mnotgod96/AppAgent)
- [vLLM](https://docs.vllm.ai)
- [Qwen3-VL-32B](https://huggingface.co/Qwen/Qwen3-VL-32B-Instruct)
- [ADBKeyboard](https://github.com/nicewook/ADBKeyboard)
- [Android Emulator CLI](https://developer.android.com/studio/run/emulator-commandline)
