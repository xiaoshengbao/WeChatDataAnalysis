import { defineCatalog } from '@json-render/core'
import { schema } from '@json-render/vue/schema'
import { z } from 'zod'

const title = z.string().max(120), field = z.string().max(40)
const componentProps = {
  Stack: z.object({ title }).strict(), Grid: z.object({ title }).strict(),
  MetricCard: z.object({ title, dataset: field, field }).strict(),
  DataTable: z.object({ title, dataset: field, columns: z.array(field).max(12) }).strict(),
  Chart: z.object({ title, dataset: field, chart_type: z.enum(['line', 'bar', 'pie', 'heatmap']), x: field, y: field, value: field, series: field }).strict(),
  SourceList: z.object({ title, dataset: field }).strict(),
}
export const analysisCatalog = defineCatalog(schema, { components: Object.fromEntries(Object.entries(componentProps)
  .map(([name, props]) => [name, { props, description: name, slots: ['Stack', 'Grid'].includes(name) ? ['default'] : [] }])), actions: {} })

// Validate again at the renderer boundary, including restored history. Never pass
// json-render expressions, actions, repeats, bindings or arbitrary props through.
export function checkedSpec(artifact) {
  if (artifact.schema_version !== 1) throw new Error('不支持的分析界面版本')
  const spec = artifact.spec
  if (!spec || Object.keys(spec).some(k => !['root', 'elements'].includes(k)) || Object.keys(spec.elements || {}).length > 40) throw new Error('无效的界面规格')
  const seen = new Set()
  function visit(key, depth) {
    if (depth > 6 || seen.has(key)) throw new Error('无效的组件层级')
    seen.add(key)
    const node = spec.elements[key]
    if (!node || Object.keys(node).some(k => !['type', 'props', 'children'].includes(k)) || !componentProps[node.type]) throw new Error('未知组件')
    componentProps[node.type].parse(node.props)
    if (!Array.isArray(node.children) || (!['Stack', 'Grid'].includes(node.type) && node.children.length)) throw new Error('无效子节点')
    if (node.props.dataset) {
      const data = artifact.datasets?.[node.props.dataset]
      if (!data || !Array.isArray(data.rows) || data.rows.length > 10000 || !Array.isArray(data.columns)) throw new Error('数据快照不可用')
    }
    for (const child of node.children) visit(child, depth + 1)
  }
  visit(spec.root, 1)
  if (seen.size !== Object.keys(spec.elements).length) throw new Error('存在未连接组件')
  return spec
}
