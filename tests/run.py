"""Run all tests:  tools\\python-env\\Scripts\\python.exe -m tests.run  [-k pattern]"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

if __name__ == '__main__':
    argv = [sys.argv[0], 'discover', '-s', str(ROOT / 'tests'), '-t', str(ROOT), '-p', 'test_*.py'] + sys.argv[1:]
    unittest.main(module=None, argv=argv)
