"use client";

/**
 * Browser voice I/O for the AVINASH console.
 *
 * - Speech recognition uses the Web Speech API (Chrome/Edge/Safari). It runs entirely in
 *   the user's browser; nothing is streamed to our backend except the final transcript the
 *   user submits as a command.
 * - Speech synthesis uses `speechSynthesis` with a user-selectable voice.
 * - A Web Audio analyser provides the live microphone level that drives the HUD waveform.
 *
 * Everything degrades gracefully: feature detection is exposed so the UI can hide controls
 * the browser does not support instead of failing silently.
 */

type RecognitionCtor = new () => SpeechRecognitionLike;

interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onend: (() => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onstart: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: ArrayLike<ArrayLike<{ transcript: string; confidence: number }> & { isFinal: boolean }>;
}

function recognitionCtor(): RecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export const voiceSupport = {
  get recognition(): boolean {
    return recognitionCtor() !== null;
  },
  get synthesis(): boolean {
    return typeof window !== "undefined" && "speechSynthesis" in window;
  },
  get microphone(): boolean {
    return typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia;
  },
};

export interface RecognizerOptions {
  lang?: string;
  continuous?: boolean;
  onInterim?: (text: string) => void;
  onFinal: (text: string) => void;
  onEnd?: () => void;
  onError?: (error: string) => void;
}

export interface Recognizer {
  start(): void;
  stop(): void;
  readonly active: boolean;
}

export function createRecognizer(options: RecognizerOptions): Recognizer | null {
  const Ctor = recognitionCtor();
  if (!Ctor) return null;
  const rec = new Ctor();
  rec.lang = options.lang ?? (typeof navigator !== "undefined" ? navigator.language : "en-US") ?? "en-US";
  rec.continuous = options.continuous ?? false;
  rec.interimResults = true;
  rec.maxAlternatives = 1;
  let active = false;
  let wantActive = false;
  rec.onstart = () => {
    active = true;
  };
  rec.onresult = (event) => {
    let interim = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      const transcript = result[0]?.transcript ?? "";
      if (result.isFinal) options.onFinal(transcript.trim());
      else interim += transcript;
    }
    if (interim && options.onInterim) options.onInterim(interim.trim());
  };
  rec.onerror = (event) => {
    if (event.error === "no-speech" || event.error === "aborted") return;
    options.onError?.(event.error);
    if (event.error === "not-allowed" || event.error === "service-not-allowed") wantActive = false;
  };
  rec.onend = () => {
    active = false;
    if (wantActive && rec.continuous) {
      // Continuous recognition is cut by the browser every ~60s; restart while the user still wants it.
      try {
        rec.start();
      } catch {
        wantActive = false;
        options.onEnd?.();
      }
      return;
    }
    options.onEnd?.();
  };
  return {
    start() {
      wantActive = true;
      if (active) return;
      try {
        rec.start();
      } catch {
        /* already started */
      }
    },
    stop() {
      wantActive = false;
      try {
        rec.stop();
      } catch {
        /* ignore */
      }
    },
    get active() {
      return active;
    },
  };
}

/** Common speech-recognition mis-hearings of assistant names, keyed by the normalised name. */
const WAKE_ALIASES: Record<string, string[]> = {
  avinash: ["avinash", "avi nash", "abhinash", "abinash", "avinaash", "avinas", "avinesh", "avanash"],
  jarvis: ["jarvis", "jarves", "jervis", "travis"],
};

/** Wake-word detection on a transcript, tolerant of common mis-hearings ("hey avinash", "a avinash", "abhinash"). */
export function matchWakeWord(transcript: string, name: string): string | null {
  const lowered = transcript.toLowerCase().trim();
  const target = name.toLowerCase().replace(/[^a-z0-9 ]/g, "").trim() || "avinash";
  const variants = Array.from(new Set([target, ...(WAKE_ALIASES[target.replace(/\s+/g, "")] ?? [])]));
  const alternatives = variants.map((v) => v.replace(/\s+/g, "\\s*")).join("|");
  const pattern = new RegExp(`\\b(?:hey|hi|ok|okay|yo|a|hay)?\\s*(?:${alternatives})\\b[,.!?]?\\s*(.*)$`, "i");
  const m = lowered.match(pattern);
  if (!m) return null;
  return (m[1] ?? "").trim();
}

