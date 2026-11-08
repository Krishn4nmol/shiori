import { lazy, Suspense, useState, type ReactNode } from 'react'

import { OpenPdfContext, type PdfTarget } from './usePdfViewer'

// pdf.js is large: load the viewer only when a PDF is first opened.
const PdfViewer = lazy(() => import('./PdfViewer'))

export function PdfViewerProvider({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<PdfTarget | null>(null)
  return (
    <OpenPdfContext.Provider value={setTarget}>
      {children}
      {target && (
        <Suspense
          fallback={
            <div className="fixed inset-0 z-50 grid place-items-center bg-stone-900/40 text-white">
              Opening PDF…
            </div>
          }
        >
          {/* key: a new target starts fresh at its own page */}
          <PdfViewer
            key={`${target.documentId}-${target.page}-${target.highlight?.slice(0, 40)}`}
            target={target}
            onClose={() => setTarget(null)}
          />
        </Suspense>
      )}
    </OpenPdfContext.Provider>
  )
}
