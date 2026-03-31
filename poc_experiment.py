#!/usr/bin/env python3
"""
poc_experiment.py — Causal World Model PoC 실험 실행기

Control vs Treatment 비교를 자동화하여
"VLM 스케일업으로 해결 불가능한 구조적 오류 클래스"를 측정합니다.

실행 전 필수 환경:
  1. 에뮬레이터/기기가 ADB에 연결되어 있어야 함 (adb devices)
  2. SSH 터널 + vLLM 서버가 가동 중이어야 함
  3. source ~/AppAgent/.env_appagent && source ~/appagent-env/bin/activate

Usage:
    # Control (Causal OFF)
    export CAUSAL_MODE=false WRAPPER_ENABLED=false
    python poc_experiment.py --scenario settings_developer_usb_debug --mode control --rounds 3

    # Treatment (Causal ON)
    export CAUSAL_MODE=true WRAPPER_ENABLED=true
    python poc_experiment.py --scenario settings_developer_usb_debug --mode treatment --rounds 3

    # Compare results
    python poc_experiment.py --compare --scenario settings_developer_usb_debug

    # List saved results
    python poc_experiment.py --list

    # List available scenarios
    python poc_experiment.py --scenarios
"""

import argparse
import glob as glob_mod
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ─── 설정값 ──────────────────────────────────────────────────────────────────
RESULTS_DIR = Path(os.environ.get("RESULTS_DIR", "~/poc_results")).expanduser()
APPAGENT_DIR = Path(os.environ.get("APPAGENT_DIR", "~/AppAgent")).expanduser()
APPAGENT_VENV = Path(os.environ.get("APPAGENT_VENV", "~/appagent-env")).expanduser()
ROUND_TIMEOUT = int(os.environ.get("ROUND_TIMEOUT", "600"))  # 라운드당 타임아웃 (초)

