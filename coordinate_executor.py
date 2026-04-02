"""
coordinate_executor.py — 좌표 직접 출력 Task Executor

Qwen3.5 (네이티브 멀티모달, GUI Agent 내장) 또는 UI-TARS 모델을 사용.
스크린샷만으로 직접 좌표를 출력. uiautomator dump, 번호 라벨링 없이 동작.

지원 모델:
  - bytedance-research/UI-TARS-72B-SFT (권장, UI 전용 학습)
  - bytedance-research/UI-TARS-7B-SFT
  - Qwen2.5-VL 계열

사용법:
  python scripts/coordinate_executor.py --app com.coupang.mobile --task "Search for Nike shoes"
"""

import argparse
import datetime
import json
import os
import re
import sys
import time

from config import load_config
from and_controller import list_all_devices, AndroidController
from model import OpenAIModel, QwenModel
from utils import print_with_color

try:
    from causal_wrapper import CausalWorldModel
    from causal_action_wrapper import CausalWrapper, ProposedAction, is_wrapper_enabled
    HAS_CAUSAL = True
except ImportError:
    HAS_CAUSAL = False


# ─── UI-TARS 프롬프트 ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a GUI agent. You are given a task and a screenshot of a mobile phone. You need to perform actions to complete the task.

## Output Format
Your output must follow this exact format:

Observation: <Describe what you see on the screen>
Thought: <Your reasoning about what to do next>
Action: <Exactly ONE action from the list below>
Summary: <Brief description of what you did>

## Available Actions

click(x, y): Click at pixel coordinates (x, y). Example: click(540, 1200)
long_press(x, y): Long press at coordinates. Example: long_press(300, 500)
type(text): Type text into the focused input field. Example: type(Nike Air Force 1)
press(key): Press a key. Options: enter, back, home. Example: press(enter)
scroll(x, y, direction): Scroll at position. direction: up, down, left, right. Example: scroll(540, 1200, down)
wait(): Wait for the screen to load.
finished(): Task is complete.

## Important Rules
- The screen resolution is {width}x{height} pixels.
- Coordinates must be integers within bounds: x in [0, {width}], y in [0, {height}].
- Click the CENTER of the target element.
- After type() to enter text, use press(enter) on the NEXT step to submit/search.
- Keyboard buttons (search icon, etc.) cannot be clicked. Use press(enter) instead.
- Only output ONE action per step.
"""

TASK_PROMPT = """Task: {task_description}

Previous actions: {last_act}

