import { useRef, useState } from 'react'

import { api } from '../api'

type Props = { onUploaded: () => void }

export function UploadZone({ onUploaded }: Props) {
  const input = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [errors, setErrors] = useState<string[]>([])

  async function upload(files: FileList | File[]) {
    const pdfs = Array.from(files)
    setErrors([])
    for (const file of pdfs) {
      if (!file.name.toLowerCase().endsWith('.pdf')) {
        setErrors((e) => [...e, `${file.name}: only PDF files are supported`])
        continue
      }
      setBusy(file.name)
      try {
        await api.uploadDocument(file)
        onUploaded()
      } catch (err) {
        setErrors((e) => [...e, `${file.name}: ${(err as Error).message}`])
      }
    }
    setBusy(null)
  }

  return (
    <div>
      <button
        type="button"
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          void upload(e.dataTransfer.files)
        }}
        disabled={busy !== null}
        className={`w-full rounded-xl border-2 border-dashed px-6 py-10 text-center transition ${
          dragging
            ? 'border-shiori-500 bg-shiori-50'
            : 'border-stone-300 bg-white hover:border-shiori-500'
        } disabled:cursor-wait disabled:opacity-70`}
      >
        <p className="font-medium text-stone-700">
          {busy ? `Uploading ${busy}…` : 'Drop PDF papers here, or click to choose'}
        </p>
        <p className="mt-1 text-sm text-stone-500">English or Japanese · up to 50 MB each</p>
      </button>
      <input
        ref={input}
        type="file"
        accept="application/pdf,.pdf"
        multiple
        hidden
        onChange={(e) => {
          if (e.target.files) void upload(e.target.files)
          e.target.value = ''
        }}
      />
      {errors.length > 0 && (
        <ul className="mt-2 space-y-1 text-sm text-red-600">
          {errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
