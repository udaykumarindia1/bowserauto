# AVINASH — HUD & assistant

The assistant is called **AVINASH** by default (rename it any time in *Customise* or by saying
"your name is …"; the wake word always follows the current name). The UI is an Iron-Man/JARVIS
style heads-up display: cyan `#00d4ff` accent on a near-black surface,
scan-lines, corner brackets, a reactive core, letter-spaced uppercase labels and a status strip —
and the assistant is a first-class control surface, not a decoration. The functionality of
[FatihMakes/Mark-LV](https://github.com/FatihMakes/Mark-LV) (a PyQt desktop JARVIS) was ported
to the web and wired to the automation backend.

## Layers

| Layer | Where | Purpose |
| --- | --- | --- |
| Theme | `frontend/src/lib/theme.ts`, `app/globals.css` | `--hue/--sat` → `--hud-*` CSS variables; live recolouring from the hue wheel, a hex field or a voice/text command (`change colour to amber`); persisted in `localStorage` (`ba.accent`, `ba.theme`) |
| HUD primitives | `components/hud/hud-primitives.tsx`, `hud-core.tsx`, `sys-monitor.tsx` | panels, pills, brackets, reactive arc-reactor core bound to `HudState` (`INITIALISING\|ONLINE\|LISTENING\|THINKING\|PROCESSING\|SPEAKING\|OFFLINE`), system monitor (CPU/RAM/disk/load from `GET /api/assistant/system`) |
| Console | `components/hud/console.tsx`, `lib/jarvis-store.ts` | right-hand AVINASH console: chat bubbles (`j` / `u` / `sys` / `log`), typewriter replies, live event log, confirm banner, mic + wake buttons, level meter |
| Controller | `hooks/use-jarvis.ts`, `lib/voice.ts` | sends commands to `POST /api/assistant/command`, applies `navigate`/`client` instructions, speaks replies (Web Speech synthesis with selectable voice/rate), push-to-talk (**Ctrl+Space**) and wake word (`Hey <assistant name>`) via Web Speech recognition, real-time subscription for proactive notices |
| Customise | `components/hud/customize-overlay.tsx`, `memory-panel.tsx`, Settings → *Memory* | assistant name (default AVINASH; stored under `preferences.jarvis.assistant_name` for backwards compatibility), your name, voice, speech rate, TTS on/off, wake word, typewriter, HUD style (`core`/`minimal`); memory panel to view/delete stored facts |
| Backend | `app/services/assistant_service.py`, `app/api/assistant.py`, `app/core/sysinfo.py`, `MemoryFact` model | deterministic command grammar (no LLM required), confirmation gate, memory with secret refusal, briefing, host metrics |

## Command grammar (`POST /api/assistant/command`)

| Intent | Examples | Effect |
| --- | --- | --- |
| `help` | "what can you do" | capability list |
| `status` | "status", "sitrep", "update me" | queue/attention/session summary (also spoken) |
| `stop_everything` | "stop everything" | **confirm** → `POST /api/tasks/stop-all` |
| `task_control` | "pause", "resume the render", "take over", "release", "retry", "skip", "stop task X" | resolves the current/named task from page context and calls the control endpoint |
| `create_task` | "run: open example.com and list the headings" | **confirm** → queues a generic browser task |
| `generate_project` | "generate the videos for Project Nova" | queues a video-generation task for the project |
| `new_session` / `open_session` / `navigate` | "new session for kling", "open the Kling session", "go to https://… in Kling" | session management via the sessions API |
| `open_page` | "open tasks", "show me the files page" | client navigation |
| `remember` / `recall` / `forget` | "remember my render preset is 1080p", "what do you remember", "forget the render preset" | `MemoryFact` CRUD; values that look like passwords/tokens/keys are refused |
| `set_theme` / `set_name` / `set_assistant` | "change colour to #ff6b00", "call me Tony", "your name is Friday" | client preferences |

Replies are concise action summaries; the assistant never exposes internal reasoning. Role
gating applies: viewers can ask anything but every mutating intent is refused with a clear
message.

## Mark-LV feature mapping

| Mark-LV (desktop) | BowserAuto (web) |
| --- | --- |
| Holographic avatar + lip sync | reactive HUD core animated by state and speech output |
| Push-to-talk Ctrl+Space, wake word "Hey Jarvis" | identical shortcuts via Web Speech API (Chrome/Edge); wake word is "Hey Avinash" (tolerant of mis-hearings) and follows the chosen assistant name |
| Instant acknowledgement replies | the HUD core switches to THINKING the moment a command is sent and the reply is typed/spoken as soon as it arrives |
| Self-describing skills/plugins | intent grammar + `GET /api/assistant/capabilities`; browser skills come from the platform adapter registry |
| Memory + Memory Panel, Undo | `MemoryFact` API, Settings → Memory panel, `forget …` command |
| Real confirmation buttons for irreversible actions | `confirm` responses rendered as a banner with explicit buttons (STOP EVERYTHING, queueing agent tasks, clearing memory) |
| Voice picker, assistant name, colour customisation | Customise overlay (voice, rate, names, hue wheel/hex) |
| Model/provider selection | Server-side multi-provider abstraction: OpenAI-compatible presets, native Gemini/Ollama/Anthropic, plus heuristic fallback |
| Hardware monitoring | SYS MONITOR panel from `/api/assistant/system` |
| Morning briefing / proactive check-ins | `GET /api/assistant/briefing` on login; proactive console notices for `task.attention`, `human.required`, failures, completions (browser notifications optional) |
| Autonomous agent tasks, browser control, file processor | the core product: task queue, browser agent, sessions, generated files |
| Screen/webcam vision, system control, clipboard, phone dashboard, reminders, weather | desktop-only — replaced by the live browser viewer (screen of the automation browser), paste-into-page input, and the notifications centre; not re-implemented as OS-level features in a web app |

## Keyboard

`Ctrl+Space` push-to-talk · `Esc` cancel speech / close overlays · `/` focus the console input.
