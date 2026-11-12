import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { t } from '../../i18n'
import { Btn } from '../../ui'
import { clipperApi, type ScannedVideo } from './clipperApi'

function compact(n: number | null | undefined): string {
  if (n == null) return '-'
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`
  return String(Math.round(n))
}

/** Fork (Clipper merge): rank viral videos from saved channels and trending, then pick one. */
export default function ViralScanPanel({ onPick, disabled }: { onPick: (url: string) => void; disabled?: boolean }) {
  useTranslation()
  const [videos, setVideos] = useState<ScannedVideo[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const scan = async (trending: boolean, useChannels: boolean) => {
    setBusy(true); setError('')
    try {
      const result = await clipperApi.scan({ trending, use_channels: useChannels })
      setVideos(result.videos)
      setNote(t('{{kept}} dari {{candidates}} video lolos filter · kuota ~{{units}} unit', { kept: result.kept, candidates: result.candidates, units: result.units_used }))
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } }; message?: string })
      setError(detail?.response?.data?.detail || detail?.message || String(e))
    } finally { setBusy(false) }
  }
  return <details className="ac-viral ac-scan">
    <summary><strong>{t('Temukan video viral')}</strong><small>{t('Dari channel yang kamu pantau dan trending YouTube (butuh YouTube Data API key di Pengaturan → Clipper).')}</small></summary>
    <div className="studio-row ac-scan-actions">
      <Btn size="sm" disabled={disabled || busy} loading={busy} onClick={() => void scan(true, true)}>{t('Channel + trending')}</Btn>
      <Btn size="sm" disabled={disabled || busy} onClick={() => void scan(false, true)}>{t('Channel saja')}</Btn>
      <Btn size="sm" disabled={disabled || busy} onClick={() => void scan(true, false)}>{t('Trending saja')}</Btn>
    </div>
    {error && <p className="ac-viral-error" role="alert">{error}</p>}
    {note && <p className="studio-muted">{note}</p>}
    {videos && videos.length === 0 && <p className="studio-muted">{t('Tidak ada video yang lolos filter (durasi ≥ 10 menit, umur ≤ 7 hari).')}</p>}
    {videos && videos.length > 0 && <ul className="ac-viral-list">
      {videos.map(video => <li key={video.video_id}>
        <div className="ac-scan-row">
          <span className="ac-viral-time">{video.score.toFixed(0)}</span>
          <div className="ac-scan-text">
            <b>{video.title}</b>
            <small>{video.channel} · {compact(video.views_per_hour)}/{t('jam')}{video.velocity_source === 'snapshot' ? '*' : ''} · {video.views_per_subscriber != null ? `${video.views_per_subscriber.toFixed(1)}× subs` : ''}{video.was_live ? ' · live' : ''}</small>
          </div>
          <Btn size="sm" disabled={disabled} onClick={() => onPick(video.url)}>{t('Pakai')}</Btn>
        </div>
      </li>)}
    </ul>}
  </details>
}
