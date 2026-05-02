import asyncio
import threading
import time

import httpx as _httpx
import openai

from gepetto.models.openai import GPT
import gepetto.models.model_manager
import gepetto.config

_ = gepetto.config._

_WRAPPER_MODELS = None
_WRAPPER_MODELS_LOCK = threading.Lock()
_WRAPPER_REFRESH_THREAD: threading.Thread | None = None
_WRAPPER_LAST_REFRESH: float = 0.0

_DEFAULT_BASE_URL = "http://localhost:8000/v1"


def _trigger_menu_refresh() -> None:
    try:
        from gepetto.ida import ui as ida_ui
        ida_ui.trigger_model_select_menu_regeneration()
    except Exception:
        pass


def _normalize_base_url(base_url: str | None) -> str:
    if not base_url:
        base_url = _DEFAULT_BASE_URL
    if not base_url.endswith("/"):
        base_url = base_url + "/"
    return base_url


def _update_wrapper_models(models: list[str], *, notify: bool = True) -> None:
    global _WRAPPER_MODELS
    normalized = sorted(dict.fromkeys(models))
    with _WRAPPER_MODELS_LOCK:
        current = list(_WRAPPER_MODELS) if _WRAPPER_MODELS is not None else []
        if normalized == current:
            return
        _WRAPPER_MODELS = normalized
    if notify:
        _trigger_menu_refresh()


def _execute_wrapper_fetch(
    base_url: str | None,
    api_key: str | None,
    proxy: str | None,
    timeout: _httpx.Timeout,
) -> list[str]:
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(
            _fetch_wrapper_models_async(base_url, api_key, proxy, timeout)
        )
    except Exception:
        return []
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def _schedule_wrapper_refresh(
    base_url: str | None,
    api_key: str | None,
    proxy: str | None,
    timeout: _httpx.Timeout,
) -> None:
    global _WRAPPER_REFRESH_THREAD, _WRAPPER_LAST_REFRESH
    with _WRAPPER_MODELS_LOCK:
        if _WRAPPER_REFRESH_THREAD and _WRAPPER_REFRESH_THREAD.is_alive():
            return
        now = time.monotonic()
        if now - _WRAPPER_LAST_REFRESH < 5.0:
            return
        _WRAPPER_LAST_REFRESH = now
        _WRAPPER_REFRESH_THREAD = threading.Thread(
            target=_refresh_wrapper_models_background,
            args=(base_url, api_key, proxy, timeout),
            name="GepettoClaudeWrapperModelRefresh",
            daemon=True,
        )
        _WRAPPER_REFRESH_THREAD.start()


def _refresh_wrapper_models_background(
    base_url: str | None,
    api_key: str | None,
    proxy: str | None,
    timeout: _httpx.Timeout,
) -> None:
    global _WRAPPER_REFRESH_THREAD
    try:
        models = _execute_wrapper_fetch(base_url, api_key, proxy, timeout)
        if models:
            _update_wrapper_models(models)
    finally:
        with _WRAPPER_MODELS_LOCK:
            _WRAPPER_REFRESH_THREAD = None


async def _fetch_wrapper_models_async(
    base_url: str | None,
    api_key: str | None,
    proxy: str | None,
    timeout: _httpx.Timeout,
) -> list[str]:
    resolved_base = _normalize_base_url(base_url)
    endpoint = f"{resolved_base}models"
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    transport = None
    if proxy:
        try:
            transport = _httpx.AsyncHTTPTransport(proxy=proxy)
        except Exception:
            pass
    try:
        async with _httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.get(endpoint, headers=headers)
    except (
        _httpx.ConnectError,
        _httpx.ConnectTimeout,
        _httpx.ReadTimeout,
        _httpx.TimeoutException,
    ):
        return []

    if response.status_code != 200:
        return []

    payload = response.json() or {}
    models = [
        model.get("id")
        for model in payload.get("data", [])
        if isinstance(model, dict) and model.get("id")
    ]
    models.sort()
    return models


class ClaudeWrapper(GPT):
    @staticmethod
    def get_menu_name() -> str:
        return "Claude.ai (Wrapper)"

    @staticmethod
    def supported_models() -> list:
        global _WRAPPER_MODELS
        with _WRAPPER_MODELS_LOCK:
            if _WRAPPER_MODELS is None:
                _WRAPPER_MODELS = []
            models = list(_WRAPPER_MODELS)

        base_url = gepetto.config.get_config(
            "ClaudeWrapper", "BASE_URL", default=_DEFAULT_BASE_URL
        )
        api_key = gepetto.config.get_config("ClaudeWrapper", "API_KEY")
        proxy = gepetto.config.get_config("Gepetto", "PROXY")
        timeout = _httpx.Timeout(2.0, connect=2.0)
        _schedule_wrapper_refresh(base_url, api_key, proxy, timeout)
        return models

    @staticmethod
    def is_configured_properly() -> bool:
        return True

    @staticmethod
    def refresh_models_sync() -> list[str]:
        base_url = gepetto.config.get_config(
            "ClaudeWrapper", "BASE_URL", default=_DEFAULT_BASE_URL
        )
        api_key = gepetto.config.get_config("ClaudeWrapper", "API_KEY")
        proxy = gepetto.config.get_config("Gepetto", "PROXY")
        timeout = _httpx.Timeout(2.0, connect=2.0)
        models = _execute_wrapper_fetch(base_url, api_key, proxy, timeout)
        if models:
            _update_wrapper_models(models)
        with _WRAPPER_MODELS_LOCK:
            current = list(_WRAPPER_MODELS) if _WRAPPER_MODELS is not None else []
        return current

    def __init__(self, model):
        try:
            super().__init__(model)
        except ValueError:
            self._streaming_restriction_active = False
            self._fallback_notice_sent = False

        base_url = gepetto.config.get_config(
            "ClaudeWrapper", "BASE_URL", default=_DEFAULT_BASE_URL
        )
        api_key = gepetto.config.get_config("ClaudeWrapper", "API_KEY") or "NO_API_KEY"
        proxy = gepetto.config.get_config("Gepetto", "PROXY")

        self.model = model
        self.client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=_httpx.Client(proxy=proxy) if proxy else None,
        )


gepetto.models.model_manager.register_model(ClaudeWrapper)
