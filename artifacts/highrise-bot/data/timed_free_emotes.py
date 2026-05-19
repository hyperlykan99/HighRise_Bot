"""
data/timed_free_emotes.py
--------------------------
Confirmed free / usable emote catalog for DJ_DUDU / ChillTopia.

Only emotes verified to be active on the bot's account are listed here.
This is the DEFAULT catalog used in "free" emote mode (!emotemode free).

Each entry:
  text  — display name; normalized to command via lowercase + strip non-alphanum
  value — exact Highrise SDK emote ID (passed to send_emote)
  time  — animation loop duration in seconds
           0   = one-shot (play once, do not loop)
           >0  = loop: play, wait `time` seconds, repeat until user says stop

Command examples:
  "The Wave"         → thewave
  "Don't Start Now"  → dontstartnow
  "Idle Sitfloor"    → idlesitfloor

Admins can switch between catalogs with:
  !emotemode free   — use only this confirmed list (default)
  !emotemode all    — use full experimental catalog

Last updated: 2026-05
"""

timed_free_emotes_list: list[dict] = [
    # ── Greetings / social ────────────────────────────────────────────────────
    {"text": "The Wave",          "value": "emote-wave",              "time": 2.690873},
    {"text": "Wave 2",            "value": "emote-wave2",             "time": 2.5},
    {"text": "Wave 3",            "value": "emote-wave3",             "time": 2.5},
    {"text": "Greet",             "value": "emote-greet",             "time": 2.833333},
    {"text": "Hello",             "value": "emote-hello",             "time": 2.5},
    {"text": "Bow",               "value": "emote-bow",               "time": 2.458333},
    {"text": "Bow 2",             "value": "emote-bow2",              "time": 2.5},
    {"text": "Salute",            "value": "emote-salute",            "time": 2.666667},
    {"text": "High Five",         "value": "emote-highfive",          "time": 3.0},
    {"text": "Fist Bump",         "value": "emote-fistbump",          "time": 2.5},
    {"text": "Blow Kiss",         "value": "emote-blowkiss",          "time": 3.125},
    {"text": "Hug",               "value": "emote-hug",               "time": 3.333333},
    # ── Reactions / emotions ──────────────────────────────────────────────────
    {"text": "Clap",              "value": "emote-clap",              "time": 1.5},
    {"text": "Clap 2",            "value": "emote-clap2",             "time": 1.666667},
    {"text": "Applause",          "value": "emote-applause",          "time": 3.083333},
    {"text": "Celebrate",         "value": "emote-celebrate",         "time": 4.458333},
    {"text": "Laugh",             "value": "emote-laugh",             "time": 3.541667},
    {"text": "Laugh 2",           "value": "emote-laugh2",            "time": 3.666667},
    {"text": "Cry",               "value": "emote-cry",               "time": 3.458333},
    {"text": "Angry",             "value": "emote-angry",             "time": 3.0},
    {"text": "Sad",               "value": "emote-sad",               "time": 3.333333},
    {"text": "Scared",            "value": "emote-scared",            "time": 3.291667},
    {"text": "Confused",          "value": "emote-confused",          "time": 3.125},
    {"text": "Excited",           "value": "emote-excited",           "time": 3.666667},
    {"text": "Surprise",          "value": "emote-surprise",          "time": 2.916667},
    {"text": "Disgusted",         "value": "emote-disgusted",         "time": 3.375},
    {"text": "Eye Roll",          "value": "emote-eyeroll",           "time": 2.583333},
    {"text": "Facepalm",          "value": "emote-facepalm",          "time": 3.291667},
    {"text": "Boo",               "value": "emote-boo",               "time": 3.458333},
    {"text": "Sorry",             "value": "emote-sorry",             "time": 3.583333},
    {"text": "Thumbs Up",         "value": "emote-thumbsup",          "time": 2.458333},
    {"text": "Thumbs Down",       "value": "emote-thumbsdown",        "time": 2.458333},
    {"text": "Shrug",             "value": "emote-shrug",             "time": 2.666667},
    {"text": "Shrug 2",           "value": "emote-shrug2",            "time": 2.791667},
    {"text": "No",                "value": "emote-no",                "time": 2.0},
    {"text": "Yes",               "value": "emote-yes",               "time": 2.041667},
    {"text": "Nod",               "value": "emote-nod",               "time": 2.0},
    {"text": "Wink",              "value": "emote-wink",              "time": 2.291667},
    {"text": "Peace",             "value": "emote-peace",             "time": 2.833333},
    {"text": "Point",             "value": "emote-point",             "time": 2.875},
    {"text": "Think",             "value": "emote-think",             "time": 3.5},
    {"text": "Roar",              "value": "emote-roar",              "time": 3.708333},
    # ── Poses ────────────────────────────────────────────────────────────────
    {"text": "Flex",              "value": "emote-flex",              "time": 4.833333},
    {"text": "Flex 2",            "value": "emote-flex2",             "time": 4.708333},
    {"text": "Pose",              "value": "emote-pose",              "time": 4.958333},
    {"text": "Pose 2",            "value": "emote-pose2",             "time": 5.041667},
    {"text": "Pose 3",            "value": "emote-pose3",             "time": 5.125},
    {"text": "Selfie",            "value": "emote-selfie",            "time": 4.708333},
    # ── Idle / sitting ────────────────────────────────────────────────────────
    {"text": "Idle",              "value": "emote-idle_loop",         "time": 8.0},
    {"text": "Idle Sitfloor",     "value": "emote-idle_sitfloor",     "time": 8.333333},
    {"text": "Idle Laydown",      "value": "emote-idle_laydown",      "time": 10.0},
    {"text": "Idle Crouch",       "value": "emote-idle_crouch",       "time": 8.0},
    {"text": "Idle Enthusiastic", "value": "emote-idle_enthusiastic", "time": 7.541667},
    {"text": "Sit",               "value": "emote-sit",               "time": 8.083333},
    {"text": "Sit 2",             "value": "emote-sit2",              "time": 8.208333},
    {"text": "Sit 3",             "value": "emote-sit3",              "time": 8.0},
    {"text": "Relax",             "value": "emote-relax",             "time": 7.708333},
    {"text": "Sleep",             "value": "emote-sleep",             "time": 7.916667},
    {"text": "Meditate",          "value": "emote-meditate",          "time": 7.0},
    {"text": "Yoga",              "value": "emote-yoga",              "time": 7.083333},
    {"text": "Stretch",           "value": "emote-stretch",           "time": 5.833333},
    {"text": "Pray",              "value": "emote-pray",              "time": 4.875},
    # ── Dance ─────────────────────────────────────────────────────────────────
    {"text": "Dance",             "value": "emote-dance",             "time": 7.041667},
    {"text": "Dance 2",           "value": "emote-dance2",            "time": 7.166667},
    {"text": "Dance 3",           "value": "emote-dance3",            "time": 7.083333},
    {"text": "Dance 4",           "value": "emote-dance4",            "time": 7.291667},
    {"text": "Disco",             "value": "emote-disco",             "time": 7.458333},
    {"text": "Floss",             "value": "emote-floss",             "time": 5.041667},
    {"text": "Dab",               "value": "emote-dab",               "time": 2.583333},
    {"text": "Robot",             "value": "emote-robot",             "time": 5.875},
    {"text": "Hip Hop",           "value": "emote-hiphop",            "time": 7.166667},
    {"text": "Shuffle",           "value": "emote-shuffle",           "time": 5.958333},
    {"text": "Moonwalk",          "value": "emote-moonwalk",          "time": 5.791667},
    {"text": "Breakdance",        "value": "emote-breakdance",        "time": 7.375},
    {"text": "Shimmy",            "value": "emote-shimmy",            "time": 4.875},
    {"text": "Stomp",             "value": "emote-stomp",             "time": 4.666667},
    {"text": "Headbang",          "value": "emote-headbang",          "time": 4.708333},
    {"text": "Wiggle",            "value": "emote-wiggle",            "time": 4.541667},
    # ── Movement ──────────────────────────────────────────────────────────────
    {"text": "Jump",              "value": "emote-jump",              "time": 3.541667},
    {"text": "Skip",              "value": "emote-skip",              "time": 3.791667},
    {"text": "Spin",              "value": "emote-spin",              "time": 3.625},
    {"text": "Flip",              "value": "emote-flip",              "time": 0},
    {"text": "Cartwheel",         "value": "emote-cartwheel",         "time": 0},
    {"text": "Bounce",            "value": "emote-bounce",            "time": 3.458333},
    # ── Magic / special ───────────────────────────────────────────────────────
    {"text": "Float",             "value": "emote-float",             "time": 6.833333},
    {"text": "Levitate",          "value": "emote-levitate",          "time": 7.0},
    {"text": "Heart",             "value": "emote-heart",             "time": 4.708333},
    {"text": "Sparkle",           "value": "emote-sparkle",           "time": 0},
    {"text": "Firework",          "value": "emote-firework",          "time": 0},
    # ── Instruments ───────────────────────────────────────────────────────────
    {"text": "Guitar",            "value": "emote-guitar",            "time": 6.875},
    {"text": "DJ",                "value": "emote-dj",                "time": 7.041667},
    {"text": "Sing",              "value": "emote-sing",              "time": 5.958333},
]
