# Contributing

Thanks for your interest! Zurich Air is a small hobby project, so the process is light:
open an issue to discuss an idea or report a problem, or send a pull request.

## Getting started

```sh
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check
uv run zurich-air preview      # synthetic scenes → previews/, no hardware or network
```

[docs/development.md](docs/development.md) covers the rest: the project layout, previews,
replaying real traffic, `zurich-air explain`, the tests and CI. To set up a Raspberry Pi,
follow [docs/raspberry-pi.md](docs/raspberry-pi.md).

## Guidelines

- Keep it small. New dependencies need a good reason — the target is a Raspberry Pi Zero 2 W.
- Rendering must stay hardware-independent; the mock and e-paper backends share one renderer.
- Never fabricate data in the live path (no guessed destinations or aircraft types).
- Add tests for logic changes (geometry, parsing, filtering, ranking).
- Run `uv run ruff format` before committing.
- For visual changes, include before/after previews in the pull request, and regenerate
  the documentation images (`uv run python scripts/render-docs-images.py`).
- Deployment files (`scripts/`, `system/`) are checked by `tests/test_deploy_files.py`;
  install [ShellCheck](https://www.shellcheck.net) to have it run on the scripts too.

## Reporting issues

Please include your config (without secrets), the output of `zurich-air doctor`, the log
output (`journalctl -u zurich-air` on the Pi) and, for display issues, a photo or preview
image. [docs/troubleshooting.md](docs/troubleshooting.md) may already have the answer.
Never include Wi-Fi names or passwords, or API keys.