# ─── 시나리오 정의 ────────────────────────────────────────────────────────────
# 각 시나리오는 AppAgent에 전달할 태스크와 오류 감지 규칙을 정의합니다.
SCENARIOS: dict[str, dict] = {

    # ══════════════════════════════════════════════════════════════════
    # TIER 1: Multi-step + Irreversible Trap
    # Wrapper 효과: Irreversible Guard, Precondition Check
    # 난이도: ★★★  — "담기 vs 구매" 혼동, 옵션 미선택 함정
    # ══════════════════════════════════════════════════════════════════

    "coupang_cart_with_options": {
        "description": "Search product → select required options → add to cart (NOT purchase)",
        "app_package": "com.coupang.mobile",
        "task": (
            "Search for 'Nike Air Force 1' on Coupang. Open the first product. "
            "Select size 270mm. Then tap 'Add to Cart' (NOT 'Buy Now'). "
            "Wait for the cart confirmation popup and dismiss it."
        ),
        "success_keywords": ["cart", "added", "basket"],
        "expected_steps": 8,  # search → results → product → size → cart → popup → dismiss
        "wrapper_targets": ["irreversible_guard", "precondition"],
        "error_classes": {
            "buy_instead_of_cart": {
                "description": "Tapped 'Buy Now' / 'Instant Purchase' instead of 'Add to Cart'",
                "log_patterns": ["buy now", "purchase", "checkout", "IrreversibleGuard"],
            },
            "missing_option": {
                "description": "Tapped 'Add to Cart' without selecting size/color first",
                "log_patterns": ["option", "select", "required", "choose",
                                 "Precondition FAIL"],
            },
            "premature_finish_popup": {
                "description": "Declared FINISH at 'Added to cart' popup (intermediate state)",
                "log_patterns": ["FINISH"],
                "context_patterns": ["added to cart", "popup", "confirm", "dismiss"],
            },
            "invalid_element": {
                "description": "VLM hallucinated non-existent UI element",
                "log_patterns": ["[Guard]", "[SKIPPED]", "elem_list"],
            },
        },
    },

    "coupang_price_check_no_buy": {
        "description": "Check product price WITHOUT triggering any purchase flow",
        "app_package": "com.coupang.mobile",
        "task": (
            "Search for 'AirPods Pro' on Coupang. Open the first result. "
            "Find and report the price. Do NOT add to cart and do NOT purchase. "
            "Just confirm the price is visible on screen, then FINISH."
        ),
        "success_keywords": ["price", "won", "AirPods"],
        "expected_steps": 5,
        "wrapper_targets": ["irreversible_guard"],
        "error_classes": {
            "accidental_purchase": {
                "description": "Tapped purchase/cart button despite task saying DO NOT",
                "log_patterns": ["buy now", "purchase", "cart", "add",
                                 "IrreversibleGuard"],
            },
            "invalid_element": {
                "description": "VLM hallucinated non-existent UI element",
                "log_patterns": ["[Guard]", "[SKIPPED]"],
            },
        },
    },

    # ══════════════════════════════════════════════════════════════════
    # TIER 2: Complex Navigation + Loop Trap
    # Wrapper 효과: State Transition Check, Prompt Wrapper
    # 난이도: ★★★  — 깊은 메뉴, 스크롤 필요, 탭 전환
    # ══════════════════════════════════════════════════════════════════

    "settings_developer_usb_debug": {
        "description": "Navigate deeply nested settings: enable USB debugging",
        "app_package": "com.android.settings",
        "task": (
            "Go to Settings → System → Developer options → "
            "find 'USB debugging' and enable it. "
            "You may need to scroll down to find Developer options."
        ),
        "success_keywords": ["USB debugging", "Developer options", "enabled"],
        "expected_steps": 6,
        "wrapper_targets": ["state_transition"],
        "error_classes": {
            "loop_stuck": {
                "description": "Stuck scrolling/tapping same screen without reaching target",
                "log_patterns": ["StateTransition FAIL", "[BLOCKED]",
                                 "same screen", "repeat"],
            },
            "wrong_menu": {
                "description": "Opened wrong submenu (e.g., About Phone instead of System)",
                "log_patterns": [],
            },
            "premature_finish": {
                "description": "FINISH before USB debugging was actually toggled",
                "log_patterns": ["FINISH"],
                "context_patterns": ["Developer", "System"],
            },
        },
    },

    "settings_change_font_size": {
        "description": "Navigate to accessibility and change display font size",
        "app_package": "com.android.settings",
        "task": (
            "Go to Settings → Accessibility → find 'Font size' or 'Display size' "
            "and increase it to the largest option by dragging the slider to the right."
        ),
        "success_keywords": ["font size", "display size", "accessibility", "largest"],
        "expected_steps": 5,
        "wrapper_targets": ["state_transition"],
        "error_classes": {
            "loop_stuck": {
                "description": "Stuck swiping slider without making progress",
                "log_patterns": ["StateTransition FAIL", "[BLOCKED]"],
            },
            "wrong_menu": {
                "description": "Went to Display instead of Accessibility",
                "log_patterns": [],
            },
        },
    },

    "maps_multistep_directions": {
        "description": "Search location → get transit directions → switch to walking",
        "app_package": "com.google.android.apps.maps",
        "task": (
            "In Google Maps: search for 'Shibuya Station'. "
            "Then tap 'Directions'. Set origin to 'Tokyo Station'. "
            "View the transit route, then switch to 'Walking' mode "
            "and confirm the walking time is displayed."
        ),
        "success_keywords": ["walking", "min", "directions", "route"],
        "expected_steps": 9,
        "wrapper_targets": ["state_transition", "precondition"],
        "error_classes": {
            "loop_stuck": {
                "description": "Stuck toggling between transit tabs",
                "log_patterns": ["StateTransition FAIL", "[BLOCKED]"],
            },
            "invalid_element": {
                "description": "VLM referenced non-existent UI element",
                "log_patterns": ["[Guard]", "[SKIPPED]"],
            },
            "premature_finish": {
                "description": "FINISH while still showing transit (not walking) route",
                "log_patterns": ["FINISH"],
                "context_patterns": ["transit", "bus", "subway"],
            },
        },
    },

    # ══════════════════════════════════════════════════════════════════
    # TIER 3: Multi-field Input + Confirmation Trap
    # Wrapper 효과: Precondition + Prompt Wrapper (팝업 오판 방지)
    # 난이도: ★★★  — 여러 필드 입력, 확인 팝업
    # ══════════════════════════════════════════════════════════════════

    "myrealtrip_search_with_date": {
        "description": "Search tour → set date filter → open first result details",
        "app_package": "com.mrt.ducati",
        "task": (
            "On MyRealTrip, search for 'Osaka'. "
            "Set the travel date to next month. "
            "Sort results by 'popularity' or 'review score' if available. "
            "Open the first tour result and check the price."
        ),
        "success_keywords": ["Osaka", "tour", "price", "date", "review"],
        "expected_steps": 8,
        "wrapper_targets": ["precondition", "state_transition"],
        "error_classes": {
            "missing_date": {
                "description": "Opened tour without setting date (precondition)",
                "log_patterns": ["Precondition FAIL", "date", "select"],
            },
            "loop_stuck": {
                "description": "Stuck on calendar or filter UI",
                "log_patterns": ["StateTransition FAIL", "[BLOCKED]"],
            },
            "invalid_element": {
                "description": "VLM hallucinated non-existent UI element",
                "log_patterns": ["[Guard]", "[SKIPPED]"],
            },
        },
    },

    "cgv_check_specific_movie": {
        "description": "Find specific movie → select theater → check showtime (no booking)",
        "app_package": "com.cgv.android.movieapp",
        "task": (
            "On CGV app, find the movie schedule. "
            "Search or browse for any currently showing movie. "
            "Select a CGV theater near 'Gangnam'. "
            "Check the available showtimes for today. "
            "Do NOT book or purchase tickets — just view the times."
        ),
        "success_keywords": ["showtime", "theater", "screen", "time", "Gangnam"],
        "expected_steps": 7,
        "wrapper_targets": ["irreversible_guard", "state_transition"],
        "error_classes": {
            "accidental_booking": {
                "description": "Entered booking/seat selection flow",
                "log_patterns": ["purchase", "pay", "book", "seat",
                                 "IrreversibleGuard"],
            },
            "loop_stuck": {
                "description": "Stuck navigating between movie list and theater list",
                "log_patterns": ["StateTransition FAIL", "[BLOCKED]"],
            },
            "premature_finish": {
                "description": "FINISH before showtimes were visible",
                "log_patterns": ["FINISH"],
                "context_patterns": ["movie", "now showing", "select"],
            },
            "invalid_element": {
                "description": "VLM hallucinated non-existent UI element",
                "log_patterns": ["[Guard]", "[SKIPPED]"],
            },
        },
    },

    "clock_alarm_with_label_and_repeat": {
        "description": "Create alarm with specific time + label + repeat days",
        "app_package": "com.google.android.deskclock",
        "task": (
            "In the Clock app, create a new alarm for 6:45 AM. "
            "Set the label to 'Morning Workout'. "
            "Set it to repeat on Monday, Wednesday, and Friday only. "
            "Save the alarm."
        ),
        "success_keywords": ["6:45", "alarm", "workout", "Mon", "Wed", "Fri",
                             "repeat", "label"],
        "expected_steps": 8,
        "wrapper_targets": ["precondition", "state_transition"],
        "error_classes": {
            "missing_config": {
                "description": "Saved alarm without setting label or repeat days",
                "log_patterns": ["Precondition FAIL"],
            },
            "loop_stuck": {
                "description": "Stuck on time picker or repeat day selection",
                "log_patterns": ["StateTransition FAIL", "[BLOCKED]"],
            },
            "premature_finish": {
                "description": "FINISH before saving the alarm (still on edit screen)",
                "log_patterns": ["FINISH"],
                "context_patterns": ["alarm", "time", "edit"],
            },
        },
    },

    # ══════════════════════════════════════════════════════════════════
    # TIER 4: Compound Navigation + Distractor UI
    # Wrapper 효과: All 3 checks + Prompt Wrapper
    # 난이도: ★★★★  — 복합 조건, 많은 UI 요소, 혼동 가능 버튼
    # ══════════════════════════════════════════════════════════════════

    "navermap_route_then_save": {
        "description": "Search → get route → save the place to favorites",
        "app_package": "com.nhn.android.nmap",
        "task": (
            "In Naver Map, search for 'Gyeongbokgung Palace'. "
            "Get transit directions from 'Seoul Station' to Gyeongbokgung. "
            "After viewing the route, go back to the place detail page "
            "and save (bookmark) Gyeongbokgung to your favorites."
        ),
        "success_keywords": ["bookmark", "saved", "favorite", "route", "Gyeongbokgung"],
        "expected_steps": 10,
        "wrapper_targets": ["state_transition", "precondition"],
        "error_classes": {
            "loop_stuck": {
                "description": "Stuck navigating between route view and place detail",
                "log_patterns": ["StateTransition FAIL", "[BLOCKED]"],
            },
            "invalid_element": {
                "description": "VLM hallucinated non-existent UI element",
                "log_patterns": ["[Guard]", "[SKIPPED]"],
            },
            "premature_finish": {
                "description": "FINISH after viewing route but before saving bookmark",
                "log_patterns": ["FINISH"],
                "context_patterns": ["route", "directions", "transit"],
            },
        },
    },

    "chrome_multi_tab_compare": {
        "description": "Open two tabs → search different things → switch between them",
        "app_package": "com.android.chrome",
        "task": (
            "In Chrome, search for 'Python list comprehension'. "
            "Then open a new tab and search for 'Python dictionary comprehension'. "
            "Switch back to the first tab to confirm both searches are preserved. "
            "FINISH on the first tab showing list comprehension results."
        ),
        "success_keywords": ["list comprehension", "tab", "Python"],
        "expected_steps": 9,
        "wrapper_targets": ["state_transition"],
        "error_classes": {
            "loop_stuck": {
                "description": "Stuck switching tabs or opening new tab",
                "log_patterns": ["StateTransition FAIL", "[BLOCKED]"],
            },
            "invalid_element": {
                "description": "VLM hallucinated non-existent UI element",
                "log_patterns": ["[Guard]", "[SKIPPED]"],
            },
            "premature_finish": {
                "description": "FINISH on second tab (dictionary) instead of first (list)",
                "log_patterns": ["FINISH"],
                "context_patterns": ["dictionary comprehension"],
            },
        },
    },

    "settings_wifi_connect_specific": {
        "description": "Navigate to Wi-Fi → toggle on → scroll to find specific network",
        "app_package": "com.android.settings",
        "task": (
            "Go to Settings → Network & Internet → Wi-Fi. "
            "Make sure Wi-Fi is turned ON. "
            "Scroll through the available networks list and find a network "
            "that contains '5G' or '5GHz' in its name. "
            "Tap on it to view its details (do NOT connect if it asks for password). "
            "FINISH when the network detail screen is visible."
        ),
        "success_keywords": ["5G", "Wi-Fi", "network", "signal", "detail"],
        "expected_steps": 7,
        "wrapper_targets": ["state_transition", "precondition"],
        "error_classes": {
            "loop_stuck": {
                "description": "Stuck scrolling Wi-Fi list without finding 5G network",
                "log_patterns": ["StateTransition FAIL", "[BLOCKED]"],
            },
            "premature_finish": {
                "description": "FINISH on Wi-Fi list instead of network detail screen",
                "log_patterns": ["FINISH"],
                "context_patterns": ["Wi-Fi", "available networks", "saved networks"],
            },
            "invalid_element": {
                "description": "VLM hallucinated non-existent UI element",
                "log_patterns": ["[Guard]", "[SKIPPED]"],
            },
        },
    },
}


