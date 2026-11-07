"""Immutable local evaluation run directories with source fingerprints only."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile
from generation_pool import GENERATION_WORKERS

ROOT=Path(__file__).resolve().parents[1]/'assistant'
EVALUATIONS=Path(__file__).resolve().parent
SOURCES=('dialogue_history.py','context_budget.py','member_views.py','member_window.py','core.py','codex_client.py','evidence.py','futu_evidence.py','public_feed.py','public_pages.py','public_research.py','outbound_images.py','generation_pool.py',
         'runtime_health.py','eval_assertions.py','group20_eval.py','staged_eval.py')

def create_run(kind,cases,base=None):
    if not re.fullmatch(r'[a-z0-9-]{1,40}',kind):raise ValueError('Invalid evaluation name')
    parent=ROOT/'runtime' if base is None else Path(base)
    parent.mkdir(parents=True,exist_ok=True)
    now=datetime.now(timezone.utc)
    target=Path(tempfile.mkdtemp(prefix=kind+'-'+now.strftime('%Y%m%d-%H%M%S')+'-',dir=parent))
    manifest={'created_at':now.isoformat(),'kind':kind,'real_group_send':False,
              'model':'gpt-6-astra','effort':'low','model_timeout_seconds':150,'configured_generation_workers':GENERATION_WORKERS,
              'cases':[{'id':c[0],'question_sha256':hashlib.sha256(c[1].encode()).hexdigest()} for c in cases],
              'source_sha256':{name:hashlib.sha256((ROOT/name if (ROOT/name).is_file() else EVALUATIONS/name).read_bytes()).hexdigest() for name in SOURCES}}
    (target/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return target
