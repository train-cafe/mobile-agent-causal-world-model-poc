# Progress Report: Mobile Agent + Causal World Model PoC

---

## 프로젝트 목표

- AppAgent에 Pearl's Causality Ladder Level 2 (Intervention) 추론 능력을 추가하는 Causal World Model 래퍼 구현
- Control (Causal 없음) vs Treatment (Causal 있음) 비교 실험을 통한 PoC 검증

---

## 아키텍처 (최종 확정)

```
┌──────────────────────────────────────┐     ┌─────────────────────────────┐
│           로컬 PC                     │     │       원격 서버 (H100 x2)   │
│                                      │     │                             │
│  Android 에뮬레이터                   │     │  vLLM                       │
│  (KVM 가속 + GPU 렌더링)              │     │  Qwen3-VL-32B-Instruct      │
│       ↕ ADB                          │ SSH │  port 8080                  │
│  AppAgent + CausalWorldModel 래퍼    │◄───►│  tensor-parallel=2          │
│                                      │tunnel│                             │
└──────────────────────────────────────┘     └─────────────────────────────┘
```

---

## Phase 1: 서버 올인원 구조 시도 및 실패

### 원래 계획
- H100 서버 한 대에서 에뮬레이터 + vLLM + AppAgent 모두 실행
- ws-scrcpy로 원격 화면 확인

### 수행한 작업
- `01_install_packages.sh` — 서버에 xvfb, adb, java, node 설치
- `02_setup_android_sdk.sh` — Android SDK + AVD 생성
- `03_run_emulator.sh` — Xvfb + swiftshader_indirect 에뮬레이터 실행
- `04_setup_ws_scrcpy.sh` — 원격 화면 뷰어 (port 8000)

### 발생한 문제들

| # | 문제 | 원인 | 해결 시도 |
|---|------|------|-----------|
| 1 | sdkmanager 라이선스 승인 hang | stdin 미연결 | `--licenses` 자동 수락 파이프 |
| 2 | Xvfb not found | 패키지 미설치 | `01_install_packages.sh` 에 추가 |
| 3 | sdkmanager 네트워크 차단 | dl.google.com 방화벽 | `02a_prepare_sdk_offline.sh` 오프라인 번들 |
| 4 | KVM 미지원 | 서버 bare-metal GPU 클러스터 | arm64-v8a 시도 → x86_64 tcg 시도 |
| 5 | ws-scrcpy EADDRINUSE | 포트 점유 프로세스 | fuser -k / /proc/cwd 기반 탐지 |
| 6 | ws-scrcpy 빌드 실패 | npm registry 접근 제한 | registry fallback 추가 |
| 7 | 에뮬레이터 ANR/극심한 렉 | swiftshader_indirect (CPU-only 렌더링) + H100은 Android 그래픽 미지원 | **해결 불가 → 아키텍처 전환 결정** |

### 결론
- **H100은 AI 텐서 연산 전용, Android 그래픽 렌더링 불가**
- swiftshader_indirect로는 상용 앱 UI 렌더링 불가능
- **서버 올인원 구조 포기 → Split Architecture로 전환**

---

## Phase 2: Split Architecture 전환

### 변경 내용
- 에뮬레이터 → 로컬 PC (KVM + GPU 가속)
- vLLM → 원격 H100 서버 유지
- AppAgent → 로컬 PC에서 원격 vLLM API 호출

### 수행한 작업
- `07_local_setup.sh` — 로컬 PC용 AppAgent + 원격 vLLM 연동 설정 스크립트
- `scripts/local/start_emulator.sh` — 로컬 에뮬레이터 실행
- `scripts/local/check_environment.sh` — 환경 검증 (ADB + vLLM + Python)
- `scripts/server/verify_vllm.sh` — 서버 vLLM 상태 확인
- `docs/migration_plan.md` — 마이그레이션 계획 문서
- `docs/architecture_audit.md` — 아키텍처 감사 문서

### 발생한 문제들

| # | 문제 | 원인 | 해결 |
|---|------|------|------|
| 1 | 로컬에서 x86_64 에뮬레이터 KVM 없이 실행 실패 | KVM 비활성화 환경 | ARM64 폴백 시도 |
| 2 | ARM64 폴백 실패 | Android QEMU2가 크로스 아키텍처 미지원 | ARM64 폴백 제거, 3가지 대안 제시 |
| 3 | `connect_device.sh` 추가 | 실제 기기 ADB over TCP 연결 대안 | 스크립트 신규 작성 |

---

## Phase 3: Causal World Model 구현

### 수행한 작업
- `causal_wrapper.py` — CausalWorldModel 클래스 (Pearl Level 2 Intervention)
  - `wrap_prompt()` — VLM 프롬프트에 인과 추론 컨텍스트 주입
  - `record_action()` — 액션 이력 기록
