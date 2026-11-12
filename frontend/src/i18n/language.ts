export const supportedLanguages = ['id', 'zh', 'en', 'ja', 'ko', 'es', 'pt', 'ru', 'fr'] as const
export type Language = typeof supportedLanguages[number]
/** Fork default: first launch (no saved choice) opens in Bahasa Indonesia. */
export const DEFAULT_LANGUAGE: Language = 'id'
export type LanguagePreference = Language | 'system'
export const LANGUAGE_STORAGE_KEY = 'autoclip.language'

export function resolveLanguage(preference: string | null, browserLanguages: readonly string[] = []): Language {
  if (supportedLanguages.includes(preference as Language)) return preference as Language
  for (const tag of browserLanguages) {
    const base = tag.toLowerCase().replace('_', '-').split('-')[0]
    if (supportedLanguages.includes(base as Language)) return base as Language
  }
  return 'en'
}

export function readPreference(): LanguagePreference {
  try {
    const value = localStorage.getItem(LANGUAGE_STORAGE_KEY)
    if (supportedLanguages.includes(value as Language)) return value as Language
    if (value === 'system') return 'system'
    if (value === null) return DEFAULT_LANGUAGE
  } catch { /* Storage may be unavailable. */ }
  return 'system'
}
