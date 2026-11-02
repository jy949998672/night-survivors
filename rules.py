"""The game rules of Night Survivors (steps 1-5).

This file knows nothing about pygame, pictures or sound. It only moves
everything by one fixed time step and applies the rules from GAME.md.
All numbers come from data/game.json, so tests and main.py use the same data.
Step 5 adds "feel" state (damage numbers, death flashes, a hurt shake) and a
per-step events dict; this file still makes no sound and draws nothing, it only
records what happened so main.py can show and play it.
"""

import json
import math
import random
from pathlib import Path

DT = 1.0 / 60.0
SPARK_LIFETIME_S = 2.0
TAU = 2.0 * math.pi


def load_config():
    data_file = Path(__file__).resolve().parent / "data" / "game.json"
    with open(data_file, encoding="utf-8") as f:
        return json.load(f)


def _dist(a, b):
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


class Game:
    def __init__(self, config, rng=None):
        self.cfg = config
        self.rng = rng if rng is not None else random.Random()
        self.restart()

    def restart(self):
        hero_cfg = self.cfg["hero"]
        self.time_s = 0.0
        self.hero = {
            "x": 0.0,
            "y": 0.0,
            "hp": hero_cfg["hp"],
            "max_hp": hero_cfg["hp"],
        }
        self.target = None  # where the hero walks to, in world coordinates
        self.enemies = []
        self.sparks = []
        self.gems = []  # dropped by dead enemies: {"x", "y", "xp"}

        # growth: level, xp and the items you own (name -> level)
        self.level = 1
        self.xp = 0
        self.weapons = {"spark_wand": 1}  # you start with the Spark Wand
        self.passives = {}
        self.pending_level_ups = 0
        self.level_up_open = False
        self.cards = []

        # weapon timers / animation state
        self.wand_cooldown_s = 0.0
        self.blade_angle = 0.0
        self.blade_positions = []
        self.ring_cooldown_s = 0.0
        self.ring_radius = 0.0

        self.spawn_accumulator_s = 0.0
        self.game_over = False

        # waves: score for the end screen, and the once-only boss
        self.kills = 0
        self.won = False
        self.boss_spawned = False

        # feel (step 5): damage numbers, death flashes, a shake when hurt, and
        # the events that happened during the last step (for main.py's sounds)
        self.floaters = []  # {"x", "y", "value", "life_s", "max_life"}
        self.flashes = []   # {"x", "y", "r0", "life_s", "max_life"}
        self.shake_s = 0.0
        self.events = self._new_events()

    @staticmethod
    def _new_events():
        return {"hit": 0, "gem": 0, "hurt": 0, "levelup": 0, "death": 0, "end": None}

    # ---- public controls -------------------------------------------------
    def set_target(self, x, y):
        self.target = (float(x), float(y))

    def clear_target(self):
        self.target = None

    def choose_card(self, index):
        """Apply a level-up card the player picked, then continue or show more."""
        if not self.level_up_open or not (0 <= index < len(self.cards)):
            return
        card = self.cards[index]
        kind = card["kind"]
        key = card["key"]
        if kind == "weapon_new":
            self.weapons[key] = 1
        elif kind == "weapon_up":
            self.weapons[key] += 1
        elif kind in ("passive_new", "passive_up"):
            self.passives[key] = self.passives.get(key, 0) + 1
            if key == "big_heart":
                pc = self.cfg["passives"]["big_heart"]
                self.hero["max_hp"] += pc["max_hp_per_level"]
                self._heal(pc["heal_per_level"])
        elif kind == "heal":
            self._heal(self.cfg["level_up"]["heal_amount"])

        self.pending_level_ups -= 1
        if self.pending_level_ups > 0:
            self.cards = self._generate_cards()
        else:
            self.cards = []
            self.level_up_open = False

    def add_xp(self, amount):
        self.xp += amount
        while self.xp >= self.xp_to_next():
            self.xp -= self.xp_to_next()
            self.level += 1
            self.pending_level_ups += 1

    # ---- derived stats (passives change these) ---------------------------
    def xp_to_next(self):
        xp = self.cfg["xp"]
        return xp["first_level"] + xp["per_level"] * (self.level - 1)

    def hero_speed(self):
        base = self.cfg["hero"]["speed"]
        lvl = self.passives.get("swift_boots", 0)
        return base * (1.0 + self.cfg["passives"]["swift_boots"]["speed_bonus_per_level"] * lvl)

    def magnet_radius(self):
        base = self.cfg["hero"]["magnet_radius"]
        lvl = self.passives.get("magnet", 0)
        return base + self.cfg["passives"]["magnet"]["magnet_per_level"] * lvl

    def _heal(self, amount):
        self.hero["hp"] = min(self.hero["max_hp"], self.hero["hp"] + amount)

    # ---- main step -------------------------------------------------------
    def step(self, dt=DT):
        self.events = self._new_events()
        self._decay_effects(dt)
        if self.game_over or self.won or self.level_up_open:
            return
        self.time_s += dt
        if self.time_s >= self.cfg["win_time"]:
            self.time_s = self.cfg["win_time"]
            self.won = True
            self.events["end"] = "win"
            return
        self._move_hero(dt)
        self._spawn_enemies(dt)
        self._update_enemies(dt)
        self._update_weapons(dt)
        self._update_sparks(dt)
        self._reap_dead_enemies()
        self._update_gems(dt)
        self._despawn_far_enemies()
        if self.hero["hp"] <= 0:
            self.hero["hp"] = 0
            self.game_over = True
            self.events["end"] = "lose"
            return
        self._maybe_open_level_up()

    def _decay_effects(self, dt):
        """Age the shake, damage numbers and death flashes by one step."""
        feel = self.cfg["feel"]
        self.shake_s = max(0.0, self.shake_s - dt)
        rise = feel["floater_rise"]
        for fl in self.floaters:
            fl["life_s"] -= dt
            fl["y"] -= rise * dt
        self.floaters = [fl for fl in self.floaters if fl["life_s"] > 0.0]
        for fa in self.flashes:
            fa["life_s"] -= dt
        self.flashes = [fa for fa in self.flashes if fa["life_s"] > 0.0]

    def _hit(self, e, amount):
        """Deal amount damage to an enemy and record a floating damage number."""
        e["hp"] -= amount
        self.events["hit"] += 1
        feel = self.cfg["feel"]
        self.floaters.append({
            "x": e["x"],
            "y": e["y"],
            "value": int(round(amount)),
            "life_s": feel["floater_life_s"],
            "max_life": feel["floater_life_s"],
        })
        if len(self.floaters) > feel["max_floaters"]:
            del self.floaters[: len(self.floaters) - feel["max_floaters"]]

    def _move_hero(self, dt):
        if self.target is None:
            return
        hero = self.hero
        dx = self.target[0] - hero["x"]
        dy = self.target[1] - hero["y"]
        d = math.hypot(dx, dy)
        if d <= self.cfg["hero"]["move_deadzone"]:
            return
        step_len = min(self.hero_speed() * dt, d)
        hero["x"] += dx / d * step_len
        hero["y"] += dy / d * step_len

    # ---- spawning --------------------------------------------------------
    def _current_segment(self):
        """The spawn-table row that applies at the current time (the last one
        whose start time we have reached)."""
        seg = self.cfg["spawning"]["segments"][0]
        for s in self.cfg["spawning"]["segments"]:
            if self.time_s >= s["from_s"]:
                seg = s
        return seg

    def _spawn_enemies(self, dt):
        sp = self.cfg["spawning"]
        self.spawn_accumulator_s += dt
        spawned_any = False
        while self.spawn_accumulator_s >= sp["interval"]:
            self.spawn_accumulator_s -= sp["interval"]
            self._spawn_tick(sp, allow=not spawned_any)
            spawned_any = True
        self._spawn_boss_if_due()

    def _spawn_tick(self, sp, allow=True):
        if allow and len(self.enemies) >= sp["max_alive"]:
            return
        seg = self._current_segment()
        to_spawn = seg["min_alive"] - len(self.enemies)
        to_spawn = max(0, min(to_spawn, sp["max_per_tick"]))
        for _ in range(to_spawn):
            etype = self.rng.choice(seg["types"])
            self.enemies.append(self._make_enemy(etype))

    def _spawn_boss_if_due(self):
        """The boss appears exactly once, at its time in the table."""
        if self.boss_spawned or self.time_s < self.cfg["boss"]["from_s"]:
            return
        self.boss_spawned = True
        self.enemies.append(self._make_enemy(self.cfg["boss"]["type"]))

    def _make_enemy(self, etype):
        cfg = dict(self.cfg["enemies"][etype])
        angle = self.rng.uniform(0.0, TAU)
        r = self.cfg["spawning"]["spawn_radius"]
        return {
            "type": etype,
            "x": self.hero["x"] + r * math.cos(angle),
            "y": self.hero["y"] + r * math.sin(angle),
            "hp": cfg["hp"],
            "speed": cfg["speed"],
            "damage": cfg["damage"],
            "xp": cfg["xp"],
            "radius": cfg["radius"],
            "boss": cfg.get("boss", False),
            "draw_scale": cfg.get("draw_scale", 1),
            "hurt_cooldown_s": 0.0,
            "blade_cd": {},  # blade index -> seconds until it can hit again
        }

    def _update_enemies(self, dt):
        hero = self.hero
        hero_cfg = self.cfg["hero"]
        contact_s = self.cfg["contact"]["interval"]
        for e in self.enemies:
            e["hurt_cooldown_s"] = max(0.0, e["hurt_cooldown_s"] - dt)
            for k in list(e["blade_cd"]):
                e["blade_cd"][k] = max(0.0, e["blade_cd"][k] - dt)
            dx = hero["x"] - e["x"]
            dy = hero["y"] - e["y"]
            d = math.hypot(dx, dy)
            if d > 1e-9:
                step_len = min(e["speed"] * dt, d)
                e["x"] += dx / d * step_len
                e["y"] += dy / d * step_len
            touching = d <= hero_cfg["radius"] + e["radius"]
            if touching and e["hurt_cooldown_s"] <= 0.0:
                hero["hp"] -= e["damage"]
                e["hurt_cooldown_s"] = contact_s
                self.shake_s = self.cfg["feel"]["shake_s"]
                self.events["hurt"] += 1

    # ---- weapons ---------------------------------------------------------
    def _wbase(self, key, field, level):
        """A weapon stat at a given level: base + per_level * (level-1)."""
        cfg = self.cfg["weapons"][key]
        return cfg[field] + cfg.get(field + "_per_level", 0) * (level - 1)

    def _bonus(self, key, field, level):
        """How many bonus grants (extra sparks / blades) apply at this level."""
        cfg = self.cfg["weapons"][key]
        return sum(1 for lv in cfg.get(field, []) if level >= lv)

    def _update_weapons(self, dt):
        self._update_spark_wand(dt)
        self._update_spin_blades(dt)
        self._update_holy_ring(dt)

    def _update_spark_wand(self, dt):
        level = self.weapons.get("spark_wand")
        if level is None:
            return
        cfg = self.cfg["weapons"]["spark_wand"]
        damage = self._wbase("spark_wand", "damage", level)
        interval = self._wbase("spark_wand", "interval", level)
        n_sparks = cfg["sparks"] + self._bonus("spark_wand", "sparks_bonus_levels", level)
        self.wand_cooldown_s -= dt
        while self.wand_cooldown_s <= 0.0 and self.enemies:
            self.wand_cooldown_s += interval
            nearest = min(self.enemies, key=lambda e: _dist(self.hero, e))
            dx = nearest["x"] - self.hero["x"]
            dy = nearest["y"] - self.hero["y"]
            d = math.hypot(dx, dy) or 1.0
            for _ in range(n_sparks):
                self.sparks.append({
                    "x": self.hero["x"],
                    "y": self.hero["y"],
                    "vx": dx / d * cfg["spark_speed"],
                    "vy": dy / d * cfg["spark_speed"],
                    "damage": damage,
                    "life_s": SPARK_LIFETIME_S,
                })

    def _update_spin_blades(self, dt):
        level = self.weapons.get("spin_blades")
        if level is None:
            self.blade_positions = []
            return
        cfg = self.cfg["weapons"]["spin_blades"]
        n = cfg["blades"] + self._bonus("spin_blades", "blades_bonus_levels", level)
        damage = self._wbase("spin_blades", "damage", level)
        self.blade_angle = (self.blade_angle + TAU / cfg["turn_period"] * dt) % TAU
        self.blade_positions = []
        hit_r = cfg["blade_radius"]
        for i in range(n):
            a = self.blade_angle + i * TAU / n
            bx = self.hero["x"] + cfg["radius"] * math.cos(a)
            by = self.hero["y"] + cfg["radius"] * math.sin(a)
            self.blade_positions.append((bx, by))
            for e in self.enemies:
                if math.hypot(e["x"] - bx, e["y"] - by) <= e["radius"] + hit_r:
                    if e["blade_cd"].get(i, 0.0) <= 0.0:
                        self._hit(e, damage)
                        e["blade_cd"][i] = cfg["hit_cooldown"]

    def _update_holy_ring(self, dt):
        level = self.weapons.get("holy_ring")
        if level is None:
            self.ring_radius = 0.0
            return
        cfg = self.cfg["weapons"]["holy_ring"]
        self.ring_radius = self._wbase("holy_ring", "radius", level)
        self.ring_cooldown_s -= dt
        if self.ring_cooldown_s <= 0.0:
            self.ring_cooldown_s += cfg["interval"]
            damage = self._wbase("holy_ring", "damage", level)
            r = self.ring_radius
            for e in self.enemies:
                if _dist(self.hero, e) <= r:
                    self._hit(e, damage)

    def _update_sparks(self, dt):
        surviving = []
        for s in self.sparks:
            s["x"] += s["vx"] * dt
            s["y"] += s["vy"] * dt
            s["life_s"] -= dt
            if s["life_s"] <= 0.0:
                continue
            hit = next(
                (e for e in self.enemies
                 if math.hypot(e["x"] - s["x"], e["y"] - s["y"]) <= e["radius"] + 2),
                None,
            )
            if hit is None:
                surviving.append(s)
                continue
            self._hit(hit, s["damage"])
        self.sparks = surviving

    def _reap_dead_enemies(self):
        alive = []
        feel = self.cfg["feel"]
        for e in self.enemies:
            if e["hp"] > 0:
                alive.append(e)
            else:
                self.kills += 1
                self.events["death"] += 1
                self.flashes.append({
                    "x": e["x"],
                    "y": e["y"],
                    "r0": e["radius"],
                    "life_s": feel["flash_s"],
                    "max_life": feel["flash_s"],
                })
                if e["xp"] > 0:
                    self.gems.append({"x": e["x"], "y": e["y"], "xp": e["xp"]})
        self.enemies = alive

    # ---- gems and level-up ----------------------------------------------
    def _update_gems(self, dt):
        hero = self.hero
        magnet = self.magnet_radius()
        pickup = self.cfg["hero"]["radius"]
        speed = self.cfg["gems"]["speed"]
        keep = []
        for g in self.gems:
            d = _dist(hero, g)
            if d <= pickup:
                self.add_xp(g["xp"])
                self.events["gem"] += 1
                continue
            if d <= magnet and d > 1e-9:
                step_len = min(speed * dt, d)
                g["x"] += (hero["x"] - g["x"]) / d * step_len
                g["y"] += (hero["y"] - g["y"]) / d * step_len
            keep.append(g)
        self.gems = keep

    def _maybe_open_level_up(self):
        if self.pending_level_ups > 0 and not self.level_up_open:
            self.cards = self._generate_cards()
            self.level_up_open = True
            self.events["levelup"] += 1

    def _generate_cards(self):
        pool = []
        weapons_cfg = self.cfg["weapons"]
        # level up a weapon you already have (unless it is maxed)
        for name, level in self.weapons.items():
            if level < weapons_cfg[name]["max_level"]:
                pool.append({"kind": "weapon_up", "key": name, "level": level + 1})
        # a brand new weapon, only while you have fewer than the maximum
        if len(self.weapons) < self.cfg["weapons_max"]:
            for name in weapons_cfg:
                if name not in self.weapons:
                    pool.append({"kind": "weapon_new", "key": name, "level": 1})
        # passive items (new or level up), never past their max level
        for name, pcfg in self.cfg["passives"].items():
            level = self.passives.get(name, 0)
            if level < pcfg["max_level"]:
                kind = "passive_up" if level > 0 else "passive_new"
                pool.append({"kind": kind, "key": name, "level": level + 1})

        self.rng.shuffle(pool)
        chosen = pool[: self.cfg["level_up"]["cards"]]
        if not chosen:  # nothing left to improve
            chosen = [{"kind": "heal", "key": "heal", "level": 0}]
        for card in chosen:
            card["name"], card["text"] = self._card_label(card)
        return chosen

    def _card_label(self, card):
        kind = card["kind"]
        if kind == "heal":
            return "Heal", f"Restore {self.cfg['level_up']['heal_amount']} HP"
        table = self.cfg["weapons"] if kind.startswith("weapon") else self.cfg["passives"]
        cfg = table[card["key"]]
        return cfg["name"], cfg["desc"]

    # ---- despawn / boss recall ------------------------------------------
    def _despawn_far_enemies(self):
        r = self.cfg["spawning"]["despawn_radius"]
        keep = []
        for e in self.enemies:
            d = _dist(self.hero, e)
            if e["boss"]:
                # the boss is never removed; if it drifts too far, pull it back
                if d > self.cfg["boss"]["recall_radius"]:
                    self._place_on_spawn_circle(e)
                keep.append(e)
            elif d <= r:
                keep.append(e)
        self.enemies = keep

    def _place_on_spawn_circle(self, e):
        angle = self.rng.uniform(0.0, TAU)
        r = self.cfg["spawning"]["spawn_radius"]
        e["x"] = self.hero["x"] + r * math.cos(angle)
        e["y"] = self.hero["y"] + r * math.sin(angle)
