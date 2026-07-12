import { useMemo, useState } from 'react'
import { fileBlastRadius, useOnto } from './store'
import type { OntoNode, Relationship } from './types'
import { RELATION_STYLE } from './types'
import { formatLoc, langOf, LEVEL_GLYPH } from './nodes'

function Arrow({ relation }: { relation: string }) {
  const st = RELATION_STYLE[relation] ?? { color: '#64748b' }
  return (
    <svg width="22" height="8" style={{ flexShrink: 0 }}>
      <line x1="0" y1="4" x2="16" y2="4" stroke={st.color} strokeWidth="2"
            strokeDasharray={st.dash} />
      <polygon points="16,0 22,4 16,8" fill={st.color} />
    </svg>
  )
}

function collectFiles(node: OntoNode): OntoNode[] {
  if (node.type === 'File') return [node]
  return (node.children ?? []).flatMap(collectFiles)
}

export function DetailPanel() {
  const selectedId = useOnto((s) => s.selectedId)
  const onto = useOnto((s) => s.ontology)
  const index = useOnto((s) => s.index)
  const tab = useOnto((s) => s.panelTab)
  const setTab = useOnto((s) => s.setPanelTab)
  const select = useOnto((s) => s.select)
  const setShowImpact = useOnto((s) => s.setShowImpact)
  const openInGraph = useOnto((s) => s.openInGraph)
  const hotspots = useOnto((s) => s.hotspots)
  const [showAllFiles, setShowAllFiles] = useState(false)

  const entry = selectedId ? index.get(selectedId) : null
  const node = entry?.node ?? null

  const rels = useMemo(() => {
    if (!onto || !node) return { out: [] as Relationship[], inc: [] as Relationship[] }
    // Files relate through file_relationships (v2); everything else through components
    const pool = node.type === 'File' ? (onto.file_relationships ?? []) : onto.component_relationships
    return {
      out: pool.filter((r) => r.source === node.id),
      inc: pool.filter((r) => r.target === node.id),
    }
  }, [onto, node])

  // file-level blast radius: reverse BFS over file_relationships (client-side)
  const fileImpact = useMemo(() => {
    if (!onto || !node || node.type !== 'File') return null
    return fileBlastRadius(onto.file_relationships ?? [], node.id)
  }, [onto, node])

  if (!onto || !node) return null
  const impact = onto.impact[node.id]
  const files = collectFiles(node).sort((a, b) => (b.loc ?? 0) - (a.loc ?? 0))
  const nameOf = (id: string) => index.get(id)?.node.name ?? id

  const depCount = rels.out.length + rels.inc.length

  return (
    <aside className="panel">
      <div className="panel-head">
        <div className="panel-title">
          <span className={`chip chip-${node.type.toLowerCase()}`}>{LEVEL_GLYPH[node.type.toLowerCase()] ?? node.type[0]}</span>
          <div>
            <div className="card-title">{node.name}</div>
            <span className={`badge badge-${node.type.toLowerCase()}`}>{node.type}</span>
          </div>
        </div>
        <button className="close-btn" onClick={() => select(null)}>×</button>
      </div>

      <div className="panel-tabs">
        {(['overview', 'properties', 'dependencies', 'impact'] as const).map((t) => (
          <button key={t} className={`tab${tab === t ? ' tab-active' : ''}`}
                  onClick={() => setTab(t)}>
            {t === 'dependencies' ? `Dependencies (${depCount})` : t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      <div className="panel-body">
        {tab === 'overview' && (
          <>
            <div className="sec-title">Description</div>
            <p className="desc">{node.description || node.rationale || '—'}</p>

            {node.type === 'File' && (
              <>
                <div className="sec-title">File</div>
                <div className="prop-row"><span>path</span><code>{node.path ?? node.name}</code></div>
                <div className="prop-row"><span>lines</span><code>{(node.loc ?? 0).toLocaleString()}</code></div>
                <div className="prop-row"><span>functions</span><code>{node.functions ?? 0}</code></div>
                <button className="mini-btn graph-jump" onClick={() => openInGraph(node.name)}>
                  Open in Code Graph ↗
                </button>

                {hotspots && node.path && (hotspots.files[node.path] || null) && (
                  <>
                    <div className="sec-title">Git Activity (since {hotspots.since})</div>
                    <div className="prop-row"><span>commits</span><code>{hotspots.files[node.path]}</code></div>
                    {(() => {
                      const pairs = hotspots.co_change
                        .filter((p) => p.a === node.path || p.b === node.path)
                        .slice(0, 5)
                      if (pairs.length === 0) return null
                      return (
                        <>
                          <div className="sec-title">Often Changes With</div>
                          {pairs.map((p, i) => {
                            const other = p.a === node.path ? p.b : p.a
                            return (
                              <div key={i} className="prop-row cochange-row">
                                <span>{other}</span>
                                <code>
                                  ×{p.count}{!p.structural && (
                                    <span className="hidden-coupling" title="co-changes but no extracted relationship - hidden coupling"> ⚠</span>
                                  )}
                                </code>
                              </div>
                            )
                          })}
                        </>
                      )
                    })()}
                  </>
                )}

                {(node.symbols?.length ?? 0) > 0 && (
                  <>
                    <div className="sec-title">Symbols ({node.symbols!.length})</div>
                    <div className="symbol-list">
                      {node.symbols!.map((s, i) => (
                        <div key={i} className="symbol-row symbol-row-btn"
                             title="open in Code Graph" onClick={() => openInGraph(s.name)}>
                          <span className="symbol-name">{s.name}</span>
                          {s.line != null && <span className="symbol-line">L{s.line}</span>}
                        </div>
                      ))}
                    </div>
                  </>
                )}

                {fileImpact && (fileImpact.direct.length > 0 || fileImpact.indirect.length > 0) && (
                  <>
                    <div className="sec-title">Impact (if modified)</div>
                    <div className="impact-grid">
                      <div className="impact-cell impact-cell-direct impact-cell-btn"
                           title="highlight impacted components on the map" onClick={() => setShowImpact(true)}>
                        <div className="metric-label">Directly Affects</div>
                        <div className="metric-val">{fileImpact.direct.length} Files</div>
                      </div>
                      <div className="impact-cell impact-cell-indirect impact-cell-btn"
                           title="highlight impacted components on the map" onClick={() => setShowImpact(true)}>
                        <div className="metric-label">Indirectly Affects</div>
                        <div className="metric-val">{fileImpact.indirect.length} Files</div>
                      </div>
                    </div>
                    <div className="code-list">
                      {[...fileImpact.direct, ...fileImpact.indirect].slice(0, 8).map((id) => (
                        <div key={id} className="code-row code-row-btn" onClick={() => select(id)}>
                          <span className="file-name">{nameOf(id)}</span>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </>
            )}

            {(node.responsibilities?.length ?? 0) > 0 && (
              <>
                <div className="sec-title">Responsibilities</div>
                <ul className="resp-list">
                  {node.responsibilities!.map((r, i) => <li key={i}>✓ {r}</li>)}
                </ul>
              </>
            )}

            {(rels.out.length > 0 || rels.inc.length > 0) && (
              <>
                <div className="sec-title">Relationships</div>
                <div className="rel-list">
                  {rels.out.map((r, i) => (
                    <div key={`o${i}`} className="rel-row" onClick={() => select(r.target)}>
                      <Arrow relation={r.relation} />
                      <span className="rel-name">{r.relation.replace(/_/g, ' ')}</span>
                      <span className="rel-target">{nameOf(r.target)}{r.count && r.count > 1 ? ` ×${r.count}` : ''}</span>
                    </div>
                  ))}
                  {rels.inc.map((r, i) => (
                    <div key={`i${i}`} className="rel-row rel-in" onClick={() => select(r.source)}>
                      <Arrow relation={r.relation} />
                      <span className="rel-name">← {r.relation.replace(/_/g, ' ')}</span>
                      <span className="rel-target">{nameOf(r.source)}</span>
                    </div>
                  ))}
                </div>
              </>
            )}

            {node.stats && (
              <>
                <div className="sec-title">Key Metrics</div>
                <div className="metric-grid">
                  <div className="metric"><div className="metric-label">Code Files</div><div className="metric-val">{node.stats.files ?? files.length}</div></div>
                  <div className="metric"><div className="metric-label">Functions</div><div className="metric-val">{node.stats.functions ?? 0}</div></div>
                  <div className="metric"><div className="metric-label">Dependencies</div><div className="metric-val">{node.stats.dependencies ?? depCount}</div></div>
                  <div className="metric"><div className="metric-label">LOC</div><div className="metric-val">{((node.stats.loc ?? 0) / 1000).toFixed(1)}k</div></div>
                </div>
              </>
            )}

            {files.length > 0 && node.type !== 'File' && (
              <>
                <div className="sec-title">Related Code ({files.length})</div>
                <div className="code-list">
                  {(showAllFiles ? files : files.slice(0, 5)).map((f) => {
                    const lang = langOf(f.name)
                    return (
                      <div key={f.id} className="code-row code-row-btn" title={f.path ?? f.name}
                           onClick={() => select(f.id)}>
                        <span className="file-lang" style={{ background: lang.color }}>{lang.label}</span>
                        <span className="file-name">{f.name}</span>
                        <span className="file-loc">{formatLoc(f.loc ?? 0)}</span>
                      </div>
                    )
                  })}
                  {files.length > 5 && (
                    <div className="more-files more-files-btn" onClick={() => setShowAllFiles((v) => !v)}>
                      {showAllFiles ? 'collapse ↑' : `+ ${files.length - 5} more files`}
                    </div>
                  )}
                </div>
              </>
            )}

            {impact && (
              <>
                <div className="sec-title">Impact (if modified)</div>
                <div className="impact-grid">
                  <div className="impact-cell impact-cell-direct impact-cell-btn"
                       title="highlight impacted components on the map" onClick={() => setShowImpact(true)}>
                    <div className="metric-label">Directly Affects</div>
                    <div className="metric-val">{impact.direct.length} Components</div>
                  </div>
                  <div className="impact-cell impact-cell-indirect impact-cell-btn"
                       title="highlight impacted components on the map" onClick={() => setShowImpact(true)}>
                    <div className="metric-label">Indirectly Affects</div>
                    <div className="metric-val">{impact.indirect.length} Components</div>
                  </div>
                </div>
                <div className="impact-total">Total Code Files <b>{impact.total_files}</b></div>
              </>
            )}
          </>
        )}

        {tab === 'properties' && (
          <>
            <div className="sec-title">Audit Trail</div>
            <div className="prop-row"><span>Confidence</span><b className={`conf-text conf-${(node.confidence ?? 'EXTRACTED').includes('llm') ? 'llm-t' : (node.confidence ?? '').includes('heuristic') ? 'heur-t' : 'ext-t'}`}>{node.confidence ?? 'EXTRACTED'}</b></div>
            {node.rationale && (
              <>
                <div className="sec-title">Rationale (why this grouping)</div>
                <p className="desc">{node.rationale}</p>
              </>
            )}
            <div className="sec-title">Identity</div>
            <div className="prop-row"><span>id</span><code>{node.id}</code></div>
            <div className="prop-row"><span>type</span><code>{node.type}</code></div>
            {node.path && <div className="prop-row"><span>path</span><code>{node.path}</code></div>}
            {node.stats && (
              <>
                <div className="sec-title">Stats</div>
                {Object.entries(node.stats).map(([k, v]) => (
                  <div key={k} className="prop-row"><span>{k}</span><code>{String(v)}</code></div>
                ))}
              </>
            )}
          </>
        )}

        {tab === 'dependencies' && (
          <>
            <div className="sec-title">Outgoing ({rels.out.length})</div>
            {rels.out.map((r, i) => (
              <div key={i} className="rel-row" onClick={() => select(r.target)}>
                <Arrow relation={r.relation} />
                <span className="rel-name">{r.relation.replace(/_/g, ' ')}</span>
                <span className="rel-target">{nameOf(r.target)}</span>
              </div>
            ))}
            <div className="sec-title">Incoming ({rels.inc.length})</div>
            {rels.inc.map((r, i) => (
              <div key={i} className="rel-row" onClick={() => select(r.source)}>
                <Arrow relation={r.relation} />
                <span className="rel-name">{r.relation.replace(/_/g, ' ')}</span>
                <span className="rel-target">{nameOf(r.source)}</span>
              </div>
            ))}
          </>
        )}

        {tab === 'impact' && (
          <>
            {!impact && <p className="desc">Impact is computed for Components.</p>}
            {impact && (
              <>
                <div className="sec-title">Directly Affects ({impact.direct.length})</div>
                {impact.direct.map((id) => (
                  <div key={id} className="rel-row" onClick={() => select(id)}>
                    <span className="impact-dot impact-dot-direct" />
                    <span className="rel-target">{nameOf(id)}</span>
                  </div>
                ))}
                <div className="sec-title">Indirectly Affects ({impact.indirect.length})</div>
                {impact.indirect.map((id) => (
                  <div key={id} className="rel-row" onClick={() => select(id)}>
                    <span className="impact-dot impact-dot-indirect" />
                    <span className="rel-target">{nameOf(id)}</span>
                  </div>
                ))}
                <div className="impact-total">Total Code Files <b>{impact.total_files}</b></div>
              </>
            )}
          </>
        )}
      </div>
    </aside>
  )
}
