"""Compatibility entry point for the shared host audio capture tool."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.tools.audio_capture import *

if __name__ == '__main__': main()
