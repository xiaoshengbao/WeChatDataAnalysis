"""Data provenance and optional UI, using the production graph with scripted models."""
import asyncio

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from test_ai_deepagents import make_service, execute, action, last_result
from test_ai_deep_contracts import prepared
from wechat_decrypt_tool.ai.analysis_ui import UISpec, create_analysis_ui, find_artifact
from wechat_decrypt_tool.ai.agent_references import valid_answer_references
from wechat_decrypt_tool.ai.deep_tools import ChatGateway
from wechat_decrypt_tool.ai.deep_calculation import CalculationTerm


def chart_spec(kind='bar'):
    return {'root': 'layout', 'elements': {
        'layout': {'type': 'Stack', 'children': ['metric', 'chart', 'table']},
        'metric': {'type': 'MetricCard', 'props': {'dataset': 'totals', 'field': 'total_messages'}},
        'chart': {'type': 'Chart', 'props': {'dataset': 'daily_totals', 'chart_type': kind, 'x': 'day', 'y': 'count'}},
        'table': {'type': 'DataTable', 'props': {'dataset': 'sender_ranking', 'columns': ['sender_id', 'sender', 'count']}},
    }}


async def counted(service):
    gateway = await prepared(service)
    scope = await gateway.select(mode='statistics')
    tool = next(t for t in gateway.tools() if t.name == 'count_messages')
    await tool.ainvoke({'scope_handle': scope['scope_handle']})
    return gateway, scope['scope_handle']


def test_statistical_snapshot_survives_finish_and_reuse_without_query(tmp_path, monkeypatch):
    service, _ = make_service(tmp_path, monkeypatch)
    async def check():
        gateway, scope = await counted(service)
        result = create_analysis_ui(gateway, '消息概览', UISpec.model_validate(chart_spec()), scope)
        artifact = find_artifact(service, gateway.guard(), result['ui_id'])
        assert artifact['datasets']['totals']['rows'][0]['total_messages'] == 1
        assert artifact['datasets']['daily_totals']['rows'][0]['count'] == 1
        assert valid_answer_references(result['reference'], {}, {}, [artifact])
        assert not valid_answer_references('[[ui:' + 'f' * 24 + ']]', {}, {}, [artifact])
        service.update(gateway.id, answer='结果如下。\n\n' + result['reference'])
        service.finish(gateway.id, 'completed')
        historical = service.public_thread(service.run(gateway.id)['thread_id'], 'account')['messages'][-1]
        assert historical['ui_artifacts'] == [artifact]
        # Reopening the store needs no model call or in-memory artifact cache.
        reopened = type(service.store)(service.store.root)
        saved_thread = reopened.get('agent_thread', service.run(gateway.id)['thread_id'])
        assert saved_thread['messages'][-1]['ui_artifacts'] == [artifact]
        with service.store.connection() as db:
            db.execute('DELETE FROM agent_material WHERE run_id=?', (gateway.id,))
        service.update(gateway.id, status='running')
        revised = create_analysis_ui(gateway, '改成折线图', UISpec.model_validate(chart_spec('line')), reuse_ui_id=result['ui_id'])
        assert revised['ui_id'] != result['ui_id']
        assert find_artifact(service, gateway.guard(), result['ui_id']) == artifact
        assert find_artifact(service, gateway.guard(), revised['ui_id'])['datasets'] == artifact['datasets']
    asyncio.run(check())


def test_reuse_cannot_cross_account_or_thread(tmp_path, monkeypatch):
    service, _ = make_service(tmp_path, monkeypatch)
    async def check():
        gateway, scope = await counted(service)
        result = create_analysis_ui(gateway, '统计', UISpec.model_validate(chart_spec()), scope)
        for override in ({'account': 'other'}, {'thread_id': 'other'}):
            with pytest.raises(ValueError, match='不属于'):
                find_artifact(service, {**gateway.guard(), **override}, result['ui_id'])
    asyncio.run(check())


