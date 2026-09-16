// Pure display transformations: no eval, HTML, network access or model callbacks.
export const columnLabels = { day: '日期', count: '消息数', sender: '成员', sender_id: '成员标识',
  total_messages: '消息总数', active_senders: '发言人数', text: '内容', event_time: '事件时间',
  evidence_status: '证据状态', event_key: '事件', value: '数值', event_count: '事件数', operation: '计算方式' }
export const labelFor = field => columnLabels[field] || field
export function displayCell(value) {
  if (value == null) return ''
  return String(value)
}
export function artifactText(artifact) {
  if (!artifact) return '[分析界面暂不可用]'
  const p = artifact.provenance || {}, offset = p.timezone_offset || 0
  const range = Number.isFinite(p.start) && Number.isFinite(p.end)
    ? `${new Date((p.start + offset) * 1000).toISOString()} 至 ${new Date((p.end + offset) * 1000).toISOString()}（不含结束时刻，显示时间为 UTC${offset >= 0 ? '+' : ''}${offset / 3600}）`.replaceAll('Z', '') : ''
  const lines = [artifact.title, range, p.scope_summary, p.coverage, p.basis]
  const emitted = new Set()
  const visit = key => {
    const node = artifact.spec?.elements?.[key]
    if (!node || emitted.has(key)) return
    emitted.add(key)
    const p = node.props || {}, table = artifact.datasets?.[p.dataset]
    if (p.title) lines.push(p.title)
    if (table) {
      if (table.note) lines.push(table.note)
      if (node.type === 'MetricCard') lines.push(`${labelFor(p.field)}：${displayCell(table.rows[0]?.[p.field])}`)
      else {
        const columns = p.columns?.length ? p.columns : table.columns
        lines.push(columns.map(labelFor).join('\t'))
        for (const row of table.rows) lines.push(columns.map(k => displayCell(row[k])).join('\t'))
      }
    }
    for (const child of node.children || []) visit(child)
  }
  visit(artifact.spec?.root)
  return lines.filter(Boolean).join('\n')
}

export function chartOption(p, table) {
  const rows = table.rows
  const categories = field => [...new Set(rows.map(row => row[field]))]
  const caption = (field, value) => field === 'sender_id'
    ? `${rows.find(r => r.sender_id === value)?.sender || '未命名'} (${value})` : displayCell(value)
  const base = { animation: false, tooltip: { trigger: 'item', renderMode: 'richText' },
    aria: { enabled: true }, color: ['#4f7cff', '#19a887', '#ed9d35', '#9467d8', '#d95c75'],
    grid: { top: 40, right: 20, bottom: 70, left: 12, containLabel: true } }
  if (p.chart_type === 'pie') return { ...base, series: [{ type: 'pie', radius: ['25%', '65%'],
    data: rows.map(row => ({ name: caption(p.x, row[p.x]), value: Number(row[p.y]) })) }] }
  const xs = categories(p.x)
  if (p.chart_type === 'heatmap') {
    const ys = categories(p.y)
    return { ...base,
      xAxis: { type: 'category', data: xs.map(v => caption(p.x, v)) },
      yAxis: { type: 'category', data: ys.map(v => caption(p.y, v)) },
      visualMap: { min: Math.min(0, ...rows.map(r => Number(r[p.value]))), max: Math.max(1, ...rows.map(r => Number(r[p.value]))), orient: 'horizontal', bottom: 0, left: 'center', calculable: true },
      series: [{ type: 'heatmap', data: rows.map(r => [xs.indexOf(r[p.x]), ys.indexOf(r[p.y]), Number(r[p.value])]) }] }
  }
  const groups = p.series ? categories(p.series) : [null]
  return { ...base, tooltip: { ...base.tooltip, trigger: 'axis' }, legend: { type: 'scroll', top: 0 },
    xAxis: { type: 'category', data: xs.map(v => caption(p.x, v)) }, yAxis: { type: 'value', name: labelFor(p.y) },
    dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 6 }],
    series: groups.map(group => {
      const values = new Map(rows.filter(r => group === null || r[p.series] === group).map(r => [r[p.x], Number(r[p.y])]))
      return { type: p.chart_type, name: group === null ? labelFor(p.y) : caption(p.series, group),
        data: xs.map(x => values.has(x) ? values.get(x) : null), connectNulls: false }
    }) }
}
