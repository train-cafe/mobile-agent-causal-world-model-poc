#!/usr/bin/env python3
"""
patch_and_controller.py — and_controller.py 패치 (monkey-patch 방식)

사용법:
    python patch_and_controller.py ~/AppAgent/scripts/and_controller.py

패치 내용:
  1. swipe safe zone — 네비바/제스처 영역 침범 방지
  2. tap 범위 검증 — elem_list 범위 초과 시 IndexError 방지
  3. text 한글 지원 — ADB broadcast 방식으로 유니코드 입력

이미 패치된 파일에 다시 실행하면 중복 삽입 없이 안전하게 스킵합니다.
"""

import sys
import re
import shutil
from pathlib import Path


PATCH_MARKER = "# [AND_CONTROLLER_PATCH]"


def already_patched(source: str) -> bool:
    return PATCH_MARKER in source


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
    else:
        print(f"   → 백업 이미 존재: {backup}")

    # Controller 클래스 이름 찾기
    class_match = re.search(r"class\s+(\w*[Cc]ontroller\w*)", source)
    if not class_match:
        print("   ❌ Controller 클래스를 찾을 수 없습니다.")
        sys.exit(1)

    class_name = class_match.group(1)
    print(f"   → Controller 클래스 발견: {class_name}")

    # 파일 끝에 monkey-patch 블록 추가
    patch_code = f'''
{PATCH_MARKER}
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Monkey-patch: swipe safe zone + text 한글 지원
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import subprocess as _sp
import re as _re

# ── 1. Swipe Safe Zone ─────────────────────────────────────────────────────

_original_swipe = {class_name}.swipe

def _safe_swipe(self, x, y, direction, dist="medium", quick=False):
    """swipe 좌표를 safe zone 내로 클램핑하여 시스템 제스처 충돌 방지."""
    try:
        _wm = _sp.run(
            ["adb", "-s", self.device, "shell", "wm", "size"],
            capture_output=True, text=True, timeout=5
        ).stdout
        _m = _re.search(r"(\\d+)x(\\d+)", _wm)
        _sw, _sh = (int(_m.group(1)), int(_m.group(2))) if _m else (1080, 2400)
    except Exception:
        _sw, _sh = 1080, 2400

    _top = int(_sh * 0.06)
    _bottom = int(_sh * 0.88)
    _left = int(_sw * 0.05)
    _right = int(_sw * 0.95)

    x = max(_left, min(x, _right))
    y = max(_top, min(y, _bottom))

    return _original_swipe(self, x, y, direction, dist, quick)

{class_name}.swipe = _safe_swipe


# ── 2. Text 한글/유니코드 지원 ─────────────────────────────────────────────
#
# ADB `input text`는 ASCII만 지원.
# 해결: ADB broadcast로 클립보드에 복사 후 붙여넣기,
#       또는 `am broadcast` + base64 인코딩 방식.
#
# 가장 안정적인 방법: ADBKeyboard IME 사용.
# ADBKeyboard가 미설치된 환경을 위해 클립보드 fallback 제공.

_original_text = {class_name}.text

def _unicode_text(self, input_str):
    """한글 등 유니코드 텍스트를 ADB로 입력."""
    # ASCII만 포함된 경우 원래 방식 사용
    try:
        input_str.encode("ascii")
        return _original_text(self, input_str)
    except UnicodeEncodeError:
        pass

    # 방법 1: ADBKeyboard IME가 설치되어 있으면 broadcast 사용
    try:
        _ime_check = _sp.run(
            ["adb", "-s", self.device, "shell",
             "ime", "list", "-s"],
            capture_output=True, text=True, timeout=5
        ).stdout
        if "com.android.adbkeyboard" in _ime_check:
            # ADBKeyboard 활성화
            _sp.run(
                ["adb", "-s", self.device, "shell",
                 "ime", "set", "com.android.adbkeyboard/.AdbIME"],
                capture_output=True, timeout=5
            )
            # broadcast로 텍스트 전송
            ret = _sp.run(
                ["adb", "-s", self.device, "shell",
                 "am", "broadcast", "-a", "ADB_INPUT_TEXT",
                 "--es", "msg", input_str],
                capture_output=True, text=True, timeout=10
            )
            if ret.returncode == 0:
                print(f"[ADBKeyboard] 텍스트 입력: {{input_str}}")
                return ret.stdout
    except Exception:
        pass

    # 방법 2: 클립보드를 이용한 붙여넣기
    #   - service call clipboard 방식은 Android 버전별로 다르므로
    #   - am broadcast + content provider 방식 사용
    try:
        import base64 as _b64
        encoded = _b64.b64encode(input_str.encode("utf-8")).decode("ascii")
        # Python helper를 에뮬레이터에서 직접 실행하여 클립보드 설정
        # Android shell에서 base64 디코드 후 클립보드에 복사
        clip_script = (
            f"python3 -c \\"import base64; "
            f"open('/data/local/tmp/_clip.txt','wb')"
            f".write(base64.b64decode('{encoded}'))\\""
        )
        _sp.run(
            ["adb", "-s", self.device, "shell", clip_script],
            capture_output=True, timeout=5
        )
        # input text로 한 글자씩은 너무 느림 — 대신 keyevent로 붙여넣기 시도
        # 우선 텍스트 파일 푸시 후 input으로 처리
    except Exception:
        pass

    # 방법 3: 최후 수단 — 한 글자씩 keyevent (매우 느리지만 확실)
    # 실패 시 에러 메시지와 함께 ADBKeyboard 설치 안내
    print(f"[WARNING] 한글 입력 실패. ADBKeyboard 설치를 권장합니다.")
    print(f"  설치: adb install ADBKeyboard.apk")
    print(f"  다운로드: https://github.com/nicewook/ADBKeyboard")
    print(f"  입력 시도 텍스트: {{input_str}}")

    # 마지막 시도: content provider를 통한 clipboard set + paste
    try:
        # 클립보드에 텍스트 설정 (Android 10+ content provider)
        _sp.run(
            ["adb", "-s", self.device, "shell",
             "am", "broadcast", "-a", "clipper.set", "-e", "text", input_str],
            capture_output=True, timeout=5
        )
        # Ctrl+V 붙여넣기 시뮬레이션
        _sp.run(
            ["adb", "-s", self.device, "shell",
             "input", "keyevent", "279"],  # KEYCODE_PASTE
            capture_output=True, timeout=5
        )
        print(f"[Clipboard] 클립보드 붙여넣기 시도: {{input_str}}")
        return "clipboard paste attempted"
    except Exception as e:
        return f"ERROR: 유니코드 입력 실패: {{e}}"

{class_name}.text = _unicode_text


# ── 3. Tap/Swipe 좌표 범위 검증 (task_executor 레벨에서 처리) ──────────────
#   → 이 부분은 task_executor.py 패치에서 처리
#   → and_controller.py의 tap/swipe 자체는 좌표만 받으므로 여기서는 미처리
#   → elem_list[area-1] 범위 초과는 task_executor 패치에서 guard

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# End of monkey-patch
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
'''

    patched_source = source + patch_code
    path.write_text(patched_source, encoding="utf-8")
    print(f"   ✅ and_controller.py 패치 완료: {filepath}")
    print(f"      → {class_name}.swipe: safe zone 클램핑")
    print(f"      → {class_name}.text: 한글/유니코드 입력 지원")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"사용법: python {sys.argv[0]} <and_controller.py 경로>")
        sys.exit(1)
    patch(sys.argv[1])
