# Migration Plan: Headless Server Emulator → Local PC Split Architecture

## 1. 현재 구조 (As-Is)

서버 단일 노드에서 Android 에뮬레이터와 vLLM을 동시에 실행하는 구조.

```
[원격 서버 — H100 x2]
  Android Emulator
    Xvfb (가상 디스플레이 :99)
    swiftshader_indirect (CPU 소프트웨어 렌더링)
        ↕ ADB (localhost)
  AppAgent
        ↕ HTTP localhost:8080
  vLLM (Qwen3-VL-32B-Instruct, port 8080)
```

관련 스크립트 실행 순서:
```bash
bash 01_install_packages.sh   # xvfb, adb, java, node 설치
bash 02_setup_android_sdk.sh  # SDK + AVD (swiftshader_indirect 설정)
bash 03_run_emulator.sh       # Xvfb + 에뮬레이터 실행
bash 04_setup_ws_scrcpy.sh    # 원격 화면 뷰어 (port 8000)
bash 05_setup_vllm.sh         # vLLM 서빙
bash 06_setup_appagent.sh     # AppAgent + 로컬 vLLM 연동
```

---

## 2. 실패 원인 (기술적 근거)

### 핵심 원인: swiftshader_indirect + H100 아키텍처 불일치

| 항목 | 내용 |
|------|------|
| **swiftshader_indirect** | CPU 기반 완전 소프트웨어 렌더링 (GPU 미사용) |
| **H100 GPU 아키텍처** | NVIDIA SM90, AI 텐서 연산(GEMM/Conv) 전용 |
| **Android 에뮬레이터 요구사항** | KVM (하드웨어 가속) + virtio-gpu (GPU 패스스루) |
| **H100의 EGL/GLES 지원** | 없음 — Android graphics pipeline 미지원 |
| **결과** | 상용 앱(배달·커머스) UI 렌더링 → ANR timeout, 실험 불가 |

### 왜 개선 불가능한가

- Xvfb 튜닝, `-gpu host`, `-gpu angle` 모두 H100에서 미지원
- Android 에뮬레이터가 KVM + virtio-gpu를 요구하는 구조적 이유
- H100 서버는 대부분 KVM 미활성화 (bare-metal GPU 클러스터)
- 소프트웨어 렌더링(swiftshader)으로는 Google Play 앱 수준의 렌더링 부하를 처리 불가

**결론**: 이 경로는 추가 튜닝이 아닌 포기가 올바른 결정.

---

## 3. 목표 구조 (To-Be)

각 컴포넌트를 최적 실행 환경으로 분리.

```
┌──────────────────────────────────────┐     ┌─────────────────────────────┐
│           로컬 PC                     │     │       원격 서버 (H100 x2)   │
│                                      │     │                             │
│  Android 에뮬레이터                   │     │  vLLM                       │
│  (KVM 가속 + GPU 렌더링)              │     │  Qwen3-VL-32B-Instruct      │
│       ↕ ADB                          │ HTTP│  port 8080                  │
│  AppAgent                            │◄───►│  tensor-parallel=2          │
│  + CausalWorldModel 래퍼             │     │                             │
│  (CAUSAL_MODE=true/false)            │     └─────────────────────────────┘
└──────────────────────────────────────┘
```

실행 순서 (To-Be):
```bash
# ── 원격 서버 (1회, 변경 없음) ──────────────────────────
bash 05_setup_vllm.sh

# ── 로컬 PC ──────────────────────────────────────────────
# 에뮬레이터: Android Studio AVD Manager 또는
bash 02_setup_android_sdk.sh   # SDK 설치
# Android Studio에서 Pixel 6 / API 34 AVD 실행
# 또는: 로컬에서 emulator 직접 실행 (KVM 있는 경우)

# AppAgent + Causal 래퍼 설치
SERVER_IP="<원격서버IP>" bash 07_local_setup.sh

# 실험 실행
source ~/AppAgent/.env_appagent && source ~/appagent-env/bin/activate
python poc_experiment.py --scenario cart_add --mode control   --rounds 5
python poc_experiment.py --scenario cart_add --mode treatment --rounds 5
python poc_experiment.py --compare --scenario cart_add
```

---

## 4. 파일 분류표