// ------------------------------------------------------------------ synthesis
export interface SpeakOptions {
  voiceURI?: string | null;
  rate?: number;
  pitch?: number;
  volume?: number;
  onStart?: () => void;
  onEnd?: () => void;
  onBoundary?: () => void;
}

export function listVoices(): SpeechSynthesisVoice[] {
  if (!voiceSupport.synthesis) return [];
  return window.speechSynthesis.getVoices();
}

/** Resolve voices asynchronously: Chrome populates them after `voiceschanged`. */
export function voicesReady(): Promise<SpeechSynthesisVoice[]> {
  return new Promise((resolve) => {
    if (!voiceSupport.synthesis) return resolve([]);
    const current = window.speechSynthesis.getVoices();
    if (current.length) return resolve(current);
    const handler = () => {
      window.speechSynthesis.removeEventListener("voiceschanged", handler);
      resolve(window.speechSynthesis.getVoices());
    };
    window.speechSynthesis.addEventListener("voiceschanged", handler);
    setTimeout(() => resolve(window.speechSynthesis.getVoices()), 1500);
  });
}

/** Pick a sensible default voice: prefer an English "Google UK English Male"/Daniel-like voice. */
export function preferredVoice(voices: SpeechSynthesisVoice[]): SpeechSynthesisVoice | null {
  const preferences = [/google uk english male/i, /daniel/i, /uk english male/i, /en-gb/i, /google us english/i, /en-us/i, /en/i];
  for (const pattern of preferences) {
    const found = voices.find((v) => pattern.test(`${v.name} ${v.lang}`));
    if (found) return found;
  }
  return voices[0] ?? null;
}

/** Strip markup/URLs so the spoken reply sounds natural. */
export function speakable(text: string): string {
  return text
    .replace(/https?:\/\/\S+/g, "a link")
    .replace(/[*_`#>]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 600);
}

export function speak(text: string, options: SpeakOptions = {}): boolean {
  if (!voiceSupport.synthesis) return false;
  const utterance = new SpeechSynthesisUtterance(speakable(text));
  const voices = listVoices();
  const voice = (options.voiceURI && voices.find((v) => v.voiceURI === options.voiceURI)) || preferredVoice(voices);
  if (voice) utterance.voice = voice;
  utterance.rate = options.rate ?? 1.02;
  utterance.pitch = options.pitch ?? 0.92;
  utterance.volume = options.volume ?? 1;
  utterance.onstart = () => options.onStart?.();
  utterance.onend = () => options.onEnd?.();
  utterance.onerror = () => options.onEnd?.();
  utterance.onboundary = () => options.onBoundary?.();
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utterance);
  return true;
}

export function stopSpeaking(): void {
  if (voiceSupport.synthesis) window.speechSynthesis.cancel();
}

// ------------------------------------------------------------------ microphone level
export interface LevelMeter {
  read(): number;
  stop(): void;
}

/** Open the microphone and return a 0–1 RMS level reader for the HUD waveform. */
export async function createLevelMeter(): Promise<LevelMeter | null> {
  if (!voiceSupport.microphone) return null;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true }, video: false });
    const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) {
      stream.getTracks().forEach((t) => t.stop());
      return null;
    }
    const ctx = new Ctx();
    const source = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.6;
    source.connect(analyser);
    const buffer = new Uint8Array(analyser.fftSize);
    return {
      read() {
        analyser.getByteTimeDomainData(buffer);
        let sum = 0;
        for (let i = 0; i < buffer.length; i++) {
          const v = (buffer[i] - 128) / 128;
          sum += v * v;
        }
        return Math.min(1, Math.sqrt(sum / buffer.length) * 4);
      },
      stop() {
        stream.getTracks().forEach((t) => t.stop());
        void ctx.close();
      },
    };
  } catch {
    return null;
  }
}
