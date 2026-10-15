"""Platform-specific executable names for the RISC-V toolchain."""
import os


def executable(name):
    stem = name[:-4] if name.lower().endswith('.exe') else name
    return stem + '.exe' if os.name == 'nt' else stem
