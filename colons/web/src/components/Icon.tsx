import { Lightbulb, FileText, CodeXml, Sprout, LayoutDashboard, History, CirclePlus } from 'lucide-react'
export type IconName = 'brainstorm' | 'write' | 'build' | 'plan' | 'dashboard' | 'history' | 'plus'
const icons = { brainstorm: Lightbulb, write: FileText, build: CodeXml, plan: Sprout, dashboard: LayoutDashboard, history: History, plus: CirclePlus }
export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  const Component = icons[name]
  return <Component size={size} strokeWidth={1.6} aria-hidden="true" />
}
