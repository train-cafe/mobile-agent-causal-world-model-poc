"""
coordinate_executor.py — 좌표 직접 출력 방식의 Task Executor

AppAgent의 uiautomator + 번호 라벨 파이프라인을 제거하고,
VLM이 스크린샷을 보고 직접 (x, y) 좌표를 출력하는 방식.

기존 AppAgent와의 차이:
  - uiautomator dump 없음 (XML 파싱 없음)
  - 번호 라벨 이미지 없음 (원본 스크린샷 그대로 VLM에 전달)
  - VLM이 tap(x, y) 형태로 직접 좌표 출력
  - 키보드, 팝업, 오버레이 등 모든 UI 요소 인식 가능

사용법:
  기존 AppAgent 디렉토리에 복사 후 실행:
    python scripts/coordinate_executor.py --app com.coupang.mobile

  또는 poc_experiment.py에서 자동 호출.
"""

import argparse
import datetime
import json
import os
import re
import sys
import time

# AppAgent의 기존 모듈 재사용
from config import load_config
from and_controller import list_all_devices, AndroidController
from model import OpenAIModel, QwenModel
from utils import print_with_color

# CausalWrapper (패치 적용 시)
try:
    from causal_wrapper import CausalWorldModel
    from causal_action_wrapper import CausalWrapper, ProposedAction, is_wrapper_enabled
    HAS_CAUSAL = True
except ImportError:
    HAS_CAUSAL = False


# ─── 프롬프트 ────────────────────────────────────────────────────────────────

COORDINATE_PROMPT = """You are an agent that performs tasks on a smartphone. You will be given a screenshot of the phone screen.

You can call the following functions to control the smartphone:

1. tap(x, y)
Tap the screen at pixel coordinates (x, y).
Example: tap(540, 1200) taps the center-bottom area of a 1080-wide screen.

2. text(text_input)
Type text into the currently focused input field.
Example: text("Nike Air Force 1")

3. long_press(x, y)
Long press at pixel coordinates (x, y).
Example: long_press(540, 300)

4. swipe(x1, y1, x2, y2)
Swipe from (x1, y1) to (x2, y2).
Example: swipe(540, 1600, 540, 800) swipes up.

5. enter()
Press the Enter/Search/Submit key on the keyboard.
Use this after text() to submit a search query or confirm input.
IMPORTANT: Keyboard buttons (search icon, enter key, etc.) are NOT visible as tappable UI elements. Always use enter() instead of trying to tap keyboard buttons.

6. back()
Press the back button.

The screen resolution is {width}x{height} pixels.

IMPORTANT RULES:
- Output coordinates as integers within the screen bounds (0-{width} for x, 0-{height} for y).
- Tap the CENTER of the target element, not the edge.
- If you see a keyboard on screen and need to submit/search, use enter(), NOT tap on keyboard buttons.
- After using text() to type something, use enter() on the next step to submit.

The task you need to complete is: {task_description}

Your past actions: {last_act}

Now, given the screenshot, decide the next action. Output in this exact format:
Observation: <What you see on the screen>
Thought: <Your reasoning for the next action>
Action: <ONE function call, e.g. tap(540, 1200) or text("hello") or enter() or FINISH>
Summary: <Brief summary of what you did and why>

You can only take ONE action at a time. Output FINISH when the task is complete."""


# ─── 응답 파싱 ───────────────────────────────────────────────────────────────

