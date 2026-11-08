import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from 'd3-force'
import { useEffect, useMemo, useState } from 'react'

import { api, type GraphEdge, type GraphEntity, type GraphNode, type ShioriDocument } from '../api'
import { useOpenPdf } from './usePdfViewer'

// A node-link graph puts any two colors next to each other, so only three color families are
// used (the most that stay colorblind-safe across all pairs). The exact type is in the details.
const FAMILIES = [
  { label: 'People & organizations', color: '#2a78d6', types: ['person', 'organization'] },
  { label: 'Places', color: '#eb6834', types: ['place'] },
  {
    label: 'Things & ideas',
    color: '#1baf7a',
    types: ['concept', 'method', 'measurement', 'object', 'other'],
  },
]
const colorOf = (type: string) =>
  (FAMILIES.find((f) => f.types.includes(type)) ?? FAMILIES[2]).color

const W = 900
const H = 560
const LABELED = 25 // most-connected entities get a visible name

type SimNode = GraphNode & SimulationNodeDatum
type SimLink = SimulationLinkDatum<SimNode> & Omit<GraphEdge, 'source' | 'target'>

/** Explore the knowledge graph: entities extracted from the papers and how they relate. */
export function GraphPage({ documents }: { documents: ShioriDocument[] }) {
  const [paperId, setPaperId] = useState('')
  const [data, setData] = useState<{ nodes: GraphNode[]; edges: GraphEdge[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [hovered, setHovered] = useState<string | null>(null)
  const [view, setView] = useState<'graph' | 'table'>('graph')

  useEffect(() => {
    api.graphOverview(paperId || undefined).then(
      (d) => {
        setData(d)
        setError(null)
      },
      (err: Error) => setError(err.message),
    )
  }, [paperId])

  const layout = useMemo(() => (data ? runLayout(data) : null), [data])
  const built = documents.filter((d) => d.graph_status === 'ready')
  const neighbors = useMemo(() => {
    const focus = hovered ?? selected
    if (!focus || !layout) return null
    const set = new Set([focus])
    for (const l of layout.links) {
      const a = (l.source as SimNode).key
      const b = (l.target as SimNode).key
      if (a === focus) set.add(b)
      if (b === focus) set.add(a)
    }
    return set
  }, [hovered, selected, layout])

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Knowledge graph</h2>
          <p className="text-sm text-stone-500">
            People, places and ideas extracted from {built.length} paper{built.length === 1 ? '' : 's'}.
            Build a paper's graph from the Library.
          </p>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <select value={paperId} onChange={(e) => { setPaperId(e.target.value); setSelected(null) }}
            className="max-w-64 rounded border border-stone-300 bg-white px-2 py-1">
            <option value="">All papers</option>
            {built.map((d) => <option key={d.id} value={d.id}>{d.title}</option>)}
          </select>
          <div className="flex rounded-lg border border-stone-200 bg-white p-0.5" role="group" aria-label="View">
            {(['graph', 'table'] as const).map((v) => (
              <button key={v} type="button" onClick={() => setView(v)}
                className={`rounded-md px-3 py-1 capitalize ${view === v ? 'bg-shiori-50 font-medium text-shiori-700' : 'text-stone-600'}`}>
                {v}
              </button>
            ))}
          </div>
        </div>
      </div>

      {error && <p className="text-red-600">{error}</p>}
      {data && data.nodes.length === 0 && (
        <p className="rounded-xl border border-dashed border-stone-300 p-8 text-center text-stone-500">
          The graph is empty. Open the Library and click “Build graph” on a paper.
        </p>
      )}

      {layout && data && data.nodes.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
          <section className="rounded-xl border border-stone-200 bg-white p-2 shadow-sm">
            {view === 'graph' ? (
              <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img"
                aria-label={`Knowledge graph with ${layout.nodes.length} entities and ${layout.links.length} relations`}>
                {layout.links.map((l, i) => {
                  const a = l.source as SimNode
                  const b = l.target as SimNode
                  const lit = !neighbors || (neighbors.has(a.key) && neighbors.has(b.key))
                  return (
                    <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                      stroke={lit ? '#a8a29e' : '#e7e5e4'} strokeWidth={lit && neighbors ? 2 : 1}>
                      <title>{`${a.name} —${l.relation}→ ${b.name}`}</title>
                    </line>
                  )
                })}
                {layout.nodes.map((n, i) => {
                  const r = 5 + Math.sqrt(n.degree) * 2.5
                  const dim = neighbors && !neighbors.has(n.key)
                  return (
                    <g key={n.key} transform={`translate(${n.x},${n.y})`} opacity={dim ? 0.25 : 1}
                      onClick={() => setSelected(n.key)} onMouseEnter={() => setHovered(n.key)}
                      onMouseLeave={() => setHovered(null)} className="cursor-pointer">
                      <circle r={Math.max(r, 10)} fill="transparent" />
                      <circle r={r} fill={colorOf(n.type)} stroke={selected === n.key ? '#1c1917' : '#fff'}
                        strokeWidth={selected === n.key ? 2.5 : 2} />
                      {(i < LABELED || neighbors?.has(n.key)) && (
                        <text y={-r - 4} textAnchor="middle" fontSize={11} fill="#44403c"
                          stroke="#fff" strokeWidth={3} paintOrder="stroke">
                          {n.name.length > 28 ? `${n.name.slice(0, 27)}…` : n.name}
                        </text>
                      )}
                      <title>{`${n.name} (${n.type}) · ${n.degree} relations${n.aliases.length ? ` · ${n.aliases.join(', ')}` : ''}`}</title>
                    </g>
                  )
                })}
              </svg>
            ) : (
              <RelationsTable nodes={data.nodes} edges={data.edges} onSelect={setSelected} />
            )}
            <div className="flex flex-wrap gap-4 px-2 pb-1 pt-2 text-xs text-stone-600">
              {FAMILIES.map((f) => (
                <span key={f.label} className="flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: f.color }} />{f.label}
                </span>
              ))}
              <span className="ml-auto text-stone-400">
                {layout.nodes.length} entities · {layout.links.length} relations · size = connections
              </span>
            </div>
          </section>
          <EntityPanel entityKey={selected} documents={documents} onSelect={setSelected} />
        </div>
      )}
    </div>
  )
}

