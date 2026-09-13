#!/usr/bin/env python3
"""
lingling — a bilingual voice secretary: summarises what Claude did, then says it aloud.
Claude answers in Thai -> Thai summary, Thai voice. In English -> English, English voice.

Reads the hook's JSON from stdin, then:
  --mode inject  : ask Claude to end every answer with a one-line spoken summary
                   (UserPromptSubmit — must stay sync, async stdout is discarded)
  --mode stop    : speak that line; if it is missing, summarise the answer instead
  --mode notify  : say a short notice when Claude wants permission or input
  --mode ensure  : check for the neural voice, spawn the installer if it is missing
  --mode install : install the neural voice (runs detached; also callable by hand)
  --mode test    : speak a test line in each language (needs no stdin)

Built so it cannot break anything: every error is swallowed and the exit code is
always 0, so a failure in here never interrupts a Claude Code session.
"""

# macOS ships python 3.9 at /usr/bin/python3, and 3.9 cannot evaluate "str | None"
# annotations at runtime. Without this line the whole file fails to import on any
# machine with no hand-installed python, leaving the plugin silent for no stated reason.
from __future__ import annotations

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
    # auto | th | en  (auto = follow the language Claude answered in)
    "language": "auto",
    # Language for spoken alerts: auto | th | en (auto = the last answer's language)
    "notify_lang": "auto",
    # Have Claude close each answer with the summary line, which doubles as a
    # transcript for anyone who did not hear the audio
    "inject_instruction": True,
    # auto | say | google | azure | edge | espeak | none
    "voice_engine": "auto",
    # auto | api | cli | none   (none = speak the raw text without summarising)
    "summarizer": "auto",
    "model": "claude-haiku-4-5-20251001",
    "min_chars": 180,          # shorter than this is spoken as-is, no summary
    "skip_under_chars": 40,    # shorter than this is not worth speaking at all
    "max_input_chars": 8000,   # keeps a runaway answer from running up a bill
    "max_spoken_chars": 600,
    "summarizer_timeout": 150, # a cold claude CLI on Windows often needs over 60s
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

SYSTEM_PROMPT_TH = """You are a personal secretary reporting results out loud to your boss.

Summarise what the AI agent just finished doing, in 2-3 conversational sentences.
Write that summary in Thai. Every rule below applies to the Thai you produce.

Rules:
- Write it to be *heard*, not read: no markdown, no bullets, no emoji, no special characters.
- Never read out code, long filenames, paths or URLs. Refer to them in the round, like
  "the config file" or "three files under the scripts folder".
- Technical terms that Thai speakers normally borrow from English stay borrowed. Do not
  force a stiff literal Thai translation onto a word people say in English every day.
- Never invent a Thai spelling for an unfamiliar English proper noun (a program, plugin
  or library name). Refer to it generically instead — "this plugin", "the tool in use".
- Talk about punctuation the way people do in conversation, never with the formal Thai
  grammatical names for the marks themselves.
- Cover three things: what got done, how it turned out, what happens next (skip any that don't apply).
- If something failed or broke, lead with that plainly. Never paper over a problem.
- Don't open with "in summary" or "based on the text" — go straight to the result.
- Pace it naturally: end each sentence with a period and separate clauses with commas,
  roughly every 5-8 words, so the speech engine has somewhere to breathe.

Reply with the spoken text only, nothing else."""

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

# The summary line Claude writes at the end of its own answer. It serves as both the
# transcript a reader can follow and the text fed straight to TTS, with no second LLM call.
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

# Spoken alerts, one set per language. The Thai entries below are Thai because they
# are read out loud to a Thai listener, not because the source is bilingual — every
# comment, name and log line in this file is English.
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


# A technical answer in Thai carries a lot of English terms, so "Thai must outnumber
# Latin" would misread it as English again and again, while an all-English answer
# holds almost no Thai characters at all. Hence the asymmetric test: enough Thai
# characters, by both share and raw count, is enough to call it Thai.
THAI_RATIO_MIN = 0.15
THAI_ABS_MIN = 8


