import aiohttp
from abc import ABC, abstractmethod
import base64
import time

from .audio_config import AudioConfigManager

class STTProvider(ABC):
    """
    通用语音识别 (Speech-to-Text) 接口类
    所有接入的大厂或自建 STT 都必须继承此类并实现 transcribe 接口
    """
    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, audio_format: str = "wav") -> str:
        """
        :param audio_bytes: 原始音频二进制流
        :param audio_format: 格式扩展名，例如 wav, mp3, ogg, amr, pcm
        :return: 解析后的纯文本字符串
        """
        pass

class CustomSTTProvider(STTProvider):
    """
    用于对接用户在使用 HuggingFace / ModelScope / 本地服务等部署的自建 STT
    """
    def __init__(self, endpoint: str, api_key: str = None):
        self.endpoint = endpoint
        self.api_key = api_key

    async def transcribe(self, audio_bytes: bytes, audio_format: str = "wav") -> str:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            
        data = aiohttp.FormData()
        data.add_field('file', audio_bytes, filename=f"audio.{audio_format}", content_type=f'audio/{audio_format}')

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.endpoint, data=data, headers=headers) as response:
                    if response.status == 200:
                        res_json = await response.json()
                        return res_json.get("text", "")
                    else:
                        error_text = await response.text()
                        print(f"[CustomSTT] Request failed: {response.status} - {error_text}")
                        return ""
        except Exception as e:
            print(f"[CustomSTT] Exception: {e}")
            return ""

class BaiduSTTProvider(STTProvider):
    """
    百度短语音识别 REST API
    """
    TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
    ASR_URL = "https://vop.baidu.com/server_api"
    SUPPORTED_FORMATS = {"pcm", "wav", "amr", "m4a"}

    def __init__(self, api_key: str, secret_key: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.access_token = None
        self.access_token_expires_at = 0.0
        
    async def _get_access_token(self):
        if self.access_token and time.time() < self.access_token_expires_at - 60:
            return self.access_token

        params = {
            "grant_type": "client_credentials",
            "client_id": self.api_key,
            "client_secret": self.secret_key,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(self.TOKEN_URL, params=params) as response:
                payload = await response.json(content_type=None)
                if response.status != 200 or not payload.get("access_token"):
                    error_message = payload.get("error_description") or payload.get("error_msg") or str(payload)
                    raise RuntimeError(f"Baidu STT token 获取失败: {error_message}")

        self.access_token = payload["access_token"]
        expires_in = int(payload.get("expires_in") or 0)
        self.access_token_expires_at = time.time() + expires_in
        return self.access_token

    async def transcribe(self, audio_bytes: bytes, audio_format: str = "wav") -> str:
        format_name = (audio_format or "wav").lower()
        if format_name not in self.SUPPORTED_FORMATS:
            raise ValueError(f"Baidu STT 暂不支持 {format_name}，请传入 wav/pcm/amr/m4a。")

        token = await self._get_access_token()
        payload = {
            "format": format_name,
            "rate": 16000,
            "channel": 1,
            "cuid": "v8chat-web-input",
            "token": token,
            "dev_pid": 1537,
            "speech": base64.b64encode(audio_bytes).decode("utf-8"),
            "len": len(audio_bytes),
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(self.ASR_URL, json=payload) as response:
                result = await response.json(content_type=None)
                if response.status != 200:
                    raise RuntimeError(f"Baidu STT 请求失败: HTTP {response.status} - {result}")

        if result.get("err_no") == 0 and result.get("result"):
            return str(result["result"][0]).strip()

        error_message = result.get("err_msg") or result.get("sn") or str(result)
        raise RuntimeError(f"Baidu STT 识别失败: {error_message}")

class MockSTTProvider(STTProvider):
    # 用于没有任何配置下的 fallback
    async def transcribe(self, audio_bytes: bytes, audio_format: str = "wav") -> str:
        return "语音识别模块暂未配置有效的 API Key。"

class STTManager:
    @staticmethod
    def get_provider() -> STTProvider:
        config = AudioConfigManager.get_config()
        stt_conf = config.get("stt", {})
        active = stt_conf.get("active_provider", "custom")
        providers_conf = stt_conf.get("providers", {})
        
        if active == "custom":
            c_conf = providers_conf.get("custom", {})
            ep = c_conf.get("endpoint")
            if ep:
                return CustomSTTProvider(ep, c_conf.get("api_key"))
        elif active == "baidu":
            b_conf = providers_conf.get("baidu", {})
            if b_conf.get("api_key") and b_conf.get("secret_key"):
                return BaiduSTTProvider(b_conf["api_key"], b_conf["secret_key"])
                
        # 返回 Mock 防止抛错导致崩溃
        return MockSTTProvider()