# ─── 환경 검증 ───────────────────────────────────────────────────────────────

def _check_adb() -> tuple[bool, str]:
    """ADB 기기 연결 확인. (connected, detail_msg) 반환."""
    try:
        result = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=5
        )
        lines = [l.strip() for l in result.stdout.splitlines()
                 if l.strip() and "List of devices" not in l
                 and not l.startswith("*")]
        devices = [l for l in lines if "device" in l.split()[-1:]]
        if not devices:
            return False, "adb devices 출력에 연결된 기기 없음"
        return True, f"연결 기기: {', '.join(d.split()[0] for d in devices)}"
    except FileNotFoundError:
        return False, "adb 명령어를 찾을 수 없음 (PATH에 adb 미포함)"
    except Exception as e:
        return False, f"adb 실행 오류: {e}"


def _check_vllm() -> tuple[bool, str]:
    """vLLM 서버 연결 확인."""
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    if not base_url:
        return False, "OPENAI_BASE_URL 미설정 (source .env_appagent 필요)"
    health_url = base_url.replace("/v1", "") + "/health"
    try:
        result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "--connect-timeout", "5", health_url],
            capture_output=True, text=True, timeout=10
        )
        code = result.stdout.strip()
        if code == "200":
            return True, f"vLLM 정상 ({health_url})"
        return False, f"vLLM 응답 코드 {code} ({health_url})"
    except Exception as e:
        return False, f"vLLM 연결 실패 ({health_url}): {e}"


