#!/usr/bin/env python3
"""
test_appagent_integration.py
────────────────────────────
로컬 vLLM(H100 x2) + Android 에뮬레이터(ADB) 통합 테스트

테스트 항목:
  1. vLLM 서버 헬스체크 및 모델 목록 확인
  2. vLLM Vision API 호출 (스크린샷 없이 텍스트만)
  3. ADB 기기 연결 확인
  4. ADB 스크린샷 캡처 → vLLM에 이미지 전달 (Vision 테스트)
  5. AppAgent config.yaml 로드 확인
  6. 간단한 ADB 제어 명령 ("설정 앱 열기")

사전 조건:
  - bash 05_setup_vllm.sh 실행 완료
  - bash 06_setup_appagent.sh 실행 완료
  - bash 03_run_emulator.sh 실행 완료
"""

import os
import sys
import json
import base64
import subprocess
import tempfile
import time
from pathlib import Path

import requests

# ─── 설정 ────────────────────────────────────────────────────────────────────
VLLM_HOST    = os.getenv("VLLM_HOST", "localhost")
VLLM_PORT    = int(os.getenv("VLLM_PORT", "8080"))
VLLM_BASE    = f"http://{VLLM_HOST}:{VLLM_PORT}/v1"
API_KEY      = os.getenv("OPENAI_API_KEY", "local-vllm-no-key")

APPAGENT_DIR = Path(os.getenv("APPAGENT_DIR", Path.home() / "AppAgent"))

# adb 경로 자동 탐색
def find_adb() -> str:
    for candidate in [
        "adb",
        str(Path.home() / ".android/sdk/platform-tools/adb"),
        "/usr/bin/adb",
    ]:
        try:
            subprocess.run([candidate, "version"],
                           capture_output=True, check=True, timeout=5)
            return candidate
        except Exception:
            continue
    return None

ADB = find_adb()


# ─── 헬퍼 ────────────────────────────────────────────────────────────────────
GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
RESET  = "\033[0m"

def ok(msg):    print(f"{GREEN}✅ {msg}{RESET}")
def fail(msg):  print(f"{RED}❌ {msg}{RESET}");  return False
def warn(msg):  print(f"{YELLOW}⚠️  {msg}{RESET}")
def info(msg):  print(f"   {msg}")

def section(title):
    print(f"\n{'='*60}")
    print(f" {title}")
    print('='*60)


# ─── 테스트 1: vLLM 헬스체크 ─────────────────────────────────────────────────
def test_vllm_health():
    section("TEST 1: vLLM 서버 헬스체크")
    try:
        r = requests.get(f"http://{VLLM_HOST}:{VLLM_PORT}/health", timeout=10)
        if r.status_code == 200:
            ok(f"vLLM 서버 정상 (http://{VLLM_HOST}:{VLLM_PORT})")
            return True
        else:
            return fail(f"헬스체크 실패: HTTP {r.status_code}")
    except requests.exceptions.ConnectionError:
        return fail(f"vLLM 서버에 연결할 수 없습니다.\n"
                    f"   → bash 05_setup_vllm.sh 먼저 실행하세요.")
    except Exception as e:
        return fail(f"헬스체크 오류: {e}")


# ─── 테스트 2: 모델 목록 및 텍스트 API 호출 ──────────────────────────────────
def test_vllm_text_api():
    section("TEST 2: vLLM 텍스트 API 호출")

    # 모델 목록
    try:
        r = requests.get(f"{VLLM_BASE}/models",
                         headers={"Authorization": f"Bearer {API_KEY}"}, timeout=10)
        models = [m["id"] for m in r.json().get("data", [])]
        info(f"서빙 중인 모델: {models}")
        model_id = models[0] if models else "unknown"
    except Exception as e:
        return fail(f"모델 목록 조회 실패: {e}")

    # 텍스트만 요청 (Vision 아님)
    payload = {
        "model": model_id,
        "messages": [
            {"role": "user", "content": "Say 'Hello from vLLM' in one sentence."}
        ],
        "max_tokens": 64,
        "temperature": 0.0,
    }
    try:
        r = requests.post(f"{VLLM_BASE}/chat/completions",
                          headers={"Authorization": f"Bearer {API_KEY}",
                                   "Content-Type": "application/json"},
                          json=payload, timeout=60)
        resp = r.json()
        content = resp["choices"][0]["message"]["content"]
        ok(f"텍스트 응답: {content[:120]}")
        return model_id
    except Exception as e:
        return fail(f"텍스트 API 호출 실패: {e}\n   응답: {r.text[:300]}")


