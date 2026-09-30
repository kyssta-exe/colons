import type { AvatarInfo } from '../lib/types'

export type AvatarState = 'idle' | 'resting' | 'thinking' | 'working' | 'speaking' | 'waiting'

/** A small living colon, with motion driven by real agent activity. */
export function BotAvatar({ avatar, size = 32, title, state = 'idle' }: {
  avatar?: AvatarInfo | null; size?: number; title?: string; state?: AvatarState
}) {
  const background = avatar?.colors[0] || '#ffffff'
  const rgb = /^#[a-f\d]{6}$/i.test(background) ? [1, 3, 5].map((start) => parseInt(background.slice(start, start + 2), 16)) : [255, 255, 255]
  const foreground = rgb[0] * 0.299 + rgb[1] * 0.587 + rgb[2] * 0.114 < 95 ? '#ffffff' : '#101827'
  return <span className={`colons-avatar colons-avatar--${state} inline-flex shrink-0 flex-col items-center justify-center rounded-full border border-black/10`}
    title={title} role="img" aria-label={`${avatar?.name || 'Colons'} avatar${state !== 'idle' && state !== 'resting' ? `, ${state}` : ''}`} data-avatar-state={state}
    style={{ width: size, height: size, background, gap: size * 0.07 }}>
    {[0, 1].map((dot) => <span key={dot} className={`colons-avatar-dot colons-avatar-dot--${dot}`}
      style={{ width: size * 0.19, height: size * 0.19, background: foreground, borderRadius: '50%' }} />)}
  </span>
}
