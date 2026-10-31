/** The ScreenMate mark (public/logo.svg): film strip → download arrow → library tray. */
export default function AppLogo({ className = "h-8 w-8" }: { className?: string }) {
  return <img src="/logo.svg" alt="" aria-hidden width={64} height={64} className={`shrink-0 ${className}`} />;
}
