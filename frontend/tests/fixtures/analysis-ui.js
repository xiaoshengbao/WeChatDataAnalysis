import { createApp, h, ref } from 'vue'
import AgentAnswer from '../../components/chat/AgentAnswer.vue'
import '../../assets/css/agent.css'
import modelArtifact from './analysis-ui-model.json'
const id = '1'.repeat(24)
const data = [128, 96, 173, 142, 203, 88, 67].map((count, i) => ({ day: `09-${String(i + 10).padStart(2, '0')}`, count }))
const node = (type, props, children = []) => ({ type, props, children })
const artifact = { id, schema_version: 1, title: '最近一周消息概览（演示数据）',
  provenance: { start: 1788969600, end: 1789574400, timezone_offset: 28800, coverage: '演示数据 · 已完整统计', source_note: '' }, citations: [],
  datasets: { totals: { columns: ['total_messages', 'active_senders'], numeric: ['total_messages', 'active_senders'], rows: [{ total_messages: 897, active_senders: 18 }] },
    daily_totals: { columns: ['day', 'count'], numeric: ['count'], rows: data } },
  spec: { root: 'root', elements: {
    root: node('Stack', { title: '' }, ['metrics', 'chart', 'table']),
    metrics: node('Grid', { title: '' }, ['total', 'active']),
    total: node('MetricCard', { title: '消息总数', dataset: 'totals', field: 'total_messages' }),
    active: node('MetricCard', { title: '活跃成员', dataset: 'totals', field: 'active_senders' }),
    chart: node('Chart', { title: '每日消息量', dataset: 'daily_totals', chart_type: 'line', x: 'day', y: 'count', value: '', series: '' }),
    table: node('DataTable', { title: '按日期查看', dataset: 'daily_totals', columns: ['day', 'count'] }),
  } },
}
createApp({ setup() {
  const narrow = ref(false), shown = ref(true), suffix = ref(''), dark = ref(false), actualModel = ref(false)
  const theme = () => { dark.value = !dark.value; document.documentElement.dataset.theme = dark.value ? 'dark' : 'light' }
  return () => h('main', { style: { maxWidth: narrow.value ? '380px' : '820px', margin: '24px auto', padding: '24px', background: dark.value ? '#222629' : '#fff', color: dark.value ? '#eef2f7' : '#243047', fontFamily: 'system-ui' } }, [
    h('nav', { style: 'display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px' }, [
      h('button', { onClick: () => { narrow.value = !narrow.value } }, '切换窄侧栏'), h('button', { onClick: theme }, '切换主题'),
      h('button', { onClick: () => { shown.value = !shown.value } }, '切换纯文字'), h('button', { onClick: () => { suffix.value += ' 继续补充文字。' } }, '追加文字'),
      h('button', { onClick: () => { actualModel.value = !actualModel.value } }, '切换实模产物'),
    ]),
    h(AgentAnswer, { uiArtifacts: [actualModel.value ? modelArtifact : artifact], text: shown.value ?
      (actualModel.value ? `以下界面由 deepseek-flash 调用生产工具生成，数据来自虚构聊天样本。\n\n[[ui:${modelArtifact.id}]]\n\n` : `消息量在 9 月 14 日达到高点，随后回落。\n\n[[ui:${id}]]\n\n可在表格中查看每日数量，或展开图表比较。`) + suffix.value : '普通回答直接使用文字，不自动生成图表。' }),
  ])
} }).mount('#app')
