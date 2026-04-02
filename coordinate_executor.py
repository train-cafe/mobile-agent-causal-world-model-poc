"""
coordinate_executor.py — UI-TARS 1.5 기반 좌표 직접 출력 Task Executor

UI-TARS-1.5-7B 모델을 사용. 스크린샷만으로 직접 좌표를 출력.
uiautomator dump, 번호 라벨링 없이 동작.

모델: ByteDance-Seed/UI-TARS-1.5-7B (Qwen2-VL 기반, 7B)

사용법:
  python scripts/coordinate_executor.py --app com.coupang.mobile --task "Search for Nike shoes"
"""

import argparse
import datetime
import json
import os
import re
import subprocess as _subprocess
import sys
import time
from typing import Optional

def _maybe_add_appagent_to_syspath() -> None:
    """
    AppAgent 원본 모듈(and_controller/model/utils/config 등)이 이 레포에 없을 수 있어,
    기본 경로(~/AppAgent/scripts)를 sys.path에 추가한다.
    """
    candidates: list[str] = []
    env_dir = os.environ.get("APPAGENT_DIR", "").strip()
    if env_dir:
        candidates.append(env_dir)
    # 흔한 기본 설치 위치들
    candidates.extend(
        [
            os.path.expanduser("~/AppAgent"),
            "/home/kang9lee/AppAgent",
        ]
    )

    for base in candidates:
        if not base or not os.path.isdir(base):
            continue
        scripts_dir = os.path.join(base, "scripts")
        if os.path.isdir(scripts_dir) and scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        if base not in sys.path:
            sys.path.insert(0, base)


def _maybe_chdir_for_appagent_import() -> Optional[str]:
    """
    AppAgent의 `scripts/and_controller.py`는 import 시점에 `load_config()`를 호출하며,
    기본값으로 `./config.yaml`(cwd 기준)을 찾는다.

    현재 레포 루트에 config.yaml이 없으면, AppAgent 루트로 잠깐 chdir해서
    import-time config 로딩이 성공하도록 한다.
    """
    if os.path.exists(os.path.join(os.getcwd(), "config.yaml")):
        return None

    candidates: list[str] = []
    env_dir = os.environ.get("APPAGENT_DIR", "").strip()
    if env_dir:
        candidates.append(env_dir)
    candidates.extend(
        [
            os.path.expanduser("~/AppAgent"),
            "/home/kang9lee/AppAgent",
        ]
    )

    for base in candidates:
        if not base or not os.path.isdir(base):
            continue
        if os.path.exists(os.path.join(base, "config.yaml")):
            old = os.getcwd()
            os.chdir(base)
            return old
    return None


_maybe_add_appagent_to_syspath()


try:
    _old_cwd = _maybe_chdir_for_appagent_import()
    from and_controller import list_all_devices, AndroidController
    from model import OpenAIModel, QwenModel
    from utils import print_with_color
    if _old_cwd:
        os.chdir(_old_cwd)
except ModuleNotFoundError as e:
    raise ModuleNotFoundError(
        f"{e}. AppAgent 모듈이 필요합니다. "
        "환경변수 APPAGENT_DIR을 AppAgent 경로로 설정하거나 "
        "'~/AppAgent/scripts'가 존재하는지 확인하세요."
    )

try:
    from causal_action_wrapper import (
        CausalWrapper,
        ProposedAction,
        VLMCritic,
        is_wrapper_enabled,
    )
    HAS_CAUSAL = True
except ImportError:
    HAS_CAUSAL = False


# ─── Config loader (standalone-friendly) ─────────────────────────────────────
#
# 이 레포는 setup 스크립트에서 `config.yaml`을 생성하는 것을 전제로 하지만,
# 어떤 환경에서는 외부 패키지 `config`가 먼저 import되어 충돌이 발생할 수 있다.
# 그래서 다음 우선순위로 설정을 로딩한다:
# 1) 사용자가 제공한 `config.load_config` (정상일 때)
# 2) 현재 작업 디렉토리의 `config.yaml` (있으면)
# 3) 환경변수 기반 기본값

