import ast,hashlib,hmac,json,math,re,time,types
from urllib.parse import urlencode
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'apps/danmu/ass_support.py'
tree=ast.parse(p.read_text(encoding='utf-8'));names=['signed_headers','identifier','font_ticket']
ns={'re':re,'math':math,'hashlib':hashlib,'hmac':hmac,'json':json,'time':time,'urlencode':urlencode,'EVENT_FORMAT':'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text'}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),str(p),'exec'),ns)
try:ns['identifier']('../private');raise AssertionError('invalid ID accepted')
except ValueError:pass
assert ns['font_ticket']('a','font-1.ttf',b'fixture',100)!=ns['font_ticket']('a','font-1.ttf',b'fixture',101)
assert 'Authorization' in ns['signed_headers']('fixture','GET','/api/v1/user/info')
print('PASS: API signing, identifiers and scoped font tickets; canceled export route absent')
assert '/export-ass' not in p.read_text(encoding='utf-8')
