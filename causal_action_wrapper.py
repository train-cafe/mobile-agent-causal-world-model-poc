"""
causal_action_wrapper.py — Action-level Causal Wrapper for AppAgent

기존 causal_wrapper.py (프롬프트 래퍼)와 별도로,
VLM이 제안한 액션을 에뮬레이터에 실행하기 **직전**에 3단계 검증을 수행한다.

삽입 위치:
  res = parse_explore_rsp(rsp)   ← VLM 응답 파싱
  act_name = res[0]
  ...
  ─── CausalWrapper.evaluate() 삽입 ───
  controller.tap(x, y)           ← ADB 실행

3가지 검증:
  A. Precondition Check (사전조건)
  B. State Transition Consistency (상태 전이 일관성)
  C. Irreversible Action Guard (비가역 방어)

rule-based 구현이며, VLM critic으로 교체할 수 있도록
각 검증을 별도 메서드로 분리한다.
"""

import os
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Any
from abc import ABC, abstractmethod
import json
import re

logger = logging.getLogger("CausalWrapper")
logger.setLevel(logging.DEBUG)

# 콘솔 핸들러 (중복 방지)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter(
        "[CausalWrapper] %(levelname)s %(message)s"
    ))
    logger.addHandler(_handler)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Data Classes
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class ProposedAction:
    """VLM이 제안한 액션."""
    act_name: str                   # "tap", "swipe", "text", "long_press", "FINISH"
    params: tuple = ()              # res[:-1] 에서 act_name 제외한 나머지
    summary: str = ""               # VLM이 출력한 요약 (last_act에 해당)
    raw_response: str = ""          # VLM 원본 응답

    # 실행 좌표 (tap/swipe/long_press인 경우)
    x: Optional[int] = None
    y: Optional[int] = None
    # swipe/drag 등 2점 좌표
    x1: Optional[int] = None
    y1: Optional[int] = None
    x2: Optional[int] = None
    y2: Optional[int] = None
    # swipe 전용
    direction: Optional[str] = None
    dist: str = "medium"
    # text 전용
    input_text: Optional[str] = None


@dataclass
class WrapperDecision:
    """Wrapper 검증 결과."""
    approved: bool
    modified_action: Optional[ProposedAction] = None
    reason: str = ""
    check_results: dict = field(default_factory=lambda: {
        "precondition": "skip",
        "state_transition": "skip",
        "irreversible_guard": "skip",
    })


@dataclass
class StepRecord:
    """한 라운드의 기록."""
    round_n: int
    action: ProposedAction
    screenshot_path: str = ""
    decision: Optional[WrapperDecision] = None
    timestamp: float = 0.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Abstract Base — VLM critic으로 교체 시 이 인터페이스를 구현
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class BasePreconditionChecker(ABC):
    @abstractmethod
    def check(self, action: ProposedAction, context: dict) -> tuple[str, str]:
        """Returns (result, reason). result: 'pass'|'fail'|'skip'"""


class BaseStateTransitionChecker(ABC):
    @abstractmethod
    def check(self, action: ProposedAction, context: dict) -> tuple[str, str]:
        """Returns (result, reason). result: 'pass'|'fail'|'skip'"""