def _load_config_fallback() -> dict:
    # setup 스크립트에서 사용한 키들 기준
    cfg = {
        "MODEL": os.environ.get("MODEL_BACKEND", os.environ.get("MODEL", "OpenAI")),
        "OPENAI_API_BASE": os.environ.get(
            "OPENAI_API_BASE",
            os.environ.get("APPAGENT_API_BASE", "http://127.0.0.1:8080/v1/chat/completions"),
        ),
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", "sk-unused"),
        "OPENAI_API_MODEL": os.environ.get("OPENAI_API_MODEL", os.environ.get("MODEL_ID", "bytedance-research/UI-TARS-72B-SFT")),
        "TEMPERATURE": float(os.environ.get("TEMPERATURE", "0.2")),
        "MAX_TOKENS": int(os.environ.get("MAX_TOKENS", "1024")),
        "MAX_ROUNDS": int(os.environ.get("MAX_ROUNDS", "20")),
        "REQUEST_INTERVAL": int(os.environ.get("REQUEST_INTERVAL", "3")),
        "WRAPPER_ENABLED": os.environ.get("WRAPPER_ENABLED", "true").strip().lower() not in ("false", "0", "no", "off"),
        "VLM_CRITIC_ENABLED": os.environ.get("VLM_CRITIC_ENABLED", "").strip().lower() in ("1", "true", "yes", "on"),
        # QwenModel용
        "DASHSCOPE_API_KEY": os.environ.get("DASHSCOPE_API_KEY", "sk-unused"),
        "QWEN_MODEL": os.environ.get("QWEN_MODEL", "qwen-vl-max"),
    }
    return cfg


