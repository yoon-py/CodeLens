import { useOnto } from './store'
import { LEVEL_COLOR, RELATION_STYLE } from './types'

const LEVEL_ROWS: { key: string; label: string; level: number; color: string }[] = [
  { key: 'product', label: 'Product', level: 1, color: LEVEL_COLOR.Product },
  { key: 'feature', label: 'Feature', level: 2, color: LEVEL_COLOR.Feature },
  { key: 'component', label: 'Component', level: 3, color: LEVEL_COLOR.Component },
  { key: 'module', label: 'Module', level: 4, color: LEVEL_COLOR.Module },
  { key: 'file', label: 'Code / File', level: 5, color: LEVEL_COLOR.File },
  { key: 'external', label: 'External', level: 6, color: LEVEL_COLOR.External },
  { key: 'database', label: 'Database', level: 6, color: LEVEL_COLOR.Database },
]

const LEGEND: { relation: string; label: string }[] = [
  { relation: 'contains', label: 'owns / contains' },
  { relation: 'depends_on', label: 'depends on' },
  { relation: 'implements', label: 'implements' },
  { relation: 'calls', label: 'calls / invokes' },
  { relation: 'integrates_with', label: 'integrates with' },
  { relation: 'references', label: 'references' },
]

export function Sidebar() {
  const onto = useOnto((s) => s.ontology)
  const maxLevel = useOnto((s) => s.maxLevel)
  const setMaxLevel = useOnto((s) => s.setMaxLevel)
  const showRelationships = useOnto((s) => s.showRelationships)
  const setShowRelationships = useOnto((s) => s.setShowRelationships)
  const showImpact = useOnto((s) => s.showImpact)
  const setShowImpact = useOnto((s) => s.setShowImpact)
  const hotspots = useOnto((s) => s.hotspots)
  const showHotspots = useOnto((s) => s.showHotspots)
  const setShowHotspots = useOnto((s) => s.setShowHotspots)
  const expandAll = useOnto((s) => s.expandAll)
  const collapseAll = useOnto((s) => s.collapseAll)
  if (!onto) return null
  const counts = onto.meta.level_counts
  const gs = onto.meta.graph_stats

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark">◆</span> codelens
      </div>

      <div className="side-section">
        <div className="side-title">Ontology Levels</div>
        {LEVEL_ROWS.map((r) => (
          <button
            key={r.key}
            className={`level-row${maxLevel >= r.level ? '' : ' level-off'}`}
            onClick={() => setMaxLevel(r.level)}
            title={`show up to ${r.label}`}
          >
            <span className="dot" style={{ background: r.color }} />
            <span className="level-label">{r.label}</span>
            <span className="level-count">{counts[r.key] ?? 0}</span>
          </button>
        ))}
      </div>

      <div className="side-section">
        <div className="side-title">Filters</div>
        <label className="check-row">
          <input type="checkbox" checked={showRelationships}
                 onChange={(e) => setShowRelationships(e.target.checked)} />
          Show Relationships
        </label>
        <label className="check-row">
          <input type="checkbox" checked={showImpact}
                 onChange={(e) => setShowImpact(e.target.checked)} />
          Show Impact
        </label>
        {hotspots && (
          <label className="check-row" title={`git churn since ${hotspots.since} (${hotspots.commits_scanned} commits)`}>
            <input type="checkbox" checked={showHotspots}
                   onChange={(e) => setShowHotspots(e.target.checked)} />
            Show Hotspots 🔥
          </label>
        )}
        <div className="btn-row">
          <button className="mini-btn" onClick={expandAll}>Expand all</button>
          <button className="mini-btn" onClick={collapseAll}>Collapse</button>
        </div>
      </div>

      <div className="side-section">
        <div className="side-title">Relationship Legend</div>
        {LEGEND.map((l) => {
          const st = RELATION_STYLE[l.relation]
          return (
            <div key={l.relation} className="legend-row">
              <svg width="26" height="6">
                <line x1="0" y1="3" x2="26" y2="3" stroke={st.color} strokeWidth="2"
                      strokeDasharray={st.dash} />
              </svg>
              {l.label}
            </div>
          )
        })}
        <div className="legend-row conf-legend">
          <span className="conf-dot conf-heuristic" /> heuristic inference
        </div>
        <div className="legend-row conf-legend">
          <span className="conf-dot conf-llm" /> LLM inference
        </div>
      </div>

      <div className="side-footer">
        {gs.nodes.toLocaleString()} nodes · {gs.edges.toLocaleString()} edges · {gs.communities} communities
        <div className="powered">graphify powered <span className="live-dot" /></div>
      </div>
    </aside>
  )
}
