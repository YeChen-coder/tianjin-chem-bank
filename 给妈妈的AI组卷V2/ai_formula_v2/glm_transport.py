"""Bounded, GLM-only acceptance client. Credentials remain in memory."""
import base64
import json
import os
from pathlib import Path

from .protocol import ProtocolError

CONFIG_KEYS = {"GLM_API_KEY", "GLM_FLASH", "GLM_PRO", "GLM_API_URL",
               "GLM_MAX_TOKENS", "GLM_REASONING_EFFORT", "CHEM_DISABLE_AI"}


def read_glm_config(keys_path=None):
    """Read only GLM fields from the explicitly selected existing file, never copy it."""
    config = {}
    if keys_path is not None:
        for line in Path(keys_path).read_text(encoding="utf-8").splitlines():
            if line.strip().startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            if key.strip() in CONFIG_KEYS:
                config[key.strip()] = value.strip().strip('"').strip("'")
    config.update({key: os.environ[key] for key in CONFIG_KEYS if os.environ.get(key)})
    return config


class GLMClient:
    def __init__(self, config, *, max_calls=6, timeout=90, observe=None):
        if config.get("CHEM_DISABLE_AI") == '1' or not config.get("GLM_API_KEY"):
            raise ProtocolError("GLM_CONFIG", "GLM 配置不可用")
        self._config = dict(config)
        self.max_calls, self.timeout, self.observe = max_calls, timeout, observe
        self.calls = []

    def complete(self, role, system, user, image_paths=None):
        if len(self.calls) >= self.max_calls:
            raise ProtocolError("CALL_LIMIT", "受控 GLM 调用上限已达到")
        images = image_paths or []
        if images and role != "flash":
            raise ProtocolError("MODEL_IMAGE_UNSUPPORTED", "有图题目必须使用已核对的图像型号")
        model = self._config.get("GLM_FLASH" if role == "flash" else "GLM_PRO") or ("glm-5.3-flash" if role == "flash" else "glm-5.3")
        # Official 5.3-Flash model card supports image input. Unknown models require verification.
        if images and model != "glm-5.3-flash":
            raise ProtocolError("MODEL_IMAGE_UNSUPPORTED", "当前配置型号尚未核对图像能力")
        content = [{"type": "text", "text": user}]
        for path in images:
            path = Path(path)
            mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(path.suffix.lower())
            if not mime or not path.is_file() or path.stat().st_size > 8*1024*1024:
                raise ProtocolError("ASSET_MISSING", "图片预览不可用或超过大小限制")
            data = base64.b64encode(path.read_bytes()).decode('ascii')
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
        tokens = int(self._config.get("GLM_MAX_TOKENS") or 8192)
        effort = self._config.get("GLM_REASONING_EFFORT") or "high"
        if not 512 <= tokens <= 32768 or effort not in ("low", "high", "max"):
            raise ProtocolError("GLM_CONFIG", "GLM 输出额度或推理强度不合法")
        body = {"model": model, "messages": [{"role": "system", "content": system},
                {"role": "user", "content": content if images else user}], "stream": False,
                "max_tokens": tokens, "temperature": 1, "top_p": .95,
                "thinking": {"type": "enabled"}, "response_format": {"type": "json_object"}}
        if model.startswith(("glm-5.2", "glm-5.3")):
            body["reasoning_effort"] = effort
        meta = {"model": model, "json_mode": True, "image_count": len(images),
                "max_tokens": tokens, "reasoning_effort": effort, "status": "requested"}
        self.calls.append(meta)
        if self.observe:
            self.observe(meta)
        url = self._config.get("GLM_API_URL") or "https://open.bigmodel.cn/api/paas/v4/chat/completions"
        if not url.startswith("https://"):
            raise ProtocolError("GLM_CONFIG", "真实 GLM 验收需要 HTTPS 地址")
        # Reuse legacy bounded keepalive-aware HTTP; socket timeout alone is not a deadline.
        try:
            from aivariant import _http_post
            status, data = _http_post(url, self._config['GLM_API_KEY'], body, self.timeout)
        except Exception:
            meta["status"] = "transport_error"
            raise ProtocolError("GLM_TRANSPORT", "GLM 网络响应未完成") from None
        if status != 200:
            code = data.get('error', {}).get('code') if isinstance(data, dict) and isinstance(data.get('error'), dict) else None
            meta.update(status='http_error', http_status=status, provider_code=code)
            raise ProtocolError('GLM_HTTP', f'GLM 请求失败 HTTP {status}' + (f'，代码 {code}' if code else ''))
        try:
            choice = data['choices'][0]
            meta.update(status="received", finish_reason=choice.get('finish_reason'), usage=data.get('usage'))
            if choice.get('finish_reason') != 'stop':
                raise ProtocolError("GLM_OUTPUT_LIMIT", "GLM 未返回完整停止响应，可能被截断")
            text = choice['message']['content']
            if not isinstance(text, str) or not text.strip():
                raise ProtocolError("GLM_EMPTY", "GLM 没有正文 JSON")
            return text, model
        except (KeyError, TypeError, IndexError):
            raise ProtocolError("GLM_RESPONSE", "GLM 返回结构不完整") from None