- `patch_task_executor.py` — AppAgent의 task_executor.py에 자동 4줄 패치
- `poc_experiment.py` — Control vs Treatment 비교 실험 실행기

### 발생한 문제들

| # | 문제 | 원인 | 해결 |
|---|------|------|------|
| 1 | HuggingFace 429 rate limit | 모델 다운로드 시 제한 | 사전 다운로드 단계 추가 |
| 2 | vLLM 엔진 초기화 실패 | GPU 메모리 이슈 | `--enforce-eager` 추가 |
| 3 | vLLM `--disable-log-requests` 오류 | 플래그 미지원 버전 | 플래그 제거 |

---

## Phase 4: 통합 테스트 및 연결

### 수행한 작업
- `test_appagent_integration.py` — 6개 항목 통합 테스트
- `05_setup_vllm.sh` — vLLM 서빙 (Qwen3-VL-32B, H100x2)

### 발생한 문제들

| # | 문제 | 원인 | 해결 |
|---|------|------|------|
| 1 | 로컬 → 서버 네트워크 접근 불가 (HTTP 000000) | ping 100% loss, 직접 IP 접근 차단 | **SSH 터널링** (`ssh -L 8080:10.11.245.167:8080 ... jumping-host`) |
| 2 | task_executor.py IndentationError (line 110) | patch 스크립트가 4칸 들여쓰기 하드코딩 | 삽입 위치 들여쓰기 자동 감지로 수정 |

### 현재 상태 (통합 테스트 결과)
- vLLM 서버 헬스체크: **PASS**
- vLLM 텍스트 API: **PASS**
- ADB 기기 연결: **PASS**
- Vision API (스크린샷 → 텍스트): **PASS**
- AppAgent config.yaml: **PASS**
- ADB 제어 (앱 열기): **PASS**
- **6/6 통과**

---

## Phase 5: AppAgent 실제 실행 (현재)

### 발생한 문제들

| # | 문제 | 원인 | 상태 |
|---|------|------|------|
| 1 | swipe 시 홈 화면으로 나감 | 제스처 네비게이션 영역에서 swipe 시작 → 시스템이 "홈으로 가기"로 인식 | **수정 중** |
| 2 | 3버튼 네비에서 swipe 무반응 | 네비바 영역에서 swipe 시작 → 시스템이 터치 이벤트 소비 | **수정 중** |
| 3 | Galaxy UI (One UI) 사용 불가 | 에뮬레이터는 AOSP만 지원, Samsung One UI는 실제 기기 전용 | 실제 기기 연결 필요 |

---

## 앞으로 할 일

### 즉시 (Phase 5 완료)
- [ ] `and_controller.py` swipe safe zone 패치 — 네비바/상태바 영역 제외한 안전 영역에서만 swipe
- [ ] 에뮬레이터 3버튼 네비게이션 강제 활성화 스크립트 추가
- [ ] AppAgent 실제 태스크 실행 검증 (swipe 포함 시나리오)

### 단기 (PoC 실험)
- [ ] Control 실험 실행: `CAUSAL_MODE=false python poc_experiment.py --scenario cart_add --mode control`
- [ ] Treatment 실험 실행: `CAUSAL_MODE=true python poc_experiment.py --scenario cart_add --mode treatment`
- [ ] 결과 비교: `python poc_experiment.py --compare`
- [ ] 실험 결과 문서화

### 중기 (품질 개선)
- [ ] deprecated 파일 정리 (`01`, `03`, `04`, `06` → `deprecated/` 이동)
- [ ] 실제 삼성 기기 ADB 연결 테스트 (Galaxy UI 필요 시)
- [ ] 추가 시나리오 실험 (cart_add 외 다른 태스크)
- [ ] Causal World Model 효과 정량적 분석

### 장기 (확장)
- [ ] 더 큰 VLM 모델 테스트 (72B 등)
- [ ] 다양한 앱에서의 Causal 효과 비교
- [ ] 논문/보고서 작성용 실험 데이터 수집

---

## 커밋 히스토리 요약 (시간순)

| Phase | 주요 커밋 | 내용 |
|-------|-----------|------|
| 1 | `4912c0d` | 최초 headless Ubuntu 설정 스크립트 |
| 1 | `4912c0d`~`5f6fa8d` | 서버 에뮬레이터 + ws-scrcpy 관련 fix 14건 |
| 2 | `89b7215` | vLLM H100x2 서빙 + AppAgent 로컬 연동 |
| 3 | `a1c509d` | Causal World Model PoC 구현 |
| 2 | `33fd752` | Split Architecture 전환 |
| 2 | `e035d91`~`b795fc6` | KVM/ARM64 문제 대응 → 실제 기기 연결 대안 |
| 4 | `4f1b92e` | 패치 스크립트 IndentationError 수정 |
