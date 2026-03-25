#!/usr/bin/env python3
"""
patch_task_executor.py — 최소 침습적으로 task_executor.py 에 Causal World Model 삽입

사용법:
    python patch_task_executor.py ~/AppAgent/scripts/task_executor.py

수행 내용:
  1. 파일 상단에 `from causal_wrapper import CausalWorldModel` import 추가
  2. task_desc 초기화 직후 causal_model 초기화 코드 삽입
  3. VLM 호출 직전 (get_model_response 앞) 에 wrap_prompt 호출 삽입
  4. 응답 파싱 직후 record_action 호출 삽입

이미 패치된 파일에 다시 실행하면 중복 삽입 없이 안전하게 스킵합니다.
"""

import sys
import re
import shutil
from pathlib import Path


PATCH_MARKER = "# [CAUSAL_PATCH]"


def already_patched(source: str) -> bool:
    return PATCH_MARKER in source


def insert_import(lines: list[str]) -> list[str]:
    """Add causal_wrapper import after the last existing import block."""
    import_block_end = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            import_block_end = i

    import_line = f"from causal_wrapper import CausalWorldModel  {PATCH_MARKER}\n"
    lines.insert(import_block_end + 1, import_line)
    return lines


def insert_init(lines: list[str]) -> list[str]:
    """
    Insert causal_model init after the line where task_desc is assigned.

    AppAgent run.py passes task via stdin prompt; task_executor typically
    stores it as `task_desc` or receives it as a parameter.
    We look for the line that assigns `task_desc` and insert init right after.
    """
    # Pattern: assignment of task_desc (e.g., "task_desc = ..." or parameter usage)
    # Also covers "self.task_desc = " inside a class
    init_code = (
        f"    causal_model = (  {PATCH_MARKER}\n"
        f"        CausalWorldModel(task_desc)\n"
        f"        if configs.get('CAUSAL_MODE', True) else None\n"
        f"    )\n"
    )

    # Look for the round loop start: `for round_count in range(...)`
    # Insert causal_model init just before the loop
    for i, line in enumerate(lines):
        if re.search(r"for\s+round_count\s+in\s+range", line):
            lines.insert(i, init_code)
            return lines

    # Fallback: insert after task_desc assignment
    for i, line in enumerate(lines):
        if re.search(r"\btask_desc\s*=", line):
            lines.insert(i + 1, init_code)
            return lines

    print("  ⚠️  causal_model init 삽입 위치를 찾지 못했습니다. 수동 삽입이 필요합니다.")
    return lines


def insert_wrap_prompt(lines: list[str]) -> list[str]:
    """
    Insert causal_model.wrap_prompt() call right before mllm.get_model_response().

    AppAgent calls: status, rsp = mllm.get_model_response(prompt, [image])
    We insert wrap_prompt AFTER the last `re.sub(r"<last_act>"` substitution
    and BEFORE the get_model_response call.
    """
    wrap_code = (
        f"        if causal_model is not None:  {PATCH_MARKER}\n"
        f"            prompt = causal_model.wrap_prompt(prompt, last_act)\n"
    )

    for i, line in enumerate(lines):
        if "get_model_response" in line and "mllm" in line:
            lines.insert(i, wrap_code)
            return lines

    print("  ⚠️  get_model_response 호출 위치를 찾지 못했습니다. 수동 삽입이 필요합니다.")
    return lines


def insert_record_action(lines: list[str]) -> list[str]:
    """
    Insert causal_model.record_action() after the response is parsed.

    AppAgent parses the response with parse_explore_rsp() or parse_act_rsp().
    res[4] is typically the action type; res[-1] is the summary.
    """
    record_code = (
        f"        if causal_model is not None and res:  {PATCH_MARKER}\n"
        f"            # res[4]=action_type, res[-1]=summary (AppAgent response format)\n"
        f"            _action = str(res[4]) if len(res) > 4 else str(res[0])\n"
        f"            _summary = str(res[-1]) if res else '(no summary)'\n"
        f"            causal_model.record_action(round_count, _action, _summary)\n"
    )

    # Look for parse_explore_rsp or parse_act_rsp assignment
    for i, line in enumerate(lines):
        if re.search(r"res\s*=\s*parse_(explore|act)_rsp", line):
            lines.insert(i + 1, record_code)
            return lines

    # Fallback: look for any res = parse_*_rsp
    for i, line in enumerate(lines):
        if re.search(r"res\s*=\s*parse_\w+_rsp", line):
            lines.insert(i + 1, record_code)
            return lines

    print("  ⚠️  parse_*_rsp 호출 위치를 찾지 못했습니다. 수동 삽입이 필요합니다.")
    return lines


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
    shutil.copy2(path, backup)
    print(f"   → 백업: {backup}")

    lines = source.splitlines(keepends=True)

    print("   → [1/4] import 추가...")
    lines = insert_import(lines)

    print("   → [2/4] causal_model 초기화 삽입...")
    lines = insert_init(lines)

    print("   → [3/4] wrap_prompt 호출 삽입...")
    lines = insert_wrap_prompt(lines)

    print("   → [4/4] record_action 호출 삽입...")
    lines = insert_record_action(lines)

    patched_source = "".join(lines)
    path.write_text(patched_source, encoding="utf-8")
    print(f"   ✅ 패치 완료: {filepath}")
    print(f"      패치 라인 수: {patched_source.count(PATCH_MARKER)}/4")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"사용법: python {sys.argv[0]} <task_executor.py 경로>")
        sys.exit(1)
    patch(sys.argv[1])
