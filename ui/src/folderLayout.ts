import type { Edge, Node } from '@xyflow/react'
import type { Ontology, OntoNode, Relationship } from './types'
import { RELATION_STYLE } from './types'

/** Structure Map: the same File nodes as the Ontology Map, grouped by literal
 * directory path instead of inferred Feature/Component. Every node here is
 * 100% EXTRACTED - there is no domain-word guessing, so this view is the
 * honest fallback when the Feature grouping looks off. Edges come straight
 * from file_relationships (already computed, schema v2) - no new backend
 * data needed. */

export interface FolderNode {
  id: string
  name: string
  path: string
  children: FolderNode[]
  files: OntoNode[]
}

const CARD_W = 210
const GAP = 36
const BAND_H = 210

export function buildFolderTree(onto: Ontology): FolderNode {
  const root: FolderNode = { id: 'folder_root', name: onto.name, path: '', children: [], files: [] }
  const byPath = new Map<string, FolderNode>([['', root]])

  const dirOf = (path: string): FolderNode => {
    const cached = byPath.get(path)
    if (cached) return cached
    const parts = path.split('/')
    const parent = dirOf(parts.slice(0, -1).join('/'))
    const node: FolderNode = {
      id: `folder_${path}`, name: parts[parts.length - 1], path, children: [], files: [],
    }
    parent.children.push(node)
    byPath.set(path, node)
    return node
  }

  const files: OntoNode[] = []
  const collect = (n: OntoNode) => {
    if (n.type === 'File') files.push(n)
    for (const c of n.children ?? []) collect(c)
  }
  collect(onto)

  for (const f of files) {
    const path = f.path ?? f.name
    const parts = path.split('/')
    dirOf(parts.slice(0, -1).join('/')).files.push(f)
  }
  return root
}

export function folderStats(n: FolderNode): { files: number; loc: number } {
  let files = n.files.length
  let loc = n.files.reduce((a, f) => a + (f.loc ?? 0), 0)
  for (const c of n.children) {
    const s = folderStats(c)
    files += s.files
    loc += s.loc
  }
  return { files, loc }
}

function edgeFor(id: string, source: string, target: string, relation: string, label?: string): Edge {
  const style = RELATION_STYLE[relation] ?? { color: '#64748b', dash: '4 4' }
  return {
    id, source, target, type: 'smoothstep',
    label: label ?? relation.replace(/_/g, ' '),
    style: { stroke: style.color, strokeWidth: 1.4, strokeDasharray: style.dash },
    labelStyle: { fill: '#cbd5e1', fontSize: 10 },
    labelBgStyle: { fill: '#0b1020', fillOpacity: 0.85 },
    labelBgPadding: [4, 2] as [number, number],
  }
}

interface LayoutResult { nodes: Node[]; edges: Edge[] }

/** Per-file "+" on a file chip drills one level past File to its symbols
 * (functions/methods/classes - already in schema v2, no new data needed).
 * Simplification: symbol-stack width isn't fed back into subtreeWidth, so a
 * heavily-expanded stack can visually overlap a sibling folder - acceptable
 * since expanding several files at once in the same stack is rare, and
 * panning/zoom still make every node reachable. */
function placeExpandedSymbols(
  files: OntoNode[], startX: number, depth: number, expanded: Set<string>,
  nodes: Node[], edges: Edge[], stackId: string,
): void {
  let x = startX
  for (const f of files) {
    if (!expanded.has(f.id) || !f.symbols?.length) continue
    const symId = `${f.id}__symbols`
    nodes.push({
      id: symId, type: 'symbolstack',
      position: { x, y: depth * BAND_H },
      data: { symbols: f.symbols, fileName: f.name, ownerId: f.id },
      className: 'onto-node',
    })
    edges.push(edgeFor(`h_${f.id}_symbols`, stackId, symId, 'contains', f.name))
    x += CARD_W + GAP
  }
}

/** Collapsed folders cost a flat card width regardless of what's inside -
 * keeps the width computation cheap and bounded no matter how deep the real
 * tree goes. */
function subtreeWidth(n: FolderNode, expanded: Set<string>): number {
  if (!expanded.has(n.id)) return CARD_W
  const parts = n.children.map((c) => subtreeWidth(c, expanded))
  if (n.files.length > 0) parts.push(CARD_W)
  if (parts.length === 0) return CARD_W
  return Math.max(CARD_W, parts.reduce((a, b) => a + b, 0) + (parts.length - 1) * GAP)
}

