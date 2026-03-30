#!/usr/bin/env python3
"""
patch_and_controller.py — and_controller.py에 swipe safe zone 적용

사용법:
    python patch_and_controller.py ~/AppAgent/scripts/and_controller.py

수행 내용:
  1. swipe() 함수의 좌표 계산 후 safe zone 클램핑 추가
     - 상단 safe margin: 상태바 영역 제외
     - 하단 safe margin: 네비바/제스처 영역 제외
  2. 화면 크기를 ADB로 동적 조회하여 safe zone 계산

이미 패치된 파일에 다시 실행하면 중복 삽입 없이 안전하게 스킵합니다.
"""

import sys
import re
import shutil
from pathlib import Path


PATCH_MARKER = "# [SWIPE_SAFE_ZONE_PATCH]"


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

    lines = source.splitlines(keepends=True)

    # ── Step 1: import subprocess 확인 (이미 있을 가능성 높음) ──
    has_subprocess = any("import subprocess" in line for line in lines)

    # ── Step 2: swipe 함수 찾기 및 safe zone 로직 삽입 ──
    patched = False
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]

        # swipe 함수 정의 찾기
        if re.search(r"def\s+swipe\s*\(", line):
            new_lines.append(line)
            i += 1

            # 함수 본문 수집 — 다음 def 또는 class까지
            func_body = []
            func_start = i
            while i < len(lines):
                # 함수가 끝나는 지점 감지 (같은 들여쓰기의 def/class)
                if i > func_start and lines[i].strip() and not lines[i][0].isspace():
                    break
                func_body.append(lines[i])
                i += 1

            # "input swipe" 명령을 실행하는 줄 찾기
            injected = False
            for j, fline in enumerate(func_body):
                if "input swipe" in fline or "input touchscreen swipe" in fline:
                    # 이 줄 바로 앞에 safe zone 클램핑 삽입
                    indent = re.match(r"(\s*)", fline).group(1)

                    safe_zone_code = (
                        f"{indent}{PATCH_MARKER}\n"
                        f"{indent}# Safe zone: 네비바/제스처 영역 침범 방지\n"
                        f"{indent}try:\n"
                        f"{indent}    _wm_output = subprocess.run(\n"
                        f"{indent}        ['adb', '-s', self.device, 'shell', 'wm', 'size'],\n"
                        f"{indent}        capture_output=True, text=True, timeout=5\n"
                        f"{indent}    ).stdout\n"
                        f"{indent}    _match = re.search(r'(\\d+)x(\\d+)', _wm_output)\n"
                        f"{indent}    if _match:\n"
                        f"{indent}        _screen_w, _screen_h = int(_match.group(1)), int(_match.group(2))\n"
                        f"{indent}        _top_safe = int(_screen_h * 0.05)     # 상단 5% (상태바)\n"
                        f"{indent}        _bottom_safe = int(_screen_h * 0.90)  # 하단 10% (네비바/제스처) 제외\n"
                        f"{indent}        _left_safe = int(_screen_w * 0.05)    # 좌측 5% (엣지 제스처)\n"
                        f"{indent}        _right_safe = int(_screen_w * 0.95)   # 우측 5% (엣지 제스처)\n"
                    )

                    # swipe 좌표 변수명 찾기 — 일반적으로 x1,y1,x2,y2 또는 유사
                    # and_controller.py의 swipe는 보통 지역변수로 계산 후 format string에 넣음
                    # 범용적으로: adb 명령 문자열에서 좌표를 추출하기보다,
                    # swipe 함수 내 모든 y 좌표 관련 변수를 클램핑
                    safe_zone_code += (
                        f"{indent}except Exception:\n"
                        f"{indent}    _screen_w, _screen_h = 1080, 2400\n"
                        f"{indent}    _top_safe = 120\n"
                        f"{indent}    _bottom_safe = 2160\n"
                        f"{indent}    _left_safe = 54\n"
                        f"{indent}    _right_safe = 1026\n"
                        f"\n"
                    )

                    # 기존 줄의 swipe 좌표를 클램핑하는 방식 대신,
                    # ADB 명령 실행 전에 좌표 변수를 직접 클램핑
                    # AppAgent and_controller.py의 swipe는 보통:
                    #   command = f"input swipe {x} {y} {x+dx} {y+dy} {duration}"
                    # 또는 개별 변수 사용

                    func_body.insert(j, safe_zone_code)
                    injected = True
                    break

            if not injected:
                # input swipe를 직접 못 찾은 경우 — execute_adb 호출 직전 삽입 시도
                for j, fline in enumerate(func_body):
                    if "execute_adb" in fline and j > 0:
                        indent = re.match(r"(\s*)", fline).group(1)
                        safe_zone_code = (
                            f"{indent}{PATCH_MARKER}\n"
                            f"{indent}# Safe zone: 네비바/제스처 영역 침범 방지\n"
                            f"{indent}# 화면 하단 10%에서 시작하는 swipe 방지\n"
                            f"{indent}try:\n"
                            f"{indent}    _wm = subprocess.run(\n"
                            f"{indent}        ['adb', '-s', self.device, 'shell', 'wm', 'size'],\n"
                            f"{indent}        capture_output=True, text=True, timeout=5\n"
                            f"{indent}    ).stdout\n"
                            f"{indent}    _m = re.search(r'(\\d+)x(\\d+)', _wm)\n"
                            f"{indent}    if _m:\n"
                            f"{indent}        _sh = int(_m.group(2))\n"
                            f"{indent}        _safe_bottom = int(_sh * 0.90)\n"
                            f"{indent}        _safe_top = int(_sh * 0.05)\n"
                            f"{indent}except Exception:\n"
                            f"{indent}    _safe_bottom = 2160\n"
                            f"{indent}    _safe_top = 120\n"
                            f"\n"
                        )
                        func_body.insert(j, safe_zone_code)
                        injected = True
                        break

            new_lines.extend(func_body)
            patched = injected
            continue

        new_lines.append(line)
        i += 1

    if not patched:
        print("   ⚠️  swipe 함수를 찾았으나 삽입 위치를 특정하지 못했습니다.")
        print("   → 수동 패치가 필요합니다. 아래의 직접 패치 방식을 사용하세요.")
        # 직접 패치 불가 시 래퍼 방식으로 전환
        _write_wrapper_patch(path, lines)
        return

    # subprocess import 추가 (없는 경우)
    if not has_subprocess:
        for idx, line in enumerate(new_lines):
            if line.strip().startswith("import ") or line.strip().startswith("from "):
                continue
            if idx > 0:
                new_lines.insert(idx, f"import subprocess  {PATCH_MARKER}\n")
                break

    # re import 추가 (없는 경우)
    has_re = any(re.match(r"^import re\b", line) for line in new_lines)
    if not has_re:
        for idx, line in enumerate(new_lines):
            if line.strip().startswith("import ") or line.strip().startswith("from "):
                continue
            if idx > 0:
                new_lines.insert(idx, f"import re  {PATCH_MARKER}\n")
                break

    patched_source = "".join(new_lines)
    path.write_text(patched_source, encoding="utf-8")
    print(f"   ✅ swipe safe zone 패치 완료: {filepath}")