def parse_coordinate_response(rsp):
    """VLM 응답에서 Action을 파싱. 좌표 기반 액션 지원."""
    try:
        observation = re.findall(r"Observation:\s*(.*?)$", rsp, re.MULTILINE)[0]
        think = re.findall(r"Thought:\s*(.*?)$", rsp, re.MULTILINE)[0]
        act = re.findall(r"Action:\s*(.*?)$", rsp, re.MULTILINE)[0]
        last_act = re.findall(r"Summary:\s*(.*?)$", rsp, re.MULTILINE)[0]

        print_with_color("Observation:", "yellow")
        print_with_color(observation, "magenta")
        print_with_color("Thought:", "yellow")
        print_with_color(think, "magenta")
        print_with_color("Action:", "yellow")
        print_with_color(act, "magenta")
        print_with_color("Summary:", "yellow")
        print_with_color(last_act, "magenta")

        if "FINISH" in act:
            return {"action": "FINISH", "summary": last_act}

        act_name = act.split("(")[0].strip()

        if act_name == "tap":
            coords = re.findall(r"tap\(\s*(\d+)\s*,\s*(\d+)\s*\)", act)
            if coords:
                x, y = int(coords[0][0]), int(coords[0][1])
                return {"action": "tap", "x": x, "y": y, "summary": last_act}

        elif act_name == "text":
            text_match = re.findall(r'text\(\s*["\'](.+?)["\']\s*\)', act)
            if text_match:
                return {"action": "text", "text": text_match[0], "summary": last_act}

        elif act_name == "long_press":
            coords = re.findall(r"long_press\(\s*(\d+)\s*,\s*(\d+)\s*\)", act)
            if coords:
                x, y = int(coords[0][0]), int(coords[0][1])
                return {"action": "long_press", "x": x, "y": y, "summary": last_act}

        elif act_name == "swipe":
            coords = re.findall(
                r"swipe\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", act
            )
            if coords:
                x1, y1, x2, y2 = (int(c) for c in coords[0])
                return {"action": "swipe", "x1": x1, "y1": y1,
                        "x2": x2, "y2": y2, "summary": last_act}

        elif act_name == "enter":
            return {"action": "enter", "summary": last_act}

        elif act_name == "back":
            return {"action": "back", "summary": last_act}

        print_with_color(f"ERROR: Failed to parse action: {act}", "red")
        return {"action": "ERROR", "summary": last_act, "raw": act}

    except Exception as e:
        print_with_color(f"ERROR: Parse exception: {e}", "red")
        print_with_color(rsp, "red")
        return {"action": "ERROR", "summary": str(e), "raw": rsp}


# ─── 액션 실행 ───────────────────────────────────────────────────────────────

def execute_action(controller, parsed, width, height):
    """파싱된 액션을 ADB로 실행."""
    action = parsed["action"]

    if action == "tap":
        x = max(0, min(parsed["x"], width))
        y = max(0, min(parsed["y"], height))
        print_with_color(f"  → tap({x}, {y})", "cyan")
        return controller.tap(x, y)

    elif action == "text":
        text = parsed["text"]
        print_with_color(f"  → text(\"{text}\")", "cyan")
        return controller.text(text)

    elif action == "long_press":
        x = max(0, min(parsed["x"], width))
        y = max(0, min(parsed["y"], height))
        print_with_color(f"  → long_press({x}, {y})", "cyan")
        return controller.long_press(x, y)

    elif action == "swipe":
        x1 = max(0, min(parsed["x1"], width))
        y1 = max(0, min(parsed["y1"], height))
        x2 = max(0, min(parsed["x2"], width))
        y2 = max(0, min(parsed["y2"], height))
        print_with_color(f"  → swipe({x1}, {y1}, {x2}, {y2})", "cyan")
        return controller.swipe_precise((x1, y1), (x2, y2))

    elif action == "enter":
        print_with_color("  → enter() [KEYCODE_ENTER]", "cyan")
        return controller.enter()

    elif action == "back":
        print_with_color("  → back()", "cyan")
        return controller.back()

    return "ERROR"


