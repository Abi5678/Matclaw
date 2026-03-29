import { useState, useRef, useCallback, useEffect } from 'react'

export interface VoiceInputState {
  isListening: boolean
  transcript: string
  isSupported: boolean
  error: string | null
  startListening: () => void
  stopListening: () => void
  resetTranscript: () => void
}

export function useVoiceInput(): VoiceInputState {
  const [isListening, setIsListening] = useState(false)
  const [transcript, setTranscript] = useState('')
  const [error, setError] = useState<string | null>(null)
  const recogRef = useRef<SpeechRecognition | null>(null)

  const isSupported =
    typeof window !== 'undefined' &&
    !!(window.SpeechRecognition || window.webkitSpeechRecognition)

  const startListening = useCallback(() => {
    if (!isSupported) return
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition
    const recog = new SR()
    recog.continuous = true
    recog.interimResults = true
    recog.lang = 'en-US'

    recog.onresult = (e) => {
      let full = ''
      for (let i = 0; i < e.results.length; i++) {
        full += e.results[i][0].transcript
      }
      setTranscript(full)
    }

    recog.onerror = (e) => {
      if (e.error === 'not-allowed') setError('Microphone access denied')
      else if (e.error === 'no-speech') setError('No speech detected')
      else setError(e.error)
      setIsListening(false)
    }

    recog.onend = () => {
      setIsListening(false)
    }

    recogRef.current = recog
    recog.start()
    setIsListening(true)
    setError(null)
  }, [isSupported])

  const stopListening = useCallback(() => {
    recogRef.current?.stop()
    setIsListening(false)
  }, [])

  const resetTranscript = useCallback(() => {
    setTranscript('')
  }, [])

  useEffect(() => {
    return () => recogRef.current?.abort()
  }, [])

  return { isListening, transcript, isSupported, error, startListening, stopListening, resetTranscript }
}
