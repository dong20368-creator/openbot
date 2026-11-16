"""Explicit local selection; failures never silently switch billing/model channels."""
import json
from pathlib import Path
from codex_client import Codex

CONFIG=Path(__file__).resolve().parent/'runtime/model-provider.json'


def selection():
    if not CONFIG.exists():return {'provider':'codex','model':'gpt-6-astra'}
    value=json.loads(CONFIG.read_text(encoding='utf-8-sig'))
    if value not in ({'provider':'codex','model':'gpt-6-astra'},
                     {'provider':'antigravity','model':'gemini-4-argon-medium'}):
        raise ValueError('invalid_model_provider_config')
    return value


def create_model(**kwargs):
    config=selection()
    if config['provider']=='antigravity':
        from argon_client import Argon
        return Argon(model=config['model'],**kwargs)
    return Codex(model=config['model'],**kwargs)
