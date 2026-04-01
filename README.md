# Mobile Agent + Causal World Model PoC

AppAgent에 Pearl's Causality Ladder Level 2 (Intervention) 추론 + 액션 검증 래퍼를 추가하는 PoC.

```
┌──────────────────────────────────────┐     ┌─────────────────────────────┐
│           로컬 PC                     │     │       원격 서버 (H100 x2)   │
│                                      │     │                             │
│  Android 에뮬레이터                   │     │  vLLM                       │
│  (KVM 가속 + GPU 렌더링)              │     │  Qwen3.5-35B-A3B (MoE)      │
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

# ── 방법 A: 전체 시나리오 한번에 실행 (권장) ──
# 모든 시나리오를 control → treatment 순서로 실행하고 비교 리포트 자동 생성
python poc_experiment.py --run-all --rounds 3

# 특정 앱만 필터링해서 실행
python poc_experiment.py --run-all --rounds 3 --filter settings,coupang

# ── 방법 B: 개별 시나리오 실행 ──
export CAUSAL_MODE=false WRAPPER_ENABLED=false
python poc_experiment.py --scenario settings_developer_usb_debug --mode control --rounds 3

export CAUSAL_MODE=true WRAPPER_ENABLED=true
python poc_experiment.py --scenario settings_developer_usb_debug --mode treatment --rounds 3

python poc_experiment.py --compare --scenario settings_developer_usb_debug

# 저장된 결과 목록
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

### 배경: Pearl's Causality Ladder와 모바일 에이전트

Pearl의 인과성 사다리(Causality Ladder)는 3단계로 나뉩니다:

| 레벨 | 이름 | 질문 | 모바일 에이전트 예시 |
|---|---|---|---|
| L1 | **Association** (관찰) | "이 화면에서 보통 어떤 버튼을 누르나?" | 일반 VLM이 하는 것: 스크린샷을 보고 다음 행동 예측 |
| L2 | **Intervention** (개입) | "이 버튼을 누르면 어떤 상태가 되나?" | **CausalWrapper가 추가하는 것**: 행동 전에 결과를 예측하고 검증 |
| L3 | **Counterfactual** (반사실) | "다른 버튼을 눌렀으면 어떻게 됐을까?" | 향후 확장 가능 |

기존 모바일 에이전트(AppAgent 등)는 **L1만 수행**: 화면을 보고 다음 행동을 출력합니다. "이 버튼을 누르면 결제가 진행된다"는 인과 추론 없이, 단순히 "이 화면에서 보통 이 버튼을 누른다"는 연관(association)만으로 행동합니다.

CausalWrapper는 **L2 Intervention**을 추가합니다: VLM이 제안한 행동을 실행하기 전에 "이 행동의 결과가 태스크 목표와 일치하는가?"를 검증합니다.

### 전체 아키텍처

CausalWrapper는 두 개의 모듈로 구성됩니다:

```
에이전트 메인 루프:
    스크린샷 캡처
    ↓
    프롬프트 빌드
    ↓
    ★ [모듈 1] Prompt Wrapper (causal_wrapper.py)
    │  VLM 프롬프트에 인과 추론 가이드를 주입
    │  → "이 행동의 결과를 예측하라"
    │  → 이전 행동 히스토리 제공
    │  → 비가역 행동 경고
    ↓
    VLM 호출 (Qwen3.5-35B-A3B)
    ↓
    응답 파싱 → proposed_action
    ↓
    ★ [모듈 2] Action Verifier (causal_action_wrapper.py)
    │  VLM이 제안한 행동을 3단계로 검증
    │  → Step 1. Precondition Check (사전조건)
    │  → Step 2. State Transition Check (상태전이)
    │  → Step 3. Irreversible Guard (비가역 방어)
    ↓
    통과 → ADB 실행 (click/type/press)
    차단 → 스킵 + 사유 로그 + 다음 라운드
