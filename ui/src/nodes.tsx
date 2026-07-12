import { useState } from 'react'
import { Handle, Position } from '@xyflow/react'
import type { NodeProps } from '@xyflow/react'
import type { External, OntoNode } from './types'
import { useOnto } from './store'

/** Custom cards for each ontology level. All follow the mockup pattern:
 * icon chip + name + level badge + one-line description. */

export const LEVEL_GLYPH: Record<string, string> = {
  product: '📦', feature: '🎯', component: '🧩',
  module: '📁', file: '📄', external: '🌐', database: '🗄️',
}

const LANG: Record<string, { label: string; color: string }> = {
  ts: { label: 'TS', color: '#3178c6' }, tsx: { label: 'TSX', color: '#3178c6' },
  js: { label: 'JS', color: '#b8a418' }, jsx: { label: 'JSX', color: '#b8a418' },
  py: { label: 'PY', color: '#3572a5' }, rs: { label: 'RS', color: '#c07a4a' },
  go: { label: 'GO', color: '#00add8' }, rb: { label: 'RB', color: '#cc342d' },
  java: { label: 'JAVA', color: '#b07219' }, cs: { label: 'C#', color: '#178600' },
  php: { label: 'PHP', color: '#6572b0' }, ex: { label: 'EX', color: '#6e4a7e' },
  css: { label: 'CSS', color: '#7d5bbe' }, html: { label: 'HTML', color: '#e34c26' },
  json: { label: 'JSON', color: '#a0a041' }, md: { label: 'MD', color: '#4a76c4' },
}

/** File-extension badge (label + brand-ish color). Fixes the old hardcoded "TS". */
export function langOf(name: string): { label: string; color: string } {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  return LANG[ext] ?? { label: (ext.slice(0, 4) || 'FILE').toUpperCase(), color: '#64748b' }
}

function Chip({ level, glyph }: { level: string; glyph: string }) {
  return <span className={`chip chip-${level}`}>{glyph}</span>
}

function confDot(conf?: string) {
  if (!conf || conf === 'EXTRACTED') return null
  const cls = conf === 'INFERRED-llm' ? 'conf-llm' : 'conf-heuristic'
  return <span className={`conf-dot ${cls}`} title={conf} />
}

export function ProductNode({ data }: NodeProps) {
  const node = (data as { node: OntoNode }).node
  const s = node.stats ?? {}
  return (
    <div className="card card-product">
      <Handle type="source" position={Position.Bottom} className="h" />
      <div className="card-head">
        <Chip level="product" glyph="📦" />
        <div>
          <div className="card-title">{node.name}</div>
          <span className="badge badge-product">Product</span>
        </div>
      </div>
      {node.description && <div className="card-desc">{node.description}</div>}
      <div className="product-stats">
        <span>{s.features ?? 0} Features</span>
        <span>{s.components ?? 0} Components</span>
        <span>{s.files ?? 0} Code Files</span>
        <span>{s.external ?? 0} External</span>
        <span>{s.database ?? 0} Databases</span>
      </div>
    </div>
  )
}

export function FeatureNode({ data }: NodeProps) {
  const node = (data as { node: OntoNode }).node
  return (
    <div className="card card-feature">
      <Handle type="target" position={Position.Top} className="h" />
      <Handle type="source" position={Position.Bottom} className="h" />
      <div className="card-head">
        <Chip level="feature" glyph="🎯" />
        <div>
          <div className="card-title">{node.name} {confDot(node.confidence)}</div>
          <span className="badge badge-feature">Feature</span>
        </div>
      </div>
      <div className="card-desc">
        {node.description || `${node.stats?.components ?? 0} components · ${node.stats?.files ?? 0} files`}
      </div>
    </div>
  )
}

