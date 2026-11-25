"""Game-specific RAM readers: turn raw console memory into labeled facts that
augment the AI's screenshot with ground truth it cannot get from pixels.

This is the "combine with reading memory" layer. Each game provides a function
that reads known addresses from the libretro core's RAM (via LibretroCore.read_*)
and returns a dict of human-readable facts. Addresses are discovered empirically
(see tools/ for the RAM-diff probes).

Fairness: every fact is tagged `fair` (information a human player could perceive
through normal play — HUD values, on-screen highlights, your own position) or
`hidden` (internal state a human cannot see — enemy HP, RNG, off-screen entities,
quest flags). In `fair_only` mode the hidden facts (and their hints) are withheld,
so the AI plays with no information advantage over a human.
"""

from __future__ import annotations

import hashlib
from typing import Callable, Dict, List, Tuple


def _onehot_index(v: int) -> int:
    """A one-hot byte (1,2,4,8,...) -> its bit index (0,1,2,...); -1 if not one-hot."""
    if v > 0 and (v & (v - 1)) == 0:
        return v.bit_length() - 1
    return -1


# One fact entry: (name, value, hint, is_fair)
Entry = Tuple[str, object, str, bool]


def _assemble(entries: List[Entry], fair_only: bool) -> Dict[str, object]:
    facts: Dict[str, object] = {}
    hints: List[str] = []
    for name, value, hint, is_fair in entries:
        if fair_only and not is_fair:
            continue
        facts[name] = value
        if hint:
            hints.append(hint)
    if hints:
        facts["_hint"] = " ".join(hints)
    if fair_only:
        facts["_mode"] = "fair"
    return facts


def ultima7_facts(emu, fair_only: bool = False) -> Dict[str, object]:
    """Ground-truth facts for Ultima VII: The Black Gate (SNES).

    Verified addresses (RETRO_MEMORY_SYSTEM_RAM / WRAM):
      0x8E   Avatar world X (16-bit LE). RIGHT increases it, LEFT decreases it. [fair]
      0x97C  Avatar world Y (16-bit LE). DOWN increases it, UP decreases it.   [fair]
      0x22E  conversation keyword cursor, one-hot bitmask (bit N = Nth keyword
             highlighted). D-pad advances it (wrapping); B speaks it.          [fair]
    """
    entries: List[Entry] = []

    # Position: a human sees where they are and whether a move worked -> FAIR.
    x = emu.read_u16(0x8E)
    y = emu.read_u16(0x97C)
    if x >= 0 and y >= 0:
        hint = ("avatar_x/avatar_y are the Avatar's overworld position (X rises going "
                "RIGHT/east, Y rises going DOWN/south). If your last move did not "
                "change them, you walked into a wall/obstacle — turn and go a "
                "different way.")
        entries.append(("avatar_x", x, hint, True))
        entries.append(("avatar_y", y, "", True))  # hint already covers both

    # A dialogue/text box is drawn iff its buffer is populated (verified: 0 in the
    # overworld, non-zero when a box shows). Gating the cursor on this removes the
    # old overworld false-positive where 0x22E coincidentally read as a power of 2.
    conv_buf = emu.read_ram(0x14440, 0x80)
    in_conv = any(conv_buf)
    if in_conv:
        entries.append(("in_conversation", True,
                        "A dialogue/text box is open on screen.", True))
        # internal signature of the dialogue text (underscore = hidden from the
        # prompt/display) so the orchestrator can re-read when the text changes.
        entries.append(("_dialogue_sig", hashlib.md5(conv_buf).hexdigest()[:8], "", True))
        # Conversation cursor: the highlight is on screen (just hard to see) -> FAIR.
        cur = emu.read_u8(0x22E)
        idx = _onehot_index(cur) if cur is not None and cur >= 0 else -1
        if idx >= 0:
            hint = (f"The highlighted conversation keyword is index {idx} (0 = leftmost "
                    "word in the topic row). Tap the Control Pad to move the highlight one "
                    "keyword (wrapping); press B to SPEAK the highlighted keyword. Move "
                    "until this index matches the word you want, then press B.")
            entries.append(("dialogue_cursor_index", idx, hint, True))

    # --- Examples of HIDDEN facts to add later (is_fair=False), e.g.:
    #   entries.append(("rng_seed", emu.read_u16(0x????), "", False))
    #   entries.append(("enemy_hp", emu.read_u8(0x????), "", False))
    # These are withheld automatically in fair_only mode.

    return _assemble(entries, fair_only)


# Final Fantasy VI (US "Final Fantasy III") playable actors, by block/actor index.
# Index into the $1600 character block == actor id for the roster slots.
_FF6_ACTORS = {
    0: "Terra", 1: "Locke", 2: "Cyan", 3: "Shadow", 4: "Edgar", 5: "Sabin",
    6: "Celes", 7: "Strago", 8: "Relm", 9: "Setzer", 10: "Mog", 11: "Gau",
    12: "Gogo", 13: "Umaro",
}


