"""GigaChat Lite integration for intent recognition.

Uses Sber OAuth2 flow:
  POST https://ngw.devices.sberbank.ru:9443/api/v2/oauth  -> access_token (30 min)
  POST https://gigachat.devices.sberbank.ru/api/v1/chat/completions

Safety:
  - Pydantic structured output parsing
  - Jailbreak detection (regex)
  - Service-id validation against price list
  - Low confidence fallback (< 0.75)
  - Non-JSON response fallback
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Literal, Optional

import aiohttp
from pydantic import BaseModel, Field

from bot.config import get_settings
from bot.services.price_loader import PriceLoader

logger = logging.getLogger(__name__)

_OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
_CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

_JAILBREAK_PATTERNS = re.compile(
    r"(?i)(игнорируй\s+инструкц|забудь\s+(все|систем|инструкц)|ignore\s+instructions|"
    r"forget\s+(your|system|all)|you\s+are\s+now|act\s+as\s+if|bypass|override\s+system|"
    r"новая\s+роль|притворись|представь\s+что\s+ты)",
    re.UNICODE,
)


class ServiceMatch(BaseModel):
    service_id: str
    quantity: int = 1


class IntentResult(BaseModel):
    action: Literal[
        "greet",
        "show_category",
        "start_kp_form",
        "add_to_cart",
        "remove_from_cart",
        "view_cart",
        "create_kp",
        "explain",
        "faq",
        "catalog",
        "clear_cart",
        "repeat_order",
        "unknown",
    ]
    services: list[ServiceMatch] = Field(default_factory=list)
    explanation_query: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)


def _build_system_prompt(services_text: str, cart_text: str) -> str:
    return f"""Ты — вежливый, дружелюбный и профессиональный помощник экологической лаборатории «Аналитик.Лаб».

Твоя главная цель — общаться с пользователем максимально естественно и приятно.

=== ПРАВИЛА ПОВЕДЕНИЯ (строго соблюдай порядок приоритета) ===

1. Если пользователь здоровается («привет», «здравствуйте», «добрый день» и т.п.) — ответь тепло и предложи помощь.

2. Если пользователь спрашивает про компанию / лабораторию («расскажи про компанию», «что за лаборатория», «про аналитик лаб») — дай красивый, живой small-talk ответ на основе информации о компании. Не добавляй ничего в корзину.

3. Если пользователь спрашивает «какие есть анализы…», «варианты анализа…», «что по почве / воде / воздуху / радиации» — покажи соответствующую категорию или наиболее релевантные услуги. НЕ добавляй автоматически в корзину.

4. Если пользователь явно хочет добавить услугу («добавь», «положи в корзину», «нужен анализ X», «закажи X», «мне нужен X») — используй action add_to_cart.

5. Если пользователь пишет конкретное название анализа («мне нужен анализ гамма-съемки», «анализ кремния в почве») — найди наиболее близкие услуги из прейскуранта и предложи их варианты (semantic search).

6. Команды навигации («корзина», «покажи корзину», «cart», «🛒», «в корзину») имеют самый высокий приоритет — сразу показывай корзину.

Никогда не придумывай услуги, которых нет в прейскуранте 2026 года.
Если не уверен — используй action "unknown" и предложи варианты кнопками.

Текущая дата: 02 апреля 2026 года.
Ты работаешь только с услугами из прейскуранта.

Твоя задача — распознать намерение пользователя и вернуть JSON.

Приоритет по воде:
- Если в сообщении есть слова: "сточная вода", "сточные воды", "анализы воды", "вода",
  то в первую очередь подбирай услуги из категории воды (природная/сточная/питьевая),
  а НЕ из воздуха или вентиляции.

Короткие примеры:
- Пользователь: "нужен анализ сточной воды" -> action="catalog" ИЛИ action="add_to_cart" c water service_id.
- Пользователь: "анализы воды на нефтепродукты" -> action="add_to_cart", services из водной категории.
- Пользователь: "питьевая вода, что проверить?" -> action="explain", explanation_query про воду.
- Пользователь: "Привет" -> {{"action":"greet","services":[],"explanation_query":null,"confidence":0.95}}
- Пользователь: "Какие есть анализы воды?" -> {{"action":"show_category","services":[],"explanation_query":"Вода природная, сточная, питьевая","confidence":0.92}}
- Пользователь: "Расскажи про лабораторию" -> {{"action":"faq","services":[],"explanation_query":"о лаборатории","confidence":0.95}}
- Пользователь: "Сформировать КП" -> {{"action":"start_kp_form","services":[],"explanation_query":null,"confidence":0.95}}
- Пользователь: "сделай кп / оформить" -> {{"action":"start_kp_form","services":[],"explanation_query":null,"confidence":0.95}}

