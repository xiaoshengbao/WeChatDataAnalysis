<template>
  <div class="analysis-chart-card">
    <p v-if="table.note" class="analysis-provenance">{{ table.note }}</p>
    <div class="analysis-chart-actions"><button type="button" @click="expand">展开图表</button><button type="button" @click="showData = !showData">{{ showData ? '收起数据' : '查看数据' }}</button></div>
    <p v-if="failed" role="status">图表暂不可用，以下为原始数据。</p>
    <VChart v-else class="analysis-chart" :option="option" :theme="theme" :init-options="{ renderer: 'svg' }" autoresize />
    <AnalysisDataTable v-if="showData || failed" :table="table" @locate="$emit('locate', $event)" />
    <dialog ref="dialog" class="analysis-chart-dialog" :aria-label="config.title || '展开图表'" @click="closeOutside">
      <header><strong>{{ config.title || '图表' }}</strong><button type="button" @click="dialog.close()">关闭</button></header>
      <VChart v-if="expanded && !failed" class="analysis-chart analysis-chart-large" :option="option" :theme="theme" :init-options="{ renderer: 'svg' }" autoresize />
    </dialog>
  </div>
</template>
<script setup>
import { computed, ref, onErrorCaptured, onMounted, onBeforeUnmount } from 'vue'
import { use } from 'echarts/core'
import { SVGRenderer } from 'echarts/renderers'
import { LineChart, BarChart, PieChart, HeatmapChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, VisualMapComponent, AriaComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import AnalysisDataTable from './AnalysisDataTable.vue'
import { chartOption } from '~/utils/analysisUi'
use([SVGRenderer, LineChart, BarChart, PieChart, HeatmapChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, VisualMapComponent, AriaComponent])
const props = defineProps({ config: { type: Object, required: true }, table: { type: Object, required: true } })
defineEmits(['locate'])
const failed = ref(false), showData = ref(false), dialog = ref(null), expanded = ref(false), theme = ref('')
const option = computed(() => ({ ...chartOption(props.config, props.table), backgroundColor: 'transparent' }))
onErrorCaptured(() => { failed.value = true; return false })
let themeObserver
onMounted(() => {
  dialog.value?.addEventListener('close', () => { expanded.value = false })
  const syncTheme = () => { theme.value = document.documentElement.dataset.theme === 'dark' ? 'dark' : '' }
  syncTheme()
  themeObserver = new MutationObserver(syncTheme)
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
})
onBeforeUnmount(() => themeObserver?.disconnect())
function expand() { expanded.value = true; dialog.value.showModal() }
function closeOutside(event) { if (event.target === dialog.value) dialog.value.close() }
</script>
