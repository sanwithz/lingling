<p align="center">
  <img src="assets/mockup.svg" width="700" alt="lingling speaking a summary aloud after Claude Code finishes a task">
</p>

<h1 align="center">lingling</h1>

<p align="center">
  <em>Claude finishes the job, then tells you what it did — out loud, in the language it answered in</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-GPL--3.0-111111?style=flat-square" alt="License">
  <img src="https://img.shields.io/badge/plugin-Claude%20Code-111111?style=flat-square" alt="Claude Code plugin">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-111111?style=flat-square" alt="Platform">
  <img src="https://img.shields.io/badge/voice-th--TH%20%7C%20en--US-111111?style=flat-square" alt="Thai and English voice">
</p>

> **Note:** `assets/mockup.svg` above is a placeholder mockup and will be replaced with real artwork.

---

You give Claude Code a task and walk off to make coffee. You come back to a wall of output and have to read all of it just to find out what happened.

**lingling** fixes that. When Claude finishes, it closes its answer with a one-line summary and reads that line aloud immediately — like having a secretary report back, so you don't have to keep glancing at the screen.

That line doubles as a **transcript**. If you weren't listening, it's right there in the chat to read.

**Bilingual, automatically.** Answer in Thai and you get a Thai summary in a Thai voice; answer in English and you get an English summary in an English voice. There is no mode to switch.

It also speaks up when Claude is stuck waiting on a permission prompt or on your input.

## Before / after

**Before** — a long answer full of markdown, code blocks and jargon, and you read all of it to learn what changed.

**After** — one or two sentences you understand on hearing them, spoken in a natural voice in the same language Claude answered in, paced like a person talking, with no attempt to read code or URLs at you.

## Highlights

- **Fires on every turn** through the `Stop` hook. Nothing to invoke.
- **Bilingual end to end.** The language of Claude's own answer picks the summary language *and* the voice.
- **A transcript on every answer**, readable in the chat when you weren't listening.
- **No added latency.** The spoken line is written during the answer, so nothing runs a model after the turn ends.
- **Spoken notifications** when Claude wants permission or input.
- **Picks a TTS engine for you** based on what's installed, or use the one you name.
- **Mute mid-session** with `/lingling:mute` and `/lingling:unmute`.

## Requirements

