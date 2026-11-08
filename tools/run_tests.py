"""Run deterministic isolated tests; never run live-model evaluations."""
from pathlib import Path
import os
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SUITE = (
    'test_core', 'test_analysis_reply', 'test_answer_revision', 'test_flow_contract',
    'test_policy_profiles', 'test_product_experience', 'test_process_generation',
    'test_protocol_faults', 'test_member_views', 'test_auto_image',
    'test_dialogue_history', 'test_investment_math', 'test_warm_transport',
    'test_architecture', 'test_generation_pool', 'test_fairness_health',
    'test_group_burst', 'test_group_summary', 'test_image_quality',
    'test_public_feed', 'test_public_pages', 'test_public_research',
    'test_futu_evidence', 'test_eval_assertions',
    'test_native_sender', 'test_resilience',
    'test_memory_controls_copy', 'test_latency_contract',
    'test_reliability_audit', 'test_supervisor_recovery',
    'test_conversation_floor', 'test_capability_context',
    'test_conversation_citations', 'test_memory_retrieval', 'test_release_checks',
)


def main():
    for folder in ('assistant', 'tests', 'evaluations'):
        sys.path.insert(0, str(ROOT / folder))
        (ROOT / folder / 'runtime').mkdir(exist_ok=True)
    scratch = ROOT / 'tests/runtime'
    os.environ['TEMP'] = os.environ['TMP'] = str(scratch)
    tempfile.tempdir = str(scratch)
    os.chdir(ROOT / 'assistant')
    tests = unittest.defaultTestLoader.loadTestsFromNames(SUITE)
    result = unittest.TextTestRunner(verbosity=1).run(tests)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
