# Platform adapters

Platform adapters are plug-ins under `backend/app/platforms/<key>/__init__.py`. The registry
(`app/platforms/registry.py`) discovers every sub-package at start-up, so adding a platform is a
single new folder. Nothing in the agent, the queue or the UI is hard-coded to a specific website.

Shipped adapters: `google_flow`, `runway`, `kling`, `luma`, `hailuo`, `pika`, `pixverse`, `vidu`
(video), `chatgpt`, `gemini`, `arena` (chat/text via `chat_base`), `generic` (any site through
the AI agent) and `mock` (built-in Mock Video Generator).

> Third-party UIs change often. Each adapter carries a `verified` flag and its selectors are
> declared as *target specs* with alternatives (`{"any": [...]}`), so a broken selector degrades to
> the next candidate and finally to the generic agent or a `needs_attention` pause — never to a
> silent hang.

## Generation workflow

`PlatformAdapter.generate(ctx, target_dir)` runs the standard pipeline; override individual steps
when a platform needs something special:

| Step | Default behaviour | Override when… |
| --- | --- | --- |
| `open_generation_tool` | navigate to `generation_url` or `start_url()` | the tool lives behind menus |
| `ensure_ready` | `check_blockers()` → human-check / login detection → raises `HumanRequired`/`LoginRequired` | platform shows cookie walls, onboarding modals |
| `upload_references` | fills `selectors["upload"]` with the scene's reference assets | drag-and-drop only UIs |
| `enter_prompt` | types the prompt into `selectors["prompt_input"]` | rich-text editors |
| `configure` | selects duration/aspect ratio via templated selectors (`{value}`) | extra settings (model, seed, style) |
| `start_generation` | clicks `selectors["generate_button"]` | confirmation dialogs |
| `detect_state` | matches `status_patterns`, `error_patterns`, `human_check_patterns` against page text + indicator selectors → `GenerationState` | progress is only in the DOM/ARIA tree |
| `wait_for_completion` | polls `detect_state` every `poll_interval_seconds` until COMPLETED/DOWNLOAD_READY, raising on FAILED/BLOCKED/human check, honouring `generation_timeout_seconds` and `stall_timeout_seconds` (calls `on_stall`) | websockets/push updates |
| `download_result` | clicks `selectors["download_button"]`, falls back to `download_via_media_src` (`<video src>`), verifies size/MIME | downloads need an extra menu |

Every step reports through `ctx.set_state(state, detail)` and `ctx.say(message)` (concise action
reasoning shown in the UI; no chain-of-thought) and honours the control token (pause/stop/takeover)
between actions. **Never** add `asyncio.sleep(<large>)`; use state detection with timeouts.

## Adding an adapter

```python
# backend/app/platforms/acme_video/__init__.py
from typing import Any, ClassVar
from app.platforms.base import AdapterContext, PlatformAdapter


class AcmeVideoAdapter(PlatformAdapter):
    key = "acme_video"
    name = "Acme Video"
    category = "video"                       # video | image | chat | generic
    base_url = "https://acme.example.com"
    generation_url = "https://acme.example.com/create"
    description = "Text-to-video with reference frames."
    requires_login = True
    output_extension = ".mp4"
    verified = False                         # flip once selectors are validated against the live site
    capabilities: ClassVar[dict[str, Any]] = {"max_clip_seconds": 10, "aspect_ratios": ["16:9", "9:16"], "reference_images": True}
    selectors: ClassVar[dict[str, Any]] = {
        "prompt_input": {"any": [{"testid": "prompt"}, {"role": "textbox", "name": "/prompt/i"}]},
        "upload": {"css": "input[type=file]"},
        "generate_button": {"role": "button", "name": "/^(generate|create)$/i"},
        "download_button": {"role": "link", "name": "/download/i"},
        "result_video": {"css": "video[src]"},
        "duration_option": {"css": "[data-duration='{value}']"},
        "aspect_ratio_option": {"css": "[data-ratio='{value}']"},
        "logged_in_indicator": {"css": "[data-testid=user-menu]"},
        "login_indicator": {"css": "a[href*='login']"},
    }
    status_patterns: ClassVar[dict[str, list[str]]] = {
        "QUEUED": [r"in queue"],
        "GENERATING": [r"generating", r"\b\d{1,3}%"],
        "PROCESSING": [r"finalizing"],
        "COMPLETED": [r"your video is ready"],
    }

    async def configure(self, ctx: AdapterContext) -> None:
        await super().configure(ctx)
        # e.g. pick a model from a dropdown
        await ctx.driver.click(self.selector("model_menu"))
```

1. Create the package; the class is picked up automatically (one subclass per package).
2. Target specs accept `css`, `testid`, `role`+`name`, `text`, `placeholder`, `label`, `xpath`;
   wrap alternatives in `{"any": [...]}`; use `{value}` placeholders for templated options.
3. Run the unit tests (`pytest tests/test_units.py -k adapter`) — they instantiate every adapter
   through the registry and validate metadata/selectors.
4. Enable it per organisation under **Platforms** (admins) — new adapters start enabled.

## Chat/text platforms

`app/platforms/chat_base.py` implements the prompt → wait for answer → save text workflow used by
`chatgpt`, `gemini` and `arena` (`output_kind = "text"`); override `extract_answer` if the
response container differs.

## Generic agent

`generic` has no selectors. Tasks on unknown sites (or `agent_mode="generic"`) run the
tool-based `BrowserAgent` which plans with the configured LLM (or the heuristic planner) using
`browser.*` (open, click, type, select, scroll, extract, get_page_text, get_accessibility_tree,
screenshot, wait, upload, download, tabs), `filesystem.*` (save_file, rename, move, list,
create_folder) and `task.*` (update_status, needs_human, retry, pause, resume, complete, fail)
tools. Every tool call is stored as an `AgentAction` with a one-line reason and shown in the
Agent decisions panel.

## Simulation options (mock adapter)

`config.mock_options` on a task: `speed` (`instant|fast|normal|slow`), `fail_rate` (0–1),
`login_required` (bool), `human_check_attempt` (int: show a "verify you are human" wall on that
attempt). These drive the retry, attention and takeover paths in smoke tests without AI credits.
