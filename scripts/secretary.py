#!/usr/bin/env python3
"""
lingling — a bilingual voice secretary: summarises what Claude did, then says it aloud.
Claude answers in Thai -> Thai summary, Thai voice. In English -> English, English voice.

Reads the hook's JSON from stdin, then:
  --mode inject  : ask Claude to end every answer with a one-line spoken summary
                   (UserPromptSubmit — must stay sync, async stdout is discarded)
  --mode stop    : speak that line; if it is missing, summarise the answer instead
  --mode notify  : say a short notice when Claude wants permission or input
  --mode test    : speak a test line in each language (needs no stdin)

Built so it cannot break anything: every error is swallowed and the exit code is
always 0, so a failure in here never interrupts a Claude Code session.
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
    # auto | th | en  (auto = เดาจากภาษาที่ Claude ตอบ)
    "language": "auto",
    # ภาษาของเสียงแจ้งเตือน: auto | th | en (auto = ตามภาษาที่ Claude ตอบครั้งล่าสุด)
    "notify_lang": "auto",
    # ให้ Claude ปิดท้ายคำตอบด้วยบรรทัดสรุป (= transcript ที่คนไม่ได้ฟังอ่านตามได้)
    "inject_instruction": True,
    # auto | say | google | azure | edge | espeak | none
    "voice_engine": "auto",
    # auto | api | cli | none   (none = อ่านข้อความดิบโดยไม่สรุป)
    "summarizer": "auto",
    "model": "claude-haiku-4-5-20251001",
    "min_chars": 180,          # ข้อความสั้นกว่านี้ไม่ต้องสรุป อ่านเลย
    "skip_under_chars": 40,    # สั้นมากๆ ไม่ต้องพูดเลย
    "max_input_chars": 8000,   # กันค่าใช้จ่ายบานปลาย
    "max_spoken_chars": 600,
    "summarizer_timeout": 150, # claude CLI บน Windows เย็นเครื่องช้ากว่า 60 วิบ่อย
    "say_voice": "Kanya",
    "say_voice_en": "Samantha",
    "say_rate": 190,
    "google_voice": "th-TH-Neural2-C",
    "google_voice_en": "en-US-Neural2-C",
    "azure_voice": "th-TH-PremwadeeNeural",
    "azure_voice_en": "en-US-JennyNeural",
    "azure_region": "southeastasia",
    "edge_voice": "th-TH-PremwadeeNeural",
    "edge_voice_en": "en-US-AriaNeural",
    "speaking_rate": 0.85,
    "notify_sounds": True,
    "log": "~/.claude/thai-secretary.log",
}

SYSTEM_PROMPT_TH = """คุณคือเลขาส่วนตัวที่กำลังรายงานผลงานให้เจ้านายฟังด้วยเสียง

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

SYSTEM_PROMPT_EN = """You are a personal secretary reporting results out loud to your boss.

Summarise what the AI agent just finished doing, in 2-3 conversational English sentences.

Rules:
- Write it to be *heard*, not read: no markdown, no bullets, no emoji, no special characters.
- Never read out code, long filenames, paths or URLs. Refer to them in the round, like
  "the config file" or "three files under the scripts folder".
- Keep technical terms people say out loud as they are, don't over-explain them.
- Cover three things: what got done, how it turned out, what happens next (skip any that don't apply).
- If something failed or broke, lead with that plainly. Never paper over a problem.
- Don't open with "In summary" or "Based on the text" — go straight to the result.
- Pace it naturally: end each sentence with a period and separate clauses with commas,
  roughly every 5-8 words, so the speech engine has somewhere to breathe.

Reply with the spoken text only, nothing else."""

SYSTEM_PROMPTS = {"th": SYSTEM_PROMPT_TH, "en": SYSTEM_PROMPT_EN}

# บรรทัดสรุปที่ Claude เขียนปิดท้ายเอง — เป็นทั้ง transcript ที่คนอ่านตามได้
# และเป็นข้อความที่เอาไปอ่านออกเสียงตรงๆ โดยไม่ต้องเรียก LLM ซ้ำอีกรอบ
SPOKEN_MARKER = "🔊"

