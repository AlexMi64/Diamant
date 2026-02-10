# Архитектура аутрича на 1200 получателей (multi-client, low-cost, resilient)

## 1) Описание архитектуры

Для нескольких клиентов и направлений предлагается managed-first схема на AWS:  
`EventBridge Scheduler -> Lambda Planner -> SQS -> Lambda Sender -> Amazon SES`, с обратным контуром событий `SES -> SNS -> Lambda Feedback`.

Решение ориентировано на до 10k писем/мес и бюджет до $100/мес, без постоянных серверов.  
Высокая отказоустойчивость достигается за счет очередей, DLQ, идемпотентности и подготовленного failover-региона SES.

## 2) Сервисы и подходы

- **Amazon SES (primary + secondary region)**: отправка и региональный fallback.
- **EventBridge Scheduler**: запуск волн и follow-up по расписанию.
- **AWS Lambda**: planner, sender workers, feedback processor.
- **SQS + DLQ**: буферизация, retry, изоляция сбоев.
- **DynamoDB**: multi-tenant состояние, sender pool, квоты, статусы jobs.
- **S3 (90 дней)**: архив событий и выгрузок.
- **CloudWatch + SNS alerts**: метрики, алерты, уведомления в Telegram/Email.

Подходы:
- serverless-first для минимальной стоимости;
- `tenant_id` во всех сущностях (изоляция клиентов);
- `idempotency_key` на отправку (защита от дублей);
- suppression list на уровне tenant + global;
- IAM least privilege, TLS, KMS encryption.

## 3) Ротация и мониторинг

**Ротация отправителей:** weighted round-robin по `SenderIdentity` с весом от репутации.  
**Throttling:** лимиты per-tenant, per-sender и per receiving-domain (gmail/outlook/yahoo).  
**Warm-up:** постепенное увеличение лимитов для новых доменов/identity.

**Auto-stop правила:**
- bounce rate > 5% (скользящее окно N отправок) -> пауза кампании;
- complaint rate > 0.1% -> немедленная блокировка sender identity;
- рост deferred/429 -> автоматическое снижение send rate.

**Мониторинг:**
- delivery/bounce/complaint rate;
- queue depth, age of oldest message, DLQ size;
- per-tenant success/fail;
- SES reject/throttle.

## 4) Распределение нагрузки

1. Planner дробит кампанию на `MessageJob` и кладет в SQS.
2. Scheduler распределяет отправку по слотам с jitter (без burst).
3. Worker Lambda читает батчами, применяет квоты/лимиты и отправляет через SES.
4. Временные ошибки -> retry с backoff; исчерпание попыток -> DLQ.
5. Fair-share квоты не дают одному клиенту “занять” весь канал.

## 5) Риски и способы закрытия

- **Проблемы deliverability:** SPF/DKIM/DMARC, warm-up, suppression, domain rotation.
- **Лимиты/деградация провайдера:** DLQ + retry + secondary SES region.
- **Кросс-tenant риски:** изоляция данных по `tenant_id`, scoped IAM, audit logs.
- **Рост затрат:** AWS Budgets, квоты по клиентам, контроль объема логов.
- **Накопление ошибочных задач:** DLQ triage playbook и auto-pause кампаний.

## 6) Важные интерфейсы/типы

Типы: `Tenant`, `Direction`, `SenderIdentity`, `Lead`, `CampaignStep`, `MessageJob`, `DeliveryEvent`, `SuppressionEntry`.

`MessageJob`:
- `job_id`, `tenant_id`, `lead_id`, `sender_id`, `template_id`, `scheduled_at`, `idempotency_key`, `status`.

`DeliveryEvent`:
- `event_id`, `message_id`, `event_type` (`sent|delivered|bounce|complaint|deferred`), `timestamp`, `provider_payload_ref`.

## 7) Примерная стоимость

Целевой диапазон: **$35–$95/мес** при нагрузке до 10k писем/мес.  
Детализация по сервисам и допущениям: `cost_estimate.csv`.