def test_dataset_pages_not_sampled_and_selected_scope_not_global(tmp_path, monkeypatch):
    service, _ = make_service(tmp_path, monkeypatch)
    async def check():
        gateway, scope = await counted(service)
        state = gateway.scope(scope)
        # Over 1000 distinct dates forces the UI to load the next saved page.
        state.update(start=0, end=1002 * 86400)
        gateway.put('scope:' + scope, 'deep_scope', state)
        gateway.save_messages([{'source': f'{i + 1:024x}', 'username': 'friend', 'anchor': f'db:{i}',
            'time': i * 86400, 'sender': '同名', 'sender_id': f'p{i % 2}', 'text': '消息', 'kind': 'text', 'media': {}} for i in range(1001)])
        other = await gateway.select(mode='statistics', start=100, end=200)
        service.update(gateway.id, statistics_scope=other['scope_handle'])
        result = create_analysis_ui(gateway, '完整分布', UISpec.model_validate(chart_spec()), scope)
        artifact = find_artifact(service, gateway.guard(), result['ui_id'])
        daily = artifact['datasets']['daily_totals']['rows']
        assert len(daily) >= 1001
        assert sum(x['count'] for x in daily) == artifact['datasets']['totals']['rows'][0]['total_messages']
        senders = artifact['datasets']['sender_ranking']['rows']
        assert {x['sender_id'] for x in senders if x['sender'] == '同名'} == {'p0', 'p1'}
    asyncio.run(check())


@pytest.mark.parametrize('mutation', ['cycle', 'unknown', 'expression', 'html', 'action'])
def test_rejects_executable_or_invalid_spec(mutation):
    spec = chart_spec()
    if mutation == 'cycle': spec['elements']['layout']['children'].append('layout')
    if mutation == 'unknown': spec['elements']['chart']['type'] = 'iframe'
    if mutation == 'expression': spec['elements']['chart']['props']['x'] = {'$eval': 'fetch()'}
    if mutation == 'html': spec['elements']['chart']['props']['html'] = '<script>alert(1)</script>'
    if mutation == 'action': spec['elements']['chart']['on'] = {'click': 'fetch'}
    with pytest.raises(ValidationError): UISpec.model_validate(spec)


def test_rejects_unknown_fields_and_uncomputed_data(tmp_path, monkeypatch):
    service, _ = make_service(tmp_path, monkeypatch)
    async def check():
        gateway = await prepared(service)
        scope = (await gateway.select(mode='statistics'))['scope_handle']
        with pytest.raises(ValueError, match='count_messages'):
            create_analysis_ui(gateway, '统计', UISpec.model_validate(chart_spec()), scope)
        await next(t for t in gateway.tools() if t.name == 'count_messages').ainvoke({'scope_handle': scope})
        bad = chart_spec()
        bad['elements']['chart']['props']['y'] = 'invented'
        with pytest.raises(ValueError, match='字段不存在'):
            create_analysis_ui(gateway, '统计', UISpec.model_validate(bad), scope)
        assert not gateway.guard().get('ui_artifacts')
    asyncio.run(check())


def test_findings_and_calculations_bind_saved_results_and_sources(tmp_path, monkeypatch):
    service, _ = make_service(tmp_path, monkeypatch)
    async def check():
        gateway, handle = await counted(service)
        gateway.put('test-finding', 'finding', {'text': '报价100元', 'sources': ['a' * 24], 'evidence_status': 'supported'})
        calc = await next(t for t in gateway.tools() if t.name == 'calculate_values').ainvoke({
            'scope_handle': handle, 'terms': [CalculationTerm(event_key='报价', value='100.25', sources=['a' * 24], confirmed=True).model_dump()]})
        spec = UISpec.model_validate({'root': 'root', 'elements': {
            'root': {'type': 'Stack', 'children': ['amount', 'facts', 'proof']},
            'amount': {'type': 'MetricCard', 'props': {'dataset': 'calculation', 'field': 'value'}},
            'facts': {'type': 'DataTable', 'props': {'dataset': 'findings'}},
            'proof': {'type': 'SourceList', 'props': {'dataset': 'sources'}},
        }})
        result = create_analysis_ui(gateway, '已确认结果', spec, handle, calc['calculation_id'])
        artifact = find_artifact(service, gateway.guard(), result['ui_id'])
        assert artifact['datasets']['calculation']['rows'][0]['value'] == '100.25'
        assert artifact['datasets']['findings']['rows'][0]['text'] == '报价100元'
        assert artifact['citations'][0]['source'] == 'a' * 24
        assert '不证明范围完整' in artifact['provenance']['coverage']
        # New unrelated range cannot borrow the saved amount or its sources.
        other = await gateway.select(mode='statistics', start=1, end=2)
        with pytest.raises(ValueError, match='来源不在'):
            create_analysis_ui(gateway, '不允许', spec, other['scope_handle'], calc['calculation_id'])
    asyncio.run(check())


