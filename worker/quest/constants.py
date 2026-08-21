from __future__ import annotations

# Questify's auto-completion priority. Keep unsupported task types recognized
# so the panel can explain them instead of silently hiding the Quest.
AUTO_COMPLETABLE_TASKS = (
    "PLAY_ON_DESKTOP",
    "PLAY_ON_XBOX",
    "PLAY_ON_PLAYSTATION",
    "PLAY_ACTIVITY",
    "WATCH_VIDEO",
    "WATCH_VIDEO_ON_MOBILE",
    "ACHIEVEMENT_IN_ACTIVITY",
)

RECOGNIZED_TASKS = (
    *AUTO_COMPLETABLE_TASKS,
    "PLAY_ON_DESKTOP_V2",
    "ACHIEVEMENT_IN_GAME",
    "STREAM_ON_DESKTOP",
)

VIDEO_TASKS = frozenset(("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"))
PLAY_TASKS = frozenset(
    (
        "PLAY_ON_DESKTOP",
        "PLAY_ON_DESKTOP_V2",
        "PLAY_ON_XBOX",
        "PLAY_ON_PLAYSTATION",
        "PLAY_ACTIVITY",
    )
)