def _load_config_yaml(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    try:
        import yaml  # type: ignore
    except Exception:
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _resolve_config_yaml_path() -> Optional[str]:
    """
    config.yaml 위치를 절대경로로 찾는다.
    우선순위:
    1) 현재 작업 디렉토리의 ./config.yaml
    2) APPAGENT_DIR/config.yaml
    3) ~/AppAgent/config.yaml 또는 /home/kang9lee/AppAgent/config.yaml
    """
    cwd_path = os.path.join(os.getcwd(), "config.yaml")
    if os.path.exists(cwd_path):
        return cwd_path

    env_dir = os.environ.get("APPAGENT_DIR", "").strip()
    if env_dir:
        p = os.path.join(env_dir, "config.yaml")
        if os.path.exists(p):
            return p

    for base in (os.path.expanduser("~/AppAgent"), "/home/kang9lee/AppAgent"):
        p = os.path.join(base, "config.yaml")
        if os.path.exists(p):
            return p

    return None


try:
    # 정상적인 local config 모듈이 존재하는 환경이면 사용
    from config import load_config as _load_config  # type: ignore

    def load_config() -> dict:  # noqa: F811
        config_path = _resolve_config_yaml_path()
        # AppAgent의 load_config는 기본이 "./config.yaml"(cwd)라서,
        # 절대경로를 명시해 cwd에 의존하지 않도록 한다.
        cfg = _load_config(config_path) if config_path else _load_config()
        # None 방어
        return cfg if isinstance(cfg, dict) else _load_config_fallback()

except Exception:

    def load_config() -> dict:  # type: ignore
        # 1) config.yaml 먼저 시도 (레포 루트 실행 가정)
        config_path = _resolve_config_yaml_path()
        cfg = _load_config_yaml(config_path) if config_path else None
        if cfg is not None:
            return cfg
        # 2) 폴백
        return _load_config_fallback()


# ─── 프롬프트 ────────────────────────────────────────────────────────────────
# UI-TARS 1.5는 자체 학습된 포맷이 있음. 최소한의 프롬프트만 제공.
# 모델이 Observation/Thought를 출력할 수도 있고 안 할 수도 있음 — 강제하지 않음.

SYSTEM_PROMPT = """You are a GUI agent. You are given a task and a screenshot of a mobile phone screen.
You must perform actions to complete the task step by step.

Screen Resolution: {width}x{height}

Available actions:
- click(start_box='(x,y)'): Click at normalized coordinates (0-1000 scale)
- type(content='text'): Type text into focused input field
- press(key): Press a key (enter, back, home)
- scroll(start_box='(x,y)', direction='up/down/left/right'): Scroll
- drag(start_box='(x1,y1)', end_box='(x2,y2)'): Drag/swipe
- finished(): Task is complete

You MUST output your response in this format:
Observation: <describe what you see on the current screen>
Thought: <explain your reasoning for the next action>
Action: <exactly one action>
Summary: <brief description of what you did>
"""

TASK_PROMPT = """Task: {task_description}

Previous actions: {last_act}

Look at the screenshot carefully. Describe what you see, think about what to do next, then perform ONE action."""


# ─── 응답 파싱 ───────────────────────────────────────────────────────────────

def parse_response(rsp):
    """
    UI-TARS 1.5 응답 파싱.

    UI-TARS는 Observation/Thought를 출력할 수도 있고 Action만 출력할 수도 있음.
    Action 라인만 확실히 추출하고, 나머지는 있으면 가져옴.
    좌표는 0-1000 정규화 스케일.
    """
    # 전체 응답에서 구조 추출 (있으면 가져오고 없으면 빈 문자열)
    obs_match = re.search(r"Observation:\s*(.*?)(?=\n\s*(?:Thought|Action):|\Z)", rsp, re.DOTALL)
    think_match = re.search(r"Thought:\s*(.*?)(?=\n\s*Action:|\Z)", rsp, re.DOTALL)
    summary_match = re.search(r"Summary:\s*(.*?)$", rsp, re.MULTILINE)

    obs_text = obs_match.group(1).strip() if obs_match else ""
    think_text = think_match.group(1).strip() if think_match else ""
    summary_text = summary_match.group(1).strip() if summary_match else ""

    # Action 추출 — "Action:" 이후 또는 응답 전체에서 액션 패턴 찾기
    act = ""
    act_match = re.search(r"Action:\s*(.*?)(?=\n\s*Summary:|\Z)", rsp, re.DOTALL)
    if act_match:
        act = act_match.group(1).strip()

    # Action: 태그가 없으면 응답 전체에서 액션 패턴 직접 검색
    if not act:
        action_patterns = [
            r"(click\(.*?\))",
            r"(type\(.*?\))",
            r"(press\(.*?\))",
            r"(press_\w+\(\))",
            r"(scroll\(.*?\))",
            r"(drag\(.*?\))",
            r"(long_press\(.*?\))",
            r"(hotkey\(.*?\))",
            r"(wait\(\))",
            r"(finished\(\))",
        ]
        for pat in action_patterns:
            m = re.search(pat, rsp)
            if m:
                act = m.group(1)
                break

    if not act:
        return {"action": "ERROR", "summary": "No action found",
                "raw": rsp, "observation": obs_text, "thought": think_text}

    # ── 파싱 결과에 항상 observation/thought 포함 ──
    base = {"summary": summary_text, "observation": obs_text,
            "thought": think_text, "raw_action": act}

    # finished / FINISH
    if "finished" in act.lower() or "FINISH" in act:
        return {**base, "action": "FINISH"}

    # click(start_box='(x,y)')
    m = re.search(r"click\(\s*start_box\s*=\s*['\"]?\((\d+)\s*,\s*(\d+)\)['\"]?\s*\)", act)
    if m:
        return {**base, "action": "click", "x": int(m.group(1)), "y": int(m.group(2)),
                "normalized": True}

    # type(content='text') / type('text') / type(text)
    m = re.search(r"type\(\s*(?:content\s*=\s*)?['\"](.+?)['\"]\s*\)", act)
    if m:
        return {**base, "action": "type", "text": m.group(1)}
    m = re.search(r"type\(\s*(?:content\s*=\s*)?(.+?)\s*\)", act)
    if m and m.group(1).strip():
        return {**base, "action": "type", "text": m.group(1).strip()}

    # long_press(start_box='(x,y)')
    m = re.search(r"long_press\(\s*start_box\s*=\s*['\"]?\((\d+)\s*,\s*(\d+)\)['\"]?\s*\)", act)
    if m:
        return {**base, "action": "long_press", "x": int(m.group(1)), "y": int(m.group(2)),
                "normalized": True}

    # scroll(start_box='(x,y)', direction='down')
    m = re.search(
        r"scroll\(\s*start_box\s*=\s*['\"]?\((\d+)\s*,\s*(\d+)\)['\"]?\s*,\s*direction\s*=\s*['\"]?(\w+)['\"]?\s*\)",
        act)
    if m:
        return {**base, "action": "scroll", "x": int(m.group(1)), "y": int(m.group(2)),
                "direction": m.group(3).lower(), "normalized": True}

    # scroll(direction='down')
    m = re.search(r"scroll\(\s*direction\s*=\s*['\"]?(\w+)['\"]?\s*\)", act)
    if m:
        return {**base, "action": "scroll", "x": 500, "y": 500,
                "direction": m.group(1).lower(), "normalized": True}

    # press(key='enter') / press(enter) / hotkey('enter')
    m = re.search(r"(?:press|hotkey)\(\s*(?:key\s*=\s*)?['\"]?(\w+)['\"]?\s*\)", act)
    if m:
        return {**base, "action": "press", "key": m.group(1).lower()}

    # drag(start_box='(x1,y1)', end_box='(x2,y2)')
    m = re.search(
        r"drag\(\s*start_box\s*=\s*['\"]?\((\d+)\s*,\s*(\d+)\)['\"]?\s*,\s*end_box\s*=\s*['\"]?\((\d+)\s*,\s*(\d+)\)['\"]?\s*\)",
        act)
    if m:
        return {**base, "action": "swipe",
                "x1": int(m.group(1)), "y1": int(m.group(2)),
                "x2": int(m.group(3)), "y2": int(m.group(4)),
                "normalized": True}

    # wait()
    if "wait" in act.lower():
        return {**base, "action": "wait"}

    # ── 폴백: click(x, y) / tap(x, y) ──
    m = re.match(r"(?:click|tap)\(\s*(\d+)\s*,\s*(\d+)\s*\)", act)
    if m:
        return {**base, "action": "click", "x": int(m.group(1)), "y": int(m.group(2)),
                "normalized": False}

    # text("...")
    m = re.match(r'text\(\s*["\'](.+?)["\']\s*\)', act)
    if m:
        return {**base, "action": "type", "text": m.group(1)}

    # enter()
    if act.strip() == "enter()":
        return {**base, "action": "press", "key": "enter"}

    # swipe(x1,y1,x2,y2)
    m = re.match(r"swipe\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", act)
    if m:
        return {**base, "action": "swipe",
                "x1": int(m.group(1)), "y1": int(m.group(2)),
                "x2": int(m.group(3)), "y2": int(m.group(4)),
                "normalized": False}

    # ── 범용 폴백: 키워드 추론 ──
    act_lower = act.lower().strip()

    if "back" in act_lower:
        print_with_color(f"[Fallback] '{act}' -> press(back)", "yellow")
        return {**base, "action": "press", "key": "back"}

    if "home" in act_lower and "page" not in act_lower:
        print_with_color(f"[Fallback] '{act}' -> press(home)", "yellow")
        return {**base, "action": "press", "key": "home"}

    if any(k in act_lower for k in ("enter", "submit", "search", "confirm", "return")):
        print_with_color(f"[Fallback] '{act}' -> press(enter)", "yellow")
        return {**base, "action": "press", "key": "enter"}

    coords = re.findall(r"(\d{2,4})\s*,\s*(\d{2,4})", act)
    if coords:
        x, y = int(coords[0][0]), int(coords[0][1])
        normalized = x <= 1000 and y <= 1000
        print_with_color(f"[Fallback] '{act}' -> click({x},{y})", "yellow")
        return {**base, "action": "click", "x": x, "y": y, "normalized": normalized}

    if any(k in act_lower for k in ("wait", "pause", "sleep")):
        return {**base, "action": "wait"}

    if any(k in act_lower for k in ("finish", "done", "complete", "end")):
        return {**base, "action": "FINISH"}

    print_with_color(f"ERROR: Unknown action (all fallbacks failed): {act}", "red")
    return {**base, "action": "ERROR", "raw": rsp}


# ─── 액션 실행 ───────────────────────────────────────────────────────────────

def _to_pixels(parsed, width, height):
    """정규화 좌표(0-1000)를 픽셀 좌표로 변환."""
    if parsed.get("normalized", False):
        for key in ("x", "y", "x1", "y1", "x2", "y2"):
            if key in parsed:
                if key.startswith("x") or key == "x":
                    parsed[key] = int(parsed[key] * width / 1000)
                else:
                    parsed[key] = int(parsed[key] * height / 1000)
    return parsed


def execute_action(controller, parsed, width, height):
    """파싱된 액션을 ADB로 실행. 정규화 좌표는 픽셀로 변환."""
    parsed = _to_pixels(parsed, width, height)
    action = parsed["action"]

    if action == "click":
        x = max(0, min(parsed["x"], width))
        y = max(0, min(parsed["y"], height))
        print_with_color(f"  -> click({x}, {y})", "cyan")
        return controller.tap(x, y)

    elif action == "type":
        text = parsed["text"]
        print_with_color(f'  -> type("{text}")', "cyan")
        return controller.text(text)

    elif action == "long_press":
        x = max(0, min(parsed["x"], width))
        y = max(0, min(parsed["y"], height))
        print_with_color(f"  -> long_press({x}, {y})", "cyan")
        return controller.long_press(x, y)

    elif action == "press":
        key = parsed["key"]
        print_with_color(f"  -> press({key})", "cyan")
        key_map = {
            "enter": "KEYCODE_ENTER",
            "back": "KEYCODE_BACK",
            "home": "KEYCODE_HOME",
        }
        keycode = key_map.get(key, f"KEYCODE_{key.upper()}")
        ret = _subprocess.run(
            ["adb", "-s", controller.device, "shell", "input", "keyevent", keycode],
            capture_output=True, text=True, timeout=5,
        )
        return ret.stdout if ret.returncode == 0 else "ERROR"

    elif action == "scroll":
        x, y = parsed["x"], parsed["y"]
        direction = parsed["direction"]
        print_with_color(f"  -> scroll({x}, {y}, {direction})", "cyan")
        dist_map = {"up": (0, -500), "down": (0, 500),
                    "left": (-500, 0), "right": (500, 0)}
        dx, dy = dist_map.get(direction, (0, -500))
        return controller.swipe_precise((x, y), (x + dx, y + dy))

    elif action == "swipe":
        x1, y1 = parsed["x1"], parsed["y1"]
        x2, y2 = parsed["x2"], parsed["y2"]
        print_with_color(f"  -> swipe({x1}, {y1}, {x2}, {y2})", "cyan")
        return controller.swipe_precise((x1, y1), (x2, y2))

    elif action == "wait":
        print_with_color("  -> wait(3s)", "cyan")
        time.sleep(3)
        return "OK"

    return "ERROR"


# ─── 메인 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="UI-TARS 1.5 Coordinate Task Executor")
    parser.add_argument("--app", required=True)
    parser.add_argument("--root_dir", default="./")
    parser.add_argument("--task", default="", help="Task description (skips prompt)")
    args = parser.parse_args()

    configs = load_config()
    app = args.app

    # VLM 모델 초기화
    if configs["MODEL"] == "OpenAI":
        mllm = OpenAIModel(
            base_url=configs["OPENAI_API_BASE"],
            api_key=configs["OPENAI_API_KEY"],
            model=configs["OPENAI_API_MODEL"],
            temperature=configs["TEMPERATURE"],
            max_tokens=configs["MAX_TOKENS"],
        )
    elif configs["MODEL"] == "Qwen":
        mllm = QwenModel(
            api_key=configs["DASHSCOPE_API_KEY"],
            model=configs["QWEN_MODEL"],
        )
    else:
        print_with_color(f"ERROR: Unsupported model: {configs['MODEL']}", "red")
        sys.exit()

    # ADB
    device_list = list_all_devices()
    if not device_list:
        print_with_color("ERROR: No device found!", "red")
        sys.exit()
    print_with_color(f"List of devices attached:\n{device_list}", "yellow")

    device = device_list[0] if len(device_list) == 1 else input("Select device: ")
    print_with_color(f"Device selected: {device}", "yellow")

    controller = AndroidController(device)
    width, height = controller.get_device_size()
    if not width or not height:
        print_with_color("ERROR: Invalid device size!", "red")
        sys.exit()
    print_with_color(f"Screen resolution: {width}x{height}", "yellow")

    # 태스크
    if args.task:
        task_desc = args.task
        print_with_color(f"Task: {task_desc}", "blue")
    else:
        print_with_color("Please enter the description of the task:", "blue")
        task_desc = input()

    # 작업 디렉토리
    work_dir = os.path.join(args.root_dir, "tasks")
    os.makedirs(work_dir, exist_ok=True)
    dir_name = datetime.datetime.now().strftime(f"task_{app}_%Y-%m-%d_%H-%M-%S")
    task_dir = os.path.join(work_dir, dir_name)
    os.makedirs(task_dir, exist_ok=True)
    log_path = os.path.join(task_dir, f"log_{app}_{dir_name}.txt")

    # CausalWrapper — Action Verifier만 사용 (Prompt Wrapper는 UI-TARS에서 무효)
    action_wrapper = None
    if HAS_CAUSAL:
        _we = os.environ.get("WRAPPER_ENABLED", "").strip().lower()
        _wo = (_we not in ("false", "0", "no", "off") if _we
               else configs.get("WRAPPER_ENABLED", True))

        _vce = os.environ.get("VLM_CRITIC_ENABLED", "").strip().lower()
        _vc_on = (
            _vce in ("1", "true", "yes", "on")
            if _vce
            else bool(configs.get("VLM_CRITIC_ENABLED", False))
        )

        if _wo and _vc_on:
            critic = VLMCritic(mllm)
            action_wrapper = CausalWrapper(
                task_desc,
                precondition_checker=critic,
                state_transition_checker=critic,
                irreversible_guard=critic,
            )
            print("[Causal] Action Verifier=ON (VLM Critic)")
        else:
            action_wrapper = CausalWrapper(task_desc) if _wo else None
            print(f"[Causal] Action Verifier={'ON' if _wo else 'OFF'}")
        print(f"[Causal] Prompt Wrapper=OFF (UI-TARS는 자체 포맷 사용)")

    # 시스템 프롬프트
    system_prompt = SYSTEM_PROMPT.format(width=width, height=height)

    # 메인 루프
    max_rounds = configs.get("MAX_ROUNDS", 20)
    request_interval = configs.get("REQUEST_INTERVAL", 3)
    round_count = 0
    last_act = "None"
    task_complete = False

    while round_count < max_rounds:
        round_count += 1
        print_with_color(f"\nRound {round_count}", "yellow")

        time.sleep(2)  # UI 안정화

        # 스크린샷
        screenshot_path = controller.get_screenshot(
            f"{dir_name}_{round_count}", task_dir
        )
        if screenshot_path == "ERROR":
            print_with_color("ERROR: Screenshot failed", "red")
            break

        # 프롬프트
        user_prompt = TASK_PROMPT.format(
            task_description=task_desc,
            last_act=last_act,
        )
        full_prompt = system_prompt + "\n\n" + user_prompt

        # VLM 호출
        print_with_color("Thinking...", "yellow")
        status, rsp = mllm.get_model_response(full_prompt, [screenshot_path])

        if not status:
            print_with_color(f"VLM error: {rsp}", "red")
            break

        # ── VLM 응답 전체 출력 (사용자가 모델의 판단을 볼 수 있도록) ──
        print_with_color("─── VLM Response ───", "yellow")
        for line in rsp.strip().split("\n"):
            print_with_color(f"  {line}", "magenta")
        print_with_color("────────────────────", "yellow")

        # 로그 (스크린샷 경로 포함)
        with open(log_path, "a") as f:
            f.write(json.dumps({
                "step": round_count,
                "prompt": full_prompt,
                "image": os.path.basename(screenshot_path),
                "response": rsp,
                "screenshot_path": screenshot_path,
            }, ensure_ascii=False) + "\n")

        # 파싱
        parsed = parse_response(rsp)

        # 파싱 결과 요약 출력
        if parsed.get("observation"):
            print_with_color(f"  Obs: {parsed['observation'][:150]}", "cyan")
        if parsed.get("thought"):
            print_with_color(f"  Think: {parsed['thought'][:150]}", "cyan")
        print_with_color(f"  Action: {parsed.get('raw_action', parsed['action'])}", "cyan")

        # FINISH / ERROR
        if parsed["action"] == "FINISH":
            task_complete = True
            break
        if parsed["action"] == "ERROR":
            last_act = f"[ERROR] {parsed.get('raw', '')[:100]}"
            time.sleep(request_interval)
            continue

        # CausalWrapper Action Verifier
        if action_wrapper is not None:
            # VLM critic용으로 좌표는 반드시 픽셀 기준으로 채워준다.
            critic_parsed = dict(parsed)
            if critic_parsed.get("normalized", False):
                critic_parsed = _to_pixels(critic_parsed, width, height)
                critic_parsed["normalized"] = False

            proposed = ProposedAction(
                act_name=parsed["action"],
                summary=parsed.get("observation", "")
                or parsed.get("summary", ""),
                raw_response=rsp,
                x=critic_parsed.get("x"),
                y=critic_parsed.get("y"),
                x1=critic_parsed.get("x1"),
                y1=critic_parsed.get("y1"),
                x2=critic_parsed.get("x2"),
                y2=critic_parsed.get("y2"),
                direction=critic_parsed.get("direction"),
                input_text=critic_parsed.get("text"),
            )
            decision = action_wrapper.evaluate(
                proposed, screenshot_path=screenshot_path
            )
            if not decision.approved:
                print_with_color(f"[CausalWrapper] BLOCKED: {decision.reason}", "red")
                action_wrapper.record_step(proposed, screenshot_path=screenshot_path)
                last_act = f"[BLOCKED] {decision.reason}"
                time.sleep(request_interval)
                continue
            action_wrapper.record_step(proposed, screenshot_path=screenshot_path)

        # 실행
        ret = execute_action(controller, parsed, width, height)
        if ret == "ERROR":
            print_with_color(f"ERROR: {parsed['action']} failed", "red")
            break

        # last_act: observation이 있으면 사용 (summary보다 정보가 많음)
        last_act = parsed.get("observation", "") or parsed.get("summary", "") or str(parsed["action"])
        time.sleep(request_interval)

    if task_complete:
        print_with_color("Task completed successfully", "yellow")
    elif round_count >= max_rounds:
        print_with_color("Task finished due to reaching max rounds", "yellow")
    else:
        print_with_color("Task finished unexpectedly", "red")


if __name__ == "__main__":
    main()