# ─── 메인 루프 ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Coordinate-based Task Executor")
    parser.add_argument("--app", required=True)
    parser.add_argument("--root_dir", default="./")
    parser.add_argument("--task", default="", help="Task description (skips interactive prompt)")
    args = parser.parse_args()

    configs = load_config()
    app = args.app
    root_dir = args.root_dir

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
        print_with_color(f"ERROR: Unsupported model type {configs['MODEL']}!", "red")
        sys.exit()

    # ADB 기기 연결
    device_list = list_all_devices()
    if not device_list:
        print_with_color("ERROR: No device found!", "red")
        sys.exit()
    print_with_color(f"List of devices attached:\n{device_list}", "yellow")

    if len(device_list) == 1:
        device = device_list[0]
        print_with_color(f"Device selected: {device}", "yellow")
    else:
        print_with_color("Please choose the Android device:", "blue")
        device = input()

    controller = AndroidController(device)
    width, height = controller.get_device_size()
    if not width and not height:
        print_with_color("ERROR: Invalid device size!", "red")
        sys.exit()
    print_with_color(f"Screen resolution: {width}x{height}", "yellow")

    # 태스크 입력: --task 인자 우선, 없으면 stdin
    if args.task:
        task_desc = args.task
        print_with_color(f"Task (from args): {task_desc}", "blue")
    else:
        print_with_color("Please enter the description of the task:", "blue")
        task_desc = input()

    # 작업 디렉토리 생성
    work_dir = os.path.join(root_dir, "tasks")
    os.makedirs(work_dir, exist_ok=True)
    task_timestamp = int(time.time())
    dir_name = datetime.datetime.fromtimestamp(task_timestamp).strftime(
        f"task_{app}_%Y-%m-%d_%H-%M-%S"
    )
    task_dir = os.path.join(work_dir, dir_name)
    os.makedirs(task_dir, exist_ok=True)
    log_path = os.path.join(task_dir, f"log_{app}_{dir_name}.txt")

    # CausalWrapper 초기화
    causal_model = None
    action_wrapper = None
    if HAS_CAUSAL:
        _causal_env = os.environ.get("CAUSAL_MODE", "").strip().lower()
        _causal_on = (
            _causal_env not in ("false", "0", "no", "off")
            if _causal_env
            else configs.get("CAUSAL_MODE", True)
        )
        causal_model = CausalWorldModel(task_desc) if _causal_on else None
        print(f"[Causal] CAUSAL_MODE={_causal_on}")

        _wrapper_env = os.environ.get("WRAPPER_ENABLED", "").strip().lower()
        _wrapper_on = (
            _wrapper_env not in ("false", "0", "no", "off")
            if _wrapper_env
            else configs.get("WRAPPER_ENABLED", True)
        )
        action_wrapper = CausalWrapper(task_desc) if _wrapper_on else None
        print(f"[Causal] WRAPPER_ENABLED={_wrapper_on}")

    # 메인 루프
    max_rounds = configs.get("MAX_ROUNDS", 20)
    request_interval = configs.get("REQUEST_INTERVAL", 3)
    round_count = 0
    last_act = "None"
    task_complete = False

    while round_count < max_rounds:
        round_count += 1
        print_with_color(f"Round {round_count}", "yellow")

        # UI 안정화 대기
        time.sleep(2)

        # 스크린샷만 캡처 (XML dump 없음!)
        screenshot_path = controller.get_screenshot(
            f"{dir_name}_{round_count}", task_dir
        )
        if screenshot_path == "ERROR":
            print_with_color("ERROR: Screenshot failed", "red")
            break

        # 프롬프트 구성
        prompt = COORDINATE_PROMPT.format(
            width=width,
            height=height,
            task_description=task_desc,
            last_act=last_act,
        )

        # CausalWrapper 프롬프트 래핑
        if causal_model is not None:
            prompt = causal_model.wrap_prompt(prompt, last_act)

        # VLM 호출
        print_with_color("Thinking about what to do...", "yellow")
        status, rsp = mllm.get_model_response(prompt, [screenshot_path])

        if not status:
            print_with_color(f"VLM error: {rsp}", "red")
            break

        # 로그 기록
        with open(log_path, "a") as logfile:
            log_item = {
                "step": round_count,
                "prompt": prompt,
                "image": os.path.basename(screenshot_path),
                "response": rsp,
            }
            logfile.write(json.dumps(log_item, ensure_ascii=False) + "\n")

        # 응답 파싱
        parsed = parse_coordinate_response(rsp)

        # CausalWrapper 기록
        if causal_model is not None:
            causal_model.record_action(
                round_count, parsed["action"], parsed.get("summary", "")
            )

        # FINISH / ERROR 체크
        if parsed["action"] == "FINISH":
            task_complete = True
            break
        if parsed["action"] == "ERROR":
            print_with_color("Parsing error, retrying next round...", "red")
            last_act = f"[ERROR] Failed to parse: {parsed.get('raw', '')[:100]}"
            time.sleep(request_interval)
            continue

        # CausalWrapper 액션 검증
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

        # 좌표 범위 검증
        if parsed["action"] in ("tap", "long_press"):
            if parsed["x"] < 0 or parsed["x"] > width or parsed["y"] < 0 or parsed["y"] > height:
                print_with_color(
                    f"[Guard] Coordinates out of bounds: ({parsed['x']}, {parsed['y']}). Clamping.",
                    "yellow",
                )

        # 액션 실행
        ret = execute_action(controller, parsed, width, height)
        if ret == "ERROR":
            print_with_color(f"ERROR: {parsed['action']} execution failed", "red")
            break

        last_act = parsed.get("summary", str(parsed["action"]))
        time.sleep(request_interval)

    # 종료 메시지
    if task_complete:
        print_with_color("Task completed successfully", "yellow")
    elif round_count >= max_rounds:
        print_with_color("Task finished due to reaching max rounds", "yellow")
    else:
        print_with_color("Task finished unexpectedly", "red")


if __name__ == "__main__":
    main()
