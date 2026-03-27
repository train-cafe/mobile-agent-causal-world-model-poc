# Architecture Audit: Mobile Agent Causal World Model PoC

이 문서는 프로젝트 내 모든 파일의 역할, 실행 위치 분류, 그리고 deprecated 대상 목록을
정리한 감사 문서입니다.

---

## 1. 프로젝트 파일 전체 목록

### 1-A. 설정/설치 스크립트 (`.sh`)

| 파일 | 역할 요약 | 실행 위치 | Deprecated? |
|------|-----------|-----------|-------------|
| `01_install_packages.sh` | apt-get: xvfb, adb, java 17, nodejs, qemu-kvm 설치 | 서버 전용 | ✅ YES |
| `02_setup_android_sdk.sh` | Android SDK cmdline-tools + API 34 + AVD 생성 | local-only 전환 | ❌ NO |
| `02a_prepare_sdk_offline.sh` | 에어갭 서버용 SDK 오프라인 번들(.tar.gz) 생성 | local (유지) | ❌ NO |
| `03_run_emulator.sh` | Xvfb + `swiftshader_indirect`로 에뮬레이터 실행 | **deprecated** | ✅ YES |
| `04_setup_ws_scrcpy.sh` | ws-scrcpy 빌드 + port 8000 실행 (원격 화면 뷰어) | **deprecated** | ✅ YES |
| `05_setup_vllm.sh` | vLLM 설치 + Qwen3-VL-32B-Instruct 서빙 (port 8080) | 서버 (유지) | ❌ NO |
| `06_setup_appagent.sh` | AppAgent 클론 + 서버 내 vLLM 연동 config 생성 | **deprecated** | ✅ YES |
| `07_local_setup.sh` | AppAgent + 원격 vLLM 연동 + Causal 래퍼 설치 | **로컬 메인** | ❌ NO |

### 1-B. Python 모듈 (프로젝트 루트)

| 파일 | 역할 요약 | 실행 위치 | Deprecated? |
|------|-----------|-----------|-------------|
| `causal_wrapper.py` | CausalWorldModel: 프롬프트 래퍼 (Pearl Level 2) | shared | ❌ NO |
| `patch_task_executor.py` | task_executor.py 자동 4줄 패치 도구 | local | ❌ NO |
| `poc_experiment.py` | Control vs Treatment 비교 실험 실행기 | local | ❌ NO |
| `test_appagent_integration.py` | ADB + vLLM 통합 테스트 (6개 항목) | local | ❌ NO |

### 1-C. AppAgent 소스 (`~/AppAgent/scripts/`)

GitHub: https://github.com/mnotgod96/AppAgent

| 파일 | 역할 요약 | 수정 여부 |
|------|-----------|-----------|
| `and_controller.py` | ADB 통신 드라이버 (screencap, tap, swipe, XML 파싱) | 수정 없음 |
| `model.py` | VLM API 래퍼 (OpenAIModel, QwenModel, parse_*_rsp) | 수정 없음 |
| `config.py` | config.yaml 로더 (`load_config()`) | 수정 없음 |
| `task_executor.py` | 메인 루프 (screenshot → prompt → VLM → action) | **패치 적용** |
| `prompts.py` | 프롬프트 템플릿 (`<task_description>`, `<last_act>`, `<ui_document>`) | 수정 없음 |
| `self_explorer.py` | 자율 탐색 모드 진입점 (learn.py에서 호출) | 수정 없음 |
| `step_recorder.py` | 사람 데모 녹화 진입점 | 수정 없음 |
| `document_generation.py` | UI 요소 문서 생성 유틸 | 수정 없음 |
| `utils.py` | 이미지 어노테이션 (bbox, grid 드로잉) | 수정 없음 |
| `run.py` (루트) | 배포(task 실행) 진입점 → task_executor.py 호출 | 수정 없음 |
| `learn.py` (루트) | 탐색 진입점 → self_explorer.py / step_recorder.py 호출 | 수정 없음 |

---

## 2. 실행 위치 분류 (server / local / shared)

### SERVER 전용

다음 파일들은 원격 H100 서버에서만 실행됩니다:

| 파일 | 이유 |
|------|------|
| `05_setup_vllm.sh` | H100 GPU, CUDA 12.1+ 필요 |
| ~~`01_install_packages.sh`~~ | deprecated: 서버 에뮬레이터 설치 용도였음 |
| ~~`03_run_emulator.sh`~~ | deprecated: 서버 에뮬레이터 실행 용도였음 |
| ~~`04_setup_ws_scrcpy.sh`~~ | deprecated: 서버 에뮬레이터 화면 뷰어 용도였음 |

### LOCAL 전용

