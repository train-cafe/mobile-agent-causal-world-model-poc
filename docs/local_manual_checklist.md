# 로컬 실행 수동 체크리스트

이 문서는 코드 에이전트가 자동으로 검증할 수 없는 **사람이 직접 확인해야 하는 항목**입니다.
각 항목을 순서대로 완료한 후 체크하세요.

---

## Phase 1: 서버 준비 (원격 H100 서버)

```bash
# 에뮬레이터 관련 프로세스 중단 (이미 중단되었다면 스킵)
pkill -f "emulator" 2>/dev/null || true
pkill -f "Xvfb" 2>/dev/null || true
pkill -f "ws-scrcpy" 2>/dev/null || true

# vLLM 서빙 상태 확인
bash scripts/server/verify_vllm.sh
```

- [ ] **P1-1** vLLM 프로세스 실행 중 확인 (`pgrep -f vllm.entrypoints` 출력)
- [ ] **P1-2** `/health` 엔드포인트 200 응답 (`curl http://localhost:8080/health`)
- [ ] **P1-3** 모델 서빙 확인 (`Qwen/Qwen3-VL-32B-Instruct` 로딩 완료)
- [ ] **P1-4** 텍스트 추론 테스트 통과 (verify_vllm.sh 3/4 이상)
- [ ] **P1-5** 비전 추론 테스트 통과 (verify_vllm.sh 4/4)

> vLLM이 실행 중이지 않다면: `bash 05_setup_vllm.sh` 실행 후 약 5~10분 대기.

---

## Phase 2: 로컬 PC 에뮬레이터 준비

### 2-A. KVM 활성화 확인

```bash
ls -la /dev/kvm 2>/dev/null && echo "KVM 사용 가능" || echo "KVM 없음"
```

- [ ] **P2-1** KVM 사용 가능 확인 또는 Android Studio 에뮬레이터 사용 결정

> KVM이 없는 경우: Android Studio(macOS/Windows)에서 GPU 가속 AVD를 직접 실행하고
> ADB TCP 연결(`adb connect localhost:5555`)로 연결.

### 2-B. AVD 생성 및 에뮬레이터 실행

```bash
# Android SDK + AVD 생성 (처음 한 번만)
bash 02_setup_android_sdk.sh

# 에뮬레이터 실행
bash scripts/local/start_emulator.sh

# ADB 연결 확인
adb devices
```

- [ ] **P2-2** `adb devices` 출력에 `emulator-5554   device` 확인
- [ ] **P2-3** 에뮬레이터 홈 화면 또는 잠금 화면 정상 표시 (ANR 없음)
- [ ] **P2-4** 에뮬레이터 반응 속도 확인 (앱 터치 → 1~3초 이내 반응)

> ANR 또는 렉이 심하다면:
> - KVM 미활성화 상태일 가능성 → `ls /dev/kvm` 확인
> - Android Studio 에뮬레이터(Hypervisor Framework/HAXM)로 전환 권장

---

## Phase 3: 배달 앱 수동 검증

> PoC 실험에 사용할 앱을 직접 설치하고 기본 동작을 수동으로 확인합니다.
> AppAgent가 탐색할 앱의 **사람 기준 정상 경로**를 미리 파악하는 것이 목적입니다.

### 3-A. 앱 설치

```bash
# APK 설치 (예시)
adb install /path/to/delivery_app.apk

# 설치된 앱 확인
adb shell pm list packages | grep <앱이름>
```

- [ ] **P3-1** 배달 앱 (또는 실험 대상 앱) APK 설치 완료
- [ ] **P3-2** 앱 아이콘이 에뮬레이터 홈 화면에 표시됨

### 3-B. 수동 경로 확인 (장바구니 담기 시나리오)

에뮬레이터에서 직접 앱을 실행하고 아래 경로를 손으로 따라가 보세요:

- [ ] **P3-3** 앱 실행 → 메인 화면 로드 완료 (ANR 없음)
- [ ] **P3-4** 음식 카테고리 선택 → 메뉴 목록 로드 완료
- [ ] **P3-5** 메뉴 상세 페이지 진입 → 옵션 선택 UI 표시
- [ ] **P3-6** 필수 옵션(수량/사이즈 등) 선택 → '담기' 버튼 활성화
- [ ] **P3-7** '담기' 탭 → "장바구니에 추가됨" 팝업 또는 메시지 표시
- [ ] **P3-8** 팝업 확인/닫기 → 장바구니에 아이템 1개 반영 확인

