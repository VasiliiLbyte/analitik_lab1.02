# Telegram-бот «Аналитик.Лаб»

Production-ready Telegram-бот для экологической лаборатории ООО «АНАЛИТИК.ЛАБ» (Санкт-Петербург). Позволяет клиентам подобрать лабораторные услуги, сформировать корзину и получить готовое коммерческое предложение в формате Word — всё через удобный гибридный интерфейс (80% свободный текст + 20% кнопки).

---

## Возможности

- **Гибридный интерфейс** — пользователь может писать свободным текстом («нужен анализ воды на тяжёлые металлы») или использовать кнопки меню и каталога
- **LLM Intent Recognizer** (GigaChat Lite) — распознаёт намерения пользователя, подбирает услуги из прейскуранта, объясняет содержание анализов
- **Каталог услуг** — 209 услуг в 8 категориях строго по прейскуранту 2026 года с поиском и пагинацией
- **Корзина** — полноценная корзина с дедупликацией, автоматическим расчётом 3% за оформление протоколов и НДС 5%
- **FSM-форма КП** — 7 шагов в формате INN-first с валидацией, автоподтяжкой реквизитов по DaData и предпросмотром
- **Генерация Word-файла** — документ в формате КП с таблицей услуг, итогами, суммой прописью (num2words)
- **Кнопка «Повторить заказ»** — копирует позиции из последнего выполненного заказа
- **Защита от 42 сценариев** — антиспам, валидация, fallback при ошибках LLM, graceful shutdown

---

## Стек технологий

| Компонент | Технология |
|-----------|------------|
| Telegram API | aiogram 3.20+ |
| FSM Storage | Redis (RedisStorage) с fallback на MemoryStorage |
| База данных | SQLAlchemy 2.0 async + Alembic (SQLite для dev, PostgreSQL для prod) |
| LLM | GigaChat Lite через Sber OAuth2 |
| Генерация документов | python-docx + num2words |
| Валидация | Pydantic v2 (settings, LLM output) |
| Rate limiting | Кастомный anti_flood middleware |
| Тестирование | pytest + pytest-asyncio (52 теста) |

---

## Структура проекта

```
bot/
├── main.py                          # Entry point: Dispatcher, Redis, роутеры, middleware
├── config.py                        # Pydantic BaseSettings, переменные окружения
├── __init__.py
├── database/
│   ├── models.py                    # SQLAlchemy ORM: User, CartItem, Order, OrderItem
│   ├── session.py                   # Async engine, sessionmaker, get_session
│   └── alembic/
│       ├── env.py                   # Async Alembic конфигурация
│       ├── script.py.mako           # Шаблон миграций
│       └── versions/                # Файлы миграций
├── states/
│   ├── user.py                      # MainMenu FSM
│   └── kp_form.py                   # KPForm FSM (7 шагов, INN-first + предпросмотр)
├── keyboards/
│   ├── main.py                      # Главное меню, кнопки отмены/назад, подсказки
│   ├── categories.py                # Каталог: категории, услуги (пагинация), детали
│   └── cart.py                      # Корзина: удаление, очистка, действия
├── handlers/
│   ├── start.py                     # /start (сброс FSM), /help
│   ├── faq.py                       # FAQ, «О лаборатории», callback back_menu
│   ├── services.py                  # Каталог по категориям, добавление в корзину
│   ├── cart.py                      # Просмотр/удаление/очистка корзины, повтор заказа
│   ├── kp_form.py                   # FSM-форма КП: 7 шагов (INN-first) + preview + генерация
│   └── free_text.py                 # Catch-all: LLM Intent Recognizer, маршрутизация
├── services/
│   ├── price_loader.py              # Загрузка/кэш JSON-прейскуранта, fuzzy-поиск
│   ├── llm_intent.py                # GigaChat OAuth2, Pydantic IntentResult, jailbreak-защита
│   ├── cart_service.py              # CRUD корзины, дедупликация, авто-3%, повтор заказа
│   └── kp_generator.py              # Генерация docx напрямую через python-docx
├── data/
│   └── preyskurant_2026.json        # 209 услуг в 8 категориях
├── utils/
│   └── validators.py                # ИНН (контрольная сумма), КПП, телефон, email
├── middleware/
│   └── anti_flood.py                # Rate limit 1.5 сек, детектор повторов, блокировка
└── requirements.txt                 # Зависимости
tests/
├── conftest.py                      # Фикстуры (in-memory DB)
├── test_validators.py               # 24 теста на валидацию ИНН/КПП/телефон/email/junk
├── test_cart_service.py             # 7 тестов на корзину (add/dedup/remove/clear/summary)
├── test_llm_intent.py               # 12 тестов на парсинг, jailbreak, валидацию service_id
└── test_kp_generator.py             # 9 тестов на расчёты, сумму прописью, генерацию .docx
```

