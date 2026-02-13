# --- CONFIGURAÇÕES DO BOT ---
import os

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except ImportError:
    pass  # python-dotenv não instalado, usa apenas os.environ

TOKEN_BOT_CONTROLE = os.environ.get('TOKEN_BOT_CONTROLE', '')
ID_CANAL_SOBRE = int(os.environ.get('ID_CANAL_SOBRE', '0')) or None
ID_CANAL_PAINEL = int(os.environ.get('ID_CANAL_PAINEL', '0')) or None