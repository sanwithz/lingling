#!/usr/bin/env python3
"""
Thai Secretary — สรุปผลงานของ Claude เป็นภาษาไทยแบบคนคุยกัน แล้วอ่านออกเสียงให้ฟัง

รับ JSON ของ hook ทาง stdin แล้ว:
  --mode stop    : ดึง last_assistant_message -> สรุปเป็นไทย -> อ่านออกเสียง
  --mode notify  : พูดแจ้งเตือนสั้นๆ ตอน Claude รอ permission / รออินพุต
  --mode test    : ทดสอบเสียงอย่างเดียว (ไม่ต้องมี stdin)

ออกแบบให้ "ห้ามพัง": ทุก error จะถูกกลืนแล้ว exit 0 เสมอ
เพื่อไม่ให้ session ของ Claude Code สะดุดเพราะ hook
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

DEFAULTS = {
    "enabled": True,
    # auto | say | google | azure | edge | espeak | none
    "voice_engine": "auto",
    # auto | api | cli | none   (none = อ่านข้อความดิบโดยไม่สรุป)
    "summarizer": "auto",
    "model": "claude-haiku-4-5-20251001",
    "min_chars": 180,          # ข้อความสั้นกว่านี้ไม่ต้องสรุป อ่านเลย
    "skip_under_chars": 40,    # สั้นมากๆ ไม่ต้องพูดเลย
    "max_input_chars": 8000,   # กันค่าใช้จ่ายบานปลาย
    "max_spoken_chars": 600,
    "say_voice": "Kanya",
    "say_rate": 190,
    "google_voice": "th-TH-Neural2-C",
    "azure_voice": "th-TH-PremwadeeNeural",
    "azure_region": "southeastasia",
    "edge_voice": "th-TH-PremwadeeNeural",
    "speaking_rate": 0.85,
    "notify_sounds": True,
    "log": "~/.claude/thai-secretary.log",
}

SYSTEM_PROMPT = """คุณคือเลขาส่วนตัวที่กำลังรายงานผลงานให้เจ้านายฟังด้วยเสียง

สรุปสิ่งที่ AI agent เพิ่งทำเสร็จ เป็นภาษาไทยแบบพูดคุย 2-3 ประโยค

กฎ:
- เขียนแบบที่ "อ่านออกเสียงแล้วฟังรู้เรื่อง" ห้ามมี markdown, bullet, emoji, หรือเครื่องหมายพิเศษ
- ห้ามอ่านโค้ด ชื่อไฟล์ยาวๆ path หรือ URL ให้เรียกรวมๆ เช่น "แก้ไฟล์ config" "อัปเดตสามไฟล์"
- ศัพท์เทคนิคที่คนไทยใช้ทับศัพท์อยู่แล้ว ให้ทับศัพท์ไปเลย อย่าแปลเป็นไทยแข็งๆ
- ห้ามสะกดชื่อเฉพาะภาษาอังกฤษที่ไม่คุ้นหู (ชื่อโปรแกรม ปลั๊กอิน ไลบรารี) เป็นคำไทยเดาสุ่ม
  ถ้าจำเป็นต้องพูดถึง ให้เรียกลอยๆ เช่น "ปลั๊กอินตัวนี้" "ระบบที่ใช้อยู่" แทนชื่อเต็ม
- ถ้าต้องพูดถึงเครื่องหมายวรรคตอน ให้ใช้คำพูดทั่วไปแบบคนคุยกัน เช่น "เว้นจังหวะ" "คั่นจังหวะ"
  ห้ามใช้ศัพท์ทางการเช่น จุลภาค มหัพภาค ทวิภาค
- ตอบให้ครบ 3 อย่าง: ทำอะไรเสร็จ / ผลเป็นยังไง / ต้องทำอะไรต่อ (ถ้าไม่มีข้อไหนก็ข้าม)
- ถ้ามีปัญหาหรือ error ให้บอกตรงๆ เป็นอย่างแรก อย่าเออออตาม
- ห้ามขึ้นต้นว่า "สรุปคือ" หรือ "จากข้อความ" ให้พูดผลลัพธ์เลย
- แบ่งจังหวะให้เป็นธรรมชาติ: จบแต่ละประโยคด้วยจุด (.) และคั่นแต่ละวลีย่อยด้วยจุลภาค (,)
  อย่าเขียนเป็นประโยคยาวพรืดไม่มีจุดหยุดพัก ประมาณ 5-8 คำต่อวลีก็เว้นจังหวะทีนึง