def _check_appagent() -> tuple[bool, str]:
    """AppAgent 설치 + 패치 상태 확인."""
    task_exec = APPAGENT_DIR / "scripts" / "task_executor.py"
    if not task_exec.exists():
        return False, f"task_executor.py 없음: {task_exec}"
    content = task_exec.read_text(encoding="utf-8")
    if "# [CAUSAL_PATCH]" not in content:
        return False, "task_executor.py에 Causal 패치 미적용"
    config_yaml = APPAGENT_DIR / "config.yaml"
    if not config_yaml.exists():
        return False, f"config.yaml 없음: {config_yaml}"
    return True, "AppAgent + 패치 확인됨"


def preflight_check() -> bool:
    """실행 전 환경 점검. 실패 시 구체적 안내 출력."""
    print(f"{'─'*60}")
    print(" Preflight Check")
    print(f"{'─'*60}")

    all_ok = True
    checks = [
        ("ADB 기기", _check_adb),
        ("vLLM 서버", _check_vllm),
        ("AppAgent", _check_appagent),
    ]
    for name, check_fn in checks:
        ok, msg = check_fn()
        status = "OK" if ok else "FAIL"
        icon = "  [+]" if ok else "  [!]"
        print(f"{icon} {name:<15} {status}  — {msg}")
        if not ok:
            all_ok = False

    # 환경변수 상태 (정보용)
    causal = os.environ.get("CAUSAL_MODE", "(unset)")
    wrapper = os.environ.get("WRAPPER_ENABLED", "(unset)")
    print(f"  [i] CAUSAL_MODE    = {causal}")
    print(f"  [i] WRAPPER_ENABLED= {wrapper}")
    print(f"{'─'*60}")

    if not all_ok:
        print("\n실행할 수 없습니다. 위 오류를 해결한 후 다시 시도하세요.")
        print("  힌트:")
        print("    source ~/AppAgent/.env_appagent")
        print("    source ~/appagent-env/bin/activate")
        print("    adb devices  # 기기 확인")
        print("    curl http://127.0.0.1:8080/health  # vLLM 확인")
    return all_ok


