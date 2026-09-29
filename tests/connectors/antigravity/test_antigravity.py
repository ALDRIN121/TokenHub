"""Antigravity generation counters are read without persisting conversation text."""

import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient
from tokenhub.app import create_app
from tokenhub.connectors.antigravity.connector import AntigravityConnector
from tokenhub.connectors.protocol import DiscoveryContext
from tokenhub.domain.models import SourceState
from tokenhub.settings import TokenHubSettings

from tests.api.conftest import ORIGIN


def _varint(value: int) -> bytes:
    parts = []
    while value > 127:
        parts.append((value & 127) | 128)
        value >>= 7
    return bytes([*parts, value])


def _number(field: int, value: int) -> bytes:
    return _varint(field << 3) + _varint(value)


def _bytes(field: int, value: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(value)) + value


def _generation(model: str, response: str, *, output_total: int = 35) -> bytes:
    usage = b''.join((
        _number(1, 10), _number(2, 100), _number(3, output_total),
        _number(5, 40), _number(9, 25), _number(10, 10),
        _bytes(11, response.encode()),
    ))
    return _bytes(1, _bytes(4, usage) + _bytes(19, model.encode()))


def _step(response: str, epoch: int) -> bytes:
    return _bytes(1, _number(1, epoch)) + _bytes(9, _bytes(11, response.encode()))


def _database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE gen_metadata (idx INTEGER PRIMARY KEY, data BLOB, size INTEGER)')
        db.execute('CREATE TABLE steps (idx INTEGER PRIMARY KEY, step_type INTEGER, metadata BLOB, secret TEXT)')
        db.execute('INSERT INTO gen_metadata VALUES (0, ?, 0)', (_generation('gemini-test', 'response-a'),))
        db.execute('INSERT INTO gen_metadata VALUES (1, ?, 0)', (_generation('gemini-test', 'response-b', output_total=999),))
        db.execute('INSERT INTO steps VALUES (0, 15, ?, ?)', (_step('response-a', 1790668800), 'private prompt'))
        db.execute('INSERT INTO steps VALUES (1, 15, ?, ?)', (_step('response-b', 1790668801), 'private prompt'))


def test_discovers_and_imports_verified_generation_usage(tmp_path: Path) -> None:
    home = tmp_path / 'home'
    path = home / '.gemini/antigravity/conversations/conversation.db'
    _database(path)
    connector = AntigravityConnector()
    context = DiscoveryContext(home, {}, lambda _: None)
    detected = connector.detect(context)
    assert detected.display_name == 'Antigravity'
    assert len(detected.sources) == 1
    source = connector.discover_sources(context)[0]
    assert source.scan_supported is True
    assert connector.scan(source, None).state == SourceState.DISCOVERED
    approved = type(source)(
        source.source_id, source.connector_id, source.provider, source.display_name,
        source.canonical_path, source.approved_root, source.source_type,
        source.path_fingerprint, SourceState.APPROVED, source.evidence_codes,
        source.scan_supported, source.parser_version,
        source.approved_root_device, source.approved_root_inode,
    )
    result = connector.scan(approved, None)
    assert result.state == SourceState.PARTIAL
    assert result.unsupported_records == 1
    assert len(result.events) == 1
    event = result.events[0]
    assert event.input_total_tokens == 150
    assert event.output_total_tokens == 35
    assert event.cache_read_tokens == 40
    assert event.reasoning_tokens == 10
    assert event.model_name == 'gemini-test'
    assert event.timestamp.isoformat() == '2026-09-29T08:00:00+00:00'
    assert 'private prompt' not in str(result)
    assert connector.scan(approved, result.cursor).events == ()


def _scan_usage(tmp_path: Path, usage: bytes, case: str = 'case'):
    """Scan one generation whose usage message is exactly ``usage``."""
    home = tmp_path / case
    path = home / '.gemini/antigravity/conversations/conversation.db'
    path.parent.mkdir(parents=True)
    # Close explicitly: on Windows an open handle blocks later cleanup of the file.
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE gen_metadata (idx INTEGER PRIMARY KEY, data BLOB, size INTEGER)')
        db.execute('CREATE TABLE steps (idx INTEGER PRIMARY KEY, step_type INTEGER, metadata BLOB)')
        blob = _bytes(1, _bytes(4, usage + _bytes(11, b'response-a')) + _bytes(19, b'gemini-test'))
        db.execute('INSERT INTO gen_metadata VALUES (0, ?, 0)', (blob,))
        db.execute('INSERT INTO steps VALUES (0, 15, ?)', (_step('response-a', 1790668800),))
    connector = AntigravityConnector()
    source = connector.discover_sources(DiscoveryContext(home, {}, lambda _: None))[0]
    approved = type(source)(
        source.source_id, source.connector_id, source.provider, source.display_name,
        source.canonical_path, source.approved_root, source.source_type,
        source.path_fingerprint, SourceState.APPROVED, source.evidence_codes,
        source.scan_supported, source.parser_version,
        source.approved_root_device, source.approved_root_inode,
    )
    return connector.scan(approved, None)