# ─── 테스트 3: ADB 기기 연결 ─────────────────────────────────────────────────
def test_adb_devices():
    section("TEST 3: ADB 기기 연결 확인")

    if not ADB:
        return fail("adb 명령어를 찾을 수 없습니다.")

    result = subprocess.run([ADB, "devices"], capture_output=True, text=True, timeout=10)
    lines = [l.strip() for l in result.stdout.splitlines()
             if l.strip() and not l.startswith("List")]
    devices = [l.split("\t")[0] for l in lines if "\tdevice" in l]

    if not devices:
        warn("연결된 ADB 기기 없음. 에뮬레이터 상태:")
        info(result.stdout)
        warn("→ bash 03_run_emulator.sh 로 에뮬레이터를 먼저 실행하세요.")
        return None

    ok(f"ADB 기기 발견: {devices}")
    return devices[0]  # 첫 번째 기기 반환


# ─── 테스트 4: 스크린샷 캡처 → vLLM Vision 호출 ─────────────────────────────
def test_vision_with_screenshot(device: str, model_id: str):
    section("TEST 4: 스크린샷 → vLLM Vision API 테스트")

    if not device or not model_id:
        warn("기기 또는 모델 없음, 스킵")
        return False

    # 스크린샷 캡처
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        info("에뮬레이터 스크린샷 캡처 중...")
        subprocess.run(
            [ADB, "-s", device, "exec-out", "screencap", "-p"],
            stdout=open(tmp_path, "wb"), check=True, timeout=30
        )
        img_size = os.path.getsize(tmp_path)
        info(f"스크린샷 크기: {img_size / 1024:.1f} KB")

        if img_size < 1000:
            return fail("스크린샷이 너무 작습니다. 에뮬레이터가 완전히 부팅됐는지 확인하세요.")

        # Base64 인코딩
        with open(tmp_path, "rb") as f:
            b64_img = base64.b64encode(f.read()).decode()

        # vLLM Vision API 호출
        info(f"vLLM Vision API 호출 중 (모델: {model_id})...")
        payload = {
            "model": model_id,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text",
                         "text": ("이것은 Android 에뮬레이터 스크린샷입니다. "
                                  "화면에 무엇이 보이는지 한국어로 1~2문장으로 설명해주세요.")},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{b64_img}"}}
                    ]
                }
            ],
            "max_tokens": 256,
            "temperature": 0.0,
        }

        r = requests.post(
            f"{VLLM_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}",
                     "Content-Type": "application/json"},
            json=payload, timeout=120
        )
        resp = r.json()
        content = resp["choices"][0]["message"]["content"]
        ok(f"Vision 응답:\n      {content[:300]}")
        return True

    except Exception as e:
        return fail(f"Vision 테스트 실패: {e}")
    finally:
        os.unlink(tmp_path)


# ─── 테스트 5: AppAgent config.yaml 로드 ─────────────────────────────────────
def test_appagent_config():
    section("TEST 5: AppAgent config.yaml 확인")

    config_path = APPAGENT_DIR / "config.yaml"
    if not config_path.exists():
        return fail(f"config.yaml 없음: {config_path}\n"
                    f"   → bash 06_setup_appagent.sh 먼저 실행하세요.")
    try:
        import yaml
        with open(config_path) as f:
            config = yaml.safe_load(f)

        api_base  = config.get("OPENAI_API_BASE", "")
        api_model = config.get("OPENAI_API_MODEL", "")
        model_type = config.get("MODEL", "")

        info(f"MODEL         : {model_type}")
        info(f"OPENAI_API_BASE : {api_base}")
        info(f"OPENAI_API_MODEL: {api_model}")

        if "localhost" in api_base or "127.0.0.1" in api_base:
            ok("config.yaml이 로컬 vLLM을 바라보고 있습니다.")
        else:
            warn(f"config.yaml이 외부 API를 바라보고 있습니다: {api_base}")
            warn("→ bash 06_setup_appagent.sh 재실행으로 수정하세요.")

        return config
    except Exception as e:
        return fail(f"config.yaml 파싱 오류: {e}")


