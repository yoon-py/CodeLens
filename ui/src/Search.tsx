import { useEffect, useMemo, useRef, useState } from 'react'
import { useReactFlow } from '@xyflow/react'
import { useOnto } from './store'
import type { Relationship } from './types'

/** Shortest directed path over component relationships (BFS). Returns the
 * ordered node ids and the specific edge keys (src|tgt|relation) traversed. */
function findPath(rels: Relationship[], from: string, to: string) {
  const adj = new Map<string, { to: string; relation: string }[]>()
  for (const r of rels) {
    if (!adj.has(r.source)) adj.set(r.source, [])
    adj.get(r.source)!.push({ to: r.target, relation: r.relation })
  }
  const prev = new Map<string, { node: string; relation: string }>()
  const queue = [from]
  const seen = new Set([from])
  while (queue.length) {
    const cur = queue.shift()!
    if (cur === to) {
      const nodes: string[] = []
      const edgeKeys: string[] = []
      let walk = to
      while (walk !== from) {
        const p = prev.get(walk)!
        nodes.unshift(walk)
        edgeKeys.push(`${p.node}|${walk}|${p.relation}`)
        walk = p.node
      }
      nodes.unshift(from)
      return { nodes, edgeKeys }
    }
    for (const e of adj.get(cur) ?? []) {
      if (!seen.has(e.to)) {
        seen.add(e.to)
        prev.set(e.to, { node: cur, relation: e.relation })
        queue.push(e.to)
      }
    }
  }
  return null
}

export function Search() {
  const open = useOnto((s) => s.searchOpen)
  const setOpen = useOnto((s) => s.setSearchOpen)
  const index = useOnto((s) => s.index)
  const onto = useOnto((s) => s.ontology)
  const select = useOnto((s) => s.select)
  const setPathResult = useOnto((s) => s.setPathResult)
  const toggleExpand = useOnto((s) => s.toggleExpand)
  const expanded = useOnto((s) => s.expanded)
  const [q, setQ] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const rf = useReactFlow()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen(true)
      }
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setOpen])

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 50)
    else setQ('')
  }, [open])

  const results = useMemo(() => {
    if (!q.trim()) return []
    const needle = q.toLowerCase()
    const out: { id: string; name: string; type: string }[] = []
    for (const [id, e] of index) {
      if (e.node.name.toLowerCase().includes(needle)) {
        out.push({ id, name: e.node.name, type: e.node.type })
        if (out.length >= 12) break
      }
    }
    return out
  }, [q, index])

  // "A -> B" path query: resolve both sides to components, BFS over relationships
  const pathQuery = useMemo(() => {
    const m = q.split(/->|→/)
    if (m.length !== 2 || !onto) return null
    const resolve = (s: string) => {
      const needle = s.trim().toLowerCase()
      if (!needle) return null
      let hit = null
      for (const e of index.values()) {
        if (e.node.type !== 'Component' && e.node.type !== 'External') continue
        const name = e.node.name.toLowerCase()
        if (name === needle) return e.node
        if (!hit && name.includes(needle)) hit = e.node
      }
      return hit
    }
    const from = resolve(m[0]); const to = resolve(m[1])
    if (!from || !to || from.id === to.id) return null
    const path = findPath(onto.component_relationships, from.id, to.id)
    const back = path ? null : findPath(onto.component_relationships, to.id, from.id)
    return { from, to, path, back }
  }, [q, index, onto])

  if (!open) return null

  const go = (id: string) => {
    const entry = index.get(id)
    // expand the owning component so module/file selections are visible
    if (entry?.componentId && entry.componentId !== id && !expanded.has(entry.componentId)) {
      toggleExpand(entry.componentId)
    }
    select(id)
    setOpen(false)
    // center after the layout re-renders
    setTimeout(() => {
      const n = rf.getNode(id) ?? (entry?.componentId ? rf.getNode(entry.componentId) : undefined)
      if (n) rf.setCenter(n.position.x + 110, n.position.y + 60, { zoom: 1, duration: 300 })
    }, 80)
  }

  return (
    <div className="search-overlay" onClick={() => setOpen(false)}>
      <div className="search-box" onClick={(e) => e.stopPropagation()}>
        <input ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)}
               placeholder="Search objects… or trace a path: extraction -> export" />
        <div className="search-results">
          {pathQuery && (
            (pathQuery.path || pathQuery.back) ? (
              <button className="search-row path-row" onClick={() => {
                const p = (pathQuery.path ?? pathQuery.back)!
                setPathResult(p)
                select(null)
                setOpen(false)
                setTimeout(() => rf.fitView({ padding: 0.15, duration: 300 }), 80)
              }}>
                <span className="chip chip-component">⇢</span>
                <span className="search-name">
                  {pathQuery.path
                    ? `${pathQuery.from.name} ⇢ ${pathQuery.to.name}`
                    : `${pathQuery.to.name} ⇢ ${pathQuery.from.name} (reverse only)`}
                </span>
                <span className="search-type">{(pathQuery.path ?? pathQuery.back)!.nodes.length - 1} hops</span>
              </button>
            ) : (
              <div className="search-empty">no path between {pathQuery.from.name} and {pathQuery.to.name}</div>
            )
          )}
          {results.map((r) => (
            <button key={r.id} className="search-row" onClick={() => go(r.id)}>
              <span className={`chip chip-${r.type.toLowerCase()}`}>{r.type[0]}</span>
              <span className="search-name">{r.name}</span>
              <span className="search-type">{r.type}</span>
            </button>
          ))}
          {q && results.length === 0 && <div className="search-empty">no matches</div>}
        </div>
      </div>
    </div>
  )
}
