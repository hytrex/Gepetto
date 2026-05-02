import os
import shutil
import subprocess
import threading

import idaapi
import ida_kernwin

import gepetto.config
from gepetto.ida.status_panel.panel_interface import LogCategory, LogLevel
from gepetto.ida.status_panel.status_panel_factory import get_status_panel

_ = gepetto.config._

STATUS_PANEL = get_status_panel()

_wrapper_process = None
_wrapper_lock = threading.Lock()


def _find_claude_cli():
    """Locate the claude CLI binary."""
    found = shutil.which("claude")
    if found:
        return found
    if os.name == "nt":
        for candidate in (
            os.path.expandvars(r"%APPDATA%\npm\claude.cmd"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\claude\claude.exe"),
        ):
            if os.path.isfile(candidate):
                return candidate
    return None


def _wrapper_server_running():
    """Check if the wrapper is reachable."""
    import httpx
    base_url = gepetto.config.get_config(
        "ClaudeWrapper", "BASE_URL", default="http://localhost:8000/v1"
    )
    base_url = base_url.rstrip("/")
    try:
        r = httpx.get(f"{base_url}/models", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


class ClaudeLoginHandler(idaapi.action_handler_t):
    """Runs 'claude auth login' to authenticate with a Claude.ai account."""

    def activate(self, ctx):
        claude_bin = _find_claude_cli()
        if not claude_bin:
            msg = _(
                "Claude CLI not found. Install it first:\n"
                "  npm install -g @anthropic-ai/claude-code\n"
                "Then try again."
            )
            ida_kernwin.info(msg)
            STATUS_PANEL.log(msg, category=LogCategory.SYSTEM, level=LogLevel.ERROR)
            return 1

        STATUS_PANEL.log(
            _("Opening Claude.ai login in browser..."),
            category=LogCategory.SYSTEM,
        )

        def _run_login():
            try:
                creation_flags = 0
                if os.name == "nt":
                    creation_flags = subprocess.CREATE_NEW_CONSOLE
                subprocess.Popen(
                    [claude_bin, "auth", "login"],
                    creationflags=creation_flags,
                )
            except Exception as e:
                print(_("Claude login failed: {error}").format(error=e))

        threading.Thread(target=_run_login, daemon=True).start()
        return 1

    def update(self, ctx):
        return idaapi.AST_ENABLE_ALWAYS


class ClaudeStartWrapperHandler(idaapi.action_handler_t):
    """Starts the claude-code-openai-wrapper server in the background."""

    def activate(self, ctx):
        global _wrapper_process

        if _wrapper_server_running():
            ida_kernwin.info(_("Claude.ai wrapper is already running."))
            return 1

        wrapper_bin = shutil.which("claude-wrapper") or shutil.which("claude-code-openai-wrapper")
        if not wrapper_bin:
            msg = _(
                "claude-wrapper not found. Install it:\n"
                "  pip install git+https://github.com/RichardAtCT/claude-code-openai-wrapper.git\n"
                "Then try again."
            )
            ida_kernwin.info(msg)
            STATUS_PANEL.log(msg, category=LogCategory.SYSTEM, level=LogLevel.ERROR)
            return 1

        STATUS_PANEL.log(
            _("Starting Claude.ai wrapper server..."),
            category=LogCategory.SYSTEM,
        )

        def _run_wrapper():
            global _wrapper_process
            try:
                creation_flags = 0
                if os.name == "nt":
                    creation_flags = subprocess.CREATE_NEW_CONSOLE
                    cmd = f'echo n | "{wrapper_bin}"'
                    with _wrapper_lock:
                        _wrapper_process = subprocess.Popen(
                            cmd,
                            shell=True,
                            creationflags=creation_flags,
                        )
                else:
                    with _wrapper_lock:
                        _wrapper_process = subprocess.Popen(
                            [wrapper_bin],
                            stdin=subprocess.PIPE,
                        )
                        try:
                            _wrapper_process.stdin.write(b"n\n")
                            _wrapper_process.stdin.flush()
                            _wrapper_process.stdin.close()
                        except Exception:
                            pass

                import time
                for _ in range(10):
                    time.sleep(2)
                    if _wrapper_server_running():
                        break

                try:
                    from gepetto.models.claude_wrapper import ClaudeWrapper
                    ClaudeWrapper.refresh_models_sync()
                except Exception:
                    pass

                def _notify_started():
                    STATUS_PANEL.log(
                        _("Claude.ai wrapper server started. Models will appear in the menu shortly."),
                        category=LogCategory.SYSTEM,
                        level=LogLevel.SUCCESS,
                    )
                    try:
                        from gepetto.ida import ui as ida_ui
                        ida_ui.trigger_model_select_menu_regeneration()
                    except Exception:
                        pass

                ida_kernwin.execute_sync(_notify_started, ida_kernwin.MFF_FAST)

            except Exception as e:
                print(_("Failed to start wrapper: {error}").format(error=e))

        threading.Thread(target=_run_wrapper, daemon=True).start()
        return 1

    def update(self, ctx):
        return idaapi.AST_ENABLE_ALWAYS


class ClaudeStopWrapperHandler(idaapi.action_handler_t):
    """Stops the wrapper server if it was started from IDA."""

    def activate(self, ctx):
        global _wrapper_process
        with _wrapper_lock:
            if _wrapper_process and _wrapper_process.poll() is None:
                _wrapper_process.terminate()
                _wrapper_process = None
                STATUS_PANEL.log(
                    _("Claude.ai wrapper server stopped."),
                    category=LogCategory.SYSTEM,
                )
            else:
                ida_kernwin.info(_("No wrapper server was started from IDA."))
        return 1

    def update(self, ctx):
        return idaapi.AST_ENABLE_ALWAYS
