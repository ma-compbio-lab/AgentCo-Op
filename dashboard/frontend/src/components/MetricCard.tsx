interface MetricCardProps {
  label: string
  value: string | number
  color: 'purple' | 'green' | 'blue' | 'amber'
}

const colorMap = {
  purple: 'text-purple-600',
  green: 'text-green-600',
  blue: 'text-blue-600',
  amber: 'text-amber-600',
}

export function MetricCard({ label, value, color }: MetricCardProps) {
  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4">
      <div className="text-xs font-medium text-gray-400 uppercase tracking-wider">{label}</div>
      <div className={`text-2xl font-bold mt-1 ${colorMap[color]}`}>{value}</div>
    </div>
  )
}
