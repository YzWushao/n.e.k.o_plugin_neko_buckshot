"""N.E.K.O 0.9.1 native plugin facade."""
from __future__ import annotations

import asyncio

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, lifecycle, llm_tool, neko_plugin, plugin_entry, ui

from .buckshot.service import BuckshotService

MANUAL = {"agent_auto": False}


def schema(properties=None, required=None):
    return {"type": "object", "properties": properties or {}, "required": required or []}


@neko_plugin
class BuckshotPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.service = BuckshotService(self.push_message)

    @lifecycle(id="startup")
    async def startup(self, **_):
        cfg = await self.config.dump(timeout=5.)
        self.service.configure(cfg.get("buckshot", {}) if isinstance(cfg, dict) else {})
        return Ok({"summary": "已就绪；在赌局陪玩面板点击开始，默认只分析"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        await self.service.close()
        return Ok({"summary": "已停止"})

    @ui.context(id="dashboard")
    async def dashboard(self, **_):
        return self.service.snapshot()

    @ui.action(id="status", label="刷新状态")
    @plugin_entry(id="status", name="赌局陪玩状态", metadata=MANUAL)
    async def status(self, **_):
        try:
            return Ok(await self.service.status())
        except Exception as exc:
            return Err(str(exc))

    @ui.action(id="start", label="开始陪玩")
    @plugin_entry(id="start", name="开始恶魔轮盘赌陪玩", description="开始观察原版游戏，默认只分析概率与建议；不能开启接管。",
                  input_schema=schema({"hwnd": {"type": "integer", "default": 0}}))
    async def start(self, hwnd: int = 0, **_):
        try:
            return Ok(await self.service.start(hwnd))
        except Exception as exc:
            return Err(str(exc))

    @ui.action(id="stop", label="暂停陪玩")
    @plugin_entry(id="stop", name="停止恶魔轮盘赌陪玩", description="停止观察并立即关闭接管。")
    async def stop(self, **_):
        return Ok(await self.service.stop())

    @ui.action(id="set_takeover", label="是否接管赌局")
    @plugin_entry(id="set_takeover", name="是否接管赌局", metadata=MANUAL,
                  input_schema=schema({"enabled": {"type": "boolean"}}, ["enabled"]))
    async def set_takeover(self, enabled: bool, **_):
        try:
            return Ok(self.service.set_takeover(enabled))
        except Exception as exc:
            return Err(str(exc))

    @ui.action(id="set_game_mode", label="单机 / 联机")
    @plugin_entry(id="set_game_mode", name="单机 / 联机", metadata=MANUAL,
                  input_schema=schema({"mode": {"type": "string", "enum": ["singleplayer", "multiplayer"]}}, ["mode"]))
    async def set_game_mode(self, mode: str, **_):
        try:
            return Ok(self.service.set_game_mode(mode))
        except Exception as exc:
            return Err(str(exc))

    @ui.action(id="reset_ammo", label="校正装弹")
    @plugin_entry(id="reset_ammo", name="校正装弹", metadata=MANUAL,
                  input_schema=schema({"live": {"type": "integer"}, "blank": {"type": "integer"}}, ["live", "blank"]))
    async def reset_ammo(self, live: int, blank: int, **_):
        return self._ammo("reset", live, blank)

    @ui.action(id="record_shell", label="记录消耗")
    @plugin_entry(id="record_shell", name="记录消耗", metadata=MANUAL,
                  input_schema=schema({"kind": {"type": "string", "enum": ["live", "blank", "unknown"]}}, ["kind"]))
    async def record_shell(self, kind: str, **_):
        return self._ammo("consume", kind)

    @ui.action(id="reveal_shell", label="记录验弹")
    @plugin_entry(id="reveal_shell", name="记录验弹", metadata=MANUAL,
                  input_schema=schema({"kind": {"type": "string", "enum": ["live", "blank"]}}, ["kind"]))
    async def reveal_shell(self, kind: str, **_):
        return self._ammo("reveal", kind)

    @ui.action(id="invert_shell", label="记录逆转")
    @plugin_entry(id="invert_shell", name="记录逆转", metadata=MANUAL)
    async def invert_shell(self, **_):
        return self._ammo("invert")

    @ui.action(id="clear_ammo", label="清除未知记录")
    @plugin_entry(id="clear_ammo", name="清除未知记录", metadata=MANUAL)
    async def clear_ammo(self, **_):
        return self._ammo("clear")

    def _ammo(self, method, *args):
        try:
            self.service.update_ledger(method, *args)
            return Ok(self.service.snapshot())
        except Exception as exc:
            return Err(str(exc))

    @llm_tool(name="buckshot_report", timeout=10., description=(
        "提交原版恶魔轮盘赌当前画面的分析和建议；只能记录真实看到的弹药事件。"
        "每个真实事件使用跨帧稳定event_id去重；不能把计划动作当成观察。"
        "返回精确概率，尚未观察到装弹数时概率未知。"), parameters=schema({
            "frame_id": {"type": "string"}, "ledger_revision": {"type": "integer"},
            "analysis": {"type": "string"}, "recommendation": {"type": "string"},
            "events": {"type": "array", "items": schema({
                "event_id": {"type": "string"},
                "type": {"type": "string", "enum": ["reset", "consume", "reveal", "invert", "clear"]},
                "live": {"type": "integer"}, "blank": {"type": "integer"},
                "kind": {"type": "string", "enum": ["live", "blank", "unknown"]},
            }, ["event_id", "type"])},
        }, ["frame_id", "ledger_revision", "analysis", "recommendation"]))
    async def report(self, frame_id: str, ledger_revision: int, analysis: str = "", recommendation: str = "", events=None, **_):
        try:
            return self.service.report(frame_id=frame_id, ledger_revision=ledger_revision,
                                       analysis=analysis, recommendation=recommendation, events=events)
        except Exception as exc:
            return {"is_error": True, "error": str(exc)}

    @llm_tool(name="buckshot_advise", timeout=10., description=(
        "按真实画面已确认的血量、道具库存、回合顺序比较普通单机或普通联机策略。"
        "先用buckshot_report记账，再调用本工具；弹药概率和模式由后台覆盖。"
        "仅建议实际持有道具；联机需要全部存活玩家和主人player_id，未轮到自己时等待。"
        "结果是有界搜索/战术评分，不是保证通关或精确整局胜率。"), parameters=schema({
            "frame_id": {"type": "string"},
            "state": schema({
                "player_health": {"type": "integer", "minimum": 1, "maximum": 16},
                "opponent_health": {"type": "integer", "minimum": 1, "maximum": 16},
                "max_health": {"type": "integer", "minimum": 1, "maximum": 16},
                "player_items": {"type": "array", "items": {"type": "string"}},
                "opponent_items": {"type": "array", "items": {"type": "string"}},
                "saw_active": {"type": "boolean"}, "opponent_cuffed": {"type": "boolean"},
                "handcuff_locked": {"type": "boolean"}, "healing_allowed": {"type": "boolean"},
                "player_id": {"type": "string"}, "turn_id": {"type": "string"},
                "turn_order": {"type": "array", "items": {"type": "string"}},
                "direction": {"type": "integer", "enum": [1, -1]},
                "future_shells": {"type": "object", "description": "手机已确认的弹序：位置为从当前弹起算的1基序号，消耗弹药后必须同步递减位置。", "additionalProperties": {"type": "string", "enum": ["live", "blank"]}},
                "players": {"type": "array", "maxItems": 4, "items": schema({
                    "id": {"type": "string"}, "name": {"type": "string"},
                    "health": {"type": "integer", "minimum": 0, "maximum": 16},
                    "max_health": {"type": "integer", "minimum": 1, "maximum": 16},
                    "items": {"type": "array", "items": {"type": "string"}},
                    "skipped": {"type": "boolean"}, "healing_allowed": {"type": "boolean"},
                }, ["id", "health", "items"])},
            }),
        }, ["frame_id", "state"]))
    async def advise(self, frame_id: str, state: dict, **_):
        try:
            from .buckshot.strategy import recommend
            self.service._frame(frame_id)
            if not isinstance(state, dict):
                raise ValueError("需要已确认的局面数据")
            state = dict(state)
            state["mode"] = self.service.game_mode
            state["ledger"] = self.service.ledger.snapshot()
            epoch = self.service.epoch
            revision = self.service.ledger_revision
            result = await asyncio.to_thread(recommend, state)
            self.service._frame(frame_id)
            if epoch != self.service.epoch or revision != self.service.ledger_revision:
                raise ValueError("局面已更新，请等待新画面重新计算策略")
            self.service.strategy = result
            return result
        except Exception as exc:
            return {"is_error": True, "error": str(exc)}

    @llm_tool(name="buckshot_act", timeout=10., description=(
        "仅在主人通过面板开启接管赌局后，执行原版恶魔轮盘赌一步click/key/wait。"
        "必须使用新画面的frame_id和control_epoch；关闭接管时后台硬拒绝。"
        "坐标是图像客户区归一化0到1；每步后等新画面。庄家回合/动画选wait。"), parameters=schema({
            "frame_id": {"type": "string"}, "control_epoch": {"type": "integer"},
            "action": {"type": "string", "enum": ["click", "key", "wait"]},
            "x": {"type": "number", "minimum": 0, "maximum": 1},
            "y": {"type": "number", "minimum": 0, "maximum": 1},
            "key": {"type": "string", "enum": ["ENTER", "ESCAPE", "SPACE", "TAB", "UP", "DOWN", "LEFT", "RIGHT", "E", "W", "A", "S", "D"]},
            "reason": {"type": "string"},
        }, ["frame_id", "control_epoch", "action", "reason"]))
    async def act(self, frame_id: str, control_epoch: int, action: str, x=None, y=None, key=None, reason="", **_):
        try:
            return await self.service.act(frame_id=frame_id, control_epoch=control_epoch,
                                          action=action, x=x, y=y, key=key, reason=reason)
        except Exception as exc:
            return {"is_error": True, "error": str(exc)}
