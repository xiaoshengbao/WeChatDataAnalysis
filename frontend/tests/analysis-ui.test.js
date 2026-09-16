import { describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import AgentAnalysisUI from '../components/chat/AgentAnalysisUI.vue'
import AgentAnswer from '../components/chat/AgentAnswer.vue'
import AnalysisDataTable from '../components/chat/AnalysisDataTable.vue'
import { renderAgentBlocks, copyAgentText } from '../utils/agentMarkdown'
import { chartOption } from '../utils/analysisUi'
import { checkedSpec } from '../lib/analysis-ui-catalog'
import { mergeRunEvent } from '../utils/agentTimeline'
import modelArtifact from './fixtures/analysis-ui-model.json'

const id = '1'.repeat(24), source = 'a'.repeat(24)
const fixture = () => ({ id, schema_version: 1, title: '消息概览',
  provenance: { start: 0, end: 86400, timezone_offset: 28800, coverage: '统计范围已读取', source_note: '仅为依据示例' },
  datasets: { totals: { rows: [{ total_messages: 42 }], columns: ['total_messages'], numeric: ['total_messages'] },
    sources: { rows: [{ text: '原文<script>alert(1)</script>', sender: '甲', sources: [source] }], columns: ['sender', 'text'], numeric: [] } },
  citations: [{ source, username: 'group', anchor: 'db:1', text: '原文', time: 1 }],
  spec: { root: 'root', elements: {
    root: { type: 'Grid', props: { title: '' }, children: ['metric', 'source'] },
    metric: { type: 'MetricCard', props: { title: '消息总数', dataset: 'totals', field: 'total_messages' }, children: [] },
    source: { type: 'SourceList', props: { title: '来源示例', dataset: 'sources' }, children: [] },
  } },
})

describe('optional analysis UI', () => {
  it('accepts a real model artifact after production backend normalization', () => {
    expect(checkedSpec(modelArtifact)).toEqual(modelArtifact.spec)
    expect(modelArtifact.datasets.totals.rows[0].total_messages).toBe(6)
  })
  it('renders text and UI in order without resetting citation numbering', () => {
    const refs = [{ source }, { source: 'b'.repeat(24) }]
    const blocks = renderAgentBlocks(`前文 [[${source}]]\n\n[[ui:${id}]]\n\n后文 [[${refs[1].source}]]`, refs)
    expect(blocks.map(b => b.kind)).toEqual(['text', 'ui', 'text'])
    expect(blocks[0].html).toContain('查看来源 1')
    expect(blocks[2].html).toContain('查看来源 2')
  })
  it('hides incomplete markers on every streaming boundary, including stopped output', () => {
    const marker = `[[ui:${id}]]`
    for (let i = 3; i < marker.length; i++) {
      const blocks = renderAgentBlocks(`前文\n\n${marker.slice(0, i)}`, [], true)
      expect(blocks.some(b => b.kind === 'ui')).toBe(false)
      expect(blocks.map(b => b.html).join('')).not.toContain('[[u')
      expect(copyAgentText(`前文\n\n${marker.slice(0, i)}`)).not.toContain('[[u')
    }
  })
  it('keeps fenced/inline code literal and ordinary responses unchanged', () => {
    const text = `\`[[ui:${id}]]\`\n\n\`\`\`json\n[[ui:${id}]]\n\`\`\``
    expect(renderAgentBlocks(text).some(b => b.kind === 'ui')).toBe(false)
    expect(copyAgentText(text)).toBe(text)
    expect(renderAgentBlocks('你好')[0].html).toBe('<p>你好</p>\n')
  })
  it('copies readable data instead of the internal UI protocol', () => {
    const copy = copyAgentText(`前文\n\n[[ui:${id}]]\n\n后文`, [], [], [fixture()])
    expect(copy).toContain('消息总数：42')
    expect(copy).toContain('统计范围已读取')
    expect(copy).not.toContain('[[ui:')
  })
  it('uses the actual json-render Vue registry and existing source navigation', async () => {
    const locate = vi.fn().mockResolvedValue(true)
    const artifact = fixture()
    const view = mount(AgentAnalysisUI, { props: { artifact }, global: { provide: { agentSourceNavigation: { locate } } } })
    await flushPromises()
    expect(view.find('.analysis-grid').exists()).toBe(true)
    expect(view.find('.analysis-metric').text()).toContain('42')
    expect(view.find('script').exists()).toBe(false)
    expect(view.text()).toContain('<script>alert(1)</script>')
    await view.findAll('button').find(b => b.text() === '查看来源').trigger('click')
    expect(locate).toHaveBeenCalledWith(artifact.citations[0])
    view.unmount()
  })
  it('preserves mounted UI and filter state as trailing text streams', async () => {
    const view = mount(AgentAnswer, { props: { text: `前文\n\n[[ui:${id}]]\n\n后文`, uiArtifacts: [fixture()] }, global: { stubs: { AgentAnalysisUI } } })
    await flushPromises()
    const grid = view.find('.analysis-grid').element
    await view.find('input').setValue('不存在')
    await view.setProps({ text: `前文\n\n[[ui:${id}]]\n\n后文继续补充`, streaming: true })
    expect(view.find('.analysis-grid').element).toBe(grid)
    expect(view.find('input').element.value).toBe('不存在')
    view.unmount()
  })
  it('locally filters, sorts and paginates without changing snapshot data', async () => {
    const rows = Array.from({ length: 65 }, (_, i) => ({ value: i, name: `成员${i}` }))
    const original = JSON.stringify(rows)
    const view = mount(AnalysisDataTable, { props: { table: { rows, columns: ['value', 'name'], numeric: ['value'] } } })
    expect(view.findAll('tbody tr')).toHaveLength(30)
    await view.find('th button').trigger('click'); await view.find('th button').trigger('click')
    expect(view.find('tbody td').text()).toBe('64')
    await view.find('input').setValue('成员64')
    expect(view.findAll('tbody tr')).toHaveLength(1)
    expect(JSON.stringify(rows)).toBe(original)
    expect(view.emitted('query')).toBeUndefined()
    view.unmount()
  })
  it.each(['on', 'visible', 'repeat'])('rejects unexpected json-render features: %s', key => {
    const a = fixture(); a.spec.elements.metric[key] = { $eval: 'fetch()' }
    expect(() => checkedSpec(a)).toThrow()
  })
  it('falls back to saved data on invalid specs', () => {
    const a = fixture(); a.spec.elements.metric.type = 'UnknownComponent'
    const view = mount(AgentAnalysisUI, { props: { artifact: a } })
    expect(view.text()).toContain('已保留数据快照')
    expect(view.text()).toContain('42')
    view.unmount()
  })
  it('merges SSE artifacts and rejects old versions', () => {
    const current = { id: 'run', version: 2, updated_at: 2 }
    const event = { type: 'analysis_ui', run_id: 'run', version: 2, updated_at: 3, ui_artifacts: [fixture()] }
    const accepted = mergeRunEvent(current, event)
    expect(accepted.ui_artifacts).toHaveLength(1)
    expect(mergeRunEvent(accepted, { ...event, version: 1, ui_artifacts: [] })).toBe(accepted)
  })
  it.each(['bar', 'line', 'pie', 'heatmap'])('creates safe %s ECharts options from data', chart_type => {
    const table = { rows: [{ day: '一', sender_id: 'a', sender: '同名', count: 2 }, { day: '二', sender_id: 'b', sender: '同名', count: 3 }] }
    const option = chartOption({ chart_type, x: 'day', y: chart_type === 'heatmap' ? 'sender_id' : 'count', value: 'count' }, table)
    expect(option.series[0].type).toBe(chart_type)
    expect(option.tooltip.renderMode).toBe('richText')
    expect(JSON.stringify(option)).not.toContain('formatter')
  })
})