| 파일 | 현재 역할 | 분류 | 변경 유형 |
|------|-----------|------|-----------|
| `01_install_packages.sh` | 서버 패키지 설치 (xvfb 포함) | **deprecated** | `deprecated/` 이동 |
| `02_setup_android_sdk.sh` | Android SDK + AVD 생성 | **local-only 전환** | 내용 유지, 실행 위치 변경 |
| `02a_prepare_sdk_offline.sh` | 오프라인 SDK 번들 생성 | local (유지) | 변경 없음 |
| `03_run_emulator.sh` | Xvfb + swiftshader 에뮬레이터 실행 | **deprecated** | `deprecated/` 이동 |
| `04_setup_ws_scrcpy.sh` | 원격 에뮬레이터 화면 뷰어 | **deprecated** | `deprecated/` 이동 |
| `05_setup_vllm.sh` | vLLM 서빙 (H100 x2) | server (유지) | 변경 없음 |
| `06_setup_appagent.sh` | AppAgent + 서버 내 vLLM 연동 | **deprecated** | `deprecated/` 이동 |
| `07_local_setup.sh` | AppAgent + 원격 vLLM + Causal 패치 | **local 메인** | 신규 (완료) |
| `causal_wrapper.py` | CausalWorldModel 프롬프트 래퍼 | shared (유지) | 변경 없음 |
| `patch_task_executor.py` | task_executor.py 자동 패치 | local (유지) | 변경 없음 |
| `poc_experiment.py` | 실험 실행기 | local (유지) | 변경 없음 |
| `test_appagent_integration.py` | ADB + vLLM 통합 테스트 | local (유지) | 변경 없음 |

> **deprecated 처리**: 즉시 삭제보다 `deprecated/` 서브디렉토리로 이동 권장.
> git 이력 보존 + 참고 가능, 실행 경로에서는 제외.

---

## 5. 구현 단계

### Phase 1: 문서화 (완료)
- [x] `docs/migration_plan.md` (이 파일)
- [x] `docs/architecture_audit.md`

### Phase 2: 로컬 실행 검증 체크리스트

```
[ ] 로컬 PC에서 KVM 사용 가능 확인
    ls -la /dev/kvm && echo "KVM 사용 가능"

[ ] Android Studio 또는 CLI로 AVD 생성/실행
    - Pixel 6, API 34, x86_64
    - AVD Manager 또는 `emulator -avd Pixel6_API34_x86_64`

[ ] ADB 연결 확인
    adb devices  # emulator-5554 device 출력 확인

[ ] 원격 서버 vLLM 구동 확인
    curl http://<SERVER_IP>:8080/health

[ ] 로컬 AppAgent 설정
    SERVER_IP="<IP>" bash 07_local_setup.sh

[ ] 통합 테스트
    source ~/AppAgent/.env_appagent
    source ~/appagent-env/bin/activate
    VLLM_HOST="<IP>" python test_appagent_integration.py

[ ] 기본 동작 확인 (설정 앱 WiFi 이동)
    printf 'y\n설정에서 WiFi 메뉴로 이동해줘\n' | \
      python ~/AppAgent/run.py --app com.android.settings

[ ] Control 실험 (Causal 없이)
    CAUSAL_MODE=false python poc_experiment.py \
      --scenario cart_add --mode control --rounds 5

[ ] Treatment 실험 (Causal 있이)
    CAUSAL_MODE=true python poc_experiment.py \
      --scenario cart_add --mode treatment --rounds 5

[ ] 결과 비교
    python poc_experiment.py --compare --scenario cart_add
```

---

## 6. 실험 설계

### 비교 조건 (최소화 원칙)

| 조건 | 모델 | CausalWrapper | 설명 |
|------|------|---------------|------|
| A — Control | Qwen3-VL-32B | `CAUSAL_MODE=false` | 순수 AppAgent 베이스라인 |
| B — Treatment | Qwen3-VL-32B | `CAUSAL_MODE=true` | Causal 래퍼 추가 |

AppAgent 코드 동일. 모델 동일. 차이는 `CAUSAL_MODE` 환경변수 하나뿐.

### 측정 지표 (성공률이 아닌 오류 클래스별 발생률)

| 지표 | 설명 | "더 좋은 모델" 방어 근거 |
|------|------|------------------------|
| `wrong_button_rate` | 비가역 액션(바로구매) 선택 빈도 | VLM은 현재 화면만 봄 → 히스토리 없이는 구분 불가 |
| `premature_finish_rate` | 팝업에서 FINISH 오판 빈도 | 상태 전이 예측 없이는 중간 상태 감지 불가 |
| `missing_option_rate` | 필수 옵션 미선택 담기 시도 빈도 | 이전 UI 상태 추적 없이는 옵션 완료 여부 판단 불가 |

이 세 오류는 모델 크기 확장으로 해결되지 않는 구조적 문제 (Pearl Level 2).

---

## 7. 리스크 및 대안

| 리스크 | 대응 |
|--------|------|
| 로컬 PC에 KVM 없음 | Android Studio 에뮬레이터 (macOS/Windows 기본 지원) 또는 물리 Android 기기 |
| 원격 vLLM → 로컬 네트워크 지연 | 일반 LAN에서 50-100ms → VLM 추론 시간(수 초) 대비 무시 가능 |
| 배달 앱 직접 설치 불가 (APK) | 공개 APK (cafe24, 배민 등) 또는 Settings 앱으로 대체 실험 |
| 실험 재현성 | ADB 스크린샷 + Causal 히스토리 JSON 저장으로 모든 라운드 감사 가능 |
