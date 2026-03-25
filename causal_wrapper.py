"""
causal_wrapper.py — Causal World Model prompt wrapper for AppAgent

Pearl Causality Ladder Level 2 (Intervention):
  - Level 1 (Association): "Given this screen, what button is usually pressed?"
  - Level 2 (Intervention): "If I press THIS button, what state will result?"

This wrapper adds two sections to AppAgent's prompt:
  [CAUSAL STATE HISTORY]  — causal chain of previous actions and their outcomes
  [CAUSAL REASONING GUIDE] — forces the VLM to predict state transitions before acting
"""

import os
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# Keywords that indicate irreversible or high-risk actions.
# Korean + English variants for common e-commerce flows.
IRREVERSIBLE_KEYWORDS = [
    # Korean purchase / payment triggers
    "바로구매", "즉시구매", "바로결제", "구매하기", "결제하기",
    "주문하기", "주문완료", "결제완료", "구매완료",
    # Korean destructive actions
    "장바구니 비우기", "전체삭제", "주문취소",
    # English equivalents
    "buy now", "checkout", "place order", "confirm purchase",
    "pay now", "submit order",
]

# UI states that are intermediate (not final), but VLMs often mistake for FINISH.
# These patterns in the screen XML/text suggest a popup / confirmation is still open.
INTERMEDIATE_POPUP_PATTERNS = [
    "장바구니에 추가",
    "장바구니에 담겼",
    "added to cart",
    "item added",
    "담기 완료",
    "추가되었습니다",
]


class CausalWorldModel:
    """
    Maintains an action history and wraps AppAgent prompts with causal reasoning.

    Usage (in task_executor.py):
        causal_model = CausalWorldModel(task_desc)           # before round loop
        prompt = causal_model.wrap_prompt(prompt, last_act)  # before VLM call
        causal_model.record_action(round_n, action, summary) # after response parse
    """

    def __init__(self, task_desc: str):
        self.task_desc = task_desc
        self.action_history: list[dict] = []
        # Each entry: {"round": int, "action": str, "summary": str}
        self._enabled = self._is_enabled()
        logger.info(f"[CausalWorldModel] enabled={self._enabled}, task='{task_desc}'")

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def record_action(self, round_n: int, action: str, summary: str) -> None:
        """Record the action and outcome summary after each round."""
        if not self._enabled:
            return
        self.action_history.append({
            "round": round_n,
            "action": action or "(unknown)",
            "summary": summary or "(no summary)",
        })
        logger.debug(f"[CausalWorldModel] recorded round {round_n}: {action}")

    def wrap_prompt(self, original_prompt: str, last_act: str) -> str:
        """
        Append [CAUSAL STATE HISTORY] and [CAUSAL REASONING GUIDE] to the prompt.

        Returns the original prompt unchanged if CAUSAL_MODE is disabled.
        """
        if not self._enabled:
            return original_prompt

        history_section = self._build_history_section()
        guide_section = self._build_guide_section(last_act)

        wrapped = original_prompt + "\n\n" + history_section + "\n\n" + guide_section
        logger.debug(f"[CausalWorldModel] prompt extended by "
                     f"{len(wrapped) - len(original_prompt)} chars")
        return wrapped

    # ──────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _is_enabled() -> bool:
        val = os.environ.get("CAUSAL_MODE", "true").strip().lower()
        return val not in ("false", "0", "no", "off")

    def _build_history_section(self) -> str:
        lines = ["[CAUSAL STATE HISTORY]"]
        if not self.action_history:
            lines.append("(No actions taken yet — this is the first step.)")
        else:
            for entry in self.action_history:
                lines.append(
                    f"Step {entry['round']}: {entry['action']} "
                    f"→ {entry['summary']}"
                )
        return "\n".join(lines)

    def _build_guide_section(self, last_act: str) -> str:
        irreversible_warning = self._irreversible_warning()
        popup_warning = self._popup_warning(last_act)

        lines = [
            "[CAUSAL REASONING GUIDE]",
            "Before selecting your next action, reason through the following steps:",
            "",
            f"1. GOAL: Current task = \"{self.task_desc}\"",
            "   Every action must advance toward this goal without unintended side effects.",
            "",
            "2. PREDICT: For each candidate action in the UI, ask:",
            "   \"If I tap/swipe/type THIS, what UI state will result?\"",
            "   - Navigate to result screens only if they are on the path to the goal.",
            "   - Avoid actions that open irrelevant screens or lose current context.",
            "",
            "3. IRREVERSIBLE ACTIONS:",
        ]

        if irreversible_warning:
            lines.append(f"   ⚠️  WARNING: {irreversible_warning}")
        else:
            lines.append(
                "   Check if any visible button triggers a purchase, deletion, or "
                "order confirmation. If so, select it only when the task explicitly "
                "requires that action."
            )

        lines += [
            "",
            "4. INTERMEDIATE STATE CHECK:",
        ]

        if popup_warning:
            lines.append(f"   ⚠️  WARNING: {popup_warning}")
        else:
            lines.append(
                "   If a confirmation popup or dialog is visible, it is an "
                "INTERMEDIATE state — do NOT output FINISH. Instead, dismiss or "
                "confirm the popup as appropriate for the task."
            )

        lines += [
            "",
            "5. OPTION COMPLETENESS:",
            "   Before tapping '담기', '장바구니', '추가', or 'Add to cart',",
            "   verify that ALL required option fields (size, quantity, color, etc.)",
            "   have been selected. Attempting to add without required options causes",
            "   an error popup that will require backtracking.",
            "",
            "Only after completing this reasoning, output your chosen action.",
        ]

        return "\n".join(lines)

    def _irreversible_warning(self) -> str:
        """Return a warning string if recent history contains risky context."""
        if not self.action_history:
            return ""
        # Check last action summary for irreversible keywords
        last_summary = self.action_history[-1]["summary"].lower() if self.action_history else ""
        for kw in IRREVERSIBLE_KEYWORDS:
            if kw.lower() in last_summary:
                return (
                    f"The previous action involved '{kw}'. "
                    f"Confirm this aligns with the task goal before proceeding."
                )
        return ""

    def _popup_warning(self, last_act: str) -> str:
        """Return a warning if last_act suggests we are in an intermediate popup state."""
        if not last_act:
            return ""
        for pattern in INTERMEDIATE_POPUP_PATTERNS:
            if pattern.lower() in last_act.lower():
                return (
                    f"The screen shows '{pattern}' — this is a confirmation popup, "
                    f"NOT the final completed state. Do NOT output FINISH yet. "
                    f"Close or confirm the popup to reach the actual final screen."
                )
        return ""

    def to_dict(self) -> dict:
        """Serialize for experiment logging."""
        return {
            "task_desc": self.task_desc,
            "enabled": self._enabled,
            "action_history": self.action_history,
        }

    def dump_log(self, path: str) -> None:
        """Write the full causal history to a JSON file for analysis."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"[CausalWorldModel] history saved to {path}")
