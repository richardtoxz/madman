import subprocess
import os
import sys


def install_libs():
    print("Installing official discord.py for the Control Bot...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "discord.py", "-t", "panel/lib"]
    )

    print("\nInstalling discord.py-self for the Selfbot...")
    # Precisamos desinstalar a discord.py do ambiente principal se houver conflito,
    # ou apenas instalar em pastas separadas.
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "discord.py-self", "-t", "worker/lib"]
    )

    print("\nConcluído! Use 'python panel/main.py' para iniciar.")


if __name__ == "__main__":
    install_libs()