def detect_lang(text: str, default: str = "th") -> str:
    """Guess the language of a text, which picks both the summary language and the voice.

    Code, URLs and paths are stripped before counting. Without that, a Thai answer
    quoting a long code block reads as English, because the Latin characters inside
    the code drown out the prose around it.
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
    """The config's `language` overrides the guess, for anyone who wants one
    language and nothing else."""
    forced = str(cfg.get("language", "auto")).strip().lower()
    if forced in ("th", "en"):
        return forced
    return detect_lang(text, default)


def lang_state_path() -> Path:
    return Path.home() / ".claude" / ".lingling-lang"


def remember_lang(lang: str) -> None:
    """Remember the last answer's language, so spoken alerts — which carry no text to
    guess from — speak the language of the conversation instead of switching mid-stream."""
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


# Replacements for each kind of noise, kept per language so no Thai word lands in the
# middle of an English sentence for an English voice to mangle, or the other way round.
PLACEHOLDERS = {
    "th": {"code": " (มีโค้ด) ", "url": " ลิงก์ ", "path": " ไฟล์ "},
    "en": {"code": " (a code block) ", "url": " a link ", "path": " a file "},
}


def strip_markup(text: str, lang: str = "th") -> str:
    """Strip the noise that is painful to listen to when read out loud."""
    words = PLACEHOLDERS.get(lang, PLACEHOLDERS["th"])
    text = FENCE_RE.sub(words["code"], text)
    text = INLINE_CODE_RE.sub(lambda m: m.group(0).strip("`"), text)
    text = LINK_RE.sub(r"\1", text)
    text = URL_RE.sub(words["url"], text)
    text = PATH_RE.sub(words["path"], text)
    text = MD_MARKS_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# The Thai particles that commonly end a spoken clause. Matching them is how a comma
# gets inserted where a Thai sentence would otherwise run on without a pause.
PACE_WORDS_RE = re.compile(
    r"(ครับ|ค่ะ|ค่า|จ้ะ|จ้า|นะครับ|นะคะ|แล้ว|เลย)(?!\s*[,.!?ๆฯ])\s+(?=\S)"
)
# List numbers like "1. " or "2) " at the start or after a space. Decimals and version
# numbers survive: this needs whitespace after the dot or bracket, and "3.14" has a
# digit there instead.
NUMBERED_LIST_RE = re.compile(r"(?:^|\s)\d{1,2}[.)]\s+")


def clean_for_speech(text: str, limit: int, lang: str = "th") -> str:
    text = strip_markup(text, lang)
    text = text.replace(SPOKEN_MARKER, " ")
    text = re.sub(r"[\[\](){}<>]", " ", text)
    text = NUMBERED_LIST_RE.sub(", ", text)           # list number -> a pause
    text = re.sub(r"(?:^|\s)[-–—•]\s+", ", ", text)   # bullet -> a pause
    if lang == "th":
        # Thai is written without spaces between words, so a summary that forgot its
        # own punctuation comes out as one unbroken rush. Insert a comma after the
        # common sentence-ending particles that have no punctuation of their own yet.
        # English needs none of this: the spaces already tell the engine where to breathe.
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
    """Reuse Claude Code's own auth, so no API key is needed.

    Critical: disableAllHooks must be passed, or the child session fires its own
    Stop hook and recurses forever.
    """
    claude = shutil.which("claude")
    if not claude:
        return None
    header = "Text to summarise"
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


# ---------------------------------------------------------------- bootstrap

# A voice that sounds human means edge-tts, and nobody should have to install it by
# hand, so this section does it for them. Three rules keep that from being rude:
#   - Install into the plugin's own venv under ~/.claude. Never touch the system
#     python or the user's global tools. Uninstalling is deleting one directory.
#   - Run detached from the hook, so nothing blocks a turn and no hook timeout can
#     kill the download halfway through.
#   - Be free to fail. Until it succeeds, the built-in system voice carries on.
VENV_DIR = Path.home() / ".claude" / "lingling-venv"
STAMP_PATH = Path.home() / ".claude" / "lingling-install.json"
RETRY_AFTER_FAILURE = 24 * 3600   # a dead network retried every session is just noise
STALE_RUNNING_AFTER = 15 * 60     # an installer killed mid-run must not lock forever


def venv_bin() -> Path:
    return VENV_DIR / ("Scripts" if sys.platform == "win32" else "bin")


def venv_python() -> Path:
    return venv_bin() / ("python.exe" if sys.platform == "win32" else "python")


def read_stamp() -> dict:
    try:
        return json.loads(STAMP_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_stamp(**fields) -> None:
    try:
        STAMP_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = read_stamp()
        data.update(fields, updated=time.time())
        STAMP_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def install_wanted(cfg: dict) -> bool:
    """Is edge-tts worth installing here?

    Only when it would actually get used: the user has not pinned a different engine,
    and what this machine can reach right now is not already a neural voice — that is,
    it would otherwise fall back to say, powershell, espeak or nothing at all.
    """
    if cfg.get("voice_engine", "auto") not in ("auto", "edge"):
        return False
    return pick_engine(cfg) not in ("google", "azure", "edge")


def install_edge_tts(cfg: dict) -> str | None:
    """Build the venv and install edge-tts into it. Returns the CLI path on success."""
    uv = find_exe("uv")
    try:
        if uv:
            # uv builds the venv in seconds, so use it whenever the machine has it
            subprocess.run([uv, "venv", str(VENV_DIR)],
                           capture_output=True, text=True, timeout=180)
            step = subprocess.run([uv, "pip", "install", "--python",
                                   str(venv_python()), "edge-tts"],
                                  capture_output=True, text=True, timeout=600)
        else:
            subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)],
                           capture_output=True, text=True, timeout=300)
            step = subprocess.run([str(venv_python()), "-m", "pip", "install",
                                   "--upgrade", "--quiet", "edge-tts"],
                                  capture_output=True, text=True, timeout=900)
    except Exception as exc:
        log(cfg, f"install: {exc}")
        return None

    exe = find_exe("edge-tts")
    if exe:
        return exe
    detail = (step.stderr or step.stdout or "").strip().splitlines()
    log(cfg, f"install failed: {detail[-1] if detail else 'edge-tts not found after install'}")
    return None


def mode_install(cfg: dict) -> None:
    """Do the real install. This is the mode spawned detached, or run by hand."""
    exe = find_exe("edge-tts")
    if exe:
        write_stamp(state="ok", edge_tts=exe)
        return

    stamp = read_stamp()
    if (stamp.get("state") == "running"
            and time.time() - stamp.get("updated", 0) < STALE_RUNNING_AFTER):
        return  # another installer is already working

    log(cfg, "install: setting up edge-tts (neural voices)")
    write_stamp(state="running", edge_tts=None)
    exe = install_edge_tts(cfg)
    write_stamp(state="ok" if exe else "failed", edge_tts=exe)
    log(cfg, f"install: {'ready at ' + exe if exe else 'failed, staying on the system voice'}")

    if exe and not any(shutil.which(player) for player in
                       ("afplay", "mpg123", "ffplay", "paplay", "aplay")) \
            and sys.platform not in ("darwin", "win32"):
        log(cfg, "install: no mp3 player on this machine — install mpg123 or ffmpeg "
                 "as well (for example: sudo apt install mpg123)")


def mode_ensure(cfg: dict) -> None:
    """Check cheaply, then spawn the installer detached. Must return at once.

    Called from SessionStart, and again at the end of UserPromptSubmit to catch the
    case where the plugin was installed mid-session, after SessionStart had passed.
    """
    if not install_wanted(cfg):
        return
    stamp = read_stamp()
    age = time.time() - stamp.get("updated", 0)
    if stamp.get("state") == "running" and age < STALE_RUNNING_AFTER:
        return
    if stamp.get("state") == "failed" and age < RETRY_AFTER_FAILURE:
        return

    # subprocess has no single way to detach on both sides: start_new_session is POSIX
    # only (Windows swallows it silently), and Windows wants creationflags instead.
    detach = ({"creationflags": 0x00000008 | 0x08000000}   # DETACHED_PROCESS | NO_WINDOW
              if sys.platform == "win32" else {"start_new_session": True})
    runner = Path(__file__).resolve()
    try:
        logfile = Path(cfg.get("log", DEFAULTS["log"])).expanduser()
        logfile.parent.mkdir(parents=True, exist_ok=True)
        with logfile.open("a", encoding="utf-8") as fh:
            subprocess.Popen([sys.executable, str(runner), "--mode", "install"],
                             stdin=subprocess.DEVNULL, stdout=fh, stderr=fh,
                             **detach)
    except Exception as exc:
        log(cfg, f"ensure: spawn failed: {exc}")


# ---------------------------------------------------------------- tts

def find_exe(name: str) -> str | None:
    """Find a CLI installed by pipx, uv or pip --user, even when the hook's PATH misses it.

    A hook inherits its PATH from the Claude Code process, not from the shell sitting
    open on the desktop, so a tool in ~/.local/bin can be invisible to shutil.which
    while running perfectly when typed by hand. Check those directories by hand too.
    """
    found = shutil.which(name)
    if found:
        return found
    for directory in (venv_bin(), Path.home() / ".local" / "bin",
                      Path("/opt/homebrew/bin"), Path("/usr/local/bin"),
                      Path.home() / "bin"):
        for candidate in (directory / name, directory / f"{name}.exe"):
            try:
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate)
            except OSError:
                continue
    return None

def pick_engine(cfg: dict) -> str:
    engine = cfg.get("voice_engine", "auto")
    if engine != "auto":
        return engine
    if os.environ.get("GOOGLE_TTS_API_KEY"):
        return "google"
    if os.environ.get("AZURE_SPEECH_KEY"):
        return "azure"
    # edge comes before say because the Thai voice macOS ships (Kanya) is an older
    # compact voice, stiff in its pacing and flat in tone, while edge is a neural voice
    # that sounds like a person. say stays as the fallback: always present, always offline.
    if find_exe("edge-tts"):
        return "edge"
    if sys.platform == "darwin" and shutil.which("say"):
        return "say"
    if sys.platform == "win32":
        return "powershell"
    if shutil.which("spd-say") or shutil.which("espeak-ng"):
        return "espeak"
    return "none"


def _play_audio_windows(path: str) -> bool:
    """Play an audio file on Windows through MCI (winmm.dll).

    Windows ships none of afplay, mpg123, ffplay, paplay or aplay. WMPlayer.OCX
    through PowerShell was tried first, but playState sticks at "waiting": an ActiveX
    control needs a Windows message loop to advance its state, and a bare PowerShell
    console has none. mciSendString needs no external loop — its "play ... wait"
    command both plays and blocks until the sound is finished.
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
    log(cfg, "no audio player found (try brew/apt install mpg123)")


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
    exe = find_exe("edge-tts")
    if not exe:
        return False
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    try:
        rate = f'{int((cfg["speaking_rate"] - 1) * 100):+d}%'
        voice = cfg["edge_voice"] if lang == "th" else cfg.get("edge_voice_en", "en-US-AriaNeural")
        proc = subprocess.run([exe, "--voice", voice, f"--rate={rate}",
                               "--text", text, "--write-media", tmp.name],
                              capture_output=True, timeout=60)
        # edge is an online service: the moment the network drops it writes an empty
        # file and exits quietly. Without this check speak() believes it spoke, and
        # never walks its fallback chain down to the system voice.
        if proc.returncode != 0 or os.path.getsize(tmp.name) < 1024:
            log(cfg, "edge tts produced no audio (offline?), falling back")
            return False
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
            # the Thai voice is not downloaded yet, so try the default one
            subprocess.run(["say", text], capture_output=True)
            log(cfg, f"voice '{voice}' not installed — "
                     "download it in System Settings > Accessibility > Spoken Content "
                     "> System Voice > Thai")
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
    # Callers normally know the language already, having judged it from the source
    # answer rather than the summary. Guessing here is only a fallback for anyone
    # who calls speak() directly.
    lang = lang or detect_lang(text)
    log(cfg, f"speak lang={lang} engine={engine}")
    stop_previous(cfg)
    fn = ENGINES.get(engine)
    if not fn or not fn(text, cfg, lang):
        # walk the fallbacks and take whatever this machine can actually do
        for name, alt in ENGINES.items():
            if name != engine and alt(text, cfg, lang):
                return


