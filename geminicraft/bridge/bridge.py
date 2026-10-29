"""Separate Geminicraft bridge; the shared core is assembled by :geminicraft:generateShared."""
import core
from gemini_bridge import GeminiBridge

core.Bridge = GeminiBridge

if __name__ == '__main__':
    core.main()
