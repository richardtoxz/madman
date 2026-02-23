"""
Configurações centralizadas para o selfbot runner.
Gerencie todos os timers, rate-limits e constantes comportamentais aqui.
"""
import re


# Flags globais de controle (estado de execução)
control_flags = {
    "started": False,
    "paused": False,
    "cancelled": False,
}


class MessageConfig:
    BASE_DELAY = 0.4
    MIN_DELAY = 0.3
    MAX_DELAY = 4.0
    BACKOFF_MULTIPLIER = 2.0
    COOLDOWN_STEP = 0.10
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
    """Configurações do rastreador de usuários."""
    FLUSH_DELAY = 8.0  # Buffer de mensagens antes de enviar ao IPC
    CONTEXT_MAXLEN = 40  # Histórico de contexto por canal
    MSG_CACHE_MAXLEN = 500  # Cache global de mensagens para edits/deletes
    SUBSCRIPTION_INTERVAL = 20.0  # Minutos entre re-assinaturas de GUILD_SUBSCRIBE
    
    # Throttling de eventos (segundos)
    PRESENCE_THROTTLE = 10.0
    PROFILE_FETCH_THROTTLE = 600.0
    TYPING_THROTTLE = 15.0


class PresenceConfig:
    """Configurações do Rich Presence (Status no Perfil)."""
    APP_ID = 1466853141508260093
    ASSET_LOGO = "madman_logo"


class ProtectionConfig:
    """Configurações da proteção automática (Anti-Scam/Spam)."""
    # Maxlen dos buffers de detecção de flood
    DM_FLOOD_MAXLEN = 50
    FR_FLOOD_MAXLEN = 30
    
    # Janelas de tempo e limites (segundos)
    DM_FLOOD_WINDOW = 300
    DM_FLOOD_MAX_UNIQUE = 10
    
    FR_FLOOD_WINDOW = 600
    FR_FLOOD_MAX_COUNT = 5

    # Regras de conteúdo
    SUSPICIOUS_TLDS = ('.xyz', '.tk', '.ml', '.ga', '.cf', '.gq', '.top', '.buzz')
    
    SAFE_DOMAINS = {
        'tenor.com', 'media.tenor.com', 'c.tenor.com',
        'giphy.com', 'media.giphy.com', 'i.giphy.com',
        'cdn.discordapp.com', 'media.discordapp.net', 'media.discordapp.com',
        'images-ext-1.discordapp.net', 'images-ext-2.discordapp.net',
        'imgur.com', 'i.imgur.com',
        'youtube.com', 'www.youtube.com', 'youtu.be',
        'twitch.tv', 'www.twitch.tv', 'clips.twitch.tv',
        'twitter.com', 'x.com', 'pbs.twimg.com',
        'reddit.com', 'www.reddit.com', 'i.redd.it',
        'spotify.com', 'open.spotify.com',
        'github.com', 'raw.githubusercontent.com',
        'google.com', 'www.google.com',
    }
    
    MEDIA_EXTENSIONS = ('.gif', '.png', '.jpg', '.jpeg', '.webp', '.mp4', '.webm', '.mov')

    SCAM_PATTERNS = [
        re.compile(r'd[i1l!]sc[o0]rd[\-\.]?(?:app|gift|nitro)', re.IGNORECASE),
        re.compile(r'st[e3][a4]m\s*c[o0]mmun[i1]ty', re.IGNORECASE),
        re.compile(r'(?:free|claim).{0,20}n[i1]tr[o0]', re.IGNORECASE),
        re.compile(r'(?:cs|tf2|dota).{0,15}(?:trade|skin|item)', re.IGNORECASE),
        re.compile(r'(?:epicgames|roblox).{0,10}(?:free|gift|promo)', re.IGNORECASE),
    ]

    INVITE_PATTERNS = [
        re.compile(r'discord\.gg/', re.IGNORECASE),
        re.compile(r'discord\.com/invite/', re.IGNORECASE),
        re.compile(r'discord\.com/events/', re.IGNORECASE),
    ]
