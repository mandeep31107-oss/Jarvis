import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Browser voice layer.
 *
 * What is real here:
 *  - SpeechRecognition (Chrome/Edge) for microphone input with interim results.
 *  - SpeechSynthesis for spoken output, with barge-in (speaking stops when the
 *    owner starts talking and the microphone is active).
 *  - Wake phrase detection by keyword spotting on the transcript.
 *
 * What is NOT here: speaker verification. Any voice can trigger the assistant.
 * That is why voice is never an authorization factor — sensitive actions still
 * require approval in the UI.
 */

interface SpeechRecognitionAlternativeLike {
  transcript: string;
  confidence: number;
}

interface SpeechRecognitionResultLike {
  isFinal: boolean;
  0: SpeechRecognitionAlternativeLike;
  length: number;
}

interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: { length: number; [index: number]: SpeechRecognitionResultLike };
}

interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  onstart: (() => void) | null;
}

type RecognitionCtor = new () => SpeechRecognitionLike;

function getRecognitionCtor(): RecognitionCtor | null {
  const w = window as unknown as {
    SpeechRecognition?: RecognitionCtor;
    webkitSpeechRecognition?: RecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export interface VoiceOptions {
  language: string;
  rate: number;
  pitch: number;
  wakePhrase: string;
  wakeEnabled: boolean;
  onUtterance: (text: string, meta: { viaWake: boolean }) => void;
}

export interface VoiceApi {
  supported: boolean;
  synthesisSupported: boolean;
  listening: boolean;
  wakeListening: boolean;
  speaking: boolean;
  interim: string;
  error: string | null;
  startListening: () => void;
  stopListening: () => void;
  startWakeListening: () => void;
  stopWakeListening: () => void;
  speak: (text: string) => void;
  interrupt: () => void;
  clearError: () => void;
}

function stripForSpeech(markdown: string): string {
  return markdown
    .replace(/```[\s\S]*?```/g, ' code block omitted. ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1')
    .replace(/[*_#>|]/g, ' ')
    .replace(/\s{2,}/g, ' ')
    .trim()
    .slice(0, 4000);
}

export function useVoice(opts: VoiceOptions): VoiceApi {
  const [supported] = useState(() => getRecognitionCtor() !== null);
  const [synthesisSupported] = useState(() => typeof window !== 'undefined' && 'speechSynthesis' in window);
  const [listening, setListening] = useState(false);
  const [wakeListening, setWakeListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [interim, setInterim] = useState('');
  const [error, setError] = useState<string | null>(null);

  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const modeRef = useRef<'off' | 'push' | 'wake'>('off');
  const optsRef = useRef(opts);
  const voicesRef = useRef<SpeechSynthesisVoice[]>([]);

  optsRef.current = opts;

  // Keep a live reference to the newest voices without re-creating recognition.
  useEffect(() => {
    if (!synthesisSupported) return;
    const update = () => {
      voicesRef.current = window.speechSynthesis.getVoices();
    };
    update();
    window.speechSynthesis.addEventListener('voiceschanged', update);
    return () => window.speechSynthesis.removeEventListener('voiceschanged', update);
  }, [synthesisSupported]);

  const ensureRecognition = useCallback((): SpeechRecognitionLike | null => {
    const Ctor = getRecognitionCtor();
    if (!Ctor) return null;
    if (recognitionRef.current) return recognitionRef.current;
    const recognition = new Ctor();
    recognition.lang = optsRef.current.language;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    recognition.continuous = false;

    recognition.onstart = () => {
      setError(null);
      setListening(true);
    };

    recognition.onresult = (event) => {
      let finalText = '';
      let interimText = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        if (!result) continue;
        const transcript = result[0]?.transcript ?? '';
        if (result.isFinal) finalText += transcript;
        else interimText += transcript;
      }
      setInterim(interimText);
      if (interimText.length > 2) {
        // Barge-in: the owner started speaking while J.A.R.V.I.S. was talking.
        if (typeof window !== 'undefined' && window.speechSynthesis?.speaking) {
          window.speechSynthesis.cancel();
          setSpeaking(false);
        }
      }
      if (finalText.trim()) {
        handleTranscript(finalText.trim());
      }
    };

    recognition.onerror = (event) => {
      const code = event.error;
      if (code === 'no-speech' || code === 'aborted') return;
      if (code === 'not-allowed' || code === 'service-not-allowed') {
        setError('Microphone permission denied. Allow microphone access for this site in your browser settings.');
        modeRef.current = 'off';
        setListening(false);
        setWakeListening(false);
        return;
      }
      if (code === 'network') {
        setError('The browser speech service is unreachable (network). Voice input needs an online browser service.');
        return;
      }
      setError(`Speech recognition error: ${code}`);
    };

    recognition.onend = () => {
      setListening(false);
      setInterim('');
      // Restart automatically in wake mode; this is why the indicator stays lit.
      if (modeRef.current === 'wake') {
        try {
          recognition.start();
        } catch {
          /* already starting */
        }
      }
    };

    recognitionRef.current = recognition;
    return recognition;
  }, []);

  const handleTranscript = useCallback((text: string) => {
    const { wakePhrase, onUtterance } = optsRef.current;
    const wake = wakePhrase.trim().toLowerCase();
    const lower = text.toLowerCase();
    const viaWake = modeRef.current === 'wake';

    if (viaWake) {
      if (!wake || !lower.includes(wake)) return;
      const idx = lower.indexOf(wake);
      const remainder = text.slice(idx + wake.length).replace(/^[\s,.:;-]+/, '').trim();
      if (remainder.length > 1) onUtterance(remainder, { viaWake: true });
      return;
    }
    onUtterance(text, { viaWake: false });
  }, []);

  const startListening = useCallback(() => {
    const recognition = ensureRecognition();
    if (!recognition) {
      setError('This browser does not support speech recognition. Use Chrome or Edge, or type instead.');
      return;
    }
    modeRef.current = 'push';
    recognition.continuous = false;
    recognition.lang = optsRef.current.language;
    try {
      recognition.start();
    } catch {
      /* start() throws if already running */
    }
  }, [ensureRecognition]);

  const stopListening = useCallback(() => {
    modeRef.current = 'off';
    setWakeListening(false);
    setListening(false);
    recognitionRef.current?.stop();
  }, []);

  const startWakeListening = useCallback(() => {
    if (!optsRef.current.wakeEnabled) {
      setError('Enable the wake phrase in Settings before starting standby listening.');
      return;
    }
    const recognition = ensureRecognition();
    if (!recognition) {
      setError('This browser does not support speech recognition.');
      return;
    }
    modeRef.current = 'wake';
    recognition.continuous = true;
    recognition.lang = optsRef.current.language;
    setWakeListening(true);
    try {
      recognition.start();
    } catch {
      /* already running */
    }
  }, [ensureRecognition]);

  const stopWakeListening = useCallback(() => {
    modeRef.current = 'off';
    setWakeListening(false);
    setListening(false);
    recognitionRef.current?.stop();
  }, []);

  const speak = useCallback(
    (text: string) => {
      if (!synthesisSupported) return;
      const spoken = stripForSpeech(text);
      if (!spoken) return;
      const synth = window.speechSynthesis;
      synth.cancel();
      const utterance = new SpeechSynthesisUtterance(spoken);
      utterance.lang = optsRef.current.language;
      utterance.rate = optsRef.current.rate;
      utterance.pitch = optsRef.current.pitch;
      const languagePrefix = optsRef.current.language.split('-')[0] ?? 'en';
      const match =
        voicesRef.current.find((v) => v.lang === optsRef.current.language) ??
        voicesRef.current.find((v) => v.lang.startsWith(languagePrefix));
      if (match) utterance.voice = match;
      utterance.onstart = () => setSpeaking(true);
      utterance.onend = () => setSpeaking(false);
      utterance.onerror = () => setSpeaking(false);
      synth.speak(utterance);
    },
    [synthesisSupported],
  );

  const interrupt = useCallback(() => {
    if (!synthesisSupported) return;
    window.speechSynthesis.cancel();
    setSpeaking(false);
  }, [synthesisSupported]);

  useEffect(() => {
    return () => {
      modeRef.current = 'off';
      recognitionRef.current?.abort();
      if (synthesisSupported) window.speechSynthesis.cancel();
    };
  }, [synthesisSupported]);

  // Changing language requires a new recognition language on the next start.
  useEffect(() => {
    if (recognitionRef.current) recognitionRef.current.lang = opts.language;
  }, [opts.language]);

  return {
    supported,
    synthesisSupported,
    listening,
    wakeListening,
    speaking,
    interim,
    error,
    startListening,
    stopListening,
    startWakeListening,
    stopWakeListening,
    speak,
    interrupt,
    clearError: () => setError(null),
  };
}