def ff6_facts(emu, fair_only: bool = False) -> Dict[str, object]:
    """Ground-truth facts for Final Fantasy VI / "Final Fantasy III" (SNES, US).

    Verified live against the running ROM (Terra reads Lv3 HP63 MP24 at game
    start; GP reads 3000 once control begins). Addresses are WRAM offsets
    (SNES $7Exxxx -> offset xxxx), per the Data Crystal FF6 RAM map:

      $1860  GP / gil, 24-bit little-endian.                               [fair]
      $1600  Character block: 16 records of $25 (37) bytes; record i is the
             actor in roster slot i (actor id $FF = empty slot). Offsets
             within a record:
               +$00 actor id   +$08 level
               +$09 cur HP(16)  +$0B max HP(16)
               +$0D cur MP(16)  +$0F max MP(16)                            [fair]

    Everything here (party HP/MP/level, gil) is shown to the player on the
    menu and battle HUD, so all facts are FAIR. Field map position and a
    battle/menu/dialog mode flag are NOT in this reader yet — they need to be
    verified in live field play (hold-a-direction diff, see tools/find_position.py)
    and will be added once reachable.
    """
    entries: List[Entry] = []

    gil = emu.read_ram(0x1860, 3)
    if len(gil) == 3:
        g = gil[0] | (gil[1] << 8) | (gil[2] << 16)
        entries.append(("gil", g, "gil = the party's money (GP), as shown on the "
                                  "menu; spend it in shops on weapons/armor/items.", True))

    members: List[str] = []
    lowest = None  # (name, pct) of the most-hurt living member
    for i in range(16):
        base = 0x1600 + i * 0x25
        actor = emu.read_u8(base)
        if actor < 0 or actor == 0xFF:
            continue  # empty roster slot
        name = _FF6_ACTORS.get(actor, f"char{actor}")
        lvl = emu.read_u8(base + 0x08)
        chp, mhp = emu.read_u16(base + 0x09), emu.read_u16(base + 0x0B)
        cmp_, mmp = emu.read_u16(base + 0x0D), emu.read_u16(base + 0x0F)
        if mhp <= 0 or mhp > 9999:
            continue  # record not populated / garbage -> skip
        tag = "" if chp > 0 else " WOUNDED"
        members.append(f"{name} Lv{lvl} HP{chp}/{mhp} MP{cmp_}/{mmp}{tag}")
        if chp > 0:
            pct = chp / mhp
            if lowest is None or pct < lowest[1]:
                lowest = (name, pct)

    if members:
        hint = ("party lists each member you have, with their current/max HP and MP "
                "and level (the same numbers on the menu and battle screens). Use it "
                "to decide when to heal, use items, or rest at an Inn. 'WOUNDED' means "
                "that member is at 0 HP (KO'd) and needs a Fenix Down / revive.")
        if lowest and lowest[1] < 0.25:
            hint += f" {lowest[0]} is low on HP ({int(lowest[1]*100)}%) — heal soon."
        entries.append(("party", " | ".join(members), hint, True))
        entries.append(("party_size", len(members), "", True))

    # --- battle state (verified live against a Whelk-fight savestate) ---
    # $11E0 (u16): encountered-monster formation — 0 in the field, non-zero in a
    # battle. The whole $11xx battle block is zeroed outside battle, so this is a
    # reliable "am I in a fight?" signal.
    formation = emu.read_u16(0x11E0)
    if formation is not None and 0 < formation < 0x800:
        entries.append(("in_battle", True,
            "You are IN A TURN-BASED BATTLE (FF6). Act ONLY when battle_menu_open=1; "
            "otherwise a gauge is still filling or an action is animating, so WAIT "
            "(don't mash). To act: move the cursor with UP/DOWN and press A to choose a "
            "command, then choose a target and press A. Heal or revive a low/0-HP ally; "
            "don't waste turns attacking a defended shell.", True))
        # $201: 1 while a command menu is open and waiting for input, else 0.
        # Verified stable through idle frames and flips to 0 once a command is taken.
        menu_open = emu.read_u8(0x201) == 1
        entries.append(("battle_menu_open", bool(menu_open),
            "1 = a command menu is OPEN and waiting for you — act now. 0 = no menu is "
            "up (gauge filling / action animating) — wait, don't press anything.", True))
        if menu_open:
            # cursor rows (0 = top). $43F6 = the top command menu (e.g. MagiTek=0,
            # Item=1); $3B = the open sub-list (e.g. the MagiTek beam list). Use these
            # WITH the screen to confirm which option is highlighted before pressing A.
            entries.append(("battle_cmd_row", emu.read_u8(0x43F6),
                "highlighted row in the TOP battle command menu (0 = top). "
                "Press DOWN/UP to change it, then A.", True))
            entries.append(("battle_sub_row", emu.read_u8(0x3B),
                "highlighted row in the OPEN sub-list (e.g. the MagiTek beam list — "
                "move to the one you want, like Heal Force, and confirm it's highlighted "
                "before pressing A). Only meaningful once a sub-menu is open on screen.",
                True))

    return _assemble(entries, fair_only)


# Registry: game key -> reader(emu, fair_only=False). "none" disables RAM facts.
READERS: Dict[str, Callable[..., Dict[str, object]]] = {
    "ultima7": ultima7_facts,
    "ff6": ff6_facts,
    "none": lambda emu, fair_only=False: {},
}


def get_reader(name: str, fair: bool = False) -> Callable[[object], Dict[str, object]]:
    """Return a reader callable emu -> facts, honoring fair mode."""
    base = READERS.get(name, READERS["none"])
    return lambda emu: base(emu, fair_only=fair)
