import type { Edge, Node } from '@xyflow/react'
import type { Ontology, OntoNode, Relationship } from './types'
import { RELATION_STYLE } from './types'

/** Banded hierarchical layout: level = fixed Y band, X from tidy-tree widths.
 * The hierarchy is a tree, so no general graph layout library is needed. */

const CARD_W: Record<string, number> = {
  product: 400, feature: 210, component: 220, module: 210, filestack: 210,
  symbolstack: 210, external: 190,
}
const GAP = 36
const BAND_Y = {
  product: 0, feature: 210, component: 430, module: 660, filestack: 855,
  symbolstack: 1080, external: 1350,
}

/** Per-file "+" drills one level past File to its symbols (functions/methods/
 * classes - already in schema v2, no new data needed). Shared by both the
 * Feature and Folder layouts since FileStackNode itself is shared. */
function placeExpandedSymbols(
  files: OntoNode[], startX: number, y: number, expanded: Set<string>,
  nodes: Node[], edges: Edge[], stackId: string,
): void {
  let x = startX
  for (const f of files) {
    if (!expanded.has(f.id) || !f.symbols?.length) continue
    const symId = `${f.id}__symbols`
    nodes.push({
      id: symId, type: 'symbolstack',
      position: { x, y },
      data: { symbols: f.symbols, fileName: f.name, ownerId: f.id },
      className: 'onto-node',
    })
    edges.push(edgeFor(`h_${f.id}_symbols`, stackId, symId, 'contains', { label: f.name }))
    x += CARD_W.symbolstack + GAP
  }
}

export interface LayoutResult {
  nodes: Node[]
  edges: Edge[]
}

interface VisComponent {
  node: OntoNode
  featureId: string
  expanded: boolean
  modules: OntoNode[]      // Module children (possibly empty)
  directFiles: OntoNode[]  // File children directly on the component
}

function edgeFor(
  id: string, source: string, target: string, relation: string,
  opts: { animated?: boolean; label?: string } = {},
): Edge {
  const style = RELATION_STYLE[relation] ?? { color: '#64748b', dash: '4 4' }
  return {
    id, source, target,
    type: 'smoothstep',
    label: opts.label ?? relation.replace(/_/g, ' '),
    animated: opts.animated ?? false,
    style: { stroke: style.color, strokeWidth: 1.4, strokeDasharray: style.dash },
    labelStyle: { fill: '#cbd5e1', fontSize: 10 },
    labelBgStyle: { fill: '#0b1020', fillOpacity: 0.85 },
    labelBgPadding: [4, 2] as [number, number],
  }
}

