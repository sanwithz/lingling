<p align="center">
  <img src="assets/mockup.svg" width="700" alt="lingling speaking a summary aloud after Claude Code finishes a task">
</p>

<h1 align="center">lingling</h1>

<p align="center">
  <em>Claude finishes the job, then tells you what it did — out loud</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-GPL--3.0-111111?style=flat-square" alt="License">
  <img src="https://img.shields.io/badge/plugin-Claude%20Code-111111?style=flat-square" alt="Claude Code plugin">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-111111?style=flat-square" alt="Platform">
  <img src="https://img.shields.io/badge/voice-th--TH%20%7C%20en--US-111111?style=flat-square" alt="Thai and English voice">
</p>

---

**lingling** (ลิงลิง) is Thai for *monkey*.

That is the point. This plugin is here to evolve you backwards. Work gets easy enough that you stop reading, stop scrolling, stop thinking — you just sit there and listen while somebody else does the job. Congratulations, you are a monkey again.

## What it does

You give Claude Code a task and wander off. When it's done, it **says out loud** what it did, in one or two sentences.

That same sentence is also printed at the end of the answer, so if you weren't listening, you can just read it.

It speaks **Thai when Claude answers in Thai, and English when Claude answers in English**. Nothing to switch.

It also speaks up when Claude is stuck waiting for your permission or your input, so you're not sitting in silence wondering why nothing is happening.

## Install

You need **Python 3.9 or newer** first. Get it from [python.org](https://www.python.org/downloads/) — not the Microsoft Store version, that one doesn't work. On Windows you also need [Git for Windows](https://git-scm.com/download/win), which most people already have.

**After installing Python, close every terminal window and open a fresh one.** Otherwise it won't be found.

Then, inside Claude Code, send these as two separate messages. The first one tells
Claude Code where to find lingling; the second one installs it.

```
/plugin marketplace add vectorkub/lingling
```

```
/plugin install lingling@lingling
```

That's it. Restart Claude Code and it will start talking.

## How to use it

There is nothing to use. Talk to Claude Code the way you always do. It will start talking back.

If you need quiet for a moment:

| | |
|---|---|
| `/lingling:mute` | Stop talking |
| `/lingling:unmute` | Start talking again |

## The voice

The voice your computer ships with is rough. On Windows there is no Thai voice at all, so it sounds Thai out with an English mouth; on macOS the Thai voice is an old one, flat and choppy.

So lingling doesn't use it. The first time it runs, it quietly installs a free, natural-sounding voice for both languages, and speaks with that instead. There is nothing for you to do, and nothing else on your computer is touched — it all lives in one folder, `~/.claude/lingling-venv`, and deleting that folder undoes it.

Two things worth knowing. The good voice is an online one, so with no internet it falls back to your computer's own voice rather than going quiet. And on Linux, it also needs something to play sound through: `sudo apt install mpg123`.

## If it's not talking

- Give it a real task. Very short answers are skipped on purpose — you don't need a voice to tell you "yes".
- Make sure you opened a **new** terminal window after installing Python.
- Check that it's not muted: `/lingling:unmute`.
- Voice sounds robotic? The good one installs in the background, so the first answer or two may speak before it is ready. `~/.claude/lingling-install.json` says how that went.
- Still nothing? `~/.claude/thai-secretary.log` records what happened, including why it stayed quiet.

## Good to know

Works in Claude Code — terminal, IDE extension, and desktop app. It can't work in Claude on the web or on your phone, because those have no way to reach your speakers.

## License

[GPL-3.0](LICENSE)