def test_completed_daily_statistics_include_zero_days_and_timezone(tmp_path, monkeypatch):
    service, _ = make_service(tmp_path, monkeypatch)
    async def check():
        gateway, handle = await counted(service)
        scope = gateway.scope(handle)
        original = gateway.guard()['evidence']['a' * 24]
        scope.update(start=original['time'] - 3 * 86400, end=original['time'] + 1)
        gateway.put('scope:' + handle, 'deep_scope', scope)
        result = create_analysis_ui(gateway, '日分布', UISpec.model_validate(chart_spec()), handle)
        artifact = find_artifact(service, gateway.guard(), result['ui_id'])
        assert [r['count'] for r in artifact['datasets']['daily_totals']['rows']] == [0, 0, 0, 1]
        assert artifact['provenance']['timezone_offset'] == gateway.guard()['timezone_offset']
    asyncio.run(check())


def test_partial_daily_statistics_do_not_invent_zero_days(tmp_path, monkeypatch):
    service, _ = make_service(tmp_path, monkeypatch)
    async def check():
        gateway, handle = await counted(service)
        scope = gateway.scope(handle)
        original = gateway.guard()['evidence']['a' * 24]
        scope.update(start=original['time'] - 3 * 86400, end=original['time'] + 1,
                     warnings=['部分数据库不可读取'])
        gateway.put('scope:' + handle, 'deep_scope', scope)
        result = create_analysis_ui(gateway, '部分日分布', UISpec.model_validate(chart_spec()), handle)
        artifact = find_artifact(service, gateway.guard(), result['ui_id'])
        daily = artifact['datasets']['daily_totals']
        assert [r['count'] for r in daily['rows']] == [1]
        assert '缺失日期不表示' in daily['note']
        assert '部分数据库不可读取' in artifact['provenance']['coverage']
    asyncio.run(check())


def test_sse_replay_and_terminal_snapshot_keep_same_artifact(tmp_path, monkeypatch):
    service, _ = make_service(tmp_path, monkeypatch)
    async def check():
        gateway, handle = await counted(service)
        result = create_analysis_ui(gateway, '统计', UISpec.model_validate(chart_spec()), handle)
        service.update(gateway.id, answer=result['reference'])
        service.timeline_item(gateway.id, 'answer', result['reference'], status='running')
        service.finish(gateway.id, 'completed')
        import json
        with service.store.connection() as db:
            events = [json.loads(row[0]) for row in db.execute('SELECT body FROM events ORDER BY id')]
        events = [e for e in events if e.get('run_id') == gateway.id and e.get('ui_artifacts')]
        assert {'analysis_ui', 'timeline_item', 'run_snapshot'} <= {e['type'] for e in events}
        assert all(e['ui_artifacts'][0]['id'] == result['ui_id'] for e in events)
        assert service.public_run(gateway.id, 'account')['ui_artifacts'][0]['id'] == result['ui_id']
    asyncio.run(check())


def test_real_graph_optional_ui_and_later_turn_reuses_saved_snapshot(tmp_path, monkeypatch):
    def count(messages):
        return action('count_messages', {'scope_handle': last_result(messages)['scope_handle']})
    def ui(messages):
        return action('create_analysis_ui', {'title': '消息量', 'spec': chart_spec(), 'scope_handle': last_result(messages)['scope_handle']})
    def answer(messages):
        return AIMessage(content='消息量如下。\n\n' + last_result(messages)['reference'])
    service, client = make_service(tmp_path, monkeypatch, responses=[
        action('select_chat_scope', {'mode': 'statistics'}), count, ui, answer])
    async def check():
        thread, run = await execute(service, '看看消息量变化，适合的话画图')
        assert run['status'] == 'completed', run.get('error')
        assert len(run['ui_artifacts']) == 1
        assert run['used']['models'] == 4
        old = run['ui_artifacts'][0]
        calls = list(service.tools.calls)
        client.responses = [action('create_analysis_ui', {'title': '折线图', 'spec': chart_spec('line'), 'reuse_ui_id': old['id']}), answer]
        _, newer = await execute(service, '换成折线图', request='two', thread=thread)
        assert newer['status'] == 'completed', newer.get('error')
        assert newer['ui_artifacts'][0]['derived_from'] == old['id']
        assert service.tools.calls == calls
        assert newer['used']['models'] == 2
    asyncio.run(check())


@pytest.mark.parametrize('question', ['你好', '只用文字说明你能做什么', '告诉我一个数值即可，不要图表'])
def test_text_only_does_not_require_ui_or_extra_model_call(tmp_path, monkeypatch, question):
    service, client = make_service(tmp_path, monkeypatch, responses=[AIMessage(content='可以直接用文字回答。')])
    async def check():
        _, run = await execute(service, question)
        assert run['status'] == 'completed'
        assert not run.get('ui_artifacts')
        assert len(client.requests) == 1
    asyncio.run(check())
