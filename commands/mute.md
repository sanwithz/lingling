---
description: ปิดเสียงเลขาส่วนตัวชั่วคราว
---
รันคำสั่งนี้เพื่อปิดเสียง แล้วรายงานผลสั้นๆ:

```bash
if python3 --version >/dev/null 2>&1; then PY=python3; elif python --version >/dev/null 2>&1; then PY=python; else echo "ไม่พบ Python ที่ใช้งานได้ (ลองปิดเทอร์มินัลทั้งหมดแล้วเปิดใหม่)"; exit 1; fi
PYTHONUTF8=1 "$PY" -c "
import json,pathlib
p=pathlib.Path.home()/'.claude'/'thai-secretary.json'
c=json.loads(p.read_text()) if p.exists() else {}
c['enabled']=False
p.parent.mkdir(parents=True,exist_ok=True)
p.write_text(json.dumps(c,ensure_ascii=False,indent=2))
print('ปิดเสียงแล้ว')"
```