Возможные действия (action):
- "greet" — приветствие/легкий small talk.
- "show_category" — показать подходящую категорию услуг (например, по воде).
- "start_kp_form" — пользователь хочет сразу начать оформление КП.
- "add_to_cart" — пользователь хочет добавить услуги. Заполни services[].
- "remove_from_cart" — пользователь хочет удалить услугу из корзины.
- "view_cart" — посмотреть корзину.
- "create_kp" — сформировать коммерческое предложение.
- "explain" — пользователь задаёт вопрос об услуге. Заполни explanation_query.
- "faq" — общий вопрос о лаборатории, сроках, контактах и т.д.
- "catalog" — пользователь хочет посмотреть каталог услуг.
- "clear_cart" — очистить корзину.
- "repeat_order" — повторить последний заказ.
- "unknown" — не удалось распознать.

Отвечай СТРОГО в формате JSON:
{{"action": "...", "services": [{{"service_id": "...", "quantity": N}}], "explanation_query": "...", "confidence": 0.0-1.0}}

Прейскурант:
{services_text}

Текущая корзина пользователя:
{cart_text if cart_text else "Пуста"}
"""


class GigaChatClient:
    def __init__(self) -> None:
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def _ensure_token(self) -> str:
        """Obtain or refresh OAuth2 token."""
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        settings = get_settings()
        if not settings.gigachat_client_id or not settings.gigachat_client_secret:
            raise RuntimeError("GigaChat credentials not configured")

        auth = aiohttp.BasicAuth(
            settings.gigachat_client_id, settings.gigachat_client_secret
        )
        data = {"scope": settings.gigachat_scope}
        headers = {"RqUID": str(uuid.uuid4())}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                _OAUTH_URL, auth=auth, data=data, headers=headers, ssl=False
            ) as resp:
                resp.raise_for_status()
                body = await resp.json()

        self._token = body["access_token"]
        self._token_expires_at = body.get("expires_at", time.time() + 1700) / 1000
        logger.info("GigaChat token refreshed, expires in %.0fs",
                     self._token_expires_at - time.time())
        return self._token  # type: ignore[return-value]

    async def recognise_intent(
        self,
        user_text: str,
        cart_text: str = "",
    ) -> IntentResult:
        """Send user text to GigaChat and parse structured intent."""

        if _JAILBREAK_PATTERNS.search(user_text):
            logger.warning("Jailbreak attempt detected: %s", user_text[:100])
            return IntentResult(action="unknown", confidence=0.0)

        loader = PriceLoader.get()
        system_prompt = _build_system_prompt(
            loader.services_summary_for_llm(), cart_text
        )

        try:
            token = await self._ensure_token()
        except Exception:
            logger.exception("Failed to obtain GigaChat token")
            return IntentResult(action="unknown", confidence=0.0)

        payload = {
            "model": "GigaChat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.1,
            "max_tokens": 512,
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    _CHAT_URL, json=payload, headers=headers, ssl=False, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    resp.raise_for_status()
                    body = await resp.json()

            content = body["choices"][0]["message"]["content"]
            return self._parse_response(content)
        except Exception:
            logger.exception("GigaChat API call failed")
            return IntentResult(action="unknown", confidence=0.0)

    def _parse_response(self, content: str) -> IntentResult:
        """Extract JSON from LLM response, validating service IDs."""
        # Strip markdown code fences if present
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            data = json.loads(cleaned)
            result = IntentResult(**data)
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("Failed to parse LLM JSON: %s — %s", exc, content[:200])
            return IntentResult(action="unknown", confidence=0.0)

        # Validate service IDs against the price list
        if result.services:
            loader = PriceLoader.get()
            valid_services = []
            for svc in result.services:
                if loader.get_service(svc.service_id):
                    valid_services.append(svc)
                else:
                    logger.warning("LLM hallucinated service_id: %s", svc.service_id)
            result.services = valid_services
            if not valid_services and result.action == "add_to_cart":
                result.confidence = min(result.confidence, 0.3)

        return result


_client: GigaChatClient | None = None


def get_gigachat_client() -> GigaChatClient:
    global _client
    if _client is None:
        _client = GigaChatClient()
    return _client