ตอบเฉพาะข้อความที่จะอ่านออกเสียง ไม่ต้องมีอะไรอื่น"""

NOTIFY_MESSAGES = {
    "permission_prompt": "คล็อดขออนุญาตรันคำสั่ง รบกวนกดยืนยันด้วยครับ",
    "idle_prompt": "คล็อดรออินพุตจากคุณอยู่ครับ",
    "agent_needs_input": "เอเจนต์ต้องการข้อมูลเพิ่มครับ",
    "agent_completed": "เอเจนต์ทำงานเสร็จแล้วครับ",
    "elicitation_dialog": "มีหน้าต่างขอข้อมูลรออยู่ครับ",
}


# ---------------------------------------------------------------- config

def config_path() -> Path:
    env = os.environ.get("THAI_SECRETARY_CONFIG")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".claude" / "thai-secretary.json"


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    path = config_path()
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            pass
    # env override: THAI_SECRETARY_<KEY>
    for key in DEFAULTS:
        env_val = os.environ.get("THAI_SECRETARY_" + key.upper())
        if env_val is None:
            continue
        default = DEFAULTS[key]
        try:
            if isinstance(default, bool):
                cfg[key] = env_val.strip().lower() in ("1", "true", "yes", "on")
            elif isinstance(default, int):
                cfg[key] = int(env_val)
            elif isinstance(default, float):
                cfg[key] = float(env_val)
            else:
                cfg[key] = env_val
        except Exception:
            pass
    return cfg


def log(cfg: dict, message: str) -> None:
    try:
        path = Path(cfg.get("log", DEFAULTS["log"])).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


# ---------------------------------------------------------------- text prep

FENCE_RE = re.compile(r"```.*?```", re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
URL_RE = re.compile(r"https?://\S+")
PATH_RE = re.compile(r"(?:[\w.-]+/){2,}[\w.-]+")
MD_MARKS_RE = re.compile(r"[*_#>|`]+")


def strip_markup(text: str) -> str:
    """เอา noise ที่อ่านออกเสียงแล้วทรมานหูออก"""
    text = FENCE_RE.sub(" (มีโค้ด) ", text)
    text = INLINE_CODE_RE.sub(lambda m: m.group(0).strip("`"), text)
    text = LINK_RE.sub(r"\1", text)
    text = URL_RE.sub(" ลิงก์ ", text)
    text = PATH_RE.sub(" ไฟล์ ", text)
    text = MD_MARKS_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


PACE_WORDS_RE = re.compile(
    r"(ครับ|ค่ะ|ค่า|จ้ะ|จ้า|นะครับ|นะคะ|แล้ว|เลย)(?!\s*[,.!?ๆฯ])\s+(?=\S)"
)
# เลขหัวข้อแบบ "1. " "2) " ที่จุดเริ่มหรือหลังช่องว่าง (ไม่ชนเลขทศนิยม/เวอร์ชันเพราะ
# ต้องมีช่องว่างตามหลังจุด/วงเล็บ ส่วน "3.14" ตามด้วยตัวเลขไม่ใช่ช่องว่าง)
NUMBERED_LIST_RE = re.compile(r"(?:^|\s)\d{1,2}[.)]\s+")


def clean_for_speech(text: str, limit: int) -> str:
    text = strip_markup(text)
    text = re.sub(r"[\[\](){}<>]", " ", text)
    text = NUMBERED_LIST_RE.sub(", ", text)           # เลขหัวข้อ -> เว้นจังหวะ
    text = re.sub(r"(?:^|\s)[-–—•]\s+", ", ", text)   # bullet -> เว้นจังหวะ
    # กันประโยคพูดรวดเดียวไม่มีจังหวะพัก แทรกจุลภาคหลังคำลงท้ายประโยคทั่วไป
    # ที่ยังไม่มีเครื่องหมายวรรคตอนตามหลัง (เผื่อ summarizer ลืมเว้นจังหวะเอง)
    text = PACE_WORDS_RE.sub(r"\1, ", text)
    text = re.sub(r"\s*,\s*,+", ", ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,")
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + " ..."
    return text


# ---------------------------------------------------------------- summarize

def summarize_via_api(text: str, cfg: dict) -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    payload = json.dumps({
        "model": cfg["model"],
        "max_tokens": 400,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": text}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        parts = [b.get("text", "") for b in body.get("content", []) if b.get("type") == "text"]
        out = "\n".join(parts).strip()
        return out or None
    except Exception as exc:
        log(cfg, f"api summarize failed: {exc}")
        return None


def summarize_via_cli(text: str, cfg: dict) -> str | None:
    """ใช้ auth เดิมของ Claude Code โดยไม่ต้องมี API key

    สำคัญ: ต้องส่ง disableAllHooks ไม่งั้น session ลูกจะยิง Stop hook
    ซ้อนกลับมาเป็น loop ไม่รู้จบ
    """
    claude = shutil.which("claude")
    if not claude:
        return None
    prompt = SYSTEM_PROMPT + "\n\n---\n\nข้อความที่ต้องสรุป:\n\n" + text
    try:
        proc = subprocess.run(
            [claude, "-p", prompt,
             "--model", "haiku",
             "--settings", '{"disableAllHooks": true}'],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
            env={**os.environ, "THAI_SECRETARY_ENABLED": "false"},
        )
        out = (proc.stdout or "").strip()
        return out or None
    except Exception as exc:
        log(cfg, f"cli summarize failed: {exc}")
        return None


def summarize(text: str, cfg: dict) -> str:
    mode = cfg.get("summarizer", "auto")
    if mode == "none":
        return text
    if mode in ("auto", "api"):
        out = summarize_via_api(text, cfg)
        if out:
            return out
        if mode == "api":
            return text
    if mode in ("auto", "cli"):
        out = summarize_via_cli(text, cfg)
        if out:
            return out
    log(cfg, "no summarizer available, falling back to raw text")
    return text


# ---------------------------------------------------------------- tts

def pick_engine(cfg: dict) -> str:
    engine = cfg.get("voice_engine", "auto")
    if engine != "auto":
        return engine
    if os.environ.get("GOOGLE_TTS_API_KEY"):
        return "google"
    if os.environ.get("AZURE_SPEECH_KEY"):
        return "azure"
    if sys.platform == "darwin" and shutil.which("say"):
        return "say"
    if shutil.which("edge-tts"):
        return "edge"
    if sys.platform == "win32":
        return "powershell"
    if shutil.which("spd-say") or shutil.which("espeak-ng"):
        return "espeak"
    return "none"


def _play_audio_windows(path: str) -> bool:
    """เล่นไฟล์เสียงบน Windows ผ่าน MCI (winmm.dll)

    ไม่มี afplay/mpg123/ffplay/paplay/aplay บน Windows โดย default
    ลองใช้ WMPlayer.OCX ผ่าน PowerShell ก่อนแต่ playState ค้างที่ "waiting"
    เพราะ ActiveX control ต้องมี Windows message loop คอยขยับ state ซึ่ง
    PowerShell console เปล่าไม่มีให้ จึงเปลี่ยนมาใช้ mciSendString ซึ่งเล่นและ
    บล็อกจนจบเพลงได้เองโดยไม่ต้องพึ่ง message loop ภายนอก (คำสั่ง "play ... wait")
    """
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if not ps:
        return False
    safe_path = path.replace("`", "``").replace('"', '`"')
    script = (
        "Add-Type -TypeDefinition '"
        "using System; using System.Runtime.InteropServices; using System.Text;"
        "public class MciPlayer {"
        "[DllImport(\"winmm.dll\")]"
        "public static extern long mciSendString(string cmd, StringBuilder ret, int retLen, IntPtr h);"
        "}';"
        "$sb = New-Object System.Text.StringBuilder 256;"
        f'[MciPlayer]::mciSendString(\'open "{safe_path}" type mpegvideo alias secline\', $sb, 256, [IntPtr]::Zero) | Out-Null;'
        "[MciPlayer]::mciSendString('play secline wait', $sb, 256, [IntPtr]::Zero) | Out-Null;"
        "[MciPlayer]::mciSendString('close secline', $sb, 256, [IntPtr]::Zero) | Out-Null;"
    )
    try:
        result = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
            capture_output=True, timeout=60,
        )
        return result.returncode == 0
    except Exception:
        return False


def play_audio(path: str, cfg: dict) -> None:
    for player in (["afplay", path], ["mpg123", "-q", path],
                   ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
                   ["paplay", path], ["aplay", "-q", path]):
        if shutil.which(player[0]):
            subprocess.run(player, capture_output=True)
            return
    if sys.platform == "win32" and _play_audio_windows(path):
        return
    log(cfg, "no audio player found (ลอง brew/apt install mpg123)")


def tts_google(text: str, cfg: dict) -> bool:
    key = os.environ.get("GOOGLE_TTS_API_KEY")
    if not key:
        return False
    payload = json.dumps({
        "input": {"text": text},
        "voice": {"languageCode": "th-TH", "name": cfg["google_voice"]},
        "audioConfig": {"audioEncoding": "MP3", "speakingRate": cfg["speaking_rate"]},
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://texttospeech.googleapis.com/v1/text:synthesize?key=" + key,
        data=payload, headers={"content-type": "application/json"}, method="POST")
    try:
        import base64
        with urllib.request.urlopen(req, timeout=25) as resp:
            audio = json.loads(resp.read().decode("utf-8"))["audioContent"]
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.write(base64.b64decode(audio))
        tmp.close()
        play_audio(tmp.name, cfg)
        os.unlink(tmp.name)
        return True
    except Exception as exc:
        log(cfg, f"google tts failed: {exc}")
        return False


def tts_azure(text: str, cfg: dict) -> bool:
    key = os.environ.get("AZURE_SPEECH_KEY")
    if not key:
        return False
    region = cfg["azure_region"]
    ssml = (
        f'<speak version="1.0" xml:lang="th-TH">'
        f'<voice name="{cfg["azure_voice"]}">'
        f'<prosody rate="{int((cfg["speaking_rate"] - 1) * 100):+d}%">{text}</prosody>'
        f"</voice></speak>"
    )
    req = urllib.request.Request(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
        data=ssml.encode("utf-8"),
        headers={
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
        }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = resp.read()
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.write(data)
        tmp.close()
        play_audio(tmp.name, cfg)
        os.unlink(tmp.name)
        return True
    except Exception as exc:
        log(cfg, f"azure tts failed: {exc}")
        return False


def tts_edge(text: str, cfg: dict) -> bool:
    if not shutil.which("edge-tts"):
        return False
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    try:
        rate = f'{int((cfg["speaking_rate"] - 1) * 100):+d}%'
        subprocess.run(["edge-tts", "--voice", cfg["edge_voice"], f"--rate={rate}",
                        "--text", text, "--write-media", tmp.name],
                       capture_output=True, timeout=60)
        play_audio(tmp.name, cfg)
        return True
    except Exception as exc:
        log(cfg, f"edge tts failed: {exc}")
        return False
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


def tts_say(text: str, cfg: dict) -> bool:
    if not shutil.which("say"):
        return False
    cmd = ["say", "-r", str(cfg["say_rate"])]
    voice = cfg.get("say_voice")
    if voice:
        cmd += ["-v", voice]
    try:
        proc = subprocess.run(cmd + [text], capture_output=True, text=True)
        if proc.returncode != 0 and voice:
            # ยังไม่ได้ลงเสียงไทย -> ลองเสียง default
            subprocess.run(["say", text], capture_output=True)
            log(cfg, f"voice '{voice}' not installed — "
                     "ลงได้ที่ System Settings > Accessibility > Spoken Content > System Voice > Thai")
        return True
    except Exception as exc:
        log(cfg, f"say failed: {exc}")
        return False


def tts_powershell(text: str, cfg: dict) -> bool:
    safe = text.replace("'", "''")
    script = ("Add-Type -AssemblyName System.Speech; "
              "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              f"$s.Speak('{safe}')")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       capture_output=True, timeout=120)
        return True
    except Exception as exc:
        log(cfg, f"powershell tts failed: {exc}")
        return False


def tts_espeak(text: str, cfg: dict) -> bool:
    if shutil.which("spd-say"):
        subprocess.run(["spd-say", "-l", "th", "-e", text], capture_output=True)
        return True
    if shutil.which("espeak-ng"):
        subprocess.run(["espeak-ng", "-v", "th", text], capture_output=True)
        return True
    return False


ENGINES = {
    "google": tts_google,
    "azure": tts_azure,
    "edge": tts_edge,
    "say": tts_say,
    "powershell": tts_powershell,
    "espeak": tts_espeak,
}


def speak(text: str, cfg: dict) -> None:
    if not text.strip():
        return
    engine = pick_engine(cfg)
    if engine == "none":
        log(cfg, "no tts engine available")
        return
    stop_previous(cfg)
    fn = ENGINES.get(engine)
    if not fn or not fn(text, cfg):
        # ไล่ fallback ตามที่มีในเครื่อง
        for name, alt in ENGINES.items():
            if name != engine and alt(text, cfg):
                return


def _is_our_process(pid: int) -> bool:
    """ยืนยันว่า PID นั้นเป็นสคริปต์ตัวนี้จริง ก่อนจะส่งสัญญาณฆ่า

    PID ถูกนำกลับมาใช้ซ้ำได้ ถ้าไม่เช็คอาจไปฆ่าโปรเซสอื่นของผู้ใช้
    """
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "ignore")
        return "secretary.py" in cmdline
    except Exception:
        pass
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                             capture_output=True, text=True, timeout=5).stdout
        return "secretary.py" in out
    except Exception:
        return False


def stop_previous(cfg: dict) -> None:
    """กันเสียงพูดทับกันเวลาสั่งงานรัวๆ"""
    pidfile = Path.home() / ".claude" / ".thai-secretary.pid"
    try:
        if pidfile.exists():
            old = int(pidfile.read_text().strip())
            if old != os.getpid() and _is_our_process(old):
                os.kill(old, 15)
    except Exception:
        pass
    try:
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        pidfile.write_text(str(os.getpid()))
    except Exception:
        pass


# ---------------------------------------------------------------- modes

def read_stdin_json() -> dict:
    """อ่าน stdin เป็น UTF-8 เสมอ

    sys.stdin.read() แบบ text-mode ปกติจะถอดรหัสด้วย locale encoding ของ
    Windows (เช่น cp1252/cp874) ไม่ใช่ UTF-8 ทำให้ข้อความไทยที่ Claude Code
    ส่งเข้ามาทาง pipe เพี้ยนตั้งแต่จุดรับ ต้องอ่านจาก buffer แล้ว decode เอง
    """
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def mode_stop(cfg: dict) -> None:
    data = read_stdin_json()
    text = (data.get("last_assistant_message") or "").strip()
    if not text:
        log(cfg, "stop: no last_assistant_message")
        return
    cleaned = strip_markup(text)
    if len(cleaned) < cfg["skip_under_chars"]:
        return
    if len(cleaned) >= cfg["min_chars"]:
        spoken = summarize(cleaned[: cfg["max_input_chars"]], cfg)
    else:
        spoken = cleaned
    spoken = clean_for_speech(spoken, cfg["max_spoken_chars"])
    log(cfg, f"stop -> {spoken}")
    speak(spoken, cfg)


def mode_notify(cfg: dict) -> None:
    if not cfg.get("notify_sounds", True):
        return
    data = read_stdin_json()
    kind = data.get("notification_type") or data.get("matcher") or ""
    message = NOTIFY_MESSAGES.get(kind)
    if not message:
        raw = (data.get("message") or "").strip()
        message = clean_for_speech(raw, 160) if raw else "คล็อดต้องการความสนใจจากคุณครับ"
    log(cfg, f"notify({kind}) -> {message}")
    speak(message, cfg)


def mode_test(cfg: dict) -> None:
    engine = pick_engine(cfg)
    print(f"engine        : {engine}")
    print(f"summarizer    : {cfg['summarizer']}"
          f" (api key: {'yes' if os.environ.get('ANTHROPIC_API_KEY') else 'no'},"
          f" claude cli: {'yes' if shutil.which('claude') else 'no'})")
    print(f"config        : {config_path()}")
    print(f"log           : {Path(cfg['log']).expanduser()}")
    speak("สวัสดีครับ ระบบเลขาส่วนตัวพร้อมทำงานแล้ว ทดสอบเสียงภาษาไทยสำเร็จ", cfg)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["stop", "notify", "test"], default="stop")
    args = parser.parse_args()

    cfg = load_config()
    if not cfg.get("enabled", True) and args.mode != "test":
        return 0
    try:
        {"stop": mode_stop, "notify": mode_notify, "test": mode_test}[args.mode](cfg)
    except Exception as exc:  # hook ห้ามพัง session เด็ดขาด
        log(cfg, f"unhandled error in {args.mode}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
