import { useMemo } from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({ breaks: true, gfm: true })

export function Markdown({ content }: { content: string }) {
  const html = useMemo(() => {
    if (!content) return ''
    const raw = marked.parse(content, { async: false }) as string
    return DOMPurify.sanitize(raw, { ADD_ATTR: ['target'] })
  }, [content])

  return <div className="md-content" dangerouslySetInnerHTML={{ __html: html }} />
}
