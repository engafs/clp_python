import unittest
import sys
from pathlib import Path

def run_all_tests():
    root_dir = Path(__file__).resolve().parent
    src_dir = str(root_dir / "src")
    
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
        sys.path.insert(0, str(root_dir))

    print("="*60)
    print("🚀 INITIATING TEST SUITE FOR CLP_PYTHON SYSTEM")
    print(f"📂 Project Root: {root_dir}")
    print("="*60)

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir='tests', pattern='test_*.py')

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "="*60)
    print("📊 TEST SUMMARY")
    print(f"✅ Passes: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"❌ Failures: {len(result.failures)}")
    print(f"⚠️ Errors: {len(result.errors)}")
    print("="*60)

    # Exit with non-zero code if there were failures or errors
    if not result.wasSuccessful():
        sys.exit(1)

if __name__ == "__main__":
    run_all_tests()