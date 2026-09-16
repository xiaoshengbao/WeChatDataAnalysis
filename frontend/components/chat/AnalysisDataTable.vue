<template>
  <div class="analysis-data-table">
    <p v-if="table.note" class="analysis-provenance">{{ table.note }}</p>
    <label>筛选已有数据 <input v-model="query" type="search" placeholder="输入关键词" /></label>
    <div class="analysis-table-scroll" tabindex="0" aria-label="分析数据表">
      <table><thead><tr><th v-for="field in fields" :key="field" :aria-sort="sort === field ? (descending ? 'descending' : 'ascending') : 'none'">
        <button type="button" @click="order(field)">{{ labelFor(field) }} {{ sort === field ? (descending ? '↓' : '↑') : '' }}</button>
      </th><th v-if="hasSources">依据</th></tr></thead>
      <tbody><tr v-for="(row, i) in pageRows" :key="i"><td v-for="field in fields" :key="field">{{ displayCell(row[field]) }}</td>
        <td v-if="hasSources"><button v-for="source in row.sources || []" :key="source" type="button" @click="$emit('locate', source)">查看来源</button></td>
      </tr></tbody></table>
    </div>
    <div class="analysis-table-pages"><span>{{ filtered.length }} 条{{ query ? '匹配结果' : '数据' }}</span>
      <button type="button" :disabled="page === 0" @click="page--">上一页</button><span>{{ page + 1 }} / {{ pages }}</span>
      <button type="button" :disabled="page + 1 >= pages" @click="page++">下一页</button>
    </div>
  </div>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import { displayCell, labelFor } from '~/utils/analysisUi'
const props = defineProps({ table: { type: Object, required: true }, columns: { type: Array, default: () => [] } })
defineEmits(['locate'])
const query = ref(''), sort = ref(''), descending = ref(false), page = ref(0)
const fields = computed(() => props.columns.length ? props.columns : props.table.columns)
const hasSources = computed(() => props.table.rows.some(r => r.sources?.length))
const filtered = computed(() => {
  const result = props.table.rows.filter(row => fields.value.some(k => displayCell(row[k]).toLocaleLowerCase().includes(query.value.toLocaleLowerCase())))
  if (sort.value) result.sort((a, b) => {
    const x = a[sort.value], y = b[sort.value]
    const value = props.table.numeric?.includes(sort.value) ? Number(x) - Number(y) : displayCell(x).localeCompare(displayCell(y), 'zh-CN', { numeric: true })
    return descending.value ? -value : value
  })
  return result
})
const pages = computed(() => Math.max(1, Math.ceil(filtered.value.length / 30)))
const pageRows = computed(() => filtered.value.slice(page.value * 30, (page.value + 1) * 30))
watch([query, sort, descending, () => props.table], () => { page.value = 0 })
function order(field) { descending.value = sort.value === field ? !descending.value : false; sort.value = field }
</script>
