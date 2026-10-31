"""Finite shell probabilities, with uncertainty retained after unobserved shots.

The state is a joint distribution of (live count, blank count, known next
shell). A next value of None means the order is exchangeable. Inversion
materializes the next-shell branches before changing the counts, preserving
the correlation between the next shell and the contents of the magazine.
No game state is read here; observations come from the player or vision model.
"""
from __future__ import annotations

from collections import defaultdict
from math import isclose


class AmmoLedger:
    MAX_SHELLS = 16

    def __init__(self):
        self._states: dict[tuple[int, int, str | None], float] = {}

    def clear(self):
        self._states = {}
        return self.snapshot()

    def reset(self, live: int, blank: int):
        if (type(live) is not int or type(blank) is not int
                or live < 0 or blank < 0
                or not 1 <= live + blank <= self.MAX_SHELLS):
            raise ValueError("实弹、空包弹必须是非负整数，总数须为 1–16。")
        self._states = {(live, blank, None): 1.0}
        return self.snapshot()

    def _require_shell(self):
        if not self._states:
            raise ValueError("还不知道这一轮的装弹数量，请先记录装弹。")
        if next(iter(self._states))[0] + next(iter(self._states))[1] == 0:
            raise ValueError("已无剩余弹药；重新装弹后请记录新的数量。")

    @staticmethod
    def _kinds(live, blank, known):
        if known is not None:
            return ((known, 1.0),)
        total = live + blank
        return tuple((kind, count / total)
                     for kind, count in (("live", live), ("blank", blank)) if count)

    def _commit(self, states):
        total = sum(states.values())
        if total <= 0:
            raise ValueError("这条观察与已记录的弹药矛盾。请核对或重置弹药记录。")
        self._states = {state: mass / total for state, mass in states.items() if mass > 0}
        return self.snapshot()

    def consume(self, kind: str):
        """Observe one discharged/ejected shell; 'unknown' retains all possibilities."""
        if kind not in ("live", "blank", "unknown"):
            raise ValueError("弹药类型须为 live、blank 或 unknown。")
        self._require_shell()
        states = defaultdict(float)
        for (live, blank, known), prior in self._states.items():
            for actual, chance in self._kinds(live, blank, known):
                if kind != "unknown" and kind != actual:
                    continue
                states[(live - (actual == "live"), blank - (actual == "blank"), None)] += prior * chance
        return self._commit(states)

    def reveal(self, kind: str):
        """Condition on a magnifier/other observation of the current next shell."""
        if kind not in ("live", "blank"):
            raise ValueError("查看结果须为 live 或 blank。")
        self._require_shell()
        states = defaultdict(float)
        for (live, blank, known), prior in self._states.items():
            for actual, chance in self._kinds(live, blank, known):
                if actual == kind:
                    states[(live, blank, kind)] += prior * chance
        return self._commit(states)

    def invert(self):
        """Invert the next shell, changing both its type and remaining counts."""
        self._require_shell()
        states = defaultdict(float)
        for (live, blank, known), prior in self._states.items():
            for actual, chance in self._kinds(live, blank, known):
                if actual == "live":
                    states[(live - 1, blank + 1, "blank")] += prior * chance
                else:
                    states[(live + 1, blank - 1, "live")] += prior * chance
        return self._commit(states)

    def snapshot(self):
        if not self._states:
            return {
                "initialized": False, "total": None, "p_live": None, "p_blank": None,
                "next_known": None, "expected_live": None, "expected_blank": None,
                "states": [], "recommendation": "装弹数量未知，请先观察装弹展示；不能可靠计算概率。",
            }
        total = sum(next(iter(self._states))[:2])
        p_live = sum(prior * (1.0 if known == "live" else 0.0 if known == "blank"
                             else live / total if total else 0.0)
                     for (live, blank, known), prior in self._states.items()) if total else None
        p_blank = 1.0 - p_live if p_live is not None else None
        next_known = ("live" if isclose(p_live, 1.0, abs_tol=1e-12)
                      else "blank" if isclose(p_live, 0.0, abs_tol=1e-12) else None) if total else None
        if not total:
            recommendation = "弹药已耗尽，等待重新装弹并更新记录。"
        elif next_known == "blank":
            recommendation = "下一发确定为空包弹；通常可对自己开枪保留回合，仍需结合当前道具效果。"
        elif next_known == "live":
            recommendation = "下一发确定为实弹；通常优先攻击对手，并考虑增伤道具。"
        else:
            recommendation = (f"下一发实弹概率 {p_live:.1%}；基础伤害为 1 时，攻击对手的单次期望伤害为 {p_live:.3f}。"
                              "这不是完整最优策略，还需比较血量、道具与回合收益。")
        return {
            "initialized": True, "total": total, "p_live": p_live, "p_blank": p_blank,
            "next_known": next_known,
            "expected_live": sum(live * prior for (live, blank, known), prior in self._states.items()),
            "expected_blank": sum(blank * prior for (live, blank, known), prior in self._states.items()),
            "states": [{"live": live, "blank": blank, "next": known, "probability": prior}
                       for (live, blank, known), prior in sorted(self._states.items(), key=lambda row: str(row[0]))],
            "recommendation": recommendation,
        }
