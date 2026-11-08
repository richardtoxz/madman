"""Parametros compartilhados de ritmo e backoff das operacoes em lote."""


class MessageConfig:
    # Intervalo unico entre mensagens, compartilhado por todas as limpezas.
    BASE_DELAY = 0.04
    MAX_DELAY = 4.0
    BACKOFF_MULTIPLIER = 2.0
    MAX_429_CONSECUTIVE = 5


class BulkConfig:
    MIN_DELAY = 1.2
    MAX_DELAY = 4.0
    BATCH_SIZE = 20
    BATCH_PAUSE_MIN = 5.0
    BATCH_PAUSE_MAX = 10.0
    INITIAL_BACKOFF = 5.0
    MAX_BACKOFF = 120.0
    BACKOFF_MULTIPLIER = 2.0
    MAX_429_CONSECUTIVE = 4
    LONG_PAUSE_CHANCE = 0.05
    LONG_PAUSE_MIN = 3.0
    LONG_PAUSE_MAX = 7.0
