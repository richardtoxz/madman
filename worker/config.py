import re
from dataclasses import dataclass


@dataclass(slots=True)
class ControlState:
    started: bool = False
    paused: bool = False
    cancelled: bool = False


class MessageConfig:
    BASE_DELAY = 0.04
    MIN_DELAY = 0.04
    MAX_DELAY = 4.0
    BACKOFF_MULTIPLIER = 2.0
    COOLDOWN_STEP = 0.0
    COOLDOWN_AFTER = 5
    MAX_429_CONSECUTIVE = 5

    BATCH_SIZE = 80
    BATCH_PAUSE_MIN = 1.5
    BATCH_PAUSE_MAX = 3.0


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


class TrackerConfig:
    FLUSH_DELAY = 4.0
    MESSAGE_BATCH_MAX = 20
    BUFFER_MAX_CHANNELS = 100
    MSG_CACHE_MAXLEN = 1_000
    WEBHOOK_QUEUE_MAXLEN = 250
    WEBHOOK_RETRY_ATTEMPTS = 3
    SUBSCRIPTION_INTERVAL_SECONDS = 300.0
    PRESENCE_COALESCE_DELAY = 1.0
    PRESENCE_SNAPSHOT_TTL = 300.0
    OFFLINE_DEBOUNCE = 12.0
    PROFILE_FETCH_THROTTLE = 900.0
    TYPING_THROTTLE = 15.0
    SUBSCRIBE_BATCH_SIZE = 10
    SUBSCRIBE_BATCH_PAUSE = 0.5
    WS_RATELIMIT_PAUSE = 5.0


class PresenceConfig:
    APP_ID = 1466853141508260093
    ASSET_LOGO = "madman_logo"


class QuestConfig:
    VIDEO_REPORT_MIN_SECONDS = 6.0
    VIDEO_REPORT_MAX_SECONDS = 8.0
    # Questify only permits a future-video leeway in its explicit risky mode.
    # Madman has no risky mode, so progress never runs ahead of wall-clock time.
    MAX_FUTURE_SECONDS = 0.0
    HEARTBEAT_MAX_INTERVAL_SECONDS = 60.0
    HEARTBEAT_BUFFER_SECONDS = 1.0
    REQUEST_RETRY_ATTEMPTS = 3
    REQUEST_RETRY_DELAY_SECONDS = 2.5
    MAX_STAGNANT_HEARTBEATS = 3
    COMPLETION_VERIFY_DELAY_SECONDS = 1.0


class ProtectionConfig:
    # Uma rajada de varias contas e uma rajada de uma unica conta sao
    # problemas diferentes. Janelas separadas impedem agir sobre a DM errada.
    DM_RAID_WINDOW = 300
    DM_RAID_MAX_UNIQUE = 10
    DM_BURST_WINDOW = 10
    DM_BURST_MAX_MESSAGES = 8
    INVITE_SPAM_WINDOW = 120
    INVITE_SPAM_MAX_MESSAGES = 3
    FR_FLOOD_WINDOW = 600
    FR_FLOOD_MAX_COUNT = 5
    ACTION_COOLDOWN = 300
    MAX_TRACKED_AUTHORS = 500
    MAX_TRACKED_MESSAGES = 2_000
    MAX_WHITELIST_USERS = 150

    # Um TLD incomum, sozinho, nao prova que um link e malicioso. Esta lista
    # so aumenta a confianca quando tambem existe linguagem de golpe.
    SUSPICIOUS_TLDS = (
        ".xyz",
        ".tk",
        ".ml",
        ".ga",
        ".cf",
        ".gq",
        ".top",
        ".buzz",
        ".click",
        ".link",
        ".live",
        ".icu",
        ".ru",
        ".cn",
    )

    SAFE_DOMAINS = {
        "discord.com",
        "discord.gg",
        "discordapp.com",
        "tenor.com",
        "giphy.com",
        "cdn.discordapp.com",
        "media.discordapp.net",
        "media.discordapp.com",
        "images-ext-1.discordapp.net",
        "images-ext-2.discordapp.net",
        "imgur.com",
        "youtube.com",
        "youtu.be",
        "twitch.tv",
        "twitter.com",
        "x.com",
        "reddit.com",
        "spotify.com",
        "github.com",
        "google.com",
        "steamcommunity.com",
        "steampowered.com",
    }

    DANGEROUS_ATTACHMENT_EXTENSIONS = (
        ".apk",
        ".bat",
        ".cmd",
        ".com",
        ".exe",
        ".hta",
        ".iso",
        ".jar",
        ".js",
        ".lnk",
        ".msi",
        ".ps1",
        ".scr",
        ".vbs",
    )

    BRAND_IMPERSONATION_PATTERNS = (
        re.compile(r"d[i1l!]sc[o0]rd[\-\.]?(?:app|gift|nitro)", re.IGNORECASE),
        re.compile(r"st[e3][a4]m\s*c[o0]mmun[i1]ty", re.IGNORECASE),
    )

    SCAM_LURE_PATTERNS = (
        re.compile(r"(?:free|claim).{0,20}n[i1]tr[o0]", re.IGNORECASE),
        re.compile(
            r"n[i1]tr[o0].{0,20}(?:gratis|gratuito|resgatar|presente)",
            re.IGNORECASE,
        ),
        re.compile(r"(?:cs|tf2|dota).{0,15}(?:trade|skin|item)", re.IGNORECASE),
        re.compile(r"(?:epicgames|roblox).{0,10}(?:free|gift|promo)", re.IGNORECASE),
        re.compile(
            r"(?:scan|escane(?:ie|ar)|verify|verificar|claim|resgatar)"
            r".{0,30}(?:qr|account|conta|gift|presente)",
            re.IGNORECASE,
        ),
    )