Given the screenshot, decide the next action."""


# ─── 응답 파싱 ───────────────────────────────────────────────────────────────

def parse_response(rsp):
    """UI-TARS 응답 파싱. 좌표는 0-1000 정규화 스케일."""
    try:
        observation = re.findall(r"Observation:\s*(.*?)(?=\n\s*Thought:|\Z)", rsp, re.DOTALL)
        think = re.findall(r"Thought:\s*(.*?)(?=\n\s*Action:|\Z)", rsp, re.DOTALL)
        act_match = re.findall(r"Action:\s*(.*?)(?=\n\s*Summary:|\Z)", rsp, re.DOTALL)
        summary = re.findall(r"Summary:\s*(.*?)$", rsp, re.MULTILINE)

        obs_text = observation[0].strip() if observation else ""
        think_text = think[0].strip() if think else ""
        act = act_match[0].strip() if act_match else ""
        summary_text = summary[0].strip() if summary else ""

        print_with_color("Observation:", "yellow")
        print_with_color(obs_text[:200], "magenta")
        print_with_color("Thought:", "yellow")
        print_with_color(think_text[:200], "magenta")
        print_with_color("Action:", "yellow")
        print_with_color(act, "magenta")
        if summary_text:
            print_with_color("Summary:", "yellow")
            print_with_color(summary_text, "magenta")

        if not act:
            return {"action": "ERROR", "summary": "No action found", "raw": rsp}

        # finished / FINISH
        if "finished" in act.lower() or "FINISH" in act:
            return {"action": "FINISH", "summary": summary_text}

        # ── UI-TARS 포맷: click(start_box='(x,y)') ──
        m = re.search(r"click\(\s*start_box\s*=\s*['\"]?\((\d+)\s*,\s*(\d+)\)['\"]?\s*\)", act)
        if m:
            return {"action": "click",
                    "x": int(m.group(1)), "y": int(m.group(2)),
                    "normalized": True, "summary": summary_text}

        # ── UI-TARS 포맷: type(content='text') / type('text') / type(text) ──
        # 따옴표 있는 경우
        m = re.search(r"type\(\s*(?:content\s*=\s*)?['\"](.+?)['\"]\s*\)", act)
        if m:
            return {"action": "type", "text": m.group(1), "summary": summary_text}
        # 따옴표 없는 경우: type(content=hello) / type(hello world)
        m = re.search(r"type\(\s*(?:content\s*=\s*)?(.+?)\s*\)", act)
        if m and m.group(1).strip():
            return {"action": "type", "text": m.group(1).strip(), "summary": summary_text}

        # ── UI-TARS 포맷: long_press(start_box='(x,y)') ──
        m = re.search(r"long_press\(\s*start_box\s*=\s*['\"]?\((\d+)\s*,\s*(\d+)\)['\"]?\s*\)", act)
        if m:
            return {"action": "long_press",
                    "x": int(m.group(1)), "y": int(m.group(2)),
                    "normalized": True, "summary": summary_text}

        # ── UI-TARS 포맷: scroll(start_box='(x,y)', direction='down') / scroll(direction='down') ──
        m = re.search(
            r"scroll\(\s*start_box\s*=\s*['\"]?\((\d+)\s*,\s*(\d+)\)['\"]?\s*,\s*direction\s*=\s*['\"]?(\w+)['\"]?\s*\)",
            act)
        if m:
            return {"action": "scroll",
                    "x": int(m.group(1)), "y": int(m.group(2)),
                    "direction": m.group(3).lower(),
                    "normalized": True, "summary": summary_text}

        # scroll without start_box: scroll(direction='down')
        m = re.search(r"scroll\(\s*direction\s*=\s*['\"]?(\w+)['\"]?\s*\)", act)
        if m:
            return {"action": "scroll",
                    "x": 500, "y": 500,
                    "direction": m.group(1).lower(),
                    "normalized": True, "summary": summary_text}

        # ── UI-TARS 포맷: press(key='enter') / press(enter) / hotkey('enter') ──
        m = re.search(r"(?:press|hotkey)\(\s*(?:key\s*=\s*)?['\"]?(\w+)['\"]?\s*\)", act)
        if m:
            return {"action": "press", "key": m.group(1).lower(),
                    "summary": summary_text}

        # ── UI-TARS 포맷: drag(start_box='(x1,y1)', end_box='(x2,y2)') ──
        m = re.search(
            r"drag\(\s*start_box\s*=\s*['\"]?\((\d+)\s*,\s*(\d+)\)['\"]?\s*,\s*end_box\s*=\s*['\"]?\((\d+)\s*,\s*(\d+)\)['\"]?\s*\)",
            act)
        if m:
            return {"action": "swipe",
                    "x1": int(m.group(1)), "y1": int(m.group(2)),
                    "x2": int(m.group(3)), "y2": int(m.group(4)),
                    "normalized": True, "summary": summary_text}

        # ── wait() ──
        if "wait" in act.lower():
            return {"action": "wait", "summary": summary_text}

        # ── 폴백: 일반 좌표 포맷 click(x, y) ──
        m = re.match(r"click\(\s*(\d+)\s*,\s*(\d+)\s*\)", act)
        if m:
            return {"action": "click",
                    "x": int(m.group(1)), "y": int(m.group(2)),
                    "normalized": False, "summary": summary_text}

        # ── 폴백: tap(x, y) ──
        m = re.match(r"tap\(\s*(\d+)\s*,\s*(\d+)\s*\)", act)
        if m:
            return {"action": "click",
                    "x": int(m.group(1)), "y": int(m.group(2)),
                    "normalized": False, "summary": summary_text}

        # ── 폴백: text("...") ──
        m = re.match(r'text\(\s*["\'](.+?)["\']\s*\)', act)
        if m:
            return {"action": "type", "text": m.group(1), "summary": summary_text}

        # ── 폴백: enter() ──
        if act.strip() == "enter()":
            return {"action": "press", "key": "enter", "summary": summary_text}

        # ── 폴백: swipe(x1,y1,x2,y2) ──
        m = re.match(r"swipe\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", act)
        if m:
            return {"action": "swipe",
                    "x1": int(m.group(1)), "y1": int(m.group(2)),
                    "x2": int(m.group(3)), "y2": int(m.group(4)),
                    "normalized": False, "summary": summary_text}

        # ══════════════════════════════════════════════════════════════
        # 범용 폴백: 알 수 없는 액션을 키워드로 자동 추론
        # UI-TARS가 예상 못한 포맷을 출력해도 최대한 처리
        # ══════════════════════════════════════════════════════════════
        act_lower = act.lower().strip()

        # back 계열: press_back(), go_back(), back(), navigate_back()
        if "back" in act_lower:
            print_with_color(f"[Fallback] '{act}' → press(back)", "yellow")
            return {"action": "press", "key": "back", "summary": summary_text}

        # home 계열: press_home(), go_home(), home()
        if "home" in act_lower and "page" not in act_lower:
            print_with_color(f"[Fallback] '{act}' → press(home)", "yellow")
            return {"action": "press", "key": "home", "summary": summary_text}

        # enter/submit/search 계열: press_enter(), submit(), search()
        if any(k in act_lower for k in ("enter", "submit", "search", "confirm", "return")):
            print_with_color(f"[Fallback] '{act}' → press(enter)", "yellow")
            return {"action": "press", "key": "enter", "summary": summary_text}

        # 좌표가 포함된 미지 액션: 숫자 2개 추출해서 click으로 처리
        coords = re.findall(r"(\d{2,4})\s*,\s*(\d{2,4})", act)
        if coords:
            x, y = int(coords[0][0]), int(coords[0][1])
            # 1000 이하면 정규화, 초과면 픽셀로 판단
            normalized = x <= 1000 and y <= 1000
            print_with_color(f"[Fallback] '{act}' → click({x},{y}) normalized={normalized}", "yellow")
            return {"action": "click", "x": x, "y": y,
                    "normalized": normalized, "summary": summary_text}

        # wait/pause 계열
        if any(k in act_lower for k in ("wait", "pause", "sleep")):
            print_with_color(f"[Fallback] '{act}' → wait()", "yellow")
            return {"action": "wait", "summary": summary_text}

        # 완료 계열
        if any(k in act_lower for k in ("finish", "done", "complete", "end")):
            print_with_color(f"[Fallback] '{act}' → FINISH", "yellow")
            return {"action": "FINISH", "summary": summary_text}

        # 그래도 못 잡으면 에러 (여기까지 오면 정말 모르는 액션)
        print_with_color(f"ERROR: Unknown action (fallback failed): {act}", "red")
        return {"action": "ERROR", "summary": summary_text, "raw": act}

    except Exception as e:
        print_with_color(f"ERROR: Parse exception: {e}", "red")
        return {"action": "ERROR", "summary": str(e), "raw": rsp}


# ─── 액션 실행 ───────────────────────────────────────────────────────────────

def _to_pixels(parsed, width, height):
    """정규화 좌표(0-1000)를 픽셀 좌표로 변환."""
    if parsed.get("normalized", False):
        for key in ("x", "y", "x1", "y1", "x2", "y2"):
            if key in parsed:
                if key.startswith("x"):
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
        import subprocess
        ret = subprocess.run(
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
    parser = argparse.ArgumentParser(description="UI-TARS Coordinate Task Executor")
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

    # CausalWrapper
    causal_model = None
    action_wrapper = None
    if HAS_CAUSAL:
        _ce = os.environ.get("CAUSAL_MODE", "").strip().lower()
        _co = (_ce not in ("false", "0", "no", "off") if _ce
               else configs.get("CAUSAL_MODE", True))
        causal_model = CausalWorldModel(task_desc) if _co else None
        print(f"[Causal] CAUSAL_MODE={_co}")

        _we = os.environ.get("WRAPPER_ENABLED", "").strip().lower()
        _wo = (_we not in ("false", "0", "no", "off") if _we
               else configs.get("WRAPPER_ENABLED", True))
        action_wrapper = CausalWrapper(task_desc) if _wo else None
        print(f"[Causal] WRAPPER_ENABLED={_wo}")

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
        print_with_color(f"Round {round_count}", "yellow")

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

        # Causal 래핑
        if causal_model is not None:
            user_prompt = causal_model.wrap_prompt(user_prompt, last_act)

        # 전체 프롬프트 = 시스템 + 유저
        full_prompt = system_prompt + "\n\n" + user_prompt

        # VLM 호출
        print_with_color("Thinking...", "yellow")
        status, rsp = mllm.get_model_response(full_prompt, [screenshot_path])

        if not status:
            print_with_color(f"VLM error: {rsp}", "red")
            break

        # 로그
        with open(log_path, "a") as f:
            f.write(json.dumps({
                "step": round_count,
                "prompt": full_prompt,
                "image": os.path.basename(screenshot_path),
                "response": rsp,
            }, ensure_ascii=False) + "\n")

        # 파싱
        parsed = parse_response(rsp)

        # Causal 기록
        if causal_model is not None:
            causal_model.record_action(
                round_count, parsed["action"], parsed.get("summary", "")
            )

        if parsed["action"] == "FINISH":
            task_complete = True
            break
        if parsed["action"] == "ERROR":
            last_act = f"[ERROR] {parsed.get('raw', '')[:100]}"
            time.sleep(request_interval)
            continue

        # CausalWrapper 검증
        if action_wrapper is not None:
            proposed = ProposedAction(
                act_name=parsed["action"],
                summary=parsed.get("summary", ""),
                raw_response=rsp,
            )
            decision = action_wrapper.evaluate(proposed)
            if not decision.approved:
                print_with_color(f"[CausalWrapper] BLOCKED: {decision.reason}", "red")
                action_wrapper.record_step(proposed)
                last_act = f"[BLOCKED] {decision.reason}"
                time.sleep(request_interval)
                continue
            action_wrapper.record_step(proposed)

        # 실행
        ret = execute_action(controller, parsed, width, height)
        if ret == "ERROR":
            print_with_color(f"ERROR: {parsed['action']} failed", "red")
            break

        last_act = parsed.get("summary", str(parsed["action"]))
        time.sleep(request_interval)

    if task_complete:
        print_with_color("Task completed successfully", "yellow")
    elif round_count >= max_rounds:
        print_with_color("Task finished due to reaching max rounds", "yellow")
    else:
        print_with_color("Task finished unexpectedly", "red")


if __name__ == "__main__":
    main()
