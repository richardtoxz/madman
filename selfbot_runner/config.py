"""
Configurações compartilhadas do selfbot runner.
"""


# Flags globais de controle compartilhadas entre módulos
control_flags = {
    "started": False,
    "paused": False,
    "cancelled": False,
}


class BulkConfig:
    """Configurações de rate-limiting para operações bulk."""
    MIN_DELAY = 3.5
    MAX_DELAY = 6.5
    BATCH_SIZE = 8
    BATCH_PAUSE_MIN = 20.0
    BATCH_PAUSE_MAX = 35.0
    INITIAL_BACKOFF = 15.0
    MAX_BACKOFF = 180.0
    BACKOFF_MULTIPLIER = 2.5
    MAX_429_CONSECUTIVE = 4
    LONG_PAUSE_CHANCE = 0.12
    LONG_PAUSE_MIN = 5.0
    LONG_PAUSE_MAX = 12.0