def test_a_first_message_without_cached_or_thinking_tokens_is_imported(tmp_path: Path) -> None:
    # Protobuf omits zero-valued fields: no cache-read (5) and no thinking (10).
    usage = _number(1, 10) + _number(2, 100) + _number(3, 25) + _number(9, 25)
    result = _scan_usage(tmp_path, usage)
    assert result.state == SourceState.HEALTHY
    assert result.unsupported_records == 0
    event = result.events[0]
    assert (event.input_total_tokens, event.output_total_tokens) == (110, 25)
    assert (event.cache_read_tokens, event.reasoning_tokens) == (0, 0)


def test_omitted_counters_do_not_relax_output_reconciliation(tmp_path: Path) -> None:
    # Output total 30 but only 25 of text and no thinking: still unverifiable.
    usage = _number(1, 10) + _number(2, 100) + _number(3, 30) + _number(9, 25)
    result = _scan_usage(tmp_path, usage)
    assert result.events == ()
    assert result.unsupported_records == 1


def test_a_usage_message_without_input_or_output_counters_stays_unsupported(tmp_path: Path) -> None:
    assert _scan_usage(tmp_path, _number(3, 25) + _number(9, 25), 'no-input').events == ()
    assert _scan_usage(tmp_path, _number(1, 10) + _number(2, 100), 'no-output').events == ()


def test_a_counter_of_the_wrong_type_stays_unsupported(tmp_path: Path) -> None:
    usage = _number(1, 10) + _number(2, 100) + _bytes(3, b'25') + _number(9, 25)
    result = _scan_usage(tmp_path, usage)
    assert result.events == ()
    assert result.unsupported_records == 1


def test_desktop_app_pb_conversations_are_reported_as_an_unreadable_format(tmp_path: Path) -> None:
    home = tmp_path / 'home'
    conversations = home / '.gemini/antigravity/conversations'
    conversations.mkdir(parents=True)
    (conversations / 'one.pb').write_bytes(b'private conversation bytes')
    result = AntigravityConnector().detect(DiscoveryContext(home, {}, lambda _: None))
    assert result.sources == ()
    assert result.state is SourceState.DISCOVERED
    assert 'unsupported_session_format' in result.evidence_codes
    # An unrecognized signal never raises how sure discovery is about the install.
    assert result.confidence.value == 'medium'
    assert 'private' not in str(result)


def test_readable_databases_do_not_report_an_unreadable_format(tmp_path: Path) -> None:
    home = tmp_path / 'home'
    conversations = home / '.gemini/antigravity/conversations'
    _database(conversations / 'one.db')
    (conversations / 'two.pb').write_bytes(b'x')
    result = AntigravityConnector().detect(DiscoveryContext(home, {}, lambda _: None))
    assert result.sources
    assert 'unsupported_session_format' not in result.evidence_codes


def test_does_not_follow_a_symlinked_conversation_database(tmp_path: Path) -> None:
    home = tmp_path / 'home'
    outside = tmp_path / 'outside.db'
    _database(outside)
    conversations = home / '.gemini/antigravity/conversations'
    conversations.mkdir(parents=True)
    (conversations / 'linked.db').symlink_to(outside)
    assert AntigravityConnector().discover_sources(DiscoveryContext(home, {}, lambda _: None)) == []


def test_one_approval_imports_antigravity_usage_to_models_and_dates(tmp_path: Path) -> None:
    home = tmp_path / 'home'
    _database(home / '.gemini/antigravity/conversations/conversation.db')
    app = create_app(TokenHubSettings(home_directory=home, data_directory=tmp_path / 'data'))
    app.state.container.discovery_context = DiscoveryContext(home, {}, lambda _: None)
    with TestClient(app, base_url='http://127.0.0.1:7432') as client:
        discovery = client.get('/api/v1/discovery').json()
        provider = next(item for item in discovery['providers'] if item['provider'] == 'antigravity')
        assert len(provider['sources']) == 1
        assert provider['sources'][0]['state'] == 'discovered'
        approved = client.post('/api/v1/collection/antigravity/enable', headers=ORIGIN)
        assert approved.status_code == 200
        assert 'antigravity-local' in approved.json()['auto_import_connectors']
        usage = client.get('/api/v1/usage').json()
        model = next(item for item in usage['models'] if item['provider'] == 'antigravity')
        assert model['model_name'] == 'gemini-test'
        assert model['workload_tokens'] == 185
        assert usage['totals']['event_count'] == 1
        date = client.get('/api/v1/usage', params={
            'from': '2026-09-29T00:00:00Z', 'to': '2026-09-30T00:00:00Z',
        }).json()
        assert date['totals']['workload_tokens'] == 185
        assert 'private prompt' not in str(usage)
