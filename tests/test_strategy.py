"""Tactical outcomes and item-order rules, independent of screenshot recognition."""
import importlib.util
import sys
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("buckshot_strategy_under_test", Path(__file__).parents[1] / "buckshot" / "strategy.py")
strategy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = strategy
spec.loader.exec_module(strategy)


def sp(**updates):
    state = dict(mode="singleplayer", player_health=2, opponent_health=2, max_health=4,
                 player_items=[], opponent_items=[], live=1, blank=1)
    state.update(updates)
    return state


def mp(count=3, **updates):
    state = dict(mode="multiplayer", player_id="a", turn_id="a", direction=1, live=2, blank=1,
                 players=[dict(id=letter, name=letter, health=3, max_health=4, items=[]) for letter in "abcd"[:count]])
    state.update(updates)
    return state


class TacticalTests(unittest.TestCase):
    def solve(self, state):
        return strategy.recommend(state, max_depth=7, max_nodes=25000, time_budget_ms=1500)

    def test_lethal_shot_spares_unnecessary_saw(self):
        result = self.solve(sp(opponent_health=1, player_items=["hand_saw"], live=1, blank=0))
        self.assertEqual(result["action"], {"type": "shoot", "target": "opponent"})

    def test_saw_then_shot_is_lethal(self):
        result = self.solve(sp(player_items=["hand_saw"], live=1, blank=0))
        self.assertEqual(result["action"]["item"], "hand_saw")
        self.assertEqual(result["ranked_actions"][0]["utility"], 1)
        self.assertEqual(result["candidate_combination"][-1], "向庄家开枪")

    def test_information_turns_half_chance_into_certain_current_magazine_win(self):
        result = self.solve(sp(player_health=1, opponent_health=1, max_health=1, player_items=["magnifying_glass"]))
        self.assertEqual(result["action"]["item"], "magnifying_glass")
        self.assertEqual(result["ranked_actions"][0]["utility"], 1)

    def test_cigarette_prevents_immediate_death(self):
        result = self.solve(sp(player_health=1, player_items=["cigarette_pack"], live=2, blank=0))
        self.assertEqual(result["action"]["item"], "cigarette_pack")

    def test_cuffs_saw_combination(self):
        result = self.solve(sp(player_health=1, opponent_health=3, player_items=["hand_saw", "handcuffs"], live=3, blank=0))
        self.assertEqual(result["ranked_actions"][0]["utility"], 1)
        plan = " ".join(result["candidate_combination"])
        self.assertIn("手铐", plan)
        self.assertIn("手锯", plan)

    def test_no_unseen_or_other_mode_items(self):
        result = self.solve(sp())
        self.assertTrue(all(row["action"]["type"] == "shoot" for row in result["ranked_actions"]))
        self.assertEqual(self.solve(sp(player_items=["adrenaline"]))["status"], "needs_information")
        state = mp(2)
        state["players"][0]["items"] = ["handcuffs"]
        self.assertEqual(self.solve(state)["status"], "needs_information")

    def test_explanatory_plan_stops_before_opponent_turn(self):
        result = self.solve(sp(live=3, blank=0, player_health=4, opponent_health=4))
        attack = next(row for row in result["ranked_actions"] if row["action"] == {"type": "shoot", "target": "opponent"})
        self.assertEqual(attack["candidate_combination"], ["向庄家开枪"])

    def test_plan_stops_at_unknown_observation(self):
        result = self.solve(sp(player_health=1, opponent_health=1, max_health=1, player_items=["magnifying_glass"]))
        self.assertEqual(result["candidate_combination"], ["使用放大镜"])

    def test_dead_player_is_not_an_adrenaline_target(self):
        state = mp()
        state["players"][0]["items"] = ["adrenaline"]
        state["players"][2].update(health=0, items=["hand_saw"])
        planner = strategy._Planner(state)
        self.assertFalse(any(action.kind == "steal" and action.donor == 2 for action in planner.actions(planner.start)))

    def test_adrenaline_immediately_uses_stolen_saw(self):
        state = mp(2, live=1, blank=0)
        state["players"][0]["items"] = ["adrenaline"]
        state["players"][1].update(health=2, items=["hand_saw"])
        result = self.solve(state)
        self.assertEqual(result["action"], {"type": "use_item", "item": "adrenaline", "steal_from": "b", "stolen_item": "hand_saw"})
        self.assertEqual(result["ranked_actions"][0]["utility"], 1)

    def test_jammer_skips_next_turn_not_shot_target(self):
        state = mp(live=3, blank=0)
        state["players"][0]["items"] = ["jammer"]
        planner = strategy._Planner(state)
        jam = next(a for a in planner.actions(planner.start) if a.kind == "use" and a.target == 1)
        after_jam = list(planner.transitions(planner.start, jam))[0][1]
        shot = strategy._Action("shoot", target=2)
        after_shot = list(planner.transitions(after_jam, shot))[0][1]
        self.assertEqual(after_shot.turn, 2)
        self.assertEqual(after_shot.health[2], 2)
        self.assertFalse(after_shot.skipped[1])

    def test_remote_reverses_direction_without_passing(self):
        state = mp(live=3, blank=0)
        state["players"][0]["items"] = ["remote"]
        planner = strategy._Planner(state)
        remote = next(a for a in planner.actions(planner.start) if a.kind == "use")
        after_remote = list(planner.transitions(planner.start, remote))[0][1]
        self.assertEqual(after_remote.turn, 0)
        self.assertEqual(after_remote.direction, -1)
        self.assertEqual(list(planner.transitions(after_remote, strategy._Action("shoot", target=1)))[0][1].turn, 2)

    def test_phone_mp_not_useful_with_only_two_shells(self):
        state = mp(live=1, blank=1)
        state["players"][0]["items"] = ["burner_phone"]
        planner = strategy._Planner(state)
        self.assertFalse(any(a.kind == "use" for a in planner.actions(planner.start)))
        state["blank"] = 2
        planner = strategy._Planner(state)
        phone = next(a for a in planner.actions(planner.start) if a.kind == "use")
        outcomes = list(planner.transitions(planner.start, phone))
        self.assertEqual(len(outcomes), 2)
        self.assertAlmostEqual(sum(prob for prob, _ in outcomes), 1)
        self.assertTrue(all(len({seq[2] for seq, _ in position.gun}) == 1 for _, position in outcomes))

    def test_beer_resets_saw_but_preserves_turn(self):
        planner = strategy._Planner(sp(player_items=["beer"], saw_active=True))
        beer = next(a for a in planner.actions(planner.start) if a.kind == "use")
        for probability, after in planner.transitions(planner.start, beer):
            self.assertFalse(after.saw)
            self.assertEqual(after.turn, 0)
            self.assertEqual(len(after.gun[0][0]), 1)

    def test_inverter_changes_current_type_and_counts(self):
        state = mp(2, live=1, blank=2, known_shell="blank")
        state["players"][0]["items"] = ["inverter"]
        planner = strategy._Planner(state)
        invert = next(a for a in planner.actions(planner.start) if a.kind == "use")
        after = list(planner.transitions(planner.start, invert))[0][1]
        self.assertTrue(all(seq[0] == 1 and sum(seq) == 2 for seq, _ in after.gun))

    def test_unknowns_and_turn_identity_are_gated(self):
        self.assertEqual(self.solve(sp(opponent_items=None))["status"], "needs_information")
        self.assertEqual(self.solve(mp(turn_id=None))["status"], "needs_information")
        missing_direction = mp()
        del missing_direction["direction"]
        self.assertEqual(self.solve(missing_direction)["status"], "needs_information")
        self.assertEqual(self.solve(mp(turn_id="b"))["status"], "wait")
        state = mp()
        state["players"][1]["health"] = None
        self.assertEqual(self.solve(state)["status"], "needs_information")

    def test_ledger_mixture_and_future_shell_information(self):
        state = sp(ledger={"states": [dict(live=1, blank=1, next="live", probability=.25),
                                           dict(live=0, blank=2, next="blank", probability=.75)]})
        planner = strategy._Planner(state)
        self.assertAlmostEqual(sum(seq[0] * prob for seq, prob in planner.start.gun), .25)
        planner = strategy._Planner(sp(live=1, blank=2, future_shells={"2": "live"}))
        self.assertTrue(all(seq == (0, 1, 0) for seq, _ in planner.start.gun))

    def test_healing_disabled_and_full_health_do_not_waste_cigarettes(self):
        for state in (sp(player_health=1, healing_allowed=False, player_items=["cigarette_pack"]),
                      sp(player_health=4, player_items=["cigarette_pack"])):
            planner = strategy._Planner(state)
            self.assertTrue(all(a.kind == "shoot" for a in planner.actions(planner.start)))

    def test_budget_is_reported_without_winrate_claim(self):
        state = mp(4, live=4, blank=4)
        for player in state["players"]:
            player["items"] = ["magnifying_glass", "adrenaline", "inverter", "hand_saw", "jammer", "remote"]
        result = strategy.recommend(state, max_nodes=128, time_budget_ms=50)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["search"]["approximate"])
        self.assertTrue(result["search"]["budget_exhausted"])
        self.assertNotIn("win_probability", result)


if __name__ == "__main__":
    unittest.main()
