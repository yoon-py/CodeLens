export type NodeType =
  | 'Product' | 'Feature' | 'Component' | 'Module' | 'File' | 'External' | 'Database'

export interface Stats {
  files?: number
  loc?: number
  functions?: number
  dependencies?: number
  components?: number
  features?: number
  external?: number
  database?: number
}

export interface FileSymbol {
  name: string
  line: number | null
}

export interface OntoNode {
  id: string
  type: NodeType
  name: string
  description?: string
  rationale?: string
  confidence?: string
  responsibilities?: string[]
  stats?: Stats
  children?: OntoNode[]
  // File-only
  path?: string
  loc?: number
  functions?: number
  symbols?: FileSymbol[]
}

export interface Relationship {
  source: string
  target: string
  relation: string
  confidence: string
  count?: number
}

export interface External {
  id: string
  type: 'External'
  name: string
  kind: string
  confidence: string
}

export interface Impact {
  direct: string[]
  indirect: string[]
  total_files: number
}

export interface Ontology extends OntoNode {
  schema_version: number
  meta: {
    built_at: string
    source_graph: string
    graph_stats: { nodes: number; edges: number; communities: number }
    level_counts: Record<string, number>
  }
  discovered_domain_words: string[]
  component_relationships: Relationship[]
  file_relationships?: Relationship[]  // schema v2
  external: External[]
  database: string[]
  impact: Record<string, Impact>
}

/** Output of `ontomap hotspots`: git churn + co-change joined onto the ontology. */
export interface Hotspots {
  generated_at: string
  since: string
  commits_scanned: number
  files: Record<string, number>       // File.path -> commit count
  components: Record<string, number>  // component id -> commit count
  co_change: { a: string; b: string; count: number; structural: boolean }[]
}

/** Flattened index entry with parent chain for search/selection. */
export interface IndexEntry {
  node: OntoNode
  parent: string | null
  featureId: string | null
  componentId: string | null
}

export const LEVEL_OF: Record<NodeType, number> = {
  Product: 1, Feature: 2, Component: 3, Module: 4, File: 5, External: 6, Database: 6,
}

export const RELATION_STYLE: Record<string, { color: string; dash?: string }> = {
  contains: { color: '#8b5cf6' },
  depends_on: { color: '#f59e0b', dash: '6 4' },
  calls: { color: '#fb923c' },
  implements: { color: '#34d399' },
  references: { color: '#94a3b8', dash: '4 4' },
  integrates_with: { color: '#f472b6', dash: '6 4' },
}

export const LEVEL_COLOR: Record<NodeType, string> = {
  Product: '#8b5cf6',
  Feature: '#d946ef',
  Component: '#22d3ee',
  Module: '#34d399',
  File: '#f59e0b',
  External: '#f87171',
  Database: '#60a5fa',
}
