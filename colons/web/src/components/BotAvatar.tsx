import type { AvatarInfo } from '../lib/types'

/** The Colons identity stays the same; only its circular surface changes. */
export function BotAvatar({ avatar, size = 32, title }: { avatar?: AvatarInfo | null; size?: number; title?: string }) {
  const background = avatar?.colors[0] || '#ffffff'
  return <span className="inline-flex shrink-0 flex-col items-center justify-center rounded-full border border-black/10" title={title}
    style={{ width: size, height: size, background, gap: size * 0.07 }}>
    {[0, 1].map((dot) => <span key={dot} style={{ width: size * 0.19, height: size * 0.19, background: '#101827', borderRadius: '50%' }} />)}
  </span>
}