| | |
|---|---|
| **Python 3.9+** | Must be a real install from [python.org](https://www.python.org/downloads/) (or `winget install Python.Python.3.13`). The Microsoft Store alias is a stub that does nothing — lingling detects and skips it. |
| **Git for Windows** | Windows only. Claude Code runs shell hooks through Git Bash, which ships with [Git for Windows](https://git-scm.com/download/win). |
| **A TTS engine** | `pip install edge-tts` is the easiest good one, and it's free. See [Voices](#voices). |

After installing Python on Windows, **close every terminal window and open a new one**. Retyping `claude` in the same window won't pick up the new `PATH`.

## Install

```
/plugin marketplace add vectorkub/lingling
```
```
/plugin install lingling@lingling
```

(Send those as two separate messages.)

### Check that it works

```bash
python scripts/secretary.py --mode test
```

It prints the TTS engine it found, the voice it will use for each language, which summariser is available, and then speaks one test line in Thai and one in English.

## How the transcript gets there

A `UserPromptSubmit` hook attaches a short instruction to every turn asking Claude to end its answer with a single line shaped like this:

```
🔊 Fixed the language detection bug, all tests pass now.
```

The `Stop` hook then feeds that line straight to text-to-speech. Two things fall out of doing it this way:

- **No delay.** Nothing has to call a model after the answer is finished, so the hook stays `async` and never holds up your turn.
- **The language always matches.** Claude writes that line in whatever language it was already answering in, so nothing has to be inferred.

If a turn has no such line — you set `inject_instruction: false`, or Claude skipped it on a one-sentence reply — the script falls back to summarising the answer itself and detecting the language from the text.

## Summariser (fallback path only)

Used only when no `🔊` line is present. The script picks the first available option; set `summarizer` in the config to force one.

| Mode | Requires | Speed | Cost |
|---|---|---|---|
| `api` | `ANTHROPIC_API_KEY` in the environment | ~1s | Very cheap (Haiku, ~200 tokens a call) |
| `cli` | The `claude` command on `PATH` | ~3-5s, slower from cold | Uses your existing subscription, no key needed |
| `none` | — | Instant | Free, but reads the raw text instead of a summary |

Either way the prompt goes out in the same language as the answer, so an English answer gets an English summary rather than a Thai one.

`cli` mode invokes `claude -p` with `--settings '{"disableAllHooks": true}'`. That part is not optional: without it the child session fires its own `Stop` hook and recurses forever.

## Voices

Chosen automatically in this order: Google → Azure → macOS `say` → edge-tts → Windows SAPI → espeak. Every engine has a Thai and an English voice and switches between them per utterance.

| Engine | Quality | Thai voice | English voice | How to enable |
|---|---|---|---|---|
| `google` | Best | `th-TH-Neural2-C` | `en-US-Neural2-C` | `export GOOGLE_TTS_API_KEY=...` |
| `azure` | Very good | `th-TH-PremwadeeNeural` | `en-US-JennyNeural` | `export AZURE_SPEECH_KEY=...` |
| `edge` | Good, free | `th-TH-PremwadeeNeural` | `en-US-AriaNeural` | `pip install edge-tts` |
| `say` | Adequate | `Kanya` | `Samantha` | macOS only, install the Thai voice first (below) |
| `espeak` | Robotic | `th` | `en` | Linux: `apt install espeak-ng` |

**Windows:** the built-in SAPI voices are English-only, so `pip install edge-tts` is worth it for `th-TH-PremwadeeNeural`.

**Installing the Thai voice on macOS:** System Settings → Accessibility → Spoken Content → System Voice → Manage Voices → pick Thai (Kanya) and download. Without it the script falls back to the default voice and notes it in the log.

**Linux** also needs an audio player: `apt install mpg123`.

## Configuration

Create `~/.claude/thai-secretary.json`. Every key is optional — include only what you want to change.

```json
{
  "enabled": true,
  "language": "auto",
  "notify_lang": "auto",
  "inject_instruction": true,
  "voice_engine": "auto",
  "summarizer": "auto",
  "model": "claude-haiku-4-5-20251001",
  "min_chars": 180,
  "skip_under_chars": 40,
  "max_spoken_chars": 600,
  "summarizer_timeout": 150,
  "say_voice": "Kanya",
  "say_voice_en": "Samantha",
  "speaking_rate": 0.85,
  "notify_sounds": true
}
```

- `language` — `auto` follows Claude's answer; `th` or `en` locks it to one language
- `notify_lang` — language for spoken notifications; `auto` follows the language of the last answer
- `inject_instruction` — set `false` to drop the `🔊` line from answers and go back to summarising after the fact
- `skip_under_chars` — answers shorter than this are not spoken at all
- `min_chars` — answers shorter than this are read as-is, without spending tokens on a summary
- `speaking_rate` — below 1.0 is slower, above is faster
- Each engine takes a pair of voices, `<engine>_voice` and `<engine>_voice_en`, for example `"edge_voice_en": "en-US-GuyNeural"`
- Any key can be overridden through the environment for one run: `THAI_SECRETARY_ENABLED=false claude`, `THAI_SECRETARY_LANGUAGE=en claude`

## Commands

| Command | What it does |
|---|---|
| `/lingling:mute` | Silence the voice secretary |
| `/lingling:unmute` | Turn it back on |

## Troubleshooting

- `/hooks` in Claude Code shows whether the hooks registered and which file they came from.
- `~/.claude/thai-secretary.log` records every step, from the text about to be spoken down to TTS errors. A `stop[inline]` line means it used Claude's own summary; `stop[summary]` means it fell back to summarising.
- The script always exits 0. Even a failure inside it cannot interrupt your session.
- **No `🔊` line on answers** — the `UserPromptSubmit` hook must **not** be `async`. Claude Code discards the stdout of async hooks, so the instruction never reaches the model.
- **Wrong language** — check the `stop[...] lang=...` line in the log to see what was detected. If it misfires often, pin `language` to `th` or `en`.
- **No sound at all** — run `python scripts/secretary.py --mode test`. If it reports `engine: none`, install one from the [Voices](#voices) table.
- **Overlapping speech** on rapid turns is already handled by stopping the previous utterance first. If it persists, confirm `async: true` is still set on both `Stop` and `Notification`.

## Known limits

- **Claude Code** — fully supported in the terminal, the IDE extension and the desktop app.
- **Cowork** — hooks run in a sandbox separate from your machine, so audio cannot reach your speakers. It would take an HTTP hook pointed at an endpoint on your own machine.
- **Chat (web and mobile)** — no hook system. The closest equivalent is a skill or style that ends every answer with a summary block, read by your operating system's read-aloud feature.

## License

[GPL-3.0](LICENSE)
