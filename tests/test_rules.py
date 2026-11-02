"""unittest tests for the rules in rules.py (steps 1-5).

The numbers come from data/game.json, the same file the game uses.
"""

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rules import Game, load_config

CONFIG = load_config()
DT = CONFIG["dt"]


class RulesTest(unittest.TestCase):
    def setUp(self):
        # hero at the origin with no target and an empty board
        self.game = Game(CONFIG)
        self.game.clear_target()
        self.game.hero["x"] = 0.0
        self.game.hero["y"] = 0.0
        self.game.enemies = []

    def no_new_spawns(self):
        # move the 1 s spawn timer away so only my own bats are on the board
        self.game.spawn_accumulator_s = -1000.0

    def no_weapons(self):
        # stop the Spark Wand so it cannot kill bats (and drop XP) during a test
        self.game.weapons = {}

    def step(self, seconds):
        for _ in range(round(seconds / DT)):
            self.game.step(DT)

    def add_bat(self, x, y, frozen=False, hp=None):
        bat = self.game._make_enemy("bat")
        bat["x"] = x
        bat["y"] = y
        if frozen:
            bat["speed"] = 0.0  # stay in place so the test is deterministic
        if hp is not None:
            bat["hp"] = hp
        self.game.enemies.append(bat)
        return bat

    # ---- step 1: core ----------------------------------------------------
    def test_starts_empty_with_full_hp(self):
        fresh = Game(CONFIG)
        self.assertEqual(len(fresh.enemies), 0)
        self.assertEqual(fresh.hero["hp"], CONFIG["hero"]["hp"])
        self.assertFalse(fresh.game_over)

    def test_spawning_reaches_the_minimum_rate_limited(self):
        self.no_weapons()
        sp = CONFIG["spawning"]
        minimum = sp["segments"][0]["min_alive"]
        self.step(1.0)
        self.assertEqual(len(self.game.enemies), sp["max_per_tick"])
        self.step(1.0)
        self.step(1.0)
        self.assertEqual(len(self.game.enemies), minimum)

    def test_bats_spawn_on_the_circle_around_the_hero(self):
        self.game._spawn_tick(CONFIG["spawning"])
        self.assertGreater(len(self.game.enemies), 0)
        for e in self.game.enemies:
            d = math.hypot(e["x"] - self.game.hero["x"], e["y"] - self.game.hero["y"])
            self.assertAlmostEqual(d, CONFIG["spawning"]["spawn_radius"], delta=1e-6)

    def test_hero_walks_toward_the_target(self):
        self.game.set_target(1000.0, 0.0)
        self.step(1.0)
        self.assertAlmostEqual(self.game.hero["x"], CONFIG["hero"]["speed"] * 1.0, delta=0.1)

    def test_hero_stops_inside_the_move_deadzone(self):
        deadzone = CONFIG["hero"]["move_deadzone"]
        self.game.set_target(deadzone / 2.0, 0.0)
        self.step(1.0)
        self.assertAlmostEqual(self.game.hero["x"], 0.0, delta=1e-9)

    def test_contact_damage_once_per_half_second(self):
        self.no_new_spawns()
        bat = self.add_bat(5.0, 0.0, hp=10000)  # survive the wand
        hp = CONFIG["hero"]["hp"]
        self.step(0.1)
        self.assertEqual(self.game.hero["hp"], hp - bat["damage"])
        self.step(0.4)  # still inside the 0.5 s cooldown
        self.assertEqual(self.game.hero["hp"], hp - bat["damage"])
        self.step(0.1)  # past 0.5 s since the last hit
        self.assertEqual(self.game.hero["hp"], hp - 2 * bat["damage"])

    def test_far_enemy_is_removed(self):
        self.no_new_spawns()
        bat = self.add_bat(CONFIG["spawning"]["despawn_radius"] + 50.0, 0.0, frozen=True)
        self.step(0.1)
        self.assertNotIn(bat, self.game.enemies)

    def test_game_over_when_hp_reaches_zero(self):
        self.no_new_spawns()
        self.no_weapons()
        self.game.hero["hp"] = 3
        self.add_bat(5.0, 0.0)
        self.step(0.1)
        self.assertTrue(self.game.game_over)
        self.assertEqual(self.game.hero["hp"], 0)

    def test_nothing_moves_after_game_over(self):
        self.no_new_spawns()
        self.no_weapons()
        self.game.hero["hp"] = 1
        bat = self.add_bat(5.0, 0.0)  # touching: one hit ends the game
        self.step(0.1)
        self.assertTrue(self.game.game_over)
        snapshot = [(bat["x"], bat["y"])]
        self.step(1.0)
        self.assertEqual([(bat["x"], bat["y"])], snapshot)

    def test_restart_resets_everything(self):
        self.game.set_target(500.0, 500.0)
        self.step(3.0)
        self.game.restart()
        self.assertFalse(self.game.game_over)
        self.assertEqual(self.game.time_s, 0.0)
        self.assertEqual(self.game.hero["hp"], CONFIG["hero"]["hp"])
        self.assertEqual(self.game.level, 1)
        self.assertEqual(self.game.weapons, {"spark_wand": 1})
        self.assertEqual(len(self.game.enemies), 0)
        self.assertEqual(len(self.game.gems), 0)

    # ---- step 3: gems and XP ------------------------------------------
    def test_spark_wand_shoots_at_the_nearest_enemy(self):
        self.no_new_spawns()
        near = self.add_bat(60.0, 0.0, frozen=True)   # hp 8, dies to one spark
        far = self.add_bat(300.0, 0.0, frozen=True)   # too far for this window
        wand = CONFIG["weapons"]["spark_wand"]
        self.step(wand["interval"] + 0.3)
        self.assertEqual(len(self.game.enemies), 1)
        self.assertIn(far, self.game.enemies)
        self.assertNotIn(near, self.game.enemies)

    def test_dead_enemy_drops_a_gem_that_gives_xp(self):
        self.no_new_spawns()
        self.add_bat(60.0, 0.0, frozen=True)  # hp 8 -> one spark kills it
        self.step(1.2)                         # spark hits, gem drops and flies in
        self.assertEqual(self.game.xp, 1)      # bat is worth 1 XP
        self.assertEqual(self.game.level, 1)   # not enough for a level-up yet

    def test_gem_flies_to_hero_when_inside_magnet(self):
        self.no_new_spawns()
        self.no_weapons()
        self.game.gems = [{"x": 40.0, "y": 0.0, "xp": 1}]  # inside the 60 px magnet
        self.step(0.05)
        self.assertEqual(len(self.game.gems), 1)
        self.assertLess(self.game.gems[0]["x"], 40.0)      # moved toward the hero
        self.assertGreater(self.game.gems[0]["x"], CONFIG["hero"]["radius"])  # not picked up yet

    def test_gem_is_collected_when_it_reaches_the_hero(self):
        self.no_new_spawns()
        self.no_weapons()
        self.game.gems = [{"x": 5.0, "y": 0.0, "xp": 2}]  # already within pickup range
        self.step(0.02)
        self.assertEqual(len(self.game.gems), 0)
        self.assertEqual(self.game.xp, 2)

    # ---- step 3: XP curve and level-up ----------------------------------
    def test_xp_needed_grows_by_three_each_level(self):
        self.assertEqual(self.game.xp_to_next(), 5)
        self.game.level = 2
        self.assertEqual(self.game.xp_to_next(), 8)
        self.game.level = 3
        self.assertEqual(self.game.xp_to_next(), 11)

    def test_level_up_pauses_and_shows_three_cards(self):
        self.no_new_spawns()
        self.no_weapons()
        self.game.add_xp(self.game.xp_to_next())
        self.game.step(DT)
        self.assertTrue(self.game.level_up_open)
        self.assertEqual(self.game.level, 2)
        self.assertEqual(len(self.game.cards), 3)

    def test_choosing_a_card_closes_the_level_up(self):
        self.game.level_up_open = True
        self.game.pending_level_ups = 1
        self.game.cards = self.game._generate_cards()
        self.game.choose_card(0)
        self.assertFalse(self.game.level_up_open)

    def test_new_weapon_card_adds_that_weapon(self):
        self.game.level_up_open = True
        self.game.pending_level_ups = 1
        self.game.cards = [{"kind": "weapon_new", "key": "spin_blades", "level": 1,
                            "name": "Spin Blades", "text": "Blades circle around you"}]
        self.game.choose_card(0)
        self.assertEqual(self.game.weapons.get("spin_blades"), 1)

    def test_big_heart_card_raises_max_hp_and_heals(self):
        self.game.hero["hp"] = 50
        self.game.level_up_open = True
        self.game.pending_level_ups = 1
        self.game.cards = [{"kind": "passive_new", "key": "big_heart", "level": 1,
                            "name": "Big Heart", "text": "+20 max HP and heal 20"}]
        self.game.choose_card(0)
        self.assertEqual(self.game.hero["max_hp"], CONFIG["hero"]["hp"] + 20)
        self.assertEqual(self.game.hero["hp"], 70)  # 50 + 20 heal

    def test_heal_card_restores_hp(self):
        self.game.hero["hp"] = 40
        self.game.level_up_open = True
        self.game.pending_level_ups = 1
        self.game.cards = [{"kind": "heal", "key": "heal", "level": 0,
                            "name": "Heal", "text": "Restore 30 HP"}]
        self.game.choose_card(0)
        self.assertEqual(self.game.hero["hp"], 70)

    def test_cards_never_offer_a_maxed_item(self):
        self.game.weapons = {"spark_wand": 5}
        for card in self.game._generate_cards():
            self.assertNotEqual(card["key"], "spark_wand")

    def test_cards_offer_heal_when_nothing_is_left(self):
        self.game.weapons = {k: 5 for k in CONFIG["weapons"]}
        self.game.passives = {k: 5 for k in CONFIG["passives"]}
        cards = self.game._generate_cards()
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["kind"], "heal")

    # ---- step 3: weapon scaling -----------------------------------------
    def test_spark_wand_fires_extra_sparks_at_level_3(self):
        self.no_new_spawns()
        self.game.weapons = {"spark_wand": 3}
        self.add_bat(200.0, 0.0, frozen=True, hp=10000)
        self.game.step(DT)  # the wand fires on the first step
        self.assertEqual(len(self.game.sparks), 2)

    def test_spin_blades_hit_a_nearby_enemy(self):
        self.no_new_spawns()
        self.game.weapons = {"spin_blades": 1}
        bat = self.add_bat(60.0, 0.0, frozen=True, hp=1000)  # right where a blade starts
        self.step(0.05)
        blades = CONFIG["weapons"]["spin_blades"]
        self.assertEqual(bat["hp"], 1000 - blades["damage"])

    def test_holy_ring_hurts_enemies_in_its_radius(self):
        self.no_new_spawns()
        self.game.weapons = {"holy_ring": 1}
        bat = self.add_bat(40.0, 0.0, frozen=True, hp=1000)  # inside the 50 px ring
        self.step(0.6)  # two pulses (at 0 s and 0.5 s)
        ring = CONFIG["weapons"]["holy_ring"]
        self.assertEqual(bat["hp"], 1000 - 2 * ring["damage"])

    # ---- step 3: passive items ------------------------------------------
    def test_swift_boots_increase_walk_speed(self):
        self.game.passives["swift_boots"] = 2
        expected = CONFIG["hero"]["speed"] * (1 + 2 * 0.10)
        self.assertAlmostEqual(self.game.hero_speed(), expected)

    def test_magnet_widens_the_pickup_range(self):
        self.game.passives["magnet"] = 1
        self.assertEqual(self.game.magnet_radius(), CONFIG["hero"]["magnet_radius"] + 30)

    # ---- step 4: waves, boss and victory --------------------------------
    def test_victory_at_two_minutes(self):
        self.game.time_s = CONFIG["win_time"] - DT
        self.game.step(DT)
        self.assertTrue(self.game.won)
        self.assertFalse(self.game.game_over)

    def test_spawn_segment_changes_with_time(self):
        segs = CONFIG["spawning"]["segments"]
        self.game.time_s = 0.0
        self.assertEqual(self.game._current_segment(), segs[0])
        self.game.time_s = 46.0  # the 0:45 row: Slime + Ghost, min 36
        self.assertEqual(self.game._current_segment(), segs[3])
        self.game.time_s = 119.0  # the last row (1:45): Bat, min 75
        self.assertEqual(self.game._current_segment(), segs[-1])

    def test_ghost_is_faster_than_the_hero(self):
        self.assertGreater(CONFIG["enemies"]["ghost"]["speed"], CONFIG["hero"]["speed"])

    def test_boss_spawns_exactly_once(self):
        self.no_new_spawns()
        self.game.time_s = CONFIG["boss"]["from_s"]
        self.game._spawn_boss_if_due()
        self.game._spawn_boss_if_due()  # a second call must not add another
        bosses = [e for e in self.game.enemies if e["boss"]]
        self.assertEqual(len(bosses), 1)
        self.assertEqual(bosses[0]["type"], CONFIG["boss"]["type"])

    def test_boss_is_never_despawned_but_recalled(self):
        boss = self.game._make_enemy(CONFIG["boss"]["type"])
        boss["x"] = CONFIG["boss"]["recall_radius"] + 200.0  # far away
        boss["y"] = 0.0
        self.game.enemies = [boss]
        self.game._despawn_far_enemies()
        self.assertEqual(len(self.game.enemies), 1)  # still there
        d = math.hypot(boss["x"], boss["y"])
        self.assertAlmostEqual(d, CONFIG["spawning"]["spawn_radius"], delta=1e-6)

    def test_kills_are_counted(self):
        self.no_new_spawns()
        bat = self.add_bat(50.0, 0.0)
        bat["hp"] = 0  # already dead
        self.game._reap_dead_enemies()
        self.assertEqual(self.game.kills, 1)
        self.assertEqual(len(self.game.enemies), 0)

    # ---- step 5: feel (damage numbers, flashes, shake) and events --------
    def test_hit_creates_a_damage_number(self):
        bat = self.add_bat(50.0, 0.0, hp=1000)
        self.game._hit(bat, 12)
        self.assertEqual(bat["hp"], 988)
        self.assertEqual(self.game.events["hit"], 1)
        self.assertEqual(len(self.game.floaters), 1)
        self.assertEqual(self.game.floaters[0]["value"], 12)

    def test_enemy_death_creates_a_flash(self):
        self.no_new_spawns()
        bat = self.add_bat(50.0, 0.0)
        bat["hp"] = 0
        self.game._reap_dead_enemies()
        self.assertEqual(self.game.events["death"], 1)
        self.assertEqual(len(self.game.flashes), 1)
        self.assertAlmostEqual(self.game.flashes[0]["x"], 50.0)

    def test_hero_hurt_shakes_the_screen(self):
        self.no_new_spawns()
        self.no_weapons()
        self.add_bat(5.0, 0.0, hp=10000)  # touching the hero
        self.game.step(DT)  # one step, so events still show this hit
        self.assertGreater(self.game.shake_s, 0.0)
        self.assertGreaterEqual(self.game.events["hurt"], 1)

    def test_shake_and_floaters_fade_away(self):
        self.no_new_spawns()
        self.no_weapons()
        self.game.shake_s = DT
        self.game.floaters = [{"x": 0.0, "y": 0.0, "value": 5,
                               "life_s": DT, "max_life": DT}]
        self.game.step(DT)  # one step ages both to zero
        self.assertEqual(self.game.shake_s, 0.0)
        self.assertEqual(len(self.game.floaters), 0)

    def test_gem_collect_counts_an_event(self):
        self.no_new_spawns()
        self.no_weapons()
        self.game.gems = [{"x": 5.0, "y": 0.0, "xp": 1}]
        self.step(0.02)
        self.assertEqual(self.game.events["gem"], 1)

    def test_level_up_counts_an_event(self):
        self.no_new_spawns()
        self.no_weapons()
        self.game.add_xp(self.game.xp_to_next())
        self.game.step(DT)
        self.assertTrue(self.game.level_up_open)
        self.assertEqual(self.game.events["levelup"], 1)

    def test_events_reset_every_step(self):
        self.no_new_spawns()
        self.no_weapons()
        self.game.enemies = []
        self.game.gems = []
        self.game.step(DT)
        self.assertEqual(self.game.events["hit"], 0)
        self.assertEqual(self.game.events["gem"], 0)
        self.assertIsNone(self.game.events["end"])

    def test_end_event_on_win(self):
        self.game.time_s = CONFIG["win_time"] - DT
        self.game.step(DT)
        self.assertEqual(self.game.events["end"], "win")


if __name__ == "__main__":
    unittest.main()
