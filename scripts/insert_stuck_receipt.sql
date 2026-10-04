INSERT INTO webhook_receipts (
    id, provider, idempotency_key, event_type, payload, verified, status,
    celery_retry_count, sweep_attempts, max_sweep_attempts,
    created_at, updated_at
) VALUES (
    gen_random_uuid(),
    'generic',
    'stuck-test-001',
    'stuck.test',
    '{"user_id": "123", "amount": 100}'::jsonb,
    false,
    'pending',
    0,
    0,
    3,
    NOW() - INTERVAL '2 hours',
    NOW() - INTERVAL '2 hours'
);