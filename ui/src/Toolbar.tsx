import { useReactFlow } from '@xyflow/react'
import { useOnto } from './store'

const LEVEL_OPTIONS = [
  { v: 3, label: 'Components' },
  { v: 4, label: 'Modules' },
  { v: 5, label: 'Files' },
  { v: 6, label: 'All Levels' },
]

export function Toolbar() {
  const onto = useOnto((s) => s.ontology)
  const maxLevel = useOnto((s) => s.maxLevel)
  const setMaxLevel = useOnto((s) => s.setMaxLevel)
  const groupBy = useOnto((s) => s.groupBy)
  const setGroupBy = useOnto((s) => s.setGroupBy)
  const showImpact = useOnto((s) => s.showImpact)
  const setShowImpact = useOnto((s) => s.setShowImpact)
  const setSearchOpen = useOnto((s) => s.setSearchOpen)
  const view = useOnto((s) => s.view)
  const setView = useOnto((s) => s.setView)
  const openInGraph = useOnto((s) => s.openInGraph)
  const { fitView } = useReactFlow()
  if (!onto) return null

  return (
    <div className="toolbar">
      <span className="toolbar-product">{onto.name}</span>
      <div className="view-tabs">
        <button className={'view-tab' + (view === 'map' ? ' active' : '')}
                onClick={() => setView('map')}>Ontology Map</button>
        <button className={'view-tab' + (view === 'graph' ? ' active' : '')}
                onClick={() => openInGraph(null)}>Code Graph</button>
      </div>
      {view === 'map' && <>
        <select className="toolbar-select" value={groupBy}
                title="Feature: inferred business domain. Folder: literal directory structure (100% EXTRACTED, no guessing)."
                onChange={(e) => setGroupBy(e.target.value as 'feature' | 'folder')}>
          <option value="feature">Group: Feature</option>
          <option value="folder">Group: Folder</option>
        </select>
        {groupBy === 'feature' && (
          <select className="toolbar-select" value={maxLevel}
                  onChange={(e) => setMaxLevel(Number(e.target.value))}>
            {LEVEL_OPTIONS.map((o) => <option key={o.v} value={o.v}>{o.label}</option>)}
          </select>
        )}
        <button className="mini-btn" onClick={() => fitView({ padding: 0.15, duration: 300 })}>
          Fit View
        </button>
        <button className="mini-btn" onClick={() => setSearchOpen(true)}>
          Search ⌘K
        </button>
        <span className="toolbar-spacer" />
        <label className="check-row toolbar-impact">
          <input type="checkbox" checked={showImpact}
                 onChange={(e) => setShowImpact(e.target.checked)} />
          Show Impact
        </label>
      </>}
    </div>
  )
}
