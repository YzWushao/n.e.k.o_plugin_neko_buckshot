from __future__ import annotations

import asyncio
import copy
import io
import json
import math
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass

from . import windows
from .probability import AmmoLedger
from .prompts import SYSTEM


@dataclass
class Frame:
    token: str
    created: float
    epoch: int
    image: object
    size: tuple[int, int]
    used: bool = False


def encode_frame(image):
    """Bound image + prompt well below the host's 256 KiB push budget."""
    image = image.copy()
    image.thumbnail((1024, 1024))
    while True:
        for quality in (82, 72, 60, 45):
            out = io.BytesIO()
            image.save(out, "JPEG", quality=quality)
            if out.tell() <= 80 * 1024:
                return image, out.getvalue()
        if max(image.size) <= 320:
            raise ValueError("画面无法压缩到传输限制内")
        image.thumbnail((int(image.width * .8), int(image.height * .8)))


def scene_distance(a, b):
    """Coarse scene change guard, not a game-state recognition algorithm."""
    av = a.convert("L").resize((64, 40)).tobytes()
    bv = b.convert("L").resize((64, 40)).tobytes()
    return sum(abs(x-y) for x, y in zip(av, bv)) / (len(av) * 255)


class BuckshotService:
    def __init__(self, push, backend=windows):
        self.push = push
        self.backend = backend
        self.ledger = AmmoLedger()
        self.running = False
        self.takeover = False
        self.game_mode = "singleplayer"
        self.epoch = 0
        self.hwnd = 0
        self.paused_reason = ""
        self.last_error = ""
        self.last_action = ""
        self.analysis = ""
        self.recommendation = ""
        self.strategy = {}
        self.last_frame_id = ""
        self.ledger_revision = 0
        self._frames = OrderedDict()
        self._events = OrderedDict()
        self._reports = set()
        self._task = None
        self._lock = asyncio.Lock()
        self._next_capture = 0.
        self._next_analysis = 0.
        self._started = 0.
        self.capture_interval = 1.5
        self.analysis_interval = 6.
        self.max_age = 20.
        self.session_seconds = 3600
        self._window_cache = []

    def configure(self, cfg):
        def number(key, default, low, high):
            value = cfg.get(key, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"无效配置: {key}")
            return max(low, min(high, float(value)))
        self.capture_interval = number("capture_interval_seconds", 1.5, 1., 10.)
        self.analysis_interval = number("analysis_interval_seconds", 6., 3., 60.)
        self.max_age = number("frame_max_age_seconds", 20., 5., 30.)
        self.session_seconds = number("session_minutes", 60, 1, 240) * 60
        self.backend.configure_game_path(cfg.get("game_exe") or None)

    def snapshot(self):
        return {
            "running": self.running, "takeover": self.takeover, "game_mode": self.game_mode,
            "control_epoch": self.epoch, "hwnd": self.hwnd,
            "paused_reason": self.paused_reason, "last_error": self.last_error,
            "last_action": self.last_action, "last_frame_id": self.last_frame_id,
            "analysis": self.analysis, "recommendation": self.recommendation,
            "strategy": self.strategy,
            "ledger": self.ledger.snapshot(), "ledger_revision": self.ledger_revision,
            "windows": self._window_cache,
        }

    async def status(self):
        self._window_cache = await asyncio.to_thread(self.backend.list_game_windows)
        return self.snapshot()

    def _invalidate(self):
        self.epoch += 1
        self._frames.clear()
        self._reports.clear()
        self.last_frame_id = ""

    def set_takeover(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("是否接管必须是布尔值")
        # Never persist this capability, and never allow the LLM to enable it.
        if self.takeover != enabled:
            self.takeover = enabled
            self._invalidate()
            self._next_capture = self._next_analysis = 0.
        return self.snapshot()

    def set_game_mode(self, mode):
        if mode not in ("singleplayer", "multiplayer"):
            raise ValueError("请选择单机或联机")
        if self.game_mode != mode:
            self.game_mode = mode
            self.takeover = False
            self.ledger.clear()
            self.ledger_revision += 1
            self._events.clear()
            self.analysis = self.recommendation = ""
            self.strategy = {}
            self._invalidate()
            self._next_capture = self._next_analysis = 0.
        return self.snapshot()

    async def start(self, hwnd=0):
        if type(hwnd) is not int or hwnd < 0:
            raise ValueError("无效游戏窗口")
        wins = await asyncio.to_thread(self.backend.list_game_windows)
        self._window_cache = wins
        candidates = [w for w in wins if not hwnd or w["hwnd"] == hwnd]
        if len(candidates) != 1:
            raise ValueError("请先启动游戏；若有多个游戏窗口，请在面板中选择一个")
        if self.running:
            if self.hwnd != candidates[0]["hwnd"]:
                raise ValueError("请先暂停再切换游戏窗口")
            return self.snapshot()
        self.hwnd = candidates[0]["hwnd"]
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._invalidate()
        self.running = True
        self.paused_reason = self.last_error = ""
        self._started = time.monotonic()
        self._next_capture = self._next_analysis = 0.
        # Start only from a regular entry (the host startup loop is transient).
        self._task = asyncio.create_task(self._loop())
        return self.snapshot()

    def halt(self, reason="已暂停"):
        self.running = self.takeover = False
        self.paused_reason = reason
        self._invalidate()
        return self.snapshot()

    async def close(self):
        self.halt()
        if self._task and not self._task.done():
            if self._task.get_loop() is not asyncio.get_running_loop():
                loop = self._task.get_loop()
                if not loop.is_closed():
                    loop.call_soon_threadsafe(self._task.cancel)
                return
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def stop(self):
        await self.close()
        return self.snapshot()

    async def _loop(self):
        try:
            while self.running:
                if self.backend.is_key_down("F8"):
                    self.halt("F8 已暂停陪玩并关闭接管")
                    break
                if time.monotonic() - self._started > self.session_seconds:
                    self.halt("本次陪玩已到时，请手动开始下一次")
                    break
                if time.monotonic() >= self._next_capture:
                    try:
                        await self.observe()
                    except Exception as exc:
                        reason = str(exc)[:240]
                        if not self.paused_reason:
                            self._invalidate()
                        self.paused_reason = reason
                        self.last_error = reason
                        self._next_capture = time.monotonic() + self.capture_interval
                await asyncio.sleep(.1)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = str(exc)[:240]
            self.halt("观察循环停止，请检查状态后重新开始")

    async def observe(self, force=False):
        async with self._lock:
            if not self.running:
                raise ValueError("陪玩尚未开始")
            epoch = self.epoch
            image = await asyncio.to_thread(self.backend.capture, self.hwnd)
            size = image.size
            small, jpeg = await asyncio.to_thread(encode_frame, image)
            if not self.running or self.epoch != epoch:
                return self.snapshot()
            now = time.monotonic()
            token = secrets.token_hex(6)
            frame = Frame(token, now, epoch, small, size)
            self._frames[token] = frame
            while len(self._frames) > 20:
                old, _ = self._frames.popitem(last=False)
                self._reports.discard(old)
            self.last_frame_id = token
            self.paused_reason = self.last_error = ""
            respond = force or now >= self._next_analysis
            mode = "接管赌局：可选择一步操作" if self.takeover else "只分析：严禁操作，给出概率及建议"
            text = (SYSTEM if respond else "Buckshot 实时观察；仅更新画面，遵守面板模式。") + "\n本帧数据=" + json.dumps({
                "frame_id": token, "control_epoch": epoch, "mode": mode, "game_mode": self.game_mode,
                "ledger_revision": self.ledger_revision, "ledger": self.ledger.snapshot(),
                "last_action": self.last_action,
            }, ensure_ascii=False)
            receipt = self.push(
                source="neko_buckshot", visibility=[],
                ai_behavior="respond" if respond else "read",
                parts=[{"type": "text", "text": text}, {"type": "image", "data": jpeg, "mime": "image/jpeg"}],
                priority=5 if respond else 3, coalesce_key="buckshot_decision" if respond else "buckshot_view",
            )
            rejected = isinstance(receipt, dict) and receipt.get("submitted") is False
            if rejected:
                self._frames.pop(token, None)
                self.last_frame_id = ""
                raise RuntimeError("画面推送被 N.E.K.O 拒绝；请检查模型会话与插件日志")
            self._next_capture = now + self.capture_interval
            if respond:
                self._next_analysis = now + self.analysis_interval
            return self.snapshot()

    def _frame(self, token, epoch=None):
        f = self._frames.get(token)
        if not self.running or not f or f.epoch != self.epoch:
            raise ValueError("旧画面或会话已暂停，请等待新画面")
        if epoch is not None and (type(epoch) is not int or epoch != self.epoch):
            raise ValueError("接管授权已经改变，请等待新画面")
        if time.monotonic() - f.created > self.max_age:
            raise ValueError("画面过期，请等待新画面")
        return f

    async def act(self, *, frame_id, control_epoch, action, x=None, y=None, key=None, reason=""):
        async with self._lock:
            if not self.takeover:
                raise ValueError("接管已关闭：只能分析，禁止鼠标和键盘操作")
            if self.paused_reason:
                raise ValueError("当前暂停：" + self.paused_reason)
            f = self._frame(frame_id, control_epoch)
            if f.used:
                raise ValueError("这张画面已经执行过操作，必须先观察新画面")
            if action not in ("click", "key", "wait"):
                raise ValueError("只支持 click/key/wait")
            if self.backend.is_key_down("F8"):
                self.halt("F8 已暂停陪玩并关闭接管")
                raise ValueError("已停止")
            if action != "wait":
                current = await asyncio.to_thread(self.backend.capture, self.hwnd)
                if current.size != f.size or scene_distance(f.image, current) > .12:
                    raise ValueError("画面已经明显变化，先等待新画面再操作")
                # Check again after the threaded capture: switch-off must win.
                self._frame(frame_id, control_epoch)
                if self.backend.is_key_down("F8"):
                    self.halt("F8 已暂停陪玩并关闭接管")
                    raise ValueError("F8 已按下，陪玩已停止")
                if not self.takeover:
                    raise ValueError("接管已关闭")
            # Consume before input: a partially failed SendInput is never retried.
            f.used = True
            self._invalidate()
            if action == "click":
                self.backend.click(self.hwnd, x, y)
            elif action == "key":
                self.backend.key(self.hwnd, key)
            self.last_action = f"{action}: {str(reason)[:160]}"
            self._next_capture = time.monotonic() + (1.2 if action != "wait" else 1.5)
            self._next_analysis = self._next_capture
            return {"summary": "操作已发送，等待实际画面确认结果", "action": self.last_action,
                    "control_epoch": self.epoch, "ledger": self.ledger.snapshot()}

    def update_ledger(self, method, *args):
        result = getattr(self.ledger, method)(*args)
        self.ledger_revision += 1
        self._events.clear()
        self._invalidate()
        self._next_capture = self._next_analysis = 0.
        return result

    def report(self, *, frame_id, ledger_revision, analysis="", recommendation="", events=None):
        self._frame(frame_id)
        if frame_id in self._reports:
            return {"summary": "此帧已分析，不重复记账", "ledger": self.ledger.snapshot()}
        if type(ledger_revision) is not int or ledger_revision != self.ledger_revision:
            raise ValueError("弹药记录已更新，请按下一帧的账本重算")
        if events is None:
            events = []
        if not isinstance(events, list) or len(events) > 8:
            raise ValueError("每次最多记录8个已观察事件")
        trial = copy.deepcopy(self.ledger)
        new_ids = []
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("无效弹药事件")
            eid = event.get("event_id")
            if not isinstance(eid, str) or not 1 <= len(eid) <= 80:
                raise ValueError("每个真实事件需要稳定的 event_id")
            if eid in self._events or eid in new_ids:
                continue
            kind = event.get("type")
            if kind == "reset":
                trial.reset(event.get("live"), event.get("blank"))
            elif kind == "consume":
                trial.consume(event.get("kind"))
            elif kind == "reveal":
                trial.reveal(event.get("kind"))
            elif kind == "invert":
                trial.invert()
            elif kind == "clear":
                trial.clear()
            else:
                raise ValueError("未知弹药事件")
            new_ids.append(eid)
        self.ledger = trial
        if new_ids:
            self.ledger_revision += 1
        for eid in new_ids:
            self._events[eid] = True
        while len(self._events) > 512:
            self._events.popitem(last=False)
        self._reports.add(frame_id)
        self.analysis = str(analysis)[:1200]
        self.recommendation = str(recommendation)[:800]
        return {"summary": self.analysis, "recommendation": self.recommendation,
                "ledger": self.ledger.snapshot(), "ledger_revision": self.ledger_revision}