```

**모듈 1 (Prompt Wrapper)** 은 VLM의 추론 품질을 높이는 **사전 개입**이고,
**모듈 2 (Action Verifier)** 는 VLM 출력을 실행 전에 걸러내는 **사후 검증**입니다.

### Action Verifier 3단계

VLM이 제안한 행동을 실행 전에 순서대로 검증합니다. 하나라도 실패하면 행동을 차단하고 다음 라운드로 넘어갑니다.

#### Step 1. Precondition Check (사전조건 검증)

**목적**: 현재 화면 상태가 해당 행동의 전제조건을 충족하는지 확인.

**왜 필요한가**: VLM은 "장바구니 담기" 버튼이 보이면 바로 누르려 합니다. 하지만 사이즈/색상 같은 필수 옵션을 선택하지 않으면 오류 팝업이 뜨고, 에이전트는 이를 복구하지 못해 태스크가 실패합니다.

**검증 규칙**:
| 규칙 | 감지 조건 | 차단 사유 |
|---|---|---|
| 로딩 상태 | "로딩", "loading", "처리 중" 키워드 | 화면 전환 미완료 — 대기 필요 |
| 상품 페이지 미경유 | "담기" 시도인데 히스토리에 상품 상세 없음 | 상품 선택 → 옵션 → 담기 순서 위반 |
| 필수 옵션 미선택 | "옵션을 선택", "필수 선택" 키워드 | 옵션 먼저 선택 필요 |

**효과**: 쿠팡에서 사이즈 미선택 후 담기 시도 → 차단 → VLM이 다음 라운드에서 사이즈 선택으로 방향 수정.

#### Step 2. State Transition Check (상태 전이 검증)

**목적**: 직전 행동이 실제로 화면 변화를 일으켰는지 확인. 같은 행동을 반복하며 진전이 없는 루프를 감지.

**왜 필요한가**: VLM은 "이 버튼을 눌러야 한다"고 판단하면, 실제로 클릭이 안 먹혀도 같은 행동을 계속 반복합니다. 특히 스크롤이 필요한 화면에서 보이지 않는 요소를 계속 탭하거나, 애니메이션 중에 탭해서 무시되는 경우가 빈번합니다.

**검증 규칙**:
| 규칙 | 감지 조건 | 차단 사유 |
|---|---|---|
| 화면 미변경 | 직전 행동의 summary와 현재 summary가 Jaccard 유사도 > 0.7 | 행동 미작동 — 다른 접근 필요 |
| 3회 연속 반복 | 같은 화면에서 3회 연속 유사 행동 | 무한 루프 — 다른 전략 필요 |
| 뒤로가기 실패 | "뒤로가기" 행동 후 화면 동일 | 네비게이션 실패 |

**효과**: 설정 앱에서 "개발자 옵션"을 찾으려고 같은 메뉴를 반복 탭 → 2회 차단 → VLM이 스크롤로 전략 변경.

#### Step 3. Irreversible Guard (비가역 행동 방어)

**목적**: 결제, 삭제, 주문 등 되돌릴 수 없는 행동이 태스크 목표와 일치하는지 확인.

**왜 필요한가**: VLM은 "바로구매"와 "장바구니 담기"의 차이를 이해하지만, UI에서 두 버튼이 나란히 있을 때 잘못된 버튼을 누르는 경우가 있습니다. 특히 "가격만 확인"이 목표인데 "구매하기"를 누르면 돌이킬 수 없습니다.

**검증 규칙**:
| 감지 키워드 | 허용 조건 | 예시 |
|---|---|---|
| 결제/구매/주문 | task_goal에 "결제", "구매", "주문" 포함 시만 허용 | "가격 확인" 태스크에서 "바로구매" → 차단 |
| 삭제/비우기 | task_goal에 "삭제", "비우기" 포함 시만 허용 | "장바구니 확인" 태스크에서 "전체삭제" → 차단 |
| 취소 | task_goal에 "취소" 포함 시만 허용 | "상품 담기" 태스크에서 "주문취소" → 차단 |

**효과**: CGV에서 "상영시간 확인만" 태스크인데 "예매하기" 클릭 → 차단 → 실제 결제 방지.

### 왜 Rule-Based로 먼저 구현했는가

| 고려사항 | Rule-Based (현재) | VLM Critic (향후) |
|---|---|---|
| **구현 속도** | 즉시 (키워드 매칭) | 프롬프트 설계 + 평가 필요 |
| **추론 비용** | 0 (문자열 비교) | VLM 호출 1회 추가 (비용 2배) |
| **지연 시간** | < 1ms | 2~5초 (VLM 응답 대기) |
| **재현성** | 100% (같은 입력 → 같은 결과) | VLM 응답에 따라 달라짐 |
| **디버깅** | 규칙이 명확하여 쉬움 | VLM 판단 근거 해석 필요 |
| **정확도** | 키워드 기반이라 한계 있음 | 맥락 이해 가능하여 높음 |

**PoC 단계에서 Rule-Based가 적합한 이유:**
1. **실험 변수 통제**: Wrapper 효과를 측정하려면 Wrapper 자체가 결정론적(deterministic)이어야 함. VLM Critic은 호출마다 결과가 달라질 수 있어 실험 재현성이 떨어짐.
2. **비용/속도**: 매 행동마다 VLM을 한번 더 호출하면 비용과 시간이 2배. PoC에서는 빠른 반복이 중요.
3. **검증 가능성**: Rule이 실패하면 "어떤 키워드가 매칭됐는지"가 명확. VLM이 실패하면 "왜 이렇게 판단했는지" 추적이 어려움.

### VLM Critic으로 교체하면 뭐가 달라지는가

Rule-Based는 키워드 매칭이라 **다음 상황에서 한계**가 있습니다:

```
예: "이 상품의 리뷰를 확인해줘" 태스크