export function layoutOntology(
  onto: Ontology,
  expanded: Set<string>,
  maxLevel: number,
  showRelationships: boolean,
  impactSets: { direct: Set<string>; indirect: Set<string>; origin: string | null },
): LayoutResult {
  const nodes: Node[] = []
  const edges: Edge[] = []

  const features = (onto.children ?? []).filter((f) => f.type === 'Feature')

  // ---- collect visible components with their expansion state ----
  const comps: VisComponent[] = []
  for (const f of features) {
    for (const c of f.children ?? []) {
      if (c.type !== 'Component') continue
      const modules = (c.children ?? []).filter((ch) => ch.type === 'Module')
      const directFiles = (c.children ?? []).filter((ch) => ch.type === 'File')
      comps.push({
        node: c, featureId: f.id,
        expanded: maxLevel >= 4 && expanded.has(c.id),
        modules, directFiles,
      })
    }
  }

  // ---- width computation (bottom-up) ----
  // an expanded component spans its module columns; a collapsed one is a card
  const compWidth = (vc: VisComponent): number => {
    if (!vc.expanded) return CARD_W.component
    const cols = vc.modules.length > 0 ? vc.modules.length : 1
    return Math.max(CARD_W.component, cols * CARD_W.module + (cols - 1) * GAP)
  }
  const featureWidth = (f: OntoNode): number => {
    const mine = comps.filter((vc) => vc.featureId === f.id)
    if (mine.length === 0 || maxLevel < 3) return CARD_W.feature
    const w = mine.reduce((acc, vc) => acc + compWidth(vc), 0) + (mine.length - 1) * GAP
    return Math.max(CARD_W.feature, w)
  }

  // ---- place features left-to-right ----
  const impactClass = (id: string): string => {
    if (!impactSets.origin) return ''
    if (id === impactSets.origin) return ' impact-origin'
    if (impactSets.direct.has(id)) return ' impact-direct'
    if (impactSets.indirect.has(id)) return ' impact-indirect'
    return ' impact-dim'
  }

  let cursor = 0
  const totalW = features.reduce((a, f) => a + featureWidth(f), 0) + (features.length - 1) * GAP

  nodes.push({
    id: onto.id,
    type: 'product',
    position: { x: totalW / 2 - CARD_W.product / 2, y: BAND_Y.product },
    data: { node: onto },
    className: 'onto-node' + impactClass(onto.id),
  })

  for (const f of features) {
    const fw = featureWidth(f)
    const fx = cursor + fw / 2 - CARD_W.feature / 2
    if (maxLevel >= 2) {
      nodes.push({
        id: f.id, type: 'feature',
        position: { x: fx, y: BAND_Y.feature },
        data: { node: f },
        className: 'onto-node' + impactClass(f.id),
      })
      edges.push(edgeFor(`h_${onto.id}_${f.id}`, onto.id, f.id, 'contains', { label: 'owns / contains' }))
    }

    // components inside this feature
    if (maxLevel >= 3) {
      let cCursor = cursor
      for (const vc of comps.filter((v) => v.featureId === f.id)) {
        const cw = compWidth(vc)
        const cx = cCursor + cw / 2 - CARD_W.component / 2
        nodes.push({
          id: vc.node.id, type: 'component',
          position: { x: cx, y: BAND_Y.component },
          data: { node: vc.node, expanded: vc.expanded, expandable: maxLevel >= 4 },
          className: 'onto-node' + impactClass(vc.node.id),
        })
        edges.push(edgeFor(`h_${f.id}_${vc.node.id}`, f.id, vc.node.id, 'contains', { label: 'owns / contains' }))

        if (vc.expanded) {
          if (vc.modules.length > 0) {
            let mCursor = cCursor
            for (const m of vc.modules) {
              const mx = mCursor + CARD_W.module / 2 - CARD_W.module / 2
              nodes.push({
                id: m.id, type: 'module',
                position: { x: mx, y: BAND_Y.module },
                data: { node: m },
                className: 'onto-node' + impactClass(vc.node.id),
              })
              edges.push(edgeFor(`h_${vc.node.id}_${m.id}`, vc.node.id, m.id, 'contains'))
              if (maxLevel >= 5) {
                const files = (m.children ?? []).filter((ch) => ch.type === 'File')
                const stackId = `${m.id}__files`
                nodes.push({
                  id: stackId, type: 'filestack',
                  position: { x: mx, y: BAND_Y.filestack },
                  data: { files, ownerId: m.id },
                  className: 'onto-node' + impactClass(vc.node.id),
                })
                edges.push(edgeFor(`h_${m.id}_files`, m.id, stackId, 'contains'))
                placeExpandedSymbols(files, mx, BAND_Y.symbolstack, expanded, nodes, edges, stackId)
              }
              mCursor += CARD_W.module + GAP
            }
          } else if (maxLevel >= 5 && vc.directFiles.length > 0) {
            const stackId = `${vc.node.id}__files`
            nodes.push({
              id: stackId, type: 'filestack',
              position: { x: cx, y: BAND_Y.module },
              data: { files: vc.directFiles, ownerId: vc.node.id },
              className: 'onto-node' + impactClass(vc.node.id),
            })
            edges.push(edgeFor(`h_${vc.node.id}_files`, vc.node.id, stackId, 'contains'))
            placeExpandedSymbols(vc.directFiles, cx, BAND_Y.filestack, expanded, nodes, edges, stackId)
          }
        }
        cCursor += cw + GAP
      }
    }
    cursor += fw + GAP
  }

  // ---- external band ----
  const componentVisible = new Set(comps.map((c) => c.node.id))
  if (maxLevel >= 6 && onto.external.length > 0) {
    const exts = onto.external
    const rowW = exts.length * CARD_W.external + (exts.length - 1) * GAP
    let ex = totalW / 2 - rowW / 2
    for (const e of exts) {
      nodes.push({
        id: e.id, type: 'external',
        position: { x: ex, y: BAND_Y.external },
        data: { ext: e },
        className: 'onto-node' + impactClass(e.id),
      })
      ex += CARD_W.external + GAP
    }
  }

  // ---- cross-component + external relationship edges ----
  if (showRelationships) {
    const nodeIds = new Set(nodes.map((n) => n.id))
    const seen = new Set<string>()
    for (const r of onto.component_relationships as Relationship[]) {
      if (!nodeIds.has(r.source) || !nodeIds.has(r.target)) continue
      if (r.relation === 'contains') continue
      const key = `${r.source}|${r.target}|${r.relation}`
      if (seen.has(key)) continue
      seen.add(key)
      const label = r.count && r.count > 1 ? `${r.relation.replace(/_/g, ' ')} ×${r.count}` : undefined
      edges.push(edgeFor(`r_${key}`, r.source, r.target, r.relation, { label }))
    }

    // file-level edges (v2), aggregated to the visible file stacks that hold them
    const stackOf = new Map<string, string>()  // file id -> filestack node id
    for (const n of nodes) {
      if (n.type !== 'filestack') continue
      for (const f of (n.data as { files: OntoNode[] }).files) stackOf.set(f.id, n.id)
    }
    if (stackOf.size > 0) {
      const agg = new Map<string, { relation: string; count: number }>()
      for (const r of (onto.file_relationships ?? []) as Relationship[]) {
        const s = stackOf.get(r.source), t = stackOf.get(r.target)
        if (!s || !t || s === t) continue
        const key = `${s}|${t}|${r.relation}`
        const e = agg.get(key)
        if (e) e.count += r.count ?? 1
        else agg.set(key, { relation: r.relation, count: r.count ?? 1 })
      }
      for (const [key, { relation, count }] of agg) {
        const [s, t] = key.split('|')
        const label = count > 1 ? `${relation.replace(/_/g, ' ')} ×${count}` : undefined
        edges.push(edgeFor(`f_${key}`, s, t, relation, { label }))
      }
    }
  }
  void componentVisible

  return { nodes, edges }
}
