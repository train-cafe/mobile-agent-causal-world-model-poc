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
