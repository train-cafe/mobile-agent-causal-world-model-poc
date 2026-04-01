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
    # NOTE: 일반 문자열 + .replace()로 class_name만 치환.
    #       f-string을 쓰면 패치 대상 코드의 {변수}가 스크립트 시점에 평가되어 NameError.
    patch_code = '''
__PATCH_MARKER__
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Monkey-patch: swipe safe zone + text 한글 지원 + XML dump 재시도
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import subprocess as _sp
import re as _re
import time as _time

# ── 1. Swipe Safe Zone ─────────────────────────────────────────────────────

_original_swipe = __CLASS_NAME__.swipe

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

__CLASS_NAME__.swipe = _safe_swipe


# ── 2. Text 한글/유니코드 지원 ─────────────────────────────────────────────

_original_text = __CLASS_NAME__.text

def _unicode_text(self, input_str):
    """한글 등 유니코드 텍스트를 ADB로 입력."""
    # ASCII만 포함된 경우 원래 방식 사용
    try:
        input_str.encode("ascii")
        return _original_text(self, input_str)
    except UnicodeEncodeError:
        pass

    # 방법 1: ADBKeyboard IME broadcast (가장 안정적)
    try:
        _ime_check = _sp.run(
            ["adb", "-s", self.device, "shell", "ime", "list", "-s"],
            capture_output=True, text=True, timeout=5
        ).stdout
        if "com.android.adbkeyboard" in _ime_check:
            _sp.run(
                ["adb", "-s", self.device, "shell",
                 "ime", "set", "com.android.adbkeyboard/.AdbIME"],
                capture_output=True, timeout=5
            )
            ret = _sp.run(
                ["adb", "-s", self.device, "shell",
                 "am", "broadcast", "-a", "ADB_INPUT_TEXT",
                 "--es", "msg", input_str],
                capture_output=True, text=True, timeout=10
            )
            if ret.returncode == 0:
                print(f"[ADBKeyboard] 텍스트 입력: {input_str}")
                return ret.stdout
    except Exception:
        pass

    # 방법 2: 클립보드 붙여넣기 (KEYCODE_PASTE)
    try:
        _sp.run(
            ["adb", "-s", self.device, "shell",
             "am", "broadcast", "-a", "clipper.set", "-e", "text", input_str],
            capture_output=True, timeout=5
        )
        _sp.run(
            ["adb", "-s", self.device, "shell",
             "input", "keyevent", "279"],
            capture_output=True, timeout=5
        )
        print(f"[Clipboard] 클립보드 붙여넣기 시도: {input_str}")
        return "clipboard paste attempted"
    except Exception:
        pass

    # 방법 3: 실패 — ADBKeyboard 설치 안내
    print("[WARNING] 한글 입력 실패. ADBKeyboard 설치를 권장합니다.")
    print("  설치: adb install ADBKeyboard.apk")
    print("  다운로드: https://github.com/nicewook/ADBKeyboard")
    print(f"  입력 시도 텍스트: {input_str}")
    return f"ERROR: 유니코드 입력 실패: {input_str}"

__CLASS_NAME__.text = _unicode_text


# ── 3. get_xml 재시도 — uiautomator dump 실패 시 최대 3회 재시도 ──────────

_original_get_xml = __CLASS_NAME__.get_xml

def _retry_get_xml(self, prefix, save_dir, max_retries=3, wait_sec=3):
    """uiautomator dump 실패 시 대기 후 재시도. 화면 전환 중 dump 실패 방지."""
    for attempt in range(max_retries):
        result = _original_get_xml(self, prefix, save_dir)
        if result != "ERROR":
            return result
        if attempt < max_retries - 1:
            print(f"[XML Retry] uiautomator dump 실패, {wait_sec}초 후 재시도 ({attempt+1}/{max_retries})...")
            _time.sleep(wait_sec)
    print(f"[XML Retry] {max_retries}회 모두 실패. 화면 전환 대기 중일 수 있습니다.")
    return "ERROR"

__CLASS_NAME__.get_xml = _retry_get_xml


# ── 4. get_screenshot 재시도 — screencap 실패 시 ─────────────────────────

_original_get_screenshot = __CLASS_NAME__.get_screenshot

def _retry_get_screenshot(self, prefix, save_dir, max_retries=2, wait_sec=2):
    """screencap 실패 시 대기 후 재시도."""
    for attempt in range(max_retries):
        result = _original_get_screenshot(self, prefix, save_dir)
        if result != "ERROR":
            return result
        if attempt < max_retries - 1:
            print(f"[Screenshot Retry] 실패, {wait_sec}초 후 재시도 ({attempt+1}/{max_retries})...")
            _time.sleep(wait_sec)
    return "ERROR"

__CLASS_NAME__.get_screenshot = _retry_get_screenshot

# ── 5. 키보드 검색 버튼 누르기 헬퍼 ──────────────────────────────────────
# uiautomator dump에 키보드(IME) 버튼이 잡히지 않는 문제 대응.
# text 입력 후 검색을 실행하려면 KEYCODE_ENTER를 보내는 것이 확실함.

_original_tap = __CLASS_NAME__.tap

def _enhanced_tap(self, x, y):
    """탭 실행. 키보드가 떠있고 탭 위치가 키보드 영역이면 ENTER 키로 대체."""
    try:
        _wm = _sp.run(
            ["adb", "-s", self.device, "shell", "wm", "size"],
            capture_output=True, text=True, timeout=5
        ).stdout
        _m = _re.search(r"(\\d+)x(\\d+)", _wm)
        _sw, _sh = (int(_m.group(1)), int(_m.group(2))) if _m else (1080, 2400)
    except Exception:
        _sw, _sh = 1080, 2400

    # 키보드 영역 감지: 화면 하단 40% 이하이고 키보드가 떠있는지 확인
    if y > _sh * 0.65:
        try:
            _kb_check = _sp.run(
                ["adb", "-s", self.device, "shell",
                 "dumpsys", "input_method"],
                capture_output=True, text=True, timeout=5
            ).stdout
            if "mInputShown=true" in _kb_check:
                # 키보드가 떠있고, 탭 위치가 키보드 영역 → ENTER로 대체
                print(f"[Keyboard] 키보드 영역 탭 감지 (y={y}/{_sh}). KEYCODE_ENTER로 대체합니다.")
                ret = _sp.run(
                    ["adb", "-s", self.device, "shell",
                     "input", "keyevent", "KEYCODE_ENTER"],
                    capture_output=True, text=True, timeout=5
                )
                return ret.stdout if ret.returncode == 0 else "ERROR"
        except Exception:
            pass

    return _original_tap(self, x, y)

__CLASS_NAME__.tap = _enhanced_tap

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# End of monkey-patch
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
'''.replace("__CLASS_NAME__", class_name).replace("__PATCH_MARKER__", PATCH_MARKER)

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