다음 파일들은 로컬 PC에서만 실행됩니다 (에뮬레이터 + ADB 필요):

| 파일 | 이유 |
|------|------|
| `07_local_setup.sh` | ADB 기기 필요, 원격 vLLM 연결 |
| `02_setup_android_sdk.sh` | 로컬 Android 에뮬레이터 설정 |
| `02a_prepare_sdk_offline.sh` | 로컬에서 오프라인 번들 생성 |
| `patch_task_executor.py` | AppAgent 소스 수정 (로컬 클론 기준) |
| `poc_experiment.py` | ADB 기기 필요 |
| `test_appagent_integration.py` | ADB 기기 필요 |

### SHARED (위치 독립적)

| 파일 | 이유 |
|------|------|
| `causal_wrapper.py` | AppAgent scripts/ 에 복사되어 사용 |
| `README.md` | 문서 |
| `docs/*.md` | 문서 |

---

## 3. ADB 통신 진입점 상세

**파일**: `~/AppAgent/scripts/and_controller.py`

```
AndroidController 클래스
│
├── execute_adb(adb_command: str) → str
│     subprocess.run(["adb", "-s", device_id, ...], capture_output=True)
│     모든 ADB 명령의 공통 실행기
│
├── get_screenshot(prefix, save_dir)
│     adb -s {device} shell screencap -p /sdcard/{prefix}.png
│     adb -s {device} pull /sdcard/{prefix}.png {local_path}
│
├── tap(x: int, y: int)
│     adb -s {device} shell input tap {x} {y}
│
├── long_press(x, y, duration=1000)
│     adb -s {device} shell input swipe {x} {y} {x} {y} {duration}
│
├── swipe(x, y, direction, dist="medium", quick=False)
│     adb -s {device} shell input swipe {x1} {y1} {x2} {y2} {ms}
│
├── text(input_str: str)
│     adb -s {device} shell input text "{escaped_str}"
│
└── list_all_devices() → list[str]
      adb devices | tail -n +2
```

**디바이스 선택**: `task_executor.py`가 시작 시 `list_all_devices()`로 연결 기기를 확인.
에뮬레이터가 1개면 자동 선택, 여러 개면 대화형 선택.

---

## 4. VLM 호출 진입점 상세

**파일**: `~/AppAgent/scripts/model.py`

```
OpenAIModel 클래스 (MODEL: "OpenAI" 설정 시 사용)
│
├── __init__(base_url, api_key, model, temperature, max_tokens)
│     config.yaml의 OPENAI_API_BASE, OPENAI_API_KEY, OPENAI_API_MODEL 읽음
│
└── get_model_response(prompt: str, images: list[str]) → (bool, str)
      HTTP POST to self.base_url (= config["OPENAI_API_BASE"])
      Authorization: Bearer {api_key}
      Body: {model, messages: [{role, content: [text + image_url]}], temperature, max_tokens}
      Returns: (success: bool, response_text: str)
```

**config.yaml 관련 키**:

| Key | 역할 | 현재 값 (07_local_setup.sh 기준) |
|-----|------|----------------------------------|
| `MODEL` | 모델 백엔드 선택 (`"OpenAI"` 또는 `"Qwen"`) | `"OpenAI"` |
| `OPENAI_API_BASE` | VLM API 전체 URL | `http://<SERVER_IP>:8080/v1/chat/completions` |
| `OPENAI_API_KEY` | API 키 (vLLM은 불필요, 더미값) | `"local-vllm-no-key"` |
| `OPENAI_API_MODEL` | 서빙 중인 모델 ID | `"Qwen/Qwen3-VL-32B-Instruct"` |
| `CAUSAL_MODE` | Causal 래퍼 활성화 여부 | `true` |

---

## 5. Causal 래퍼 주입점 상세

**파일**: `~/AppAgent/scripts/task_executor.py`

```python
# ── 메인 루프 구조 ─────────────────────────────────────────────────
round_count = 0
while round_count < configs["MAX_ROUNDS"]:        # ~line 62
    round_count += 1

    # 1. 스크린샷 + XML 캡처
    get_screenshot(...)                            # and_controller.py

    # 2. UI 요소 추출 + 문서 로드
    traverse_tree(...)

    # 3. 프롬프트 빌드
    prompt = re.sub(r"<task_description>", ...)
    prompt = re.sub(r"<ui_document>", ...)
    prompt = re.sub(r"<last_act>", last_act, prompt)   # ~line 200

    # ★ Causal 래퍼 주입점 (patch_task_executor.py가 여기에 삽입)
    # if causal_model is not None:
    #     prompt = causal_model.wrap_prompt(prompt, last_act)

    # 4. VLM 호출
    status, rsp = mllm.get_model_response(prompt, [image])  # ~line 206

    # 5. 응답 파싱
    res = parse_explore_rsp(rsp)                   # ~line 215

    # ★ Causal 이력 기록 주입점
    # if causal_model is not None and res:
    #     causal_model.record_action(round_count, res[4], res[-1])

    # 6. 액션 실행 (tap/swipe/text/FINISH)
    ...
```

