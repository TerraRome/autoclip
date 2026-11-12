import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t } from '../../i18n'
import { Btn } from '../../ui'
import { studioApi, errorText } from './api'
import type { ImportOptions } from './types'

/** Fork (Clipper merge): find the viral moments of a YouTube link, then make one project per moment. */
export type ViralMoment = {
  start: number; end: number; peak_time: number; heat: number
  reason?: { summary?: string; details?: string[] }
  transcript?: { t: number; text: string }[]
}
export type ViralResult = {
  video_id: string; url: string; title: string; channel: string; duration_s: number
  heatmap_available: boolean; source: string; moments: ViralMoment[]
}

export function clock(sec: number): string {
  const s = Math.max(0, Math.round(sec))
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}` : `${m}:${String(r).padStart(2, '0')}`
}

/** One import form per moment: only that range is downloaded. */
export function momentForm(result: ViralResult, moment: ViralMoment, options: ImportOptions, platforms: string[], browser: string): FormData {
  const body = new FormData()
  body.append('url', result.url)
  body.append('clip_start', String(Math.max(0, moment.start)))
  body.append('clip_end', String(moment.end))
  body.append('name', `${result.title.slice(0, 80)} · ${clock(moment.start)}`)
  body.append('goal', options.goal === 'auto' ? 'highlight' : options.goal)
  body.append('language', options.language)
  body.append('instruction', options.instruction)
  platforms.forEach(platform => body.append('platforms', platform))
  body.append('auto_start', 'true')
  body.append('portrait_style', options.portrait_style || 'auto')
  if (options.aspect) body.append('aspect', options.aspect)
  if (options.duration) body.append('duration', String(options.duration))
  if (browser) body.append('browser', browser)
  return body
}

export default function ViralMomentsPanel({ url, browser, platforms, options, disabled, onImported }: {
  url: string; browser: string; platforms: string[]; options: ImportOptions; disabled?: boolean
  onImported: () => Promise<void>
}) {
  useTranslation()
  const [state, setState] = useState<'idle' | 'running' | 'done' | 'failed' | 'creating'>('idle')
  const [message, setMessage] = useState('')
  const [result, setResult] = useState<ViralResult | null>(null)
  const [picked, setPicked] = useState<number[]>([])
  const [created, setCreated] = useState(0)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])
  useEffect(() => { setState('idle'); setResult(null); setPicked([]); setCreated(0) }, [url])

  const poll = (jobId: string) => {
    timer.current = setTimeout(async () => {
      try {
        const job = await studioApi.viralStatus(jobId)
        if (job.status === 'running') { setMessage(job.message || ''); poll(jobId); return }
        if (job.status === 'failed' || !job.result) { setState('failed'); setMessage(job.error || ''); return }
        setResult(job.result)
        setPicked(job.result.moments.map((_, index) => index))
        setState('done')
      } catch (error) { setState('failed'); setMessage(t(errorText(error))) }
    }, 1500)
  }
  const find = async () => {
    setState('running'); setMessage(''); setResult(null); setCreated(0)
    try { poll((await studioApi.viralStart(url, browser || undefined)).job_id) }
    catch (error) { setState('failed'); setMessage(t(errorText(error))) }
  }
  const create = async () => {
    if (!result) return
    setState('creating')
    let count = 0
    try {
      for (const index of picked) {
        await studioApi.import(momentForm(result, result.moments[index], options, platforms, browser))
        count += 1
        setCreated(count)
      }
      setState('done')
      void onImported().catch(() => undefined)
    } catch (error) { setState('failed'); setMessage(t(errorText(error))) }
  }
  const toggle = (index: number) => setPicked(list => list.includes(index) ? list.filter(i => i !== index) : [...list, index].sort((a, b) => a - b))

  return <div className="ac-viral">
    <div className="ac-viral-head">
      <div><strong>{t('找爆点')}</strong><small>{t('从热力图、直播弹幕和评论里的时间戳找出最精彩的片段，只下载这些片段。')}</small></div>
      <Btn size="sm" disabled={disabled || state === 'running' || state === 'creating'} loading={state === 'running'} onClick={() => void find()}>{result ? t('再找一次') : t('找爆点')}</Btn>
    </div>
    {state === 'running' && <p className="studio-muted" role="status">{message || t('正在寻找爆点…')}</p>}
    {state === 'failed' && <p className="ac-viral-error" role="alert">{t('查找爆点失败')}{message ? `: ${message}` : ''}</p>}
    {result && <>
      <p className="studio-muted">{result.title} · {t('来源：{{source}}', { source: result.source })}</p>
      {result.moments.length === 0 && <p className="studio-muted">{t('没有找到明显的爆点。')}</p>}
      <ul className="ac-viral-list">
        {result.moments.map((moment, index) => <li key={`${moment.start}-${moment.end}`} className={picked.includes(index) ? 'is-picked' : ''}>
          <label>
            <input type="checkbox" checked={picked.includes(index)} disabled={state === 'creating'} onChange={() => toggle(index)} />
            <span className="ac-viral-time">{clock(moment.start)}–{clock(moment.end)}</span>
            <span className="ac-viral-reason">{moment.reason?.summary || ''}</span>
          </label>
          {!!moment.transcript?.length && <details><summary>{t('查看台词')}</summary><p>{moment.transcript.map(line => line.text).join(' ')}</p></details>}
        </li>)}
      </ul>
      {result.moments.length > 0 && <div className="studio-row ac-viral-foot">
        {created > 0 && state !== 'creating' && <span className="studio-muted" role="status">{t('已创建 {{count}} 个项目，正在后台制作。', { count: created })}</span>}
        <Btn variant="cta" size="sm" disabled={disabled || !picked.length || state === 'creating'} loading={state === 'creating'} onClick={() => void create()}>{t('为选中的 {{count}} 个片段生成', { count: picked.length })}</Btn>
      </div>}
    </>}
  </div>
}
