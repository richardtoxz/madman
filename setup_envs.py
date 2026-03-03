import subprocess
import os
import sys


def install_libs():
    print("Installing official discord.py for the Control Bot...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "discord.py", "-t", "panel/lib"]
    )

    print("\nInstalling discord.py-self for the Selfbot...")

    subprocess.run(
        [sys.executable, "-m", "pip", "install", "discord.py-self", "-t", "worker/lib"]
    )

    print("\nConcluído! Use 'python panel/main.py' para iniciar.")

    print("\nPatching Python 3.13 audioop compatibility...")
    patch_audioop()


def patch_audioop():
    paths = ["panel/lib/discord/player.py", "worker/lib/discord/player.py"]
    for p in paths:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                content = f.read()

            if (
                "import audioop" in content
                and "try:\n    import audioop" not in content
            ):
                content = content.replace(
                    "import audioop",
                    "try:\n    import audioop\nexcept ImportError:\n    audioop = None",
                )
                with open(p, "w", encoding="utf-8") as f:
                    f.write(content)
                print(f"Patched: {p}")


if __name__ == "__main__":
    install_libs()
