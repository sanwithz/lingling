---
description: Turn the voice secretary back on
---
Run this to turn the voice back on, then report the result in one short line:

```bash
# On Windows python3 is usually a Microsoft Store stub that fails on --version,
# so probe in this order and take the first one that actually answers.
if python3 --version >/dev/null 2>&1; then PY=python3; elif python --version >/dev/null 2>&1; then PY=python; elif py --version >/dev/null 2>&1; then PY=py; else echo "No usable Python found. Install Python 3.9+ from python.org, then reopen every terminal window."; exit 1; fi
PYTHONUTF8=1 "$PY" -c "
import json,pathlib
p=pathlib.Path.home()/'.claude'/'thai-secretary.json'
c=json.loads(p.read_text()) if p.exists() else {}
c['enabled']=True
p.parent.mkdir(parents=True,exist_ok=True)
p.write_text(json.dumps(c,ensure_ascii=False,indent=2))
print('Voice secretary unmuted.')"
```
