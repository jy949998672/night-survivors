"""Night Survivors: window, drawing, input and sound.

Step 5 (Sound and feel): plays the short sounds from sounds/ and shows damage
numbers, a flash when an enemy dies and a small screen shake when the hero is
hurt. Run `python make_sounds.py` to build sounds/ (this file never imports
wave or make_sounds, so it still works in a browser through pygbag).
Run `python make_assets.py` to build assets/ from raw_art/ (this file never
imports Pillow or make_assets either). If assets/ or sounds/ is empty, main.py
falls back to shapes and silence.
The actual game rules live in rules.py; this file only reads input, moves the
camera, draws and plays sound. The loop is async and ends with
asyncio.run(main()) so pygbag can run this same file in a web browser.

Run with a fixed number of frames for headless checks:
    python main.py --frames 300
"""

import asyncio
import math
import os
import random
import sys

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame

import rules
from rules import Game, load_config

CONFIG = load_config()
CFG = CONFIG["window"]
VIEW_W, VIEW_H = CFG["width"], CFG["height"]
DT = CONFIG["dt"]

COL_FLOOR_A = (60, 60, 80)
COL_FLOOR_B = (50, 50, 68)
COL_HERO = (80, 180, 255)
COL_BAT = (200, 80, 80)
COL_SPARK = (255, 230, 120)
COL_BLADE = (210, 210, 225)
COL_RING = (255, 240, 150)
COL_GEM_1 = (90, 150, 255)
COL_GEM_2 = (255, 120, 210)
COL_HP = (220, 60, 60)
COL_HP_BACK = (70, 30, 30)
COL_XP = (80, 130, 255)
COL_XP_BACK = (30, 40, 70)
COL_VICTORY = (120, 240, 140)

WIN_TIME = CONFIG["win_time"]


def fmt_time(seconds):
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}:{s:02d}"

# loaded PNGs from assets/ (made by make_assets.py); empty = draw shapes instead
assets = {}
# loaded sounds from sounds/ (made by make_sounds.py); empty = stay silent
sounds = {}
audio_ok = False


def load_assets():
    assets.clear()
    for name, filename in CONFIG["assets"]["sprite"].items():
        _try_load(name, filename)
    _try_load("floor", CONFIG["assets"]["floor_tile"])
    for xp, filename in CONFIG["assets"]["gems"].items():
        _try_load(f"gem_{xp}", filename)
    print(f"[assets] loaded {len(assets)} pictures" if assets else "[assets] none found, drawing shapes")


def _try_load(key, filename):
    path = os.path.join("assets", filename)
    if os.path.exists(path):
        assets[key] = pygame.image.load(path).convert_alpha()


def _sound_path(filename):
    """Pick the sound file that exists, preferring .ogg (the browser format)
    over .wav. Returns None if neither is present."""
    base = os.path.splitext(filename)[0]
    for ext in (".ogg", ".wav"):
        path = os.path.join("sounds", base + ext)
        if os.path.exists(path):
            return path
    return None


def load_sounds():
    """Load the sounds from sounds/. If there is no audio device (a headless
    check, or a browser that has not unlocked sound yet) we just stay silent.
    Sound must never stop the game from running, so every failure is caught."""
    global audio_ok
    sounds.clear()
    audio_ok = False
    try:
        if pygame.mixer.get_init() is None:
            pygame.mixer.init()
    except Exception as exc:  # no audio device is fine, the game just plays silent
        print(f"[sound] mixer unavailable ({exc}), staying silent")
        return
    for key, filename in CONFIG["sounds"].items():
        path = _sound_path(filename)
        if path is None:
            continue
        try:
            sounds[key] = pygame.mixer.Sound(path)
        except Exception as exc:  # an unsupported file must not crash the game
            print(f"[sound] could not load {path} ({exc})")
    audio_ok = bool(sounds)
    print(f"[sound] loaded {len(sounds)} sounds" if sounds else "[sound] none found, staying silent")


def play_events(events):
    """Make a sound for whatever happened during the last step."""
    if not audio_ok:
        return
    if events["hit"]:
        _play("hit")
    if events["gem"]:
        _play("gem")
    if events["hurt"]:
        _play("hurt")
    if events["levelup"]:
        _play("levelup")
    if events["end"]:
        _play("end")


def _play(key):
    sound = sounds.get(key)
    if sound is not None:
        sound.play()


def screen_to_world(x, y, hero):
    """Turn a screen point (mouse/finger) into a world point.

    The camera keeps the hero in the middle, so the world point is the
    offset from the screen centre added to the hero position.
    """
    return x - VIEW_W / 2.0 + hero["x"], y - VIEW_H / 2.0 + hero["y"]


