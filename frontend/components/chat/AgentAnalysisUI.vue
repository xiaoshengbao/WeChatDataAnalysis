<template>
  <section class="agent-analysis-ui" :aria-label="artifact.title">
    <header><strong>{{ artifact.title }}</strong></header>
    <p class="analysis-provenance">{{ range }} · {{ artifact.provenance?.coverage }}</p>
    <details v-if="artifact.provenance?.scope_summary" class="analysis-provenance"><summary>数据范围与口径</summary><p>{{ artifact.provenance.scope_summary }}</p><p>{{ artifact.provenance.basis }}</p></details>
    <p v-if="locateError" role="alert">{{ locateError }}</p>
    <template v-if="failed || !spec">
      <p role="status">分析界面暂不可用，已保留数据快照。</p>
      <pre class="analysis-fallback">{{ fallback }}</pre>
      <AnalysisDataTable v-for="(table, key) in artifact.datasets" :key="key" :table="table" @locate="locate" />
    </template>
    <JSONUIProvider v-else :registry="registry"><Renderer :spec="spec" :registry="registry" /></JSONUIProvider>
  </section>
</template>
<script setup>
import { computed, defineAsyncComponent, h, inject, onErrorCaptured, ref } from 'vue'
import { defineRegistry, Renderer, JSONUIProvider } from '@json-render/vue'
import { analysisCatalog, checkedSpec } from '~/lib/analysis-ui-catalog'
import { artifactText, displayCell, labelFor } from '~/utils/analysisUi'
import AnalysisDataTable from './AnalysisDataTable.vue'
const AnalysisChart = defineAsyncComponent(() => import('./AnalysisChart.vue'))
const props = defineProps({ artifact: { type: Object, required: true } })
const emit = defineEmits(['locate'])
const failed = ref(false), locateError = ref(''), navigation = inject('agentSourceNavigation', null)
const spec = computed(() => { try { return checkedSpec(props.artifact) } catch { return null } })
const fallback = computed(() => artifactText(props.artifact))
const range = computed(() => {
  const p = props.artifact.provenance || {}, offset = p.timezone_offset || 0
  if (!Number.isFinite(p.start) || !Number.isFinite(p.end)) return '范围信息未保存'
  const format = stamp => new Date((stamp + offset) * 1000).toISOString().slice(0, 16).replace('T', ' ')
  return `${format(p.start)} 至 ${format(p.end)}（不含结束时刻，UTC${offset >= 0 ? '+' : ''}${offset / 3600}）`
})
async function locate(source) {
  locateError.value = ''
  const citation = props.artifact.citations?.find(c => c.source === source)
  if (!citation) { locateError.value = '此来源暂不可用'; return }
  try {
    if (navigation?.locate) { if (await navigation.locate(citation) === false) throw new Error('定位未完成，请重试') }
    else emit('locate', citation)
  } catch (error) { locateError.value = error.message || '定位失败，请重试' }
}
const heading = title => title ? h('h4', title) : null
const container = className => ({ props: p, children }) => h('div', { class: className }, [heading(p.title), children])
const { registry } = defineRegistry(analysisCatalog, { components: {
  Stack: container('analysis-stack'), Grid: container('analysis-grid'),
  MetricCard: ({ props: p }) => h('div', { class: 'analysis-metric' }, [h('small', p.title || labelFor(p.field)), h('strong', displayCell(props.artifact.datasets[p.dataset].rows[0]?.[p.field]))]),
  DataTable: ({ props: p }) => h('section', [heading(p.title), h(AnalysisDataTable, { table: props.artifact.datasets[p.dataset], columns: p.columns, onLocate: locate })]),
  Chart: ({ props: p }) => h('section', [heading(p.title), h(AnalysisChart, { config: p, table: props.artifact.datasets[p.dataset], onLocate: locate })]),
  SourceList: ({ props: p }) => h('section', [heading(p.title || '来源示例'), h('small', props.artifact.provenance.source_note),
    h(AnalysisDataTable, { table: props.artifact.datasets[p.dataset], onLocate: locate })]),
} })
onErrorCaptured(() => { failed.value = true; return false })
</script>
<style>
.agent-analysis-ui{container-type:inline-size;margin:18px 0;padding:16px;border:1px solid #94a3b840;border-radius:12px;min-width:0;color:inherit;background:color-mix(in srgb,currentColor 2%,transparent)}
.agent-analysis-ui header{display:flex;justify-content:space-between;gap:12px}.analysis-provenance{font-size:12px;opacity:.7;margin:8px 0 16px;overflow-wrap:anywhere}
.analysis-stack{display:flex;flex-direction:column;gap:16px}.analysis-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.analysis-grid>h4{grid-column:1/-1}
.agent-analysis-ui section,.analysis-grid>*,.analysis-stack>*{min-width:0}.agent-analysis-ui h4{margin:0 0 10px;font-weight:600;font-size:14px}
.analysis-metric{padding:14px;border:1px solid #94a3b830;border-radius:10px}.analysis-metric small{display:block;opacity:.7}.analysis-metric strong{display:block;font-size:26px;overflow-wrap:anywhere}
.analysis-chart{height:320px;width:100%;min-width:0}.analysis-chart-actions,.analysis-table-pages{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:8px 0;font-size:12px}
.agent-analysis-ui button{cursor:pointer;padding:4px 8px;border:1px solid #94a3b840;border-radius:6px;background:transparent;color:inherit}.agent-analysis-ui button:disabled{opacity:.4;cursor:default}
.agent-analysis-ui button:focus-visible,.agent-analysis-ui input:focus-visible{outline:2px solid #4f7cff;outline-offset:2px}
.analysis-data-table label{display:flex;align-items:center;gap:8px;font-size:12px;margin-bottom:8px}.analysis-data-table input{min-width:0;width:160px;padding:5px 8px;border:1px solid #94a3b840;border-radius:6px;background:transparent;color:inherit}
.analysis-table-scroll{overflow:auto;max-height:400px}.analysis-table-scroll table{width:100%;border-collapse:collapse;font-size:12px}.analysis-table-scroll th,.analysis-table-scroll td{padding:8px;text-align:left;border-bottom:1px solid #94a3b830;white-space:pre-wrap;overflow-wrap:anywhere;min-width:70px;max-width:360px}.analysis-table-scroll th{position:sticky;top:0;background:var(--agent-bg,#fff);color:var(--agent-text,#243047)}
.analysis-fallback{white-space:pre-wrap;max-height:180px;overflow:auto}.analysis-chart-dialog{width:min(1000px,92vw);max-height:90vh;padding:20px;border:1px solid #94a3b8;border-radius:12px;color:#243047;background:#fff}.analysis-chart-dialog::backdrop{background:#0008}.analysis-chart-large{height:65vh}.analysis-chart-dialog header{margin-bottom:12px}
@container (max-width:520px){.analysis-grid{grid-template-columns:minmax(0,1fr)}}
html[data-theme=dark] .analysis-chart-dialog,html[data-theme=dark] .analysis-table-scroll th{background:#222629;color:#e5e7eb}
</style>
