# Contributing to One-Click MuJoCo on Azure

Thanks for wanting to help make MuJoCo deploys on Azure less painful. Here's how to contribute.

## Quick Start for Contributors

```bash
git clone https://github.com/Haptic-AI/one-click-mujoco-azure.git
cd one-click-mujoco-azure
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## What We're Looking For

Check `issues.md` for tracked issues. Beyond that, we welcome:

- **Bug fixes** -- if `hcloud` broke for you, it probably broke for someone else too
- **New VM presets** -- tested configs for other Azure VM sizes
- **Startup script improvements** -- faster installs, better error handling, new packages
- **Documentation** -- clearer explanations, more examples, better troubleshooting tips
- **Robot demos** -- new MJCF models, dm_control tasks, or RL examples

## How to Submit Changes

1. Fork the repo
2. Create a branch (`git checkout -b fix/your-fix-name`)
3. Make your changes
4. Test locally -- at minimum, run `hcloud preflight` and verify imports:
   ```bash
   python -c "from hcloud import cli, config, azure_vm; print('OK')"
   ```
5. For changes to the startup script or deploy flow, run the end-to-end test:
   ```bash
   pytest -m slow tests/test_deploy.py
   ```
   (This deploys a real VM and costs ~$0.19 for the CPU preset. Test takes ~15 min.)
6. Open a PR with a clear description of what you changed and why

## Code Style

- Keep it simple. This project values clarity over cleverness.
- No unnecessary abstractions. If three lines of code are clearer than a helper function, keep the three lines.
- Use `python3` consistently (not `python`) in scripts and docs.
- Error messages should tell the user what to do, not just what went wrong.

## Reporting Issues

If something broke during deploy, include:
- The `hcloud` command you ran
- The full error output
- Your Azure region and VM preset
- Whether you're on CPU or GPU

Add it to `issues.md` via PR, or open a GitHub issue.

## Cost Awareness

If your PR changes the deploy flow or startup script, test with `--preset cpu` first (~$0.19/hr). Don't leave test VMs running. Always `hcloud destroy` when done.

## Contributors

| Name | Links |
|------|-------|
| **Diego Prats** | [LinkedIn](https://www.linkedin.com/in/diegoprats/) -- [X/Twitter](https://x.com/mexitlan) |
| **Chris Mendez** | [LinkedIn](https://www.linkedin.com/in/chrisjmendez/) -- [X/Twitter](https://x.com/0xchrism) |

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
