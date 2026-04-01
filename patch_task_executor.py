#!/usr/bin/env python3
"""
patch_task_executor.py — AppAgent task_executor.py 자동 패치

사용법:
    python patch_task_executor.py ~/AppAgent/scripts/task_executor.py

수행 내용 (6개 패치):
  1. import 추가 (causal_wrapper + causal_action_wrapper)
  2. causal_model 초기화 (프롬프트 래퍼)
  3. causal_wrapper 초기화 (액션 래퍼)
  4. VLM 호출 직전: wrap_prompt
  5. VLM 응답 파싱 직후: record_action + wrapper.evaluate()
  6. 액션 실행 직전: wrapper 판단에 따라 차단/실행

이미 패치된 파일에 다시 실행하면 중복 삽입 없이 안전하게 스킵합니다.
"""

import sys
import re
import shutil
from pathlib import Path


PATCH_MARKER = "# [CAUSAL_PATCH]"
WRAPPER_MARKER = "# [ACTION_WRAPPER_PATCH]"


def already_patched(source: str) -> bool:
    return WRAPPER_MARKER in source


def already_has_prompt_patch(source: str) -> bool:
    return PATCH_MARKER in source


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 1: Import 추가
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def insert_imports(lines: list[str]) -> list[str]:
    """Add both causal_wrapper and causal_action_wrapper imports."""
    import_block_end = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            import_block_end = i

    imports = (
        f"import os  {PATCH_MARKER}\n"
        f"from causal_wrapper import CausalWorldModel  {PATCH_MARKER}\n"
        f"from causal_action_wrapper import (  {WRAPPER_MARKER}\n"
        f"    CausalWrapper, ProposedAction, is_wrapper_enabled\n"
        f")\n"
    )
    for j, imp_line in enumerate(imports.splitlines(keepends=True)):
        lines.insert(import_block_end + 1 + j, imp_line)
    return lines


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 2: causal_model 초기화 (프롬프트 래퍼)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def insert_init(lines: list[str]) -> list[str]:
    """Insert causal_model + action_wrapper init before the main loop."""
    # Look for the round loop: `while round_count < ` or `for round_count in range`
    for i, line in enumerate(lines):
        if (re.search(r"while\s+round_count\s*<", line) or
                re.search(r"for\s+round_count\s+in\s+range", line)):
            indent = re.match(r"(\s*)", line).group(1)
            init_code = (
                f"{indent}# ── Causal 프롬프트 래퍼 초기화 ──  {PATCH_MARKER}\n"
                f"{indent}# 환경변수 우선, 미설정 시 config.yaml 참조\n"
                f"{indent}_causal_env = os.environ.get('CAUSAL_MODE', '').strip().lower()\n"
                f"{indent}_causal_on = (_causal_env not in ('false', '0', 'no', 'off')\n"
                f"{indent}    if _causal_env else configs.get('CAUSAL_MODE', True))\n"
                f"{indent}causal_model = CausalWorldModel(task_desc) if _causal_on else None\n"
                f"{indent}print(f'[Causal] CAUSAL_MODE={{_causal_on}} (env={{repr(_causal_env) if _causal_env else \"unset\"}})')\n"
                f"{indent}# ── Causal 액션 래퍼 초기화 ──  {WRAPPER_MARKER}\n"
                f"{indent}_wrapper_env = os.environ.get('WRAPPER_ENABLED', '').strip().lower()\n"
                f"{indent}_wrapper_on = (_wrapper_env not in ('false', '0', 'no', 'off')\n"
                f"{indent}    if _wrapper_env else configs.get('WRAPPER_ENABLED', True))\n"
                f"{indent}action_wrapper = CausalWrapper(task_desc) if _wrapper_on else None\n"
                f"{indent}print(f'[Causal] WRAPPER_ENABLED={{_wrapper_on}} (env={{repr(_wrapper_env) if _wrapper_env else \"unset\"}})')\n"
                f"\n"
            )
            lines.insert(i, init_code)
            return lines

    # Fallback: insert after task_desc assignment
    for i, line in enumerate(lines):
        if re.search(r"\btask_desc\s*=", line):
            indent = re.match(r"(\s*)", line).group(1)
            init_code = (
                f"{indent}# ── Causal 프롬프트 래퍼 초기화 ──  {PATCH_MARKER}\n"
                f"{indent}_causal_env = os.environ.get('CAUSAL_MODE', '').strip().lower()\n"
                f"{indent}_causal_on = (_causal_env not in ('false', '0', 'no', 'off')\n"
                f"{indent}    if _causal_env else configs.get('CAUSAL_MODE', True))\n"
                f"{indent}causal_model = CausalWorldModel(task_desc) if _causal_on else None\n"
                f"{indent}# ── Causal 액션 래퍼 초기화 ──  {WRAPPER_MARKER}\n"
                f"{indent}_wrapper_env = os.environ.get('WRAPPER_ENABLED', '').strip().lower()\n"
                f"{indent}_wrapper_on = (_wrapper_env not in ('false', '0', 'no', 'off')\n"
                f"{indent}    if _wrapper_env else configs.get('WRAPPER_ENABLED', True))\n"
                f"{indent}action_wrapper = CausalWrapper(task_desc) if _wrapper_on else None\n"
            )
            lines.insert(i + 1, init_code)
            return lines

    print("  ⚠️  초기화 삽입 위치를 찾지 못했습니다. 수동 삽입 필요.")
    return lines


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 3: wrap_prompt (VLM 호출 직전)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def insert_wrap_prompt(lines: list[str]) -> list[str]:
    """Insert wrap_prompt call before get_model_response."""
    for i, line in enumerate(lines):
        if "get_model_response" in line and "mllm" in line:
            indent = re.match(r"(\s*)", line).group(1)
            wrap_code = (
                f"{indent}if causal_model is not None:  {PATCH_MARKER}\n"
                f"{indent}    prompt = causal_model.wrap_prompt(prompt, last_act)\n"
            )
            lines.insert(i, wrap_code)
            return lines

    print("  ⚠️  get_model_response 위치를 찾지 못했습니다.")
    return lines


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 4: 응답 파싱 직후 — record_action + wrapper evaluate
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def insert_action_wrapper(lines: list[str]) -> list[str]:
    """
    Insert CausalWrapper evaluation between response parsing and action execution.

    AppAgent flow:
        res = parse_explore_rsp(rsp)   # or parse_grid_rsp
        act_name = res[0]
        last_act = res[-1]
        res = res[:-1]
        if act_name == "FINISH": ...
        elif act_name == "tap": ...    ← 여기 전에 삽입

    삽입 위치: `act_name = res[0]` 직후,
              첫 번째 `if act_name == "FINISH"` 직전
    """
    # parse_*_rsp 직후에 record_action 삽입
    for i, line in enumerate(lines):
        if re.search(r"res\s*=\s*parse_(explore|grid|act)_rsp", line):
            indent = re.match(r"(\s*)", line).group(1)
            record_code = (
                f"{indent}if causal_model is not None and res:  {PATCH_MARKER}\n"
                f"{indent}    _action = str(res[0])\n"
                f"{indent}    _summary = str(res[-1]) if res else '(no summary)'\n"
                f"{indent}    causal_model.record_action(round_count, _action, _summary)\n"
            )
            lines.insert(i + 1, record_code)
            break

    # `act_name = res[0]` 이후, `if act_name == "FINISH"` 직전에
    # wrapper.evaluate() 호출 + elem_list 범위 검증 삽입
    for i, line in enumerate(lines):
        # "if act_name == "FINISH"" 또는 유사 패턴
        if re.search(r'if\s+act_name\s*==\s*["\']FINISH["\']', line):
            indent = re.match(r"(\s*)", line).group(1)
            wrapper_code = (
                f"{indent}# ── elem_list 범위 초과 방어 ──  {WRAPPER_MARKER}\n"
                f"{indent}if act_name in ('tap', 'long_press', 'swipe') and len(res) > 1:\n"
                f"{indent}    _area_idx = res[1] if isinstance(res[1], int) else None\n"
                f"{indent}    if _area_idx is not None and _area_idx > len(elem_list):\n"
                f"{indent}        print_with_color(\n"
                f"{indent}            f'[Guard] 요소 번호 {{_area_idx}} > 최대 {{len(elem_list)}}. 스킵.', 'red'\n"
                f"{indent}        )\n"
                f"{indent}        last_act = f'[SKIPPED] 존재하지 않는 UI 요소 {{_area_idx}} 참조'\n"
                f"{indent}        time.sleep(configs.get('REQUEST_INTERVAL', 3))\n"
                f"{indent}        continue\n"
                f"\n"
                f"{indent}# ── CausalWrapper 액션 검증 ──  {WRAPPER_MARKER}\n"
                f"{indent}if action_wrapper is not None and act_name != 'FINISH':\n"
                f"{indent}    _proposed = ProposedAction(\n"
                f"{indent}        act_name=act_name,\n"
                f"{indent}        summary=last_act,\n"
                f"{indent}        raw_response=rsp if 'rsp' in dir() else '',\n"
                f"{indent}    )\n"
                f"{indent}    _decision = action_wrapper.evaluate(_proposed)\n"
                f"{indent}    if not _decision.approved:\n"
                f"{indent}        print_with_color(\n"
                f"{indent}            f'[CausalWrapper] BLOCKED: {{_decision.reason}}', 'red'\n"
                f"{indent}        )\n"
                f"{indent}        action_wrapper.record_step(_proposed)\n"
                f"{indent}        last_act = f'[BLOCKED] {{_decision.reason}}'\n"
                f"{indent}        time.sleep(configs.get('REQUEST_INTERVAL', 3))\n"
                f"{indent}        continue\n"
                f"{indent}    action_wrapper.record_step(_proposed)\n"
                f"\n"
            )
            lines.insert(i, wrapper_code)
            return lines

    print("  ⚠️  FINISH 분기 위치를 찾지 못했습니다.")
    return lines


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 5: 스크린샷/XML 캡처 전 UI 안정화 대기
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

