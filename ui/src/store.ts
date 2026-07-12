import { create } from 'zustand'
import type { Hotspots, IndexEntry, Ontology, OntoNode, Relationship } from './types'

/** Reverse BFS over file_relationships: which files break if this one changes? */
export function fileBlastRadius(frs: Relationship[], fileId: string) {
  const dependents = new Map<string, string[]>()
  for (const r of frs) {
    if (!dependents.has(r.target)) dependents.set(r.target, [])
    dependents.get(r.target)!.push(r.source)
  }
  const direct = new Set(dependents.get(fileId) ?? [])
  const seen = new Set([fileId, ...direct])
  const indirect = new Set<string>()
  let frontier = [...direct]
  while (frontier.length) {
    const next: string[] = []
    for (const f of frontier)
      for (const dep of dependents.get(f) ?? [])
        if (!seen.has(dep)) { seen.add(dep); indirect.add(dep); next.push(dep) }
    frontier = next
  }
  return { direct: [...direct], indirect: [...indirect] }
}

interface OntoState {
  ontology: Ontology | null
  index: Map<string, IndexEntry>
  selectedId: string | null
  expanded: Set<string>       // component ids expanded to module/file level
  maxLevel: number            // 3=components, 4=modules, 5=files, 6=external
  showRelationships: boolean
  showImpact: boolean
  searchOpen: boolean
  view: 'map' | 'graph'       // ontology map vs graphify's raw code graph
  // ⌘K "A -> B": ordered node ids + the edge keys (src|tgt|relation) on the path
  pathResult: { nodes: string[]; edgeKeys: string[] } | null
  graphFocus: string | null   // node label to focus when opening the Code Graph tab
  hotspots: Hotspots | null   // optional git-history overlay
  showHotspots: boolean
  panelTab: 'overview' | 'properties' | 'dependencies' | 'impact'

  load: (o: Ontology) => void
  select: (id: string | null) => void
  toggleExpand: (id: string) => void
  expandAll: () => void
  collapseAll: () => void
  setMaxLevel: (n: number) => void
  setShowRelationships: (b: boolean) => void
  setShowImpact: (b: boolean) => void
  setSearchOpen: (b: boolean) => void
  setView: (v: OntoState['view']) => void
  setPanelTab: (t: OntoState['panelTab']) => void
  setPathResult: (p: OntoState['pathResult']) => void
  openInGraph: (label: string | null) => void
  setHotspots: (h: Hotspots | null) => void
  setShowHotspots: (b: boolean) => void
}

function buildIndex(root: Ontology): Map<string, IndexEntry> {
  const index = new Map<string, IndexEntry>()
  const walk = (
    node: OntoNode, parent: string | null,
    featureId: string | null, componentId: string | null,
  ) => {
    const fid = node.type === 'Feature' ? node.id : featureId
    const cid = node.type === 'Component' ? node.id : componentId
    index.set(node.id, { node, parent, featureId: fid, componentId: cid })
    for (const c of node.children ?? []) walk(c, node.id, fid, cid)
  }
  walk(root, null, null, null)
  for (const e of root.external) {
    index.set(e.id, {
      node: { id: e.id, type: 'External', name: e.name } as OntoNode,
      parent: root.id, featureId: null, componentId: null,
    })
  }
  return index
}

export const useOnto = create<OntoState>()((set, get) => ({
  ontology: null,
  index: new Map(),
  selectedId: null,
  expanded: new Set(),
  maxLevel: 6,
  showRelationships: true,
  showImpact: false,
  searchOpen: false,
  view: 'map',
  pathResult: null,
  graphFocus: null,
  hotspots: null,
  showHotspots: false,
  panelTab: 'overview',

  load: (o) => set({ ontology: o, index: buildIndex(o) }),
  select: (id) => set({ selectedId: id, panelTab: 'overview' }),
  toggleExpand: (id) => {
    const expanded = new Set(get().expanded)
    if (expanded.has(id)) expanded.delete(id)
    else expanded.add(id)
    set({ expanded })
  },
  expandAll: () => {
    const { ontology } = get()
    if (!ontology) return
    const all = new Set<string>()
    for (const f of ontology.children ?? [])
      for (const c of f.children ?? []) all.add(c.id)
    set({ expanded: all })
  },
  collapseAll: () => set({ expanded: new Set() }),
  setMaxLevel: (n) => set({ maxLevel: n }),
  setShowRelationships: (b) => set({ showRelationships: b }),
  setShowImpact: (b) => set({ showImpact: b }),
  setSearchOpen: (b) => set({ searchOpen: b }),
  setView: (v) => set({ view: v }),
  setPanelTab: (t) => set({ panelTab: t }),
  setPathResult: (p) => set({ pathResult: p }),
  openInGraph: (label) => set({ graphFocus: label, view: 'graph' }),
  setHotspots: (h) => set({ hotspots: h }),
  setShowHotspots: (b) => set({ showHotspots: b }),
}))
