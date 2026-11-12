import api from '../../services/api'

/** Fork (Clipper merge): settings for the merged Project Clipper engine. */
export type ClipperSettings = {
  channels: string; region: string; category: string
  whisper_cpp_enabled: boolean; whisper_cpp_binary: string; whisper_cpp_model: string; whisper_language: string
  kamus: string; claude_model: 'sonnet' | 'opus' | 'haiku'; claude_bridge: boolean
  youtube_api_key_set: boolean; youtube_api_key_tail: string
}
export type ClipperStatus = {
  settings: ClipperSettings
  claude: { installed: boolean; running: boolean; base_url: string; models: string[]; error: string }
  whisper_cpp: { ready: boolean; vad: boolean }
}
export type ScannedVideo = {
  video_id: string; url: string; title: string; channel: string; published_at: string; duration_s: number
  views: number; subscribers: number | null; was_live: boolean; score: number; views_per_hour: number
  velocity_source: string; views_per_subscriber: number | null
}
export type ScanResult = { videos: ScannedVideo[]; candidates: number; kept: number; units_used: number }

export const clipperApi = {
  status: (): Promise<ClipperStatus> => api.get('/studio/clipper/settings'),
  save: (body: Partial<ClipperSettings> & { youtube_api_key?: string }): Promise<ClipperStatus> => api.post('/studio/clipper/settings', body),
  scan: (body: { trending: boolean; use_channels: boolean; max_age_days?: number; min_duration?: number }): Promise<ScanResult> =>
    api.post('/studio/clipper/scan', body, { timeout: 120000 }),
}
