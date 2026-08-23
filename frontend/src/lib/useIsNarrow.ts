import { useCallback, useSyncExternalStore } from 'react'

/**
 * True while the viewport is under `px` wide.
 *
 * Tailwind handles nearly everything responsive here, and should - a class is
 * cheaper and does not re-render. This exists for the cases where a *value*
 * rather than a style has to change: chart axis widths, label truncation
 * lengths, panel heights. Recharts takes those as numbers, not classes.
 *
 * useSyncExternalStore rather than useState + useEffect: matchMedia already is
 * an external store, so subscribing to it directly means the first paint is
 * correct rather than flashing the desktop layout and then correcting, and
 * there is no effect writing state on mount.
 */
export function useIsNarrow(px: number): boolean {
  const query = `(max-width: ${px - 1}px)`

  const subscribe = useCallback(
    (onChange: () => void) => {
      if (typeof window === 'undefined' || !window.matchMedia) return () => {}
      const mql = window.matchMedia(query)
      mql.addEventListener('change', onChange)
      return () => mql.removeEventListener('change', onChange)
    },
    [query],
  )

  const snapshot = useCallback(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return false
    return window.matchMedia(query).matches
  }, [query])

  // Server snapshot: no viewport, so assume the wide layout.
  return useSyncExternalStore(subscribe, snapshot, () => false)
}
