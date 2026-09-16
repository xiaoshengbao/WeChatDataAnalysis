"""Optional, data-bound UI artifacts. No model code, URLs or model-supplied numbers."""
import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from .deep_synchronization import serialized

UI_PATTERN = re.compile(r'\[\[ui:([a-f0-9]{24})\]\]', re.I)
MAX_ROWS = 10000


class UIProps(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field('', max_length=120)
    dataset: str = Field('', max_length=40, description='totals/daily_totals/sender_ranking/by_day_sender/findings/calculation/calculation_terms/sources')
    field: str = Field('', max_length=40, description='MetricCard 的数值列；数据集必须只有一行')
    chart_type: Literal['line', 'bar', 'pie', 'heatmap'] = 'bar'
    x: str = Field('', max_length=40)
    y: str = Field('', max_length=40)
    value: str = Field('', max_length=40, description='热力图数值列')
    series: str = Field('', max_length=40, description='可选系列分组列')
    columns: list[str] = Field(default_factory=list, max_length=12)


class UIElement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['Stack', 'Grid', 'MetricCard', 'DataTable', 'Chart', 'SourceList']
    props: UIProps = Field(default_factory=UIProps)
    children: list[str] = Field(default_factory=list, max_length=40)


class UISpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    root: str
    elements: dict[str, UIElement] = Field(min_length=1, max_length=40)

    @model_validator(mode='after')
    def tree(self):
        seen = set()
        def visit(key, depth):
            if key not in self.elements or key in seen or depth > 6:
                raise ValueError('界面必须是无循环、无重复节点的树，深度不超过6')
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', key):
                raise ValueError('组件编号仅支持字母、数字、下划线和连字符')
            seen.add(key)
            node = self.elements[key]
            if node.type not in ('Stack', 'Grid') and node.children:
                raise ValueError('只有 Stack/Grid 可以包含子节点')
            for child in node.children:
                visit(child, depth + 1)
        visit(self.root, 1)
        if seen != self.elements.keys():
            raise ValueError('存在未连接的组件')
        return self


def find_artifact(service, run, ui_id):
    """Only reuse saved data from this account AND this conversation."""
    if not re.fullmatch(r'[a-f0-9]{24}', ui_id):
        raise ValueError('界面编号无效')
    with service.store.connection() as db:
        row = db.execute("SELECT p.body FROM agent_piece p JOIN records r ON r.kind='agent_run' AND r.id=p.run_id "
            "WHERE p.kind='analysis_ui' AND p.id=? AND r.account=? AND json_extract(r.body,'$.thread_id')=? LIMIT 1",
            ('ui:' + ui_id, run['account'], run['thread_id'])).fetchone()
    if not row:
        raise ValueError('界面不存在或不属于当前账号和对话')
    return json.loads(row[0])


def referenced_artifacts(run, text=None):
    keys = {key.lower() for key in UI_PATTERN.findall(text if text is not None else '\n'.join(
        [run.get('answer', '')] + [x.get('text', '') for x in run.get('timeline', [])]))}
    return [a for a in run.get('ui_artifacts', []) if a['id'] in keys]


def dataset(rows, numeric=()):
    if len(rows) > MAX_ROWS:
        raise ValueError('可视化数据超过10000行，请缩小范围；不会截取分页结果冒充完整分布')
    columns = list(dict.fromkeys(k for row in rows for k in row if k != 'sources'))
    return {'rows': rows, 'columns': columns, 'numeric': list(numeric)}


def statistics_data(gateway, scope, wanted):
    if not gateway.get('statistics-coverage:' + scope['handle']):
        raise ValueError('请先对该范围调用 count_messages，再创建统计界面')
    needed = {'daily_totals': 'daily_totals', 'sender_ranking': 'sender_ranking', 'by_day_sender': 'items'}
    result = {key: [] for name, key in needed.items() if name in wanted}
    more_flags = {'items': 'has_more', 'daily_totals': 'daily_has_more', 'sender_ranking': 'sender_has_more'}
    offset = 0
    while True:
        gateway.guard()
        page = gateway.service.workspace.statistics(gateway.id, offset, 1000, scope_handle=scope['handle'])
        for key in result:
            result[key].extend(page[key])
            if len(result[key]) > MAX_ROWS:
                raise ValueError('分布超过10000行，请缩小统计范围后画图')
        if not any(page[more_flags[k]] for k in result):
            break
        offset += 1000
    grouped = defaultdict(int)
    names = {}
    for row in result.get('items', []):
        sender = row.get('sender_id') or row['username'] + ':' + (row.get('sender') or 'unknown')
        grouped[(row['day'], sender)] += row['count']
        names[sender] = row.get('sender') or sender
    daily_note = ''
    if 'daily_totals' in result:
        zone = timezone(timedelta(seconds=gateway.guard().get('timezone_offset', 0)))
        first = datetime.fromtimestamp(scope['start'], zone).date()
        last = datetime.fromtimestamp(scope['end'] - 1, zone).date()
        days = (last - first).days + 1
        counts = {row['day']: row['count'] for row in result['daily_totals']}
        if days > MAX_ROWS and counts:
            first, last = datetime.fromisoformat(min(counts)).date(), datetime.fromisoformat(max(counts)).date()
            days = (last - first).days + 1
            daily_note = '日趋势仅展示首末消息之间的日期，查询范围内其余日期消息数为0。'
        if days > MAX_ROWS:
            raise ValueError('每日趋势超过10000天，请缩小时间范围')
        if scope['read_complete'] and not scope.get('warnings'):
            result['daily_totals'] = [{'day': (first + timedelta(days=i)).isoformat(),
                'count': counts.get((first + timedelta(days=i)).isoformat(), 0)} for i in range(days)]
        else:
            daily_note = '仅显示已读取消息的日期，缺失日期不表示消息数为0。'
    return {key: value for key, value in {
        'totals': dataset([{'total_messages': page['total_messages'], 'active_senders': page['active_senders']}], ['total_messages', 'active_senders']),
        'daily_totals': {**dataset(result.get('daily_totals', []), ['count']), 'note': daily_note},
        'sender_ranking': dataset(result.get('sender_ranking', []), ['count']),
        'by_day_sender': dataset([{'day': day, 'sender_id': sender, 'sender': names[sender], 'count': count}
                                  for (day, sender), count in grouped.items()], ['count']),
    }.items() if key in wanted}


def snapshot_data(gateway, spec, scope_handle, calculation_id):
    run = gateway.guard()
    scope = gateway.scope(scope_handle)
    wanted = {node.props.dataset for node in spec.elements.values() if node.props.dataset}
    data, sources = {}, set()
    if wanted.intersection({'totals', 'daily_totals', 'sender_ranking', 'by_day_sender'}):
        data.update(statistics_data(gateway, scope, wanted))
    if 'findings' in wanted:
        rows, offset = [], 0
        while True:
            page = gateway.service.workspace.page(gateway.id, gateway.version, 'finding', offset, 500)
            for item in page['items']:
                proofs = run['evidence'].get_many(item.get('sources', []))
                if not proofs or len(proofs) != len(set(item.get('sources', []))) or not all(gateway.permits(scope, p) for p in proofs.values()):
                    continue
                rows.append({k: item.get(k, '') for k in ('text', 'event_time', 'evidence_status')} | {'sources': list(proofs)})
                sources.update(proofs)
            if len(rows) > MAX_ROWS:
                raise ValueError('发现超过10000行，请缩小范围')
            if not page['has_more']:
                break
            offset += 500
        data['findings'] = dataset(rows)
    if wanted.intersection({'calculation', 'calculation_terms'}):
        calc = gateway.get('calculation:' + calculation_id)
        if not calc:
            raise ValueError('请使用 calculate_values 返回的 calculation_id')
        proofs = run['evidence'].get_many(calc['sources'])
        if len(proofs) != len(set(calc['sources'])) or not all(gateway.permits(scope, p) for p in proofs.values()):
            raise ValueError('计算结果的来源不在指定范围')
        sources.update(proofs)
        data['calculation'] = dataset([{k: calc[k] for k in ('value', 'event_count', 'operation')}], ['value', 'event_count'])
        data['calculation_terms'] = dataset([{k: term[k] for k in ('event_key', 'value', 'sources')} for term in calc['terms']], ['value'])
    # Representative originals are explicitly labelled as examples, never as full coverage.
    if 'sources' in wanted:
        for original in run['evidence'].rows():
            if gateway.permits(scope, original):
                sources.add(original['source'])
            if len(sources) >= 100:
                break
    proofs = run['evidence'].get_many(sources)
    citations = [gateway.service.public_source(p, run['account']) for p in proofs.values()]
    data['sources'] = dataset([{'sender': p.get('sender', ''), 'text': p.get('text', '')[:2000], 'sources': [p['source']]} for p in citations[:100]])
    coverage = '统计范围已读取' if scope['mode'] == 'statistics' and scope['read_complete'] else '基于已读取资料，非全量统计'
    if calculation_id:
        coverage += '；计算仅涵盖已确认事件，不证明范围完整'
    if scope.get('warnings'):
        coverage = '基于可读取资料，范围有缺口：' + '；'.join(str(w) for w in scope['warnings']) + ('；计算仅涵盖已确认事件' if calculation_id else '')
    return data, citations, {'start': scope['start'], 'end': scope['end'], 'timezone_offset': run.get('timezone_offset', 0),
        'conversations': scope['conversations'], 'sender': scope.get('sender', ''), 'coverage': coverage,
        'scope_summary': '、'.join(scope.get('names', {}).get(u, u) for u in scope['conversations']) +
            ('；仅统计成员：' + scope['sender'] if scope.get('sender') else ''),
        'basis': '消息数按已读取的消息记录计数；发言人数按成员标识去重，缺失标识时按会话与昵称区分。',
        'source_note': '来源列表仅展示最多100条依据示例'}


def validate_bindings(spec, data):
    clean = spec.model_dump()
    allowed = {'Stack': {'title'}, 'Grid': {'title'}, 'MetricCard': {'title', 'dataset', 'field'},
        'DataTable': {'title', 'dataset', 'columns'}, 'SourceList': {'title', 'dataset'},
        'Chart': {'title', 'dataset', 'chart_type', 'x', 'y', 'value', 'series'}}
    for key, node in spec.elements.items():
        explicit = node.props.model_dump(exclude_unset=True)
        if set(explicit) - allowed[node.type]:
            raise ValueError(f'{node.type} 不支持属性 {sorted(set(explicit) - allowed[node.type])}')
        clean['elements'][key]['props'] = {k: v for k, v in node.props.model_dump().items() if k in allowed[node.type]}
        p = node.props
        if node.type in ('Stack', 'Grid'):
            continue
        if p.dataset not in data:
            raise ValueError(f'数据集 {p.dataset} 不存在；可用：{list(data)}')
        table = data[p.dataset]
        columns = set(table['columns'])
        fields = p.columns if node.type == 'DataTable' else [p.field] if node.type == 'MetricCard' else [p.x, p.y] if node.type == 'Chart' else []
        if node.type == 'Chart':
            fields += [f for f in (p.value, p.series) if f]
        if not set(fields) <= columns:
            raise ValueError(f'字段不存在；{p.dataset} 的列为 {table["columns"]}')
        if node.type == 'MetricCard' and (len(table['rows']) != 1 or p.field not in table['numeric']):
            raise ValueError('指标卡只绑定单行数据集的数值列，例如 totals.total_messages 或 calculation.value')
        if node.type == 'SourceList' and p.dataset != 'sources':
            raise ValueError('SourceList 必须绑定 sources 数据集')
        if node.type != 'Chart':
            continue
        if p.chart_type == 'pie' and (p.series or len(table['rows']) > 100):
            raise ValueError('饼图仅支持单系列且最多100项，请使用表格或缩小范围')
        if p.chart_type in ('line', 'bar'):
            series_count = len({row[p.series] for row in table['rows']}) if p.series else 1
            if series_count > 32 or len({row[p.x] for row in table['rows']}) * series_count > 20000:
                raise ValueError('图表系列或坐标组合过多，请缩小范围或用表格')
        numeric = p.value if p.chart_type == 'heatmap' else p.y
        if numeric not in table['numeric']:
            raise ValueError('图表数值列必须是程序统计或计算列')
        keys = set()
        for row in table['rows']:
            number = float(row[numeric])
            if not math.isfinite(number) or abs(number) > 9007199254740991:
                raise ValueError('数值超出图表精确显示范围，请使用指标卡或表格')
            if p.chart_type == 'pie' and number < 0:
                raise ValueError('负数不适合饼图，请用柱状图')
            identity = (row[p.x], row[p.y] if p.chart_type == 'heatmap' else row.get(p.series))
            if identity in keys:
                raise ValueError('图表维度存在重复，请选择系列字段或更合适的数据集')
            keys.add(identity)
    return clean


@serialized
def create_analysis_ui(gateway, title, spec, scope_handle='', calculation_id='', reuse_ui_id=''):
    run = gateway.guard()
    if run.get('parent_run_id'):
        raise ValueError('分析界面由主助手在汇总后生成')
    if reuse_ui_id:
        previous = find_artifact(gateway.service, run, reuse_ui_id)
        data, citations, provenance = previous['datasets'], previous['citations'], previous['provenance']
    else:
        data, citations, provenance = snapshot_data(gateway, spec, scope_handle, calculation_id)
    clean = validate_bindings(spec, data)
    used = {n['props'].get('dataset') for n in clean['elements'].values()}
    data = {key: value for key, value in data.items() if key in used}
    if not data:
        raise ValueError('分析界面至少包含一个绑定真实数据的组件')
    artifact = {'schema_version': 1, 'title': title[:120], 'spec': clean, 'datasets': data,
        'citations': citations, 'provenance': provenance, 'derived_from': reuse_ui_id,
        'run_id': run['id'], 'version': run['version']}
    serialized = json.dumps(artifact, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if len(serialized.encode()) > 2 * 1024 * 1024:
        raise ValueError('界面数据超过2MiB，请缩小范围或减少组件')
    ui_id = hashlib.sha256(serialized.encode()).hexdigest()[:24]
    artifact['id'] = ui_id
    gateway.put('ui:' + ui_id, 'analysis_ui', artifact)
    artifacts = {a['id']: a for a in gateway.guard().get('ui_artifacts', [])}
    artifacts[ui_id] = artifact
    gateway.service.update(gateway.id, ui_artifacts=list(artifacts.values()))
    current = gateway.guard()
    gateway.service.store.event(run['account'], 'agent', {'type': 'analysis_ui', 'run_id': run['id'],
        'thread_id': run['thread_id'], 'version': run['version'], 'updated_at': current['updated_at'], 'ui_artifacts': [artifact]})
    return {'ui_id': ui_id, 'reference': f'[[ui:{ui_id}]]', 'title': artifact['title'],
        'datasets': {key: value['columns'] for key, value in data.items()},
        'coverage': provenance['coverage'], 'instruction': '需要展示时在正文单独一行插入 reference；用文字说明结论，不输出JSON或内部数据编号。'}
