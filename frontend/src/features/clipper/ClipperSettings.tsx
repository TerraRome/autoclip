import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Select, Switch, message } from 'antd'
import { t } from '../../i18n'
import { Btn, Row, Section } from '../../ui'
import { clipperApi, type ClipperSettings as Settings, type ClipperStatus } from './clipperApi'

/** Fork (Clipper merge): Claude subscription, whisper.cpp + kamus, YouTube Data API. */
export default function ClipperSettings() {
  useTranslation()
  const [status, setStatus] = useState<ClipperStatus | null>(null)
  const [draft, setDraft] = useState<Partial<Settings>>({})
  const [apiKey, setApiKey] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    clipperApi.status().then(next => { setStatus(next); setDraft(next.settings) }).catch(e => setError(String(e?.message || e)))
  }, [])
  const set = <K extends keyof Settings>(key: K, value: Settings[K]) => setDraft(current => ({ ...current, [key]: value }))
  const save = async () => {
    setSaving(true)
    try {
      const next = await clipperApi.save({ ...draft, ...(apiKey ? { youtube_api_key: apiKey } : {}) })
      setStatus(next); setDraft(next.settings); setApiKey('')
      void message.success(t('已保存'))
    } catch (e) { void message.error(String((e as Error)?.message || e)) } finally { setSaving(false) }
  }
  const copy = (text: string) => { void navigator.clipboard?.writeText(text).then(() => message.success(t('已复制'))).catch(() => undefined) }
  if (!status) return <Section title="Clipper">{error ? <p className="ac-sub">{error}</p> : <p className="ac-sub">{t('加载中…')}</p>}</Section>
  const claude = status.claude
  return <>
    <Section title={t('Claude 订阅')} description={t('用 Claude Code（claude -p）分析和写钩子，计入你的 Claude 订阅额度，而不是 API 费用。')}>
      <div className="ac-rows">
        <Row label={t('状态')} hint={claude.installed ? (claude.running ? t('Claude Code 已找到，本地接口正在运行。') : t('Claude Code 已找到，但本地接口没有启动：{{error}}', { error: claude.error || '-' })) : t('没有找到 claude 命令。安装 Claude Code 后在终端运行一次 claude 并登录。')}>
          <span className={`ac-pill ${claude.running ? 'is-ok' : ''}`}>{claude.running ? t('运行中') : t('未运行')}</span>
        </Row>
        <Row label={t('作为分析模型使用')} hint={t('在「服务商与模型」里添加「自定义兼容接口」，地址填下面这个，API 密钥随便填，模型填 sonnet、opus 或 haiku。只能读文字，看画面请另选多模态模型。')} wide>
          <div className="ac-clipper-inline"><code>{claude.base_url}</code><Btn size="sm" onClick={() => copy(claude.base_url)}>{t('复制')}</Btn></div>
        </Row>
        <Row label={t('爆点钩子用的模型')} control={<Select value={draft.claude_model} style={{ width: 160 }} onChange={v => set('claude_model', v)} options={claude.models.map(m => ({ value: m, label: m }))} />} />
        <Row label={t('本地接口')} hint={t('关闭后 127.0.0.1:11435 不再提供 Claude 接口。')} control={<Switch checked={!!draft.claude_bridge} onChange={v => set('claude_bridge', v)} />} />
      </div>
    </Section>

    <Section title={t('whisper.cpp（显卡转写）')} description={t('有显卡时比默认的本地 Whisper 快很多。设置好后，本地转写会改用它，出错时自动退回默认方式。')}>
      <div className="ac-rows">
        <Row label={t('启用 whisper.cpp')} hint={status.whisper_cpp.ready ? (status.whisper_cpp.vad ? t('已就绪，含 Silero VAD 时间校准。') : t('已就绪。把 whisper-vad-speech-segments 放在 whisper-cli 旁、ggml-silero 模型放在模型旁，字幕时间会更准。')) : t('还没就绪：请填写两个存在的文件路径。')} control={<Switch checked={!!draft.whisper_cpp_enabled} onChange={v => set('whisper_cpp_enabled', v)} />} />
        <Row label={t('whisper-cli 路径')} hint={t('例如 H:\\Project AI\\clipper\\tools\\whisper\\whisper-cli.exe')} wide>
          <input className="ac-input" value={draft.whisper_cpp_binary || ''} onChange={e => set('whisper_cpp_binary', e.target.value)} placeholder="...\\whisper-cli.exe" />
        </Row>
        <Row label={t('模型文件路径')} hint={t('例如 ggml-large-v3-turbo-q5_0.bin')} wide>
          <input className="ac-input" value={draft.whisper_cpp_model || ''} onChange={e => set('whisper_cpp_model', e.target.value)} placeholder="...\\ggml-large-v3-turbo-q5_0.bin" />
        </Row>
        <Row label={t('识别语言')} control={<Select value={draft.whisper_language} style={{ width: 160 }} onChange={v => set('whisper_language', v)} options={[{ value: 'auto', label: t('自动检测') }, { value: 'id', label: 'Bahasa Indonesia' }, { value: 'en', label: 'English' }, { value: 'ja', label: '日本語' }, { value: 'zh', label: '中文' }]} />} />
        <Row label={t('纠错词典')} hint={t('每行一条。「错1, 错2 => 正确」会替换整词；只写一个词则作为拼写提示交给 whisper（人名、游戏术语）。')} stack>
          <textarea className="ac-input ac-clipper-area" rows={6} value={draft.kamus || ''} onChange={e => set('kamus', e.target.value)} placeholder={'IJU, ijau => hijau\nKaela'} />
        </Row>
      </div>
    </Section>

    <Section title={t('YouTube Data API')} description={t('用于从关注的频道和热门榜里找爆款视频。每次约 3–10 个配额单位（每天 10,000）。')}>
      <div className="ac-rows">
        <Row label={t('API 密钥')} hint={status.settings.youtube_api_key_set ? t('已保存，结尾 {{tail}}。留空保持不变。', { tail: status.settings.youtube_api_key_tail }) : t('在 Google Cloud 控制台为 YouTube Data API v3 创建。只保存在本机。')} wide>
          <input className="ac-input" type="password" value={apiKey} onChange={e => setApiKey(e.target.value)} placeholder="AIza…" autoComplete="off" />
        </Row>
        <Row label={t('关注的频道')} hint={t('每行一个 @handle、频道 ID 或频道链接。')} stack>
          <textarea className="ac-input ac-clipper-area" rows={5} value={draft.channels || ''} onChange={e => set('channels', e.target.value)} placeholder={'@channel\nUCxxxxxxxxxxxxxxxxxxxxxx'} />
        </Row>
        <Row label={t('热门榜地区')} control={<input className="ac-input" style={{ width: 80 }} value={draft.region || ''} onChange={e => set('region', e.target.value.toUpperCase())} maxLength={2} />} />
        <Row label={t('热门榜分类')} hint={t('20 = 游戏；留空 = 全部分类。')} control={<input className="ac-input" style={{ width: 80 }} value={draft.category || ''} onChange={e => set('category', e.target.value)} maxLength={3} />} />
      </div>
    </Section>
    <div className="ac-clipper-save"><Btn variant="cta" loading={saving} onClick={() => void save()}>{t('保存设置')}</Btn></div>
  </>
}