function place(
  n: FolderNode, x: number, depth: number, expanded: Set<string>,
  nodes: Node[], edges: Edge[], parentId: string, impactClass: (id: string) => string,
): void {
  const w = subtreeWidth(n, expanded)
  const isExpanded = expanded.has(n.id)
  const expandable = n.children.length > 0 || n.files.length > 0
  nodes.push({
    id: n.id, type: 'folder',
    position: { x: x + w / 2 - CARD_W / 2, y: depth * BAND_H },
    data: { node: n, stats: folderStats(n), expanded: isExpanded, expandable },
    className: 'onto-node' + impactClass(n.id),
  })
  edges.push(edgeFor(`h_${parentId}_${n.id}`, parentId, n.id, 'contains', 'owns / contains'))

  if (!isExpanded) return
  let cursor = x
  for (const c of n.children) {
    const cw = subtreeWidth(c, expanded)
    place(c, cursor, depth + 1, expanded, nodes, edges, n.id, impactClass)
    cursor += cw + GAP
  }
  if (n.files.length > 0) {
    const stackId = `${n.id}__files`
    nodes.push({
      id: stackId, type: 'filestack',
      position: { x: cursor + CARD_W / 2 - CARD_W / 2, y: (depth + 1) * BAND_H },
      data: { files: n.files, ownerId: n.id },
      className: 'onto-node' + impactClass(n.id),
    })
    edges.push(edgeFor(`h_${n.id}_files`, n.id, stackId, 'contains'))
    placeExpandedSymbols(n.files, cursor, depth + 2, expanded, nodes, edges, stackId)
  }
}

export function layoutFolderMap(
  onto: Ontology, root: FolderNode, expanded: Set<string>, showRelationships: boolean,
  impactSets: { direct: Set<string>; indirect: Set<string>; origin: string | null },
): LayoutResult {
  const nodes: Node[] = []
  const edges: Edge[] = []
  const impactClass = (id: string): string => {
    if (!impactSets.origin) return ''
    if (id === impactSets.origin) return ' impact-origin'
    if (impactSets.direct.has(id)) return ' impact-direct'
    if (impactSets.indirect.has(id)) return ' impact-indirect'
    return ' impact-dim'
  }

  // top-level folders are always visible (like the Feature band); deeper
  // levels need an explicit expand, same interaction as Component -> Module
  const topWidths = root.children.map((c) => subtreeWidth(c, expanded))
  const rootFilesW = root.files.length > 0 ? CARD_W : 0
  const totalW = topWidths.reduce((a, b) => a + b, 0)
    + (rootFilesW ? rootFilesW + GAP : 0)
    + Math.max(0, root.children.length - 1) * GAP

  nodes.push({
    id: onto.id, type: 'product',
    position: { x: totalW / 2 - 400 / 2, y: 0 },
    data: { node: onto },
    className: 'onto-node' + impactClass(onto.id),
  })

  let cursor = 0
  for (const c of root.children) {
    place(c, cursor, 1, expanded, nodes, edges, onto.id, impactClass)
    cursor += subtreeWidth(c, expanded) + GAP
  }
  if (root.files.length > 0) {
    const rootStackId = `${root.id}__files`
    nodes.push({
      id: rootStackId, type: 'filestack',
      position: { x: cursor, y: BAND_H },
      data: { files: root.files, ownerId: root.id },
      className: 'onto-node' + impactClass(root.id),
    })
    edges.push(edgeFor(`h_${onto.id}_rootfiles`, onto.id, rootStackId, 'contains'))
    placeExpandedSymbols(root.files, cursor, 2, expanded, nodes, edges, rootStackId)
  }

  if (showRelationships) {
    const stackOf = new Map<string, string>()
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
        edges.push(edgeFor(`f_${key}`, s, t, relation, label))
      }
    }
  }

  return { nodes, edges }
}

/** Every folder id in the tree that has something to expand - for "Expand all". */
export function allFolderIds(root: FolderNode): string[] {
  const out: string[] = []
  const walk = (n: FolderNode) => {
    if (n.children.length > 0 || n.files.length > 0) out.push(n.id)
    for (const c of n.children) walk(c)
  }
  for (const c of root.children) walk(c)
  return out
}
