"""Focused L4 panel regression; block network and use isolated test ledgers."""
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

if __name__ == '__main__':
    def denied(*args,**kwargs):
        raise AssertionError('NETWORK_FORBIDDEN_IN_L4_OFFLINE_TESTS')
    suite = unittest.defaultTestLoader.discover('tests/agent_l4',pattern='test_*.py')
    with patch.object(socket.socket,'connect',denied),patch.object(socket,'create_connection',denied):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
