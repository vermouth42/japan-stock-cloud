"""Validate one versioned, ordered 100-symbol universe for a completed session."""
import hashlib,json,re
from datetime import date
from pathlib import Path

def load_universe(path='watchlist.json',expected=None):
    x=json.loads(Path(path).read_text(encoding='utf-8'))
    if x.get('schema_version')!=1:raise ValueError('Unsupported universe schema')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',x.get('version','')):raise ValueError('Invalid version')
    effective=date.fromisoformat(x['effective_from'])
    selected=date.fromisoformat(x['selection_asof'])
    if selected>effective:raise ValueError('Selection follows effective session')
    if expected is not None and effective>date.fromisoformat(expected):raise ValueError('Future universe')
    codes=x.get('codes')
    if not isinstance(codes,list) or len(codes)!=100:raise ValueError('Expected 100 symbols')
    if any(not isinstance(c,str) or re.fullmatch(r'(?:[0-9]{4}|[0-9]{3}[A-Z])',c) is None for c in codes):raise ValueError('Invalid security code')
    if len(set(codes))!=100:raise ValueError('Expected 100 unique symbols')
    return x

def universe_hash(x):
    return hashlib.sha256(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def save_snapshot(x,expected,directory='universes'):
    root=Path(directory);root.mkdir(parents=True,exist_ok=True)
    path=root/(expected+'_'+x['version']+'.json')
    content=json.dumps(x,ensure_ascii=False,indent=2)+'\n'
    for prior in root.glob('*_'+x['version']+'.json'):
        if prior.read_text(encoding='utf-8')!=content:raise ValueError('Refuse changing historical universe version')
    path.write_text(content,encoding='utf-8');return str(path)