INJECTED_INSTRUCTION = """<lingling-voice-secretary>
A text-to-speech secretary reads your answers aloud for this user, and the line below
doubles as the written transcript for anyone who missed the audio.

End every response with one final line, after all other content, in this exact shape:

{marker} <one or two sentences summarising what you just did>

Rules for that line only (the rest of your response is unaffected):
- Write it in the SAME language as the rest of your response. Answered in Thai, write it
  in Thai; answered in English, write it in English. Never mix the two in this line.
- It gets read aloud, so use plain conversational prose: no markdown, no code, no file
  paths, no URLs, and no emoji other than the leading {marker}.
- Cover what got done, how it turned out, and what is next. Skip whichever don't apply.
  If something failed, lead with that.
- Under 300 characters, with commas between clauses so it reads with natural pauses.
- Emit it exactly once, as the very last line. Never put it inside a code block.
- Skip it only when your whole response is a single short sentence.
</lingling-voice-secretary>"""

NOTIFY_MESSAGES = {
    "th": {
        "permission_prompt": "คล็อดขออนุญาตรันคำสั่ง รบกวนกดยืนยันด้วยครับ",
        "idle_prompt": "คล็อดรออินพุตจากคุณอยู่ครับ",
        "agent_needs_input": "เอเจนต์ต้องการข้อมูลเพิ่มครับ",
        "agent_completed": "เอเจนต์ทำงานเสร็จแล้วครับ",
        "elicitation_dialog": "มีหน้าต่างขอข้อมูลรออยู่ครับ",
        "_fallback": "คล็อดต้องการความสนใจจากคุณครับ",
    },
    "en": {
        "permission_prompt": "Claude needs your permission to run a command.",
        "idle_prompt": "Claude is waiting on your input.",
        "agent_needs_input": "An agent needs more information from you.",
        "agent_completed": "The agent has finished its work.",
        "elicitation_dialog": "There's a dialog waiting for your input.",
        "_fallback": "Claude needs your attention.",
    },
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


THAI_CHAR_RE = re.compile(r"[฀-๿]")
LATIN_CHAR_RE = re.compile(r"[A-Za-z]")


# คำตอบไทยสายเทคมีศัพท์อังกฤษปนเยอะ ถ้านับแบบ "ไทยต้องชนะละติน" จะตัดสินผิด
# เป็นอังกฤษบ่อย กลับกันคำตอบอังกฤษล้วนแทบไม่มีอักษรไทยเลย จึงใช้เกณฑ์
# ไม่สมมาตร: มีอักษรไทยมากพอ (ทั้งสัดส่วนและจำนวนดิบ) ก็นับเป็นไทย
THAI_RATIO_MIN = 0.15
THAI_ABS_MIN = 8


def detect_lang(text: str, default: str = "th") -> str:
    """เดาภาษาของข้อความ เพื่อเลือกทั้งภาษาที่จะสรุปและเสียงที่จะใช้พูด

    ตัดโค้ด/URL/path ทิ้งก่อนนับเสมอ ไม่งั้นคำตอบภาษาไทยที่แปะโค้ดบล็อกยาวๆ
    จะโดนนับเป็นอังกฤษ เพราะตัวอักษรละตินในโค้ดท่วมเนื้อความจริง
    """
    probe = FENCE_RE.sub(" ", text)
    probe = INLINE_CODE_RE.sub(" ", probe)
    probe = URL_RE.sub(" ", probe)
    probe = PATH_RE.sub(" ", probe)
    thai = len(THAI_CHAR_RE.findall(probe))
    latin = len(LATIN_CHAR_RE.findall(probe))
    total = thai + latin
    if total == 0:
        return default
    if thai == 0:
        return "en"
    if thai >= THAI_ABS_MIN and thai / total >= THAI_RATIO_MIN:
        return "th"
    return "th" if thai > latin else "en"


def resolve_lang(text: str, cfg: dict, default: str = "th") -> str:
    """ค่า language ใน config ทับการเดาได้ เผื่อคนอยากล็อกภาษาเสียงไว้ภาษาเดียว"""
    forced = str(cfg.get("language", "auto")).strip().lower()
    if forced in ("th", "en"):
        return forced
    return detect_lang(text, default)


def lang_state_path() -> Path:
    return Path.home() / ".claude" / ".lingling-lang"


def remember_lang(lang: str) -> None:
    """จำภาษาของคำตอบล่าสุดไว้ ให้เสียงแจ้งเตือน ซึ่งไม่มีข้อความให้เดาภาษา
    พูดภาษาเดียวกับที่คุยกันอยู่ ไม่ใช่สลับภาษากลางคัน"""
    try:
        path = lang_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(lang, encoding="utf-8")
    except Exception:
        pass


def recall_lang(default: str = "th") -> str:
    try:
        value = lang_state_path().read_text(encoding="utf-8").strip()
        return value if value in ("th", "en") else default
    except Exception:
        return default


# คำแทนของ noise แต่ละชนิด แยกตามภาษาที่จะพูด จะได้ไม่มีคำไทยโผล่
# กลางประโยคอังกฤษ แล้วโดนเสียงอังกฤษสะกดมั่ว หรือกลับกัน
PLACEHOLDERS = {
    "th": {"code": " (มีโค้ด) ", "url": " ลิงก์ ", "path": " ไฟล์ "},
    "en": {"code": " (a code block) ", "url": " a link ", "path": " a file "},
}


def strip_markup(text: str, lang: str = "th") -> str:
    """เอา noise ที่อ่านออกเสียงแล้วทรมานหูออก"""
    words = PLACEHOLDERS.get(lang, PLACEHOLDERS["th"])
    text = FENCE_RE.sub(words["code"], text)
    text = INLINE_CODE_RE.sub(lambda m: m.group(0).strip("`"), text)
    text = LINK_RE.sub(r"\1", text)
    text = URL_RE.sub(words["url"], text)
    text = PATH_RE.sub(words["path"], text)
    text = MD_MARKS_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


PACE_WORDS_RE = re.compile(
    r"(ครับ|ค่ะ|ค่า|จ้ะ|จ้า|นะครับ|นะคะ|แล้ว|เลย)(?!\s*[,.!?ๆฯ])\s+(?=\S)"
)
# เลขหัวข้อแบบ "1. " "2) " ที่จุดเริ่มหรือหลังช่องว่าง (ไม่ชนเลขทศนิยม/เวอร์ชันเพราะ
# ต้องมีช่องว่างตามหลังจุด/วงเล็บ ส่วน "3.14" ตามด้วยตัวเลขไม่ใช่ช่องว่าง)
NUMBERED_LIST_RE = re.compile(r"(?:^|\s)\d{1,2}[.)]\s+")


def clean_for_speech(text: str, limit: int, lang: str = "th") -> str:
    text = strip_markup(text, lang)
    text = text.replace(SPOKEN_MARKER, " ")
    text = re.sub(r"[\[\](){}<>]", " ", text)
    text = NUMBERED_LIST_RE.sub(", ", text)           # เลขหัวข้อ -> เว้นจังหวะ
    text = re.sub(r"(?:^|\s)[-–—•]\s+", ", ", text)   # bullet -> เว้นจังหวะ
    if lang == "th":
        # กันประโยคพูดรวดเดียวไม่มีจังหวะพัก แทรกจุลภาคหลังคำลงท้ายประโยคทั่วไป
        # ที่ยังไม่มีเครื่องหมายวรรคตอนตามหลัง (เผื่อ summarizer ลืมเว้นจังหวะเอง)
        # อังกฤษไม่ต้อง เพราะเว้นวรรคระหว่างคำอยู่แล้ว engine หาจังหวะเองได้
        text = PACE_WORDS_RE.sub(r"\1, ", text)
    text = re.sub(r"\s*,\s*,+", ", ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,")
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + " ..."
    return text


# ---------------------------------------------------------------- summarize

def summarize_via_api(text: str, cfg: dict, lang: str = "th") -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    payload = json.dumps({
        "model": cfg["model"],
        "max_tokens": 400,
        "system": SYSTEM_PROMPTS.get(lang, SYSTEM_PROMPT_TH),
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


def summarize_via_cli(text: str, cfg: dict, lang: str = "th") -> str | None:
    """ใช้ auth เดิมของ Claude Code โดยไม่ต้องมี API key

    สำคัญ: ต้องส่ง disableAllHooks ไม่งั้น session ลูกจะยิง Stop hook
    ซ้อนกลับมาเป็น loop ไม่รู้จบ
    """
    claude = shutil.which("claude")
    if not claude:
        return None
    header = "ข้อความที่ต้องสรุป" if lang == "th" else "Text to summarise"
    prompt = SYSTEM_PROMPTS.get(lang, SYSTEM_PROMPT_TH) + "\n\n---\n\n" + header + ":\n\n" + text
    try:
        proc = subprocess.run(
            [claude, "-p", prompt,
             "--model", "haiku",
             "--settings", '{"disableAllHooks": true}'],
            capture_output=True, text=True, timeout=int(cfg.get("summarizer_timeout", 150)),
            encoding="utf-8", errors="replace",
            env={**os.environ, "THAI_SECRETARY_ENABLED": "false"},
        )
        out = (proc.stdout or "").strip()
        return out or None
    except Exception as exc:
        log(cfg, f"cli summarize failed: {exc}")
        return None


def summarize(text: str, cfg: dict, lang: str = "th") -> str:
    mode = cfg.get("summarizer", "auto")
    if mode == "none":
        return text
    if mode in ("auto", "api"):
        out = summarize_via_api(text, cfg, lang)
        if out:
            return out
        if mode == "api":
            return text
    if mode in ("auto", "cli"):
        out = summarize_via_cli(text, cfg, lang)
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


def tts_google(text: str, cfg: dict, lang: str = "th") -> bool:
    key = os.environ.get("GOOGLE_TTS_API_KEY")
    if not key:
        return False
    lang_code = "th-TH" if lang == "th" else "en-US"
    voice = cfg["google_voice"] if lang == "th" else cfg.get("google_voice_en", "en-US-Neural2-C")
    payload = json.dumps({
        "input": {"text": text},
        "voice": {"languageCode": lang_code, "name": voice},
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


def tts_azure(text: str, cfg: dict, lang: str = "th") -> bool:
    key = os.environ.get("AZURE_SPEECH_KEY")
    if not key:
        return False
    region = cfg["azure_region"]
    lang_code = "th-TH" if lang == "th" else "en-US"
    voice = cfg["azure_voice"] if lang == "th" else cfg.get("azure_voice_en", "en-US-JennyNeural")
    ssml = (
        f'<speak version="1.0" xml:lang="{lang_code}">'
        f'<voice name="{voice}">'
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


def tts_edge(text: str, cfg: dict, lang: str = "th") -> bool:
    if not shutil.which("edge-tts"):
        return False
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    try:
        rate = f'{int((cfg["speaking_rate"] - 1) * 100):+d}%'
        voice = cfg["edge_voice"] if lang == "th" else cfg.get("edge_voice_en", "en-US-AriaNeural")
        subprocess.run(["edge-tts", "--voice", voice, f"--rate={rate}",
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


def tts_say(text: str, cfg: dict, lang: str = "th") -> bool:
    if not shutil.which("say"):
        return False
    cmd = ["say", "-r", str(cfg["say_rate"])]
    voice = cfg.get("say_voice") if lang == "th" else cfg.get("say_voice_en")
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


def tts_powershell(text: str, cfg: dict, lang: str = "th") -> bool:
    safe = text.replace("'", "''")
    culture = "th-TH" if lang == "th" else "en-US"
    script = ("Add-Type -AssemblyName System.Speech; "
              "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              f"try {{ $s.SelectVoiceByHints('NotSet', 'NotSet', 0, "
              f"[System.Globalization.CultureInfo]::new('{culture}')) }} catch {{}}; "
              f"$s.Speak('{safe}')")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       capture_output=True, timeout=120)
        return True
    except Exception as exc:
        log(cfg, f"powershell tts failed: {exc}")
        return False


def tts_espeak(text: str, cfg: dict, lang: str = "th") -> bool:
    if shutil.which("spd-say"):
        subprocess.run(["spd-say", "-l", lang, "-e", text], capture_output=True)
        return True
    if shutil.which("espeak-ng"):
        subprocess.run(["espeak-ng", "-v", lang, text], capture_output=True)
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


def speak(text: str, cfg: dict, lang: str | None = None) -> None:
    if not text.strip():
        return
    engine = pick_engine(cfg)
    if engine == "none":
        log(cfg, "no tts engine available")
        return
    # ปกติผู้เรียกรู้ภาษาอยู่แล้ว (ตัดสินจากคำตอบต้นทาง ไม่ใช่จากบทสรุป)
    # เดาเองเป็นทางสำรองเฉยๆ เผื่อมีใครเรียก speak ตรงๆ
    lang = lang or detect_lang(text)
    log(cfg, f"speak lang={lang} engine={engine}")
    stop_previous(cfg)
    fn = ENGINES.get(engine)
    if not fn or not fn(text, cfg, lang):
        # ไล่ fallback ตามที่มีในเครื่อง
        for name, alt in ENGINES.items():
            if name != engine and alt(text, cfg, lang):
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

# บรรทัดสรุปอยู่ท้ายคำตอบเสมอ จำกัดขอบเขตการค้นไว้แถวท้าย กัน 🔊 ที่ Claude
# บังเอิญพิมพ์ไว้กลางคำตอบ (เช่นตอนอธิบายปลั๊กอินตัวนี้เอง) ถูกหยิบมาอ่านผิดตัว
SPOKEN_SEARCH_TAIL = 1200
SPOKEN_MAX_CHARS = 800


def extract_spoken_line(message: str) -> str | None:
    """ดึงบรรทัด 🔊 ที่ Claude เขียนปิดท้ายคำตอบออกมา

    บรรทัดนี้ทำหน้าที่สองอย่างพร้อมกัน: เป็น transcript ที่คนไม่ได้ฟังอ่านตามได้
    ในแชทตรงนั้นเลย และเป็นข้อความที่เอาไปเข้า TTS ได้ทันทีโดยไม่ต้องเรียก LLM
    สรุปซ้ำ ทำให้ Stop hook เป็น async ที่ไม่หน่วงอะไรเลย
    """
    tail_start = max(0, len(message) - SPOKEN_SEARCH_TAIL)
    idx = message.rfind(SPOKEN_MARKER, tail_start)
    if idx == -1:
        return None
    spoken = message[idx + len(SPOKEN_MARKER):].strip()
    spoken = spoken.strip("*_:>- \t")
    # อยู่ในโค้ดบล็อก = Claude กำลังยกตัวอย่างรูปแบบ ไม่ได้กำลังสรุปงานจริง
    if not spoken or "```" in spoken or len(spoken) > SPOKEN_MAX_CHARS:
        return None
    return spoken


def mode_inject(cfg: dict) -> None:
    """UserPromptSubmit: บอก Claude ให้ปิดท้ายคำตอบด้วยบรรทัดสรุปสำหรับอ่านออกเสียง

    ข้อบังคับสองข้อที่ทดสอบกับ Claude Code จริงแล้ว ห้ามเปลี่ยนโดยไม่ทดสอบซ้ำ:

    1. hook นี้ต้องเป็น sync (ห้ามใส่ async: true ใน hooks.json)
       เพราะ Claude Code ทิ้ง stdout ของ async hook ทั้งหมด คำสั่งจะไปไม่ถึงโมเดล
    2. ต้องพิมพ์เป็น "ข้อความเปล่า" ไม่ใช่ JSON ที่มี additionalContext
       UserPromptSubmit เป็นอีเวนต์กลุ่มพิเศษที่เอา stdout ดิบไปต่อเข้า context ให้เลย
       ส่วน additionalContext ถูกกลืนหายทั้งกรณี suppressOutput true และ false
       (ยิงทดสอบด้วย claude -p แล้ว: แบบ JSON โมเดลตอบ NONE, แบบข้อความเปล่าโมเดลเห็นค่า)

    งานในโหมดนี้คือพิมพ์ข้อความคงที่ก้อนเดียวแล้วจบ ไม่มี I/O ช้าอะไรให้หน่วง turn
    """
    if not cfg.get("inject_instruction", True):
        return
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(INJECTED_INSTRUCTION.format(marker=SPOKEN_MARKER), flush=True)


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
    message = (data.get("last_assistant_message") or "").strip()
    if not message:
        log(cfg, "stop: no last_assistant_message")
        return

    spoken = extract_spoken_line(message)
    if spoken:
        # ทางหลัก: Claude เขียนบทสรุปมาให้แล้ว (และผู้ใช้เห็นมันในแชทไปแล้ว)
        # ภาษาของบรรทัดนี้ = ภาษาที่ Claude เลือกตอบ ตรงตามที่ต้องการพอดี
        source = "inline"
        lang = resolve_lang(spoken, cfg)
    else:
        # ทางสำรอง: session ที่ยังไม่ได้ฉีด instruction หรือคำตอบสั้นจน Claude ข้าม
        source = "summary"
        lang = resolve_lang(message, cfg)
        cleaned = strip_markup(message, lang)
        if len(cleaned) < cfg["skip_under_chars"]:
            return
        if len(cleaned) >= cfg["min_chars"]:
            spoken = summarize(cleaned[: cfg["max_input_chars"]], cfg, lang)
        else:
            spoken = cleaned

    spoken = clean_for_speech(spoken, cfg["max_spoken_chars"], lang)
    if not spoken:
        return
    remember_lang(lang)
    log(cfg, f"stop[{source}] lang={lang} -> {spoken}")
    speak(spoken, cfg, lang)


def notify_lang(cfg: dict) -> str:
    """เสียงแจ้งเตือนไม่มีข้อความให้เดาภาษา จึงเดินตามภาษาของคำตอบล่าสุดแทน"""
    choice = str(cfg.get("notify_lang", "auto")).strip().lower()
    if choice in ("th", "en"):
        return choice
    forced = str(cfg.get("language", "auto")).strip().lower()
    if forced in ("th", "en"):
        return forced
    return recall_lang()


def mode_notify(cfg: dict) -> None:
    if not cfg.get("notify_sounds", True):
        return
    data = read_stdin_json()
    kind = data.get("notification_type") or data.get("matcher") or ""
    lang = notify_lang(cfg)
    table = NOTIFY_MESSAGES.get(lang, NOTIFY_MESSAGES["th"])
    message = table.get(kind)
    if not message:
        raw = (data.get("message") or "").strip()
        message = clean_for_speech(raw, 160, lang) if raw else table["_fallback"]
    log(cfg, f"notify({kind}) lang={lang} -> {message}")
    speak(message, cfg, lang)


TEST_LINES = {
    "th": "สวัสดีครับ ระบบเลขาส่วนตัวพร้อมทำงานแล้ว, ทดสอบเสียงภาษาไทยสำเร็จ",
    "en": "Hi, your voice secretary is up and running, the English voice works too.",
}

VOICE_KEYS = {
    "google": "google_voice", "azure": "azure_voice",
    "edge": "edge_voice", "say": "say_voice",
}


def mode_test(cfg: dict) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    engine = pick_engine(cfg)
    print(f"engine        : {engine}")
    key = VOICE_KEYS.get(engine)
    if key:
        print(f"voice th      : {cfg.get(key)}")
        print(f"voice en      : {cfg.get(key + '_en')}")
    print(f"language      : {cfg['language']} (notify: {cfg['notify_lang']},"
          f" last seen: {recall_lang()})")
    print(f"summarizer    : {cfg['summarizer']}"
          f" (api key: {'yes' if os.environ.get('ANTHROPIC_API_KEY') else 'no'},"
          f" claude cli: {'yes' if shutil.which('claude') else 'no'})")
    print(f"transcript    : {'on' if cfg['inject_instruction'] else 'off'}"
          f" (Claude closes each answer with a {SPOKEN_MARKER} line)")
    print(f"config        : {config_path()}")
    print(f"log           : {Path(cfg['log']).expanduser()}")
    forced = str(cfg.get("language", "auto")).strip().lower()
    langs = [forced] if forced in ("th", "en") else ["th", "en"]
    for lang in langs:
        print(f"speaking {lang}   : {TEST_LINES[lang]}")
        speak(TEST_LINES[lang], cfg, lang)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["inject", "stop", "notify", "test"],
                        default="stop")
    args = parser.parse_args()

    cfg = load_config()
    if not cfg.get("enabled", True) and args.mode != "test":
        return 0
    try:
        {"inject": mode_inject, "stop": mode_stop,
         "notify": mode_notify, "test": mode_test}[args.mode](cfg)
    except Exception as exc:  # hook ห้ามพัง session เด็ดขาด
        log(cfg, f"unhandled error in {args.mode}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
