import os
import sys

lib_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
sys.path.insert(0, lib_path)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.backend import SelfbotManager
from config import TOKEN_BOT_CONTROLE
from bot import ControlBot


def main():
    if not TOKEN_BOT_CONTROLE:
        print("❌ Token do bot controle não configurado!")
        sys.exit(1)

    manager = SelfbotManager()
    bot = ControlBot(manager)
    bot.run(TOKEN_BOT_CONTROLE)


if __name__ == "__main__":
    main()
