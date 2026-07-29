import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rag", "eval"))

def test_rag_metrics_selftest_passes():
    import rag_metrics
    assert rag_metrics.selftest() == 0
