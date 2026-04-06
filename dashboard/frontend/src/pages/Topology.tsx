import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  ReactFlow,
  Background,
  Controls,
  type Node,
  type Edge,
  Position,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { api } from '../lib/api'
import type { RunSummary, TopologyData } from '../lib/types'
import { X } from 'lucide-react'

const statusColors: Record<string, string> = {
  success: '#16a34a',
  failed: '#dc2626',
  running: '#d97706',
  pending: '#9ca3af',
  cached: '#2563eb',
  skipped: '#9ca3af',
}

function nodeStyle(status: string) {
  return {
    background: '#fff',
    border: `2px solid ${statusColors[status] ?? '#9ca3af'}`,
    borderRadius: 12,
    padding: '10px 16px',
    fontSize: 13,
    fontWeight: 600,
    color: '#111827',
    minWidth: 120,
    textAlign: 'center' as const,
  }
}

export function Topology() {
  const { runId } = useParams()
  const navigate = useNavigate()
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [topo, setTopo] = useState<TopologyData | null>(null)
  const [selectedNode, setSelectedNode] = useState<any>(null)
  const [currentRunId, setCurrentRunId] = useState(runId ?? '')

  useEffect(() => {
    api.getRuns().then(data => {
      setRuns(data)
      if (!currentRunId && data.length > 0) {
        setCurrentRunId(data[0].run_id)
      }
    })
  }, [])

  useEffect(() => {
    if (currentRunId) {
      api.getTopology(currentRunId).then(setTopo)
    }
  }, [currentRunId])

  const flowNodes: Node[] = (topo?.nodes ?? []).map((n, i) => ({
    id: n.id,
    position: { x: 200 * i, y: 100 + (i % 2) * 80 },
    data: {
      label: (
        <div>
          <div style={{ fontSize: 11, color: statusColors[n.status] ?? '#888', marginBottom: 2 }}>
            {n.status.toUpperCase()}
          </div>
          <div>{n.role}</div>
          {n.confidence != null && (
            <div style={{ fontSize: 10, color: '#888', marginTop: 2 }}>
              conf: {(n.confidence * 100).toFixed(0)}%
            </div>
          )}
        </div>
      ),
    },
    style: nodeStyle(n.status),
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
  }))

  const flowEdges: Edge[] = (topo?.edges ?? []).map(e => ({
    id: e.id,
    source: e.src,
    target: e.dst,
    animated: false,
    style: { stroke: '#a78bfa', strokeWidth: 2 },
  }))

  const onNodeClick = useCallback((_: any, node: Node) => {
    const topoNode = topo?.nodes.find(n => n.id === node.id)
    setSelectedNode(topoNode)
  }, [topo])

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 bg-white">
        <h2 className="text-xl font-semibold text-gray-800">Topology</h2>
        <select
          className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white"
          value={currentRunId}
          onChange={e => { setCurrentRunId(e.target.value); navigate(`/topology/${e.target.value}`) }}
        >
          {runs.map(r => (
            <option key={r.run_id} value={r.run_id}>{r.name} — {r.timestamp.slice(0, 10)}</option>
          ))}
        </select>
      </div>
      <div className="flex-1 relative">
        <ReactFlow
          nodes={flowNodes}
          edges={flowEdges}
          onNodeClick={onNodeClick}
          fitView
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#e5e7eb" gap={20} />
          <Controls />
        </ReactFlow>

        {selectedNode && (
          <div className="absolute top-4 right-4 w-80 bg-white border border-gray-200 rounded-xl shadow-lg p-4 max-h-[80vh] overflow-auto">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold text-gray-800">{selectedNode.role}</h3>
              <button onClick={() => setSelectedNode(null)} className="text-gray-400 hover:text-gray-600">
                <X size={16} />
              </button>
            </div>
            <div className="space-y-2 text-sm">
              <div><span className="text-gray-400">Status:</span> <span className={`font-medium`} style={{ color: statusColors[selectedNode.status] }}>{selectedNode.status}</span></div>
              <div><span className="text-gray-400">Kind:</span> {selectedNode.kind}</div>
              {selectedNode.confidence != null && <div><span className="text-gray-400">Confidence:</span> {(selectedNode.confidence * 100).toFixed(1)}%</div>}
              {selectedNode.cost && (
                <div>
                  <span className="text-gray-400">Cost:</span>
                  <pre className="mt-1 bg-gray-50 rounded p-2 text-xs overflow-auto">{JSON.stringify(selectedNode.cost, null, 2)}</pre>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