---

## Быстрый старт

### Требования

- Python 3.11+
- Redis (опционально — бот работает с MemoryStorage при недоступности Redis)

### 1. Клонирование и установка зависимостей

```bash
git clone https://github.com/VasiliiLbyte/analitik_lab1.02.git
cd analitik_lab1.02

python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/macOS

pip install -r bot/requirements.txt
```

### 2. Настройка `.env`

Скопируйте пример и заполните значения:

```bash
copy .env.example .env        # Windows
# cp .env.example .env        # Linux/macOS
```

Минимальная конфигурация (обязателен только `BOT_TOKEN`):

```env
BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11

# GigaChat (для LLM Intent Recognizer — без него бот работает через каталог)
GIGACHAT_CLIENT_ID=your_client_id
GIGACHAT_CLIENT_SECRET=your_client_secret
```

### 3. Запуск бота

```bash
python -m bot.main
```

Бот автоматически:
1. Создаст таблицы в базе данных (SQLite по умолчанию)
2. Загрузит прейскурант из JSON
3. Начнёт polling

### 3.1 Чистый перезапуск без конфликтов polling

Если бот уже запускался, используйте единый сценарий:

```bash
pkill -f "python -m bot.main" || true
sleep 1
ps aux | grep "python -m bot.main"
python -m bot.main
```

Проверка успешного запуска:
- в логах есть `Starting polling...` и `Bot started: ...`;
- в логах отсутствует `TelegramConflictError`.

### 4. Запуск тестов

```bash
python -m pytest tests/ -v
```

---

## Миграции базы данных (Alembic)

```bash
# Создать миграцию
alembic revision --autogenerate -m "описание изменений"

# Применить миграции
alembic upgrade head
```

Для PostgreSQL (production) замените `DATABASE_URL`:

```env
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/analitik_lab
```

---

## Каталог услуг

209 услуг разделены на 8 категорий:

| Категория | Кол-во услуг | Примеры |
|-----------|:------------:|---------|
| Обследование и расчёты | 6 | Обследование вентиляционных систем, расчёт выбросов |
| Воздух промышленных выбросов | 8 | Углеводороды С1-С10, окислы азота, бенз(а)пирен |
| Атмосферный воздух / СЗЗ | 13 | Формальдегид, метеоданные, справки УГМС |
| Вода | 67 | Металлы, БПК5, ХПК, нефтепродукты, радиология |
| Почвы, грунты, отходы | 63 | Тяжёлые металлы, пестициды, нефтепродукты, ПАУ |
| Выезд и пробоотбор | 20 | Выезд по СПб, отбор проб воды/грунта, пробоподготовка |
| Оформление документов | 11 | Протоколы (3%), загрузка в ФСА, выписки |
| Физические факторы | 21 | Шум, вибрация, микроклимат, ЭМИ, освещённость |

---

## Сценарий работы пользователя

```
Пользователь: /start
Бот: Приветствие + главное меню (6 кнопок)

Пользователь: "Нужен анализ сточной воды на нефтепродукты и тяжёлые металлы"
Бот: [LLM] -> Добавлено в корзину: Нефтепродукты, Fe, Cu, Zn, Pb, Ni...

Пользователь: 🛒 Корзина
Бот: Список позиций + подитог + 3% протоколы + НДС 5% + итого

Пользователь: 📄 Сформировать КП
Бот: Шаг 1/7: Введите ИНН (проверка контрольной суммы)
     Если DaData доступен: предлагает подтянуть реквизиты (название, КПП, адрес)
     Шаг 2/7: Название организации
     Шаг 3/7: КПП
     Шаг 4/7: Юридический адрес
     Шаг 5/7: ФИО контактного лица
     Шаг 6/7: Телефон или email
     Шаг 7/7: Фактическое местоположение отбора проб
     Предпросмотр: все данные + итоги
     [Подтвердить] -> Генерация Word -> Отправка файла -> Корзина очищена
```

---

## Форма КП: 7 шагов (INN-first)

| Шаг | Поле | Валидация |
|:---:|-------|-----------|
| 1 | ИНН | 10 или 12 цифр + контрольная сумма |
| 2 | Название организации | 3-300 символов |
| 3 | КПП | Ровно 9 цифр |
| 4 | Юридический адрес | 10-500 символов |
| 5 | ФИО контактного лица | 2-200 символов |
| 6 | Телефон или email | Формат +7 XXX XXX-XX-XX или email |
| 7 | Фактическое местоположение отбора проб | 10-500 символов |

После шага ИНН бот может предложить автозаполнение реквизитов по DaData:
- **Подтвердить и заполнить** — применяет найденные `org_name`, `kpp`, `address`;
- **Ввести вручную** — продолжает пошаговый ручной ввод.

