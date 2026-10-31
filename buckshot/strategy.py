"""Bounded, deterministic tactical search for ordinary SP and 2–4 player MP.

This is a current-magazine expectiminimax planner, not a full-game win-rate
oracle. Health/item utility evaluates unfinished leaves. MP uses a defensive
coalition model (every opponent minimizes our utility), and simulated item
observations are shared information. The caller must supply observed facts;
this module neither recognizes screenshots nor invents unseen inventory.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from functools import lru_cache
from itertools import combinations
from time import monotonic

ITEMS = ("magnifying_glass", "hand_saw", "handcuffs", "beer", "cigarette_pack",
         "adrenaline", "burner_phone", "inverter", "jammer", "remote")
SP_ITEMS = ITEMS[:5]
MP_ITEMS = tuple(item for item in ITEMS if item != "handcuffs")
NAMES = dict(zip(ITEMS, ("放大镜", "手锯", "手铐", "啤酒", "香烟", "肾上腺素", "一次性手机", "逆转器", "干扰器", "遥控器")))
ALIASES = {"magnifier": "magnifying_glass", "glass": "magnifying_glass", "handsaw": "hand_saw",
           "saw": "hand_saw", "cigarette": "cigarette_pack", "cigarettes": "cigarette_pack",
           "phone": "burner_phone", "cuffs": "handcuffs", **{v: k for k, v in NAMES.items()}}
WEIGHTS = (0.4, 0.6, 0.7, 0.2, 0.5, 0.7, 0.3, 0.5, 0.7, 0.25)


@dataclass(frozen=True)
class _Position:
    health: tuple
    inventories: tuple
    gun: tuple  # ((ordered binary shell sequence, probability), ...)
    turn: int = 0
    direction: int = 1
    skipped: tuple = ()
    locked: tuple = ()
    saw: bool = False


@dataclass(frozen=True)
class _Action:
    kind: str
    item: int = -1
    target: int = -1
    donor: int = -1


def _normalized(worlds):
    merged = defaultdict(float)
    for sequence, probability in worlds:
        merged[tuple(sequence)] += probability
    mass = sum(merged.values())
    if mass <= 0:
        raise ValueError("弹药数量与已知弹序互相矛盾。")
    return tuple(sorted((seq, round(prob / mass, 14)) for seq, prob in merged.items() if prob > 0))


@lru_cache(maxsize=128)
def _orders(live, blank):
    total = live + blank
    return tuple(tuple(int(i in indices) for i in range(total)) for indices in combinations(range(total), live))


def _gun(state):
    ledger = state.get("ledger") or {}
    rows = ledger.get("states") if isinstance(ledger, dict) else None
    rows = rows or [{"live": state.get("live"), "blank": state.get("blank"), "next": state.get("known_shell"), "probability": 1}]
    worlds = []
    for row in rows:
        live, blank = row.get("live"), row.get("blank")
        if type(live) is not int or type(blank) is not int or min(live, blank) < 0 or not 1 <= live + blank <= 10:
            raise ValueError("请提供已确认的剩余实弹、空包弹数量（合计 1–10 发）。")
        known = row.get("next")
        if known not in (None, "unknown", "live", "blank"):
            raise ValueError("known_shell 须为 live、blank 或 unknown。")
        orders = _orders(live, blank)
        if known in ("live", "blank"):
            orders = tuple(seq for seq in orders if seq[0] == (known == "live"))
        if not orders:
            raise ValueError("已知下一发与剩余弹药冲突。")
        mass = float(row.get("probability", 1))
        if not 0 < mass <= 1:
            raise ValueError("弹药状态的概率须大于 0 且不超过 1。")
        worlds.extend((seq, mass / len(orders)) for seq in orders)
    worlds = _normalized(worlds)
    # Future positions are one-based relative to the CURRENT chamber.
    for position, kind in (state.get("future_shells") or {}).items():
        index = int(position) - 1
        if not 0 <= index < len(worlds[0][0]) or kind not in ("live", "blank"):
            raise ValueError("已知弹序的位置或类型无效。")
        worlds = _normalized((seq, prob) for seq, prob in worlds if seq[index] == (kind == "live"))
    return worlds


def _inventory(value, pool):
    if value is None:
        raise ValueError("请先确认双方桌面道具；未知道具不能当作没有。")
    counts = Counter(value) if isinstance(value, list) else dict(value)
    result = [0] * len(ITEMS)
    for raw, count in counts.items():
        name = ALIASES.get(str(raw), str(raw))
        if type(count) is not int or not 0 <= count <= 8:
            raise ValueError("每种道具数量须为 0–8 的整数。")
        if count == 0:
            continue
        if name not in pool:
            raise ValueError(f"当前模式不支持道具：{raw}。请核对游戏模式与识别结果。")
        result[ITEMS.index(name)] += count
    if sum(result) > 8:
        raise ValueError("单个玩家的桌面道具不能超过 8 个。")
    return tuple(result)


class _Planner:
    def __init__(self, state):
        self.mode = state.get("mode", state.get("game_mode", "singleplayer"))
        if self.mode not in ("singleplayer", "multiplayer"):
            raise ValueError("游戏模式须为 singleplayer 或 multiplayer。")
        self.pool = SP_ITEMS if self.mode == "singleplayer" else MP_ITEMS
        if self.mode == "singleplayer":
            players = [dict(id="player", name="你", health=state.get("player_health"), items=state.get("player_items"),
                            healing_allowed=state.get("healing_allowed", True)),
                       dict(id="opponent", name="庄家", health=state.get("opponent_health"), items=state.get("opponent_items"),
                            skipped=state.get("opponent_cuffed", False))]
            players[1]["locked"] = bool(state.get("handcuff_locked") or state.get("opponent_cuffed"))
        else:
            players = state.get("players")
            if not isinstance(players, list) or not 2 <= len(players) <= 4:
                raise ValueError("联机需要 2–4 名玩家的顺序、血量与已确认道具。")
            ids = [str(player.get("id")) for player in players]
            me = str(state.get("player_id"))
            if state.get("player_id") is None or state.get("turn_id") is None:
                raise ValueError("联机须先确认 player_id 与 turn_id，不能猜测轮到谁。")
            if me not in ids or len(set(ids)) != len(ids):
                raise ValueError("player_id 须对应唯一的玩家 ID。")
            order = [str(value) for value in state.get("turn_order", ids)]
            if set(order) != set(ids) or len(order) != len(ids):
                raise ValueError("turn_order 须包含每个玩家 ID 且不能重复。")
            lookup = dict(zip(ids, players))
            pivot = order.index(me)
            players = [lookup[key] for key in order[pivot:] + order[:pivot]]
        self.ids = tuple(str(player["id"]) for player in players)
        self.names = tuple(str(player.get("name", player["id"])) for player in players)
        self.maximum = tuple(player.get("max_health", state.get("max_health")) for player in players)
        self.heal_allowed = tuple(bool(player.get("healing_allowed", True)) for player in players)
        health = tuple(player.get("health") for player in players)
        for current, maximum in zip(health, self.maximum):
            if type(current) is not int or type(maximum) is not int or not 0 <= current <= maximum <= 20 or maximum < 1:
                raise ValueError("请先确认各玩家的血量和本轮最大血量；被遮挡的血量不要猜测。")
        if health[0] <= 0:
            raise ValueError("当前玩家已出局。")
        inventories = tuple(_inventory(player.get("items"), self.pool) for player in players)
        if self.mode == "multiplayer" and len(players) > 2 and state.get("direction") is None:
            raise ValueError("三人或四人联机须确认当前轮转方向 direction（1 或 -1）。")
        direction = state.get("direction", 1)
        if direction not in (1, -1):
            raise ValueError("direction 须为 1 或 -1。")
        turn_id = str(state.get("turn_id", self.ids[0]))
        if turn_id not in self.ids:
            raise ValueError("turn_id 不在玩家列表内。")
        self.start = _Position(health, inventories, _gun(state), self.ids.index(turn_id), direction,
                               tuple(bool(player.get("skipped", False)) for player in players),
                               tuple(bool(player.get("locked", False)) for player in players), bool(state.get("saw_active", False)))
        self.memo = {}
        self.nodes = 0
        self.leaf_count = 0

    def _observe(self, gun, index=0):
        for shell in (0, 1):
            subset = [(seq, mass) for seq, mass in gun if seq[index] == shell]
            probability = sum(mass for _, mass in subset)
            if probability > 1e-12:
                yield shell, probability, _normalized(subset)

    def _pass(self, pos):
        skipped, locked = list(pos.skipped), list(pos.locked)
        current = pos.turn
        for _ in range(2 * len(pos.health) + 1):
            current = (current + pos.direction) % len(pos.health)
            if pos.health[current] <= 0:
                continue
            if skipped[current]:
                skipped[current] = False
                continue
            locked[current] = False
            return replace(pos, turn=current, skipped=tuple(skipped), locked=tuple(locked))
        return pos

    def _item_targets(self, pos, index):
        name, actor = ITEMS[index], pos.turn
        total = len(pos.gun[0][0])
        p_live = sum(seq[0] * prob for seq, prob in pos.gun)
        if name == "magnifying_glass":
            return [-1] if 1e-12 < p_live < 1 - 1e-12 else []
        if name == "hand_saw":
            return [-1] if not pos.saw else []
        if name == "cigarette_pack":
            return [-1] if self.heal_allowed[actor] and pos.health[actor] < self.maximum[actor] else []
        if name == "handcuffs":
            return [i for i, hp in enumerate(pos.health) if i != actor and hp > 0 and not pos.skipped[i] and not pos.locked[i]]
        if name == "jammer":
            return [i for i, hp in enumerate(pos.health) if i != actor and hp > 0 and not pos.skipped[i]]
        if name == "remote":
            return [-1] if sum(hp > 0 for hp in pos.health) > 2 else []
        if name == "burner_phone":
            first = 1 if self.mode == "singleplayer" else 2
            uncertain = any(len({seq[i] for seq, _ in pos.gun}) > 1 for i in range(first, total))
            return [-1] if uncertain else []
        return [-1]

    def actions(self, pos):
        actor = pos.turn
        result = [_Action("shoot", target=i) for i, hp in enumerate(pos.health) if hp > 0]
        for index, count in enumerate(pos.inventories[actor]):
            if not count:
                continue
            if ITEMS[index] == "adrenaline":
                for donor, inventory in enumerate(pos.inventories):
                    if donor == actor or pos.health[donor] <= 0:
                        continue
                    for stolen, amount in enumerate(inventory):
                        if amount and ITEMS[stolen] != "adrenaline":
                            result.extend(_Action("steal", stolen, target, donor) for target in self._item_targets(pos, stolen))
            else:
                result.extend(_Action("use", index, target) for target in self._item_targets(pos, index))
        return result

    def transitions(self, pos, action):
        actor = pos.turn
        if action.kind == "shoot":
            for shell, probability, observed in self._observe(pos.gun):
                health = list(pos.health)
                health[action.target] = max(0, health[action.target] - shell * (2 if pos.saw else 1))
                gun = _normalized((seq[1:], mass) for seq, mass in observed)
                after = replace(pos, health=tuple(health), gun=gun, saw=False)
                if action.target != actor or shell:
                    after = self._pass(after)
                yield probability, after
            return
        inventory = [list(items) for items in pos.inventories]
        if action.kind == "steal":
            inventory[actor][ITEMS.index("adrenaline")] -= 1
            inventory[action.donor][action.item] -= 1
        else:
            inventory[actor][action.item] -= 1
        after = replace(pos, inventories=tuple(tuple(items) for items in inventory))
        name = ITEMS[action.item]
        if name == "hand_saw":
            yield 1.0, replace(after, saw=True)
        elif name == "cigarette_pack":
            health = list(after.health)
            health[actor] = min(self.maximum[actor], health[actor] + 1)
            yield 1.0, replace(after, health=tuple(health))
        elif name in ("handcuffs", "jammer"):
            skipped, locked = list(after.skipped), list(after.locked)
            skipped[action.target] = True
            if name == "handcuffs":
                locked[action.target] = True
            yield 1.0, replace(after, skipped=tuple(skipped), locked=tuple(locked))
        elif name == "remote":
            yield 1.0, replace(after, direction=-after.direction)
        elif name == "inverter":
            yield 1.0, replace(after, gun=_normalized(((1 - seq[0],) + seq[1:], mass) for seq, mass in after.gun))
        elif name == "beer":
            for shell, probability, observed in self._observe(after.gun):
                yield probability, replace(after, gun=_normalized((seq[1:], mass) for seq, mass in observed), saw=False)
        elif name == "magnifying_glass":
            for shell, probability, observed in self._observe(after.gun):
                yield probability, replace(after, gun=observed)
        elif name == "burner_phone":
            first = 1 if self.mode == "singleplayer" else 2
            positions = range(first, len(after.gun[0][0]))
            for index in positions:
                for shell, probability, observed in self._observe(after.gun, index):
                    yield probability / len(positions), replace(after, gun=observed)

    def utility(self, pos):
        if pos.health[0] <= 0:
            return -1.0
        opponents = [i for i in range(1, len(pos.health)) if pos.health[i] > 0]
        if not opponents:
            return 1.0
        own = pos.health[0] / self.maximum[0]
        other = sum(pos.health[i] / self.maximum[i] for i in opponents) / len(opponents)
        def resource(i):
            return sum(amount * weight for amount, weight in zip(pos.inventories[i], WEIGHTS))

        value = .5 * (own - other) + .025 * (resource(0) - max(resource(i) for i in opponents))
        value += .12 * (len(pos.health) - 1 - len(opponents)) / (len(pos.health) - 1)
        value += .05 if pos.turn == 0 else -.05
        return max(-.9, min(.9, value))

    def value(self, pos, depth):
        self.nodes += 1
        if self.nodes > self.max_nodes or monotonic() > self.deadline:
            raise _BudgetExceeded
        terminal = pos.health[0] <= 0 or not any(pos.health[1:])
        if terminal:
            return self.utility(pos), ()
        if depth <= 0 or not pos.gun[0][0]:
            self.leaf_count += 1
            return self.utility(pos), ()
        key = (pos, depth)
        if key in self.memo:
            return self.memo[key]
        best_score, best_plan = (-2.0, ()) if pos.turn == 0 else (2.0, ())
        for action in self.actions(pos):
            score, continuation, largest = 0.0, (), -1.0
            outcomes = list(self.transitions(pos, action))
            for probability, after in outcomes:
                child_score, plan = self.value(after, depth - 1)
                score += probability * child_score
                if probability > largest:
                    largest, continuation = probability, plan
            if (pos.turn == 0 and score > best_score + 1e-10) or (pos.turn != 0 and score < best_score - 1e-10):
                # Explanatory combinations contain only OUR uninterrupted
                # turn. Opponent actions still affect the minimax score, but
                # must never be presented as instructions for the player.
                best_score = score
                best_plan = (action,) + (continuation if len(outcomes) == 1 else ()) if pos.turn == 0 else ()
        self.memo[key] = (best_score, best_plan)
        return best_score, best_plan

    def describe(self, action):
        if action.kind == "shoot":
            return {"type": "shoot", "target": self.ids[action.target]}
        result = {"type": "use_item", "item": "adrenaline" if action.kind == "steal" else ITEMS[action.item]}
        if action.target >= 0:
            result["target"] = self.ids[action.target]
        if action.kind == "steal":
            result.update(steal_from=self.ids[action.donor], stolen_item=ITEMS[action.item])
        return result

    def text(self, action):
        if action.kind == "shoot":
            return "对自己开枪" if action.target == 0 else f"向{self.names[action.target]}开枪"
        text = f"使用{NAMES[ITEMS[action.item]]}"
        if action.kind == "steal":
            text = f"使用肾上腺素，窃取{self.names[action.donor]}的{NAMES[ITEMS[action.item]]}并立即使用"
        if action.target >= 0:
            text += f"（目标：{self.names[action.target]}）"
        return text


class _BudgetExceeded(Exception):
    pass


def recommend(state: dict, *, max_depth=6, max_nodes=8000, time_budget_ms=150):
    """Rank legal first actions and short combinations from confirmed state.

    Common inputs: mode, live/blank OR ledger.snapshot(), known_shell,
    future_shells (1-based positions), saw_active. SP: player_health,
    opponent_health,max_health,player_items,opponent_items,opponent_cuffed,
    handcuff_locked,healing_allowed. MP: players [{id,name,health,max_health,
    items,skipped,locked,healing_allowed}],player_id,turn_order,direction,
    turn_id. Inventories must be explicitly known ([] means observed empty).
    """
    try:
        planner = _Planner(state)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return {"status": "needs_information", "action": None, "recommendation": str(exc), "analysis": "当前信息不足，先确认画面，不执行猜测的道具操作。", "ranked_actions": []}
    if planner.start.turn != 0:
        return {"status": "wait", "action": None, "recommendation": "等待轮到你，再根据最新画面重新计算。", "analysis": "当前不是你的操作回合。", "ranked_actions": []}
    planner.max_nodes = max(128, min(100000, int(max_nodes)))
    planner.deadline = monotonic() + max(10, min(3000, float(time_budget_ms))) / 1000
    root = planner.actions(planner.start)
    ranked, completed, truncated = [], 0, False
    for depth in range(1, max(1, min(12, int(max_depth))) + 1):
        current = []
        try:
            for action in root:
                score, continuation, largest = 0.0, (), -1.0
                outcomes = list(planner.transitions(planner.start, action))
                for probability, after in outcomes:
                    value, plan = planner.value(after, depth - 1)
                    score += probability * value
                    if probability > largest:
                        largest, continuation = probability, plan
                current.append((score, action, continuation if len(outcomes) == 1 else ()))
        except _BudgetExceeded:
            truncated = True
            break
        # Preserve deterministic action order on equal scores. Every root gets
        # the same completed horizon; an interrupted iteration is discarded.
        ranked = sorted(current, key=lambda row: -round(row[0], 10))
        completed = depth
    if not ranked:
        # Budget can be tiny relative to an information item's many outcomes.
        # Use complete one-step values for every root rather than partial data.
        ranked = sorted([(sum(prob * planner.utility(after) for prob, after in planner.transitions(planner.start, action)), action, ()) for action in root], key=lambda row: -round(row[0], 10))
        truncated = True
    best = ranked[0]
    best_plan = [best[1], *best[2]]
    p_live = sum(seq[0] * mass for seq, mass in planner.start.gun)
    assumptions = ["只搜索当前一轮装弹，未模拟下一次发道具与重新装弹。", "未搜索到终局时使用血量、道具与回合控制的近似评分；评分不是实战胜率。",
                   "搜索中的查看弹药结果视为共享信息；真实对手的私有信息与行为可能不同。"]
    if planner.mode == "multiplayer":
        assumptions.append("联机按其他玩家都优先针对你的保守模型评估；不假设结盟或预测真实玩家意图。")
    return {"status": "ok", "mode": planner.mode, "action": planner.describe(best[1]),
            "recommendation": planner.text(best[1]) + "。每完成一步后重新观察，不连续照抄后续分支。",
            "analysis": f"下一发实弹概率 {p_live:.1%}；比较实际持有道具的合法用法、目标与短组合。",
            "candidate_combination": [planner.text(action) for action in best_plan[:5]],
            "ranked_actions": [{"action": planner.describe(action), "utility": round(score, 5),
                                "candidate_combination": [planner.text(step) for step in (action, *continuation)[:5]]}
                               for score, action, continuation in ranked[:8]],
            "search": {"depth_completed": completed, "nodes": planner.nodes, "budget_exhausted": truncated, "approximate": True},
            "assumptions": assumptions, "supported_items": list(planner.pool)}