**patch_task_executor.py가 삽입하는 4개 위치**:

| # | 삽입 위치 | 삽입 코드 |
|---|-----------|-----------|
| 1 | 파일 상단 import 블록 뒤 | `from causal_wrapper import CausalWorldModel` |
| 2 | `for round_count in range` 루프 직전 | `causal_model = CausalWorldModel(task_desc) if ... else None` |
| 3 | `get_model_response` 호출 직전 | `if causal_model: prompt = causal_model.wrap_prompt(prompt, last_act)` |
| 4 | `parse_*_rsp` 호출 직후 | `if causal_model and res: causal_model.record_action(...)` |

---

## 6. Deprecated 파일 목록 (제거 후보)

아래 4개 파일은 "서버에서 에뮬레이터 실행" 시나리오에만 필요하며,
목표 아키텍처(로컬 에뮬레이터 + 원격 vLLM)에서는 불필요합니다.

| # | 파일 | Deprecated 이유 | 권장 처리 |
|---|------|----------------|-----------|
| 1 | `03_run_emulator.sh` | Xvfb + swiftshader_indirect 조합, 실험 불가 경로 | `deprecated/` 이동 |
| 2 | `04_setup_ws_scrcpy.sh` | 서버 에뮬레이터 원격 화면 뷰어, 로컬 이전 후 불필요 | `deprecated/` 이동 |
| 3 | `01_install_packages.sh` | 서버에 xvfb/에뮬레이터 패키지 설치 전제 | `deprecated/` 이동 |
| 4 | `06_setup_appagent.sh` | 서버 올인원 방식, `07_local_setup.sh`로 완전 대체됨 | `deprecated/` 이동 |

> **처리 방법**: `git mv 03_run_emulator.sh deprecated/03_run_emulator.sh`
> 즉시 삭제는 히스토리 손실 위험 → `deprecated/` 서브디렉토리로 이동하고
> README에 "이 파일들은 headless 서버 에뮬레이터 방식의 실패 기록입니다" 주석 추가.

---

## 7. 변경 대상 파일 전체 요약 (경로 + 변경 유형)

| 파일 경로 | 변경 유형 | 상태 |
|-----------|-----------|------|
| `07_local_setup.sh` | 신규 생성 | ✅ 완료 |
| `causal_wrapper.py` | 신규 생성 | ✅ 완료 |
| `patch_task_executor.py` | 신규 생성 | ✅ 완료 |
| `poc_experiment.py` | 신규 생성 | ✅ 완료 |
| `docs/migration_plan.md` | 신규 생성 | ✅ 완료 |
| `docs/architecture_audit.md` | 신규 생성 | ✅ 완료 (이 파일) |
| `README.md` | 수정 (아키텍처 다이어그램, Step 3 추가) | ✅ 완료 |
| `deprecated/01_install_packages.sh` | 이동 | ⬜ 미완료 |
| `deprecated/03_run_emulator.sh` | 이동 | ⬜ 미완료 |
| `deprecated/04_setup_ws_scrcpy.sh` | 이동 | ⬜ 미완료 |
| `deprecated/06_setup_appagent.sh` | 이동 | ⬜ 미완료 |
| `~/AppAgent/scripts/task_executor.py` | 패치 적용 (실행 시 자동) | ⬜ 로컬 실행 시 적용 |

---

## 8. 의존성 관계도

```
05_setup_vllm.sh          ← 원격 서버에서 1회 실행
        │ HTTP
        ▼
07_local_setup.sh         ← SERVER_IP 지정 후 로컬에서 실행
  ├── 클론: ~/AppAgent
  ├── 복사: causal_wrapper.py → ~/AppAgent/scripts/
  ├── 실행: patch_task_executor.py → ~/AppAgent/scripts/task_executor.py
  └── 생성: ~/AppAgent/config.yaml (CAUSAL_MODE: true)
        │
        ▼
poc_experiment.py
  ├── Control:   CAUSAL_MODE=false → AppAgent (task_executor.py, wrap_prompt 스킵)
  └── Treatment: CAUSAL_MODE=true  → AppAgent + CausalWorldModel.wrap_prompt()
        │
        ▼
  결과 저장: ~/poc_results/<scenario>_<mode>_<timestamp>.json
        │
        ▼
  poc_experiment.py --compare → Control vs Treatment 비교 리포트
```
