import 'react-pdf/dist/Page/TextLayer.css'

import { useCallback, useEffect, useRef, useState } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import type { TextContent } from 'pdfjs-dist/types/src/display/api'

import { api } from '../api'
import { escapeHtml, findHighlightedItems } from '../lib/highlight'
import type { PdfTarget } from './usePdfViewer'

// Must be set in the same module that renders <Document> (see react-pdf README).
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()

// Assets copied by vite.config.ts; cMaps are required for Japanese text.
const PDF_OPTIONS = {
  cMapUrl: '/pdfjs/cmaps/',
  cMapPacked: true,
  standardFontDataUrl: '/pdfjs/standard_fonts/',
  wasmUrl: '/pdfjs/wasm/',
}

const ZOOMS = [0.75, 1, 1.25, 1.5, 2]

type Props = { target: PdfTarget; onClose: () => void }

export default function PdfViewer({ target, onClose }: Props) {
  const [pageCount, setPageCount] = useState<number | null>(null)
  const [page, setPage] = useState(target.page)
  const [zoom, setZoom] = useState(1)
  const [width, setWidth] = useState(700)
  const [error, setError] = useState<string | null>(null)
  const body = useRef<HTMLDivElement>(null)

  const pageEnd = target.pageEnd ?? target.page
  const highlightOn = Boolean(target.highlight) && page >= target.page && page <= pageEnd
  // Items of the current page that belong to the cited passage, found once its text loads.
  const [marked, setMarked] = useState<{ page: number; items: Set<number> } | null>(null)

  // Fit the page to the panel width.
  useEffect(() => {
    const el = body.current
    if (!el) return
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width) - 32))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  // Esc closes, arrow keys turn pages.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
      if (e.key === 'ArrowRight') setPage((p) => Math.min(p + 1, pageCount ?? p))
      if (e.key === 'ArrowLeft') setPage((p) => Math.max(p - 1, 1))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, pageCount])

  const onText = useCallback(
    ({ items }: TextContent) => {
      if (!highlightOn || !target.highlight) return
      // Marked-content entries carry no text but still count in itemIndex.
      const texts = items.map((item) => ('str' in item ? item.str : ''))
      setMarked({ page, items: findHighlightedItems(texts, target.highlight) })
    },
    [highlightOn, page, target.highlight],
  )

  const pageMarks = marked?.page === page ? marked.items : null
  const renderText = useCallback(
    ({ str, itemIndex }: { str: string; itemIndex: number }) => {
      const safe = escapeHtml(str)
      return pageMarks?.has(itemIndex) ? `<mark class="shiori-hl">${safe}</mark>` : safe
    },
    [pageMarks],
  )

  const scrollToHighlight = useCallback(() => {
    body.current?.querySelector('mark.shiori-hl')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [])

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={target.title}>
      <button type="button" aria-label="Close PDF" className="absolute inset-0 bg-stone-900/40" onClick={onClose} />
      <div className="relative flex h-full w-full max-w-4xl flex-col bg-stone-100 shadow-2xl">
        <header className="flex flex-wrap items-center gap-3 border-b border-stone-200 bg-white px-4 py-2.5 text-sm">
          <div className="min-w-0 flex-1">
            <p className="truncate font-semibold text-stone-900" title={target.title}>
              {target.title}
            </p>
            {target.highlight && (
              <p className="text-xs text-stone-500">
                Cited passage highlighted on {target.page === pageEnd ? `p. ${target.page}` : `pp. ${target.page}–${pageEnd}`}
              </p>
            )}
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setPage((p) => Math.max(p - 1, 1))}
              disabled={page <= 1}
              className="rounded px-2 py-1 hover:bg-stone-100 disabled:opacity-30"
              aria-label="Previous page"
            >
              ‹
            </button>
            <span className="tabular-nums text-stone-600">
              {page} / {pageCount ?? '…'}
            </span>
            <button
              type="button"
              onClick={() => setPage((p) => Math.min(p + 1, pageCount ?? p))}
              disabled={pageCount !== null && page >= pageCount}
              className="rounded px-2 py-1 hover:bg-stone-100 disabled:opacity-30"
              aria-label="Next page"
            >
              ›
            </button>
          </div>
          <select
            value={zoom}
            onChange={(e) => setZoom(Number(e.target.value))}
            className="rounded border border-stone-300 bg-white px-1.5 py-1 text-xs"
            aria-label="Zoom"
          >
            {ZOOMS.map((z) => (
              <option key={z} value={z}>
                {Math.round(z * 100)}%
              </option>
            ))}
          </select>
          <a
            href={api.fileUrl(target.documentId, page)}
            target="_blank"
            rel="noreferrer"
            className="text-xs text-stone-600 hover:underline"
          >
            Open in new tab
          </a>
          <button
            type="button"
            onClick={onClose}
            className="rounded px-2 py-1 text-lg leading-none text-stone-500 hover:bg-stone-100"
            aria-label="Close"
          >
            ×
          </button>
        </header>

        <div ref={body} className="flex-1 overflow-auto p-4">
          {error ? (
            <p className="text-red-600">Could not open this PDF: {error}</p>
          ) : (
            <Document
              file={api.fileUrl(target.documentId)}
              options={PDF_OPTIONS}
              onLoadSuccess={({ numPages }) => {
                setPageCount(numPages)
                setPage((p) => Math.min(Math.max(p, 1), numPages))
              }}
              onLoadError={(err) => setError(err.message)}
              loading={<p className="text-stone-500">Loading PDF…</p>}
              className="flex justify-center"
            >
              <Page
                key={`${page}-${pageMarks ? 'marked' : 'plain'}`}
                pageNumber={page}
                width={Math.max(200, width * zoom)}
                renderAnnotationLayer={false}
                customTextRenderer={renderText}
                onGetTextSuccess={onText}
                onRenderTextLayerSuccess={scrollToHighlight}
                className="shadow-md"
                loading={<p className="text-stone-500">Rendering page…</p>}
              />
            </Document>
          )}
        </div>
      </div>
    </div>
  )
}
