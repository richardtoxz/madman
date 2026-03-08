"""Selfbot Runner — executa selfbots individuais como sub-processos."""

from pathlib import Path
import sys


_LIB_PATH = Path(__file__).resolve().parent / "lib"

if _LIB_PATH.is_dir():
    lib_path = str(_LIB_PATH)
    if lib_path not in sys.path:
        sys.path.insert(0, lib_path)