def _is_our_process(pid: int) -> bool:
    """Confirm a PID really belongs to this script before signalling it.

    PIDs get recycled. Without this check, the kill could land on some unrelated
    process of the user's.
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
    """Keep utterances from piling on top of each other during rapid turns."""
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

# The summary line always sits at the end, so the search is limited to the tail. That
# keeps a 🔊 Claude happened to type mid-answer — while explaining this very plugin,
# say — from being picked up and read out as the summary.
SPOKEN_SEARCH_TAIL = 1200
SPOKEN_MAX_CHARS = 800


def extract_spoken_line(message: str) -> str | None:
    """Pull out the 🔊 line Claude wrote at the end of its answer.

    That line does two jobs at once: it is the transcript a reader can follow right
    there in the chat, and it is text ready to go straight into TTS without a second
    LLM call. That is what keeps the Stop hook async and free of any delay.
    """
    tail_start = max(0, len(message) - SPOKEN_SEARCH_TAIL)
    idx = message.rfind(SPOKEN_MARKER, tail_start)
    if idx == -1:
        return None
    spoken = message[idx + len(SPOKEN_MARKER):].strip()
    spoken = spoken.strip("*_:>- \t")
    # Inside a code block means Claude is demonstrating the format, not summarising
    if not spoken or "```" in spoken or len(spoken) > SPOKEN_MAX_CHARS:
        return None
    return spoken


def mode_inject(cfg: dict) -> None:
    """UserPromptSubmit: ask Claude to end its answer with a line written to be spoken.

    Two constraints, both established against real Claude Code. Do not change either
    without testing it again:

    1. This hook must stay sync — never async: true in hooks.json. Claude Code
       discards the stdout of an async hook, so the instruction never reaches the model.
    2. It must print plain text, not JSON carrying additionalContext. UserPromptSubmit
       is one of the special events whose raw stdout is appended to the context as-is,
       while additionalContext is swallowed whether suppressOutput is true or false.
       (Verified with claude -p: the JSON form made the model answer NONE, the plain
       text form made it read the value.)

    All this mode does is print one fixed block and stop. There is no slow I/O in it
    to hold up the turn.
    """
    if not cfg.get("inject_instruction", True):
        return
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(INJECTED_INSTRUCTION.format(marker=SPOKEN_MARKER), flush=True)
    # The plugin may have been installed mid-session, after SessionStart already
    # passed. This is the safety net. It normally reads one stamp file and returns,
    # so it holds up nothing.
    mode_ensure(cfg)


def read_stdin_json() -> dict:
    """Always read stdin as UTF-8.

    A plain text-mode sys.stdin.read() decodes with the Windows locale encoding —
    cp1252, cp874 — rather than UTF-8, which corrupts the Thai that Claude Code pipes
    in, right at the point of entry. Read the buffer and decode it by hand instead.
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
        # The main path: Claude wrote the summary itself, and the user has already
        # read it in the chat. This line's language is the language Claude chose to
        # answer in, which is exactly what the voice should follow.
        source = "inline"
        lang = resolve_lang(spoken, cfg)
    else:
        # The fallback: a session that never got the instruction, or an answer
        # short enough that Claude skipped the line
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
    """An alert carries no text to guess from, so it follows the last answer's language."""
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


# Spoken by --mode test, so each line has to be in the language of the voice it checks.
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
    # This mode is run by hand, so the user can wait: install right here rather
    # than spawning it into the background
    if install_wanted(cfg):
        print("installing    : edge-tts (neural voices), this takes a moment ...")
        mode_install(cfg)
    engine = pick_engine(cfg)
    print(f"engine        : {engine}")
    if engine == "edge":
        print(f"edge-tts      : {find_exe('edge-tts')}")
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
    parser.add_argument("--mode", choices=["inject", "stop", "notify", "test",
                                           "ensure", "install"],
                        default="stop")
    args = parser.parse_args()

    cfg = load_config()
    if not cfg.get("enabled", True) and args.mode != "test":
        return 0
    try:
        {"inject": mode_inject, "stop": mode_stop,
         "notify": mode_notify, "test": mode_test,
         "ensure": mode_ensure, "install": mode_install}[args.mode](cfg)
    except Exception as exc:  # a hook must never take a session down
        log(cfg, f"unhandled error in {args.mode}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
