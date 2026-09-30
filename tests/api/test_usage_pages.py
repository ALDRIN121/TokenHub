from tests.api.conftest import ORIGIN


def test_bounded_usage_routes_validate_pages_and_share_totals(client):
    client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    summary = client.get('/api/v1/usage/summary')
    assert summary.status_code == 200
    assert summary.json()['totals']['workload_tokens'] == 125
    assert summary.json()['models'] == summary.json()['sessions'] == []
    models = client.get('/api/v1/usage/models?limit=1').json()
    assert len(models['items']) == models['total'] == 1
    sessions = client.get('/api/v1/usage/sessions').json()
    assert len(sessions['items']) == 1
    key = sessions['items'][0]['session_key']
    assert sessions['items'][0]['models'] == []
    detail = client.get(f'/api/v1/usage/sessions/{key}/models').json()
    assert detail['items'][0]['workload_tokens'] == 125
    assert client.get('/api/v1/usage/models?limit=101').status_code == 422
    assert client.get('/api/v1/usage/models?offset=-1').status_code == 422
    assert client.get('/api/v1/usage/models?sort=canonical_path').status_code == 422
    assert client.get('/api/v1/usage/summary?from=2026-01-01T00:00:00').status_code == 422


def test_lightweight_metadata_is_path_free_and_keeps_provider_counts(client):
    discovery = client.get('/api/v1/discovery?include_sources=false').json()
    codex = next(item for item in discovery['providers'] if item['provider'] == 'codex')
    assert codex['sources'] == []
    assert codex['source_count'] == codex['supported_source_count'] == 1
    quality = client.get('/api/v1/data-quality?lightweight=true&limit=1').json()
    assert len(quality['source_freshness']) == 1
    assert quality['source_count'] == 2
    assert quality['source_freshness'][0]['display_name']
    assert 'canonical_path' not in str(discovery) + str(quality)
