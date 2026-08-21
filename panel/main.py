import os
import sys

from engine.backend import SelfbotManager
from .config import TOKEN_BOT_CONTROLE
from .bot import ControlBot


def keep_alive():
    try:
        from flask import Flask

        app = Flask("keep_alive")

        @app.route("/")
        def home():
            return "Madman Bot is running!"

        def run_server():
            raw_port = os.environ.get("PORT", "8080").strip()
            try:
                port = int(raw_port)
            except ValueError:
                print(f"⚠️ PORT inválida: {raw_port!r}. Keep-alive desabilitado.")
                return
            if not 1 <= port <= 65535:
                print(f"⚠️ PORT fora do intervalo: {port}. Keep-alive desabilitado.")
                return
            import logging

            log = logging.getLogger("werkzeug")
            log.setLevel(logging.ERROR)
            app.run(host="0.0.0.0", port=port)

        import threading

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()
        print("🌍 Keep-alive web server iniciado. (Render Port Bind OK)")
    except ImportError:
        print("⚠️ Flask não instalado. Keep-alive desabilitado.")


def main():
    if not TOKEN_BOT_CONTROLE:
        print("❌ Token do bot controle não configurado!")
        sys.exit(1)

    manager = SelfbotManager()
    bot = ControlBot(manager)

    keep_alive()

    bot.run(TOKEN_BOT_CONTROLE)


if __name__ == "__main__":
    main()
