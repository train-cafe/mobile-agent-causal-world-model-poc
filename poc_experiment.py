#!/usr/bin/env python3
"""
poc_experiment.py — Causal World Model PoC 실험 실행기

Control vs Treatment 비교를 자동화하여
"VLM 스케일업으로 해결 불가능한 구조적 오류 클래스"를 측정합니다.

Usage:
    # Control (no Causal)
    export CAUSAL_MODE=false WRAPPER_ENABLED=false
    python poc_experiment.py --scenario maps_search_location --mode control --rounds 5

    # Treatment (Causal + Wrapper)
    export CAUSAL_MODE=true WRAPPER_ENABLED=true
    python poc_experiment.py --scenario maps_search_location --mode treatment --rounds 5

    # Compare results
    python poc_experiment.py --compare --scenario maps_search_location

    # List saved results
    python poc_experiment.py --list
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ─── 설정값 ──────────────────────────────────────────────────────────────────
RESULTS_DIR = Path(os.environ.get("RESULTS_DIR", "~/poc_results")).expanduser()
APPAGENT_DIR = Path(os.environ.get("APPAGENT_DIR", "~/AppAgent")).expanduser()
APPAGENT_VENV = Path(os.environ.get("APPAGENT_VENV", "~/appagent-env")).expanduser()

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


# ─── 실행 로직 ────────────────────────────────────────────────────────────────

def run_scenario(scenario_name: str, mode: str, rounds: int) -> dict:
    """
    AppAgent 를 실행하고 오류 클래스별 발생 횟수를 기록합니다.

    실제 AppAgent 실행은 ADB 연결 및 에뮬레이터가 필요합니다.
    에뮬레이터 없이 실행하면 dry_run 모드로 구조만 테스트합니다.
    """
    if scenario_name not in SCENARIOS:
        print(f"❌ 알 수 없는 시나리오: {scenario_name}")
        print(f"   사용 가능: {list(SCENARIOS.keys())}")
        sys.exit(1)

    scenario = SCENARIOS[scenario_name]
    causal_enabled = mode == "treatment"

    print(f"\n{'='*60}")
    print(f" 시나리오: {scenario['description']}")
    print(f" 모드: {mode.upper()} (CAUSAL_MODE={'true' if causal_enabled else 'false'})")
    print(f" 반복 횟수: {rounds}")
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
        "round_logs": [],
    }

    # ADB 연결 확인
    adb_available = _check_adb()
    if not adb_available:
        print("⚠️  ADB 기기가 연결되지 않았습니다.")
        print("   dry_run 모드: 실제 AppAgent 실행 없이 결과 구조만 확인합니다.")
        results["dry_run"] = True
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        _save_results(results, scenario_name, mode)
        return results

    env = os.environ.copy()
    env["CAUSAL_MODE"] = "true" if causal_enabled else "false"

    for round_idx in range(1, rounds + 1):
        print(f"[Round {round_idx}/{rounds}] 시작...")
        round_result = _run_single_round(scenario, env, round_idx)
        results["round_logs"].append(round_result)
        results["rounds_completed"] += 1

        if round_result.get("success"):
            results["success_count"] += 1

        for error_class, error_info in scenario["error_classes"].items():
            if _detect_error(round_result["log"], error_info):
                results["error_counts"][error_class] += 1
                print(f"   ⚠️  오류 감지: {error_class} — {error_info['description']}")

        # 에뮬레이터 상태 리셋 (앱 재시작)
        _reset_app(scenario["app_package"])
        time.sleep(2)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    _save_results(results, scenario_name, mode)
    _print_round_summary(results, scenario)
    return results


def _run_single_round(scenario: dict, env: dict, round_idx: int) -> dict:
    """AppAgent 를 한 번 실행하고 로그를 캡처합니다."""
    python_bin = str(APPAGENT_VENV / "bin" / "python")
    run_script = str(APPAGENT_DIR / "run.py")

    if not Path(run_script).exists():
        return {"success": False, "log": "", "error": f"run.py not found: {run_script}"}

    task = scenario["task"]
    app = scenario["app_package"]
    stdin_input = f"y\n{task}\n"

    try:
        proc = subprocess.run(
            [python_bin, run_script, "--app", app],
            input=stdin_input,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(APPAGENT_DIR),
            timeout=300,   # 5분 타임아웃
        )
        log = proc.stdout + proc.stderr
        success = any(kw.lower() in log.lower() for kw in scenario["success_keywords"])
        return {
            "round": round_idx,
            "success": success,
            "returncode": proc.returncode,
            "log": log[:8000],   # 로그 최대 8KB
        }
    except subprocess.TimeoutExpired:
        return {"round": round_idx, "success": False, "log": "", "error": "timeout"}
    except Exception as e:
        return {"round": round_idx, "success": False, "log": "", "error": str(e)}


def _detect_error(log: str, error_info: dict) -> bool:
    """로그에서 특정 오류 클래스 패턴을 감지합니다."""
    log_lower = log.lower()
    log_patterns = error_info.get("log_patterns", [])
    context_patterns = error_info.get("context_patterns", [])

    has_log_pattern = any(p.lower() in log_lower for p in log_patterns if p)
    if not context_patterns:
        return has_log_pattern

    has_context = any(p.lower() in log_lower for p in context_patterns if p)
    return has_log_pattern and has_context


def _check_adb() -> bool:
    try:
        result = subprocess.run(
            ["adb", "devices"],
            capture_output=True, text=True, timeout=5
        )
        lines = [l.strip() for l in result.stdout.splitlines()
                 if l.strip() and "List of devices" not in l]
        return len(lines) > 0
    except Exception:
        return False


def _reset_app(package: str) -> None:
    try:
        subprocess.run(
            ["adb", "shell", "am", "force-stop", package],
            capture_output=True, timeout=5
        )
    except Exception:
        pass


def _save_results(results: dict, scenario_name: str, mode: str) -> None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = RESULTS_DIR / f"{scenario_name}_{mode}_{ts}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장: {filename}")


def _print_round_summary(results: dict, scenario: dict) -> None:
    n = results["rounds_completed"]
    print(f"\n{'='*60}")
    print(f" {results['mode'].upper()} 모드 결과 요약")
    print(f"{'='*60}")
    print(f" 완료 라운드: {n}/{results['rounds_total']}")
    print(f" 성공: {results['success_count']}/{n} "
          f"({results['success_count']/n*100:.0f}%)" if n else " 성공: 0/0")
    print(f"\n 오류 클래스별 발생률:")
    for ec, count in results["error_counts"].items():
        desc = scenario["error_classes"][ec]["description"]
        rate = f"{count}/{n}" if n else "0/0"
        print(f"   {ec:<25} {rate:<8} — {desc}")
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

def main():
    parser = argparse.ArgumentParser(
        description="Causal World Model PoC 실험 실행기",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command")

    # run subcommand (default)
    run_p = subparsers.add_parser("run", help="시나리오 실행")
    run_p.add_argument("--scenario", required=True, choices=list(SCENARIOS.keys()),
                       help="실행할 시나리오")
    run_p.add_argument("--mode", required=True, choices=["control", "treatment"],
                       help="control=Causal 없이, treatment=Causal 있이")
    run_p.add_argument("--rounds", type=int, default=5,
                       help="반복 횟수 (기본값: 5)")

    # compare subcommand
    cmp_p = subparsers.add_parser("compare", help="Control vs Treatment 결과 비교")
    cmp_p.add_argument("--scenario", default="", help="특정 시나리오만 비교 (생략 시 전체)")

    # list subcommand
    subparsers.add_parser("list", help="저장된 결과 목록 출력")

    # Legacy flat argument style for convenience
    # python poc_experiment.py --scenario X --mode Y --rounds N
    parser.add_argument("--scenario", choices=list(SCENARIOS.keys()))
    parser.add_argument("--mode", choices=["control", "treatment"])
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--list", action="store_true")

    args = parser.parse_args()

    if args.command == "compare" or args.compare:
        scenario = getattr(args, "scenario", "") or ""
        compare_results(scenario)
    elif args.command == "list" or args.list:
        list_results()
    elif args.command == "run" or (args.scenario and args.mode):
        scenario = args.scenario
        mode = args.mode
        rounds = args.rounds
        run_scenario(scenario, mode, rounds)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
