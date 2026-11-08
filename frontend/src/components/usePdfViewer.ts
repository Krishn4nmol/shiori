import { createContext, useContext } from 'react'

export type PdfTarget = {
  documentId: string
  title: string
  page: number
  pageEnd?: number
  /** Passage text to highlight on pages page..pageEnd */
  highlight?: string
}

export const OpenPdfContext = createContext<(target: PdfTarget) => void>(() => {})

/** Open a paper in the in-app viewer, optionally highlighting a cited passage. */
export function useOpenPdf() {
  return useContext(OpenPdfContext)
}