# ─── 테스트 6: ADB 제어 - 설정 앱 열기 ───────────────────────────────────────
def test_adb_control(device: str):
    section("TEST 6: ADB 제어 - 설정 앱 열기")

    if not device:
        warn("기기 없음, 스킵")
        return False

    try:
        # 설정 앱 실행
        info("설정 앱(com.android.settings) 실행...")
        result = subprocess.run(
            [ADB, "-s", device, "shell",
             "am", "start", "-a", "android.settings.SETTINGS"],
            capture_output=True, text=True, timeout=15
        )
        if "Error" in result.stdout or result.returncode != 0:
            return fail(f"앱 실행 실패: {result.stdout} {result.stderr}")

        ok("설정 앱 실행 명령 전송 완료")
        time.sleep(2)

        # 현재 포커스된 액티비티 확인
        result = subprocess.run(
            [ADB, "-s", device, "shell",
             "dumpsys", "window", "windows"],
            capture_output=True, text=True, timeout=15
        )
        # mCurrentFocus 파싱
        for line in result.stdout.splitlines():
            if "mCurrentFocus" in line or "mFocusedApp" in line:
                info(f"현재 포커스: {line.strip()}")
                break

        # 홈 버튼으로 복귀
        subprocess.run(
            [ADB, "-s", device, "shell", "input", "keyevent", "KEYCODE_HOME"],
            capture_output=True, timeout=5
        )
        ok("ADB 제어 테스트 완료 (설정 앱 열기 → 홈 복귀)")
        return True

    except Exception as e:
        return fail(f"ADB 제어 실패: {e}")


# ─── 메인 ─────────────────────────────────────────────────────────────────────
def main():
    print("\n" + "="*60)
    print(" AppAgent + 로컬 vLLM(H100) 통합 테스트")
    print("="*60)
    print(f" vLLM: http://{VLLM_HOST}:{VLLM_PORT}")
    print(f" AppAgent: {APPAGENT_DIR}")
    print(f" ADB: {ADB or '(찾을 수 없음)'}")

    results = {}

    # 1. vLLM 헬스체크
    results["vllm_health"] = test_vllm_health()

    # 2. 텍스트 API
    model_id = None
    if results["vllm_health"]:
        model_id = test_vllm_text_api()
        results["vllm_text"] = bool(model_id)
    else:
        warn("vLLM 서버 미연결로 텍스트/Vision 테스트 스킵")
        results["vllm_text"] = False

    # 3. ADB 기기
    device = test_adb_devices()
    results["adb_device"] = device is not None

    # 4. Vision (스크린샷 → vLLM)
    if results["vllm_health"] and device and model_id:
        results["vision"] = test_vision_with_screenshot(device, model_id)
    else:
        warn("Vision 테스트 조건 미충족 (vLLM 또는 ADB 필요), 스킵")
        results["vision"] = None

    # 5. AppAgent config
    config = test_appagent_config()
    results["appagent_config"] = bool(config)

    # 6. ADB 제어
    if device:
        results["adb_control"] = test_adb_control(device)
    else:
        results["adb_control"] = None

    # ─── 최종 리포트 ─────────────────────────────────────────────────────────
    section("최종 테스트 결과")
    labels = {
        "vllm_health":     "vLLM 서버 헬스체크",
        "vllm_text":       "vLLM 텍스트 API",
        "adb_device":      "ADB 기기 연결",
        "vision":          "Vision API (스크린샷)",
        "appagent_config": "AppAgent config.yaml",
        "adb_control":     "ADB 제어 (앱 열기)",
    }
    passed = 0
    skipped = 0
    for key, label in labels.items():
        val = results.get(key)
        if val is True:
            print(f"  {GREEN}✅ PASS{RESET}  {label}")
            passed += 1
        elif val is False:
            print(f"  {RED}❌ FAIL{RESET}  {label}")
        else:
            print(f"  {YELLOW}⏭  SKIP{RESET}  {label}")
            skipped += 1

    total = len(labels) - skipped
    print(f"\n  결과: {passed}/{total} 통과 ({skipped}개 스킵)")

    if passed == total:
        print(f"\n{GREEN}  🎉 모든 테스트 통과! AppAgent + 로컬 vLLM 연동 준비 완료.{RESET}")
        print("\n  AppAgent 실행 예시:")
        print(f"    source {APPAGENT_DIR}/.env_appagent")
        print(f"    source {Path.home()}/appagent-env/bin/activate")
        print(f"    cd {APPAGENT_DIR}")
        print(f"    python run.py --app com.android.settings \\")
        print(f"                  --task '설정에서 WiFi 화면으로 이동해줘' \\")
        print(f"                  --series wifi_test")
    elif passed > 0:
        print(f"\n{YELLOW}  일부 테스트 실패. 위 ❌ 항목을 확인하세요.{RESET}")
    else:
        print(f"\n{RED}  테스트 실패. 05_setup_vllm.sh 및 03_run_emulator.sh 실행 여부를 확인하세요.{RESET}")

    return 0 if (passed == total) else 1


if __name__ == "__main__":
    sys.exit(main())