class BaseIrreversibleGuard(ABC):
    @abstractmethod
    def check(self, action: ProposedAction, context: dict) -> tuple[str, str]:
        """Returns (result, reason). result: 'pass'|'fail'|'skip'"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Rule-Based 구현
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class RulePreconditionChecker(BasePreconditionChecker):
    """
    기능 A: Precondition Check (사전조건 검증)

    proposed action 실행 전, 현재 상태가 전제조건을 만족하는지 검사.
    """

    # "담기/장바구니" 류 액션에서 필수로 거쳐야 하는 상태 키워드
    CART_ACTION_KEYWORDS = [
        "담기", "장바구니", "추가", "add to cart", "add item",
    ]
    # 상품 상세 페이지를 암시하는 키워드 (summary/last_act에 나타남)
    PRODUCT_PAGE_INDICATORS = [
        "상품", "메뉴", "옵션", "사이즈", "수량", "가격",
        "product", "detail", "option", "size", "quantity", "price",
    ]
    # 로딩 상태 키워드
    LOADING_INDICATORS = [
        "로딩", "loading", "please wait", "잠시만", "처리 중",
    ]
    # 필수 옵션 미선택 키워드
    OPTION_REQUIRED_KEYWORDS = [
        "옵션을 선택", "필수 선택", "옵션 선택", "required",
        "select option", "choose", "사이즈를 선택", "색상을 선택",
    ]

    def check(self, action: ProposedAction, context: dict) -> tuple[str, str]:
        summary_lower = action.summary.lower()
        # Action/Summary 부분만 검사 (Observation 제외 — 화면 설명의 키워드 오탐 방지)
        raw = action.raw_response
        action_text = ""
        import re as _re
        am = _re.search(r"Action:\s*(.*?)(?=\n\s*Summary:|\Z)", raw, _re.DOTALL)
        sm = _re.search(r"Summary:\s*(.*?)$", raw, _re.MULTILINE)
        if am:
            action_text += am.group(1).strip().lower()
        if sm:
            action_text += " " + sm.group(1).strip().lower()
        combined = summary_lower + " " + action_text

        # 히스토리에서 최근 상태 참조
        history = context.get("step_history", [])
        last_summary = ""
        if history:
            last_record = history[-1]
            if last_record.action:
                last_summary = last_record.action.summary.lower()

        # Rule 1: 로딩 상태 감지 → 대기 권고
        for kw in self.LOADING_INDICATORS:
            if kw in combined or kw in last_summary:
                return ("fail",
                        f"로딩 상태 감지 ('{kw}'). 화면 전환 완료 후 재시도 필요.")

        # Rule 2: "담기" 류 액션인데 상품 상세 페이지 경유 안 한 경우
        is_cart_action = any(kw in combined for kw in self.CART_ACTION_KEYWORDS)
        if is_cart_action:
            # 히스토리에서 상품 상세 페이지 경유 여부 확인
            visited_product = False
            for record in history:
                if record.action:
                    rec_summary = record.action.summary.lower()
                    if any(ind in rec_summary for ind in self.PRODUCT_PAGE_INDICATORS):
                        visited_product = True
                        break
            if not visited_product and len(history) > 0:
                return ("fail",
                        "장바구니 담기 시도이나 상품 상세 페이지 경유 이력 없음. "
                        "상품 선택 → 옵션 확인 → 담기 순서를 확인하세요.")

        # Rule 3: 필수 옵션 미선택 상태에서 담기/확인 시도
        if is_cart_action or action.act_name == "tap":
            for kw in self.OPTION_REQUIRED_KEYWORDS:
                if kw in last_summary or kw in combined:
                    return ("fail",
                            f"필수 옵션 미선택 ('{kw}'). "
                            f"옵션을 먼저 선택한 후 진행하세요.")

        return ("pass", "")


class RuleStateTransitionChecker(BaseStateTransitionChecker):
    """
    기능 B: State Transition Consistency Check (상태 전이 일관성)

    직전 액션 실행 후, 기대했던 상태 전이가 실제로 일어났는지 검사.
    """

    # 동일 화면 반복 감지 임계값
    MAX_SAME_SCREEN_REPEATS = 3

    def check(self, action: ProposedAction, context: dict) -> tuple[str, str]:
        history = context.get("step_history", [])
        if len(history) < 1:
            return ("skip", "이전 기록 없음 (첫 라운드)")

        prev = history[-1]
        prev_action = prev.action

        # Rule 1: 직전 액션이 tap/swipe인데 summary가 이전과 거의 동일
        #         → 클릭이 안 먹힌 것으로 판단
        if prev_action and prev_action.act_name in ("tap", "swipe", "long_press"):
            if (prev_action.summary and action.summary and
                    self._summary_similar(prev_action.summary, action.summary)):
                # 동일 화면 반복 횟수 카운트
                repeat_count = self._count_consecutive_similar(history)
                if repeat_count >= self.MAX_SAME_SCREEN_REPEATS:
                    return ("fail",
                            f"동일 화면 {repeat_count}회 연속 반복. "
                            f"이전 {prev_action.act_name} 액션이 효과 없음. "
                            f"다른 접근법 필요.")
                elif repeat_count >= 2:
                    return ("fail",
                            f"이전 {prev_action.act_name} 액션 후 화면 변화 없음 "
                            f"({repeat_count}회 반복). 액션 미작동 가능성.")

        # Rule 2: 직전 액션이 "뒤로가기" 의미인데 화면 안 바뀜
        if prev_action and prev_action.act_name == "tap":
            back_keywords = ["뒤로", "back", "이전", "돌아가기"]
            if any(kw in (prev_action.summary or "").lower() for kw in back_keywords):
                if self._summary_similar(
                    prev_action.summary or "", action.summary or ""
                ):
                    return ("fail",
                            "뒤로가기 후 화면 변화 없음. 네비게이션 실패.")

        return ("pass", "")

    @staticmethod
    def _summary_similar(a: str, b: str) -> bool:
        """두 summary가 유사한지 간단 비교 (단어 집합 Jaccard)."""
        if not a or not b:
            return False
        words_a = set(a.lower().split())
        words_b = set(b.lower().split())
        if not words_a or not words_b:
            return False
        intersection = words_a & words_b
        union = words_a | words_b
        jaccard = len(intersection) / len(union) if union else 0
        return jaccard > 0.7

    @staticmethod
    def _count_consecutive_similar(history: list[StepRecord]) -> int:
        """히스토리 끝에서 연속으로 유사한 summary 수."""
        if len(history) < 2:
            return 1
        count = 1
        latest = history[-1].action.summary or ""
        for i in range(len(history) - 2, -1, -1):
            prev_summary = history[i].action.summary or ""
            if RuleStateTransitionChecker._summary_similar(latest, prev_summary):
                count += 1
            else:
                break
        return count


class RuleIrreversibleGuard(BaseIrreversibleGuard):
    """
    기능 C: Irreversible Action Guard (비가역 방어)

    결제/삭제/비우기 등 위험 액션 감지 시 task_goal과 비교하여 차단.
    """

    # 비가역 액션 키워드
    DANGER_KEYWORDS = [
        # 결제/구매
        "결제", "결제하기", "바로구매", "즉시구매", "구매하기",
        "주문하기", "주문완료", "결제완료", "구매완료",
        "pay", "checkout", "place order", "buy now", "confirm purchase",
        "submit order",
        # 삭제
        "삭제", "삭제하기", "전체삭제", "비우기", "장바구니 비우기",
        "delete", "remove all", "clear cart", "empty",
        # 주문 취소
        "주문취소", "취소하기", "cancel order",
    ]

    # task_goal에 이 키워드가 있으면 해당 위험 액션을 허용
    GOAL_PERMITS = {
        "결제": ["결제", "구매", "주문", "pay", "checkout", "purchase", "order"],
        "삭제": ["삭제", "delete", "remove", "비우기", "clear"],
        "취소": ["취소", "cancel"],
    }

    def check(self, action: ProposedAction, context: dict) -> tuple[str, str]:
        task_goal = context.get("task_goal", "").lower()
        summary_lower = action.summary.lower()

        # raw_response에서 Action/Summary 부분만 추출 (Observation은 화면 설명이므로 제외)
        # VLM이 화면에 "Buy now" 버튼이 보인다고 설명하는 것과,
        # 실제로 "Buy now"를 클릭하는 것은 다름.
        raw = action.raw_response
        action_text = ""
        import re as _re
        action_match = _re.search(r"Action:\s*(.*?)(?=\n\s*Summary:|\Z)", raw, _re.DOTALL)
        summary_match = _re.search(r"Summary:\s*(.*?)$", raw, _re.MULTILINE)
        if action_match:
            action_text += action_match.group(1).strip().lower()
        if summary_match:
            action_text += " " + summary_match.group(1).strip().lower()

        # 검사 대상: summary + Action/Summary 텍스트만 (Observation 제외)
        combined = summary_lower + " " + action_text

        for kw in self.DANGER_KEYWORDS:
            if kw.lower() not in combined:
                continue

            # 이 위험 키워드가 task_goal에 의해 허용되는지 확인
            permitted = False
            for category, permit_keywords in self.GOAL_PERMITS.items():
                if any(pk in kw.lower() for pk in permit_keywords):
                    # 이 카테고리의 위험 액션이 허용되려면 goal에 관련 키워드 필요
                    if any(pk in task_goal for pk in permit_keywords):
                        permitted = True
                        break

            if not permitted:
                return ("fail",
                        f"비가역 액션 감지: '{kw}'. "
                        f"태스크 목표('{context.get('task_goal', '')}')와 "
                        f"일치하지 않아 차단합니다.")

        return ("pass", "")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# VLM Critic 구현
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class VLMCritic(
    BasePreconditionChecker,
    BaseStateTransitionChecker,
    BaseIrreversibleGuard,
):
    """
    VLM 기반 안전 크리틱.

    - 단일 VLM 호출로 3가지 검증을 수행한다.
    - CausalWrapper가 `check_all()`을 이용해 1회 호출하도록 설계되어 있다.
    - 실패 시 rule-based 체크로 폴백(추가 VLM 호출 없음).
    """

    HISTORY_MAX = 5

    def __init__(self, mllm, max_history: Optional[int] = None):
        self.mllm = mllm
        if max_history is not None:
            self.HISTORY_MAX = max_history

        # 파싱/응답 실패 시 파편화된 rule-based 폴백
        self._fallback_precondition = RulePreconditionChecker()
        self._fallback_state_transition = RuleStateTransitionChecker()
        self._fallback_irreversible = RuleIrreversibleGuard()

    # 개별 check()는 직접 호출되지 않는 것을 전제한다.
    # (CausalWrapper가 check_all() 경로를 사용)
    def check(self, action: ProposedAction, context: dict) -> tuple[str, str]:
        return ("skip", "")

    def check_all(self, action: ProposedAction, context: dict) -> dict:
        screenshot_path = context.get("screenshot_path", "")
        if not screenshot_path:
            return {
                "precondition": self._fallback_precondition.check(action, context),
                "state_transition": self._fallback_state_transition.check(
                    action, context
                ),
                "irreversible_guard": self._fallback_irreversible.check(
                    action, context
                ),
            }

        prompt = self._build_prompt(action, context)
        status, rsp = self.mllm.get_model_response(prompt, [screenshot_path])
        if not status or not rsp:
            return self._fallback_all(action, context)

        parsed = self._parse_vlm_json(rsp)
        if not parsed:
            return self._fallback_all(action, context)

        # VLM 응답 스키마(권장):
        # {
        #   "precondition": {"approved": true/false, "reason": "..."},
        #   "repetition": {"approved": true/false, "reason": "..."},
        #   "safety": {"approved": true/false, "reason": "..."},
        #   "approved": true/false,
        #   "reason": "..."
        # }
        overall_approved = parsed.get("approved", None)
        overall_reason = (parsed.get("reason", "") or "").strip()

        pre_obj = parsed.get("precondition")
        rep_obj = parsed.get("repetition") or parsed.get("state_transition")
        safety_obj = parsed.get("safety") or parsed.get("irreversible_guard")

        # NOTE:
        # - precondition/safety: approved=True => pass, approved=False => fail
        # - repetition: 질문이 "Is this repeated without progress?"라서
        #   approved=True(= 반복임) => fail, approved=False(= 반복 아님) => pass 로 해석해야 함.
        pre_res = self._to_result_tuple(
            pre_obj,
            overall_approved=overall_approved,
            overall_reason=overall_reason,
            invert=False,
            default_on_missing="fail",
        )
        rep_res = self._to_result_tuple(
            rep_obj,
            # repetition은 overall_approved(전체 승인 여부)로 추론하면 오동작하기 쉬움.
            # repetition obj가 없으면 기본 pass로 두고, obj가 있으면 그 값만 사용.
            overall_approved=None,
            overall_reason=overall_reason,
            invert=True,
            default_on_missing="pass",
            allow_overall_fallback=False,
        )
        safety_res = self._to_result_tuple(
            safety_obj,
            overall_approved=overall_approved,
            overall_reason=overall_reason,
            invert=False,
            default_on_missing="fail",
        )

        return {
            "precondition": pre_res,
            "state_transition": rep_res,
            "irreversible_guard": safety_res,
        }

    def _fallback_all(self, action: ProposedAction, context: dict) -> dict:
        return {
            "precondition": self._fallback_precondition.check(action, context),
            "state_transition": self._fallback_state_transition.check(
                action, context
            ),
            "irreversible_guard": self._fallback_irreversible.check(action, context),
        }

    def _build_prompt(self, action: ProposedAction, context: dict) -> str:
        task_goal = context.get("task_goal", "")
        history = context.get("step_history", [])[-self.HISTORY_MAX :]

        prev_lines: list[str] = []
        for rec in history:
            if not rec.action:
                continue
            a = rec.action
            coords = self._format_coords(a)
            prev_lines.append(
                f"- {a.act_name} {coords} | summary='{(a.summary or '')[:120]}'"
            )

        prev_actions_text = "\n".join(prev_lines) if prev_lines else "(none)"

        proposed_coords = self._format_coords(action)

        return (
            "You are a safety checker for a mobile GUI agent.\n"
            f"Task goal: {task_goal}\n"
            f"Proposed action: {action.act_name} at coordinates {proposed_coords}\n"
            f"Previous actions: {prev_actions_text}\n\n"
            "Look at the screenshot and the coordinates, then judge:\n"
            "1. PRECONDITION: Is the screen ready for this action?\n"
            "   (e.g., required options selected, page fully loaded)\n"
            "2. REPETITION: Is this the same action repeated WITHOUT progress?\n"
            "   If YES (repeated without progress), set repetition.approved=true (BLOCK).\n"
            "   If NO (not repeated / progress expected), set repetition.approved=false (ALLOW).\n"
            "3. SAFETY: Will this action trigger an irreversible operation\n"
            "   (purchase, delete, cancel) that does NOT match the task goal?\n"
            "\n"
            "IMPORTANT:\n"
            "- Do NOT rely only on button visibility.\n"
            "- Check COORDINATES to determine what element is actually being clicked.\n"
            "- Do NOT block just because the Observation mentions forbidden words (e.g., 'Buy now').\n"
            "\n"
            "Respond ONLY with valid JSON (no markdown):\n"
            "{\n"
            '  "precondition": {"approved": true/false, "reason": "..."},\n'
            '  "repetition": {"approved": true/false, "reason": "..."},\n'
            '  "safety": {"approved": true/false, "reason": "..."} ,\n'
            '  "approved": true/false,\n'
            '  "reason": "..." \n'
            "}\n"
        )

    def _format_coords(self, action: ProposedAction) -> str:
        if action.act_name in ("click", "tap", "long_press"):
            if action.x is not None and action.y is not None:
                return f"({action.x}, {action.y})"
        if action.act_name == "swipe" and None not in (action.x1, action.y1, action.x2, action.y2):
            return f"from ({action.x1}, {action.y1}) to ({action.x2}, {action.y2})"
        if action.act_name == "scroll" and action.x is not None and action.y is not None:
            return f"({action.x}, {action.y}), direction='{action.direction}'"
        return "(no coordinates)"

    def _parse_vlm_json(self, text: str) -> Optional[dict]:
        cleaned = text.strip()
        # 코드블록 제거(있을 경우)
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)

        start = cleaned.find("{")
        if start < 0:
            return None

        depth = 0
        end = -1
        for i in range(start, len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        if end < 0:
            return None

        candidate = cleaned[start:end]
        try:
            return json.loads(candidate)
        except Exception:
            return None

    def _to_result_tuple(
        self,
        obj: Any,
        overall_approved: Any,
        overall_reason: str,
        invert: bool,
        default_on_missing: str,
        allow_overall_fallback: bool = True,
    ) -> tuple[str, str]:
        """
        Args:
            invert: True면 approved 의미를 뒤집어 해석.
                    (예: repetition은 approved=True => "repeated" => fail)
            default_on_missing: approved 필드/obj가 없을 때의 기본값 ("pass"|"fail")
        """
        def _apply(approved: Any, reason: str) -> tuple[str, str]:
            if approved is True:
                return ("fail", reason) if invert else ("pass", reason)
            if approved is False:
                return ("pass", reason) if invert else ("fail", reason)
            # None/unknown
            if default_on_missing == "pass":
                return ("pass", reason)
            return ("fail", reason)

        reason_fallback = (overall_reason or "").strip()

        if isinstance(obj, dict):
            approved = obj.get("approved", None)
            reason = (obj.get("reason", "") or "").strip() or reason_fallback
            # overall_approved는 per-check가 없을 때만 사용
            if (
                allow_overall_fallback
                and approved is None
                and overall_approved in (True, False)
            ):
                return _apply(overall_approved, reason)
            return _apply(approved, reason)

        # per-check obj 자체가 누락된 경우 overall로 폴백
        if allow_overall_fallback and overall_approved in (True, False):
            return _apply(overall_approved, reason_fallback)
        return ("pass", reason_fallback) if default_on_missing == "pass" else ("fail", reason_fallback or "missing approved")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Main Wrapper Class
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class CausalWrapper:
    """
    VLM이 제안한 액션을 실행 전에 3단계 검증하는 게이트키퍼.

    사용법:
        wrapper = CausalWrapper(task_goal="배달의민족에서 치킨 1마리 장바구니 담기")
        decision = wrapper.evaluate(proposed_action, screenshot_path)
        if decision.approved:
            execute_action(decision.modified_action or proposed_action)
        else:
            log(decision.reason)
    """

    def __init__(
        self,
        task_goal: str,
        precondition_checker: Optional[BasePreconditionChecker] = None,
        state_transition_checker: Optional[BaseStateTransitionChecker] = None,
        irreversible_guard: Optional[BaseIrreversibleGuard] = None,
    ):
        self.task_goal = task_goal
        self.step_history: list[StepRecord] = []
        self.round_count = 0

        # 인터페이스 분리: rule-based 기본, VLM critic으로 교체 가능
        self.precondition_checker = precondition_checker or RulePreconditionChecker()
        self.state_transition_checker = state_transition_checker or RuleStateTransitionChecker()
        self.irreversible_guard = irreversible_guard or RuleIrreversibleGuard()

        logger.info(f"[CausalWrapper] initialized. task_goal='{task_goal}'")

    def evaluate(
        self,
        proposed_action: ProposedAction,
        screenshot_path: str = "",
    ) -> WrapperDecision:
        """
        3가지 검증을 순서대로 수행.

        Args:
            proposed_action: VLM이 제안한 액션
            screenshot_path: 현재 스크린샷 경로 (VLM critic용 예약)

        Returns:
            WrapperDecision
        """
        self.round_count += 1
        context = {
            "task_goal": self.task_goal,
            "step_history": self.step_history,
            "screenshot_path": screenshot_path,
            "round_count": self.round_count,
        }

        check_results = {}

        # ── VLM Critic(단일 VLM 호출) 경로 ──
        combined_checker = self.precondition_checker
        if (
            hasattr(combined_checker, "check_all")
            and combined_checker is self.state_transition_checker
            and combined_checker is self.irreversible_guard
        ):
            all_results = combined_checker.check_all(proposed_action, context)

            result_a, reason_a = all_results.get("precondition", ("skip", ""))
            result_b, reason_b = all_results.get("state_transition", ("skip", ""))
            result_c, reason_c = all_results.get(
                "irreversible_guard", ("skip", "")
            )

            check_results["precondition"] = result_a
            check_results["state_transition"] = result_b
            check_results["irreversible_guard"] = result_c

            if result_a == "fail":
                decision = WrapperDecision(
                    approved=False,
                    reason=f"[Precondition FAIL] {reason_a}",
                    check_results=check_results,
                )
                self._log_decision(proposed_action, decision)
                return decision
            if result_b == "fail":
                decision = WrapperDecision(
                    approved=False,
                    reason=f"[StateTransition FAIL] {reason_b}",
                    check_results=check_results,
                )
                self._log_decision(proposed_action, decision)
                return decision
            if result_c == "fail":
                decision = WrapperDecision(
                    approved=False,
                    reason=f"[IrreversibleGuard FAIL] {reason_c}",
                    check_results=check_results,
                )
                self._log_decision(proposed_action, decision)
                return decision

            decision = WrapperDecision(
                approved=True,
                reason="All checks passed (VLM critic).",
                check_results=check_results,
            )
            self._log_decision(proposed_action, decision)
            return decision

        # ── A. Precondition Check ──
        result_a, reason_a = self.precondition_checker.check(proposed_action, context)
        check_results["precondition"] = result_a
        if result_a == "fail":
            decision = WrapperDecision(
                approved=False,
                reason=f"[Precondition FAIL] {reason_a}",
                check_results=check_results,
            )
            self._log_decision(proposed_action, decision)
            return decision

        # ── B. State Transition Consistency ──
        result_b, reason_b = self.state_transition_checker.check(proposed_action, context)
        check_results["state_transition"] = result_b
        if result_b == "fail":
            decision = WrapperDecision(
                approved=False,
                reason=f"[StateTransition FAIL] {reason_b}",
                check_results=check_results,
            )
            self._log_decision(proposed_action, decision)
            return decision

        # ── C. Irreversible Action Guard ──
        result_c, reason_c = self.irreversible_guard.check(proposed_action, context)
        check_results["irreversible_guard"] = result_c
        if result_c == "fail":
            decision = WrapperDecision(
                approved=False,
                reason=f"[IrreversibleGuard FAIL] {reason_c}",
                check_results=check_results,
            )
            self._log_decision(proposed_action, decision)
            return decision

        # ── 모두 통과 ──
        decision = WrapperDecision(
            approved=True,
            reason="All checks passed",
            check_results=check_results,
        )
        self._log_decision(proposed_action, decision)
        return decision

    def record_step(self, action: ProposedAction, screenshot_path: str = "") -> None:
        """액션 실행 후 히스토리에 기록."""
        record = StepRecord(
            round_n=self.round_count,
            action=action,
            screenshot_path=screenshot_path,
            timestamp=time.time(),
        )
        self.step_history.append(record)

    def _log_decision(self, action: ProposedAction, decision: WrapperDecision) -> None:
        """결정 결과를 콘솔에 출력."""
        status = "✅ APPROVED" if decision.approved else "❌ BLOCKED"
        checks = decision.check_results

        logger.info(
            f"Round {self.round_count} | {status} | "
            f"action={action.act_name} | "
            f"pre={checks.get('precondition', '?')} "
            f"trans={checks.get('state_transition', '?')} "
            f"guard={checks.get('irreversible_guard', '?')}"
        )
        if not decision.approved:
            logger.warning(f"  → {decision.reason}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ON/OFF 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def is_wrapper_enabled() -> bool:
    """환경변수 또는 config에서 wrapper ON/OFF 확인."""
    val = os.environ.get("WRAPPER_ENABLED", "true").strip().lower()
    return val not in ("false", "0", "no", "off")
