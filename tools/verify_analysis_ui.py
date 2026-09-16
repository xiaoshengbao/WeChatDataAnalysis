"""Run fixed optional-UI cases against a configured model using synthetic chats only.

The profile is read-only; credentials stay in memory. Output contains a separate
test store, saved UI snapshots, observed choices, model calls and elapsed time.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sqlite3
import time

from verify_deepagents_matrix import MatrixTools

CASES = [
    ('greeting', '你好，简单介绍一下你能做什么。', 'none'),
    ('scalar', '项目协作群2026年9月1日到9月9日（不含）的消息共有多少条？只告诉我数字即可。', 'none'),
    ('text_only', '用纯文字比较项目协作群2026年9月1日到9月9日（不含）每天的消息量，不要图表或卡片。', 'none'),
    ('dashboard', '请统计项目协作群2026年9月1日到9月9日（不含）的消息，用消息总数指标卡、每日消息量柱状图和成员排行表组合展示。', 'required'),
    ('revise', '把刚才的柱状图改成折线图，其他不变，复用已保存数据。', 'reuse'),
    ('auto_trend', '分析项目协作群2026年9月1日到9月9日（不含）消息量的每日变化及成员分布，选择你认为最清楚的表达方式。', 'observe'),
]


async def main(args):
    from wechat_decrypt_tool.ai.storage import AIStore
    from wechat_decrypt_tool.ai.providers import ModelService, public_profile
    from wechat_decrypt_tool.ai.service import AIService
    from wechat_decrypt_tool.ai.agent_service import AgentService
    from wechat_decrypt_tool.ai.analysis_ui import referenced_artifacts
    source = args.data / 'output/ai/ai.sqlite3'
    with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute("SELECT body FROM records WHERE kind='profile' AND id=?", (args.profile,)).fetchone()
        if not row:
            raise ValueError('指定模型配置不存在')
        profile = json.loads(row[0])
    args.output.mkdir(parents=True, exist_ok=True)
    store = AIStore(args.output / 'state')
    store.put('profile', public_profile(profile), id=args.profile)
    store.put('defaults', {'text': args.profile}, id='global')
    models = ModelService(store)
    resolve = models.resolve
    def configured(*values, **kwargs):
        result = resolve(*values, **kwargs)
        if result['id'] == args.profile:
            result['api_key'] = profile.get('api_key', '')
        return result
    models.resolve = configured
    service = AgentService(AIService(store, models), MatrixTools())
    results, dashboard_thread = [], None
    try:
        for name, question, expected in CASES:
            if args.cases and name not in args.cases.split(','):
                continue
            if name == 'revise' and dashboard_thread is None:
                continue
            thread = dashboard_thread if name == 'revise' else await service.create_thread('analysis-ui-eval', 'project', name)
            if name == 'dashboard': dashboard_thread = thread
            start = time.monotonic()
            pending = await service.submit(thread['id'], 'analysis-ui-eval', {'text': question, 'request_id': name})
            worker = service.workers[pending['id']]
            while not worker.done():
                await asyncio.wait([worker], timeout=10)
                if time.monotonic() - start > args.timeout:
                    await service.stop_run(pending['id'], 'analysis-ui-eval')
                current = service.run(pending['id'])
                print(json.dumps({'case': name, 'status': current['status'], 'model_calls': current['used']['models']}, ensure_ascii=False), flush=True)
            await worker
            run = service.public_run(pending['id'], 'analysis-ui-eval')
            ui = referenced_artifacts(run, run['answer'])
            actions = [t['action'] for t in run['timeline'] if t.get('kind') == 'tool']
            checks = {'completed': run['status'] == 'completed'}
            if expected == 'none': checks['text_only'] = not ui and 'create_analysis_ui' not in actions
            if expected in ('required', 'reuse'): checks['rendered_ui'] = bool(ui)
            if expected == 'reuse':
                checks['reused_data'] = bool(ui) and all(a['derived_from'] for a in ui)
                checks['no_requery'] = not any(a in ('read_messages', 'search_messages', 'count_messages') for a in actions)
            if name == 'dashboard':
                types = {n['type'] for a in ui for n in a['spec']['elements'].values()}
                checks['combined_components'] = {'MetricCard', 'Chart', 'DataTable'} <= types
                checks['program_count'] = any(a['datasets'].get('totals', {}).get('rows', [{}])[0].get('total_messages') == 6 for a in ui)
            if name == 'revise': checks['line_chart'] = any(n['type'] == 'Chart' and n['props']['chart_type'] == 'line' for a in ui for n in a['spec']['elements'].values())
            entry = {'case': name, 'expected': expected, 'model': profile.get('model'), 'status': run['status'],
                'seconds': round(time.monotonic() - start, 2), 'model_calls': run['used']['models'], 'actions': actions,
                'ui_count': len(ui), 'checks': checks, 'passed': all(checks.values()), 'error': run['error'],
                'answer': run['answer'], 'ui_artifacts': ui}
            results.append(entry)
            (args.output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({k: v for k, v in entry.items() if k not in ('answer', 'ui_artifacts')}, ensure_ascii=False), flush=True)
            if 'HTTP 402' in run['error'] or (run.get('error_info') or {}).get('action') == 'settings': break
    finally:
        await service.stop()
    return all(r['passed'] for r in results)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', default='')
    parser.add_argument('--timeout', type=float, default=120)
    raise SystemExit(0 if asyncio.run(main(parser.parse_args())) else 1)