UI_SETTLE_MARKER = "# [UI_SETTLE_PATCH]"


def insert_ui_settle(lines):
    """get_screenshot() 호출 직전에 UI 안정화 대기를 삽입."""
    for i, line in enumerate(lines):
        if "get_screenshot" in line and "controller" in line and UI_SETTLE_MARKER not in line:
            indent = re.match(r"(\s*)", line).group(1)
            settle_code = (
                f"{indent}# ── UI 안정화 대기 ── {UI_SETTLE_MARKER}\n"
                f"{indent}time.sleep(2)  # 화면 전환/애니메이션 완료 대기\n"
            )
            lines.insert(i, settle_code)
            return lines
    return lines


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 6: enter() 액션 추가 — 키보드 Enter/Search 키 지원
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ENTER_MARKER = "# [ENTER_ACTION_PATCH]"


def insert_enter_action(lines):
    """
    3곳 패치:
    1. 프롬프트에 enter() 액션 설명 추가
    2. parse_explore_rsp에 enter 파싱 추가
    3. task_executor.py 메인 루프에 enter 실행 분기 추가
    """

    # ── 1. 프롬프트 패치: text() 설명 뒤에 enter() 추가 ──
    for i, line in enumerate(lines):
        if "text(text_input: str)" in line and ENTER_MARKER not in "".join(lines[max(0,i-2):i+2]):
            # text() 설명 블록 끝을 찾아서 enter() 설명 삽입
            # "showing in the lower half of the screen." 뒤에 삽입
            for j in range(i, min(i + 10, len(lines))):
                if "lower half of the screen" in lines[j]:
                    indent = re.match(r"(\s*)", lines[j]).group(1) or ""
                    enter_doc = (
                        f"\n"
                        f"{indent}6. enter()  {ENTER_MARKER}\n"
                        f"{indent}This function is used to press the Enter/Search/Submit key on the keyboard.\n"
                        f"{indent}Use this INSTEAD OF tapping a keyboard button. Keyboard buttons (like the search\n"
                        f"{indent}icon on the keyboard) are NOT labeled with numeric tags and cannot be tapped.\n"
                        f"{indent}After using text() to type a search query, call enter() to submit it.\n"
                        f"{indent}A simple use case: after text(\"Nike Air Force 1\"), call enter() to search.\n"
                    )
                    lines.insert(j + 1, enter_doc)
                    break
            break

    # ── 2. parse_explore_rsp 패치: enter 파싱 ──
    for i, line in enumerate(lines):
        if 'act_name == "tap"' in line and "enter" not in "".join(lines[max(0,i-3):i]):
            indent = re.match(r"(\s*)", line).group(1)
            enter_parse = (
                f"{indent}if act_name == 'enter':  {ENTER_MARKER}\n"
                f"{indent}    return [act_name, last_act]\n"
            )
            lines.insert(i, enter_parse)
            break

    # ── 3. 액션 실행 패치: enter 분기 ──
    for i, line in enumerate(lines):
        if 'act_name == "swipe"' in line and "controller" in "".join(lines[i:i+5]):
            # swipe 분기 뒤에 enter 분기 추가
            # swipe 블록의 끝(break 포함)을 찾음
            for j in range(i + 1, min(i + 15, len(lines))):
                if "elif" in lines[j] or ("grid" in lines[j] and "act_name" in lines[j]):
                    indent = re.match(r"(\s*)", lines[j]).group(1)
                    enter_exec = (
                        f"{indent}elif act_name == 'enter':  {ENTER_MARKER}\n"
                        f"{indent}    ret = controller.enter()\n"
                        f"{indent}    if ret == 'ERROR':\n"
                        f"{indent}        print_with_color('ERROR: enter execution failed', 'red')\n"
                        f"{indent}        break\n"
                    )
                    lines.insert(j, enter_exec)
                    break
            break

    return lines


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Main patch function
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def patch(filepath: str) -> None:
    path = Path(filepath)
    if not path.exists():
        print(f"❌ 파일을 찾을 수 없습니다: {filepath}")
        sys.exit(1)

    source = path.read_text(encoding="utf-8")

    if already_patched(source):
        print(f"   ✅ 이미 패치됨 (중복 스킵): {filepath}")
        return

    # Backup original
    backup = path.with_suffix(".py.orig")
    if not backup.exists():
        shutil.copy2(path, backup)
        print(f"   → 백업: {backup}")

    lines = source.splitlines(keepends=True)

    print("   → [1/7] import 추가...")
    lines = insert_imports(lines)

    print("   → [2/7] causal_model + action_wrapper 초기화 삽입...")
    lines = insert_init(lines)

    print("   → [3/7] UI 안정화 대기 삽입...")
    lines = insert_ui_settle(lines)

    print("   → [4/7] enter() 액션 추가 (프롬프트 + 파싱 + 실행)...")
    lines = insert_enter_action(lines)

    print("   → [5/7] wrap_prompt 호출 삽입...")
    lines = insert_wrap_prompt(lines)

    print("   → [6/7] record_action 삽입...")
    # insert_action_wrapper handles both record_action and wrapper evaluate
    print("   → [7/7] action_wrapper.evaluate() 삽입...")
    lines = insert_action_wrapper(lines)

    patched_source = "".join(lines)
    path.write_text(patched_source, encoding="utf-8")

    causal_count = patched_source.count(PATCH_MARKER)
    wrapper_count = patched_source.count(WRAPPER_MARKER)
    print(f"   ✅ 패치 완료: {filepath}")
    print(f"      프롬프트 래퍼 패치: {causal_count}개")
    print(f"      액션 래퍼 패치: {wrapper_count}개")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"사용법: python {sys.argv[0]} <task_executor.py 경로>")
        sys.exit(1)
    patch(sys.argv[1])
