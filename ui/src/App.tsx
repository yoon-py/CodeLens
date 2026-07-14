import { useEffect, useMemo, useState } from 'react'
import {
  Background, MiniMap, ReactFlow, ReactFlowProvider, useReactFlow,
} from '@xyflow/react'
import type { NodeMouseHandler } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { layoutOntology } from './layout'
import { nodeTypes } from './nodes'
import { fileBlastRadius, useOnto } from './store'
import { Sidebar } from './Sidebar'
import { DetailPanel } from './DetailPanel'
import { Toolbar } from './Toolbar'
import { Search } from './Search'
import type { Hotspots, Ontology } from './types'

const MINIMAP_COLOR: Record<string, string> = {
  product: '#8b5cf6', feature: '#d946ef', component: '#22d3ee',
  module: '#34d399', filestack: '#f59e0b', external: '#f87171',
}

function Canvas() {
  const onto = useOnto((s) => s.ontology)
  const expanded = useOnto((s) => s.expanded)
  const maxLevel = useOnto((s) => s.maxLevel)
  const showRelationships = useOnto((s) => s.showRelationships)
  const showImpact = useOnto((s) => s.showImpact)
  const selectedId = useOnto((s) => s.selectedId)
  const select = useOnto((s) => s.select)
  const pathResult = useOnto((s) => s.pathResult)
  const setPathResult = useOnto((s) => s.setPathResult)
  const hotspots = useOnto((s) => s.hotspots)
  const showHotspots = useOnto((s) => s.showHotspots)
  const rf = useReactFlow()
  const { fitView } = rf

  const index = useOnto((s) => s.index)

  const impactSets = useMemo(() => {
    const empty = { direct: new Set<string>(), indirect: new Set<string>(), origin: null as string | null }
    if (!onto || !showImpact || !selectedId) return empty
    if (onto.impact[selectedId]) {
      const imp = onto.impact[selectedId]
      return { direct: new Set(imp.direct), indirect: new Set(imp.indirect), origin: selectedId }
    }
    // File selected: blast radius over file_relationships, rolled up to the
    // owning components (that is the granularity the canvas can light up)
    const entry = index.get(selectedId)
    if (entry?.node.type === 'File' && entry.componentId) {
      const blast = fileBlastRadius(onto.file_relationships ?? [], selectedId)
      const compOf = (fid: string) => index.get(fid)?.componentId
      const direct = new Set(blast.direct.map(compOf).filter((c): c is string => !!c && c !== entry.componentId))
      const indirect = new Set(blast.indirect.map(compOf).filter(
        (c): c is string => !!c && c !== entry.componentId && !direct.has(c)))
      return { direct, indirect, origin: entry.componentId }
    }
    return empty
  }, [onto, showImpact, selectedId, index])

  const { nodes, edges } = useMemo(() => {
    if (!onto) return { nodes: [], edges: [] }
    const res = layoutOntology(onto, expanded, maxLevel, showRelationships, impactSets)
    const pathNodes = pathResult ? new Set(pathResult.nodes) : null
    const pathEdges = pathResult ? new Set(pathResult.edgeKeys.map((k) => `r_${k}`)) : null
    // churn -> heat bucket 1..4, normalized against the hottest component
    const maxChurn = showHotspots && hotspots
      ? Math.max(1, ...Object.values(hotspots.components)) : 0
    const heatOf = (id: string): string => {
      if (!maxChurn || !hotspots) return ''
      const c = hotspots.components[id]
      if (!c) return ''
      return ` heat-${Math.min(4, Math.max(1, Math.ceil((c / maxChurn) * 4)))}`
    }
    return {
      nodes: res.nodes.map((n) => ({
        ...n,
        className: (n.className ?? '')
          + (n.id === selectedId ? ' selected' : '')
          + (pathNodes ? (pathNodes.has(n.id) ? ' path-node' : ' impact-dim') : '')
          + heatOf(n.id),
      })),
      edges: res.edges.map((e) => (
        pathEdges?.has(e.id)
          ? { ...e, animated: true, className: 'path-edge',
              style: { ...e.style, strokeWidth: 3, stroke: '#22d3ee' } }
          : e
      )),
    }
  }, [onto, expanded, maxLevel, showRelationships, impactSets, selectedId, pathResult, hotspots, showHotspots])

  // refit when the visible structure changes materially
  useEffect(() => {
    const t = setTimeout(() => fitView({ padding: 0.15, duration: 250 }), 60)
    return () => clearTimeout(t)
  }, [maxLevel, onto, fitView])

  // smooth-pan to the selected node (relationship/impact clicks jump the map to it);
  // files live inside a filestack node, so fall back to the stack that holds them
  useEffect(() => {
    if (!selectedId) return
    const t = setTimeout(() => {
      const n = rf.getNode(selectedId) ?? rf.getNodes().find(
        (c) => c.type === 'filestack' &&
          (c.data as { files?: { id: string }[] }).files?.some((f) => f.id === selectedId),
      )
      if (n) rf.setCenter(n.position.x + 110, n.position.y + 45, { zoom: rf.getZoom(), duration: 400 })
    }, 40)
    return () => clearTimeout(t)
  }, [selectedId, rf])

  const onNodeClick: NodeMouseHandler = (_, node) => {
    if (node.type === 'filestack') {
      const owner = (node.data as { ownerId?: string }).ownerId
      select(owner ?? node.id)
      return
    }
    select(node.id)
  }

  if (!onto) return <div className="loading">loading ontology…</div>

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodeClick={onNodeClick}
      onPaneClick={() => { select(null); setPathResult(null) }}
      minZoom={0.08}
      fitView
      proOptions={{ hideAttribution: true }}
      nodesConnectable={false}
    >
      <Background color="#1e293b" gap={28} />
      <MiniMap pannable zoomable className="minimap" nodeStrokeWidth={0}
               nodeColor={(n) => MINIMAP_COLOR[n.type ?? ''] ?? '#64748b'} />
    </ReactFlow>
  )
}