export function ComponentNode({ data }: NodeProps) {
  const { node, expanded, expandable } = data as {
    node: OntoNode; expanded: boolean; expandable: boolean
  }
  const toggleExpand = useOnto((s) => s.toggleExpand)
  return (
    <div className="card card-component">
      <Handle type="target" position={Position.Top} className="h" />
      <Handle type="source" position={Position.Bottom} className="h" />
      <div className="card-head">
        <Chip level="component" glyph="🧩" />
        <div>
          <div className="card-title">{node.name} {confDot(node.confidence)}</div>
          <span className="badge badge-component">Component</span>
        </div>
        {expandable && (
          <button
            className="expand-btn"
            title={expanded ? 'collapse' : 'expand modules/files'}
            onClick={(e) => { e.stopPropagation(); toggleExpand(node.id) }}
          >
            {expanded ? '−' : '+'}
          </button>
        )}
      </div>
      <div className="card-desc">{node.description || node.rationale}</div>
      <div className="card-meta">
        {node.stats?.files ?? 0} files · {(node.stats?.loc ?? 0).toLocaleString()} LOC
      </div>
    </div>
  )
}

export function ModuleNode({ data }: NodeProps) {
  const node = (data as { node: OntoNode }).node
  return (
    <div className="card card-module">
      <Handle type="target" position={Position.Top} className="h" />
      <Handle type="source" position={Position.Bottom} className="h" />
      <div className="card-head">
        <Chip level="module" glyph="📁" />
        <div>
          <div className="card-title">{node.name}</div>
          <span className="badge badge-module">Module</span>
        </div>
      </div>
      <div className="card-meta">
        {node.stats?.files ?? 0} files · {(node.stats?.loc ?? 0).toLocaleString()} LOC
      </div>
    </div>
  )
}

const STACK_LIMIT = 3

export function FileStackNode({ data }: NodeProps) {
  const { files } = data as { files: OntoNode[]; ownerId: string }
  const select = useOnto((s) => s.select)
  const [showAll, setShowAll] = useState(false)
  const sorted = [...files].sort((a, b) => (b.loc ?? 0) - (a.loc ?? 0))
  const shown = showAll ? sorted : sorted.slice(0, STACK_LIMIT)
  const rest = files.length - shown.length
  return (
    <div className="filestack">
      <Handle type="target" position={Position.Top} className="h" />
      {shown.map((f) => {
        const lang = langOf(f.name)
        return (
          <div key={f.id} className="file-chip file-chip-btn" title={f.path ?? f.name}
               onClick={(e) => { e.stopPropagation(); select(f.id) }}>
            <span className="file-lang" style={{ background: lang.color }}>{lang.label}</span>
            <span className="file-name">{f.name}</span>
            <span className="file-loc">{formatLoc(f.loc ?? 0)}</span>
          </div>
        )
      })}
      {rest > 0 && (
        <div className="file-chip file-more"
             onClick={(e) => { e.stopPropagation(); setShowAll(true) }}>… {rest} more files</div>
      )}
      {showAll && files.length > STACK_LIMIT && (
        <div className="file-chip file-more"
             onClick={(e) => { e.stopPropagation(); setShowAll(false) }}>collapse ↑</div>
      )}
    </div>
  )
}

export function ExternalNode({ data }: NodeProps) {
  const ext = (data as { ext: External }).ext
  return (
    <div className="card card-external">
      <Handle type="target" position={Position.Top} className="h" />
      <div className="card-head">
        <Chip level="external" glyph="🌐" />
        <div>
          <div className="card-title">{ext.name}</div>
          <span className="badge badge-external">External · {ext.kind}</span>
        </div>
      </div>
    </div>
  )
}

export function formatLoc(loc: number): string {
  return loc >= 1000 ? `${(loc / 1000).toFixed(1)}k LOC` : `${loc} LOC`
}

export const nodeTypes = {
  product: ProductNode,
  feature: FeatureNode,
  component: ComponentNode,
  module: ModuleNode,
  filestack: FileStackNode,
  external: ExternalNode,
}
