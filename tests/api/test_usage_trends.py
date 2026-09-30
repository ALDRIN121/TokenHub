from tests.api.conftest import ORIGIN


def test_trends_api_reconciles_and_preserves_path_free_cached_views(client):
    client.post('/api/v1/collection/codex/enable', headers=ORIGIN)
    result = client.get('/api/v1/usage/trends').json()
    assert result['current']['workload_tokens'] == client.get('/api/v1/dashboard').json()['workload_tokens'] == 125
    assert result['data_version'] == client.get('/api/v1/collection').json()['data_version']
    assert result['breakdown']['items'][0]['provider'] == 'codex'
    assert 'canonical_path' not in str(result)
    assert 'sk-synthetic-secret' not in str(result)


def test_trends_api_validates_calendar_bounds_timezones_and_limits(client):
    invalid = [
        {'from': '2026-09-27T00:00:00Z'},
        {'from': '2026-09-27T00:00:00', 'to': '2026-09-28T00:00:00Z'},
        {'from': '2026-09-27T12:00:00Z', 'to': '2026-09-28T00:00:00Z'},
        {'from': '2026-09-28T00:00:00Z', 'to': '2026-09-27T00:00:00Z'},
        {'time_zone': '../private/path'}, {'time_zone': 'Missing/Timezone'},
        {'granularity': 'hour'}, {'provider': 'unknown'}, {'dimension': 'sessions'},
        {'offset': -1}, {'limit': 101}, {'model': 'm', 'unknown_model': 'true'},
    ]
    for query in invalid:
        response = client.get('/api/v1/usage/trends', params=query)
        assert response.status_code == 422, query
    response = client.get('/api/v1/usage/trends', params={'time_zone': '../private/path'})
    assert '../private/path' not in response.text
