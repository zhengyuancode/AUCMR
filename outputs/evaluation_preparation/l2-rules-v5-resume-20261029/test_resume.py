import importlib.util
from pathlib import Path
import socket

import pytest

from eval.evaluation_runtime import RuntimeDeferred, RuntimeResponseError
from tests.test_evaluation_runtime import binding, response, FakeTransport as MockTransport, MESSAGES

spec = importlib.util.spec_from_file_location('l2_wait_resume', Path(__file__).with_name('resume.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('NETWORK_DENIED')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


def test_concurrency_wait_before_dispatch_and_cached_recovery(tmp_path, monkeypatch):
    logs = []
    monkeypatch.setattr(m, 'event', lambda kind, **kw: logs.append((kind, kw)))
    transport = MockTransport(response())
    r = m.DiagnosticRuntime(binding(), tmp_path/'run', 'L2RULES', tmp_path/'ledger.sqlite', transport=transport)
    budget = r.budget.inner
    budget.reserve('OTHER_LAYER', 'CONSTRUCTED', binding()['per_request_upper_bound'])
    def release(_):
        assert len(transport.calls) == 0
        budget.settle('OTHER_LAYER', 0.0001)
    r.budget = m.WaitingBudget(budget, lambda kind, **kw: logs.append((kind, kw)), sleep=release)
    first = r.request(MESSAGES, task_id='fixture')
    second = r.request(MESSAGES, task_id='fixture')
    assert first['request_id'] == second['request_id'] and len(transport.calls) == 1
    assert [x[0] for x in logs] == ['WAITING_NOT_SENT', 'RESERVATION_ACQUIRED']
    assert len(list((tmp_path/'run/runtime_requests').glob('*.json'))) == 1


@pytest.mark.parametrize('reason', [x for x in m.RESERVE_CODES if x != 'Concurrency limit'] + ['secret_must_not_be_logged'])
def test_other_reservation_failures_do_not_wait_or_leak(reason):
    class Blocked:
        calls = 0
        def reserve(self, *args):
            self.calls += 1
            raise ValueError(reason)
    inner = Blocked(); logs = []
    wait = m.WaitingBudget(inner, lambda k, **kw: logs.append((k, kw)), sleep=lambda _: pytest.fail('unexpected wait'))
    with pytest.raises(ValueError):
        wait.reserve('fixture', 'fixture', {})
    assert inner.calls == 1
    assert 'secret_must_not_be_logged' not in repr(logs)


def test_wait_timeout_no_dispatch_or_new_reservation(tmp_path, monkeypatch):
    logs = []; clock = [0]
    monkeypatch.setattr(m, 'event', lambda k, **kw: logs.append((k, kw)))
    transport = MockTransport(response())
    r = m.DiagnosticRuntime(binding(), tmp_path/'run', 'L2RULES', tmp_path/'ledger.sqlite', transport=transport)
    budget = r.budget.inner
    budget.reserve('OTHER_LAYER', 'CONSTRUCTED', binding()['per_request_upper_bound'])
    r.budget = m.WaitingBudget(budget, lambda k, **kw: logs.append((k, kw)),
                              clock=lambda: clock[0], sleep=lambda t: clock.__setitem__(0, clock[0]+t), max_wait=10)
    with pytest.raises(RuntimeDeferred):
        r.request(MESSAGES, task_id='fixture')
    assert not transport.calls
    assert not list((tmp_path/'run/runtime_requests').glob('*.json'))
    assert any(x[1].get('reason') == 'CONCURRENCY_WAIT_TIMEOUT' for x in logs)
    with budget.connect() as db:
        assert db.execute('select id,state from reservations').fetchall() == [('OTHER_LAYER', 'RESERVED')]


def test_sent_invalid_response_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(m, 'event', lambda *a, **kw: None)
    transport = MockTransport(response(body='broken json'))
    r = m.DiagnosticRuntime(binding(), tmp_path/'run', 'L2RULES', tmp_path/'ledger.sqlite', transport=transport)
    for _ in range(2):
        with pytest.raises(RuntimeResponseError):
            r.request(MESSAGES, task_id='fixture')
    assert len(transport.calls) == 1


def test_unauthorized_before_config_access():
    with pytest.raises(m.b.Stop, match='LIVE_NOT_AUTHORIZED'):
        m.run(False, 'unused')