На каждом шаге доступны кнопки **Назад** и **Отмена**. Команды `отмена`, `назад`, `/start` обрабатываются на любом этапе.

---

## Генерация Word-файла

Коммерческое предложение генерируется в формате .docx (python-docx) и включает:

- Заголовок: «Коммерческое предложение № НФ-XXX от DD MMMM YYYY г.»
- Реквизиты исполнителя (ООО «АНАЛИТИК.ЛАБ»)
- Реквизиты заказчика (из формы)
- Таблицу услуг с группировкой по категориям и подитогами
- Строку «Оформление протоколов (3% от сметной стоимости)»
- Итого / НДС 5% / Всего с НДС
- Сумму прописью на русском языке

---

## LLM Intent Recognizer

Бот использует GigaChat Lite для распознавания намерений:

**Распознаваемые действия:**
- `add_to_cart` — добавить услуги в корзину
- `remove_from_cart` — удалить из корзины
- `view_cart` — показать корзину
- `create_kp` — сформировать КП
- `explain` — объяснить услугу
- `faq` — общий вопрос о лаборатории
- `catalog` — открыть каталог
- `clear_cart` — очистить корзину
- `repeat_order` — повторить последний заказ

**Защиты:**
- Service-ID валидация — LLM не может добавить несуществующую услугу
- Low confidence (< 0.75) — показываются кнопки-подсказки
- Jailbreak-детектор — regex на паттерны инъекций (ru/en)
- Non-JSON fallback — при невалидном ответе LLM показывается каталог

---

## Защита от 42 сценариев

### Перезапуски и потеря состояния (4 сценария)

| # | Сценарий | Реализация |
|---|----------|-----------|
| 1 | /start в любой момент (включая середину формы) | `start.py`: `state.clear()` при каждом /start |
| 2 | Перезапуск бота / потеря Redis | `main.py`: fallback `MemoryStorage` при недоступности Redis |
| 3 | Несколько устройств одновременно | FSM привязан к `user_id` в Redis/Memory — одно состояние |
| 4 | Истечение сессии FSM (>24 ч) | `state_ttl=86400` в RedisStorage; при таймауте — чистый старт |

### Спам и мусорный ввод (5 сценариев)

| # | Сценарий | Реализация |
|---|----------|-----------|
| 5 | 50+ одинаковых сообщений подряд | `anti_flood.py`: блокировка после 10 повторов на 5 мин |
| 6 | Только цифры / эмодзи / спецсимволы | `validators.py`: `is_junk_text()` → fallback-сообщение |
| 7 | Длинные сообщения (>2000 символов) | `validators.py`: `sanitise_for_llm()` обрезает до 2000 |
| 8 | Сообщения на английском + опечатки | LLM понимает оба языка; `PriceLoader.search()` — fuzzy-match |
| 9 | Повтор одного сообщения >3 раз | `anti_flood.py`: предупреждение на 5-м, бан на 10-м |

### Корзина и повторные заказы (4 сценария)

| # | Сценарий | Реализация |
|---|----------|-----------|
| 10 | Добавление одной услуги дважды (кнопка + текст) | `cart_service.py`: `add_item()` увеличивает quantity |
| 11 | Пустая корзина + попытка сформировать КП | `cart.py`: проверка `if not summary.items` → «Корзина пуста» |
| 12 | Второй заказ без очистки первого | Корзина автоочищается после успешной генерации КП |
| 13 | Удаление всех позиций | `cart.py`: переключение на `empty_cart_keyboard()` |

### Форма КП (7 сценариев)

| # | Сценарий | Реализация |
|---|----------|-----------|
| 14 | Невалидный ИНН (неправильная контрольная сумма) | `validators.py`: `validate_inn()` с checksum |
| 15 | Невалидный КПП | `validators.py`: `validate_kpp()` — строго 9 цифр |
| 16 | Невалидный телефон / email | `validators.py`: `validate_contact_info()` — regex |
| 17 | Слишком короткое/длинное название | `validate_org_name()`: 3-300 символов |
| 18 | Выход из формы и повторный запуск | `_handle_cancel()` / `/start` — `state.clear()` |
| 19 | Ошибка генерации Word | `kp_form.py`: try/except + логирование + сообщение пользователю |
| 20 | «Отмена» / «назад» на любом шаге | `_handle_cancel()`, `_go_back()`, inline-кнопки `kp_back`, `kp_cancel` |

### LLM Intent (7 сценариев)

