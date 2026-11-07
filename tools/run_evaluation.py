"""Explicit opt-in runner for synthetic live-model evaluations."""
import argparse
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = ('evaluate_questions', 'eval_acceptance_polish', 'eval_policy_acceptance',
           'eval_context_upgrade', 'eval_member_views', 'eval_valuation_latency',
           'eval_outbound_images', 'evaluate_person_context', 'investment_eval',
           'group20_eval', 'staged_eval', 'futu_eval', 'lifestyle_eval')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name', choices=ALLOWED, nargs='?')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--allow-model-call', action='store_true')
    args = parser.parse_args()
    if args.list:
        print('\n'.join(ALLOWED))
        return
    if not args.name or not args.allow_model_call:
        parser.error('Choose a name and explicitly add --allow-model-call. This uses your model account and quota.')
    for folder in ('assistant', 'evaluations'):
        sys.path.insert(0, str(ROOT / folder))
        (ROOT / folder / 'runtime').mkdir(exist_ok=True)
    os.chdir(ROOT / 'assistant')
    sys.argv = [args.name]
    runpy.run_path(str(ROOT / 'evaluations' / (args.name + '.py')), run_name='__main__')


if __name__ == '__main__':
    main()