type CodeGraphInfo = { type: 'iframe' | 'external' | 'none'; url?: string; error?: string }

function CodeGraphPane() {
  const graphFocus = useOnto((s) => s.graphFocus)
  const [info, setInfo] = useState<CodeGraphInfo | null>(null)

  useEffect(() => {
    fetch('/api/code-graph').then((r) => r.json()).then(setInfo).catch(() => setInfo({ type: 'none' }))
  }, [])

  if (!info) return <div className="graph-frame-status">loading…</div>

  if (info.type === 'iframe') {
    // graphify's self-contained interactive graph, served by `lensme serve`;
    // ?q= is handled by a loader the server injects (focuses the matching node)
    const src = info.url + (graphFocus ? `?q=${encodeURIComponent(graphFocus)}` : '')
    return <iframe className="graph-frame" src={src} title="Code Graph" />
  }

  if (info.type === 'external') {
    // codebase-memory-mcp's 3D graph UI: a separate live server whose CSP
    // (frame-ancestors 'none') blocks iframing, so it opens in a new tab
    return (
      <div className="graph-frame-status">
        <p>3D code graph (codebase-memory-mcp)</p>
        <a href={info.url} target="_blank" rel="noreferrer" className="graph-frame-link">
          Open in new tab ↗
        </a>
      </div>
    )
  }

  return (
    <div className="graph-frame-status">
      no code graph available{info.error ? ` — ${info.error}` : ' - run graphify export, or lensme cbm, first'}
    </div>
  )
}

function CanvasOrGraph() {
  const view = useOnto((s) => s.view)
  if (view === 'graph') return <CodeGraphPane />
  return <Canvas />
}

export default function App() {
  const load = useOnto((s) => s.load)
  const setHotspots = useOnto((s) => s.setHotspots)
  const built = useOnto((s) => s.ontology?.meta.built_at)

  useEffect(() => {
    let stop = false
    const fetchOnto = async () => {
      try {
        const res = await fetch('/ontology.json', { cache: 'no-store' })
        if (!res.ok) return
        const data: Ontology = await res.json()
        if (!stop && data.meta?.built_at !== useOnto.getState().ontology?.meta.built_at) {
          load(data)
        }
      } catch { /* server not up yet */ }
      try {
        const res = await fetch('/hotspots.json', { cache: 'no-store' })
        if (res.ok) {
          const hs: Hotspots = await res.json()
          if (!stop && hs.generated_at !== useOnto.getState().hotspots?.generated_at) {
            setHotspots(hs)
          }
        }
      } catch { /* hotspots are optional */ }
    }
    fetchOnto()
    const t = setInterval(fetchOnto, 5000) // freshness: pick up `lensme sync --watch` rebuilds
    return () => { stop = true; clearInterval(t) }
  }, [load, setHotspots])
  void built

  return (
    <ReactFlowProvider>
      <div className="app">
        <Sidebar />
        <main className="canvas-wrap">
          <Toolbar />
          <CanvasOrGraph />
        </main>
        <DetailPanel />
        <Search />
      </div>
    </ReactFlowProvider>
  )
}
