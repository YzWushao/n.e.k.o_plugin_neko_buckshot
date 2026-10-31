"""Exercise the control boundary with synthetic images and zero OS input."""
from __future__ import annotations

import asyncio
import copy
import sys
import threading
import time
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1]))
from buckshot.service import BuckshotService


class FakeBackend:
    def __init__(self):
        self.inputs = []
        self.f8 = False
        self.capture_count = 0
        self.image = Image.new("RGB", (640, 400), (32, 48, 64))
        self.capture_entered = threading.Event()
        self.capture_release = threading.Event()
        self.block_capture = False

    def configure_game_path(self, path):
        self.path = path

    def list_game_windows(self):
        return [{"hwnd": 77, "title": "Synthetic game", "foreground": True}]

    def capture(self, hwnd):
        assert hwnd == 77
        self.capture_count += 1
        if self.block_capture:
            self.capture_entered.set()
            if not self.capture_release.wait(3):
                raise RuntimeError("Test did not release the blocked capture")
        return self.image.copy()

    def click(self, hwnd, x, y):
        self.inputs.append(("click", hwnd, x, y))

    def key(self, hwnd, key):
        self.inputs.append(("key", hwnd, key))

    def is_key_down(self, key):
        assert key == "F8"
        return self.f8


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self, takeover=False, receipt=None):
        self.backend = FakeBackend()
        self.pushes = []

        def push(**kwargs):
            self.pushes.append(kwargs)
            return {"submitted": True} if receipt is None else receipt

        service = BuckshotService(push, backend=self.backend)
        service.running = True
        service.hwnd = 77
        service._started = time.monotonic()
        if takeover:
            service.set_takeover(True)
        self.service = service
        return service

    async def asyncTearDown(self):
        if hasattr(self, "backend"):
            self.backend.capture_release.set()
        if hasattr(self, "service"):
            await self.service.close()

    async def frame(self, service):
        result = await service.observe()
        return result["last_frame_id"], result["control_epoch"]

    async def blocked_action(self, service, token, epoch):
        self.backend.block_capture = True
        task = asyncio.create_task(service.act(
            frame_id=token, control_epoch=epoch, action="click", x=.5, y=.5))
        entered = await asyncio.to_thread(self.backend.capture_entered.wait, 2)
        self.assertTrue(entered, "action must reach its asynchronous recheck capture")
        return task

    async def test_analysis_only_refuses_every_action_without_input(self):
        service = self.make_service()
        token, epoch = await self.frame(service)
        for action in ("click", "key", "wait"):
            with self.assertRaisesRegex(ValueError, "接管已关闭"):
                await service.act(frame_id=token, control_epoch=epoch, action=action,
                                  x=.5, y=.5, key="ENTER")
        self.assertEqual(self.backend.inputs, [])
        self.assertEqual(self.backend.capture_count, 1)
        self.assertIn("只分析", self.pushes[0]["parts"][0]["text"])

    async def test_disabling_takeover_during_capture_wins(self):
        service = self.make_service(takeover=True)
        token, epoch = await self.frame(service)
        task = await self.blocked_action(service, token, epoch)
        service.set_takeover(False)
        self.backend.capture_release.set()
        with self.assertRaises(ValueError):
            await task
        self.assertFalse(service.takeover)
        self.assertEqual(self.backend.inputs, [])
        self.assertNotIn(token, service._frames)

    async def test_pending_observation_cannot_publish_after_disable(self):
        service = self.make_service(takeover=True)
        self.backend.block_capture = True
        task = asyncio.create_task(service.observe())
        self.assertTrue(await asyncio.to_thread(self.backend.capture_entered.wait, 2))
        service.set_takeover(False)
        self.backend.capture_release.set()
        await task
        self.assertEqual(self.pushes, [])
        self.assertEqual(service.last_frame_id, "")
        self.assertEqual(len(service._frames), 0)

    async def test_old_epoch_and_stale_frames_cannot_control(self):
        service = self.make_service(takeover=True)
        old_token, old_epoch = await self.frame(service)
        service.set_takeover(False)
        service.set_takeover(True)
        new_token, new_epoch = await self.frame(service)
        self.assertGreater(new_epoch, old_epoch)
        with self.assertRaises(ValueError):
            await service.act(frame_id=old_token, control_epoch=old_epoch, action="wait")
        with self.assertRaises(ValueError):
            await service.act(frame_id=new_token, control_epoch=old_epoch, action="wait")
        service._frames[new_token].created -= service.max_age + 1
        with self.assertRaisesRegex(ValueError, "过期"):
            await service.act(frame_id=new_token, control_epoch=new_epoch, action="wait")
        self.assertEqual(self.backend.inputs, [])

    async def test_one_input_per_frame_and_fresh_frame_allows_next(self):
        service = self.make_service(takeover=True)
        token, epoch = await self.frame(service)
        await service.act(frame_id=token, control_epoch=epoch, action="click", x=.25, y=.75)
        with self.assertRaises(ValueError):
            await service.act(frame_id=token, control_epoch=epoch, action="click", x=.25, y=.75)
        self.assertEqual(self.backend.inputs, [("click", 77, .25, .75)])
        token, epoch = await self.frame(service)
        await service.act(frame_id=token, control_epoch=epoch, action="key", key="ENTER")
        self.assertEqual(self.backend.inputs[-1], ("key", 77, "ENTER"))
        self.assertEqual(len(self.backend.inputs), 2)

    async def test_scene_change_blocks_click(self):
        service = self.make_service(takeover=True)
        token, epoch = await self.frame(service)
        self.backend.image = Image.new("RGB", (640, 400), (255, 255, 255))
        with self.assertRaisesRegex(ValueError, "明显变化"):
            await service.act(frame_id=token, control_epoch=epoch, action="click", x=.5, y=.5)
        self.assertEqual(self.backend.inputs, [])

    async def test_stop_disables_and_invalidates_pending_action(self):
        service = self.make_service(takeover=True)
        token, epoch = await self.frame(service)
        task = await self.blocked_action(service, token, epoch)
        await service.stop()
        self.backend.capture_release.set()
        with self.assertRaises(ValueError):
            await task
        self.assertFalse(service.running)
        self.assertFalse(service.takeover)
        self.assertEqual(service.last_frame_id, "")
        self.assertEqual(self.backend.inputs, [])

    async def test_f8_in_action_disables_and_stops(self):
        service = self.make_service(takeover=True)
        token, epoch = await self.frame(service)
        self.backend.f8 = True
        with self.assertRaises(ValueError):
            await service.act(frame_id=token, control_epoch=epoch, action="click", x=.5, y=.5)
        self.assertFalse(service.running)
        self.assertFalse(service.takeover)
        self.assertEqual(self.backend.inputs, [])

    async def test_f8_during_capture_disables_and_stops(self):
        service = self.make_service(takeover=True)
        token, epoch = await self.frame(service)
        task = await self.blocked_action(service, token, epoch)
        self.backend.f8 = True
        self.backend.capture_release.set()
        with self.assertRaises(ValueError):
            await task
        self.assertFalse(service.running)
        self.assertFalse(service.takeover)
        self.assertEqual(self.backend.inputs, [])

    async def test_loop_polls_f8_without_waiting_for_an_observation(self):
        service = self.make_service(takeover=True)
        self.backend.f8 = True
        await service._loop()
        self.assertFalse(service.running)
        self.assertFalse(service.takeover)
        self.assertEqual(self.backend.capture_count, 0)

    async def test_report_retry_and_event_ids_are_idempotent(self):
        service = self.make_service()
        token, _ = await self.frame(service)
        args = dict(frame_id=token, ledger_revision=0, analysis="Observed loading",
                    events=[{"event_id": "load-1", "type": "reset", "live": 2, "blank": 2}])
        first = service.report(**args)
        before = service.ledger.snapshot()
        self.assertEqual(service.ledger_revision, 1)
        service.report(**args)
        self.assertEqual(service.ledger.snapshot(), before)
        self.assertEqual(service.ledger_revision, 1)
        token, _ = await self.frame(service)
        second = service.report(frame_id=token, ledger_revision=1,
                                events=[{"event_id": "load-1", "type": "reset", "live": 2, "blank": 2}])
        self.assertEqual(second["ledger"], first["ledger"])
        self.assertEqual(service.ledger_revision, 1)

    async def test_report_events_commit_atomically(self):
        service = self.make_service()
        token, _ = await self.frame(service)
        before = service.ledger.snapshot()
        with self.assertRaises(ValueError):
            service.report(frame_id=token, ledger_revision=0, analysis="Must not commit", events=[
                {"event_id": "load-1", "type": "reset", "live": 0, "blank": 2},
                {"event_id": "shot-1", "type": "consume", "kind": "live"},
            ])
        self.assertEqual(service.ledger.snapshot(), before)
        self.assertEqual(service.ledger_revision, 0)
        self.assertEqual(service.analysis, "")
        self.assertEqual(len(service._events), 0)
        self.assertNotIn(token, service._reports)
        service.report(frame_id=token, ledger_revision=0, events=[
            {"event_id": "load-1", "type": "reset", "live": 0, "blank": 2},
            {"event_id": "shot-1", "type": "consume", "kind": "blank"},
        ])
        self.assertEqual(service.ledger.snapshot()["total"], 1)

    async def test_manual_correction_invalidates_frame_and_old_revision(self):
        service = self.make_service(takeover=True)
        token, epoch = await self.frame(service)
        service.update_ledger("reset", 1, 3)
        self.assertEqual(service.ledger_revision, 1)
        self.assertEqual(service.last_frame_id, "")
        with self.assertRaises(ValueError):
            service.report(frame_id=token, ledger_revision=0, events=[])
        with self.assertRaises(ValueError):
            await service.act(frame_id=token, control_epoch=epoch, action="wait")
        new_token, _ = await self.frame(service)
        with self.assertRaisesRegex(ValueError, "弹药记录已更新"):
            service.report(frame_id=new_token, ledger_revision=0, events=[])
        self.assertEqual(service.ledger.snapshot()["total"], 4)

    async def test_rejected_push_never_produces_actionable_frame(self):
        service = self.make_service(takeover=True, receipt={"submitted": False})
        with self.assertRaisesRegex(RuntimeError, "推送被"):
            await service.observe()
        self.assertEqual(len(service._frames), 0)
        self.assertEqual(service.last_frame_id, "")
        self.assertEqual(self.backend.inputs, [])

    async def test_switching_singleplayer_multiplayer_clears_and_revokes_pending_input(self):
        service = self.make_service(takeover=True)
        for mode in ("multiplayer", "singleplayer"):
            service.update_ledger("reset", 2, 2)
            service.analysis = "Previous table analysis"
            service.recommendation = "Previous table move"
            service.strategy = {"target": "Previous player"}
            service.set_takeover(True)
            self.backend.capture_entered.clear()
            self.backend.capture_release.clear()
            self.backend.block_capture = False
            token, epoch = await self.frame(service)
            revision = service.ledger_revision
            task = await self.blocked_action(service, token, epoch)
            switched = service.set_game_mode(mode)
            self.backend.capture_release.set()
            with self.assertRaises(ValueError):
                await task
            self.assertEqual(switched["game_mode"], mode)
            self.assertFalse(switched["takeover"])
            self.assertTrue(switched["running"])
            self.assertFalse(switched["ledger"]["initialized"])
            self.assertEqual(switched["ledger_revision"], revision + 1)
            self.assertEqual(switched["last_frame_id"], "")
            self.assertGreater(switched["control_epoch"], epoch)
            self.assertEqual(switched["analysis"], "")
            self.assertEqual(switched["recommendation"], "")
            self.assertEqual(switched["strategy"], {})
            self.assertEqual(self.backend.inputs, [])

    async def test_invalid_game_mode_does_not_mutate_active_session(self):
        service = self.make_service(takeover=True)
        service.update_ledger("reset", 1, 2)
        await self.frame(service)
        before = copy.deepcopy(service.snapshot())
        for invalid in ("single", "online", "", None, True, {}, []):
            with self.assertRaises(ValueError):
                service.set_game_mode(invalid)
            self.assertEqual(service.snapshot(), before)
        # Reapplying the selected mode is idempotent and does not revoke a frame.
        self.assertEqual(service.set_game_mode("singleplayer"), before)
        self.assertEqual(self.backend.inputs, [])


if __name__ == "__main__":
    unittest.main()
