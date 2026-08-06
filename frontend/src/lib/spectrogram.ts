// Magnitude spectrogram drawing — ported from the legacy
// original tool's drawSpectrogram path. STFT with a
// Hann window, log-dB normalization, viridis-ish color map, 8 kHz cap
// (the band that actually carries word boundary cues for English
// sibilants and stops).

// In-place radix-2 Cooley-Tukey FFT. `re` and `im` must be the same
// power-of-two length. Output replaces input.
export function fft(re: Float32Array, im: Float32Array): void {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j], re[i]];
      [im[i], im[j]] = [im[j], im[i]];
    }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const ang = (-2 * Math.PI) / len;
    const wR = Math.cos(ang);
    const wI = Math.sin(ang);
    for (let i = 0; i < n; i += len) {
      let cR = 1;
      let cI = 0;
      for (let j = 0; j < len / 2; j++) {
        const h2 = len / 2;
        const tR = cR * re[i + j + h2] - cI * im[i + j + h2];
        const tI = cR * im[i + j + h2] + cI * re[i + j + h2];
        re[i + j + h2] = re[i + j] - tR;
        im[i + j + h2] = im[i + j] - tI;
        re[i + j] += tR;
        im[i + j] += tI;
        const nR = cR * wR - cI * wI;
        cI = cR * wI + cI * wR;
        cR = nR;
      }
    }
  }
}

export interface SpectrogramFrames {
  spec: Float32Array; // length nFrames * nBins
  nFrames: number;
  nBins: number; // bins actually filled (capped at 8 kHz)
  hop: number;
  fftSize: number;
}

export function computeSpectrogram(
  samples: Float32Array,
  sr: number,
  fftSize: number,
  hop: number,
): SpectrogramFrames {
  const nFrames = Math.max(0, Math.floor((samples.length - fftSize) / hop) + 1);
  const nBinsAll = fftSize / 2 + 1;
  const nBins = Math.min(nBinsAll, Math.ceil(8000 / (sr / fftSize)));
  const hann = new Float32Array(fftSize);
  for (let i = 0; i < fftSize; i++) {
    hann[i] = 0.5 * (1 - Math.cos((2 * Math.PI * i) / (fftSize - 1)));
  }
  const spec = new Float32Array(nFrames * nBins);
  const re = new Float32Array(fftSize);
  const im = new Float32Array(fftSize);
  for (let f = 0; f < nFrames; f++) {
    const off = f * hop;
    for (let i = 0; i < fftSize; i++) {
      re[i] = (samples[off + i] || 0) * hann[i];
      im[i] = 0;
    }
    fft(re, im);
    for (let b = 0; b < nBins; b++) {
      const r = re[b];
      const i2 = im[b];
      spec[f * nBins + b] = Math.sqrt(r * r + i2 * i2);
    }
  }
  return { spec, nFrames, nBins, hop, fftSize };
}

// Viridis-ish blue→pink→yellow→white. Cheap, no lookup table.
function specColor(t: number): [number, number, number] {
  t = Math.max(0, Math.min(1, t));
  if (t < 0.33) {
    const u = t / 0.33;
    return [0, Math.round(u * 80), Math.round(40 + u * 160)];
  }
  if (t < 0.66) {
    const u = (t - 0.33) / 0.33;
    return [
      Math.round(u * 255),
      Math.round(80 + u * 175),
      Math.round(200 - u * 100),
    ];
  }
  const u = (t - 0.66) / 0.34;
  return [255, 255, Math.round(100 + u * 155)];
}

export interface SpectrogramDrawOptions {
  // Drawn on top of the spec: dim the area outside [segStart, segEnd] and
  // paint vertical yellow boundary lines at those times.
  segStartSec: number;
  segEndSec: number;
  // Optional green dashed marker for "add token" mode.
  addStartSec: number | null;
}

export function drawSpectrogram(
  ctx: CanvasRenderingContext2D,
  widthCss: number,
  heightCss: number,
  buffer: AudioBuffer,
  viewStartSec: number,
  viewEndSec: number,
  options: SpectrogramDrawOptions,
): void {
  const w = widthCss;
  const h = heightCss;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#12131a";
  ctx.fillRect(0, 0, w, h);
  if (viewEndSec <= viewStartSec) return;

  const sr = buffer.sampleRate;
  const samples = buffer.getChannelData(0);
  const s0 = Math.max(0, Math.floor(viewStartSec * sr));
  const s1 = Math.min(samples.length, Math.floor(viewEndSec * sr));
  if (s1 - s0 < 512) return;
  const region = samples.subarray(s0, s1);

  const fftSize = 512;
  const hop = 128;
  const { spec, nFrames, nBins } = computeSpectrogram(region, sr, fftSize, hop);
  if (nFrames === 0) return;

  let maxVal = 0;
  for (let i = 0; i < spec.length; i++) {
    if (spec[i] > maxVal) maxVal = spec[i];
  }
  if (maxVal === 0) maxVal = 1;

  // Render at native frame×bin resolution then scale up — much faster than
  // painting w*h pixels directly, and the scaling gives the familiar
  // "smeared" spectrogram look.
  const tmp = document.createElement("canvas");
  tmp.width = nFrames;
  tmp.height = nBins;
  const tc = tmp.getContext("2d");
  if (!tc) return;
  const img = tc.createImageData(nFrames, nBins);
  for (let f = 0; f < nFrames; f++) {
    for (let b = 0; b < nBins; b++) {
      const val = spec[f * nBins + b] / maxVal;
      const db = 20 * Math.log10(val + 1e-6);
      const norm = Math.max(0, Math.min(1, (db + 60) / 60));
      const [r, g, bl] = specColor(norm);
      const y = nBins - 1 - b;
      const idx = (y * nFrames + f) * 4;
      img.data[idx] = r;
      img.data[idx + 1] = g;
      img.data[idx + 2] = bl;
      img.data[idx + 3] = 255;
    }
  }
  tc.putImageData(img, 0, 0);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(tmp, 0, 0, w, h);

  // Boundary overlay
  const dur = viewEndSec - viewStartSec;
  const x1 = ((options.segStartSec - viewStartSec) / dur) * w;
  const x2 = ((options.segEndSec - viewStartSec) / dur) * w;
  ctx.fillStyle = "rgba(0,0,0,0.25)";
  if (x1 > 0) ctx.fillRect(0, 0, Math.min(x1, w), h);
  if (x2 < w) ctx.fillRect(Math.max(0, x2), 0, w - Math.max(0, x2), h);
  ctx.strokeStyle = "#f7c948";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(x1, 0);
  ctx.lineTo(x1, h);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(x2, 0);
  ctx.lineTo(x2, h);
  ctx.stroke();

  // Frequency axis labels
  ctx.fillStyle = "rgba(255,255,255,0.4)";
  ctx.font = "9px monospace";
  ctx.fillText("8kHz", 2, 10);
  ctx.fillText("0", 2, h - 3);

  if (options.addStartSec !== null) {
    const ax = ((options.addStartSec - viewStartSec) / dur) * w;
    ctx.strokeStyle = "#4ade80";
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(ax, 0);
    ctx.lineTo(ax, h);
    ctx.stroke();
    ctx.setLineDash([]);
  }
}