def card_rects(n):
    """The n card rectangles shown on the level-up screen (also used for clicks)."""
    w = VIEW_W - 40
    h = 130
    gap = 14
    total = n * h + (n - 1) * gap
    y0 = (VIEW_H - total) // 2
    x = (VIEW_W - w) // 2
    return [pygame.Rect(x, y0 + i * (h + gap), w, h) for i in range(n)]


def inventory_text(game):
    parts = []
    for key, level in game.weapons.items():
        parts.append(f"{CONFIG['weapons'][key]['short']} {level}")
    for key, level in game.passives.items():
        parts.append(f"{CONFIG['passives'][key]['short']} {level}")
    return "  ".join(parts)


def draw(screen, game, font_big, font_small, paused):
    hero = game.hero
    cam_x = hero["x"] - VIEW_W / 2.0
    cam_y = hero["y"] - VIEW_H / 2.0
    # a small shake for a moment after the hero is hurt
    if game.shake_s > 0.0:
        amp = CONFIG["feel"]["shake_amplitude"] * (game.shake_s / CONFIG["feel"]["shake_s"])
        cam_x += random.uniform(-amp, amp)
        cam_y += random.uniform(-amp, amp)

    # repeating floor: one tile, forever in every direction
    floor_img = assets.get("floor")
    if floor_img is not None:
        tile = floor_img.get_width()
        _draw_tiled(screen, floor_img, cam_x, cam_y, tile)
    else:
        tile = CONFIG["floor"]["tile"]
        x0 = int(cam_x // tile) * tile
        y0 = int(cam_y // tile) * tile
        for ty in range(y0 - tile, int(cam_y) + VIEW_H + tile, tile):
            for tx in range(x0 - tile, int(cam_x) + VIEW_W + tile, tile):
                col = COL_FLOOR_A if ((tx // tile) + (ty // tile)) % 2 == 0 else COL_FLOOR_B
                pygame.draw.rect(screen, col, (round(tx - cam_x), round(ty - cam_y), tile + 1, tile + 1))

    def blit_or_circle(key, pos, radius, colour, angle=0.0, scale=1):
        img = assets.get(key)
        if img is not None:
            if scale != 1:
                size = (img.get_width() * scale, img.get_height() * scale)
                img = pygame.transform.scale(img, size)
            if angle:
                img = pygame.transform.rotate(img, angle)
            screen.blit(img, img.get_rect(center=pos))
        else:
            pygame.draw.circle(screen, colour, pos, radius * scale)

    # holy ring (drawn under the hero)
    if "holy_ring" in game.weapons and game.ring_radius > 0:
        pygame.draw.circle(screen, COL_RING, (round(hero["x"] - cam_x), round(hero["y"] - cam_y)),
                           round(game.ring_radius), 2)

    for g in game.gems:
        pos = (round(g["x"] - cam_x), round(g["y"] - cam_y))
        colour = COL_GEM_1 if g["xp"] <= 1 else COL_GEM_2
        blit_or_circle(f"gem_{g['xp']}", pos, 6, colour)

    for e in game.enemies:
        pos = (round(e["x"] - cam_x), round(e["y"] - cam_y))
        blit_or_circle(e["type"], pos, e["radius"], COL_BAT, scale=e.get("draw_scale", 1))

    # a quick expanding ring where an enemy just died
    feel = CONFIG["feel"]
    for fa in game.flashes:
        pos = (round(fa["x"] - cam_x), round(fa["y"] - cam_y))
        prog = 1.0 - fa["life_s"] / fa["max_life"]
        radius = round(fa["r0"] + prog * feel["flash_expand"])
        pygame.draw.circle(screen, (255, 220, 150), pos, radius, 2)

    for s in game.sparks:
        pos = (round(s["x"] - cam_x), round(s["y"] - cam_y))
        angle = math.degrees(math.atan2(s["vy"], s["vx"]))
        blit_or_circle("spark", pos, 3, COL_SPARK, angle=-(angle - 45))

    for bx, by in game.blade_positions:
        pos = (round(bx - cam_x), round(by - cam_y))
        blit_or_circle("spark", pos, 6, COL_BLADE, angle=-(math.degrees(game.blade_angle) - 45))

    hpos = (round(hero["x"] - cam_x), round(hero["y"] - cam_y))
    blit_or_circle("hero", hpos, CONFIG["hero"]["radius"], COL_HERO)

    # damage numbers float up and fade out
    for fl in game.floaters:
        pos = (round(fl["x"] - cam_x), round(fl["y"] - cam_y))
        t = max(0.0, min(1.0, fl["life_s"] / fl["max_life"]))
        txt = font_small.render(str(fl["value"]), True, (255, 240, 180))
        txt.set_alpha(int(255 * t))
        screen.blit(txt, txt.get_rect(center=pos))

    _draw_hud(screen, game, font_small)

    if game.game_over or game.won:
        if game.won:
            title, colour = "VICTORY", COL_VICTORY
        else:
            title, colour = "GAME OVER", (255, 80, 80)
        label = font_big.render(title, True, colour)
        stats = font_small.render(
            f"time {fmt_time(game.time_s)}   kills {game.kills}   level {game.level}",
            True, (240, 240, 240))
        again = font_small.render("click or tap to play again", True, (240, 240, 240))
        cx = VIEW_W // 2
        screen.blit(label, label.get_rect(center=(cx, VIEW_H // 2 - 40)))
        screen.blit(stats, stats.get_rect(center=(cx, VIEW_H // 2)))
        screen.blit(again, again.get_rect(center=(cx, VIEW_H // 2 + 30)))
        return

    if game.level_up_open:
        _draw_level_up(screen, game, font_big, font_small)
        return

    if paused:
        label = font_big.render("PAUSED", True, (255, 255, 255))
        screen.blit(label, label.get_rect(center=(VIEW_W // 2, VIEW_H // 2)))


def _draw_tiled(screen, img, cam_x, cam_y, tile):
    x0 = int(cam_x // tile) * tile
    y0 = int(cam_y // tile) * tile
    for ty in range(y0 - tile, int(cam_y) + VIEW_H + tile, tile):
        for tx in range(x0 - tile, int(cam_x) + VIEW_W + tile, tile):
            screen.blit(img, (round(tx - cam_x), round(ty - cam_y)))


def _draw_hud(screen, game, font_small):
    hero = game.hero
    # HP bar (red) and XP bar (blue), top left
    pygame.draw.rect(screen, COL_HP_BACK, (8, 8, 120, 10))
    pygame.draw.rect(screen, COL_HP, (8, 8, round(120 * max(0.0, hero["hp"] / hero["max_hp"])), 10))
    pygame.draw.rect(screen, COL_XP_BACK, (8, 22, 120, 8))
    pygame.draw.rect(screen, COL_XP, (8, 22, round(120 * min(1.0, game.xp / game.xp_to_next())), 8))
    status = font_small.render(f"Lv {game.level}   HP {hero['hp']}/{hero['max_hp']}", True, (240, 240, 240))
    screen.blit(status, (8, 34))

    # top right: the survival clock, e.g. "0:45 / 2:00"
    clock_label = font_small.render(f"{fmt_time(game.time_s)} / {fmt_time(WIN_TIME)}", True, (240, 240, 240))
    screen.blit(clock_label, clock_label.get_rect(topright=(VIEW_W - 8, 8)))

    # bottom: what you have, e.g. "Spark 2  Blades 1  Boots 1"
    items = font_small.render(inventory_text(game), True, (200, 200, 200))
    screen.blit(items, (8, VIEW_H - 22))


def _draw_level_up(screen, game, font_big, font_small):
    veil = pygame.Surface((VIEW_W, VIEW_H), pygame.SRCALPHA)
    veil.fill((0, 0, 0, 170))
    screen.blit(veil, (0, 0))
    title = font_big.render("Level Up!", True, (255, 240, 160))
    screen.blit(title, title.get_rect(center=(VIEW_W // 2, 70)))

    rects = card_rects(len(game.cards))
    for rect, card in zip(rects, game.cards):
        pygame.draw.rect(screen, (35, 38, 55), rect, border_radius=10)
        pygame.draw.rect(screen, (120, 130, 180), rect, 2, border_radius=10)
        name = font_big.render(card["name"], True, (240, 240, 240))
        screen.blit(name, (rect.x + 16, rect.y + 14))
        if card["level"] > 0:
            lvl = font_small.render(f"Lv {card['level']}", True, (150, 200, 255))
            screen.blit(lvl, (rect.x + 16, rect.y + 58))
        text = font_small.render(card["text"], True, (200, 200, 200))
        screen.blit(text, (rect.x + 16, rect.y + 92))


paused = False


async def main():
    global paused
    frames = None
    auto_harness = False
    args = sys.argv[1:]
    while "--frames" in args:
        i = args.index("--frames")
        frames = int(args[i + 1])
        del args[i:i + 2]
    if "--autoharness" in args:
        auto_harness = True

    screen = pygame.display.set_mode((VIEW_W, VIEW_H))
    pygame.display.set_caption(CFG["title"])
    font_big = pygame.font.Font(None, 40)
    font_small = pygame.font.Font(None, 22)
    load_assets()
    load_sounds()

    game = Game(CONFIG)
    clock = pygame.time.Clock()
    mouse = [VIEW_W / 2.0, VIEW_H / 2.0]
    touching = [False]
    frame = 0
    harness_seen = set()

    while True:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit()
                return
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_p:
                paused = not paused
            if ev.type == pygame.MOUSEMOTION:
                mouse = list(ev.pos)
            if ev.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
                if ev.type == pygame.FINGERDOWN:
                    touching[0] = True
                    px, py = ev.x * VIEW_W, ev.y * VIEW_H
                else:
                    px, py = ev.pos
                if game.level_up_open:
                    for i, rect in enumerate(card_rects(len(game.cards))):
                        if rect.collidepoint(px, py):
                            game.choose_card(i)
                            break
                elif game.game_over or game.won:
                    game.restart()
                    paused = False
                else:
                    game.set_target(*screen_to_world(px, py, game.hero))
            if ev.type in (pygame.MOUSEBUTTONUP, pygame.FINGERUP):
                if ev.type == pygame.FINGERUP:
                    touching[0] = False
                    game.clear_target()

        if not game.game_over and not game.won and not game.level_up_open and not paused:
            if not touching[0]:
                # computer: keep walking toward the pointer every frame
                game.set_target(*screen_to_world(mouse[0], mouse[1], game.hero))
            # phone: while the finger is down the target stays where it was set
            game.step(DT)
            play_events(game.events)

        if auto_harness:
            _harness(game, frame, harness_seen)

        draw(screen, game, font_big, font_small, paused)
        pygame.display.flip()

        frame += 1
        if frames is not None and frame >= frames:
            print(
                f"[done] t={game.time_s:4.1f}s lv={game.level} hp={game.hero['hp']} "
                f"enemies={len(game.enemies)} gems={len(game.gems)} kills={game.kills} "
                f"inv=[{inventory_text(game)}] game_over={game.game_over} won={game.won}"
            )
            pygame.quit()
            return
        clock.tick(60)
        await asyncio.sleep(0)


def _harness(game, frame, seen):
    """Headless-only nudges so every step-4 code path runs at least once."""
    if frame == 10 and "weapons" not in seen:
        game.weapons["spin_blades"] = 2
        game.weapons["holy_ring"] = 1
        game.passives["swift_boots"] = 1
        game.passives["magnet"] = 1
        seen.add("weapons")
    if frame == 90 and "levelup" not in seen:
        game.add_xp(game.xp_to_next())  # force a level-up card screen
        seen.add("levelup")
    if game.level_up_open and "lu_shown" not in seen:
        print(f"[harness] level-up open, cards={[c['name'] for c in game.cards]}")
        seen.add("lu_shown")
    if not game.level_up_open and "lu_shown" in seen and "lu_picked" not in seen:
        print(f"[harness] a card was chosen by a click; inv=[{inventory_text(game)}]")
        seen.add("lu_picked")
    # if a real click never picked a card, choose one so the unattended run continues
    if game.level_up_open and frame > 95:
        game.choose_card(0)
    # jump the clock just under the boss time so the next step spawns the Demon
    if frame == 150 and "boss" not in seen:
        game.time_s = CONFIG["boss"]["from_s"] - DT
        seen.add("boss")
    if "boss" in seen and "boss_shown" not in seen:
        boss = next((e for e in game.enemies if e.get("boss")), None)
        if boss is not None:
            print(f"[harness] frame {frame}: boss spawned at ({round(boss['x'])},{round(boss['y'])})")
            seen.add("boss_shown")
    # jump the clock just under the win time so the next step wins
    if frame == 210 and "win" not in seen:
        game.time_s = WIN_TIME - DT
        seen.add("win")
    if game.won and "win_shown" not in seen:
        print(f"[harness] frame {frame}: VICTORY screen is up (kills={game.kills})")
        seen.add("win_shown")
    # after the win, restart and force the GAME OVER screen too
    if game.won and "win_shown" in seen and "over" not in seen:
        game.restart()
        game.hero["hp"] = 0
        game.step(DT)
        print(f"[harness] frame {frame}: GAME OVER screen is up")
        seen.add("over")


if __name__ == "__main__":
    pygame.init()
    asyncio.run(main())