Rule-Based:
  VLM이 "구매하기" 버튼 클릭 제안
  → Irreversible Guard: "구매" 키워드 감지, task_goal에 "구매" 없음 → 차단 ✓

  VLM이 "상품 비교하기" 버튼 클릭 제안 (리뷰 탭이 아님)
  → 3단계 모두 통과 (위험 키워드 없음) → 허용 ✗ (잘못된 방향이지만 감지 불가)

VLM Critic:
  "상품 비교하기"가 "리뷰 확인" 태스크에 도움이 되는가?
  → VLM이 맥락을 이해하여 "리뷰 탭을 눌러야 한다"고 판단 → 차단 ✓
```

| 상황 | Rule-Based | VLM Critic |
|---|---|---|
| 위험 키워드가 명확한 행동 (구매, 삭제) | 잡음 | 잡음 |
| 위험하지 않지만 방향이 틀린 행동 | **못 잡음** | 잡음 |
| 새로운 앱/UI에서 예상 못한 패턴 | **못 잡음** | 맥락으로 판단 가능 |
| 한국어/영어 혼합 키워드 | 목록에 있어야 감지 | 자연어 이해로 감지 |

### 교체 방법

ABC 인터페이스로 분리되어 있어 구현체만 교체하면 됩니다:

```python
from abc import ABC, abstractmethod

# 인터페이스 (변경 없음)
class BasePreconditionChecker(ABC):
    @abstractmethod
    def check(self, action, context) -> (str, str):
        """Returns (result, reason). result: 'pass' | 'fail' | 'skip'"""

# Rule-Based 구현 (현재)
class RulePreconditionChecker(BasePreconditionChecker):
    def check(self, action, context):
        # 키워드 매칭으로 검증
        ...

# VLM Critic 구현 (향후)
class VLMPreconditionChecker(BasePreconditionChecker):
    def __init__(self, mllm):
        self.mllm = mllm  # VLM 모델 인스턴스

    def check(self, action, context):
        prompt = f"""
        Task: {context['task_goal']}
        Proposed action: {action.act_name} - {action.summary}
        History: {context['step_history']}
        
        Is this action's precondition satisfied? Answer: pass or fail with reason.
        """
        screenshot = context['screenshot_path']
        status, response = self.mllm.get_model_response(prompt, [screenshot])
        # VLM 응답 파싱 → (result, reason)
        ...

# 교체: 한 줄만 변경
wrapper = CausalWrapper(
    task_goal,
    precondition_checker=VLMPreconditionChecker(mllm),      # ← 여기만 변경
    state_transition_checker=RuleStateTransitionChecker(),    # 혼합 가능
    irreversible_guard=RuleIrreversibleGuard(),
)
```

교체 대상 3개 클래스:
- `RulePreconditionChecker` → `VLMPreconditionChecker`
- `RuleStateTransitionChecker` → `VLMStateTransitionChecker`
- `RuleIrreversibleGuard` → `VLMIrreversibleGuard`

Rule과 VLM을 **혼합**하는 것도 가능합니다. 예: 비가역 방어는 Rule로 확실히 잡고, 사전조건은 VLM으로 맥락 판단.

---

## 트러블슈팅

### 에뮬레이터 성능 최적화 (버벅거림/느림/앱 크래시)

Google Play 이미지는 백그라운드 서비스가 많아 무겁습니다:

```bash
# 1. AVD RAM 늘리기 (4GB → 8GB)
#    Android Studio → Device Manager → 해당 AVD 연필(Edit) → Show Advanced
#    → RAM: 8192 MB

# 2. GPU 가속 확인 (host GPU 사용)
emulator -avd Pixel_6_Play -gpu host &

# 3. 불필요한 Google 서비스 비활성화 (에뮬 안에서)
adb shell pm disable-user --user 0 com.google.android.gms.policy_sidecar_ota
adb shell pm disable-user --user 0 com.google.android.apps.wellbeing
adb shell pm disable-user --user 0 com.google.android.apps.safetyhub

# 4. 애니메이션 끄기 (속도 향상)
adb shell settings put global window_animation_scale 0
adb shell settings put global transition_animation_scale 0
adb shell settings put global animator_duration_scale 0

# 5. 해상도 낮추기 (선택)
adb shell wm size 720x1600    # 원래: 1080x2400
adb shell wm size reset       # 복원
```

### uiautomator dump 실패 (XML 파일 없음)

```
adb: error: failed to stat remote object '/sdcard/...xml': No such file or directory
```

화면 전환 중 `uiautomator dump`가 실패하는 경우입니다.
패치 적용 시 자동 재시도(3회, 3초 간격)가 동작합니다:
```bash
SERVER_IP="127.0.0.1" bash 07_local_setup.sh   # 패치 재적용
```

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
├── 05_setup_vllm.sh              # [서버] vLLM + Qwen3.5-35B-A3B 서빙
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
- [Qwen3.5-35B-A3B](https://huggingface.co/Qwen/Qwen3.5-35B-A3B)
- [ADBKeyboard](https://github.com/nicewook/ADBKeyboard)
- [Android Emulator CLI](https://developer.android.com/studio/run/emulator-commandline)