function runLayout(data: { nodes: GraphNode[]; edges: GraphEdge[] }) {
  const nodes: SimNode[] = data.nodes.map((n) => ({ ...n }))
  const byKey = new Map(nodes.map((n) => [n.key, n]))
  const links: SimLink[] = data.edges
    .filter((e) => byKey.has(e.source) && byKey.has(e.target))
    .map((e) => ({ ...e, source: byKey.get(e.source)!, target: byKey.get(e.target)! }))
  // Run the force simulation to rest up front: a static layout, no jittering animation.
  const sim = forceSimulation(nodes)
    .force('link', forceLink<SimNode, SimLink>(links).distance(70))
    .force('charge', forceManyBody().strength(-160))
    .force('center', forceCenter(W / 2, H / 2))
    .force('collide', forceCollide<SimNode>((n) => 8 + Math.sqrt(n.degree) * 2.5))
    .stop()
  for (let i = 0; i < 300; i++) sim.tick()
  for (const n of nodes) {
    n.x = Math.max(20, Math.min(W - 20, n.x ?? W / 2))
    n.y = Math.max(24, Math.min(H - 12, n.y ?? H / 2))
  }
  return { nodes, links }
}

function RelationsTable({
  nodes, edges, onSelect,
}: { nodes: GraphNode[]; edges: GraphEdge[]; onSelect: (key: string) => void }) {
  const names = new Map(nodes.map((n) => [n.key, n.name]))
  return (
    <div className="max-h-[560px] overflow-y-auto">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-white text-left text-xs uppercase tracking-wide text-stone-500">
          <tr><th className="p-2">From</th><th className="p-2">Relation</th><th className="p-2">To</th><th className="p-2">Evidence</th></tr>
        </thead>
        <tbody>
          {edges.map((e, i) => (
            <tr key={i} className="border-t border-stone-100 align-top">
              <td className="p-2"><button type="button" className="text-shiori-700 hover:underline" onClick={() => onSelect(e.source)}>{names.get(e.source)}</button></td>
              <td className="p-2 text-stone-600">{e.relation}</td>
              <td className="p-2"><button type="button" className="text-shiori-700 hover:underline" onClick={() => onSelect(e.target)}>{names.get(e.target)}</button></td>
              <td className="p-2 text-xs text-stone-500">“{e.evidence}”</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function EntityPanel({
  entityKey, documents, onSelect,
}: { entityKey: string | null; documents: ShioriDocument[]; onSelect: (key: string) => void }) {
  const [entity, setEntity] = useState<GraphEntity | null>(null)
  const openPdf = useOpenPdf()

  useEffect(() => {
    if (entityKey) void api.graphEntity(entityKey).then(setEntity, () => setEntity(null))
  }, [entityKey])

  if (!entityKey || !entity || entity.key !== entityKey) {
    return (
      <aside className="rounded-xl border border-dashed border-stone-300 p-4 text-sm text-stone-500">
        Click an entity to see its relations, with the evidence from the papers.
      </aside>
    )
  }
  const titles = new Map(documents.map((d) => [d.id, d.title]))
  return (
    <aside className="max-h-[620px] space-y-3 overflow-y-auto rounded-xl border border-stone-200 bg-white p-4 text-sm shadow-sm">
      <header>
        <p className="text-xs uppercase tracking-wide text-stone-500">{entity.type}</p>
        <h3 className="text-base font-semibold">{entity.name}</h3>
        {entity.aliases.length > 0 && <p className="text-stone-600">{entity.aliases.join(' · ')}</p>}
      </header>
      <section>
        <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-stone-500">Relations</h4>
        {entity.facts.length === 0 && <p className="text-stone-400">None extracted.</p>}
        <ul className="space-y-2">
          {entity.facts.map((f, i) => (
            <li key={i}>
              <p>
                <button type="button" className="font-medium hover:underline" onClick={() => onSelect(keyOf(f.source))}>{f.source}</button>
                <span className="text-stone-500"> —{f.relation}→ </span>
                <button type="button" className="font-medium hover:underline" onClick={() => onSelect(keyOf(f.target))}>{f.target}</button>
              </p>
              <p className="text-xs text-stone-500">“{f.evidence}” · {titles.get(f.paper_id) ?? 'paper'}</p>
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-stone-500">Mentioned in</h4>
        <ul className="space-y-1">
          {entity.mentions.map((m) => (
            <li key={m.chunk_id}>
              <button type="button" className="text-shiori-700 hover:underline"
                onClick={() => openPdf({ documentId: m.paper_id, title: m.paper_title, page: m.page_start })}>
                {m.paper_title}, p. {m.page_start} ↗
              </button>
            </li>
          ))}
        </ul>
      </section>
    </aside>
  )
}

/** Same normalization as the backend's entity_key, so clicking a name opens its entity. */
function keyOf(name: string): string {
  return name.normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}\s_]/gu, ' ').replace(/\s+/g, ' ').trim()
}
