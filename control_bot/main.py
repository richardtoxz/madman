import os
import sys

# Adiciona o diretório da biblioteca oficial ao path
lib_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
sys.path.insert(0, lib_path)

# Adiciona o diretório pai ao path para importar core.backend
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.backend import SelfbotManager
from config import TOKEN_BOT_CONTROLE
from bot import ControlBot


def main():
    """Entry point principal do bot de controle."""
    if not TOKEN_BOT_CONTROLE:
        print("❌ TOKEN_BOT_CONTROLE não configurado! Verifique o arquivo .env")
        sys.exit(1)

    manager = SelfbotManager()
    bot = ControlBot(manager)
    bot.run(TOKEN_BOT_CONTROLE)


if __name__ == "__main__":
    main()
