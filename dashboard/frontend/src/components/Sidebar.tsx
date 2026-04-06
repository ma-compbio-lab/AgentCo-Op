import { NavLink } from 'react-router-dom'
import { LayoutDashboard, GitBranch, ScrollText, MessageSquare, Settings } from 'lucide-react'

const links = [
  { to: '/', icon: LayoutDashboard, label: 'Overview' },
  { to: '/topology', icon: GitBranch, label: 'Topology' },
  { to: '/logs', icon: ScrollText, label: 'Logs' },
  { to: '/chat', icon: MessageSquare, label: 'Chat' },
  { to: '/config', icon: Settings, label: 'Config' },
]

export function Sidebar() {
  return (
    <aside className="w-52 bg-white border-r border-gray-200 flex flex-col">
      <div className="px-5 py-5">
        <h1 className="text-lg font-bold text-purple-600">AgentCo-Op</h1>
      </div>
      <nav className="flex-1 px-3 space-y-1">
        {links.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                isActive
                  ? 'bg-purple-50 text-purple-700 border border-purple-200'
                  : 'text-gray-500 hover:bg-gray-100 hover:text-gray-700'
              }`
            }
          >
            <Icon size={18} />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="px-3 pb-4">
        <div className="bg-gray-50 rounded-lg px-3 py-2 text-xs">
          <div className="text-gray-400">Framework</div>
          <div className="text-gray-600 font-medium">AgentCo-Op v0.1.0</div>
        </div>
      </div>
    </aside>
  )
}
