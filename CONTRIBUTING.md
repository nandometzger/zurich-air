# Contributing

Thanks for your interest! Zurich Air is a small hobby project, so the process is light.

## Setup

```sh
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check
```

Generate preview images from synthetic data (no hardware or network needed):

```sh
uv run zurich-air preview
open previews/        # macOS
```

To set up a Raspberry Pi, follow [docs/raspberry-pi.md](docs/raspberry-pi.md).

## Guidelines

- Keep it small. New dependencies need a good reason — the target is a Raspberry Pi Zero 2 W.
- Rendering must stay hardware-independent; the mock and e-paper backends share one renderer.
- Never fabricate data in the live path (no guessed destinations or aircraft types).
- Add tests for logic changes (geometry, parsing, filtering, ranking).
- Run `uv run ruff format` before committing.
- For visual changes, include before/after previews in the pull request.
- Deployment files (`scripts/`, `system/`) are checked by `tests/test_deploy_files.py`;
  install [ShellCheck](https://www.shellcheck.net) to have it run on the scripts too.

## Reporting issues

Please include your config (without secrets), the log output (`journalctl -u zurich-air` on the
Pi) and, for display issues, a photo or preview image. Never include Wi-Fi names or passwords.
