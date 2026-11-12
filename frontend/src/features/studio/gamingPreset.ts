import { t } from '../../i18n'
import type { ImportOptions } from './types'

/** Fork addition: one-click defaults for gameplay streams (TikTok / Shorts highlights). */
export type ProductionPreset = 'general' | 'gaming'

const KEY = 'autoclip.import.preset.v1'

export const GAMING_PLATFORMS = ['tiktok', 'youtube_shorts'] as const

/** Instruction is a catalog key so the visible text follows the UI language. */
export const GAMING_INSTRUCTION_KEY = '游戏直播：挑选高光时刻——极限操作、大胜或翻车、主播搞笑或激动的反应、弹幕刷屏的瞬间。保留完整的铺垫和之后的反应。'

export function gamingOptions(current: ImportOptions): ImportOptions {
  return {
    ...current,
    goal: 'highlight',
    aspect: 'portrait',
    duration: 60,
    portrait_style: 'auto',
    instruction: current.instruction.trim() ? current.instruction : t(GAMING_INSTRUCTION_KEY),
  }
}

/** Leaving the preset restores automatic matching but keeps any instruction the user typed themselves. */
export function generalOptions(current: ImportOptions, defaults: ImportOptions): ImportOptions {
  const presetText = current.instruction === t(GAMING_INSTRUCTION_KEY)
  return { ...defaults, html_template: current.html_template, instruction: presetText ? '' : current.instruction }
}

export function readPreset(): ProductionPreset {
  try { return localStorage.getItem(KEY) === 'gaming' ? 'gaming' : 'general' } catch { return 'general' }
}

export function writePreset(value: ProductionPreset) {
  try { localStorage.setItem(KEY, value) } catch { /* preference is optional */ }
}
