import { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import type { Tone } from '../lib/format'
import { toneClasses } from '../lib/format'

/* --------------------------------------------------------------- CountUp */
/** Counts to a real value. Used only on figures the engine actually computed. */
export function CountUp({
  value,
  format,
  duration = 900,
  delay = 0,
}: {
  value: number
  format: (n: number) => string
  duration?: number
  delay?: number
}) {
  const [shown, setShown] = useState(0)
  const raf = useRef<number>(0)

  useEffect(() => {
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduce || duration === 0) {
      setShown(value)
      return
    }
    let start = 0
    const tick = (t: number) => {
      if (!start) start = t
      const elapsed = t - start - delay
      if (elapsed < 0) {
        raf.current = requestAnimationFrame(tick)
        return
      }
      const p = Math.min(1, elapsed / duration)
      // ease-out cubic: fast settle, no bounce - this is a readout, not a toy
      setShown(value * (1 - Math.pow(1 - p, 3)))
      if (p < 1) raf.current = requestAnimationFrame(tick)
      else setShown(value)
    }
    raf.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf.current)
  }, [value, duration, delay])

  return <>{format(shown)}</>
}

/* ------------------------------------------------------------------- Tag */
export function Tag({
  tone = 'slate',
  children,
  title,
}: {
  tone?: Tone
  children: React.ReactNode
  title?: string
}) {
  const t = toneClasses[tone]
  return (
    <span
      title={title}
      className={`label inline-flex items-center gap-1 rounded-[2px] border px-1.5 py-[1px] ${t.bg} ${t.border} ${t.text}`}
      style={{ letterSpacing: '0.07em' }}
    >
      {children}
    </span>
  )
}

/* ----------------------------------------------------------------- Meter */
/** A confidence readout. Ticked at 0.6 and 0.85 - the thresholds that decide
 *  whether the engine auto-resolves or asks a person. */
export function Meter({ value, tone }: { value: number; tone: Tone }) {
  const t = toneClasses[tone]
  return (
    <div className="flex items-center gap-2">
      <div className="relative h-[6px] w-16 overflow-hidden rounded-[1px] bg-bar">
        <motion.div
          className="absolute inset-y-0 left-0"
          style={{ background: t.stroke }}
          initial={{ width: 0 }}
          animate={{ width: `${Math.max(2, value * 100)}%` }}
          transition={{ duration: 0.5, ease: 'easeOut' }}
        />
        <div className="absolute inset-y-0 left-[60%] w-px bg-sheet/70" />
        <div className="absolute inset-y-0 left-[85%] w-px bg-sheet/70" />
      </div>
      <span className={`num text-[11px] ${t.text}`}>{value.toFixed(2)}</span>
    </div>
  )
}

/* ------------------------------------------------------------------ Stat */
export function Stat({
  label,
  children,
  hint,
  size = 'md',
}: {
  label: string
  children: React.ReactNode
  hint?: string
  size?: 'sm' | 'md' | 'lg' | 'xl'
}) {
  const sizes = {
    sm: 'text-[17px]',
    md: 'text-[clamp(19px,4.4vw,24px)]',
    lg: 'text-[clamp(24px,6vw,34px)]',
    xl: 'text-[clamp(38px,11vw,58px)] leading-[0.95]',
  }
  return (
    // min-w-0 so a long figure shrinks rather than widening its grid column.
    <div className="min-w-0">
      <div className="label">{label}</div>
      <div className={`num mt-1 font-medium ${sizes[size]}`}>{children}</div>
      {hint && <div className="mt-1 text-[11px] leading-snug text-slate">{hint}</div>}
    </div>
  )
}

/* ---------------------------------------------------------------- Panel */
export function Panel({
  title,
  right,
  children,
  className = '',
}: {
  title?: string
  right?: React.ReactNode
  children: React.ReactNode
  className?: string
}) {
  return (
    // min-w-0 is load-bearing. A grid or flex item defaults to `min-width:auto`,
    // meaning it refuses to shrink below its content - so one wide table inside
    // a panel pushes the panel past its column and the whole page scrolls
    // sideways, even though the table has its own scroll container. Panels are
    // laid out in grids all over this app, so the fix belongs here rather than
    // at each call site.
    <section className={`sheet min-w-0 ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between border-b border-rule px-3 py-2">
          {title && <h2 className="label">{title}</h2>}
          {right}
        </header>
      )}
      {children}
    </section>
  )
}

/* ----------------------------------------------------------- SidePanel */
/**
 * The detail shell used by the exception and provenance panels.
 *
 * Two different things depending on room:
 *
 *   lg and up   a sticky column beside the list, which is what you want when
 *               you are comparing a detail against the rows around it
 *   below lg    a bottom drawer over a backdrop
 *
 * The drawer matters more than it looks. Stacked in the flow, a detail panel
 * lands *below* twenty rows of list - you tap a row and the thing you asked
 * for is off-screen, so it reads as nothing having happened.
 *
 * Escape closes it, the backdrop closes it, and while the drawer is open the
 * body cannot scroll underneath it.
 */
export function SidePanel({
  onClose,
  label,
  children,
}: {
  onClose: () => void
  label: string
  children: React.ReactNode
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // Only lock the body while the drawer is actually covering it. At lg the
  // panel is an ordinary column and locking would strand the reader.
  useEffect(() => {
    const drawer = window.matchMedia('(max-width: 1023px)')
    if (!drawer.matches) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [])

  return (
    <>
      <motion.div
        className="fixed inset-0 z-30 bg-ink/25 lg:hidden"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.16 }}
        onClick={onClose}
        aria-hidden="true"
      />
      <motion.aside
        role="dialog"
        aria-modal="false"
        aria-label={label}
        initial={{ opacity: 0, y: 24, x: 0 }}
        animate={{ opacity: 1, y: 0, x: 0 }}
        exit={{ opacity: 0, y: 24, x: 0 }}
        transition={{ duration: 0.2, ease: 'easeOut' }}
        className="sheet fixed inset-x-0 bottom-0 z-40 max-h-[85vh] overflow-y-auto rounded-b-none lg:sticky lg:inset-auto lg:top-4 lg:z-auto lg:max-h-[calc(100vh-2rem)] lg:rounded-[3px]"
      >
        {children}
      </motion.aside>
    </>
  )
}

/* ------------------------------------------------------------- Skeleton */
export function Pulse({ className = '' }: { className?: string }) {
  return (
    <motion.div
      className={`rounded-[2px] bg-bar ${className}`}
      animate={{ opacity: [0.45, 0.85, 0.45] }}
      transition={{ duration: 1.4, repeat: Infinity, ease: 'easeInOut' }}
    />
  )
}