| # | Сценарий | Реализация |
|---|----------|-----------|
| 21 | Low confidence (< 0.75) | `free_text.py`: кнопки-подсказки `low_confidence_keyboard()` |
| 22 | LLM придумывает несуществующую услугу | `llm_intent.py`: `_parse_response()` фильтрует по `PriceLoader` |
| 23 | Jailbreak-попытки (ru/en) | `llm_intent.py`: `_JAILBREAK_PATTERNS` regex → `action=unknown` |
| 24 | LLM отвечает не JSON | `_parse_response()`: `json.JSONDecodeError` → `action=unknown` |
| 25 | GigaChat API недоступен | `recognise_intent()`: try/except → `action=unknown` |
| 26 | OAuth-токен истёк | `_ensure_token()`: автообновление за 60 сек до expiry |
| 27 | LLM возвращает JSON в markdown code-fence | `_parse_response()`: strip ````json...```` |

### Общие защиты (15 сценариев)

| # | Сценарий | Реализация |
|---|----------|-----------|
| 28 | Anti-flood (1 сообщение / 1.5 сек) | `AntiFloodMiddleware` в `main.py` |
| 29 | Валидация на каждом шаге FSM | `_VALIDATORS` dict в `kp_form.py` |
| 30 | Автоочистка корзины после КП | `kp_form.py`: `clear_cart(session, user_id)` после отправки файла |
| 31 | Кнопка «Повторить последний заказ» | `cart.py`: `handle_repeat_order()` + `repeat_last_order()` |
| 32 | Логирование всех ошибок | `logger.exception()` в каждом handler'е и сервисе |
| 33 | Пустой текст сообщения | `free_text.py`: `if not text: return` |
| 34 | Отсутствие прейскуранта JSON | `PriceLoader.load()`: FileNotFoundError с traceback |
| 35 | Отсутствие GigaChat credentials | `_ensure_token()`: RuntimeError с описанием |
| 36 | Несколько callback за 1 нажатие | `callback.answer()` вызывается в каждом callback-хэндлере |
| 37 | Ввод текста во время preview | `handle_preview_text()` → «используйте кнопки» |
| 38 | Корзина пуста при старте формы из free_text | `handle_create_kp_button()` проверяет `summary.items` |
| 39 | Удаление несуществующего элемента | `remove_item()` возвращает `False`, показывает alert |
| 40 | Database not initialized | `get_session()`: `RuntimeError("call init_db first")` |
| 41 | Кнопка каталога при unknown intent | `free_text.py`: `categories_keyboard()` fallback |
| 42 | Graceful shutdown | `on_shutdown()`: `close_db()` + `bot.session.close()` |

---

## Тестирование

52 теста покрывают ключевые модули:

```bash
python -m pytest tests/ -v
```

| Модуль | Тестов | Что проверяется |
|--------|:------:|----------------|
| `test_validators.py` | 24 | ИНН checksum, КПП, телефон, email, junk-детектор |
| `test_cart_service.py` | 7 | Add/dedup/remove/clear, 3% fee, НДС, пустая корзина |
| `test_llm_intent.py` | 12 | Jailbreak regex, JSON parsing, hallucination filter |
| `test_kp_generator.py` | 9 | Дата, суммы, склонение рублей/копеек, docx-генерация |

---

## Переменные окружения

| Переменная | По умолчанию | Описание |
|------------|:------------:|----------|
| `BOT_TOKEN` | — | Токен Telegram-бота (обязательно) |
| `REDIS_URL` | `redis://localhost:6379/0` | URL Redis-сервера |
| `DATABASE_URL` | `sqlite+aiosqlite:///bot.db` | URL базы данных |
| `GIGACHAT_CLIENT_ID` | `""` | Client ID для GigaChat API |
| `GIGACHAT_CLIENT_SECRET` | `""` | Client Secret для GigaChat API |
| `GIGACHAT_SCOPE` | `GIGACHAT_API_PERS` | Scope OAuth2 |
| `LOG_LEVEL` | `INFO` | Уровень логирования |
| `NDS_RATE` | `5.0` | Ставка НДС (%) |
| `PROTOCOL_FEE_RATE` | `3.0` | Ставка оформления протоколов (%) |
| `ANTI_FLOOD_RATE` | `1.5` | Минимальный интервал между сообщениями (сек) |
| `FSM_TTL` | `86400` | TTL для FSM-ключей в Redis (сек) |
| `LLM_CONFIDENCE_THRESHOLD` | `0.75` | Порог уверенности LLM |

---

## Реквизиты исполнителя

```
ООО "АНАЛИТИК.ЛАБ"
ИНН 7806341520, КПП 781601001
192102, Город Санкт-Петербург, вн.тер. г. Муниципальный Округ Волковское,
ул Дубровская, дом 13, литера А, помещение 2-Н, комната 27
```

---

## Лицензия

Проект разработан для ООО «АНАЛИТИК.ЛАБ». Все права защищены.
