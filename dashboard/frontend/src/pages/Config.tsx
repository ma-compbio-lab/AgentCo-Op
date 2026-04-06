import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { SkillInfo } from '../lib/types'
import { ChevronDown, ChevronRight } from 'lucide-react'

export function Config() {
  const [config, setConfig] = useState<any>(null)
  const [skills, setSkills] = useState<{ meta_skills: SkillInfo[]; agent_skills: SkillInfo[] } | null>(null)
  const [tab, setTab] = useState<'config' | 'meta' | 'agent'>('config')
  const [expandedSkill, setExpandedSkill] = useState<string | null>(null)

  useEffect(() => {
    api.getConfig().then(setConfig)
    api.getSkills().then(setSkills)
  }, [])

  return (
    <div className="p-6 space-y-6">
      <h2 className="text-xl font-semibold text-gray-800">Configuration</h2>

      <div className="flex gap-2 border-b border-gray-200">
        {[
          { key: 'config', label: 'Settings' },
          { key: 'meta', label: `Meta-Skills (${skills?.meta_skills.length ?? 0})` },
          { key: 'agent', label: `Agent-Skills (${skills?.agent_skills.length ?? 0})` },
        ].map(t => (
          <button key={t.key} onClick={() => setTab(t.key as any)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === t.key ? 'border-purple-600 text-purple-600' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'config' && config && (
        <div className="space-y-4">
          <div className="bg-white border border-gray-200 rounded-xl p-4">
            <h3 className="font-medium text-gray-700 mb-3">Base Configuration</h3>
            <pre className="bg-gray-50 rounded-lg p-4 text-sm text-gray-600 overflow-auto max-h-60">
              {JSON.stringify(config.base, null, 2)}
            </pre>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-white border border-gray-200 rounded-xl p-4">
              <h3 className="font-medium text-gray-700 mb-2">Available Models</h3>
              <div className="space-y-1">
                {(config.available_models ?? []).map((m: string) => (
                  <div key={m} className="text-sm text-gray-600 px-2 py-1 bg-gray-50 rounded">{m}</div>
                ))}
              </div>
            </div>
            <div className="bg-white border border-gray-200 rounded-xl p-4">
              <h3 className="font-medium text-gray-700 mb-2">Available Experiments</h3>
              <div className="space-y-1">
                {(config.available_experiments ?? []).map((e: string) => (
                  <div key={e} className="text-sm text-gray-600 px-2 py-1 bg-gray-50 rounded">{e}</div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {(tab === 'meta' || tab === 'agent') && (
        <div className="space-y-2">
          {(tab === 'meta' ? skills?.meta_skills : skills?.agent_skills)?.map(skill => (
            <div key={skill.name} className="bg-white border border-gray-200 rounded-xl overflow-hidden">
              <button onClick={() => setExpandedSkill(expandedSkill === skill.name ? null : skill.name)}
                className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-gray-50">
                {expandedSkill === skill.name ? <ChevronDown size={14} className="text-gray-400" /> : <ChevronRight size={14} className="text-gray-400" />}
                <div className="flex-1">
                  <div className="font-medium text-gray-800 text-sm">{skill.name}</div>
                  <div className="text-xs text-gray-500">{skill.description}</div>
                </div>
                <div className="flex gap-1 flex-wrap">
                  {skill.domain_tags.map(t => (
                    <span key={t} className="px-2 py-0.5 bg-purple-50 text-purple-600 rounded text-xs">{t}</span>
                  ))}
                </div>
              </button>
              {expandedSkill === skill.name && (
                <div className="px-4 pb-4 border-t border-gray-100">
                  <div className="flex gap-1 mt-2 mb-2 flex-wrap">
                    {skill.capability_tags.map(t => (
                      <span key={t} className="px-2 py-0.5 bg-blue-50 text-blue-600 rounded text-xs">{t}</span>
                    ))}
                  </div>
                  {skill.roles && skill.roles.length > 0 && (
                    <div className="mb-2">
                      <div className="text-xs font-medium text-gray-500 mb-1">Roles:</div>
                      {skill.roles.map((r: any, i: number) => (
                        <div key={i} className="text-xs text-gray-600 ml-2">- {r.role}: {r.description}</div>
                      ))}
                    </div>
                  )}
                  <pre className="bg-gray-50 rounded p-3 text-xs text-gray-600 whitespace-pre-wrap">{skill.body}</pre>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
