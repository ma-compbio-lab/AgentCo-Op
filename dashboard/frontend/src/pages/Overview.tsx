import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import type { RunSummary } from '../lib/types'
import { MetricCard } from '../components/MetricCard'

export function Overview() {
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [selected, setSelected] = useState<RunSummary | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    api.getRuns().then(data => {
      setRuns(data)
      if (data.length > 0) setSelected(data[0])
    })
  }, [])

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-gray-800">Overview</h2>
        <select
          className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white"
          value={selected?.run_id ?? ''}
          onChange={e => setSelected(runs.find(r => r.run_id === e.target.value) ?? null)}
        >
          {runs.map(r => (
            <option key={r.run_id} value={r.run_id}>
              {r.name} — {r.timestamp.slice(0, 10)}
            </option>
          ))}
        </select>
      </div>

      {selected && (
        <div className="grid grid-cols-4 gap-4">
          <MetricCard label="Score" value={`${((selected.solve_rate ?? 0) * 100).toFixed(1)}%`} color="green" />
          <MetricCard label="Total Cost" value={`$${selected.cost.toFixed(4)}`} color="blue" />
          <MetricCard label="Tasks" value={selected.task_count} color="purple" />
          <MetricCard label="Status" value={selected.status} color="amber" />
        </div>
      )}

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50">
              <th className="text-left px-4 py-3 font-medium text-gray-500">Run</th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Benchmark</th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Score</th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Tasks</th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Cost</th>
            </tr>
          </thead>
          <tbody>
            {runs.map(run => (
              <tr
                key={run.run_id}
                onClick={() => navigate(`/topology/${run.run_id}`)}
                className="border-b border-gray-50 hover:bg-purple-50 cursor-pointer transition-colors"
              >
                <td className="px-4 py-3 font-medium text-gray-800">{run.name}</td>
                <td className="px-4 py-3 text-gray-600">{run.benchmark}</td>
                <td className="px-4 py-3">
                  <span className="text-green-600 font-semibold">
                    {((run.solve_rate ?? 0) * 100).toFixed(1)}%
                  </span>
                </td>
                <td className="px-4 py-3 text-gray-600">{run.task_count}</td>
                <td className="px-4 py-3 text-gray-600">${run.cost.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
