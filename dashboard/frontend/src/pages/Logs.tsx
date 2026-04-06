import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../lib/api'
import type { RunSummary, LogEvent } from '../lib/types'
import { ChevronDown, ChevronRight } from 'lucide-react'

const statusBadge: Record<string, string> = {
  success: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
  skipped: 'bg-gray-100 text-gray-500',
  cached: 'bg-blue-100 text-blue-700',
}

export function Logs() {
  const { runId } = useParams()
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [events, setEvents] = useState<LogEvent[]>([])
  const [currentRunId, setCurrentRunId] = useState(runId ?? '')
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null)
  const [filterType, setFilterType] = useState('')
  const [filterNode, setFilterNode] = useState('')

  useEffect(() => {
    api.getRuns().then(data => {
      setRuns(data)
      if (!currentRunId && data.length > 0) setCurrentRunId(data[0].run_id)
    })
  }, [])

  useEffect(() => {
    if (currentRunId) api.getLogs(currentRunId).then(setEvents)
  }, [currentRunId])

  const types = [...new Set(events.map(e => e.type))]
  const nodes = [...new Set(events.filter(e => e.node_id).map(e => e.node_id!))]
  const filtered = events.filter(e =>
    (!filterType || e.type === filterType) && (!filterNode || e.node_id === filterNode)
  )

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-gray-800">Logs</h2>
        <select className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white" value={currentRunId}
          onChange={e => setCurrentRunId(e.target.value)}>
          {runs.map(r => <option key={r.run_id} value={r.run_id}>{r.name}</option>)}
        </select>
      </div>

      <div className="flex gap-3">
        <select className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white" value={filterType}
          onChange={e => setFilterType(e.target.value)}>
          <option value="">All types</option>
          {types.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
        <select className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white" value={filterNode}
          onChange={e => setFilterNode(e.target.value)}>
          <option value="">All nodes</option>
          {nodes.map(n => <option key={n} value={n}>{n}</option>)}
        </select>
        <span className="text-sm text-gray-400 self-center">{filtered.length} events</span>
      </div>

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50">
              <th className="w-8"></th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Type</th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Node</th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Status</th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Confidence</th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Task</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 200).map((event, i) => (
              <tbody key={i}>
                <tr onClick={() => setExpandedIdx(expandedIdx === i ? null : i)}
                  className="border-b border-gray-50 hover:bg-gray-50 cursor-pointer">
                  <td className="pl-3 text-gray-400">
                    {expandedIdx === i ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs">{event.type}</td>
                  <td className="px-4 py-2">{event.node_id ?? '\u2014'}</td>
                  <td className="px-4 py-2">
                    {event.status && (
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${statusBadge[event.status] ?? 'bg-gray-100 text-gray-500'}`}>
                        {event.status}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-gray-600">{event.confidence != null ? `${(event.confidence * 100).toFixed(0)}%` : '\u2014'}</td>
                  <td className="px-4 py-2 text-gray-400 text-xs">{event.task_id ?? ''}</td>
                </tr>
                {expandedIdx === i && (
                  <tr>
                    <td colSpan={6} className="px-6 py-3 bg-gray-50">
                      <pre className="text-xs text-gray-600 overflow-auto max-h-60">{JSON.stringify(event, null, 2)}</pre>
                    </td>
                  </tr>
                )}
              </tbody>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