# ─── 실행 로직 ────────────────────────────────────────────────────────────────

def run_scenario(scenario_name: str, mode: str, rounds: int) -> dict:
    """AppAgent를 에뮬레이터 위에서 실제 실행하고 로그를 분석합니다."""
    if scenario_name not in SCENARIOS:
        print(f"알 수 없는 시나리오: {scenario_name}")
        print(f"사용 가능: {', '.join(SCENARIOS.keys())}")
        sys.exit(1)

    if not preflight_check():
        sys.exit(1)

    scenario = SCENARIOS[scenario_name]
    causal_enabled = mode == "treatment"

    print(f"\n{'='*60}")
    print(f" Scenario : {scenario['description']}")
    print(f" Mode     : {mode.upper()}")
    print(f" CAUSAL   : {'ON' if causal_enabled else 'OFF'}")
    print(f" WRAPPER  : {'ON' if causal_enabled else 'OFF'}")
    print(f" Rounds   : {rounds}")
    print(f" Timeout  : {ROUND_TIMEOUT}s per round")
    print(f"{'='*60}\n")

    results = {
        "scenario": scenario_name,
        "mode": mode,
        "causal_enabled": causal_enabled,
        "timestamp": datetime.now().isoformat(),
        "rounds_total": rounds,
        "rounds_completed": 0,
        "success_count": 0,
        "error_counts": {ec: 0 for ec in scenario["error_classes"]},
        "round_details": [],
    }

    for round_idx in range(1, rounds + 1):
        print(f"\n[Round {round_idx}/{rounds}] {'─'*40}")

        # 앱 강제 종료 후 재시작
        _reset_app(scenario["app_package"])
        time.sleep(1)
        _launch_app(scenario["app_package"])
        time.sleep(3)  # 앱 시작 대기

        round_result = _run_single_round(scenario, round_idx, causal_enabled)
        results["round_details"].append(round_result)
        results["rounds_completed"] += 1

        # 결과 판정
        if round_result["task_complete"]:
            results["success_count"] += 1
            print(f"  Result: SUCCESS ({round_result['steps_used']} steps)")
        else:
            reason = round_result.get("failure_reason", "unknown")
            print(f"  Result: FAIL — {reason} ({round_result['steps_used']} steps)")

        # 오류 클래스 감지
        combined_log = round_result["stdout"] + round_result.get("appagent_log", "")
        for ec_name, ec_info in scenario["error_classes"].items():
            if _detect_error(combined_log, ec_info):
                results["error_counts"][ec_name] += 1
                print(f"  Error detected: {ec_name} — {ec_info['description']}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    _save_results(results, scenario_name, mode)
    _print_round_summary(results, scenario)
    return results


def _run_single_round(
    scenario: dict, round_idx: int, causal_enabled: bool
) -> dict:
    """
    AppAgent의 task_executor.py를 직접 호출하여 한 라운드 실행.

    AppAgent run.py는 os.system()으로 task_executor.py를 호출하는데,
    이러면 subprocess의 stdin/stdout 캡처가 안 됨.
    따라서 task_executor.py를 직접 실행합니다.

    stdin 입력 순서:
      1. docs 확인: "y" (no docs 사용)
      2. task 설명: scenario["task"]
    (기기가 1대이면 기기 선택 프롬프트 생략됨)
    """
    python_bin = str(APPAGENT_VENV / "bin" / "python")
    task_script = str(APPAGENT_DIR / "scripts" / "task_executor.py")
    app = scenario["app_package"]

    if not Path(task_script).exists():
        return _make_fail_result(round_idx, f"task_executor.py not found: {task_script}")

    # stdin: docs(y) + task description
    task_text = scenario["task"]
    stdin_input = f"y\n{task_text}\n"

    # 환경변수 구성 — 현재 쉘 환경을 상속하고 모드만 덮어쓰기
    env = os.environ.copy()
    env["CAUSAL_MODE"] = "true" if causal_enabled else "false"
    env["WRAPPER_ENABLED"] = "true" if causal_enabled else "false"

    # task_executor.py 실행 전 task 디렉토리 목록 기록 (로그 파일 찾기용)
    tasks_dir = APPAGENT_DIR / "tasks"
    existing_tasks = set(tasks_dir.glob("task_*")) if tasks_dir.exists() else set()

    start_time = time.time()
    print(f"  Executing: {python_bin} {task_script} --app {app}")
    print(f"  Task: {task_text[:80]}{'...' if len(task_text) > 80 else ''}")

    try:
        proc = subprocess.run(
            [python_bin, task_script, "--app", app],
            input=stdin_input,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(APPAGENT_DIR),
            timeout=ROUND_TIMEOUT,
        )
        elapsed = time.time() - start_time
        stdout = proc.stdout + proc.stderr

        # AppAgent가 생성한 로그 파일 찾기
        appagent_log = ""
        new_tasks = set(tasks_dir.glob("task_*")) - existing_tasks if tasks_dir.exists() else set()
        log_file = None
        steps_used = 0
        if new_tasks:
            task_dir = max(new_tasks, key=lambda p: p.stat().st_mtime)
            log_files = list(task_dir.glob("log_*.txt"))
            if log_files:
                log_file = log_files[0]
                appagent_log = log_file.read_text(encoding="utf-8", errors="replace")
                # 각 줄이 JSON → step 수 = 줄 수
                steps_used = sum(1 for line in appagent_log.strip().splitlines() if line.strip())

        # 성공 판정: stdout에서 "Task completed successfully" 확인
        task_complete = "task completed successfully" in stdout.lower()

        # 실패 사유 분류
        failure_reason = ""
        if not task_complete:
            if proc.returncode != 0:
                failure_reason = f"exit code {proc.returncode}"
            elif "max rounds" in stdout.lower() or "reaching max" in stdout.lower():
                failure_reason = "max rounds exhausted"
            elif "no device found" in stdout.lower():
                failure_reason = "ADB device not found"
            elif "error" in stdout.lower().split("\n")[-3:]:
                failure_reason = "runtime error"
            else:
                failure_reason = "task not completed (unexpected exit)"

        return {
            "round": round_idx,
            "task_complete": task_complete,
            "failure_reason": failure_reason,
            "returncode": proc.returncode,
            "steps_used": steps_used,
            "elapsed_seconds": round(elapsed, 1),
            "stdout": stdout[:10000],
            "appagent_log": appagent_log[:20000],
            "log_file": str(log_file) if log_file else "",
        }

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start_time
        return {
            "round": round_idx,
            "task_complete": False,
            "failure_reason": f"timeout ({ROUND_TIMEOUT}s)",
            "returncode": -1,
            "steps_used": 0,
            "elapsed_seconds": round(elapsed, 1),
            "stdout": "",
            "appagent_log": "",
            "log_file": "",
        }
    except Exception as e:
        return _make_fail_result(round_idx, str(e))


def _make_fail_result(round_idx: int, reason: str) -> dict:
    return {
        "round": round_idx,
        "task_complete": False,
        "failure_reason": reason,
        "returncode": -1,
        "steps_used": 0,
        "elapsed_seconds": 0,
        "stdout": "",
        "appagent_log": "",
        "log_file": "",
    }


def _detect_error(log: str, error_info: dict) -> bool:
    """로그에서 특정 오류 클래스 패턴을 감지합니다."""
    if not log:
        return False
    log_lower = log.lower()
    log_patterns = error_info.get("log_patterns", [])
    context_patterns = error_info.get("context_patterns", [])

    if not log_patterns:
        return False

    has_log_pattern = any(p.lower() in log_lower for p in log_patterns if p)
    if not context_patterns:
        return has_log_pattern

    has_context = any(p.lower() in log_lower for p in context_patterns if p)
    return has_log_pattern and has_context


def _reset_app(package: str) -> None:
    """앱 강제 종료."""
    try:
        subprocess.run(
            ["adb", "shell", "am", "force-stop", package],
            capture_output=True, timeout=5
        )
    except Exception:
        pass


def _launch_app(package: str) -> None:
    """앱 메인 액티비티 시작."""
    try:
        subprocess.run(
            ["adb", "shell", "monkey", "-p", package,
             "-c", "android.intent.category.LAUNCHER", "1"],
            capture_output=True, timeout=5
        )
    except Exception:
        pass


def _save_results(results: dict, scenario_name: str, mode: str) -> None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = RESULTS_DIR / f"{scenario_name}_{mode}_{ts}.json"

    # round_details의 stdout/appagent_log를 별도 파일로 분리 (JSON 크기 관리)
    details_dir = RESULTS_DIR / "logs"
    details_dir.mkdir(parents=True, exist_ok=True)
    for rd in results.get("round_details", []):
        round_n = rd.get("round", 0)
        # stdout/appagent_log → 별도 파일, 결과 JSON에는 경로만 기록
        for key in ("stdout", "appagent_log"):
            content = rd.get(key, "")
            if len(content) > 500:
                log_filename = f"{scenario_name}_{mode}_{ts}_r{round_n}_{key}.txt"
                log_path = details_dir / log_filename
                log_path.write_text(content, encoding="utf-8")
                rd[key] = f"[see logs/{log_filename}]"

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved: {filename}")


def _print_round_summary(results: dict, scenario: dict) -> None:
    n = results["rounds_completed"]
    if n == 0:
        print("No rounds completed.")
        return

    print(f"\n{'='*60}")
    print(f" {results['mode'].upper()} Mode — {results['scenario']}")
    print(f"{'='*60}")
    print(f" Rounds    : {n}/{results['rounds_total']}")
    success_pct = results['success_count'] / n * 100
    print(f" Success   : {results['success_count']}/{n} ({success_pct:.0f}%)")

    # 평균 step 수
    steps = [rd.get("steps_used", 0) for rd in results.get("round_details", []) if rd.get("steps_used")]
    if steps:
        print(f" Avg steps : {sum(steps)/len(steps):.1f}")

    # 평균 소요 시간
    times = [rd.get("elapsed_seconds", 0) for rd in results.get("round_details", []) if rd.get("elapsed_seconds")]
    if times:
        print(f" Avg time  : {sum(times)/len(times):.1f}s")

    print(f"\n Error class breakdown:")
    for ec, count in results["error_counts"].items():
        desc = scenario["error_classes"][ec]["description"]
        pct = count / n * 100
        print(f"   {ec:<30} {count}/{n} ({pct:4.0f}%)  {desc}")
    print(f"{'='*60}\n")


# ─── 비교 리포트 ──────────────────────────────────────────────────────────────

def compare_results(scenario_name: str) -> None:
    """저장된 결과 파일에서 Control vs Treatment 를 비교합니다."""
    if not RESULTS_DIR.exists():
        print(f"결과 디렉토리가 없습니다: {RESULTS_DIR}")
        return

    files = list(RESULTS_DIR.glob(f"{scenario_name}_*.json")) if scenario_name \
        else list(RESULTS_DIR.glob("*.json"))

    if not files:
        print("비교할 결과 파일이 없습니다.")
        print(f"  먼저 실험을 실행하세요: python poc_experiment.py --scenario cart_add --mode control")
        return

    # 가장 최근 control / treatment 파일 선택
    control_files = sorted([f for f in files if "_control_" in f.name])
    treatment_files = sorted([f for f in files if "_treatment_" in f.name])

    if not control_files and not treatment_files:
        print("control 또는 treatment 결과 파일이 없습니다.")
        return

    print(f"\n{'='*70}")
    print(f" PoC 결과 비교 — {scenario_name or '전체'}")
    print(f"{'='*70}")

    def load_latest(file_list: list[Path]) -> dict | None:
        if not file_list:
            return None
        with open(file_list[-1], encoding="utf-8") as f:
            return json.load(f)

    ctrl = load_latest(control_files)
    trt = load_latest(treatment_files)

    if ctrl:
        print(f"\n📋 Control  ({control_files[-1].name})")
        _print_summary_row(ctrl)

    if trt:
        print(f"\n📋 Treatment ({treatment_files[-1].name})")
        _print_summary_row(trt)

    if ctrl and trt:
        print(f"\n{'─'*70}")
        print(f" {'지표':<30} {'Control':>10} {'Treatment':>10} {'개선율':>10}")
        print(f"{'─'*70}")

        ctrl_n = ctrl["rounds_completed"] or 1
        trt_n = trt["rounds_completed"] or 1

        # 오류율 비교
        all_error_keys = set(ctrl.get("error_counts", {}).keys()) | \
                         set(trt.get("error_counts", {}).keys())
        for ec in sorted(all_error_keys):
            ctrl_count = ctrl.get("error_counts", {}).get(ec, 0)
            trt_count = trt.get("error_counts", {}).get(ec, 0)
            ctrl_rate = ctrl_count / ctrl_n
            trt_rate = trt_count / trt_n
            improvement = _improvement(ctrl_rate, trt_rate, lower_is_better=True)
            print(f" {ec:<30} {ctrl_count}/{ctrl_n:>3}      {trt_count}/{trt_n:>3}      {improvement:>10}")

        # 성공률 비교
        ctrl_success = ctrl["success_count"] / ctrl_n
        trt_success = trt["success_count"] / trt_n
        improvement = _improvement(ctrl_success, trt_success, lower_is_better=False)
        print(f" {'task_success_rate':<30} "
              f"{ctrl['success_count']}/{ctrl_n:>3}      "
              f"{trt['success_count']}/{trt_n:>3}      {improvement:>10}")

        print(f"{'='*70}\n")


def _print_summary_row(r: dict) -> None:
    n = r["rounds_completed"] or 1
    success_rate = r["success_count"] / n * 100
    print(f"   success_rate: {r['success_count']}/{r['rounds_completed']} ({success_rate:.0f}%)")
    for ec, count in r.get("error_counts", {}).items():
        print(f"   {ec}: {count}/{r['rounds_completed']}")


def _improvement(ctrl: float, trt: float, lower_is_better: bool) -> str:
    if ctrl == 0 and trt == 0:
        return "±0%"
    if ctrl == 0:
        return "+∞" if not lower_is_better else "-∞"
    delta = (ctrl - trt) / ctrl * 100
    if lower_is_better:
        arrow = "↓" if delta > 0 else "↑"
        return f"{abs(delta):.0f}%{arrow}"
    else:
        arrow = "↑" if delta > 0 else "↓"
        return f"{abs(delta):.0f}%{arrow}"


def list_results() -> None:
    if not RESULTS_DIR.exists() or not list(RESULTS_DIR.glob("*.json")):
        print(f"결과 없음 ({RESULTS_DIR})")
        return
    print(f"\n저장된 결과 ({RESULTS_DIR}):")
    for f in sorted(RESULTS_DIR.glob("*.json")):
        with open(f, encoding="utf-8") as fp:
            r = json.load(fp)
        n = r.get("rounds_completed", 0)
        s = r.get("success_count", 0)
        mode = r.get("mode", "?")
        scenario = r.get("scenario", "?")
        print(f"  {f.name:<50} mode={mode:<10} success={s}/{n}")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def print_scenarios() -> None:
    """사용 가능한 시나리오 목록 출력."""
    print(f"\n{'─'*70}")
    print(f" {'Scenario':<35} {'App':<30} {'Steps':>5}")
    print(f"{'─'*70}")
    for name, s in SCENARIOS.items():
        pkg = s["app_package"].split(".")[-1]
        steps = s.get("expected_steps", "?")
        print(f" {name:<35} {pkg:<30} {steps:>5}")
    print(f"{'─'*70}")
    print(f" Total: {len(SCENARIOS)} scenarios\n")


def main():
    parser = argparse.ArgumentParser(
        description="Causal World Model PoC Experiment Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Flat argument style (primary interface)
    parser.add_argument("--scenario", choices=list(SCENARIOS.keys()),
                        help="Scenario to run")
    parser.add_argument("--mode", choices=["control", "treatment"],
                        help="control=Causal OFF, treatment=Causal ON")
    parser.add_argument("--rounds", type=int, default=3,
                        help="Number of rounds (default: 3)")
    parser.add_argument("--compare", action="store_true",
                        help="Compare control vs treatment results")
    parser.add_argument("--list", action="store_true",
                        help="List saved results")
    parser.add_argument("--scenarios", action="store_true",
                        help="List available scenarios")
    parser.add_argument("--check", action="store_true",
                        help="Run preflight check only")

    args = parser.parse_args()

    if args.scenarios:
        print_scenarios()
    elif args.check:
        preflight_check()
    elif args.compare:
        scenario = args.scenario or ""
        compare_results(scenario)
    elif args.list:
        list_results()
    elif args.scenario and args.mode:
        run_scenario(args.scenario, args.mode, args.rounds)
    else:
        parser.print_help()
        print("\nQuick start:")
        print("  python poc_experiment.py --scenarios              # 시나리오 목록")
        print("  python poc_experiment.py --check                  # 환경 점검")
        print("  python poc_experiment.py --scenario settings_developer_usb_debug --mode control --rounds 3")


if __name__ == "__main__":
    main()