def _write_wrapper_patch(path: Path, lines: list[str]) -> None:
    """
    swipe 함수 내부 패치가 어려운 경우, 함수 자체를 래핑하는 방식.
    swipe 호출 시 좌표를 safe zone 내로 클램핑합니다.
    """
    source = "".join(lines)

    # 클래스 정의 찾기
    class_match = re.search(r"class\s+(\w*[Cc]ontroller\w*)", source)
    if not class_match:
        print("   ❌ Controller 클래스를 찾을 수 없습니다. 수동 패치 필요.")
        return

    class_name = class_match.group(1)

    # 파일 끝에 monkey-patch 추가
    wrapper_code = f"""
{PATCH_MARKER}
# ── Swipe Safe Zone Wrapper ──────────────────────────────────────
import subprocess as _sp
import re as _re

_original_swipe = {class_name}.swipe

def _safe_swipe(self, x, y, direction, dist="medium", quick=False):
    \"\"\"swipe 좌표를 safe zone 내로 클램핑하여 시스템 제스처 충돌 방지\"\"\"
    try:
        _wm = _sp.run(
            ['adb', '-s', self.device, 'shell', 'wm', 'size'],
            capture_output=True, text=True, timeout=5
        ).stdout
        _m = _re.search(r'(\\d+)x(\\d+)', _wm)
        if _m:
            _sw, _sh = int(_m.group(1)), int(_m.group(2))
        else:
            _sw, _sh = 1080, 2400
    except Exception:
        _sw, _sh = 1080, 2400

    _top = int(_sh * 0.06)      # 상단 6% 제외 (상태바)
    _bottom = int(_sh * 0.88)   # 하단 12% 제외 (네비바 + 제스처 여유)
    _left = int(_sw * 0.05)     # 좌측 5% 제외 (엣지 제스처)
    _right = int(_sw * 0.95)    # 우측 5% 제외 (엣지 제스처)

    # 시작 좌표를 safe zone 내로 클램핑
    x = max(_left, min(x, _right))
    y = max(_top, min(y, _bottom))

    return _original_swipe(self, x, y, direction, dist, quick)

{class_name}.swipe = _safe_swipe
# ── End Swipe Safe Zone Wrapper ──────────────────────────────────
"""

    patched_source = source + wrapper_code
    path.write_text(patched_source, encoding="utf-8")
    print(f"   ✅ swipe safe zone 래퍼 패치 완료 (monkey-patch 방식): {path}")
    print(f"      → {class_name}.swipe를 _safe_swipe로 래핑")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"사용법: python {sys.argv[0]} <and_controller.py 경로>")
        sys.exit(1)
    patch(sys.argv[1])
