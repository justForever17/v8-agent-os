"""Durable provider admission; only the actual transport acquires a permit.

Candidate planning and health UI read snapshots. They never reserve a probe.
Permits are scoped to one invocation and one circuit generation, not a model.
"""
from __future__ import annotations

import math
import time
import uuid
from typing import Any

from core.database import db
from core.llm_exceptions import V8LLMProviderUnavailableError


_NEUTRAL_ERRORS = {
    'content_policy_block', 'context_window_overflow', 'invalid_request',
    'capability_mismatch', 'model_capability_unavailable', 'provider_circuit_open',
    'structured_output_invalid', 'model_output_incomplete', 'response_contract_violation',
}


class ProviderCircuitOpen(V8LLMProviderUnavailableError):
    def __init__(self, provider_id: str, *, retry_after_seconds: float, reason: str = 'circuit_open'):
        super().__init__(code='provider_circuit_open', provider=provider_id, retryable=False,
            message='Provider circuit is temporarily unavailable for this invocation.',
            user_action='Wait for the circuit recovery window or explicitly test the configured provider.',
            details={'retryAfterSeconds': max(0.0, retry_after_seconds), 'reason': reason})


def _positive(value: Any, default: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value > 0 else default


class ProviderCircuitService:
    def __init__(self, database=None, *, clock=None):
        self._database = database
        self._clock = clock or time.time

    @property
    def database(self):
        return self._database if self._database is not None else db

    @staticmethod
    def _schema(conn):
        conn.execute('''CREATE TABLE IF NOT EXISTS provider_circuit_state (
            provider_id TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'closed',
            generation INTEGER NOT NULL DEFAULT 0, open_until REAL,
            probe_id TEXT, probe_expires_at REAL, updated_at REAL NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS provider_circuit_attempts (
            id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, generation INTEGER NOT NULL,
            is_probe INTEGER NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL,
            finished_at REAL, error_code TEXT)''')
        conn.execute('''CREATE INDEX IF NOT EXISTS idx_provider_circuit_attempt_window
            ON provider_circuit_attempts(provider_id, generation, finished_at)''')

    @staticmethod
    def _view(row, now: float) -> dict[str, Any]:
        if row is None:
            return {'circuitState': 'closed', 'circuitAllowsAttempt': True, 'circuitGeneration': 0,
                    'probeInFlight': False, 'retryAfterSeconds': 0.0, 'circuitReason': 'closed'}
        busy = row['state'] == 'half_open' and float(row['probe_expires_at'] or 0) > now
        delay = (float(row['probe_expires_at'] or 0) if busy else float(row['open_until'] or 0)) - now
        allowed = row['state'] == 'closed' or (not busy and delay <= 0)
        state = 'closed' if row['state'] == 'closed' else 'half_open' if allowed or busy else 'open'
        return {'circuitState': state, 'circuitAllowsAttempt': allowed,
                'circuitGeneration': row['generation'], 'probeInFlight': busy,
                'retryAfterSeconds': max(0.0, delay),
                'circuitReason': 'probe_in_flight' if busy else 'probe_available' if state == 'half_open' else state}

    def snapshot(self, provider_id: str) -> dict[str, Any]:
        with self.database.get_connection() as conn:
            self._schema(conn)
            row = conn.execute('SELECT * FROM provider_circuit_state WHERE provider_id=?', (provider_id,)).fetchone()
            return self._view(row, self._clock())

    def acquire(self, provider_id: str, *, config: dict, attempt_id: str | None = None,
                explicit_probe: bool = False) -> dict[str, Any]:
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError('provider_circuit_provider_required')
        permit_id = attempt_id or str(uuid.uuid4())
        governance = config.get('governance') or {}
        lease_seconds = _positive(governance.get('providerCircuitProbeLeaseSeconds'), 120)

        def write():
            with self.database.get_connection() as conn:
                self._schema(conn)
                conn.execute('BEGIN IMMEDIATE')
                now = self._clock()
                conn.execute("INSERT OR IGNORE INTO provider_circuit_state(provider_id, updated_at) VALUES (?,?)", (provider_id, now))
                state = conn.execute('SELECT * FROM provider_circuit_state WHERE provider_id=?', (provider_id,)).fetchone()
                prior = conn.execute('SELECT * FROM provider_circuit_attempts WHERE id=?', (permit_id,)).fetchone()
                if prior:
                    if prior['provider_id'] != provider_id:
                        raise ValueError('provider_circuit_attempt_identity_conflict')
                    current_probe = not prior['is_probe'] or (
                        state['probe_id'] == permit_id and float(state['probe_expires_at'] or 0) > now)
                    if prior['state'] != 'pending' or prior['generation'] != state['generation'] or not current_probe:
                        raise ProviderCircuitOpen(provider_id, retry_after_seconds=0, reason='attempt_not_current')
                    return {'permitId': permit_id, 'providerId': provider_id, 'generation': prior['generation'], 'probe': bool(prior['is_probe'])}
                view = self._view(state, now)
                if not view['circuitAllowsAttempt'] and (not explicit_probe or view['probeInFlight']):
                    raise ProviderCircuitOpen(provider_id, retry_after_seconds=view['retryAfterSeconds'], reason=view['circuitReason'])
                probe = state['state'] != 'closed'
                generation = int(state['generation']) + (1 if probe else 0)
                if probe:
                    if state['probe_id']:
                        conn.execute("UPDATE provider_circuit_attempts SET state='expired', finished_at=?, error_code='probe_lease_expired' WHERE id=? AND state='pending'",
                                     (now, state['probe_id']))
                    conn.execute("UPDATE provider_circuit_state SET state='half_open', generation=?, probe_id=?, probe_expires_at=?, updated_at=? WHERE provider_id=?",
                                 (generation, permit_id, now + lease_seconds, now, provider_id))
                conn.execute("INSERT INTO provider_circuit_attempts(id,provider_id,generation,is_probe,state,created_at) VALUES (?,?,?,?,'pending',?)",
                             (permit_id, provider_id, generation, int(probe), now))
                conn.commit()
                return {'permitId': permit_id, 'providerId': provider_id, 'generation': generation, 'probe': probe}
        return self.database._run_write_with_retry(write)

    def finish(self, permit: dict, *, success: bool, config: dict, error_code: str | None = None) -> bool:
        outcome = 'success' if success else 'neutral' if error_code in _NEUTRAL_ERRORS else 'failure'
        return self._settle(permit, outcome=outcome, config=config, error_code=error_code)

    def release(self, permit: dict, *, reason: str = 'cancelled') -> bool:
        """Release before dispatch or on cancellation; never invent success."""
        return self._settle(permit, outcome='released', config={}, error_code=reason)

    def _settle(self, permit: dict, *, outcome: str, config: dict, error_code: str | None) -> bool:
        governance = config.get('governance') or {}
        cooldown = _positive(governance.get('providerCircuitCooldownSeconds'), 60)
        minimum_samples = max(1, int(_positive(governance.get('providerFailureThreshold'), 3)))
        try:
            rate = float(governance.get('providerErrorRateThreshold', .6))
        except (TypeError, ValueError):
            rate = .6
        rate = min(1.0, max(0.0, rate)) if math.isfinite(rate) else .6
        window_seconds = _positive(governance.get('providerHealthWindowDays'), 7) * 86400

        def write():
            with self.database.get_connection() as conn:
                self._schema(conn)
                conn.execute('BEGIN IMMEDIATE')
                now = self._clock()
                call = conn.execute('SELECT * FROM provider_circuit_attempts WHERE id=?', (permit.get('permitId'),)).fetchone()
                if call is None or call['provider_id'] != permit.get('providerId') or call['generation'] != permit.get('generation'):
                    raise ValueError('provider_circuit_permit_mismatch')
                if call['state'] != 'pending':
                    return False
                state = conn.execute('SELECT * FROM provider_circuit_state WHERE provider_id=?', (call['provider_id'],)).fetchone()
                current = state['generation'] == call['generation'] and (
                    (not call['is_probe'] and state['state'] == 'closed') or
                    (call['is_probe'] and state['probe_id'] == call['id'] and float(state['probe_expires_at'] or 0) > now))
                conn.execute('UPDATE provider_circuit_attempts SET state=?, finished_at=?, error_code=? WHERE id=?',
                             (outcome if current else 'stale', now, error_code, call['id']))
                if not current:
                    conn.commit()
                    return False
                if call['is_probe']:
                    next_state = 'closed' if outcome == 'success' else 'open'
                    open_until = (None if next_state == 'closed' else now + cooldown if outcome == 'failure'
                                  else max(now, float(state['open_until'] or 0)))
                    conn.execute('UPDATE provider_circuit_state SET state=?, generation=generation+1, open_until=?, probe_id=NULL, probe_expires_at=NULL, updated_at=? WHERE provider_id=?',
                                 (next_state, open_until, now, call['provider_id']))
                elif outcome == 'failure':
                    samples = conn.execute("SELECT COUNT(*) AS events, SUM(CASE WHEN state='failure' THEN 1 ELSE 0 END) AS errors FROM provider_circuit_attempts "
                        "WHERE provider_id=? AND generation=? AND finished_at>=? AND state IN ('success','failure')",
                        (call['provider_id'], call['generation'], now-window_seconds)).fetchone()
                    if samples['events'] >= minimum_samples and samples['errors'] / samples['events'] >= rate:
                        conn.execute("UPDATE provider_circuit_state SET state='open', generation=generation+1, open_until=?, probe_id=NULL, probe_expires_at=NULL, updated_at=? WHERE provider_id=?",
                                     (now+cooldown, now, call['provider_id']))
                conn.commit()
                return True
        return self.database._run_write_with_retry(write)


provider_circuit_service = ProviderCircuitService()
