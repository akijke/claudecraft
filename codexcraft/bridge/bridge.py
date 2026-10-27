"""Separate Codexcraft bridge; shared core is assembled by :codexcraft:generateShared."""
import core
from codex_bridge import CodexBridge

core.Bridge = CodexBridge

if __name__ == '__main__':
    core.main()