> **왜 이걸 직접 하나?**
> AppAgent가 실패하는 3가지 오류 패턴(옵션 미선택, 바로구매 혼동, 팝업 완료 오판)이
> 실제로 이 앱에서 재현 가능한지 확인하기 위함입니다.
> 만약 이 앱에서 옵션 선택이 없다면 다른 시나리오 앱으로 교체하세요.

### 3-C. poc_experiment.py 시나리오 앱 패키지명 업데이트

`poc_experiment.py`의 `SCENARIOS["cart_add"]["app_package"]`를 실제 앱 패키지명으로 수정:
```python
"app_package": "com.example.delivery",  # ← 실제 패키지명으로 교체
```

- [ ] **P3-9** `poc_experiment.py` 시나리오 앱 패키지명 수정 완료

---

## Phase 4: AppAgent + Causal 래퍼 설정

```bash
# 원격 서버 IP를 실제 IP로 교체
SERVER_IP="<원격서버IP>" bash 07_local_setup.sh

# 환경 검증 (3/3 통과 확인)
SERVER_IP="<원격서버IP>" bash scripts/local/check_environment.sh
```

- [ ] **P4-1** `07_local_setup.sh` 완료 — config.yaml의 `OPENAI_API_BASE`가 원격 서버를 가리킴
- [ ] **P4-2** `check_environment.sh` 3/3 ✅ 통과

---

## Phase 5: 1회 루프 동작 검증 (가장 중요)

```bash
source ~/AppAgent/.env_appagent
source ~/appagent-env/bin/activate
cd ~/AppAgent

# 기본 동작 테스트 (Settings 앱 — 배달 앱 준비 전 먼저 테스트)
printf 'y\n설정에서 WiFi 메뉴로 이동해줘\n' | python run.py --app com.android.settings
```

- [ ] **P5-1** AppAgent → 스크린샷 캡처 → 서버 vLLM API 전송 (로그에 HTTP 요청 확인)
- [ ] **P5-2** vLLM 응답 수신 → 액션 파싱 (tap/swipe/text 중 하나)
- [ ] **P5-3** ADB로 액션 실행 → 에뮬레이터 화면 변경 확인
- [ ] **P5-4** 최소 3라운드 이상 정상 반복 동작

> 이 단계가 통과되면 **로컬 에뮬레이터 ↔ 원격 VLM 파이프라인이 end-to-end로 작동**하는 것입니다.

---

## Phase 6: PoC 실험 실행

```bash
cd /path/to/mobile-agent-causal-world-model-poc

# Control (Causal 없이)
CAUSAL_MODE=false python poc_experiment.py --scenario cart_add --mode control --rounds 5

# Treatment (Causal 있이)
CAUSAL_MODE=true  python poc_experiment.py --scenario cart_add --mode treatment --rounds 5

# 결과 비교
python poc_experiment.py --compare --scenario cart_add
```

- [ ] **P6-1** Control 5회 실행 완료 — 결과 JSON 저장됨 (`~/poc_results/cart_add_control_*.json`)
- [ ] **P6-2** Treatment 5회 실행 완료 — 결과 JSON 저장됨
- [ ] **P6-3** `--compare` 출력에서 오류 클래스별 발생률 차이 확인

---

## 최종 완료 조건 요약

| # | 항목 | 담당 | 확인 방법 |
|---|------|------|-----------|
| 1 | 서버 vLLM API 정상 | 코드/자동 | `verify_vllm.sh` 4/4 |
| 2 | 로컬 에뮬레이터 KVM 가속 실행 | **수동** | ANR 없이 배달 앱 구동 |
| 3 | 배달 앱 수동 경로 확인 | **수동** | 장바구니 담기 end-to-end |
| 4 | AppAgent → 서버 API 연결 | 코드/자동 | `check_environment.sh` 3/3 |
| 5 | AppAgent 1회 루프 동작 | **수동 확인** | 로그에서 3라운드 확인 |
| 6 | PoC 실험 Control/Treatment | **수동** | compare 결과 비교 |
